#!/usr/bin/env python3
"""PHASE 2 correction check: walk_techset() only walks technique slots == FOLLOW.
Slots holding an ALIAS pointer consume 0 bytes and are SKIPPED -> the measured
demand UNDERCOUNTS. Quantify how many techsets in each zone have aliased slots,
and re-measure genuine raid's satisfaction with alias-resolved demands."""
import sys, os, struct
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import _p2_inv as INV
from _matconst_map import be32, be16, walk_technique, PTRS, FOLLOW
from _p1_dump13 import nm

AL = lambda v: 0xA0000000 <= v < 0xC0000000


def slot_profile(Z, s):
    slots = [be32(Z, s + 8 + i * 4) for i in range(32)]
    return Counter('FOLLOW' if v == FOLLOW else ('ZERO' if v == 0 else
                   ('ALIAS' if AL(v) else 'OTHER:%08x' % v)) for v in slots)


def report(path):
    inv = INV.build(path)
    Z, TS = inv['Z'], inv['TS']
    tot = Counter()
    with_alias = []
    for k, t in TS.items():
        p = slot_profile(Z, t['span'])
        tot.update(p)
        if p.get('ALIAS'):
            with_alias.append((k, t['name'], p['ALIAS'], p['FOLLOW']))
    print('  slot values across %d techsets: %s' % (len(TS), dict(tot)))
    print('  techsets with >=1 ALIASED technique slot: %d' % len(with_alias))
    for k, n, a, f in with_alias[:12]:
        print('     k=%-4d %-44s alias=%d follow=%d' % (k, n[:44], a, f))
    return inv, with_alias


print('=== OURS ===')
inv_o, al_o = report('mp_skate_final.zone')
print('\n=== GENUINE RAID ===')
inv_g, al_g = report('../wiiu_ref/mp_raid_genuine.zone')

print('\n=== genuine raid: which materials bind wpc_shadowcaster_wj6w5j60, and what do they carry? ===')
NAME = 'wpc_shadowcaster_wj6w5j60'
ks = set(inv_g['name2k'].get(NAME, []))
n = 0
for M in inv_g['MATS']:
    if M['k'] in ks:
        n += 1
        if n <= 10:
            print('  %-44s texc=%d tex=%s' % (M['name'][:44], M['texc'],
                                              ' '.join(sorted(nm(h) for h in M['tex']))))
print('  total genuine materials bound to it: %d' % n)

print('\n=== ours: which materials bind it ===')
ks = set(inv_o['name2k'].get(NAME, []))
n = 0
for M in inv_o['MATS']:
    if M['k'] in ks:
        n += 1
        if n <= 10:
            print('  %-44s texc=%d tex=%s' % (M['name'][:44], M['texc'],
                                              ' '.join(sorted(nm(h) for h in M['tex']))))
print('  total our materials bound to it: %d' % n)
