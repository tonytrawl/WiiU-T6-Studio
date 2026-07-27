#!/usr/bin/env python3
"""
_check_ts_coverage.py — is the boot-20 faulting techset one of the ones loader_sim never walks?

Faulting material (dump): techniqueSet name 'lit_sm_r0c0n0x0_b1c1n1s1_b2c2n2v2',
constants {colorTint2, colorTint1, alphaRevealP(0x88befc31), occlusionAmo, colorTint}
-> demanded 0x88befc32 is ABSENT -> unbounded walk -> AV.

If that techset is beyond the sim break (asset 801), gfxtail17 could not have remapped it,
which fully explains why the "0/551 missing" audit was blind to it.
"""
import re
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_zone
from _matconst_map import techset_const_hashes

SRC = 'mp_skate_gfxtail17.zone'
TARGET_NAME = b'lit_sm_r0c0n0x0_b1c1n1s1_b2c2n2v2'
WANT = 0x88befc32

Z = open(SRC, 'rb').read()
rc = wiiu_zone.ZoneReader(Z)
rc.read_string_table()
rc.read_asset_list()
n_ts = sum(1 for (c, p, nm) in rc.assets if nm == 'TECHNIQUE_SET')
ts_idx_all = [i for i, (c, p, nm) in enumerate(rc.assets) if nm == 'TECHNIQUE_SET']

em, spans, CO = LS.simulate(SRC, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}
walked = set(ts_spans)
print('TECHNIQUE_SET assets in list : %d' % n_ts)
print('walked by loader_sim         : %d' % len(walked))
unwalked = [i for i in ts_idx_all if i not in walked]
print('UNWALKED asset indices       : %s' % unwalked)

print('\n--- which walked techsets demand 0x%08x ---' % WANT)
dem = {}
for i, (s, e) in ts_spans.items():
    try:
        hashes, _ = techset_const_hashes(Z, s)
    except Exception as ex:
        continue
    dem[i] = hashes
    if WANT in hashes:
        print('  asset %d demands it' % i)

print('\n--- locate the target techset NAME in the zone ---')
for m in re.finditer(re.escape(TARGET_NAME + b'\x00'), Z):
    print('  name string @ file 0x%x (%d)' % (m.start(), m.start()))

# Which walked span (if any) contains that name?
hit = None
for m in re.finditer(re.escape(TARGET_NAME + b'\x00'), Z):
    for i, (s, e) in ts_spans.items():
        if s <= m.start() < e:
            hit = (i, s, e, m.start())
print('\ntarget techset inside a WALKED span? %s' % (('YES asset %d span[%d,%d) name@%d' % hit) if hit else 'NO -> it is beyond the sim break -> NEVER remapped'))
