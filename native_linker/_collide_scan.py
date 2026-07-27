#!/usr/bin/env python3
"""
_collide_scan.py — which of skate's techsets are SHADOWED by an earlier-loading zone?

Mechanism (confirmed at instruction level):
  Load_MaterialTechniqueSetAsset -> DB_LinkXAssetEntry -> lwz r0,4(r31); stw r0,0(r30)
  i.e. our zone's techset pointer is OVERWRITTEN with the POOLED entry (first registration wins).
Zones loading before the map (patch_mp, common_mp, ui_mp, code_post_gfx*, faction_*, dlc*_load_mp)
register first, so any skate techset sharing a name with them is DISCARDED and its materials are
served the genuine body instead.

Renaming our copy is safe for by-name lookups (Material_FindTechniqueSet -> DB_FindXAssetHeader):
the genuine copy keeps the original name registered, so lookups still resolve.

The rename set = skate techsets that (a) collide with an earlier zone AND (b) we REMAPPED
(gfxtail1 -> gfxtail18 arg changes inside their span). (b) marks exactly the techsets whose
demands our materials could not satisfy -- the ones that crash when the genuine body wins.
"""
import os
import re
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_ff
from _matconst_map import FOLLOW, be32

SKATE = 'mp_skate_gfxtail18.zone'
ORIG_BUILD = 'mp_skate_gfxtail1.zone'
UPD = r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\content\english'
BASE = r'E:\Wii U Black ops 2\content\english'
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


Z = open(SKATE, 'rb').read()
Z1 = open(ORIG_BUILD, 'rb').read()
em, spans, CO = LS.simulate(SKATE, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}


def ts_name(s):
    if be32(Z, s) != FOLLOW:
        return None
    e = Z.find(b'\x00', s + 136)
    nm = bytes(Z[s + 136:e]).decode('latin1', 'replace')
    return nm if nm and all(32 <= c < 127 for c in nm.encode()) else None


names = {i: ts_name(s) for i, (s, e) in ts_spans.items()}
print('skate techsets walked: %d  (named: %d)' % (len(ts_spans), sum(1 for v in names.values() if v)))

# (b) which did WE remap? diff gfxtail1 vs gfxtail18 inside each span
remapped = set()
same_len = len(Z) == len(Z1)
for i, (s, e) in ts_spans.items():
    if same_len and Z[s:e] != Z1[s:e]:
        remapped.add(i)
print('skate techsets whose bytes WE changed (gfxtail1->18): %d' % len(remapped))

# (a) collisions with earlier-loading zones
uniq = sorted({v for v in names.values() if v})
blobs = []
for f in EARLIER:
    for D, tag in ((UPD, 'UPDATE'), (BASE, 'base')):
        p = os.path.join(D, f)
        if os.path.exists(p):
            try:
                blobs.append((f, tag, get_zone(p)))
            except Exception as e:
                print('  decrypt failed %s/%s: %s' % (tag, f, str(e)[:30]))
            break
print('earlier-loading zones searched: %d' % len(blobs))

collide = {}
for nm in uniq:
    needle = nm.encode() + b'\x00'
    for (f, tag, B) in blobs:
        if B.find(needle) != -1:
            collide[nm] = '%s/%s' % (tag, f)
            break
print('\nskate techset names ALSO present in an earlier zone: %d / %d' % (len(collide), len(uniq)))

danger = []
for i, nm in sorted(names.items()):
    if nm and nm in collide and i in remapped:
        danger.append((i, nm, collide[nm]))
print('\n*** SHADOWED **AND** REMAPPED (these crash — the rename set): %d' % len(danger))
for (i, nm, where) in danger:
    print('   asset %-4d %-40s shadowed by %s' % (i, nm, where))

safe = [(i, names[i]) for i in sorted(remapped) if names.get(i) and names[i] not in collide]
print('\n(remapped but NOT shadowed -> our copy is used, remap works): %d' % len(safe))
