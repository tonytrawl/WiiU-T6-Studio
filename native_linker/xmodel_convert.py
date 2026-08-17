#!/usr/bin/env python3
"""
XModel PC(v147, LE) -> console(WiiU v148, BE) converter  (HANDOFF Track C).

This module converts the XModel *body* (the 248 B PC struct -> 244 B console struct), its
non-surface trailing data, AND the XSurface block (convert_xmodel_surfaces, below).

XSurface (80 -> 128 B) validated vs genuine mp_raid/zm_transit (validate_xmodel_surface.py):
headers byte-exact (masked omap ptrs), per-surface dynamic (verts0/verts1/vertList/triIndices)
byte-exact EXCEPT two inherently-non-reproducible regions, and 100% self-resync on 260 MP + 35 ZM
models. The two lossy regions (documented, not bugs): (1) verts0 normal/tangent (PC's 10-bit
packed frame already lost precision, see latte_vertex); (2) collision-tree node counts (the console
linker REBUILDS the surface BVH — nc 18 vs PC 20 seen; leaves match). Weapon *_view models diverge
wholesale (console re-authored the mesh, different vert/tri counts) — same caveat as the body.

== Body layout (verified vs genuine common_mp, 465 matched pairs) ==
Console XModel = 244 B, PC = 248 B. PC-identical through offset +208; PC's `bool bad`@212 (+3 pad)
is DROPPED and the tail shifts -4. Every field is a plain byte-swap / pointer-relocate EXCEPT:
  * himipInvSqRadii (ptr @200): PC = null, console = FOLLOW to an inline `numsurfs` f32 array that
    the console linker GENERATES (per-surface inverse-square himip radius). NOT derivable from PC
    by copy — must be synthesized (passed in via `himip`).
  * memUsage (@204): a console-computed memory-usage stat; differs from PC. Passed in via `memusage`.
Both are the only non-PC-derivable body fields; everything else is byte-exact from PC.

Pointer fields (name, boneNames, parentList, quats, trans, partClassification, baseMat, surfs,
materialHandles, collSurfs, boneInfo, physPreset, collmaps, physConstraints) are remapped through
`reloc` (default identity for tests; wire to the omap at integration).
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
PC_BODY = 248
CO_BODY = 244

# FIX B (boot-53 skybox class): installed by produce_nobackbone when
# INLINE_ASSET_NAMES is on — resolves a PC b5-alias word to the name string
# so convert_xmodel can emit aliased root names INLINE (FOLLOW + string).
# None (default) = unchanged byte-exact behavior for oracle validation.
INLINE_NAME_RESOLVER = None

# I1 stage 1 (dedup_binder.MharrBinder): installed by produce_nobackbone ONLY
# for maps that opt in. When set, an XModel.materialHandles dedup back-ref is
# resolved to the PC cell it NAMES (ordinal identity, PC FILE offsets) and
# re-minted from OUR OWN emitted holder cell, BEFORE the generic
# `Omap.reloc` chain — the same shape as the boot-proven et7 resolver
# `Omap.reloc_asset_entry`. None (the default, every other map) leaves this
# file's behavior byte-identical to before I1.
MHARR_BINDER = None


def _default_reloc(v):
    return v


def _sw32(pc, o):
    return struct.pack('>I', struct.unpack_from('<I', pc, o)[0])

def _sw16(pc, o):
    return struct.pack('>H', struct.unpack_from('<H', pc, o)[0])


def _lodinfo(pc, o):
    """One XModelLodInfo (28 B, same size both platforms)."""
    b = bytearray()
    b += _sw32(pc, o + 0)                 # dist (f32)
    b += _sw16(pc, o + 4)                 # numsurfs (u16)
    b += _sw16(pc, o + 6)                 # surfIndex (u16)
    for k in range(5):
        b += _sw32(pc, o + 8 + k * 4)     # partBits[5]
    return bytes(b)


def convert_xmodel_bonedata(pc, body_off):
    """Convert the contiguous trailing bone-data block that follows the XModel body and precedes
    the surfaces: name string, boneNames, parentList, quats, trans, partClassification, baseMat.
    Sub-structs are the same size on both platforms; per-array swap/verbatim verified vs genuine
    common_mp (363-swap boneNames, etc). Returns (console_bytes, next_pc_off) where next_pc_off
    points at the `surfs` array (surface conversion is a separate sub-project).
    boneInfo/collSurfs/himip/physPreset/collmaps come AFTER surfaces and are not handled here."""
    nb, nrb = pc[body_off + 4], pc[body_off + 5]
    n = nb - nrb
    out = bytearray()
    src = body_off + PC_BODY
    def p(o):
        return struct.unpack_from('<I', pc, body_off + o)[0]

    if p(0) in PTRS:                                   # name c-string
        end = pc.index(0, src); out += pc[src:end + 1]; src = end + 1
    if p(8) in PTRS:                                   # boneNames: nb x ScriptString(u16) — swap
        for i in range(nb):
            out += _sw16(pc, src + i * 2)
        src += 2 * nb
    if p(12) in PTRS:                                  # parentList: (nb-nrb) x u8 — verbatim
        out += pc[src:src + n]; src += n
    if p(16) in PTRS:                                  # quats: (nb-nrb) x XModelQuat(4 s16) — swap
        for i in range(n * 4):
            out += _sw16(pc, src + i * 2)
        src += 8 * n
    if p(20) in PTRS:                                  # trans: (nb-nrb) x 4 f32 — swap
        for i in range(n * 4):
            out += _sw32(pc, src + i * 4)
        src += 16 * n
    if p(24) in PTRS:                                  # partClassification: nb x u8 — verbatim
        out += pc[src:src + nb]; src += nb
    if p(28) in PTRS:                                  # baseMat: nb x DObjAnimMat(8 f32) — swap
        for i in range(nb * 8):
            out += _sw32(pc, src + i * 4)
        src += 32 * nb
    return bytes(out), src


# ------------------------------------------------------------------ surfaces
# PC XSurface = 80 B, console = 128 B (a GX2 struct, NOT a shifted PC struct).
# Header field map (derived empirically vs genuine common_mp/mp_raid, aligned by
# model-name + surface-index; see /tmp/align_surf.py exploration):
#   off  PC(80,LE)                     console(128,BE)
#   +0   tileMode u8                   +0   (copy)
#   +1   vertListCount u8              +1   (copy)
#   +2   flags u16                     +2   (swap)
#   +4   vertCount u16                 +4   (swap)
#   +6   triCount u16                  +6   (swap)
#   +8   baseTriIndex u16              +8   (swap)
#   +10  baseVertIndex u16             +10  (swap)
#   +12  triIndices*                   +12  (ptr)
#   +16  vertInfo.vertCount[4] i16     +16  (4x swap)
#   +24  vertsBlend* (skinned)         +24  (skinned; raise for now)
#   +28  tensionData* (skinned)        +28
#   +32  verts0*                       +52  (ptr)  <-- RELOCATED slot
#   --                                 +72  verts1* (NEW console 2nd stream; FOLLOW)
#   +40  vertList*                     +96  (ptr)  <-- RELOCATED slot
#   +48  partBits[5] u32               +108 (5x swap)
# verts0/verts1/triIndices are LINEAR buffers (no GX2 tiling); latte_vertex
# re-encodes them byte-exact contiguously.  Console per-surface dynamic order:
# verts0 (24*vc) -> verts1 (8*vc) -> vertList(+trees) -> triIndices (6*tc).
SURF_PC = 80
SURF_CO = 128


def _u16le(d, o):
    return struct.unpack_from('<H', d, o)[0]

def _u32le(d, o):
    return struct.unpack_from('<I', d, o)[0]


def _ptr(v, reloc):
    """Pointer word -> BE console word: FOLLOW/INSERT preserved, else relocated."""
    return struct.pack('>I', v if v in PTRS else reloc(v))


def convert_surface_header(pc, o, reloc=_default_reloc, force_rigid=False,
                           skin=None, inline_dedup=False):
    """One PC XSurface header (80 B) -> console header (128 B).

    skin: (n1, n2, n3) Latte skin-stream u16 counts (from _latte_skin_streams) for a
    SKINNED PC surface (flags&2, vertsBlend/tension present) — emits the genuine skinned
    console header: flags kept, vertsBlend marker @+24, counts @+28/+40, three stream
    FOLLOW markers @+32/+36/+44.

    force_rigid (legacy fallback, superseded by skin): emit a skinned PC surface as a
    GENUINE rigid console surface — clear flags&2, null blend/stream slots (bind-pose
    verts render). With neither, a skinned surface raises (byte-parity contexts).

    Rule (D), HANDOFF_pipeline_bake_rules.md — DEDUP-SKINNED surfaces REFUSE loudly:
    a PC vertsBlend/tensionData word that is a nonzero non-FOLLOW/INSERT value is an
    0xA0-family dedup alias to another surface's skin data. The old behavior fell
    through to force_rigid: flag bit 2 cleared, skin slots left NULL, vertInfo counts
    KEPT — the engine then runs the skin pre-pass off vertInfo and binds NULL (the
    boot-proven teardrop-flag/restroom crash class). Translated-alias emission (the
    D2-style synthesis) is NOT implemented because it cannot be oracle-validated:
    genuine-corpus census 2026-07-30 found ZERO dedup-skinned surfaces —
    mp_raid_genuine: 1773 surfaces, 6 skinned (all four skin words @+24/+32/+36/+44
    FOLLOW, fully inline), 212 dedup surfaces ALL rigid; mp_dockside_wiiu: 2067
    surfaces, 5 skinned all-FOLLOW, 398 dedup all rigid. Unlike D2's verts1/vertList
    synthesis (derivable from the in-header vertCount alone), the skin-stream aliases
    at +0x20/+0x24/+0x2c need the SOURCE surface's n1/n2 stream lengths, which a
    dedup PC header does not carry (PC has no skin streams), so a blind synthesis
    would be guesswork with zero genuine precedent. Fail the build instead of
    shipping the crash class."""
    vb = _u32le(pc, o + 24)
    td = _u32le(pc, o + 28)
    if vb in PTRS or td in PTRS:
        if skin is None and not force_rigid:
            raise NotImplementedError('skinned surface header (flags&2): pass skin= counts '
                                      'from _latte_skin_streams (or force_rigid)')
    elif inline_dedup:
        # rule (D) FIX: this dedup-skinned surface resolved uniquely to its source
        # (see _resolve_dedup_skin); the caller emits the source's vertsBlend, skin
        # streams, verts0/verts1 and triIndices INLINE, so the header takes the
        # genuine skinned shape -- flags bit 1 KEPT, the three alias words FOLLOW.
        if skin is None:
            raise ValueError('rule (D) inline_dedup requires skin= counts derived '
                             'from the SOURCE surface (_latte_skin_streams)')
    else:
        if vb != 0 or td != 0:
            # rule (D): dedup-skinned surface — never force_rigid-null silently.
            raise ValueError(
                'rule (D) dedup-skinned XSurface preservation '
                '(HANDOFF_pipeline_bake_rules.md): PC surface @0x%x carries a dedup '
                'alias in its skin pointers (vertsBlend=0x%08x tensionData=0x%08x, '
                'flags=0x%04x) instead of FOLLOW/INSERT/NULL. Emitting it force_rigid '
                'would clear flag bit 2 and null the skin streams while KEEPING '
                'vertInfo counts, so the engine runs the skin pre-pass off vertInfo '
                'and binds NULL (boot-proven crash). No genuine console zone contains '
                'a dedup-skinned surface (census: raid 212 + dockside 398 dedup '
                'surfaces, all rigid; all 11 skinned surfaces fully inline), so the '
                'translated-alias skin block cannot be oracle-validated. Refusing to '
                'convert: inline the source skin data upstream (drop the dedup for '
                'this surface) or convert it as a genuinely rigid PC surface.'
                % (o, vb, td, _u16le(pc, o + 2)))
        skin = None
    out = bytearray(SURF_CO)
    out[0] = pc[o + 0]                                     # tileMode
    out[1] = pc[o + 1]                                     # vertListCount
    flags = _u16le(pc, o + 2)
    if force_rigid and skin is None:
        flags &= ~2                                        # clear the skinned bit
    struct.pack_into('>H', out, 2, flags)                 # flags
    struct.pack_into('>H', out, 4, _u16le(pc, o + 4))     # vertCount
    struct.pack_into('>H', out, 6, _u16le(pc, o + 6))     # triCount
    struct.pack_into('>H', out, 8, _u16le(pc, o + 8))     # baseTriIndex
    struct.pack_into('>H', out, 10, _u16le(pc, o + 10))   # baseVertIndex
    out[12:16] = _ptr(FOLLOW if inline_dedup                  # triIndices
                      else _u32le(pc, o + 12), reloc)
    for j in range(4):                                    # vertInfo.vertCount[4]
        out[16 + j * 2:18 + j * 2] = _sw16(pc, o + 16 + j * 2)
    if skin is not None:
        n1, n2, n3 = skin
        out[24:28] = struct.pack('>I', FOLLOW)            # vertsBlend
        out[28:32] = struct.pack('>I', (n2 << 16) | n1)   # stream counts 1+2
        out[32:36] = struct.pack('>I', FOLLOW)            # skin stream 1
        out[36:40] = struct.pack('>I', FOLLOW)            # skin stream 2
        out[40:44] = struct.pack('>I', n3)                # stream count 3
        out[44:48] = struct.pack('>I', FOLLOW)            # skin stream 3
    v0 = FOLLOW if inline_dedup else _u32le(pc, o + 32)
    co_v0 = v0 if v0 in PTRS else reloc(v0)
    out[52:56] = struct.pack('>I', co_v0)                 # verts0  (PC@32 -> CO@52)
    if v0 in PTRS:
        # inline surface: console adds the second vertex stream -> FOLLOW
        out[72:76] = struct.pack('>I', FOLLOW)
        out[96:100] = _ptr(_u32le(pc, o + 40), reloc)     # vertList (PC@40 -> CO@96)
    elif 0xA0000000 <= co_v0 < 0xC0000000:
        # DEDUP surface (verts0 aliased to another surface's data). Genuine
        # console ALWAYS aliases verts1 AND vertList too (raid oracle 132/132;
        # skate boot-48/49: verts1=NULL here = invisible/exploded models).
        # Layout rule, oracle-EXACT on all 132 raid dedup surfaces:
        #   verts1  = align64(verts0_payload + vertCount*24)
        #   vertList = verts1 + vertCount*8
        # Synthesized from the relocated verts0 rather than trusting a carried
        # vertList reloc — removes the stale-alias failure mode entirely.
        vc_ = _u16le(pc, o + 4)
        v0_pay = (co_v0 - 1) & 0x1FFFFFFF
        v1_pay = (v0_pay + vc_ * 24 + 63) & ~63
        out[72:76] = struct.pack('>I', 0xA0000000 + v1_pay + 1)
        if pc[o + 1]:                                     # vertListCount > 0
            out[96:100] = struct.pack('>I', 0xA0000000 + (v1_pay + vc_ * 8) + 1)
        else:
            out[96:100] = _ptr(_u32le(pc, o + 40), reloc)
    else:
        out[72:76] = b'\0\0\0\0'
        out[96:100] = _ptr(_u32le(pc, o + 40), reloc)     # vertList (PC@40 -> CO@96)
    for j in range(5):                                    # partBits[5] (PC@48 -> CO@108)
        out[108 + j * 4:112 + j * 4] = _sw32(pc, o + 48 + j * 4)
    return bytes(out)


# A3 collision-BVH rebuild (console_bvh.py): the console linker rebuilds each
# rigid vertList's collision tree; byte-swapping the PC tree leaves 69 raid
# models size-different (net +334 B). The rebuild algorithm is 33/35 byte-exact
# on sampled genuine trees but its remaining split-plane-pick nuance regresses
# ~48 other models (net -9,936 B) when applied everywhere, so it stays OFF
# until the pick heuristic is fully cracked (HANDOFF_track_XModel.md A3).
REBUILD_COLLISION_TREES = False


def _vertlist_extent(pc, start, vlc):
    """Dry-scan: byte length of the PC vertList region (entries + trees)."""
    c = start + vlc * 12
    for k in range(vlc):
        vl = start + k * 12
        if _u32le(pc, vl + 8) not in PTRS:
            continue
        tb = c
        c += 40
        if _u32le(pc, tb + 28) in PTRS:
            c += _u32le(pc, tb + 24) * 16
        if _u32le(pc, tb + 36) in PTRS:
            c += _u32le(pc, tb + 32) * 2
    return c - start


def _convert_vertlist(pc, c, vlc, reloc, tree_src=None):
    """PC vertList (vlc x XRigidVertList(12) + optional XSurfaceCollisionTree) -> console.

    tree_src = (verts0_off, tris_off) PC offsets: REBUILD each collision tree with the
    console linker's BVH algorithm (console_bvh; the console recomputes node/leaf
    sets — byte-swapping the PC tree leaves the A3 size-diffs). None -> legacy swap."""
    import sys, os
    out = bytearray()
    base = c[0]
    c[0] += vlc * 12
    for k in range(vlc):
        vl = base + k * 12
        for j in range(4):                        # boneOffset/vertCount/triOffset/triCount u16
            out += _sw16(pc, vl + j * 2)
        out += _ptr(_u32le(pc, vl + 8), reloc)    # collisionTree*
    for k in range(vlc):
        vl = base + k * 12
        if _u32le(pc, vl + 8) not in PTRS:
            continue
        tb = c[0]
        c[0] += 40
        nc = _u32le(pc, tb + 24)
        lc = _u32le(pc, tb + 32)
        has_nodes = _u32le(pc, tb + 28) in PTRS
        has_leafs = _u32le(pc, tb + 36) in PTRS
        if tree_src is not None and has_nodes and has_leafs:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import console_bvh as CB
            tri_off = _u16le(pc, vl + 4)          # triOffset
            tri_cnt = _u16le(pc, vl + 6)          # triCount
            bt = CB.build_tree_pc(pc, tree_src[0], tree_src[1], tri_off, tri_cnt)
            for j in range(3):
                out += struct.pack('>f', float(bt['trans'][j]))
            for j in range(3):
                out += struct.pack('>f', float(bt['scale'][j]))
            out += struct.pack('>I', len(bt['nodes']))
            out += _ptr(_u32le(pc, tb + 28), reloc)
            out += struct.pack('>I', len(bt['leafs']))
            out += _ptr(_u32le(pc, tb + 36), reloc)
            for n in bt['nodes']:
                out += struct.pack('>8H', *n)
            out += struct.pack('>%dH' % len(bt['leafs']), *bt['leafs'])
        else:
            for j in range(6):                    # trans[3]+scale[3] f32
                out += _sw32(pc, tb + j * 4)
            out += _sw32(pc, tb + 24)             # nodeCount
            out += _ptr(_u32le(pc, tb + 28), reloc)  # nodes*
            out += _sw32(pc, tb + 32)             # leafCount
            out += _ptr(_u32le(pc, tb + 36), reloc)  # leafs*
            if has_nodes:
                nb = c[0]
                for i in range(nc * 8):           # XSurfaceCollisionNode = 8 u16
                    out += _sw16(pc, nb + i * 2)
            if has_leafs:
                lb = c[0] + (nc * 16 if has_nodes else 0)
                for i in range(lc):               # XSurfaceCollisionLeaf = 1 u16
                    out += _sw16(pc, lb + i * 2)
        # consume the PC tree data regardless of emit path
        if has_nodes:
            c[0] += nc * 16
        if has_leafs:
            c[0] += lc * 2
    return bytes(out)


# ------------------------------------------------- Latte skin streams (A2)
# The 3 console-only skin streams ARE PC-derivable (RE'd 2026-07-13 vs genuine
# mp_raid, 6/6 skinned surfaces byte-exact incl. 3/4-bone verts; the prior
# "not derivable" ruling in HANDOFF_skinned_skinstream.md is disproven).
# Layout after vertsBlend, sizes n1=lo16(+28) n2=hi16(+28) n3=(+40), each x2 B:
#   st1 (n1 u16): BE command stream, per 128-vertex group:
#     0000 B          set current bone (B = boneIndex*64); re-emitted per group,
#                     carried across chunk boundaries
#     0001 C S        C consecutive 1-bone verts at group-local index S
#     0002 C S w[C]   multi-bone verts whose MIN influence bone == current
#     0003 C S w[C]   non-min influence of the current bone
#     0004 C S        verts whose PC-first bone (b0) == current bone
#     0006            group advance (also once after the last group)
#     weights: stored PC u16 for b1..b3; b0 gets 0xffff - sum(other weights)
#   st2 (n2 u16): per-group vert counts (n2 = ceil(vc/128), last partial)
#   st3 (n3 u16): st1 chunk sizes. Chunks: atomic units (SETBONE / one whole
#     per-(bone,op) run sequence / GROUPEND) packed to <=512 u16; on overflow
#     close with 0005 + 0008-pad to a 32-u16 multiple (last chunk: 0008-pad
#     only, no 0005); n1 = total zero-padded to a 64-u16 multiple.

def _decode_vertsblend(pc, off, vi):
    """PC (LE) vertsBlend @off -> per-vertex ordered [(bone, storedweight)]
    (PC record order, b0 first; 1/3/5/7 u16s for 1..4 bones)."""
    verts = []
    p = off
    def u16(x):
        return struct.unpack_from('<H', pc, x)[0]
    for nb_ in range(4):
        for _ in range(vi[nb_]):
            v = [(u16(p) // 64, None)]
            for j in range(nb_):
                v.append((u16(p + 2 + j * 4) // 64, u16(p + 4 + j * 4)))
            p += 2 + 4 * nb_
            verts.append(v)
    return verts


def _latte_skin_streams(pc, vb_off, vi):
    """Generate the 3 console skin streams from PC vertsBlend.
    Returns (bytes st1+st2+st3 BE-packed, (n1, n2, n3))."""
    verts = _decode_vertsblend(pc, vb_off, vi)
    ng = (len(verts) + 127) // 128
    st2 = [min(128, len(verts) - g * 128) for g in range(ng)]
    units = []                       # atomic word-lists
    cur_bone = None
    for g in range(ng):
        grp = verts[g * 128:g * 128 + 128]
        for b in sorted({bn for v in grp for bn, _ in v}):
            ops = {1: [], 2: [], 3: [], 4: []}
            for li, v in enumerate(grp):
                bones = [bn for bn, _ in v]
                if b not in bones:
                    continue
                if len(v) == 1:
                    ops[1].append((li, None))
                else:
                    if b == v[0][0]:     # pc-first bone: complement weight
                        w = (0xffff - sum(x for _, x in v[1:])) & 0xffff
                    else:
                        w = dict(v)[b]
                    ops[2 if b == min(bones) else 3].append((li, w))
                    if b == v[0][0]:
                        ops[4].append((li, None))
            emitted_setbone = b == cur_bone
            for op in (1, 2, 3, 4):
                lst = ops[op]
                u = []
                i = 0
                while i < len(lst):      # consecutive-index runs
                    j = i
                    while j + 1 < len(lst) and lst[j + 1][0] == lst[j][0] + 1:
                        j += 1
                    u += [op, j - i + 1, lst[i][0]]
                    if op in (2, 3):
                        u += [w for _, w in lst[i:j + 1]]
                    i = j + 1
                if u:
                    if not emitted_setbone:
                        units.append([0x0000, b * 64])
                        emitted_setbone = True
                        cur_bone = b
                    units.append(u)
        units.append([0x0006])
        cur_bone = None
    st1, st3 = [], []
    cstart = [0]
    def close(final=False):
        if not final:
            st1.append(0x0005)
        while (len(st1) - cstart[0]) % 32:
            st1.append(0x0008)
        st3.append(len(st1) - cstart[0])
        cstart[0] = len(st1)
    for u in units:
        if (len(st1) - cstart[0]) + len(u) > 512:
            close()
        st1.extend(u)
    close(final=True)
    while len(st1) % 64:
        st1.append(0)
    n1, n2, n3 = len(st1), len(st2), len(st3)
    blob = struct.pack('>%dH' % (n1 + n2 + n3), *(st1 + st2 + st3))
    return blob, (n1, n2, n3)


# ------------------------------------------------- rule (D) dedup-skinned fix
# SPEC_ruleD_inline_dedup_skinned.md / FINDINGS_ruleD_dedup_skinned_xsurfaces.md
#
# A DEDUP-SKINNED surface differs from the surface it dedups against in EXACTLY
# three pointer words -- vertsBlend(+24), verts0(+32), triIndices(+12), an 0xA0
# alias where the source has FOLLOW.  Measured on all 31 in zm_nuked: flags,
# tileMode, vertListCount, baseTriIndex, baseVertIndex, partBits[5], vertInfo[4],
# vertCount and triCount are IDENTICAL 31/31; tensionData and vertList are NULL on
# both (and on all 384 inline-skinned surfaces in the zone).
#
# So the fix is not a synthesis: resolve the dedup to its source and emit the three
# payloads INLINE from the source's PC offsets, producing a surface whose shape is
# the one every genuine skinned surface has (raid 6/6, dockside 5/5, transit 10/10,
# all FOLLOW/NULL/FOLLOW/NULL/FOLLOW).  Alias-minting into our own emitted source
# block was REJECTED: zero retail precedent in MP or ZM.
#
# ⛔ TWO INVARIANTS THIS PATH MUST NOT BREAK:
#   1. The PC cursor MUST NOT advance for an inlined payload.  A dedup surface owns
#      no bytes at its own stream position; the payload is read from the SOURCE's
#      offsets.  Advancing would move `cur`, which becomes materialHandles' pc_base
#      -> I1's census keys (PC FILE offsets) would drift.  Gate: next_pc_off must be
#      byte-identical to parse_xmodel_pc's span end for all 664 models.
#   2. No `marks` are registered for inlined payloads.  The PC span belongs to the
#      SOURCE surface, which registers it already; a second console target for the
#      same PC offset would make the fine map ambiguous.  A PC pointer into that
#      region must keep resolving to the source's console copy.
DEDUP_SKIN_INDEX = None       # installed by produce_nobackbone; None = raise as before
DEDUP_SKIN_GAP_MAX = 16       # PC runtime alignment slack the file cursor does not model

# ---- rule (D) THIRD SELECTOR state (2026-08-17) -----------------------------
# ⛔⛔ DO NOT ASSIGN THESE DIRECTLY. Use install_dedup_skin_state(), which sets
# all of them together. Two call sites install rule-(D) state
# (produce_nobackbone.py and ruleD_xmodel_census.py) and they MUST NOT drift:
# the census reporting a different number from the build is not hypothetical, it
# is the defect FINDINGS_mirage_last_two.md 6.1 records (highrise read 40 where
# the build read 10, because the census installed one table and the build
# installed another). A single installer makes divergence impossible by
# construction, which is stronger than a guard that merely detects it -- and
# _resolve_dedup_skin RAISES if it finds the index installed without its
# companions, so bypassing the installer fails loudly instead of silently
# reverting to the two-selector behaviour.
DEDUP_SKIN_OWNER = None       # {pc_body_start: walk_index} -- the join data
DEDUP_SKIN_OAT = None         # oat_ptrtable.PtrTable of FOLLOW producers, or None
DEDUP_SKIN_OAT_SOURCE = None  # path of the loaded table, for receipts


def build_dedup_skin_index(pc, model_offsets):
    """Index every INLINE-skinned surface in the zone by (vertInfo, vertCount,
    triCount) -> its PC payload offsets, for dedup-skinned surfaces to resolve
    against.  `model_offsets` = PC body offsets of the zone's XModels.

    Pure PC-derived and pass-invariant: build once, never rebuild per emit pass.
    Cursor arithmetic mirrors convert_xmodel_surfaces exactly; any drift is caught
    at lookup by the independent alias-gap band, which refuses rather than guesses."""
    idx = {}
    for s in model_offsets:
        if _u32le(pc, s + 32) not in PTRS:      # surfs array itself aliased/NULL
            continue
        ns = pc[s + 6]
        _bones, sb = convert_xmodel_bonedata(pc, s)
        c = sb + ns * SURF_PC
        for i in range(ns):
            o = sb + i * SURF_PC
            flags, vc, tc, vlc = _u16le(pc, o + 2), _u16le(pc, o + 4), _u16le(pc, o + 6), pc[o + 1]
            vi = tuple(struct.unpack_from('<h', pc, o + 16 + j * 2)[0] for j in range(4))
            vb_off = v0_off = ti_off = None
            if _u32le(pc, o + 24) in PTRS:
                vb_off = c
                c += (vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]) * 2
            if _u32le(pc, o + 28) in PTRS:
                c += sum(vi) * 4
            if not (flags & 1) and _u32le(pc, o + 32) in PTRS:
                v0_off = c
                c += vc * 32
            if _u32le(pc, o + 40) in PTRS:
                c += _vertlist_extent(pc, c, vlc)
            if _u32le(pc, o + 12) in PTRS:
                ti_off = c
                c += tc * 6
            if None not in (vb_off, v0_off, ti_off):
                idx.setdefault((vi, vc, tc), []).append(
                    dict(vb=vb_off, v0=v0_off, ti=ti_off, model=s, surf=i))
    return idx


def install_dedup_skin_state(pc, bodies, map_name=None, verbose=False):
    """THE ONLY SUPPORTED WAY to install rule-(D) resolution state.

    Sets DEDUP_SKIN_INDEX, DEDUP_SKIN_OWNER and (when a dump exists)
    DEDUP_SKIN_OAT **together**, from one walk of `bodies`, so the build and the
    census cannot drift apart. `bodies` = walk_pc_bodies() rows
    (i, name, root, start, end, hp).

    `map_name` enables the THIRD selector by locating that map's OAT ptrtable
    through the one path convention (oat_ptrtable.ptrtable_path). Omit it, or
    have no dump on disk, and resolution falls back to exactly the two-selector
    behaviour that shipped before -- i.e. an ambiguous tie still RAISES.

    ⛔ A MISSING TABLE IS NOT AN ERROR (the fleet has dumps for 3 maps); a
    MALFORMED one IS. TableRefusal/OSError leave the selector disabled, anything
    else propagates -- a corrupt table that silently disables the narrowing
    would look exactly like correct conservative behaviour, which is the shape
    this project keeps getting caught by.
    """
    global DEDUP_SKIN_INDEX, DEDUP_SKIN_OWNER
    global DEDUP_SKIN_OAT, DEDUP_SKIN_OAT_SOURCE
    import os as _os

    xrows = [(i, s) for (i, _nm, _r, s, _e, _hp) in bodies
             if _r == 'XModel' and s is not None]
    DEDUP_SKIN_INDEX = build_dedup_skin_index(pc, [s for (_i, s) in xrows])
    DEDUP_SKIN_OWNER = {s: i for (i, s) in xrows}
    DEDUP_SKIN_OAT = None
    DEDUP_SKIN_OAT_SOURCE = None

    if map_name:
        import oat_ptrtable as OP
        p = OP.ptrtable_path(map_name)
        if _os.path.exists(p):
            try:
                DEDUP_SKIN_OAT = OP.load_producers(p)
                DEDUP_SKIN_OAT_SOURCE = p
            except (OP.TableRefusal, OSError) as ex:
                if verbose:
                    print('    rule (D) third selector DISABLED: %s' % ex)
    if verbose:
        print('    rule (D) state: %d index keys, %d owners, OAT %s'
              % (len(DEDUP_SKIN_INDEX or {}), len(DEDUP_SKIN_OWNER or {}),
                 DEDUP_SKIN_OAT_SOURCE or 'ABSENT (two-selector behaviour)'))
    return DEDUP_SKIN_INDEX


def _oat_dedup_owner(vb_alias):
    """OAT's answer for a dedup-skinned surface's own vertsBlend alias word:
    the walk index (`load_index`) of the asset that PRODUCED that payload, or
    None when the table cannot answer uniquely.

    ⛔ `load_index` IS THE WALK INDEX. `asset_ordinal` is NOT -- it is measured
    to disagree, and picking it refuses on every run, which reads as a
    conservative well-behaved selector and passes a negative arm for free.

    ⛔ allow_plus=False. The +256 bucket is a known rt under-count class; here
    an approximate hit names the WRONG SOURCE ASSET and emits the wrong skin
    block, which is the boot-proven crash rule (D) exists to prevent. An
    approximate answer is worse than none.

    ⛔ The exact-hit path inside PtrTable.lookup does NOT check `field` (only
    the +256 fallback does), so the field is verified HERE -- validate the
    target's CONTENT, never just that a lookup returned something (GN).
    """
    if DEDUP_SKIN_OAT is None:
        return None
    import oat_ptrtable as OP
    payload = (vb_alias - 1) & 0x1FFFFFFF
    e, plus = DEDUP_SKIN_OAT.lookup(payload, field='vertsBlend',
                                    allow_plus=False)
    if e is None or plus:
        return None
    if OP.normalize_field(e.field) != 'vertsBlend':
        return None                       # right offset, wrong field -> refuse
    if str(e.asset_type).lower() != 'xmodel':
        return None
    li = (e.raw or {}).get('load_index')
    return li if isinstance(li, int) else None


def _resolve_dedup_skin(pc, o):
    """Resolve a dedup-skinned surface header at PC offset `o` to its source
    payloads, or return None (caller raises).  TWO selectors must agree and the
    match must be UNIQUE:
      * content key (vertInfo, vertCount, triCount) -- collides in general (28 of
        zm_nuked's 384 inline-skinned surfaces share a key), so it is not alone
        sufficient and its success is not a tautology;
      * alias-gap band -- (verts0_alias - vertsBlend_alias) must exceed the
        candidate's own PC file gap by 0..15 B.  Both aliases point into the
        source's contiguous payload, so the unknown alias base CANCELS: this uses
        no inverted address and never touches pc_inv (which Fix-6 measured ~38 KB
        out at XModel depth on this map).

    A THIRD selector (2026-08-17) is consulted ONLY when those two leave >1
    candidate: OAT's own answer for this surface's vertsBlend alias handle,
    joined in ASSET-IDENTITY space (`load_index`). See _oat_dedup_owner.
    ⛔ It can only ever NARROW: zero candidates still return None, a unique
    candidate is returned without consulting OAT at all, and a tie that OAT
    cannot break uniquely still returns None and the caller raises exactly as
    before. The refusal is narrowed, never removed."""
    if DEDUP_SKIN_INDEX is None:
        return None
    # ---- DIVERGENCE GUARD ----------------------------------------------------
    # The index installed WITHOUT its companions means a caller assigned
    # DEDUP_SKIN_INDEX directly instead of going through
    # install_dedup_skin_state(). Returning None here would silently give that
    # caller the old two-selector behaviour -- a build and a census reporting
    # different numbers with neither one erroring. Fail loudly instead.
    # ⛔ The message must NOT contain the string 'rule (D)': ruleD_xmodel_census
    # classifies refusals by that substring, and a state bug counted as a
    # rule-(D) refusal is a state bug hiding inside the number it corrupts.
    if DEDUP_SKIN_OWNER is None:
        raise RuntimeError(
            'dedup-skin state is INCOMPLETE: DEDUP_SKIN_INDEX is installed but '
            'DEDUP_SKIN_OWNER is not. Both are set together by '
            'xmodel_convert.install_dedup_skin_state(pc, bodies, map_name); '
            'assigning DEDUP_SKIN_INDEX directly is no longer supported. '
            'Refusing to resolve, because the silent alternative is a census '
            'and a build disagreeing with neither of them raising.')
    vc, tc = _u16le(pc, o + 4), _u16le(pc, o + 6)
    vi = tuple(struct.unpack_from('<h', pc, o + 16 + j * 2)[0] for j in range(4))
    vb_a, v0_a = _u32le(pc, o + 24), _u32le(pc, o + 32)
    if not (0xA0000000 <= vb_a < 0xC0000000 and 0xA0000000 <= v0_a < 0xC0000000):
        return None                              # not the alias shape this path handles
    want = v0_a - vb_a
    hits = [s for s in DEDUP_SKIN_INDEX.get((vi, vc, tc), ())
            if 0 <= want - (s['v0'] - s['vb']) < DEDUP_SKIN_GAP_MAX]
    if len(hits) == 1:
        return hits[0]
    if len(hits) < 2:
        return None                              # nothing to narrow
    # ---- THIRD SELECTOR: OAT, on a >1 tie only -------------------------------
    owner = _oat_dedup_owner(vb_a)
    if owner is None:
        return None                              # table silent -> raise as today
    narrowed = [s for s in hits if DEDUP_SKIN_OWNER.get(s['model']) == owner]
    return narrowed[0] if len(narrowed) == 1 else None


def convert_xmodel_surfaces(pc, sb, ns, reloc=_default_reloc, marks=None,
                            co_base=0):
    """Convert the surfs[ns] header block + all per-surface dynamic data.
    `sb` = PC surfs-array offset. Returns (console_bytes, next_pc_off).
    Console layout: ns x 128-B header, then per surface: verts0(24*vc) verts1(8*vc)
    vertList(+trees) triIndices(6*tc)."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
    from latte_vertex import pc_vertex_to_console
    # pass 1: per-surface dynamic blobs (skinned: vertsBlend swap + generated
    # Latte skin streams; PC tensionData consumed, dropped). Skin counts feed
    # the header pass, so headers are emitted after the dynamic walk.
    dyn = []                           # per surface: (blob, skin_counts, rel_marks)
    c = [sb + ns * SURF_PC]
    for i in range(ns):
        o = sb + i * SURF_PC
        flags = _u16le(pc, o + 2)
        vc = _u16le(pc, o + 4)
        tc = _u16le(pc, o + 6)
        vlc = pc[o + 1]
        vi = [struct.unpack_from('<h', pc, o + 16 + j * 2)[0] for j in range(4)]
        blob = bytearray()
        skin = None
        rel = []
        vbw, tdw = _u32le(pc, o + 24), _u32le(pc, o + 28)
        if vbw not in PTRS and tdw not in PTRS and (vbw or tdw):
            # rule (D) DEDUP-SKINNED. Resolve to the source and emit ITS payloads
            # inline (SPEC_ruleD_inline_dedup_skinned.md). No resolution -> fall
            # through to convert_surface_header, which raises exactly as before.
            ded = _resolve_dedup_skin(pc, o)
            if ded is not None:
                nvb = vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]
                for k in range(nvb):                  # vertsBlend: byte-swap
                    blob += _sw16(pc, ded['vb'] + k * 2)
                streams, skin = _latte_skin_streams(pc, ded['vb'], vi)
                blob += streams
                if not (flags & 1):                   # verts0 + console verts1
                    v0blk = bytearray(); v1blk = bytearray()
                    for v in range(vc):
                        a, b = pc_vertex_to_console(pc, ded['v0'] + v * 32)
                        v0blk += a; v1blk += b
                    blob += v0blk; blob += v1blk
                for t in range(tc * 3):               # triIndices
                    blob += _sw16(pc, ded['ti'] + t * 2)
                # ⛔ c[0] NOT advanced (this surface owns no PC bytes here) and
                # `rel` left EMPTY (the PC span belongs to the source surface).
                dyn.append((blob, skin, rel, True))
                continue
        if _u32le(pc, o + 24) in PTRS:                # vertsBlend (u16s), pre-verts0
            vb_off = c[0]
            nvb = vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]
            c[0] += nvb * 2
            for k in range(nvb):                      # console vertsBlend = byte-swap
                blob += _sw16(pc, vb_off + k * 2)
            streams, skin = _latte_skin_streams(pc, vb_off, vi)
            blob += streams
        if _u32le(pc, o + 28) in PTRS:                # tensionData (f32s): consumed, dropped
            c[0] += (vi[0] + vi[1] + vi[2] + vi[3]) * 4
        vsrc = None
        if not (flags & 1) and _u32le(pc, o + 32) in PTRS:
            vsrc = c[0]
            c[0] += vc * 32
            v0blk = bytearray(); v1blk = bytearray()
            for v in range(vc):
                a, b = pc_vertex_to_console(pc, vsrc + v * 32)
                v0blk += a; v1blk += b
            if marks is not None:      # verts0: element-scaled (PC 32 -> co 24)
                rel.append(('scaled', vsrc, len(blob), vc, 32, 24))
            blob += v0blk; blob += v1blk
        if _u32le(pc, o + 40) in PTRS:
            # console REBUILDS collision trees from geometry (A3): locate the
            # PC triIndices (follows the vertList region) for the rebuild
            tree_src = None
            if REBUILD_COLLISION_TREES and vsrc is not None \
                    and _u32le(pc, o + 12) in PTRS:
                tree_src = (vsrc, c[0] + _vertlist_extent(pc, c[0], vlc))
            blob += _convert_vertlist(pc, c, vlc, reloc, tree_src=tree_src)
        if _u32le(pc, o + 12) in PTRS:
            tsrc = c[0]
            c[0] += tc * 6
            if marks is not None:      # triIndices: same size both sides
                rel.append(('lin', tsrc, len(blob), tc * 6))
            for t in range(tc * 3):
                blob += _sw16(pc, tsrc + t * 2)
        dyn.append((blob, skin, rel, False))
    # pass 2: headers (with skin counts), then the dynamic blobs
    out = bytearray()
    for i in range(ns):
        out += convert_surface_header(pc, sb + i * SURF_PC, reloc,
                                      skin=dyn[i][1], force_rigid=True,
                                      inline_dedup=dyn[i][3])
    for (blob, _, rel, _ded) in dyn:
        if marks is not None:
            for m in rel:
                marks.append((m[0], m[1], co_base + len(out) + m[2]) + m[3:])
        out += blob
    return bytes(out), c[0]


