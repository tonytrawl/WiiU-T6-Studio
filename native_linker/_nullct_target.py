#!/usr/bin/env python3
"""
_nullct_target.py — pick the REPOINT target for the boot-23 decals.

_nullct_oracle_pair.py proved the defect is the BINDING, not the material:
    raid : wpc/decal_damage_wall_fillet constc=0 -> wpc_unlitdecalblend_multiply_35079164 (demands NOTHING)
    skate: wpc/decal_damage_wall_fillet constc=0 -> wpc_unlit_replace_4688792e           (demands 0xe27483cf)
`wpc_unlit` prefixes BOTH -> techset_translate.py's name-prefix substitution mis-picked.

The material's techSet* (+80) is a block-5 alias into the asset array: a 4-byte VALUE that
consumes NO stream bytes. Repointing it is strictly size-neutral (rtmap + gfxtail stack stay
valid). This script finds, for each of the 10, a skate techset that (a) exists in skate's own
asset array and (b) demands NOTHING.

Also: build a hash -> constant-NAME map out of every material constant table in both zones
(CONSTDEF = 32B: nameHash@0, name[12]@4, literal[4]f@16) to identify 0xe27483cf / 0x88befc31.
"""
import re
import struct
import sys
from collections import defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
from _matconst_map import CONSTDEF, FOLLOW, be32
from _nullct_oracle import scan

RAID = '../wiiu_ref/mp_raid_genuine.zone'
SKATE = 'mp_skate_gfxtail19.zone'


def hash_names(path):
    """hash -> name, harvested from every located material's constantTable."""
    Z, mats, demand, ts_spans, ts_name, ts_idx = scan(path)
    hn = {}
    for m in mats:
        if not m['constc'] or m['ct_off'] is None:
            continue
        for k in range(m['constc']):
            c = m['ct_off'] + k * CONSTDEF
            h = be32(Z, c)
            nm = bytes(Z[c + 4:c + 16]).split(b'\x00')[0].decode('latin1', 'replace')
            if nm:
                hn.setdefault(h, nm)
    return hn, (Z, mats, demand, ts_spans, ts_name, ts_idx)


print('harvesting constant names ...')
hn_s, S = hash_names(SKATE)
hn_r, R = hash_names(RAID)
HN = dict(hn_r); HN.update(hn_s)
print('  known constant hashes: skate %d + raid %d -> %d union' % (len(hn_s), len(hn_r), len(HN)))
for h in (0xe27483cf, 0x88befc31, 0x88befc32, 0x00e262b2, 0x7793a248):
    print('    0x%08x = %s' % (h, HN.get(h, '<unknown>')))

Zs, mats_s, dem_s, spans_s, tsname_s, tsidx_s = S

# --- every skate techset: name + demand ---
allts = {}
for k, (s, e) in spans_s.items():
    allts[k] = (tsname_s(s) or '<noname>', dem_s.get(k, set()))

print('\n%s\nSKATE techsets matching *unlitdecalblend* / *decal* :\n%s' % ('=' * 78, '=' * 78))
for k, (nm, d) in sorted(allts.items()):
    if 'decal' in nm.lower():
        print('  ts %-4d %-46s demands %s' % (k, nm, ['0x%08x' % h for h in sorted(d)] or 'NOTHING'))

# --- who else binds to 678 / 699 ? (is the techset shared with satisfied materials?) ---
bound = defaultdict(list)
for m in mats_s:
    k = tsidx_s(m['ts'])
    if k is not None:
        bound[k].append(m)

for k in (678, 699):
    nm, d = allts.get(k, ('?', set()))
    lst = bound.get(k, [])
    print('\n%s\nts %d  %s   demands %s\n%s'
          % ('=' * 78, k, nm, ['0x%08x' % h for h in sorted(d)], '=' * 78))
    print('  bound materials: %d' % len(lst))
    for m in sorted(lst, key=lambda x: x['constc']):
        miss = d - set(m['consts'])
        print('    constc=%-3d %-52s %s' % (m['constc'], m['name'][:52],
                                            'MISSING' if miss else 'ok'))

# --- candidate zero-demand targets, by name family ---
print('\n%s\nZERO-DEMAND skate techsets by prefix family\n%s' % ('=' * 78, '=' * 78))
fam = defaultdict(list)
for k, (nm, d) in allts.items():
    if not d:
        fam[nm.split('_')[0]].append((k, nm))
for p in ('wpc', 'mc', 'mlv', 'lit'):
    got = sorted(fam.get(p, []))
    print('  %-4s : %d zero-demand techsets' % (p, len(got)))
    for k, nm in got[:14]:
        print('        ts %-4d %s' % (k, nm))
