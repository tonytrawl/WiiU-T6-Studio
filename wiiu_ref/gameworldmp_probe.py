#!/usr/bin/env python3
"""
GAMEWORLD_MP console (Wii U v148) layout probe (PathData walk).

FINDINGS (see main() verification):
  GameWorldMp body = PC-IDENTICAL 44 bytes:
    +0 name* (alias to the d3dbsp name ComWorld already wrote)
    +4 PathData: nodeCount@0, originalNodeCount@4, nodes*@8, basenodes*@12
       (RUNTIME block, no file bytes), visBytes@16, pathVis*@20,
       smoothBytes@24, smoothCache*@28, nodeTreeCount@32, nodeTree*@36
  Dynamic stream (PC rules, from GameWorldSp.txt ZoneCode):
    nodes: (nodeCount+128) x pathnode_t, then per node Links
           (totalLinkCount x pathlink_s 16B)
    pathVis: visBytes, smoothCache: smoothBytes
    nodeTree: nodeTreeCount x pathnode_tree_t(16B), then per element:
           axis<0 -> u.s.nodes (nodeCount x u16); else child[2] FOLLOW ->
           one pathnode_tree_t each, recursive.
  CONSOLE DELTA: NONE. pathnode_t serializes as the full PC 144 bytes
  (constant 68 + dynamic 48 + transient 28, transient pointers not
  followed), pathlink_s 16, pathnode_tree_t 16. The walk with PC sizes
  lands byte-exact on the next asset on Wii U mp_raid (glass techset body
  at 0x40f5989), Wii U zm_transit (next body at 0x62c3a02, which opens
  with the d3dbsp name alias), and PC mp_raid (PC techset body, 152-byte
  PC MaterialTechniqueSet, at 0x5725767). Only the usual big-endian word
  swap applies on console.

  NOTE for WP-B: between gfxworld_probe2's WiiU END and this asset sits a
  console-only ~11.6 KB block: SSkinShaders GX2 programs named
  gpuskin1bone..gpuskin4bone.glsl (GfxWorld siege-skin tail). On PC that
  block does not exist (GfxWorld ends ~0x314 bytes after the probe END with
  lut materials + occluders).
"""
import struct, sys

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


class Walker:
    def __init__(self, d, e, node_size):
        self.d = d
        self.e = e
        self.ns = node_size

    def u32(self, o):
        return struct.unpack(self.e + 'I', self.d[o:o+4])[0]

    def u16(self, o):
        return struct.unpack(self.e + 'H', self.d[o:o+2])[0]

    def walk(self, body):
        d, e = self.d, self.e
        (nodeCount, orig, nodes_p, base_p, visBytes, vis_p, smoothBytes,
         smooth_p, treeCount, tree_p) = struct.unpack(
            e + '10I', d[body+4:body+44])
        o = body + 44
        info = dict(nodeCount=nodeCount, visBytes=visBytes,
                    smoothBytes=smoothBytes, treeCount=treeCount)
        if nodes_p in PTRS:
            n = nodeCount + 128
            base = o
            o += n * self.ns
            for i in range(n):
                nb = base + i * self.ns
                total_links = self.u16(nb + 60)
                links_p = self.u32(nb + 64)
                if links_p in PTRS:
                    o += total_links * 16          # pathlink_s
        # basenodes: RUNTIME block, no file bytes
        if vis_p in PTRS:
            o += visBytes
        if smooth_p in PTRS:
            o += smoothBytes
        if tree_p in PTRS:
            o = self.tree_array(o, treeCount)
        return o, info

    def tree_array(self, o, count):
        base = o
        o += count * 16
        for i in range(count):
            o = self.tree_dyn(base + i * 16, o)
        return o

    def tree_dyn(self, tb, o):
        axis = struct.unpack(self.e + 'i', self.d[tb:tb+4])[0]
        if axis < 0:
            # u.s = {int nodeCount; u16* nodes}
            cnt = self.u32(tb + 8)
            if self.u32(tb + 12) in PTRS:
                o += cnt * 2
        else:
            for k in (8, 12):
                if self.u32(tb + k) in PTRS:       # child: one tree node
                    o = self.tree_array(o, 1)
        return o


def find_body(d, e, nodeCount_hint=None):
    """GameWorldMp body: alias name + nodeCount==originalNodeCount>0 +
    FOLLOW nodes/pathVis/smoothCache/nodeTree pointers."""
    out = []
    for i in range(0, len(d) - 44, 1):
        pass  # too slow; use marker scan instead
    return out


def main():
    cases = [
        ('mp_raid_genuine.zone', '>', 0x040aa61d, 0x40f5989),
        ('../PC ff/mp_raid.zone', '<', 0x056da3fb, 0x57257ff - 152),
        ('zm_transit_original.zone', '>', None, None),
    ]
    for path, e, body, expect_end in cases:
        d = open(path, 'rb').read()
        if body is None:
            # locate via the 44-byte signature: u32 nc==u32 onc, FOLLOW x2,
            # then visBytes, FOLLOW, smoothBytes, FOLLOW, treeCount, FOLLOW
            body = None
            pos = 0
            ff = b'\xff\xff\xff\xff' * 2
            while True:
                pos = d.find(ff, pos)
                if pos < 0:
                    break
                b = pos - 12
                pos += 1
                if b < 0:
                    continue
                v = struct.unpack(e + '11I', d[b:b+44])
                if (v[1] == v[2] and 0 < v[1] < 100000 and v[3] == FOLLOW
                        and v[9] < 100000 and v[0] >= 0x80000000):
                    body = b
                    break
            if body is None:
                print(path, 'no GameWorldMp body found')
                continue
        for ns in (144,):
            w = Walker(d, e, ns)
            try:
                end, info = w.walk(body)
            except Exception as ex:
                print('%s node_size=%d walk failed: %s' % (path, ns, ex))
                continue
            tag = ''
            if expect_end is not None:
                tag = 'MATCHES techset anchor' if end == expect_end else \
                      'off by %+d' % (end - expect_end)
            else:
                nxt = struct.unpack(e + 'I', d[end:end+4])[0]
                tag = 'next-u32=%08x' % nxt
            print('%s body=0x%x node_size=%d end=0x%x %s %s' %
                  (path, body, ns, end, info, tag))


if __name__ == '__main__':
    main()
