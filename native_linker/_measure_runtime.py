#!/usr/bin/env python3
"""Scratch: measure genuine-alias runtime shifts vs the event-replay sim.
Anchors: SndBank SndAlias name -> its list's name string (EXACT pairing);
GameWorldMp tree child aliases -> dedup'd tree nodes (histogram pairing).
Run with gfx_skip=0: the SndBank/GWMP modal shifts then read off the
unmodeled runtime constants (GfxWorld runtime; clipMap dynEnt runtime)."""
import sys, os, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from collections import Counter
import loader_sim as LS

FOLLOW = 0xFFFFFFFF
PTRS = (0xFFFFFFFF, 0xFFFFFFFE)


def _is_b5(v):
    return 0xA0000001 <= v <= 0xBFFFFFFF


def sndbank_anchors(d, b, e, body=4756):
    """[(alias_value, listname_stream_off)] from one bank."""
    u32 = lambda o: struct.unpack_from(e + 'I', d, o)[0]
    out = []
    (name_p, aliasCount, alias_p, aliasIndex_p, radverbCount, radverbs_p,
     duckCount, ducks_p) = struct.unpack_from(e + '8I', d, b)
    o = b + body
    if name_p in PTRS:
        o = d.index(b'\x00', o) + 1
    if alias_p in PTRS:
        base = o
        o += aliasCount * 20
        for i in range(aliasCount):
            lb = base + i * 20
            lname_p, lid, head_p, cnt, seq = struct.unpack_from(e + '5I', d, lb)
            noff = None
            if lname_p in PTRS:
                noff = o
                o = d.index(b'\x00', o) + 1
            if head_p in PTRS:
                ab = o
                o += cnt * 100
                for k in range(cnt):
                    a = ab + k * 100
                    v = u32(a)
                    if noff is not None and _is_b5(v):
                        out.append((v, noff))
                    for po in (a + 8, a + 12, a + 20):
                        if u32(po) in PTRS:
                            o = d.index(b'\x00', o) + 1
    return out


def gwmp_tree_anchors(d, b, e):
    """(node_starts, alias_values) from the nodeTree recursion."""
    u32 = lambda o: struct.unpack_from(e + 'I', d, o)[0]
    u16 = lambda o: struct.unpack_from(e + 'H', d, o)[0]
    (nodeCount, orig, nodes_p, base_p, visBytes, vis_p, smoothBytes,
     smooth_p, treeCount, tree_p) = struct.unpack_from(e + '10I', d, b + 4)
    o = b + 44
    if u32(b) in PTRS:
        o = d.index(b'\x00', o) + 1
    if nodes_p in PTRS:
        n = nodeCount + 128
        nbase = o
        o += n * 144
        for i in range(n):
            nb = nbase + i * 144
            if u32(nb + 64) in PTRS:
                o += u16(nb + 60) * 16
    if vis_p in PTRS:
        o += visBytes
    if smooth_p in PTRS:
        o += smoothBytes
    nodes = []
    aliases = []

    def tree_array(o, count):
        base = o
        o += count * 16
        for i in range(count):
            nodes.append(base + i * 16)
            o = tree_dyn(base + i * 16, o)
        return o

    def tree_dyn(tb, o):
        axis = struct.unpack_from(e + 'i', d, tb)[0]
        if axis < 0:
            cnt = u32(tb + 8)
            if u32(tb + 12) in PTRS:
                o += cnt * 2
        else:
            for k in (8, 12):
                v = u32(tb + k)
                if v in PTRS:
                    o = tree_array(o, 1)
                elif _is_b5(v):
                    aliases.append(v)
        return o

    if tree_p in PTRS:
        tree_array(o, treeCount)
    return nodes, aliases


def report(tag, spans, rtmap, D, e):
    print('== %s ==' % tag)
    for (i, nm, root, s, epos) in spans:
        if root == 'SndBank' and epos > s:
            anc = sndbank_anchors(D, s, e)
            c = Counter(((v - 1) & 0x1FFFFFFF) - rtmap.rt(noff - 64)
                        for (v, noff) in anc)
            print('  SndBank @0x%x: %d anchors, shifts %s' %
                  (s, len(anc), c.most_common(6)))
        if root == 'GameWorldMp' and epos > s:
            nodes, aliases = gwmp_tree_anchors(D, s, e)
            rts = [rtmap.rt(n - 64) for n in nodes]
            c = Counter()
            for v in aliases:
                b5 = (v - 1) & 0x1FFFFFFF
                for r in rts:
                    dlt = b5 - r
                    if -10**7 < dlt < 10**8:
                        c[dlt] += 1
            print('  GWMP @0x%x: %d tree nodes, %d child aliases, top shifts %s'
                  % (s, len(nodes), len(aliases), c.most_common(6)))


def main():
    pol = dict(gfx_skip=0)
    em, spans, CO = LS.simulate('../wiiu_ref/mp_raid_genuine.zone',
                                verbose=False, policy=pol)
    report('console mp_raid', spans, LS.RuntimeMap(em.omap), CO, '>')
    em, spans, PC = LS.simulate_pc('../PC ff/mp_raid.zone', verbose=False)
    report('PC mp_raid', spans, LS.RuntimeMap(em.omap), PC, '<')


if __name__ == '__main__':
    main()