def convert_xmodel_materialhandles(pc, base, ns, reloc=_default_reloc,
                                   co_base=None, owner=None):
    """materialHandles: ns pointer words (relocated); FOLLOW -> inline console Material
    (via material_convert.convert_material). Returns (console_bytes, next_pc_off).

    I1: when MHARR_BINDER is installed, a dedup back-ref cell is bound to the
    holder cell it NAMES instead of being relocated through the address chain.
    `co_base` is this cell array's offset within the console body being emitted
    — the body the binder was given via `begin_asset`, which for a NESTED model is
    the enclosing asset, not the model (convert_xmodel adds the model's own offset)
    — and `owner` the PC file offset of the owning XModel; together with the
    assembler's per-asset console origin they give the binder OUR address for
    every holder cell. Threaded exactly as `marks`/`co_base` already are for
    convert_xmodel_surfaces. The binder carries `.encode` = Omap._encode
    (our-stream offset -> runtime handle), the SAME chokepoint every other
    minted pointer goes through; the binder never encodes anything itself.
    A REFUSAL falls through to `_ptr(v, reloc)` — today's behavior, verbatim."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import material_convert as MC
    out = bytearray()
    words = [_u32le(pc, base + i * 4) for i in range(ns)]
    bind = MHARR_BINDER
    if bind is not None:
        bind.observe(owner, base, words, co_base)
    for i in range(ns):
        v = words[i]
        w = None
        if bind is not None and bind.encode is not None and v not in PTRS:
            co = bind.bind_cell(base + i * 4, v)
            if co is not None:
                w = bind.encode(co)
        out += struct.pack('>I', w) if w is not None else _ptr(v, reloc)
    c = base + ns * 4
    for i in range(ns):
        if _u32le(pc, base + i * 4) in PTRS:
            # A1: enable the XModel-inline image source for this inline material so
            # inline-pixel images (skybox_<map>) emit inline instead of the 1x1 stub.
            # Scoped so the GfxWorld materialMemory path never sees it.
            _prev = MC.XMODEL_INLINE_ACTIVE
            MC.XMODEL_INLINE_ACTIVE = True
            try:
                mb, c = MC.convert_material(pc, c, reloc)
            finally:
                MC.XMODEL_INLINE_ACTIVE = _prev
            out += mb
    return bytes(out), c


# ------------------------------------------------------ post-surface tail
# Stream order after materialHandles: collSurfs -> boneInfo -> himipInvSqRadii -> physPreset
# (-> collmaps -> physConstraints, handled in the full driver).
# collSurfs: PC XModelCollSurf_s = 44 (+ inline collTris[numCollTris] x 48) -> console = 36 and
#   the collTris are DROPPED.  Field map (empirically pinned vs genuine common_mp):
#     PC44 = { collTris* @0, numCollTris @4, Bounds bounds @8 (mins3+maxs3 f32, 24 B),
#              int @32, int @36, int @40 }.
#   console36 = PC[+8 .. +43] byte-swapped as 9 words (bounds + the 3 trailing ints); the LEADING
#   {collTris*, numCollTris} pair is removed (console reads collTris out-of-line / not at all).
COLLSURF_PC = 44
COLLSURF_CO = 36
COLLTRI = 48
XBONEINFO = 44
PHYSPRESET = 84


def convert_xmodel_collsurfs(pc, base, ncoll, reloc=_default_reloc):
    """PC collSurfs (44 B each + collTris) -> console collSurfs (36 B each, collTris dropped)."""
    out = bytearray()
    c = base + COLLSURF_PC * ncoll
    for i in range(ncoll):
        cs = base + i * COLLSURF_PC
        # console36 = PC[+8..+43] swapped as 9 words (bounds f32 x6 + 3 ints); the leading
        # {collTris* @0, numCollTris @4} pair is dropped.
        for w in range(9):
            out += _sw32(pc, cs + 8 + w * 4)
    for i in range(ncoll):
        cs = base + i * COLLSURF_PC
        if _u32le(pc, cs + 0) in PTRS:                  # collTris* present -> consume+drop
            c += _u32le(pc, cs + 4) * COLLTRI
    return bytes(out), c


def convert_xmodel_collmaps(pc, base, ncoll, reloc=_default_reloc):
    """collmaps chain: ncoll x Collmap(4) then per-collmap followers, mirroring the loader
    (OAT Load_Collmap -> PhysGeomList(12) -> count x PhysGeomInfo(68) -> per-geom BrushWrapper(96)
    -> sides(12*n) / verts(12*n) / planes(20*n)). Every struct is identical-layout PC<->console
    (no SwapEndianness branches in the OAT fills) => structural byte-swap + ptr relocation.
    Stream order per LoadArray: full array first, THEN each element's followers in order.
    cplane_s last word (type/signbits/pad) copied verbatim, as in the clipmap cplane rule."""
    out = bytearray()
    cm = base                                             # Collmap array: ncoll x {geomList*}
    for i in range(ncoll):
        out += _ptr(_u32le(pc, cm + i * 4), reloc)
    cur = cm + ncoll * 4
    for i in range(ncoll):
        if _u32le(pc, cm + i * 4) not in PTRS:
            continue
        gl = cur                                          # PhysGeomList(12): count, geoms*, contents
        count = _u32le(pc, gl)
        out += _sw32(pc, gl)
        out += _ptr(_u32le(pc, gl + 4), reloc)
        out += _sw32(pc, gl + 8)
        cur = gl + 12
        if _u32le(pc, gl + 4) not in PTRS:
            continue
        ga = cur                                          # count x PhysGeomInfo(68)
        for g in range(count):
            go = ga + g * 68
            out += _ptr(_u32le(pc, go), reloc)            # brush*
            for w in range(1, 17):                        # type, orientation[3][3], offset, halfLengths
                out += _sw32(pc, go + w * 4)
        cur = ga + count * 68
        for g in range(count):                            # per-geom followers
            if _u32le(pc, ga + g * 68) not in PTRS:
                continue
            bw = cur                                      # BrushWrapper(96)
            numsides = _u32le(pc, bw + 28)
            numverts = _u32le(pc, bw + 84)
            for w in range(8):                            # mins, contents, maxs, numsides
                out += _sw32(pc, bw + w * 4)
            out += _ptr(_u32le(pc, bw + 32), reloc)       # sides*
            for w in range(9, 21):                        # axial_cflags[2][3] + axial_sflags[2][3]
                out += _sw32(pc, bw + w * 4)
            out += _sw32(pc, bw + 84)                     # numverts
            out += _ptr(_u32le(pc, bw + 88), reloc)       # verts*
            out += _ptr(_u32le(pc, bw + 92), reloc)       # planes*
            cur = bw + 96
            if _u32le(pc, bw + 32) in PTRS:               # sides: numsides x cbrushside_t(12)
                sb = cur
                for s in range(numsides):
                    so = sb + s * 12
                    out += _ptr(_u32le(pc, so), reloc)    # plane*
                    out += _sw32(pc, so + 4)              # cflags
                    out += _sw32(pc, so + 8)              # sflags
                cur = sb + numsides * 12
                for s in range(numsides):                 # per-side follower: inline cplane_s(20)
                    if _u32le(pc, sb + s * 12) in PTRS:   # when plane* is FOLLOW
                        for w in range(4):
                            out += _sw32(pc, cur + w * 4)
                        out += pc[cur + 16:cur + 20]      # type/signbits/pad verbatim
                        cur += 20
            if _u32le(pc, bw + 88) in PTRS:               # verts: numverts x vec3(12)
                for w in range(numverts * 3):
                    out += _sw32(pc, cur + w * 4)
                cur += numverts * 12
            if _u32le(pc, bw + 92) in PTRS:               # planes: numsides x cplane_s(20)
                for s in range(numsides):
                    po = cur + s * 20
                    for w in range(4):                    # normal xyz + dist
                        out += _sw32(pc, po + w * 4)
                    out += pc[po + 16:po + 20]            # type/signbits/pad verbatim
                cur += numsides * 20
    return bytes(out), cur


def convert_xmodel_boneinfo(pc, base, nb):
    """boneInfo: nb x XBoneInfo(44). Same size both platforms; byte-swap the 11 words each."""
    out = bytearray()
    for i in range(nb):
        for w in range(11):
            out += _sw32(pc, base + i * XBONEINFO + w * 4)
    return bytes(out), base + XBONEINFO * nb


def convert_xmodel_physpreset(pc, base, reloc=_default_reloc):
    """physPreset: inline PhysPreset(84) + name + sndAliasPrefix strings.
    Field-swap the 21 words; name(@0)/sndAlias(@28) pointers relocated, strings copied."""
    out = bytearray()
    for w in range(PHYSPRESET // 4):
        o = base + w * 4
        if w * 4 in (0, 28):
            out += _ptr(_u32le(pc, o), reloc)
        else:
            out += _sw32(pc, o)
    c = base + PHYSPRESET
    for so in (0, 28):
        if _u32le(pc, base + so) in PTRS:
            end = pc.index(0, c)
            out += pc[c:end + 1]
            c = end + 1
    return bytes(out), c


def convert_xmodel_body(pc, off, reloc=_default_reloc, memusage=None, himip=FOLLOW):
    """PC XModel body @off -> console 244 B body. `memusage` (u32) and `himip` (ptr word) are the
    two non-PC-derivable fields; default himip=FOLLOW (console generates the inline array)."""
    def ptr(o):
        return struct.pack('>I', reloc(struct.unpack_from('<I', pc, o)[0]))
    out = bytearray()
    out += ptr(off + 0)                    # name
    out += pc[off + 4: off + 8]            # numBones, numRootBones, numsurfs, lodRampType
    for o in (8, 12, 16, 20, 24, 28, 32, 36):
        out += ptr(off + o)                # 8 pointer members
    for i in range(4):
        out += _lodinfo(pc, off + 40 + i * 28)      # lodInfo[4] @40..151
    out += ptr(off + 152)                  # collSurfs
    out += _sw32(pc, off + 156)            # numCollSurfs
    out += _sw32(pc, off + 160)            # contents
    out += ptr(off + 164)                  # boneInfo
    out += _sw32(pc, off + 168)            # radius
    for k in range(3):
        out += _sw32(pc, off + 172 + k * 4)   # mins vec3
    for k in range(3):
        out += _sw32(pc, off + 184 + k * 4)   # maxs vec3
    out += _sw16(pc, off + 196)            # numLods
    out += _sw16(pc, off + 198)            # collLod
    # himipInvSqRadii: genuine emits FOLLOW iff numsurfs>0, NULL when the model has no surfaces
    # (raid oracle 440/440: ns>0 -> ffffffff, ns==0 -> 00000000).
    if pc[off + 6] == 0:
        himip = 0
    out += struct.pack('>I', himip)        # himipInvSqRadii (console-generated; default FOLLOW)
    mu = struct.unpack_from('<I', pc, off + 204)[0] if memusage is None else memusage
    out += struct.pack('>I', mu)           # memUsage (console-computed; caller supplies)
    out += _sw32(pc, off + 208)            # flags
    # PC `bool bad` @212 (+3 pad) dropped; tail shifts -4
    out += ptr(off + 216)                  # physPreset
    out += pc[off + 220: off + 221]        # numCollmaps u8
    out += b'\x00' * 3                      # pad
    out += ptr(off + 224)                  # collmaps
    out += ptr(off + 228)                  # physConstraints
    # lightingOriginOffset vec3 + lightingOriginRange: copied VERBATIM (NOT byte-swapped) —
    # a linker quirk, 465/0 across the matched-pair oracle (cf. Material `contents`).
    out += pc[off + 232: off + 248]
    assert len(out) == CO_BODY, len(out)
    return bytes(out)


def convert_xmodel(pc, off, reloc=_default_reloc, memusage=None, marks=None,
                   co_base=0):
    """Full XModel PC->console driver: body -> bonedata -> surfaces -> materialHandles ->
    collSurfs -> boneInfo -> himipInvSqRadii -> physPreset -> (collmaps/physConstraints raise).
    Returns (console_bytes, next_pc_off).  Skinned surfaces raise NotImplementedError.

    `co_base` is THIS MODEL'S byte offset within the console body of the ASSET the
    assembler is emitting -- the body the I1 binder was handed via
    `MharrBinder.begin_asset(co_cursor)`.  0 for a top-level XMODEL asset, where the
    model IS the body; NON-ZERO for a model inlined inside a larger asset (today only
    XModels nested in a WEAPON body, see weapon_convert._recurse).  It is added to
    every downstream `co_base` so the offsets handed to the binder and to `marks` are
    always relative to the SAME origin the assembler staged, never to the start of a
    nested sub-body.

    Omitting it was a real defect, not a cosmetic one: the binder staged 45 holder +
    94 alias cells short by the nested model's own offset on zm_nuked (probe PG3), so
    at co+assets_end they landed on float/index data (0x3F800000, 0x01870185 ...),
    several at non-4-aligned offsets. mp_raid has no mharr-bearing nested models, so
    no raid-only calibration could ever surface it -- the same shape as the thermal
    sub-material trap.

    Lossy/derived regions (self-consistent but not byte-identical to genuine, documented per
    section): verts0 normal/tangent, collision-tree node counts, boneInfo per-bone recomputed
    bounds, and inline-material image pixels (image-conversion track).  himipInvSqRadii is a
    console-generated numsurfs*f32 array (emitted; values synthesised as 0.0 when PC has none)."""
    nb, nrb, ns = pc[off + 4], pc[off + 5], pc[off + 6]
    ncoll = _u32le(pc, off + 156)
    body = convert_xmodel_body(pc, off, reloc, memusage=memusage, himip=FOLLOW)
    bones, cur = convert_xmodel_bonedata(pc, off)
    out = bytearray(body)
    # FIX B (skybox boot-53 class): an ALIASED model name whose payload drifts
    # registers the model under a garbage name -> DB substitutes a name-stamped
    # DEFAULT model (skybox -> mc/global_black). When the assembler installs
    # INLINE_NAME_RESOLVER, re-emit aliased names INLINE (FOLLOW + string) at
    # the name's sequence point (before bone data — name is field 0).
    nmw = _u32le(pc, off + 0)
    if (INLINE_NAME_RESOLVER is not None and nmw not in PTRS
            and 0xA0000001 <= nmw <= 0xBFFFFFFF):
        nm = INLINE_NAME_RESOLVER(nmw)
        if nm:
            out[0:4] = b'\xff\xff\xff\xff'
            out += nm + b'\x00'
    out += bones
    # surfaces
    if _u32le(pc, off + 32) in PTRS:
        surf, cur = convert_xmodel_surfaces(pc, cur, ns, reloc, marks=marks,
                                            co_base=co_base + len(out))
        out += surf
    # materialHandles
    if _u32le(pc, off + 36) in PTRS:
        mh, cur = convert_xmodel_materialhandles(pc, cur, ns, reloc,
                                                 co_base=co_base + len(out),
                                                 owner=off)
        out += mh
    # collSurfs
    if _u32le(pc, off + 152) in PTRS:
        cs, cur = convert_xmodel_collsurfs(pc, cur, ncoll, reloc)
        out += cs
    # boneInfo
    if _u32le(pc, off + 164) in PTRS:
        bi, cur = convert_xmodel_boneinfo(pc, cur, nb)
        out += bi
    # himipInvSqRadii: console-generated numsurfs f32 (body ptr forced FOLLOW above)
    if _u32le(pc, off + 200) in PTRS:
        cur += 4 * ns                              # consume PC copy if present
        out += b'\x00\x00\x00\x00' * ns            # synthesise (LOD himip radii)
    else:
        out += b'\x00\x00\x00\x00' * ns
    # physPreset
    if _u32le(pc, off + 216) in PTRS:
        pp, cur = convert_xmodel_physpreset(pc, cur, reloc)
        out += pp
    if _u32le(pc, off + 224) in PTRS and pc[off + 220]:
        cmb, cur = convert_xmodel_collmaps(pc, cur, pc[off + 220], reloc)
        out += cmb
    if _u32le(pc, off + 228) in PTRS:
        raise NotImplementedError('inline physConstraints not built')
    return bytes(out), cur
