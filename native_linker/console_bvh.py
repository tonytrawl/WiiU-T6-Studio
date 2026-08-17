#!/usr/bin/env python3
"""
Console XSurfaceCollisionTree rebuild (Track A / A3).

The console linker REBUILDS each rigid vertList's collision BVH rather than
reusing the PC zone's tree (counts and contents differ). Algorithm =
tools/ref_oat/src/ObjLoading/XModel/CollisionTreeCreator.cpp (OAT's RE of the
game linker), ported with the float semantics measured against genuine
mp_raid trees (33/35 byte-exact, remaining 2 = a split-plane heuristic
tie nuance, see HANDOFF_track_XModel.md):
  * volumes: strict per-op float32, product order (d0*d1)*d2 — proven 394/394
    against genuine leaf-merge decisions;
  * leaf merge test: cv <= f32(pv + tv);
  * plane-sweep arrays sorted ASCENDING (OAT's std::greater is not what the
    genuine output shows);
  * AABB quantization: full double chain (f32 per-op does NOT match genuine);
  * bias: f32 per op, the 0.4999999990686774 addend in double.
Node = 8 u16 (aabb mins/maxs, childBeginIndex, childCount | 0x8000-leaf flag);
leaf = u16 (triangleBeginIndex | 0x8000 twoTriangles).
"""
import struct
import numpy as np

f32 = np.float32
FLT_MAX = np.float32(np.finfo(np.float32).max)
MIN_ITEMS_PER_LEAF = 1
MAX_ITEMS_PER_LEAF = 16


class Bounds:
    __slots__ = ('mins', 'maxs')
    def __init__(self):
        self.mins = np.array([FLT_MAX] * 3, dtype=np.float32)
        self.maxs = np.array([-FLT_MAX] * 3, dtype=np.float32)
    def copy(self):
        b = Bounds.__new__(Bounds)
        b.mins = self.mins.copy(); b.maxs = self.maxs.copy()
        return b
    def expand_tri(self, tri):
        for v in tri:
            np.minimum(self.mins, v, out=self.mins)
            np.maximum(self.maxs, v, out=self.maxs)
    def expand_bounds(self, o):
        np.minimum(self.mins, o.mins, out=self.mins)
        np.maximum(self.maxs, o.maxs, out=self.maxs)
    def volume(self):
        d = self.maxs - self.mins
        return f32(f32(d[0] * d[1]) * d[2])
    def added_volume(self, o):
        e = self.copy(); e.expand_bounds(o)
        return f32(e.volume() - self.volume())


def _pick_split_plane(bounds, remap, count):
    gb = Bounds()
    for i in range(count):
        gb.expand_bounds(bounds[remap[i]])
    smallest = 1 if f32(gb.maxs[0] - gb.mins[0]) > f32(gb.maxs[1] - gb.mins[1]) else 0
    if f32(gb.maxs[smallest] - gb.mins[smallest]) > f32(gb.maxs[2] - gb.mins[2]):
        smallest = 2
    bias = []
    for i in range(3):
        num = f32(f32(f32(gb.maxs[i] - gb.mins[i]) + f32(1.0)) * f32(10.0))
        den = f32(f32(gb.maxs[smallest] - gb.mins[smallest]) + f32(1.0))
        bias.append(int(float(f32(num / den)) + 0.4999999990686774))
    best = -1
    chosen_axis = chosen_dist = None
    for axis in range(3):
        mins_l, maxs_l, cop_l = [], [], []
        for i in range(count):
            b = bounds[remap[i]]
            if b.mins[axis] == b.maxs[axis]:
                cop_l.append(f32(b.mins[axis]))
            else:
                mins_l.append(f32(b.mins[axis]))
                maxs_l.append(f32(b.maxs[axis]))
        mins_l.sort(); maxs_l.sort(); cop_l.sort()
        mm = len(mins_l); cc = len(cop_l)
        side_front = 0; side_back = count; side_split = 0; side_on = 0
        prev_min = 0; prev_on = 0
        if cc and mm:
            next_dist = cop_l[0] if f32(cop_l[0] - mins_l[0]) < 0.0 else mins_l[0]
        elif mm:
            next_dist = mins_l[0]
        elif cc:
            next_dist = cop_l[0]
        else:
            continue
        mi = xi = oi = 0
        while next_dist < FLT_MAX:
            dist = next_dist
            next_dist = FLT_MAX
            side_split += prev_min
            side_back -= prev_min
            prev_min = 0
            while mi < mm and mins_l[mi] == dist:
                prev_min += 1; mi += 1
            if mi < mm and mins_l[mi] < next_dist:
                next_dist = mins_l[mi]
            while xi < mm and maxs_l[xi] == dist:
                side_front += 1; side_split -= 1; xi += 1
            if xi < mm and next_dist > maxs_l[xi]:
                next_dist = maxs_l[xi]
            side_front += prev_on
            side_on -= prev_on
            prev_on = 0
            while oi < cc and cop_l[oi] == dist:
                prev_on += 1; oi += 1
            side_on += prev_on
            side_back -= prev_on
            if oi < cc and next_dist > cop_l[oi]:
                next_dist = cop_l[oi]
            if side_front > 1 and side_back > 1:
                h = bias[axis] + count - abs(side_front - side_back) - side_on - 4 * side_split
                if not side_on and not side_split and not prev_min:
                    h += int(float(f32(next_dist - dist)))
                if h > best:
                    best = h
                    chosen_axis = axis
                    if side_on or side_split or prev_min:
                        chosen_dist = f32(dist)
                    else:
                        chosen_dist = f32(f32(dist + next_dist) * f32(0.5))
    if best == -1:
        return None
    return chosen_axis, chosen_dist


