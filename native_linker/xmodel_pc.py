#!/usr/bin/env python3
"""
PC-side XModel span parser (HANDOFF Track C span / Track E dispatch). Returns the exact end offset
of one PC XModel so the walk can resync. This is the SPAN half only (reading PC extents) — fully
separable from the console GX2 surface CONVERSION (which needs the vd0-session tooling); PC XSurface
is a plain 80-B struct with no verts1/GX2 split.

Stream order (mirrors console xmodel_probe.parse_xmodel, PC sizes):
  body(248) -> [name, boneNames, parentList, quats, trans, partClassification, baseMat]  (== the
  convert_xmodel_bonedata span) -> surfs[numsurfs]xXSurface(80) + each surface's dynamic
  (verts0 vc*32, vertList vlc*XRigidVertList(12)+trees, triIndices tc*6) -> materialHandles[numsurfs]
  (asset refs; FOLLOW -> inline Material via material_convert) -> collSurfs[numCollSurfs]xXModelCollSurf_s
  (44)(+collTris) -> boneInfo[numBones]xXBoneInfo(44) -> himipInvSqRadii[numsurfs] (PC: usually null)
  -> physPreset(84)+strings -> collmaps -> physConstraints.
"""
import struct
import material_convert as MC
import xmodel_convert as XC

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

BODY = 248
SURF = 80
VTX = 32                      # PC GfxPackedVertex
XRVL = 12                     # XRigidVertList
CTREE = 40                    # XSurfaceCollisionTree
COLLSURF = 44                 # XModelCollSurf_s
COLLTRI = 48                  # XModelCollTri_s
XBONEINFO = 44
PHYSPRESET = 84
DOBJ = 32                     # DObjAnimMat (baseMat handled in bonedata)


class Fail(Exception):
    pass


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]

def _u16(d, o):
    return struct.unpack_from('<H', d, o)[0]


def _surface_dyn(d, b, c):
    """One PC XSurface's dynamic data at cursor c; b = surface body offset."""
    flags = _u16(d, b + 2)
    vc = _u16(d, b + 4)
    tc = _u16(d, b + 6)
    vlc = d[b + 1]
    # skinned surfaces (flags & 2): vertInfo.vertsBlend (u16) + tensionData (f32), emitted before
    # verts0. vertInfo.vertCount[4] = s16 @ XSurface+16; counts per the ZoneCode arraysize exprs.
    vi = [struct.unpack_from('<h', d, b + 16 + j * 2)[0] for j in range(4)]
    if _u32(d, b + 24) in PTRS:                        # vertsBlend
        c[0] += (vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]) * 2
    if _u32(d, b + 28) in PTRS:                        # tensionData
        c[0] += (vi[0] + vi[1] + vi[2] + vi[3]) * 4
    # verts0: present when !(flags & 1) and the ptr FOLLOWs (reusable -> may be aliased)
    if not (flags & 1) and _u32(d, b + 32) in PTRS:
        c[0] += vc * VTX
    # vertList: vlc x XRigidVertList(12), each optionally an XSurfaceCollisionTree
    if _u32(d, b + 40) in PTRS:
        base = c[0]
        c[0] += vlc * XRVL
        for k in range(vlc):
            if _u32(d, base + k * XRVL + 8) in PTRS:          # collisionTree
                tb = c[0]
                c[0] += CTREE
                node_count = _u32(d, tb + 24)
                leaf_count = _u32(d, tb + 32)
                if _u32(d, tb + 28) in PTRS:
                    c[0] += node_count * 16                    # XSurfaceCollisionNode
                if _u32(d, tb + 36) in PTRS:
                    c[0] += leaf_count * 2                     # XSurfaceCollisionLeaf
    # triIndices: tc x 6 (XSurfaceTri16 = 3 u16)
    if _u32(d, b + 12) in PTRS:
        c[0] += tc * 6


