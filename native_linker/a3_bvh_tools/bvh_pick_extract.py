"""Extract divergent pick instances by replaying genuine tree topology, then
fit heuristic variants.

For each genuine tree, walk its topology; at each internal node (with the
input item order reconstructed by following genuine partitions), brute-force
(axis, dist) that reproduces the genuine child partition sizes and recursion;
record instances where our pick differs."""
import struct, os, sys, pickle
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Tony - Main Rig\Downloads\Testing enviroment\native_linker')
from a3_test import walk, tri_coords
import console_bvh as CB
f32 = np.float32

SCR = os.path.dirname(os.path.abspath(__file__))
MODELS = [210, 598, 394, 470, 183, 258, 232, 253,
          243, 248, 304, 513, 573, 613, 447, 185, 186, 196, 261, 334, 305, 557, 660]

def leaf_pass(tris, tri_offset):
    lv, lb = [], []
    last = False; prev = None
    for tn, tri in enumerate(tris):
        tb = CB.Bounds(); tb.expand_tri(tri)
        sm = False
        if last:
            pv = prev.volume(); tv = tb.volume()
            prev.expand_bounds(tb); cv = prev.volume()
            if cv <= f32(pv + tv): sm = True
        if sm:
            lb[-1] = prev.copy(); lv[-1] = (lv[-1][0], 1); last = False
        else:
            lv.append((tri_offset + tn, 0)); lb.append(tb.copy()); last = True
            prev = tb.copy()
    return lv, lb

def gen_children(gnodes, i):
    beg, cnt = gnodes[i][6], gnodes[i][7] & 0x7fff
    if gnodes[i][7] >> 15:
        return None
    return list(range(beg, beg + cnt))

def node_range(gnodes, i, memo):
    if i in memo: return memo[i]
    beg, cnt = gnodes[i][6], gnodes[i][7] & 0x7fff
    if gnodes[i][7] >> 15:
        memo[i] = (beg, beg + cnt)
    else:
        subs = [node_range(gnodes, c, memo) for c in range(beg, beg + cnt)]
        memo[i] = (min(a for a, _ in subs), max(b for _, b in subs))
    return memo[i]

def candidates(bounds, order, count):
    """(axis, dist) candidates: event values and midpoints of adjacent events."""
    out = []
    for axis in range(3):
        vals = set()
        for i in order[:count]:
            vals.add(float(f32(bounds[i].mins[axis])))
            vals.add(float(f32(bounds[i].maxs[axis])))
        sv = sorted(vals)
        for j, v in enumerate(sv):
            out.append((axis, f32(v)))
            if j + 1 < len(sv):
                out.append((axis, f32(f32(f32(v) + f32(sv[j+1])) * f32(0.5))))
    return out

def try_partition(bounds, order, count, axis, dist):
    rm = list(order)
    orig = CB._pick_split_plane
    CB._pick_split_plane = lambda b, r, c: (axis, dist)
    r = CB._split(bounds, rm, 0, count)
    CB._pick_split_plane = orig
    return r, rm

INSTANCES = []   # (model, bounds(list), order, count, gen_axis, gen_dist, our_pick)
CONSTRAINTS = [] # picks where ours already == genuine (bounds, order, count, axis, dist)