def _split(bounds, remap, off, count):
    r = _pick_split_plane(bounds, remap[off:], count)
    if r is None:
        return None
    axis, dist = r
    bot, top = 0, count - 1
    b0, b1 = Bounds(), Bounds()
    def bmin(i): return bounds[remap[off + i]].mins[axis]
    def bmax(i): return bounds[remap[off + i]].maxs[axis]
    while bot <= top:
        while bot <= top and dist >= bmax(bot) and dist > bmin(bot):
            b0.expand_bounds(bounds[remap[off + bot]]); bot += 1
        while bot <= top and bmin(top) >= dist and bmax(top) > dist:
            b1.expand_bounds(bounds[remap[off + top]]); top -= 1
        if bot > top:
            break
        if ((bmin(bot) < dist or bmax(bot) <= dist)
                and (dist < bmax(top) or dist <= bmin(top))):
            mid = bot
            while mid < top:
                if bmin(mid) >= dist and bmax(mid) > dist:
                    remap[off + mid], remap[off + top] = remap[off + top], remap[off + mid]
                    break
                if dist >= bmax(mid) and dist > bmin(mid):
                    remap[off + mid], remap[off + bot] = remap[off + bot], remap[off + mid]
                    break
                mid += 1
            if mid == top:
                break
        else:
            remap[off + bot], remap[off + top] = remap[off + top], remap[off + bot]
    if bot <= top and (bot < MIN_ITEMS_PER_LEAF or top - bot + 1 < MIN_ITEMS_PER_LEAF
                       or count - top - 1 < MIN_ITEMS_PER_LEAF):
        while bot <= top:
            while bot <= top:
                bb = bounds[remap[off + bot]]
                if b1.added_volume(bb) < b0.added_volume(bb):
                    break
                b0.expand_bounds(bb); bot += 1
            while bot <= top:
                bt = bounds[remap[off + top]]
                if b0.added_volume(bt) < b1.added_volume(bt):
                    break
                b1.expand_bounds(bt); top -= 1
            if bot >= top:
                if bot == top:
                    if 2 * bot >= count:
                        top -= 1
                    else:
                        bot += 1
            else:
                remap[off + bot], remap[off + top] = remap[off + top], remap[off + bot]
                bot += 1; top -= 1
    if bot == 0 or bot == count:
        return None
    return bot, top + 1


