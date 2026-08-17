#!/usr/bin/env python3
"""
Task #28: console (Wii U) clipMap_t layout probe for T6.

BODY: console clipMap_t = PC-IDENTICAL, **332 bytes** (NOTE: struct_layout.py
reports 328 because it silently drops `uint16_t (*triIndices)[3]` — a
pointer-to-array member it can't parse; the real PC struct has triIndices at
+120, shifting everything after by +4 vs struct_layout's table).

mp_raid_genuine.zone: body @ 0x4117f02 (found via the bsp-name alias a22ca9d0;
4 occurrences = GfxWorld/ComWorld/clipMap/inline-MapEnts). Anchors: visibility
= 480 bytes of 0xff @0x4514816, inline MapEnts + entity blob, techset 853
@0x454d50e = end bound.

Field map (BE, offsets = PC):
 +0 name*(alias) +4 isInUse
 ClipInfo @8..79: +8 planeCount +12 planes*(alias->GfxWorld's) +16 numMaterials
  +20 materials* +24 numBrushSides +28 brushsides* +32 leafbrushNodesCount
  +36 leafbrushNodes* +40 numLeafBrushes +44 leafbrushes*(alias) +48 numBrushVerts
  +52 brushVerts* +56 nuinds +60 uinds* +64 numBrushes u16 +68 brushes*
  +72 brushBounds* +76 brushContents*
 +80 pInfo*(reusable) +84 numStaticModels +88 staticModelList* +92 numNodes
 +96 nodes* +100 numLeafs +104 leafs* +108 vertCount +112 verts* +116 triCount
 +120 triIndices* +124 triEdgeIsWalkable* +128 partitionCount +132 partitions*
 +136 aabbTreeCount +140 aabbTrees* +144 numSubModels +148 cmodels*
 +152 numClusters +156 clusterBytes +160 visibility* +164 vised +168 mapEnts*
 +172 box_brush* +176 box_model(76) +252 originalDynEntCount u16
 +254 dynEntCount[4] u16 +262 pad2? +264 dynEntDefList[2] +272 dynEntPoseList[2](RT)
 +280 dynEntClientList[2](RT) +288 dynEntServerList[2](RT) +296 dynEntCollList[4](RT)
 +312.. num_constraints/constraints*/max_ropes/ropes*(RT)/checksum -> 332
 (exact dynEnt tail offsets verified by the walk below)
"""
import struct
import sys

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def u32(d, o):
    return struct.unpack('>I', d[o:o+4])[0]


def u16(d, o):
    return struct.unpack('>H', d[o:o+2])[0]


class Cur:
    def __init__(self, d, o):
        self.d = d
        self.o = o

    def mark(self, label):
        print('  %-44s cur=0x%08x' % (label, self.o))

    def skip(self, n):
        self.o += n

    def cstr(self, maxlen=256):
        e = self.d.index(b'\x00', self.o)
        s = self.d[self.o:e]
        self.o = e + 1
        return s.decode('latin-1', 'replace')


