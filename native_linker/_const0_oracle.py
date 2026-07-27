#!/usr/bin/env python3
"""_const0_oracle.py — boot-36 front: TYPE-0 (vertex) material-constant demands.

Boot 36 (gfxtail29, 365 draws) died in the SAME stride-32 unbounded constantTable
search as family 9, but the demanding arg is TYPE 0 (dest 0xf9), value 0x4c53b0bf:
    movbe edi,[r13+r9+4] ; demanded hash from the arg record
    loop: add r10d,0x20 ; movbe eax,[r13+r10] ; cmp eax,edi ; jne loop  -> 0x50000000
IW numbering: 0 = MATERIAL_VERTEX_CONST, 6 = MATERIAL_PIXEL_CONST. Every family-9
audit/remap (gfxtail14/17/18, _nullct_oracle) covered ONLY type 6.

This oracle: (1) empirically confirms type-0 values live in the CONSTANT-hash
vocabulary (raid control), (2) audits demand_{0,6}(techset) ⊆ consts(material)
under per-index AND name-group keyings, (3) includes skate's 4 beyond-sim-break
tail techsets (§6.6: they were the constants blind spot too).

Usage: python _const0_oracle.py [zone ...]
"""
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
from _matconst_map import be16, be32, walk_techset, FOLLOW, PTRS
from _nullct_oracle import scan
from _sampler_oracle import techset_args

AL = lambda v: 0xA0000000 <= v < 0xC0000000
CONST_TYPES = (0, 6)

TAIL_TS = {'mp_skate_gfxtail29.zone':
           {804: 84317304, 815: 89654859, 817: 89678237, 819: 89678868},
           'mp_skate_gfxtail28.zone':
           {804: 84317304, 815: 89654859, 817: 89678237, 819: 89678868}}


def audit(path):
    print('=' * 78)
    print(path)
    print('=' * 78)
    Z, mats, dem6, ts_spans, ts_name, ts_idx = scan(path)
    ts_spans = dict(ts_spans)
    for k, s in TAIL_TS.get(path, {}).items():
        ts_spans[k] = (s, None)
    print('techsets: %d (incl. %d tail)   materials: %d'
          % (len(ts_spans), len(TAIL_TS.get(path, {})), len(mats)))

    constvocab = set()
    for mm in mats:
        constvocab |= set(mm['consts'])
    print('constant hash vocabulary: %d distinct' % len(constvocab))

    ts_args = {}
    for i, (s, e) in ts_spans.items():
        try:
            ts_args[i] = techset_args(Z, s)
        except Exception:
            ts_args[i] = []

    # empirical: which arg types' values live in the CONST vocabulary?
    tstat = defaultdict(lambda: [0, set()])
    for args in ts_args.values():
        for (t, d, v) in args:
            if v in PTRS:
                continue
            tstat[t][0] += 1
            tstat[t][1].add(v)
    print('%-6s %8s %9s %14s' % ('type', 'args', 'distinct', 'in-constvocab'))
    for t in sorted(tstat):
        n, vals = tstat[t]
        inv = sum(1 for v in vals if v in constvocab)
        print('%-6d %8d %9d %10d (%3.0f%%)'
              % (t, n, len(vals), inv, 100.0 * inv / len(vals) if vals else 0))

    demand = {i: set(v for (t, d, v) in args if t in CONST_TYPES and v not in PTRS)
              for i, args in ts_args.items()}
    d0 = {i: set(v for (t, d, v) in args if t == 0 and v not in PTRS)
          for i, args in ts_args.items()}

    name_of = {i: ts_name(s) for i, (s, e) in ts_spans.items()}
    groups = defaultdict(list)
    for i, nm in name_of.items():
        if nm:
            groups[nm].append(i)

    bound = unwalked = 0
    unsat_idx, unsat_grp = [], []
    for mm in mats:
        if not AL(mm['ts']):
            continue
        k = ts_idx(mm['ts'])
        if k is None:
            continue
        if k not in demand:
            unwalked += 1
            print('   STILL-UNAUDITED bind @%d %s -> asset %s' % (mm['_off'], mm['name'], k))
            continue
        cs = set(mm['consts'])
        nm = name_of.get(k)
        union = set()
        for j in (groups.get(nm) or [k]):
            union |= demand.get(j, set())
        if not union:
            continue
        bound += 1
        if demand[k] - cs:
            unsat_idx.append((mm, k, demand[k] - cs))
        gmiss = union - cs
        if gmiss:
            unsat_grp.append((mm, k, nm, gmiss))

    print('materials bound to a const-demanding techset: %d  (unwalked binds: %d)'
          % (bound, unwalked))
    print('UNSATISFIED per-index   : %d' % len(unsat_idx))
    print('UNSATISFIED name-group  : %d' % len(unsat_grp))
    t0only = [x for x in unsat_grp
              if any(h in (d0.get(j, set()) if True else set())
                     for j in (groups.get(x[2]) or [x[1]]) for h in x[3])]
    for (mm, k, nm, miss) in unsat_grp[:30]:
        kinds = ''.join(sorted(set(
            ('0' if any(h in d0.get(j, set()) for j in (groups.get(nm) or [k])) else '6')
            for h in miss)))
        print('   @%-9d %-44s ts%-4d %-36s t%s misses %s'
              % (mm['_off'], (mm['name'] or '?')[:44], k, (nm or '?')[:36], kinds,
                 ' '.join('0x%08x' % h for h in sorted(miss))))
    if len(unsat_grp) > 30:
        print('   ... %d more' % (len(unsat_grp) - 30))
    return dict(unsat_idx=unsat_idx, unsat_grp=unsat_grp, demand=demand,
                groups=groups, name_of=name_of, mats=mats)


if __name__ == '__main__':
    paths = sys.argv[1:] or ['../wiiu_ref/mp_raid_genuine.zone', 'mp_skate_gfxtail29.zone']
    for p in paths:
        audit(p)
        print()
