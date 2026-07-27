#!/usr/bin/env python3
"""PHASE 2 CORRECTION: G4's "genuine reads 0 unsatisfied" is measured with a walker
that SKIPS aliased technique slots (they consume 0 zone bytes). Genuine raid has 559
such slots across 165 techsets; OUR zone has ZERO (we emit alias-free corpus blobs).
So genuine's demand is systematically UNDER-measured and the comparison is unfair.

Re-measure BOTH zones at ALIAS-RESOLVED, PER-TECHNIQUE-SLOT granularity:
  - genuine raid: use the corpus blob (alias-free extraction of that same zone)
  - ours:         the zone bodies are already alias-free
and report, per technique slot index, how many materials cannot satisfy it.
"""
import sys, os, json, struct
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import _p2_inv as INV
import techset_translate as TT
import shader_probe as SP
from _matconst_map import be32, be16, walk_technique, PTRS, FOLLOW
from _p1_dump13 import nm

CONST_TYPES = (0, 6)
SAMPLER = 2


def per_slot_demands(d, s):
    """slot index -> (const set, sampler set) for an ALIAS-FREE techset body at s."""
    slots = [be32(d, s + 8 + i * 4) for i in range(32)]
    c = SP.Cur(d, s + 136)
    if be32(d, s) == FOLLOW:
        c.cstr(160)
    out = {}
    for i, v in enumerate(slots):
        if v != FOLLOW:
            continue
        end, passes = walk_technique(d, c.o)
        cc, ss = set(), set()
        for p in passes:
            for j in range(p['nargs']):
                a = p['args_off'] + j * 8
                t, val = be16(d, a), be32(d, a + 4)
                if val in PTRS:
                    continue
                if t in CONST_TYPES:
                    cc.add(val)
                elif t == SAMPLER:
                    ss.add(val)
        out[i] = (cc, ss)
        c.o = end
    return out


CORPUS = json.load(open(os.path.join(TT.CORPUS_DIR, 'index.json')))


def corpus_slots(name):
    m = CORPUS.get(name)
    if not m or m.get('kind') != 'inline':
        return None
    p = m['path']
    if not os.path.isabs(p):
        p = os.path.join(TT.ROOT, p)
    try:
        blob = open(p, 'rb').read()
    except OSError:
        return None
    try:
        return per_slot_demands(blob, 0)
    except Exception:
        return None


def analyse(path, label, use_corpus):
    inv = INV.build(path, verbose=False)
    Z, TS, MATS = inv['Z'], inv['TS'], inv['MATS']
    slotdem = {}
    n_corp = n_zone = 0
    for k, t in TS.items():
        ps = corpus_slots(t['name']) if use_corpus else None
        if ps is not None:
            n_corp += 1
        else:
            try:
                ps = per_slot_demands(Z, t['span'])
                n_zone += 1
            except Exception:
                ps = {}
        slotdem[k] = ps
    per_slot = Counter()
    mats_bad = set()
    pairs = 0
    for M in MATS:
        ps = slotdem.get(M['k'])
        if not ps:
            continue
        for i, (cc, ss) in ps.items():
            if (cc - M['consts']) or (ss - M['tex']):
                per_slot[i] += 1
                mats_bad.add(M['off'])
                pairs += 1
    print('%-22s materials=%-6d techsets=%-4d (demand from corpus=%d / zone=%d)'
          % (label, len(MATS), len(TS), n_corp, n_zone))
    print('   materials with >=1 unsatisfiable technique slot: %d (%.2f%%)'
          % (len(mats_bad), 100.0 * len(mats_bad) / max(len(MATS), 1)))
    print('   (material, slot) unsatisfiable pairs: %d' % pairs)
    print('   top slots: %s' % sorted(per_slot.items(), key=lambda x: -x[1])[:14])
    return inv, slotdem, per_slot, mats_bad


print('=== GENUINE RAID, alias-resolved via corpus ===')
gi, gs, gps, gbad = analyse('../wiiu_ref/mp_raid_genuine.zone', 'raid genuine', True)
print()
print('=== GENUINE RAID, as G4 measures it (zone bodies, aliases skipped) ===')
gi2, gs2, gps2, gbad2 = analyse('../wiiu_ref/mp_raid_genuine.zone', 'raid genuine (G4)', False)
print()
print('=== OURS (zone bodies are already alias-free) ===')
oi, os_, ops, obad = analyse('mp_skate_final.zone', 'mp_skate_final', False)
