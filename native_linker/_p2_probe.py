#!/usr/bin/env python3
"""PHASE 2 probe: (a) duplicate techset names in our zone and whether their BODIES
differ; (b) the shadowcaster body-demand defect vs the genuine corpus blob;
(c) demand-subset candidate search for the residual *67n_135."""
import sys, os, re, json, struct
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import _p2_inv as INV
import techset_translate as TT
from _p1_dump13 import nm

inv = INV.build('mp_skate_final.zone')
Z, TS, name2k, MATS = inv['Z'], inv['TS'], inv['name2k'], inv['MATS']

print('\n=== (a) duplicate techset NAMES in our zone ===')
dups = {n: ks for n, ks in name2k.items() if len(ks) > 1}
print('names with >1 slot: %d' % len(dups))
ndiff = 0
for n, ks in sorted(dups.items()):
    sigs = set()
    for k in ks:
        t = TS[k]
        sigs.add((frozenset(t['dc'] or ()), frozenset(t['ds'] or ())))
    tag = 'SAME-demand' if len(sigs) == 1 else '*** DEMANDS DIFFER ***'
    if len(sigs) > 1:
        ndiff += 1
    print('  %-44s slots=%s  %s' % (n[:44], ks, tag))
    if len(sigs) > 1:
        for k in ks:
            t = TS[k]
            print('       k=%-4d const=%s samp=%s' % (
                k, ' '.join(sorted(nm(h) for h in t['dc'])),
                ' '.join(sorted(nm(h) for h in t['ds']))))
print('duplicate names whose bodies DEMAND DIFFERENTLY: %d' % ndiff)

print('\n=== (b) shadowcaster: our zone vs genuine corpus blob ===')
for k in name2k.get('wpc_shadowcaster_wj6w5j60', []):
    t = TS[k]
    print('  ours   k=%-4d const=%s  samp=%s' % (
        k, ' '.join(sorted(nm(h) for h in t['dc'])),
        ' '.join(sorted(nm(h) for h in t['ds']))))
gen = INV.build('../wiiu_ref/mp_raid_genuine.zone', verbose=False)
for k in gen['name2k'].get('wpc_shadowcaster_wj6w5j60', []):
    t = gen['TS'][k]
    print('  GENUINE k=%-4d const=%s  samp=%s' % (
        k, ' '.join(sorted(nm(h) for h in t['dc'])),
        ' '.join(sorted(nm(h) for h in t['ds']))))

print('\n=== (c) demand-subset candidates for the residual materials ===')
for want_name in ['*67n_135(', 'wpc/shadowcaster']:
    M = None
    for m in MATS:
        if m['name'].startswith(want_name.rstrip('(')) or m['name'].startswith(want_name):
            M = m
            break
    if M is None:
        print('  %s NOT FOUND' % want_name); continue
    print('\n  %s' % M['name'][:80])
    print('    carries tex=%s const=%s'
          % (' '.join(sorted(nm(h) for h in M['tex'])),
             ' '.join(sorted(nm(h) for h in M['consts']))))
    cands = []
    for k, t in TS.items():
        if t['dc'] is None:
            continue
        if (t['dc'] - M['consts']) or (t['ds'] - M['tex']):
            continue
        cands.append((len(t['dc']) + len(t['ds']), k, t['name']))
    cands.sort(reverse=True)
    print('    console techsets whose demand is a SUBSET of the carry: %d' % len(cands))
    for sc, k, n in cands[:10]:
        print('       used=%-3d k=%-4d %s' % (sc, k, n))
