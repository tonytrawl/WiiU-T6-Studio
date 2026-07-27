#!/usr/bin/env python3
"""gfxtail29: remap unsatisfiable SAMPLER demands (boot-35 front, HANDOFF §1).

The texture analog of gfxtail18: T6 pools techsets BY NAME, so the plan is keyed
by TECHSET NAME-GROUP. For each group G:
    S   = intersection of texdef-hash sets of ALL materials binding to ANY member
    bad = (union of type-2 sampler demands of all members) - S
    rewrite every type-2 arg VALUE in EVERY member of G whose hash is in bad -> min(S)
Closed loop on boot 35: the faulting material wpc/water_ocean_mp_skate @78444883
(DB 0x104e3338 in dump 27040) binds ts716 lit_sm_r0c0n0s0_b1c1n1s1 and misses the
demanded 0x9434aede — it must appear in the plan and be clean afterwards.

SHADOWING: any group we remap whose name is ALSO registered by an earlier-loading
zone (patch_mp etc.) is served the GENUINE body at runtime — our remap would be
dead. Those need the gfxtail19 rename treatment; this script CHECKS and reports;
it refuses to apply while a remapped group is shadowed.

CONSTRAINTS: arg VALUES only, never counts; size-neutral; byte-granular scans;
clipMap gate in+out. Built from gfxtail18 (deployed) + _sampler_oracle (raid 0/0).
Usage: python _patch_gfxtail29_sampler_remap.py [--apply]
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
from _sampler_oracle import mat_texhashes, techset_args

SRC = 'mp_skate_gfxtail28.zone'
DST = 'mp_skate_gfxtail29.zone'
FF = 'mp_skate_gfxtail29.ff'
BB = 84512493
ARG_SAMPLER = 2                 # derived on raid: 47/50 distinct values in texvocab,
                                # 100% on skate; every other type scores 0%
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

# ---- audit populations (scan = the _nullct_oracle walker, walk_material inside) ----
_, mats, _demand6, ts_spans, ts_name, ts_idx = scan(SRC)
print('materials: %d   techsets walked: %d' % (len(mats), len(ts_spans)))

carried = {}
for mm in mats:
    hs, kind = mat_texhashes(orig, mm['_off'])
    carried[mm['_off']] = set(hs) if hs is not None else None
assert all(v is not None for v in carried.values()), 'aliased texdef table — resolve first'

demand = {}
for i, (s, e) in ts_spans.items():
    try:
        demand[i] = set(v for (t, d, v) in techset_args(orig, s)
                        if t == ARG_SAMPLER and v not in PTRS)
    except Exception:
        demand[i] = set()

name_of = {i: ts_name(s) for i, (s, e) in ts_spans.items()}
groups = defaultdict(list)
for i, nm in name_of.items():
    if nm:
        groups[nm].append(i)

# ---- bind materials to name-groups; report EVERY unaudited bind (§6.6) ----
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
print('binds beyond the sim break: %d' % len(unwalked))
for mm in unwalked:
    print('   UNAUDITED @%-9d texc=%-3d %s' % (mm['_off'], mm['texc'], (mm['name'] or '?')[:60]))

# ---- plan per name-group ----
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
    S = set.intersection(*[carried[m['_off']] for m in ms])
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
    print('   %-40s members=%-12s mats=%-3d bad=%s -> 0x%08x'
          % (nm[:40], members, nmats, ['0x%08x' % h for h in sorted(bad)], tgt))
if nofix:
    print('!! groups with NO common texture (cannot remap -> need repoint): %d' % len(nofix))
    for nm, members, n in nofix:
        print('   %-40s members=%s mats=%d' % (nm[:40], members, n))

# ---- closed loop: the boot-35 victim must be covered ----
water = [mm for mm in mats if mm['name'] == 'wpc/water_ocean_mp_skate']
assert len(water) == 1
wk = ts_idx(water[0]['ts'])
wnm = name_of.get(wk)
assert wnm in plan, 'boot-35 victim group %r NOT in plan' % wnm
assert 0x9434aede in plan[wnm][0], '0x9434aede not among the group bad hashes'
print('closed loop: water group %r in plan, 0x9434aede covered -> OK' % wnm)

# ---- shadowing check for every group we touch ----
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
print('earlier-loading zones searched: %d' % len(blobs))
shadowed = {}
for nm in plan:
    needle = nm.encode() + b'\x00'
    for (fn, tag, B) in blobs:
        if B.find(needle) != -1:
            shadowed[nm] = '%s/%s' % (tag, fn)
            break
print('remap groups SHADOWED by an earlier zone: %d' % len(shadowed))
for nm, where in sorted(shadowed.items()):
    print('   %-40s shadowed by %s' % (nm[:40], where))

# ---- the 4 beyond-sim-break binds: audit against their manually-located bodies
# (_locate_tail_techsets.py; row 803 has ptr word 0xf06d1815 = NO inline body, no
# bound material; row 817 = ',trivial_9z33feqw' with ZERO techniques). All four
# must be satisfied or the apply refuses.
TAIL_TS = {804: 84317304, 815: 89654859, 817: 89678237, 819: 89678868}
tail_bad = 0
for mm in unwalked:
    k = ts_idx(mm['ts'])
    s = TAIL_TS.get(k)
    if s is None:
        print('   !! unaudited bind @%d -> asset %s with NO known body' % (mm['_off'], k))
        tail_bad += 1
        continue
    dem = set(v for (t, d, v) in techset_args(orig, s) if t == ARG_SAMPLER and v not in PTRS)
    miss = dem - carried[mm['_off']]
    print('   tail bind @%-9d %-40s ts%d miss=%s'
          % (mm['_off'], (mm['name'] or '?')[:40], k,
             ['0x%08x' % h for h in sorted(miss)] or 'NONE'))
    if miss:
        tail_bad += 1

if '--apply' not in sys.argv:
    print('\nDRY RUN — no bytes written')
    sys.exit(0)

assert not shadowed, 'shadowed remap groups present — rename them first (gfxtail19 pattern)'
assert not nofix, 'S-empty groups present — repoint those materials first'
assert tail_bad == 0, 'tail binds unsatisfied/unlocated — extend the plan first'

# ---- apply: rewrite type-2 arg VALUES only, in EVERY member of each group ----
n_args = 0
for nm, (bad, target, members, _n) in plan.items():
    for k in members:
        s, e = ts_spans[k]
        passes, _ = walk_techset(bytes(Z), s)
        for p in passes:
            base = p['args_off']
            for j in range(p['nargs']):
                a = base + j * 8
                if be16(Z, a) == ARG_SAMPLER and be32(Z, a + 4) in bad:
                    struct.pack_into('>I', Z, a + 4, target)
                    n_args += 1
print('remapped %d type-2 args' % n_args)

assert len(Z) == len(orig), 'ZONE GREW — forbidden'
assert gate(Z, 'out') == 0

# ---- verify BOTH keyings on the patched zone ----
dem2 = {i: set(v for (t, d, v) in techset_args(bytes(Z), s)
               if t == ARG_SAMPLER and v not in PTRS) for i, (s, e) in ts_spans.items()}
bad_idx = bad_grp = 0
for mm in mats:
    if not AL(mm['ts']):
        continue
    k = ts_idx(mm['ts'])
    if k is None or k not in dem2:
        continue
    ch = carried[mm['_off']]
    if dem2[k] - ch:
        bad_idx += 1
    nm = name_of.get(k)
    union = set()
    for j in (groups.get(nm) or [k]):
        union |= dem2.get(j, set())
    if union - ch:
        bad_grp += 1
print('post-patch unsatisfied: per-index=%d name-group=%d (both must be 0)'
      % (bad_idx, bad_grp))
assert bad_idx == 0 and bad_grp == 0

changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d (size-neutral)' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
