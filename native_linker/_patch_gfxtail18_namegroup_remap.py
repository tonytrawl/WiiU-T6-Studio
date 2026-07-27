#!/usr/bin/env python3
"""gfxtail18: re-key the family-9 constant remap by TECHSET NAME-GROUP.

WHY (see FINDINGS_family9_name_dedup.md):
gfxtail14/17 bucketed materials by ASSET INDEX (via the material's ts alias). But T6 pools
assets BY NAME: our zone has 17 duplicated TECHNIQUE_SET names (raid, genuine, has ZERO), and
for a same-named pair BOTH asset-array entries resolve to the winner's body. So a material
whose alias points at asset 743 is served asset 752's args -- and 752 demands 0x88befc32,
which that material lacks -> unbounded constantTable walk -> the boot-20 AV.

Under the per-index key the plan was EMPTY for exactly the techsets that matter
("techset 752: 0 materials mapped"), which is why gfxtail17 changed nothing for them and why
its audit reported 0/551 missing.

FIX: for each NAME-GROUP G:
  S   = intersection of the constant sets of ALL materials binding to ANY member of G
  bad = (union of demands of all members of G) - S
  rewrite every type-6 arg in EVERY member of G whose hash is in `bad` -> min(S)
Direction-agnostic: it does not matter which duplicate wins the pool.

CONSTRAINTS HONOURED: arg VALUES only, never counts (no stream desync); size-neutral;
byte-granular material scan; clipMap gate in+out.
Built from gfxtail17 (deployed).
"""
import hashlib
import re
import struct
import sys
from collections import defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
import loader_sim as LS
import wiiu_zone
from _matconst_map import (ARG_CONST_HASH, CONSTDEF, FOLLOW, PTRS, be16, be32,
                           parse_material, techset_const_hashes, walk_techset)

SRC = 'mp_skate_gfxtail17.zone'
DST = 'mp_skate_gfxtail18.zone'
FF = 'mp_skate_gfxtail18.ff'
BB = 84512493

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)
isalias = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or isalias(v)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, d))
    return d


assert gate(Z, 'in') == 0

rc = wiiu_zone.ZoneReader(bytes(Z))
rc.read_string_table()
rc.read_asset_list()
em, spans, CO = LS.simulate(SRC, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}
demand = {i: techset_const_hashes(bytes(Z), s)[0] for i, (s, e) in ts_spans.items()}
n_ts = sum(1 for (c, p, nm) in rc.assets if nm == 'TECHNIQUE_SET')
print('techsets: %d in asset list, %d walked (%d beyond sim break)'
      % (n_ts, len(demand), n_ts - len(demand)))


def ts_name(s):
    """name string is inline at body+136 ONLY when name* == FOLLOW (per walk_techset)."""
    if be32(Z, s) != FOLLOW:
        return None
    e = Z.find(b'\x00', s + 136)
    nm = bytes(Z[s + 136:e]).decode('latin1', 'replace')
    return nm if nm and all(32 <= c < 127 for c in nm.encode()) else None


name_of = {i: ts_name(s) for i, (s, e) in ts_spans.items()}
groups = defaultdict(list)
for i, nm in name_of.items():
    if nm:
        groups[nm].append(i)
dups = {nm: v for nm, v in groups.items() if len(v) > 1}
print('techset names: %d distinct, %d DUPLICATED (%d assets)'
      % (len(groups), len(dups), sum(len(v) for v in dups.values())))

arr = rc.assets_off - 64
our_arr = (arr + 7) & ~7


def ts_idx(a):
    v = (a - 1) & 0x1FFFFFFF
    if (v - our_arr - 4) % 8:
        return None
    k = (v - our_arr - 4) // 8
    return k if 0 <= k < len(rc.assets) else None


# --- BYTE-ALIGNED material scan (never step by 4) ---
mats = []
last = -1
for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), bytes(Z)):
    o = m.start()
    if o < last or o + 104 > len(Z):
        continue
    texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
    if not (be32(Z, o + 88) == FOLLOW and 1 <= constc <= 64 and texc <= 64 and sbc <= 64
            and all(ptrish(be32(Z, o + x)) for x in (80, 84, 92, 96))):
        continue
    try:
        info, nxt = parse_material(bytes(Z), o)
        names = [Z[info['ct_off'] + k * CONSTDEF + 4:info['ct_off'] + k * CONSTDEF + 16]
                 for k in range(constc)]
        if all(n[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in n) for n in names) and info['name']:
            info['cset'] = set(info['consts'])
            mats.append(info)
            last = o + 104
    except Exception:
        pass
print('materials located (BYTE-aligned): %d' % len(mats))

# --- bind materials to NAME-GROUPS ---
by_group = defaultdict(list)
unwalked_binds = 0
for mm in mats:
    if not isalias(mm['ts']):
        continue
    k = ts_idx(mm['ts'])
    if k is None:
        continue
    if k not in demand:
        unwalked_binds += 1          # binds to a techset beyond the sim break
        continue
    nm = name_of.get(k)
    by_group[nm if nm else ('#idx%d' % k)].append(mm)
print('materials binding to a techset beyond the sim break: %d (NOT auditable here)'
      % unwalked_binds)

# --- plan: per NAME-GROUP ---
plan = {}
for nm, ms in by_group.items():
    members = groups.get(nm, [])
    if not members:
        continue
    union = set()
    for j in members:
        union |= (demand[j] or set())
    S = set.intersection(*[m['cset'] for m in ms])
    bad = union - S
    if not bad:
        continue
    if not S:
        print('  !! group %r: NO constant common to its %d materials -> cannot remap' % (nm, len(ms)))
        continue
    plan[nm] = (bad, min(S), members)

print('name-groups to remap: %d (unsatisfiable hashes %d)'
      % (len(plan), sum(len(v[0]) for v in plan.values())))
for nm, (bad, tgt, members) in sorted(plan.items()):
    print('   %-38s members=%s  bad=%s -> 0x%08x'
          % (nm, members, ['0x%08x' % h for h in sorted(bad)], tgt))

# --- apply: rewrite type-6 arg VALUES only, in EVERY member of the group ---
n_args = 0
for nm, (bad, target, members) in plan.items():
    for k in members:
        s, e = ts_spans[k]
        passes, _ = walk_techset(bytes(Z), s)
        for p in passes:
            assert p['lits'] == 0, 'literal args present in techset %d' % k
            base = p['args_off']
            for j in range(p['nargs']):
                a = base + j * 8
                if be16(Z, a) == ARG_CONST_HASH and be32(Z, a + 4) in bad:
                    struct.pack_into('>I', Z, a + 4, target)
                    n_args += 1
print('remapped %d type-6 args' % n_args)

assert len(Z) == len(orig), 'ZONE GREW — forbidden'
assert gate(Z, 'out') == 0

# --- verify BOTH models on the patched zone ---
dem2 = {i: techset_const_hashes(bytes(Z), s)[0] for i, (s, e) in ts_spans.items()}
bad_idx = bad_grp = 0
for mm in mats:
    if not isalias(mm['ts']):
        continue
    k = ts_idx(mm['ts'])
    if k is None or k not in dem2:
        continue
    if dem2[k] - mm['cset']:
        bad_idx += 1
    nm = name_of.get(k)
    union = set()
    for j in (groups.get(nm) or [k]):
        union |= (dem2[j] or set())
    if union - mm['cset']:
        bad_grp += 1
print('post-patch: materials missing a const  per-index=%d  NAME-GROUP=%d  (both must be 0)'
      % (bad_idx, bad_grp))
assert bad_idx == 0 and bad_grp == 0

changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d (size-neutral)' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
