#!/usr/bin/env python3
"""PHASE 1 step 3/4: for the 13 G4-unsatisfied materials, resolve the BOUND TECHSET
(live loader_sim, no cached simmap) and print CARRIED vs DEMANDED by NAME."""
import sys, re, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
from _nullct_oracle import scan
from _matconst_map import be32, be16, walk_techset, PTRS, FOLLOW
from _p1_dump13 import carried, nm, material_at_name, VOCAB
from _p1_recover import H

CONST_TYPES = (0, 6)
SAMPLER_TYPE = 2

TARGETS = ['*127n_236n_238(', '*127n_294n_236n(', '*127n_557n_238(', '*145n_5_192n(',
           '*1n_67n_175_5(', '*222_236n_65(', '*23n_73_71_65(', '*4n_5_192n(',
           '*53n_661_5_238(', '*67n_135(', '*75n_5_192n(', '*93n_192n(', 'wpc/shadowcaster']


def ts_demands(Z, s):
    passes, _ = walk_techset(Z, s)
    consts, samps = set(), set()
    for p in passes:
        for j in range(p['nargs']):
            a = p['args_off'] + j * 8
            t = be16(Z, a)
            v = be32(Z, a + 4)
            if v in PTRS:
                continue
            if t in CONST_TYPES:
                consts.add(v)
            elif t == SAMPLER_TYPE:
                samps.add(v)
    return consts, samps


def main(path):
    print('=' * 100)
    print(path)
    print('=' * 100)
    Z, mats, demand6, ts_spans, ts_name, ts_idx = scan(path)
    by_off = {m['_off']: m for m in mats}
    for pat in TARGETS:
        ms = [m.start() for m in re.finditer(re.escape(pat.encode()), Z)]
        if not ms:
            print('%-20s NOT FOUND' % pat); continue
        o = ms[0]
        e = Z.index(b'\x00', o)
        full = Z[o:e].decode('latin1')
        b = material_at_name(Z, o)
        tex, tc, consts, kind, texc, constc = carried(Z, b)
        ts = be32(Z, b + 80)
        k = ts_idx(ts)
        tsn, dc, ds = '?', set(), set()
        if k is not None and k in ts_spans:
            s, _e = ts_spans[k]
            tsn = ts_name(s) or '?'
            try:
                dc, ds = ts_demands(Z, s)
            except Exception as ex:
                tsn += ' (WALK FAIL %s)' % ex
        ctex, ccon = set(tex), set(consts)
        print('\n### %s' % full)
        print('  techset[%s] = %s' % (k, tsn.lstrip(',')))
        print('  CARRIED tex   : %s' % ' '.join(nm(h) for h in tex))
        print('  DEMAND  tex   : %s' % ' '.join(sorted(nm(h) for h in ds)))
        print('  MISSING tex   : %s' % ' '.join(sorted(nm(h) for h in (ds - ctex))) or '-')
        print('  CARRIED const : %s' % ' '.join(nm(h) for h in consts))
        print('  DEMAND  const : %s' % ' '.join(sorted(nm(h) for h in dc)))
        print('  MISSING const : %s' % ' '.join(sorted(nm(h) for h in (dc - ccon))) or '-')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_final.zone')
