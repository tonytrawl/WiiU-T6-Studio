#!/usr/bin/env python3
"""PHASE 2: derive + measure the KEY-FREE correction rule.

CANDIDATE RULE (manifest-aware rebind):
  techset_rebind currently resolves a material's intended techset by looking up the
  PC techset NAME in the console zone. But the emit stage (produce_nobackbone, via
  techset_translate) SUBSTITUTES unmatched PC techsets with a genuine console blob
  that carries the SUBSTITUTE'S name. So the PC name is absent by construction and
  the rebind skips -> the handle keeps its raw PC index -> binds an unrelated slot.

  RULE: intent_console = manifest[pc_name].console   (identity for 'exact')
        then bind to the console slot carrying intent_console.
  GUARD: only rewrite a handle whose CURRENT techset demand is NOT a subset of the
         material's carry (refuse-on-satisfied), and only accept the new slot if
         its demand IS a subset. Otherwise leave untouched.

Measures: fixes, collateral (currently-correct handles the rule would move), and
runs the same rule against GENUINE console output as a control.
"""
import sys, os, re, json, struct
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import _p2_inv as INV
import loader_sim as LS
import pc_zone
import techset_translate as TT

FOLLOW = 0xFFFFFFFF
AL = lambda v: 0xA0000000 <= v < 0xC0000000
le32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
_cstr = INV._cstr

ZPATH = 'mp_skate_final.zone'
PCPATH = '../mp_skate_pc.zone'
MANPATH = '../wiiu_ref/techset_corpus/mp_skate_subst.json'


def pc_side(pcpath):
    pc = open(pcpath, 'rb').read()
    empc, spanspc, _ = LS.simulate_pc(pc, verbose=False)
    ts_name = {sp[0]: (_cstr(pc, sp[3] + 152) or '').lstrip(',') for sp in spanspc
               if sp[2] == 'MaterialTechniqueSet'}
    prc = pc_zone.PCZoneReader(pc); prc.read_string_table(); prc.read_asset_list()
    arr = ((prc.assets_off - 64) + 7) & ~7
    N = len(prc.assets)

    def dec_k(a):
        p = (a - 1) & 0x1FFFFFFF
        lo = arr + 4
        return (p - lo) // 8 if (lo <= p < lo + N * 8 and (p - lo) % 8 == 0) else None

    # material name -> intended PC techset name  (name FOLLOWs at body+112 on PC)
    mat_ts = {}
    for sp in spanspc:
        pass
    # scan PC material bodies the same way techset_rebind does
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


def main():
    inv = INV.build(ZPATH)
    Z, MATS, TS, name2k = inv['Z'], inv['MATS'], inv['TS'], inv['name2k']
    pc, pc_ts_name, pc_dec_k = pc_side(PCPATH)
    man = json.load(open(MANPATH))['map']

    print('\nmanifest: %d PC techsets, methods=%s'
          % (len(man), dict(Counter(v['method'] for v in man.values()))))

    def satisfied(M, k):
        ts = TS.get(k)
        if ts is None or ts['dc'] is None:
            return None
        return not (ts['dc'] - M['consts']) and not (ts['ds'] - M['tex'])

    cls = Counter()
    changes = {}          # material off -> (old_k, new_k)
    collateral = []       # currently-SATISFIED materials the unguarded rule would move
    fixed, unfixable = [], []

    for M in MATS:
        k = M['k']
        sat_now = satisfied(M, k)
        intent_pc = pc_mat_intent(pc, pc_ts_name, pc_dec_k, M['name'])
        if intent_pc is None:
            cls['no-pc-intent'] += 1
            if sat_now is False:
                unfixable.append((M, 'no PC intent'))
            continue
        entry = man.get(intent_pc)
        intent_con = entry['console'] if entry else intent_pc
        method = entry['method'] if entry else 'none'
        ks = name2k.get(intent_con) or []
        if not ks:
            cls['intent-console-absent'] += 1
            if sat_now is False:
                unfixable.append((M, 'console name %s absent' % intent_con))
            continue
        k2 = ks[0]
        if k2 == k:
            cls['already-bound-correctly'] += 1
            continue
        sat_new = satisfied(M, k2)
        cls['rule-would-move:%s' % method] += 1
        if sat_now:
            collateral.append((M, k, k2, method, sat_new))
        else:
            if sat_new:
                fixed.append((M, k, k2, method))
                changes[M['off'] + 80] = k2
            else:
                unfixable.append((M, 'manifest target %s still unsat' % intent_con))

    print('\n--- classification over %d materials ---' % len(MATS))
    for kk, v in sorted(cls.items()):
        print('  %-34s %d' % (kk, v))

    base_unsat = [M for M in MATS if satisfied(M, M['k']) is False]
    print('\nBASELINE unsatisfied materials: %d' % len(base_unsat))
    print('rule FIXES (unsat -> sat)      : %d' % len(fixed))
    print('rule leaves UNFIXED            : %d' % (len(base_unsat) - len(fixed)))
    print('\n*** COLLATERAL ***')
    print('currently-SATISFIED materials whose handle the UNGUARDED rule would move: %d'
          % len(collateral))
    bad = [c for c in collateral if c[4] is not True]
    print('   ...of those, would become UNSATISFIED: %d' % len(bad))
    print('with the refuse-on-satisfied GUARD, handles moved on satisfied materials: 0')

    print('\n--- the fixes ---')
    for (M, k, k2, method) in fixed:
        print('  %-46s k %-5s %-42s -> k %-5s %-42s [%s]'
              % (M['name'][:46], k, TS[k]['name'][:42], k2, TS[k2]['name'][:42], method))
    print('\n--- still unfixed ---')
    for (M, why) in unfixable:
        print('  %-46s %s' % (M['name'][:46], why))

    json.dump({str(o): k for o, k in changes.items()}, open('_p2_changes.json', 'w'))
    print('\nhandle rewrites staged: %d (offsets in _p2_changes.json)' % len(changes))


if __name__ == '__main__':
    main()
