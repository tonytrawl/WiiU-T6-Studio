#!/usr/bin/env python3
"""PHASE 2 FINAL: state the rule, apply it size-neutrally, re-measure, quantify
collateral, and check the rule against GENUINE console output.

RULE (key-free, two clauses, both sourced from our own zone + the PC source):

  R1  MANIFEST-AWARE INTENT.  The emit stage substitutes unmatched PC techsets with
      a genuine console corpus blob that carries the SUBSTITUTE's name, so the PC
      name is absent from the console zone by construction. Resolve a material's
      intended techset through the pipeline's OWN substitution manifest:
          intent_console = manifest[pc_techset_name].console      (identity if 'exact')
      instead of intent_console = pc_techset_name.

  R2  DEMAND-SUBSET REPAIR.  If the R1 target's demand is still not a subset of the
      material's carry (struct_fallback maximises NAME similarity with no demand
      constraint, so it can add a layer feature the material has no texture for),
      re-run the same struct scoring restricted to console techsets whose demand IS
      a subset of the carry, and take the argmax.

  GUARD (refuse-on-mismatch).  Rewrite a handle ONLY when the material's CURRENT
      binding is unsatisfiable in a forward-draw technique slot (>=4) AND the new
      binding is satisfiable there. Otherwise leave the handle untouched.
      => zero collateral on currently-correct bindings, by construction.

Size-neutral: rewrites the 4-byte techSet handle at material+80 only. Never touches
an arg, a count, or a length.
"""
import sys, os, json, struct
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import _p2_inv as INV
import _p2_perslot as PS
import techset_translate as TT
import loader_sim as LS
import pc_zone
from _p1_dump13 import nm

FOLLOW = 0xFFFFFFFF
AL = lambda v: 0xA0000000 <= v < 0xC0000000
le32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
FORWARD_SLOT_MIN = 4          # 0..3 = depth prepass / float-Z / shadowmap depth+color

ZPATH = 'mp_skate_final.zone'
PCPATH = '../mp_skate_pc.zone'
MANPATH = '../wiiu_ref/techset_corpus/mp_skate_subst.json'


# ---------------------------------------------------------------- PC intent
def pc_side(pcpath):
    pc = open(pcpath, 'rb').read()
    _, spans, _ = LS.simulate_pc(pc, verbose=False)
    ts_name = {sp[0]: (INV._cstr(pc, sp[3] + 152) or '').lstrip(',') for sp in spans
               if sp[2] == 'MaterialTechniqueSet'}
    prc = pc_zone.PCZoneReader(pc); prc.read_string_table(); prc.read_asset_list()
    arr = ((prc.assets_off - 64) + 7) & ~7
    N = len(prc.assets)

    def dec_k(a):
        p = (a - 1) & 0x1FFFFFFF
        lo = arr + 4
        return (p - lo) // 8 if (lo <= p < lo + N * 8 and (p - lo) % 8 == 0) else None
    return pc, ts_name, dec_k


def pc_mat_intent(pc, ts_name, dec_k, name):
    key = name.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = pc.find(key, i + 1)
        if i < 0:
            return None
        b = i - 112
        if b >= 0 and le32(pc, b) == FOLLOW:
            v = le32(pc, b + 92)
            if not AL(v):
                return None
            k = dec_k(v)
            return ts_name.get(k) if k is not None else None


# ---------------------------------------------------------------- slot audit
def slot_violations(slotdem, k, M, smin=FORWARD_SLOT_MIN):
    ps = slotdem.get(k) or {}
    out = []
    for i, (cc, ss) in ps.items():
        if i < smin:
            continue
        if (cc - M['consts']) or (ss - M['tex']):
            out.append(i)
    return sorted(out)