for idx in MODELS:
    try:
        gbin = open(os.path.join(SCR, 'idx%d_gen.bin' % idx), 'rb').read()
    except FileNotFoundError:
        continue
    gn, surfs = walk(gbin)
    for s in surfs:
        for k, tg in enumerate(s['trees']):
            if tg is None: continue
            (_, _, triOff, tcnt) = tg['vl']
            tris = tri_coords(gbin, s, triOff, tcnt)
            lv, lb = leaf_pass(tris, triOff)
            glf = list(struct.unpack('>%dH' % tg['lc'], tg['leafs']))
            vals = [t | (0x8000 if two else 0) for (t, two) in lv]
            if sorted(vals) != sorted(glf):
                continue   # leaf pass mismatch; skip (shouldn't happen)
            gnodes = [struct.unpack('>8H', tg['nodes'][i*16:(i+1)*16]) for i in range(tg['nc'])]
            memo = {}
            ok_tree = [True]
            def rec(gi, order):
                if not ok_tree[0]: return None
                ch = gen_children(gnodes, gi)
                lo, hi = node_range(gnodes, gi, memo)
                count = hi - lo
                if ch is None:
                    return order
                # genuine partition boundaries from children ranges
                bnds = [node_range(gnodes, c, memo) for c in ch]
                # children were created per partition: 1, 2 or 3 nodes per partition.
                # Reconstruct partition sizes: try all groupings of children into
                # 2-3 consecutive groups matching CreateAabbSubTrees emission.
                # Simpler: find (axis,dist) whose partition + recursion reproduces
                # genuine leaf order; the child grouping falls out of _split.
                # strong check: candidate partition must reproduce, per genuine
                # child range, the exact multiset of leaf VALUES from genuine leafs
                def child_multisets_ok(rm):
                    for (clo, chi) in bnds:
                        cand = sorted(vals[x] for x in rm[clo - lo:chi - lo])
                        genv = sorted(glf[clo:chi])
                        if cand != genv:
                            return False
                    return True
                res = None
                for (axis, dist) in candidates(lb, order, count):
                    r, rm = try_partition(lb, order, count, axis, dist)
                    if r is None: continue
                    mid, last = r
                    cuts = {lo + mid, lo + last}
                    edges = {a for a, _ in bnds} | {b for _, b in bnds}
                    if not cuts <= edges: continue
                    if not child_multisets_ok(rm): continue
                    res = (axis, dist, rm)
                    break
                if res is None:
                    ok_tree[0] = False
                    return None
                axis, dist, rm = res
                ours = CB._pick_split_plane(lb, order, count)
                ours_ok = False
                if ours is not None:
                    ro, rmo = try_partition(lb, order, count, *ours)
                    ours_ok = (ro == try_partition(lb, order, count, axis, dist)[0]
                               and child_multisets_ok(rmo))
                if not ours_ok:
                    INSTANCES.append(dict(model=idx, surf=s['i'], node=gi,
                                          order=list(order), count=count,
                                          gen=(axis, float(dist)),
                                          our=(None if ours is None else (ours[0], float(ours[1])))))
                else:
                    CONSTRAINTS.append(dict(model=idx, surf=s['i'], node=gi,
                                            order=list(order), count=count,
                                            gen=(axis, float(dist))))
                # recurse into children with partitioned order
                mid, last = try_partition(lb, order, count, axis, dist)[0]
                parts = [(0, mid), (mid, last), (last, count)]
                # map genuine children to partitions by their ranges
                pos = 0
                new_order = list(rm)
                for (a, b) in parts:
                    if a == b: continue
                    sub_children = [c for c in ch if node_range(gnodes, c, memo)[0] >= lo + a
                                    and node_range(gnodes, c, memo)[1] <= lo + b]
                    for c in sub_children:
                        clo, chi = node_range(gnodes, c, memo)
                        seg = rec(c, new_order[clo - lo:chi - lo])
                        if seg is None: return None
                        new_order[clo - lo:chi - lo] = seg
                return new_order
            root_order = list(range(len(lv)))
            final = rec(0, root_order)
            if final is not None and ok_tree[0]:
                if [vals[i] for i in final] != glf:
                    pass  # order reconstruction imperfect; instances still useful
# store
pickle.dump(dict(instances=INSTANCES, constraints=CONSTRAINTS, ),
            open(os.path.join(SCR, 'a3_fit_data.pkl'), 'wb'))
print('divergent picks:', len(INSTANCES), ' matching picks:', len(CONSTRAINTS))
from collections import Counter
print(Counter((i['gen'][0], i['our'][0] if i['our'] else None) for i in INSTANCES))
