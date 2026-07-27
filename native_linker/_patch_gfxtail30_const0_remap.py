#!/usr/bin/env python3
"""gfxtail30: remap unsatisfiable TYPE-0 (vertex) material-constant demands.

Boot 36 (gfxtail29, 365 draws) = the family-9 stride-32 constantTable runaway,
but the demanding args are TYPE 0 — every earlier constants audit/remap
(gfxtail14/17/18, _nullct_oracle) covered ONLY type 6. Raid control
(_const0_oracle): type-0 values 46/46 in the constant vocabulary, invariant
demand_{0,6} ⊆ consts holds 281/0. Skate gfxtail29: 31 violators in 3 groups
(ts730 4layer ×24 hashes, ts715 unlit_add ×1, ts718 watershore ×5 — the dump's
exact arg neighborhood; the engine's resumable search cursor explains the
0x4c53b0bf fault after the 0x19cc0727 runaway).

Plan: per techset NAME-GROUP, bad = union(type-0+6 demands) − intersection
(consts of bound materials); rewrite matching type-0/6 arg VALUES → min(S).
Includes the 4 tail techsets. Same constraints as gfxtail18/29 (VALUES only,
size-neutral, clipMap gate, shadow check).
Usage: python _patch_gfxtail30_const0_remap.py [--apply]
"""
import hashlib
import os
import re
import struct
import sys
from collections import defaultdict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
from _matconst_map import be16, be32, walk_techset, FOLLOW, PTRS
from _nullct_oracle import scan
from _sampler_oracle import techset_args

SRC = 'mp_skate_gfxtail29.zone'
DST = 'mp_skate_gfxtail30.zone'
FF = 'mp_skate_gfxtail30.ff'
BB = 84512493
CONST_TYPES = (0, 6)
TAIL_TS = {804: 84317304, 815: 89654859, 817: 89678237, 819: 89678868}
AL = lambda v: 0xA0000000 <= v < 0xC0000000

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, d))
    return d


assert gate(Z, 'in') == 0

_, mats, _d6, ts_spans, ts_name, ts_idx = scan(SRC)
ts_spans = dict(ts_spans)
for k, s in TAIL_TS.items():
    ts_spans[k] = (s, None)
print('materials: %d   techsets: %d (incl. %d tail)' % (len(mats), len(ts_spans), len(TAIL_TS)))

demand = {}
for i, (s, e) in ts_spans.items():
    try:
        demand[i] = set(v for (t, d, v) in techset_args(orig, s)
                        if t in CONST_TYPES and v not in PTRS)
    except Exception:
        demand[i] = set()

name_of = {i: ts_name(s) for i, (s, e) in ts_spans.items()}
groups = defaultdict(list)
for i, nm in name_of.items():
    if nm:
        groups[nm].append(i)

by_group = defaultdict(list)
unwalked = []
for mm in mats:
    if not AL(mm['ts']):
        continue
    k = ts_idx(mm['ts'])
    if k is None:
        continue
    if k not in demand:
        unwalked.append(mm)
        continue
    nm = name_of.get(k)
    by_group[nm if nm else ('#idx%d' % k)].append(mm)
print('binds with no walked techset: %d' % len(unwalked))
for mm in unwalked:
    print('   UNAUDITED @%-9d %s' % (mm['_off'], (mm['name'] or '?')[:60]))

plan = {}
nofix = []
for nm, ms in by_group.items():
    members = groups.get(nm, [])
    if not members:
        continue
    union = set()
    for j in members:
        union |= demand.get(j, set())
    if not union:
        continue
    S = set.intersection(*[set(m['consts']) for m in ms])
    bad = union - S
    if not bad:
        continue
    if not S:
        nofix.append((nm, members, len(ms)))
        continue
    plan[nm] = (bad, min(S), members, len(ms))

print('name-groups to remap: %d (bad hashes %d, %d materials affected)'
      % (len(plan), sum(len(v[0]) for v in plan.values()),
         sum(v[3] for v in plan.values())))
for nm, (bad, tgt, members, nmats) in sorted(plan.items()):
    print('   %-40s members=%-12s mats=%-3d bad(%d) -> 0x%08x'
          % (nm[:40], members, nmats, len(bad), tgt))
