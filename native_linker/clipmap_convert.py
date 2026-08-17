#!/usr/bin/env python3
"""
PC(LE) -> console(BE) clipMap_t converter (Track G assemble; chase findings §3).

PC and console clipMap_t serialize IDENTICALLY (identical section boundaries and
totals on raid 4,412,940 and dockside 2,910,696) — conversion is a per-section
field-width byte swap + pointer reloc. Section order mirrors the validated
wiiu_ref/clipmap_probe.walk; per-offset word classes were derived empirically
from the raid PC/console pair (intersection over all array elements) and
cross-checked on dockside. Float-mantissa-drift words (staticModelList/cmodels
placement bounds, ~166 on raid) remain plain u32 swaps — the residual byte
diffs are link-time recompute, allowlisted at the gate.
"""
import struct
from smalls_convert import Sw, PTRS, _default_reloc
import material_convert as MC

FOLLOW = 0xFFFFFFFF


def _plane(s):                       # cplane_s 20: normal/dist f32 + type bytes
    s.u32(4); s.raw(4)


def _cmodel(s):                      # cmodel_t 76 (also box_model @body+176)
    s.u32(7)                         # bounds + radius (@24 drift class)
    s.u32()                          # +28 (raid-ambiguous; scalar)
    s.u16(2)                         # +32
    s.u32(9)                         # +36..71
    s.u32()                          # +72 (raid-ambiguous; scalar)


def _physpreset_inline(s):
    b = s.o
    s.ptr(); s.u32(6); s.ptr(); s.u32(13)      # 84-B body
    if s.peek32(b) in PTRS:
        s.cstr()                               # name
    if s.peek32(b + 28) in PTRS:
        s.cstr()                               # sndAliasPrefix