def build_tree(tri_coords, tri_offset=0):
    """tri_coords: per tri, 3x np.float32[3] (vertList tri order).
    Returns dict(trans f32[3], scale f32[3], nodes [8-u16 tuples], leafs [u16])."""
    gb = Bounds()
    leafs = []
    leaf_bounds = []
    last_mergeable = False
    prev = None
    for tn, tri in enumerate(tri_coords):
        tb = Bounds(); tb.expand_tri(tri)
        gb.expand_bounds(tb)
        should_merge = False
        if last_mergeable:
            pv = prev.volume()
            tv = tb.volume()
            prev.expand_bounds(tb)
            cv = prev.volume()
            if cv <= f32(pv + tv):
                should_merge = True
        if should_merge:
            leaf_bounds[-1] = prev
            leafs[-1] = (leafs[-1][0], 1)
            last_mergeable = False
        else:
            leafs.append((tri_offset + tn, 0))
            leaf_bounds.append(tb.copy())
            last_mergeable = True
            prev = tb.copy()
    trans = -gb.mins
    delta = gb.maxs - gb.mins
    with np.errstate(divide='ignore'):
        scale = np.array([f32(f32(65535.0) / delta[j]) for j in range(3)],
                         dtype=np.float32)
    n = len(leafs)
    nodes = []
    if n:
        remap = list(range(n))
        nodes.append([0, n, 0, 0])
        def create_subtrees(ti, base, first, count):
            r = _split(leaf_bounds, remap, base + first, count) \
                if count > MAX_ITEMS_PER_LEAF else None
            fi = nodes[ti][0]
            if r:
                mid, last = r
                nodes.append([first + fi, mid, 0, 0])
                if mid < last:
                    nodes.append([mid + first + fi, last - mid, 0, 0])
                nodes.append([last + first + fi, count - last, 0, 0])
            else:
                nodes.append([first + fi, count, 0, 0])
        def build_r(ti, base):
            nodes[ti][2] = len(nodes)
            nodes[ti][3] = 0
            cnt = nodes[ti][1]
            r = _split(leaf_bounds, remap, base, cnt) if cnt > MAX_ITEMS_PER_LEAF else None
            if r:
                mid, last = r
                sub0 = len(nodes)
                create_subtrees(ti, base, 0, mid)
                if mid < last:
                    create_subtrees(ti, base, mid, last - mid)
                create_subtrees(ti, base, last, cnt - last)
                nodes[ti][3] = len(nodes) - nodes[ti][2]
                for ci in range(nodes[ti][3]):
                    si = sub0 + ci
                    build_r(si, base + nodes[si][0] - nodes[ti][0])
        build_r(0, 0)
        leafs = [leafs[remap[i]] for i in range(n)]
        leaf_bounds = [leaf_bounds[remap[i]] for i in range(n)]
    out_nodes = []
    for (first_item, item_count, first_child, child_count) in nodes:
        nb = Bounds()
        for li in range(first_item, first_item + item_count):
            nb.expand_bounds(leaf_bounds[li])
        q = []
        with np.errstate(invalid='ignore', over='ignore'):
            for j in range(3):
                v = (float(nb.mins[j]) + float(trans[j])) * float(scale[j]) - 0.5
                q.append(0 if v != v else int(min(max(v, 0.0), 65535.0)))
            for j in range(3):
                v = (float(nb.maxs[j]) + float(trans[j])) * float(scale[j]) + 0.5
                q.append(0 if v != v else int(min(max(v, 0.0), 65535.0)))
        if child_count:
            q += [first_child, child_count]
        else:
            q += [first_item, item_count | 0x8000]
        out_nodes.append(tuple(q))
    out_leafs = [tbi | (0x8000 if two else 0) for (tbi, two) in leafs]
    return dict(trans=trans, scale=scale, nodes=out_nodes, leafs=out_leafs)


def build_tree_pc(pc, verts0_off, tris_off, tri_off, tri_count):
    """Build the console tree from PC zone data: verts0 (32-B stride,
    LE f32 xyz at +0) + triIndices (LE u16). tri_off/tri_count per XRigidVertList."""
    coords = []
    for t in range(tri_off, tri_off + tri_count):
        tri = []
        for j in range(3):
            vi = struct.unpack_from('<H', pc, tris_off + (t * 3 + j) * 2)[0]
            x, y, z = struct.unpack_from('<fff', pc, verts0_off + vi * 32)
            tri.append(np.array([x, y, z], dtype=np.float32))
        coords.append(tri)
    return build_tree(coords, tri_offset=tri_off)