if nofix:
    print('!! groups with NO common constant (cannot remap): %d' % len(nofix))
    for nm, members, n in nofix:
        print('   %-40s members=%s mats=%d' % (nm[:40], members, n))

# closed loop: the boot-36 miss set must be covered (dump 36316 arg neighborhood)
B36 = {0x19cc0727, 0x95bacba2, 0x9ea1a764, 0x9ea1a765, 0x9ea1a767}
cover = set()
for nm, (bad, tgt, members, nmats) in plan.items():
    cover |= bad
missing_cover = B36 - cover
print('closed loop: boot-36 missing hashes covered: %d/%d %s'
      % (len(B36 - missing_cover), len(B36),
         'OK' if not missing_cover else '*** %s NOT covered' % ['0x%08x' % h for h in missing_cover]))

# shadow check
import wiiu_ff
UPD = r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\content\english'
BASE_DIR = r'E:\Wii U Black ops 2\content\english'
EARLIER = ['patch_mp.ff', 'patch.ff', 'common_mp.ff', 'common_patch_mp.ff', 'ui_mp.ff',
           'patch_ui_mp.ff', 'code_post_gfx_mp.ff', 'code_post_gfx.ff', 'code_pre_gfx_mp.ff',
           'faction_pmc_mp.ff', 'faction_fbi_mp.ff', 'dlc0_load_mp.ff', 'dlc1_load_mp.ff',
           'seasonpass_load_mp.ff']


def get_zone(p):
    r = wiiu_ff.decrypt(open(p, 'rb').read())
    if isinstance(r, dict):
        return [v for v in r.values() if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    if isinstance(r, tuple):
        return [v for v in r if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    return bytes(r)


blobs = []
for fn in EARLIER:
    for D, tag in ((UPD, 'UPDATE'), (BASE_DIR, 'base')):
        p = os.path.join(D, fn)
        if os.path.exists(p):
            try:
                blobs.append((fn, tag, get_zone(p)))
            except Exception as e:
                print('  decrypt failed %s/%s: %s' % (tag, fn, str(e)[:40]))
            break
shadowed = {}
for nm in plan:
    needle = nm.encode() + b'\x00'
    for (fn, tag, B) in blobs:
        if B.find(needle) != -1:
            shadowed[nm] = '%s/%s' % (tag, fn)
            break
print('remap groups SHADOWED by an earlier zone (%d searched): %d'
      % (len(blobs), len(shadowed)))
for nm, where in sorted(shadowed.items()):
    print('   %-40s shadowed by %s' % (nm[:40], where))

if '--apply' not in sys.argv:
    print('\nDRY RUN — no bytes written')
    sys.exit(0)

assert not shadowed and not nofix and not unwalked and not missing_cover

n_args = 0
for nm, (bad, target, members, _n) in plan.items():
    for k in members:
        s = ts_spans[k][0]
        passes, _ = walk_techset(bytes(Z), s)
        for p in passes:
            base = p['args_off']
            for j in range(p['nargs']):
                a = base + j * 8
                if be16(Z, a) in CONST_TYPES and be32(Z, a + 4) in bad:
                    struct.pack_into('>I', Z, a + 4, target)
                    n_args += 1
print('remapped %d type-0/6 args' % n_args)

assert len(Z) == len(orig), 'ZONE GREW — forbidden'
assert gate(Z, 'out') == 0

dem2 = {i: set(v for (t, d, v) in techset_args(bytes(Z), s)
               if t in CONST_TYPES and v not in PTRS) for i, (s, e) in ts_spans.items()}
bad_idx = bad_grp = 0
for mm in mats:
    if not AL(mm['ts']):
        continue
    k = ts_idx(mm['ts'])
    if k is None or k not in dem2:
        continue
    cs = set(mm['consts'])
    if dem2[k] - cs:
        bad_idx += 1
    nm = name_of.get(k)
    union = set()
    for j in (groups.get(nm) or [k]):
        union |= dem2.get(j, set())
    if union - cs:
        bad_grp += 1
print('post-patch unsatisfied {0,6}: per-index=%d name-group=%d (both must be 0)'
      % (bad_idx, bad_grp))
assert bad_idx == 0 and bad_grp == 0

changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d (size-neutral)' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