def walk(d, b, sizes, e='>', mat_span=None):
    # endian-aware local readers (shadow the module BE u32/u16 so this works for PC LE too;
    # clipMap_t is PC-identical, so the same walk + sizes apply — only the byte order differs).
    def u32(d, o):
        return struct.unpack(e + 'I', d[o:o+4])[0]
    def u16(d, o):
        return struct.unpack(e + 'H', d[o:o+2])[0]
    def i16(d, o):
        return struct.unpack(e + 'h', d[o:o+2])[0]
    g = lambda off: u32(d, b + off)
    c = Cur(d, b + 332)
    c.mark('start (body end)')
    # name: alias in mp_raid (no data). Order = member order.
    if g(0) in PTRS:
        c.cstr()
        c.mark('name chars')

    # ClipInfo (embedded @8)
    if g(12) in PTRS:                       # planes (usually alias)
        c.skip(g(8) * 20)
        c.mark('planes x%d' % g(8))
    nm = g(16)
    if g(20) in PTRS:                       # materials: ClipMaterial(12) + names
        mb = c.o
        c.skip(nm * 12)
        names = 0
        for i in range(nm):
            if u32(d, mb + i*12) in PTRS:
                c.cstr()
                names += 1
        c.mark('materials x%d (%d inline names)' % (nm, names))
    if g(28) in PTRS:                       # brushsides: cbrushside_t(12), plane reusable
        sb = c.o
        c.skip(g(24) * 12)
        pl = 0
        for i in range(g(24)):
            if u32(d, sb + i*12) in PTRS:
                c.skip(20)
                pl += 1
        c.mark('brushsides x%d (%d inline planes)' % (g(24), pl))
    if g(36) in PTRS:                       # leafbrushNodes: cLeafBrushNode_s(20)
        lb = c.o
        c.skip(g(32) * sizes['cLeafBrushNode_s'])
        extra = 0
        for i in range(g(32)):
            no = lb + i*sizes['cLeafBrushNode_s']
            cnt = i16(d, no + 2)                          # leafBrushCount i16 @+2
            if cnt > 0 and u32(d, no + 8) in PTRS:       # data.leaf.brushes @+8
                c.skip(cnt * 2)                          # LeafBrush u16
                extra += cnt
        c.mark('leafbrushNodes x%d (+%d leafBrush u16)' % (g(32), extra))
    if g(44) in PTRS:                       # leafbrushes u16
        c.skip(g(40) * 2)
        c.mark('leafbrushes x%d' % g(40))
    if g(52) in PTRS:                       # brushVerts vec3
        c.skip(g(48) * 12)
        c.mark('brushVerts x%d' % g(48))
    if g(60) in PTRS:                       # uinds u16
        c.skip(g(56) * 2)
        c.mark('uinds x%d' % g(56))
    nbr = u16(d, b+64)
    if g(68) in PTRS:                       # brushes cbrush_t (sides/verts reusable=alias)
        c.skip(nbr * sizes['cbrush_t'])
        c.mark('brushes x%d (%d each)' % (nbr, sizes['cbrush_t']))
    if g(72) in PTRS:
        c.skip(nbr * 24)                    # brushBounds Bounds(24)
        c.mark('brushBounds x%d' % nbr)
    if g(76) in PTRS:
        c.skip(nbr * 4)                     # brushContents int
        c.mark('brushContents x%d' % nbr)

    # main clipMap arrays
    if g(80) in PTRS:                       # pInfo (reusable -> usually alias)
        c.skip(72)
        c.mark('pInfo inline')
    if g(88) in PTRS:
        c.skip(g(84) * sizes['cStaticModel_s'])
        c.mark('staticModelList x%d (%d each)' % (g(84), sizes['cStaticModel_s']))
    if g(96) in PTRS:
        c.skip(g(92) * 8)                   # cNode_t {plane*(alias), children i16[2]}
        c.mark('nodes x%d' % g(92))
    if g(104) in PTRS:
        c.skip(g(100) * sizes['cLeaf_s'])
        c.mark('leafs x%d (%d each)' % (g(100), sizes['cLeaf_s']))
    if g(112) in PTRS:
        c.skip(g(108) * 12)                 # verts vec3
        c.mark('verts x%d' % g(108))
    tc = g(116)
    if g(120) in PTRS:
        c.skip(tc * 6)                      # triIndices u16[3]
        c.mark('triIndices x%d' % tc)
    if g(124) in PTRS:
        c.skip(((3*tc + 31)//32) * 4)       # triEdgeIsWalkable bitset
        c.mark('triEdgeIsWalkable %d bytes' % (((3*tc+31)//32)*4))
    if g(132) in PTRS:
        c.skip(g(128) * sizes['CollisionPartition'])
        c.mark('partitions x%d (%d each)' % (g(128), sizes['CollisionPartition']))
    if g(140) in PTRS:
        c.skip(g(136) * sizes['CollisionAabbTree'])
        c.mark('aabbTrees x%d (%d each)' % (g(136), sizes['CollisionAabbTree']))
    if g(148) in PTRS:
        c.skip(g(144) * sizes['cmodel_t'])
        c.mark('cmodels x%d (%d each)' % (g(144), sizes['cmodel_t']))
    if g(160) in PTRS:
        c.skip(g(152) * g(156))             # visibility
        c.mark('visibility %d bytes' % (g(152)*g(156)))
    if g(168) in PTRS:                      # mapEnts: inline MapEnts asset (36+blob+triggers)
        me = c.o
        c.skip(36)
        if u32(d, me) in PTRS:
            c.cstr()
        if u32(d, me+4) in PTRS:
            c.skip(u32(d, me+8))            # entityString
        # MapTriggers: models 8/hulls 32/slabs 20
        for cnt_o, ptr_o, rsz in ((12, 16, 8), (20, 24, 32), (28, 32, 20)):
            if u32(d, me+ptr_o) in PTRS:
                c.skip(u32(d, me+cnt_o) * rsz)
        c.mark('mapEnts inline (%d entity chars)' % u32(d, me+8))
    if g(172) in PTRS:
        c.skip(96)                          # box_brush cbrush_t
        c.mark('box_brush inline')
    # dynEnt tail: originalDynEntCount u16 @252, dynEntCount[4] u16 @254..261
    # dynEntDefList[2] @264/+268 (DynEntityDef 84 each; pose/client/server/coll
    # lists are RUNTIME -> 0 stream bytes)
    for i, off in enumerate((264, 268)):
        cnt = u16(d, b + 254 + i*2)
        if g(off) in PTRS:
            defbase = c.o
            c.skip(cnt * 84)
            c.mark('dynEntDefList[%d] x%d (84 each)' % (i, cnt))
            # Per-def inline sub-assets (OAT Load_DynEntityDef order): xModel@32, destroyedxModel@36,
            # destroyFx@44, destroyPieces@52 (XModelPieces), physPreset@56. Consume those present.
            inl = 0
            for j in range(cnt):
                db = defbase + j*84
                for nm2, o2 in (('xModel', 32), ('destroyedxModel', 36), ('destroyFx', 44),
                                ('destroyPieces', 52)):
                    if u32(d, db + o2) in PTRS:
                        raise RuntimeError('inline %s in DynEntityDef needs a dedicated consumer' % nm2)
                if u32(d, db + 56) in PTRS:        # physPreset: 84 body + name str + sndAliasPrefix str
                    pp = c.o
                    c.skip(84)
                    if u32(d, pp + 0) in PTRS:     # name @0
                        c.cstr()
                    if u32(d, pp + 28) in PTRS:    # sndAliasPrefix @28
                        c.cstr()
                    inl += 1
            if inl:
                c.mark('dynEntDefList[%d] inline physPresets x%d' % (i, inl))
    if g(316) in PTRS:                      # constraints: PhysConstraint = 168
        ncon = g(312)
        conbase = c.o
        c.skip(ncon * 168)
        c.mark('constraints x%d (168 each)' % ncon)
        # per-constraint dynamics (OAT array order: all bodies, then each element's
        # follows): target_bone1 @20 / target_bone2 @36 strings, material @140 —
        # rope constraints carry a full INLINE Material (e.g. zm_transit techrope).
        for j in range(ncon):
            cb = conbase + j * 168
            if u32(d, cb + 20) in PTRS:
                c.cstr()
            if u32(d, cb + 36) in PTRS:
                c.cstr()
            if u32(d, cb + 140) in PTRS:
                if mat_span is None:
                    raise RuntimeError('inline constraint material needs a consumer')
                c.o = mat_span(d, c.o)
        c.mark('constraint dynamics')
    # ropes @324 RUNTIME -> 0 bytes; checksum @328 -> body 332
    return c


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'mp_raid_genuine.zone'
    body = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x4117f02
    d = open(path, 'rb').read()
    sizes = dict(cLeafBrushNode_s=20, cbrush_t=96, cStaticModel_s=84,
                 cLeaf_s=44, CollisionPartition=16, CollisionAabbTree=32,
                 cmodel_t=76)
    c = walk(d, body, sizes)
    print('END = 0x%x' % c.o)


if __name__ == '__main__':
    main()