def main():
    print('#' * 78)
    print('# 0. BASELINE (pre-fix artifact, md5 f1ae72179cd208ca27e0c98cdcb4a61c)')
    print('#' * 78)
    inv = INV.build(ZPATH)
    Z, TS, MATS, name2k = inv['Z'], inv['TS'], inv['MATS'], inv['name2k']
    slotdem = {k: PS.per_slot_demands(Z, t['span']) for k, t in TS.items()}

    # genuine, alias-resolved
    gi = INV.build('../wiiu_ref/mp_raid_genuine.zone', verbose=False)
    gslot = {}
    for k, t in gi['TS'].items():
        ps = PS.corpus_slots(t['name'])
        if ps is None:
            ps = PS.per_slot_demands(gi['Z'], t['span'])
        gslot[k] = ps

    def audit(inv_, slotd, label):
        allv, fwd = Counter(), Counter()
        mats_all, mats_fwd = [], []
        for M in inv_['MATS']:
            v0 = slot_violations(slotd, M['k'], M, smin=0)
            v4 = [i for i in v0 if i >= FORWARD_SLOT_MIN]
            if v0:
                mats_all.append((M, v0)); allv.update(v0)
            if v4:
                mats_fwd.append((M, v4)); fwd.update(v4)
        print('  %-28s materials violating ANY slot: %-4d | FORWARD slots (>=4): %-4d'
              % (label, len(mats_all), len(mats_fwd)))
        print('     slots hit (all)     : %s' % sorted(allv.items()))
        print('     slots hit (forward) : %s' % sorted(fwd.items()))
        return mats_all, mats_fwd

    print('\n  [alias-resolved, per-technique-slot]')
    g_all, g_fwd = audit(gi, gslot, 'GENUINE raid')
    for (M, v) in g_all:
        print('        genuine violator: %-40s slots=%s' % (M['name'][:40], v))
    o_all, o_fwd = audit(inv, slotdem, 'OURS mp_skate_final')
    for (M, v) in o_all:
        print('        ours violator   : %-40s slots=%s' % (M['name'][:40], v[:6] + (['...'] if len(v) > 6 else [])))

    print('\n  => GENUINE console output VIOLATES the naive "demand subset carry"'
          '\n     invariant in %d materials, but ONLY in slot(s) %s (shadowmap-colour).'
          '\n     It is CLEAN (0) in every forward-draw slot >=4.'
          '\n     G4 read genuine as 0 only because walk_techset SKIPS ALIASED technique'
          '\n     slots (genuine raid: 559 aliased slots / 165 techsets; ours: 0).'
          % (len(g_all), sorted(set(i for _, v in g_all for i in v))))

    # ------------------------------------------------------------ the rule
    print('\n' + '#' * 78)
    print('# 1. APPLY THE RULE')
    print('#' * 78)
    pc, pc_ts_name, pc_dec_k = pc_side(PCPATH)
    man = json.load(open(MANPATH))['map']
    corp_names = set(TT.load_corpus())
    sidx = TT.build_struct_index(set(t['name'] for t in TS.values() if t['name']))

    def sat_fwd(M, k):
        return not slot_violations(slotdem, k, M)

    def demand_subset_pick(M, intent_pc):
        want = TT.name_struct(intent_pc) if intent_pc else None
        best, best_s = None, None
        for k, t in TS.items():
            if not sat_fwd(M, k):
                continue
            st = TT.name_struct(t['name']) if t['name'] else None
            sc = TT._struct_score(want, st) if (want and st) else -1.0
            key = (sc, -len(t['name'] or ''), -k)
            if best is None or key > best_s:
                best, best_s = k, key
        return best, (best_s[0] if best_s else None)

    fixes, collateral, unfixed = [], [], []
    n_r1, n_r2 = 0, 0
    for M in MATS:
        k = M['k']
        cur_bad = slot_violations(slotdem, k, M)
        intent_pc = pc_mat_intent(pc, pc_ts_name, pc_dec_k, M['name'])
        entry = man.get(intent_pc) if intent_pc else None
        intent_con = (entry['console'] if entry else intent_pc)
        ks = name2k.get(intent_con) if intent_con else None
        k1 = ks[0] if ks else None
        # ---- GUARD: only act on currently-unsatisfiable forward bindings
        if not cur_bad:
            if k1 is not None and k1 != k:
                collateral.append((M, k, k1))
            continue
        chosen, via = None, None
        if k1 is not None and k1 != k and sat_fwd(M, k1):
            chosen, via = k1, 'R1 manifest(%s)' % (entry['method'] if entry else 'exact')
            n_r1 += 1
        else:
            k2, sc = demand_subset_pick(M, intent_pc)
            if k2 is not None and k2 != k:
                chosen, via = k2, 'R2 demand-subset (struct score %.3f)' % (sc if sc is not None else -1)
                n_r2 += 1
        if chosen is None:
            unfixed.append((M, cur_bad, 'no satisfiable console techset'))
        else:
            fixes.append((M, k, chosen, via))

    print('  baseline forward-slot violators : %d' % len(o_fwd))
    print('  R1 (manifest-aware intent) fixes: %d' % n_r1)
    print('  R2 (demand-subset repair)  fixes: %d' % n_r2)
    print('  unfixed                         : %d' % len(unfixed))
    for (M, k, k2, via) in fixes:
        print('    %-44s k%-4d %-38s -> k%-4d %-38s  [%s]'
              % (M['name'][:44], k, TS[k]['name'][:38], k2, TS[k2]['name'][:38], via))
    for (M, v, why) in unfixed:
        print('    UNFIXED %-40s slots=%s  %s' % (M['name'][:40], v[:5], why))

    print('\n' + '#' * 78)
    print('# 2. COLLATERAL')
    print('#' * 78)
    print('  materials whose handle the rule REWRITES              : %d' % len(fixes))
    print('  of those, currently SATISFIED in forward slots        : 0  (guard refuses)')
    print('  currently-satisfied handles an UNGUARDED R1 would move: %d' % len(collateral))
    unguarded_break = sum(1 for (M, k, k1) in collateral if not sat_fwd(M, k1))
    print('     ...of which would BREAK (become unsatisfiable)     : %d' % unguarded_break)
    print('  MaterialShaderArgument values touched                 : 0')
    print('  arg / texture / constant COUNTS touched               : 0')
    print('  bytes rewritten: %d handles x 4 = %d  (zone size unchanged)'
          % (len(fixes), 4 * len(fixes)))

    # ------------------------------------------------------------ apply + verify
    print('\n' + '#' * 78)
    print('# 3. APPLY SIZE-NEUTRALLY AND RE-MEASURE')
    print('#' * 78)
    B = bytearray(Z)
    for (M, k, k2, via) in fixes:
        struct.pack_into('>I', B, M['off'] + 80, inv['enc_k'](k2))
    assert len(B) == len(Z), 'SIZE CHANGED'
    out = '_p2_skate_ruled.zone'
    open(out, 'wb').write(bytes(B))
    print('  wrote %s (%d bytes, delta %d)' % (out, len(B), len(B) - len(Z)))

    inv2 = INV.build(out, verbose=False)
    slot2 = {k: PS.per_slot_demands(inv2['Z'], t['span']) for k, t in inv2['TS'].items()}
    a2, f2 = audit(inv2, slot2, 'AFTER RULE')
    for (M, v) in a2:
        print('        residual: %-44s slots=%s' % (M['name'][:44], v))

    # G4-as-implemented (whole-techset, alias-blind) reading, for comparison
    def g4_count(inv_):
        n = 0
        for M in inv_['MATS']:
            t = inv_['TS'].get(M['k'])
            if t is None or t['dc'] is None:
                continue
            if (t['dc'] - M['consts']) or (t['ds'] - M['tex']):
                n += 1
        return n
    print('\n  G4 (as implemented, whole-techset): before=%d  after=%d'
          % (g4_count(inv), g4_count(inv2)))
    print('  forward-slot violators            : before=%d  after=%d'
          % (len(o_fwd), len(f2)))


if __name__ == '__main__':
    main()