def _collmaps_span(d, c, ncm):
    """PC collmaps: ncm x Collmap(4) ptrs, then each PhysGeomList chain (sizes identical to console
    consume_collmaps: PhysGeomList 12, PhysGeomInfo 68, BrushWrapper 96, cbrushside_t 12, cplane_s 20)."""
    base = c[0]
    c[0] += 4 * ncm
    for i in range(ncm):
        if _u32(d, base + i * 4) not in PTRS:
            continue
        gl = c[0]
        c[0] += 12                                # PhysGeomList {count, geoms*, contents}
        cnt = _u32(d, gl)
        if _u32(d, gl + 4) in PTRS:
            gbase = c[0]
            c[0] += 68 * cnt                      # PhysGeomInfo bodies
            for g in range(cnt):
                if _u32(d, gbase + g * 68) not in PTRS:
                    continue
                bw = c[0]
                c[0] += 96                        # BrushWrapper
                nsides = _u32(d, bw + 28)
                nverts = _u32(d, bw + 84)
                if _u32(d, bw + 32) in PTRS:      # sides
                    sbase = c[0]
                    c[0] += 12 * nsides
                    for s in range(nsides):
                        if _u32(d, sbase + s * 12) in PTRS:
                            c[0] += 20            # cplane_s
                if _u32(d, bw + 88) in PTRS:      # verts
                    c[0] += 12 * nverts
                if _u32(d, bw + 92) in PTRS:      # planes
                    c[0] += 20 * nsides


def parse_xmodel_pc(d, off):
    """PC XModel at `off` -> end offset."""
    if _u32(d, off) != FOLLOW and _u32(d, off) < 0xA0000000:
        raise Fail('bad xmodel name ptr')
    nb, nrb, ns = d[off + 4], d[off + 5], d[off + 6]
    ncoll = _u32(d, off + 156)
    ncollmaps = d[off + 220]
    # body + bone-data prefix (name..baseMat) — reuse the validated bonedata span
    _, cur = XC.convert_xmodel_bonedata(d, off)
    c = [cur]

    # surfs: ns bodies, then each surface's dynamic
    if _u32(d, off + 32) in PTRS:
        sb = c[0]
        c[0] += ns * SURF
        for i in range(ns):
            _surface_dyn(d, sb + i * SURF, c)
    # materialHandles: ns asset refs; FOLLOW -> inline Material
    if _u32(d, off + 36) in PTRS:
        base = c[0]
        c[0] += 4 * ns
        for i in range(ns):
            if _u32(d, base + i * 4) in PTRS:
                _, nxt = MC.convert_material(d, c[0])
                c[0] = nxt
    # collSurfs: numCollSurfs x XModelCollSurf_s(44), each collTris[numCollTris] x 48
    if _u32(d, off + 152) in PTRS:
        base = c[0]
        c[0] += COLLSURF * ncoll
        for i in range(ncoll):
            cs = base + i * COLLSURF
            if _u32(d, cs + 0) in PTRS:
                c[0] += _u32(d, cs + 4) * COLLTRI                # numCollTris
    # boneInfo: numBones x XBoneInfo(44), no dynamic
    if _u32(d, off + 164) in PTRS:
        c[0] += XBONEINFO * nb
    # himipInvSqRadii: numsurfs floats (PC: usually null; consume if FOLLOW)
    if _u32(d, off + 200) in PTRS:
        c[0] += 4 * ns
    # physPreset: inline PhysPreset(84) + name + sndAliasPrefix strings
    if _u32(d, off + 216) in PTRS:
        pb = c[0]
        c[0] += PHYSPRESET
        if _u32(d, pb + 0) in PTRS:
            c[0] = d.index(b'\x00', c[0]) + 1
        if _u32(d, pb + 28) in PTRS:
            c[0] = d.index(b'\x00', c[0]) + 1
    # collmaps: numCollmaps -> Collmap chain
    if _u32(d, off + 224) in PTRS and ncollmaps:
        _collmaps_span(d, c, ncollmaps)
    # physConstraints: rare
    if _u32(d, off + 228) in PTRS:
        raise Fail('inline physConstraints — not built')
    return c[0]