def convert_clipmap(pc, off, reloc=_default_reloc):
    s = Sw(pc, off, reloc)
    g = lambda o: s.peek32(off + o)
    u16at = lambda o: struct.unpack_from('<H', pc, o)[0]
    i16at = lambda o: struct.unpack_from('<h', pc, o)[0]

    # ---- 332-B body ----
    s.ptr()                          # name
    s.u32()                          # isInUse
    for _ in range(7):               # ClipInfo counts/ptr pairs @8..63
        s.u32(); s.ptr()
    s.u16(2)                         # numBrushes u16 @64
    s.ptr(3)                         # brushes/brushBounds/brushContents
    s.ptr()                          # pInfo @80
    for _ in range(3):               # numStaticModels/nodes/leafs pairs @84..107
        s.u32(); s.ptr()
    s.u32(); s.ptr()                 # vertCount/verts @108
    s.u32()                          # triCount @116
    s.ptr(2)                         # triIndices/triEdgeIsWalkable
    for _ in range(3):               # partitions/aabbTrees/cmodels pairs @128..151
        s.u32(); s.ptr()
    s.u32(2)                         # numClusters/clusterBytes @152
    s.ptr()                          # visibility @160
    s.u32()                          # vised @164
    s.ptr(2)                         # mapEnts/box_brush @168
    _cmodel(s)                       # box_model @176 (76 B)
    s.u16(5)                         # originalDynEntCount + dynEntCount[4] @252
    s.raw(2)                         # pad @262
    s.ptr(12)                        # dynEntDef/Pose/Client/Server[2] + Coll[4]
    s.u32(); s.ptr()                 # num_constraints/constraints @312
    s.u32(); s.ptr()                 # max_ropes/ropes(RT) @320
    s.u32()                          # checksum @328
    assert s.o == off + 332

    # ---- dynamics, probe order ----
    if g(0) in PTRS:
        s.cstr()                     # name chars
    if g(12) in PTRS:                # planes
        for _ in range(g(8)):
            _plane(s)
    nm = g(16)
    if g(20) in PTRS:                # materials: ClipMaterial(12) + names
        mb = s.o
        for _ in range(nm):
            s.ptr(); s.u32(2)
        for i in range(nm):
            if s.peek32(mb + i * 12) in PTRS:
                s.cstr()
    if g(28) in PTRS:                # brushsides + inline planes
        sb = s.o
        for _ in range(g(24)):
            s.ptr(); s.u32(2)
        for i in range(g(24)):
            if s.peek32(sb + i * 12) in PTRS:
                _plane(s)
    if g(36) in PTRS:                # leafbrushNodes(20) + trailing leafBrush u16
        lb = s.o
        for _ in range(g(32)):
            s.raw(2); s.u16()        # axis u8 + pad + leafBrushCount i16
            s.u32()                  # contents
            s.ptr()                  # data.leaf.brushes @8 (union)
            s.u32(); s.u16(2)        # union tail
        for i in range(g(32)):
            no = lb + i * 20
            cnt = i16at(no + 2)
            if cnt > 0 and s.peek32(no + 8) in PTRS:
                s.u16(cnt)
    if g(44) in PTRS:
        s.u16(g(40))                 # leafbrushes u16
    if g(52) in PTRS:
        s.u32(g(48) * 3)             # brushVerts vec3
    if g(60) in PTRS:
        s.u16(g(56))                 # uinds u16
    nbr = u16at(off + 64)
    if g(68) in PTRS:                # brushes cbrush_t(96)
        for _ in range(nbr):
            s.u32(8)                 # @0..31
            s.ptr()                  # sides @32
            s.u32(13)                # @36..87
            s.ptr()                  # verts @88
            s.u32()                  # @92
    if g(72) in PTRS:
        s.u32(nbr * 6)               # brushBounds Bounds(24)
    if g(76) in PTRS:
        s.u32(nbr)                   # brushContents
    if g(80) in PTRS:
        s.u32(18)                    # pInfo inline (72)
    if g(88) in PTRS:                # staticModelList(84)
        for _ in range(g(84)):
            s.u32()                  # +0
            s.ptr()                  # xmodel @4
            s.u32(19)                # +8..83 (bounds drift class stays u32)
    if g(96) in PTRS:                # nodes(8)
        for _ in range(g(92)):
            s.ptr(); s.u16(2)
    if g(104) in PTRS:               # leafs(44)
        for _ in range(g(100)):
            s.u16(2); s.u32(9); s.u16(2)
    if g(112) in PTRS:
        s.u32(g(108) * 3)            # verts vec3
    tc = g(116)
    if g(120) in PTRS:
        s.u16(tc * 3)                # triIndices u16[3]
    if g(124) in PTRS:
        s.raw(((3 * tc + 31) // 32) * 4)   # triEdgeIsWalkable bitset
    if g(132) in PTRS:               # partitions(16)
        for _ in range(g(128)):
            s.raw(4); s.u32(3)
    if g(140) in PTRS:               # aabbTrees(32)
        for _ in range(g(136)):
            s.u32(3); s.u16(2); s.u32(4)
    if g(148) in PTRS:               # cmodels(76)
        for _ in range(g(144)):
            _cmodel(s)
    if g(160) in PTRS:
        s.raw(g(152) * g(156))       # visibility bytes
    if g(168) in PTRS:               # mapEnts inline (36 + blob + triggers)
        me = s.o
        s.ptr(2); s.u32(2); s.ptr(); s.u32(); s.ptr(); s.u32(); s.ptr()
        if s.peek32(me) in PTRS:
            s.cstr()
        if s.peek32(me + 4) in PTRS:
            s.raw(s.peek32(me + 8))  # entityString
        if s.peek32(me + 16) in PTRS:     # trigger models(8)
            for _ in range(s.peek32(me + 12)):
                s.u32(); s.u16(2)
        if s.peek32(me + 24) in PTRS:     # trigger hulls(32)
            for _ in range(s.peek32(me + 20)):
                s.u32(7); s.u16(2)
        if s.peek32(me + 32) in PTRS:     # trigger slabs(20)
            for _ in range(s.peek32(me + 28)):
                s.u32(5)
    if g(172) in PTRS:               # box_brush cbrush_t(96)
        s.u32(8); s.ptr(); s.u32(13); s.ptr(); s.u32()
    for i, doff in enumerate((264, 268)):  # dynEntDefList[2] (84 each)
        cnt = u16at(off + 254 + i * 2)
        if g(doff) in PTRS:
            db0 = s.o
            for _ in range(cnt):
                s.u32(8)             # type + pose @0..31
                s.ptr(2)             # xModel/destroyedxModel @32/36
                s.u16(2)             # @40 = 0x28 brushModel(u16) + 0x2a physicsBrushModel(u16)
                                     #   rule (BS): swapping this pair as one u32 TRANSPOSES it.
                                     #   Latent so far (equal on 524/524 skate records) but wrong.
                s.ptr()              # destroyFx @44
                s.u32()              # @48
                s.ptr()              # destroyPieces @52
                s.ptr()              # physPreset @56
                s.u16(2)             # @60 = 0x3c/0x3e physConstraints[0],[1]
                s.u16(2)             # @64 = 0x40/0x42 physConstraints[2],[3]
                                     #   rule (BS): was u32-swapped -> [2]<->[3] transposed on
                                     #   30/524 skate records (measured; e.g. rec 161 PC
                                     #   [117,118,119,120] -> console [117,118,120,119]).
                s.u32(3)             # @68..79
                s.u16(2)             # @80
            for j in range(cnt):
                db = db0 + j * 84
                for o2 in (32, 36, 44, 52):
                    if s.peek32(db + o2) in PTRS:
                        raise RuntimeError('inline sub-asset @dynEntDef+%d needs a consumer' % o2)
                if s.peek32(db + 56) in PTRS:
                    _physpreset_inline(s)
    if g(316) in PTRS:               # constraints PhysConstraint(168)
        ncon = g(312)
        cb0 = s.o
        for _ in range(ncon):
            s.u16(); s.raw(2)        # targetname + pad
            s.u32(3)                 # type/attach1/target_index1
            s.u16(); s.raw(2)        # target_ent1 + pad
            s.ptr()                  # target_bone1 @20
            s.u32(2)                 # attach2/target_index2
            s.u16(); s.raw(2)        # target_ent2 + pad
            s.ptr()                  # target_bone2 @36
            s.u32(25)                # @40..139
            s.ptr()                  # material @140
            s.u32(6)                 # handles @144..167
        for j in range(ncon):
            cb = cb0 + j * 168
            if s.peek32(cb + 20) in PTRS:
                s.cstr()
            if s.peek32(cb + 36) in PTRS:
                s.cstr()
            if s.peek32(cb + 140) in PTRS:
                body, nxt = MC.convert_material(pc, s.o, s.reloc)
                s.b += body
                s.o = nxt
    return bytes(s.b), s.o
