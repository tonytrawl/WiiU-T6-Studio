#!/usr/bin/env python3
"""
_dup_techset_names.py — do TECHNIQUE_SETs share a NAME, and do same-named ones demand
DIFFERENT constant sets?

Why this matters (boot-20 proof): the faulting material's alias maps to zone asset 743
('lit_sm_r0c0n0x0_b1c1n1s1_b2c2n2v2', which does NOT demand 0x88befc32), yet at runtime it
was searched with args demanding 0x88befc32 -- the demand set of asset 752, which carries the
SAME NAME. T6 registers assets into a global pool BY NAME, so same-named duplicates collapse
to one instance and every reference resolves to the winner. A per-zone-index audit therefore
mis-attributes: it checks a material against the body its alias points at, not the body the
pool hands back.

NAME accessor per the validated walker (_matconst_map.walk_techset): techset body is 136B,
name* @0, and the name string is inline at body+136 ONLY when name* == FOLLOW.
"""
import re
import sys
from collections import defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_zone
from _matconst_map import (CONSTDEF, FOLLOW, PTRS, be32, parse_material,
                           techset_const_hashes)

SRC = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_gfxtail17.zone'
Z = bytearray(open(SRC, 'rb').read())
isalias = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or isalias(v)

rc = wiiu_zone.ZoneReader(bytes(Z))
rc.read_string_table()
rc.read_asset_list()
em, spans, CO = LS.simulate(SRC, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}


def ts_name(s):
    """techset name: inline at body+136 iff name* == FOLLOW, else aliased (no inline string)."""
    if be32(Z, s) != FOLLOW:
        return None, 'ALIASED'
    e = Z.find(b'\x00', s + 136)
    nm = bytes(Z[s + 136:e]).decode('latin1', 'replace')
    ok = nm and all(32 <= c < 127 for c in nm.encode())
    return (nm if ok else None), ('inline' if ok else 'UNPRINTABLE')


name_of, kind_of, demand = {}, {}, {}
for i, (s, e) in ts_spans.items():
    nm, k = ts_name(s)
    name_of[i], kind_of[i] = nm, k
    try:
        demand[i] = techset_const_hashes(bytes(Z), s)[0]
    except Exception:
        demand[i] = None

kinds = defaultdict(int)
for i in ts_spans:
    kinds[kind_of[i]] += 1
print('walked techsets: %d   name kinds: %s' % (len(ts_spans), dict(kinds)))

groups = defaultdict(list)
for i, nm in name_of.items():
    if nm:
        groups[nm].append(i)
dups = {nm: v for nm, v in groups.items() if len(v) > 1}
print('distinct inline names: %d   names with DUPLICATES: %d (covering %d assets)'
      % (len(groups), len(dups), sum(len(v) for v in dups.values())))

diverge = {}
for nm, idxs in dups.items():
    sets = [demand[i] for i in idxs if demand[i] is not None]
    if len(sets) >= 2 and any(s != sets[0] for s in sets[1:]):
        diverge[nm] = idxs
print('duplicate-name groups whose DEMAND SETS DIVERGE: %d' % len(diverge))
for nm, idxs in sorted(diverge.items()):
    allh = set()
    for i in idxs:
        allh |= (demand[i] or set())
    print('  %s' % nm)
    for i in idxs:
        d = demand[i] or set()
        print('     asset %3d: %2d demanded   lacks-vs-sibling: %s'
              % (i, len(d), ['0x%08x' % h for h in sorted(allh - d)] or '-'))

# ---- material side ----
arr = rc.assets_off - 64
our_arr = (arr + 7) & ~7


def ts_idx(a):
    v = (a - 1) & 0x1FFFFFFF
    if (v - our_arr - 4) % 8:
        return None
    k = (v - our_arr - 4) // 8
    return k if 0 <= k < len(rc.assets) else None


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
            info['_off'] = o
            info['cset'] = set(info['consts'])
            mats.append(info)
            last = o + 104
    except Exception:
        pass

print('\n--- AUDIT: per-index model (what gfxtail17 assumed) vs NAME-GROUP model (runtime) ---')
bad_idx = bad_grp = mapped = 0
offenders = defaultdict(int)
for mm in mats:
    k = ts_idx(mm['ts']) if isalias(mm['ts']) else None
    if k is None or k not in demand or demand[k] is None:
        continue
    mapped += 1
    if demand[k] - mm['cset']:
        bad_idx += 1
    nm = name_of.get(k)
    union = set()
    for j in (groups.get(nm) or [k]):
        union |= (demand[j] or set())
    miss = union - mm['cset']
    if miss:
        bad_grp += 1
        for h in miss:
            offenders[h] += 1

print('materials scanned / mapped to a walked techset : %d / %d' % (len(mats), mapped))
print('materials MISSING a demanded const, per-index   : %d   <- the "0/551" claim' % bad_idx)
print('materials MISSING a demanded const, name-group  : %d   <- the real exposure' % bad_grp)
if offenders:
    print('hashes that would trigger the unbounded walk:')
    for h, n in sorted(offenders.items(), key=lambda kv: -kv[1]):
        print('   0x%08x : %d materials' % (h, n))
