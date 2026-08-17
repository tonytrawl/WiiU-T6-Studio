#!/usr/bin/env python3
"""
GfxWorld DYNAMICS converter (PC LE -> WiiU console BE), region-by-region.

Region conversion methods (VALIDATED against the genuine mp_raid oracle where marked):
each region is walked on both platforms via gfxworld_probe2 (which knows both layouts),
paired by label, and converted per REGION_SPEC below.

METHODS:
  swap4 / swap2   : endian-reverse fixed-size elements (validated byte-exact vs oracle)
  fields          : per-struct field-aware swap (swap 4-byte scalars, keep byte fields,
                    relocate pointers via omap)
  reorder_pc      : depends on the console's per-surface vertex/surface REORDER; for a
                    WORKING map we keep PC order, so emit the PC data (swapped) as-is and
                    the referencing offsets/indices stay self-consistent
  reencode        : console stores a DIFFERENT encoding (baked lighting) -> real re-encode
                    (hard) or reuse-genuine(Raid)/flat-default(DLC first render)
  console_gx2     : GX2-format blob (textures/world verts) -> gx2_texture / latte world-vert
  reuse / gen     : console-specific / console-only -> reuse genuine (Raid) or generate (DLC)

VALIDATION STATUS (mp_raid, byte-exact vs oracle):
  BYTE-EXACT: shadowMapVol, smVolPlanes, exposureVol, expVolPlanes, fogVol, fogVolPlanes,
    dpvsPlanes.planes(cplane 20B: swap4 x4 words, keep type/signbits/pad bytes),
    dpvsPlanes.nodes(uint16 swap2), models(64B swap4), lightRegion, shadowGeom(uint16),
    lightGrid.rowDataStart(uint16).
  FIELD-AWARE (structs known, small byte/ptr residue): GfxStaticModelInst(36B all-float swap4;
    ~0.5% residue = reorder), GfxSurface(80B: srfTriangles + material PTR + index bytes + bounds).
  REORDER_PC (keep PC order for working map): dpvs.sortedSurfIndex, draw.indices, draw.vd*.
  REENCODE (HARD): lightGrid.entries(4B, 100% diff), lightGrid.coeffs(2.8MB, ~100% diff) = baked
    ambient lighting; reuse-genuine(Raid) or flat-default(DLC first render).
  REUSE/GEN (console-specific): sunLight, cells(+extra), draw.reflectionProbes/lightmaps(GX2 tex),
    draw.vd0(36B world verts GX2), materialMemory, outdoorImage(GX2), dpvs.smodelDrawInsts,
    streamInfo.aabbTrees/leafRefs, tail material(GX2), occluders.
"""
import struct

# (region-label-key) -> (method, params)
REGION_SPEC = {
    'shadowMapVol':          ('swap4', {}),
    'smVolPlanes':           ('swap4', {}),
    'exposureVol':           ('swap4', {}),
    'expVolPlanes':          ('swap4', {}),
    'fogVol':                ('swap4', {}),
    'fogVolPlanes':          ('swap4', {}),
    'dpvsPlanes.planes':     ('fields', {'stride': 20, 'swap_words': 4}),  # cplane: keep last word bytes
    'dpvsPlanes.nodes':      ('swap2', {}),
    'models':                ('swap4', {}),
    'lightRegion':           ('swap4', {}),
    'shadowGeom':            ('swap2', {}),
    'lightGrid.rowDataStart':('swap2', {}),
    'dpvs.smodelInsts':      ('swap4', {}),                                # GfxStaticModelInst all-float
    'dpvs.surfaces':         ('surface', {'stride': 80}),                  # field-aware + material ptr
    'dpvs.sortedSurfIndex':  ('reorder_pc', {}),
    'draw.indices':          ('swap2', {}),        # u16 index buffer: endian-swap, keep PC order
    'draw.vd.data':          ('world_vertex', {}), # 1st span = vd0 36B world verts (conv_world_vertex);
                                                    # 2nd span = vd1 (stride 4 = 2x u16, swap2) — the
                                                    # assembler routes the 2nd occurrence to swap2
    'lightGrid.entries':     ('entry4', {}),   # GfxLightGridEntry: u16 colorsIndex swap + 2 idx bytes
    'lightGrid.coeffs':      ('swap2', {}),     # 2.8MB baked SH lighting = uint16 endian-swap (VALIDATED)
    'lightGrid.rawRowData':  ('fields', {}),
    'sunLight':              ('reuse', {}),
    'cells':                 ('reuse', {}),
    'draw.reflectionProbes': ('console_gx2', {}),
    'draw.lightmaps':        ('console_gx2', {}),
    'materialMemory':        ('reuse', {}),
    'outdoorImage inline':   ('console_gx2', {}),
    'dpvs.smodelDrawInsts':  ('reuse', {}),
    'streamInfo.aabbTrees':  ('gen', {}),
    'streamInfo.leafRefs':   ('gen', {}),
    'tail material inline':  ('console_gx2', {}),
    'occluders':             ('gen', {}),
    'dpvs.smodelCastsShadow':('fields', {}),
}


def swap_n(pc_bytes, n):
    """Endian-reverse each n-byte element. Byte-exact for uniform-size scalar arrays."""
    out = bytearray(len(pc_bytes))
    for i in range(0, len(pc_bytes) - n + 1, n):
        out[i:i + n] = pc_bytes[i:i + n][::-1]
    return bytes(out)


def swap_fields(pc_bytes, stride, swap_words):
    """Per-element: endian-reverse the first `swap_words` 4-byte words, copy the rest
    (byte fields) verbatim. Used for structs mixing floats/ints with byte fields."""
    out = bytearray(pc_bytes)
    for i in range(0, len(pc_bytes) - stride + 1, stride):
        for w in range(swap_words):
            out[i + w * 4:i + w * 4 + 4] = pc_bytes[i + w * 4:i + w * 4 + 4][::-1]
    return bytes(out)


def conv_entries(pc_bytes):
    """GfxLightGridEntry (4B): u16 colorsIndex (swap) + 2 index bytes (keep). VALIDATED."""
    out = bytearray(pc_bytes)
    for i in range(0, len(pc_bytes) - 3, 4):
        out[i:i + 2] = pc_bytes[i:i + 2][::-1]
    return bytes(out)


_FOLLOW = 0xFFFFFFFF
_INSERT = 0xFFFFFFFE
_PTR_SENTINELS = (_FOLLOW, _INSERT, 0)


def _identity_reloc(v):
    return v


def conv_surface(pc_bytes, stride=80, reloc=None):
    """GfxSurface (80B) PC LE -> console BE, field-aware. Layout (T6, from OAT write_db):
      srfTriangles_t @0..48:
        +0..12 mins vec3 (swap4), +12 vertexDataOffset0 (swap4),
        +16..28 maxs vec3 (swap4), +28 vertexDataOffset1 (swap4),
        +32 firstVertex (swap4), +36 himipRadiusInvSq (swap4),
        +40 vertexCount u16 (swap2), +42 triCount u16 (swap2), +44 baseIndex (swap4)
      +48 material* (POINTER: relocated via `reloc`, see below)
      +52 lightmapIndex, +53 reflectionProbeIndex, +54 primaryLightIndex, +55 flags (keep bytes)
      +56..80 bounds[2] vec3 (swap4)
    All geometry-relevant scalars (vertexDataOffset0/firstVertex/vertexCount/triCount/baseIndex)
    are preserved in PC order -> self-consistent with PC vd0/indices.

    `reloc(pc_ptr_value) -> console_ptr_value` maps the PC material alias to the console alias
    (the zone-writer omap) at integration time; FOLLOW/INSERT/null are preserved verbatim. The
    default identity reloc is only for the self-contained round-trip / oracle test (there the
    material word is the expected per-surface divergence). For a REAL map a proper reloc is
    MANDATORY — an unrelocated PC alias points at nothing on console and crashes the loader.
    """
    if reloc is None:
        reloc = _identity_reloc
    out = bytearray(pc_bytes)
    n = len(pc_bytes)
    SW4 = (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 44, 56, 60, 64, 68, 72, 76)  # material@48 handled separately
    SW2 = (40, 42)
    for b in range(0, n - stride + 1, stride):
        for o in SW4:
            out[b + o:b + o + 4] = pc_bytes[b + o:b + o + 4][::-1]
        for o in SW2:
            out[b + o:b + o + 2] = pc_bytes[b + o:b + o + 2][::-1]
        # material* @48: PC LE u32 -> reloc -> console BE u32 (sentinels preserved verbatim)
        pc_ptr = struct.unpack_from('<I', pc_bytes, b + 48)[0]
        con_ptr = pc_ptr if pc_ptr in _PTR_SENTINELS else reloc(pc_ptr)
        struct.pack_into('>I', out, b + 48, con_ptr)
        # bytes +52..56 (lightmapIndex/reflectionProbeIndex/primaryLightIndex/flags) kept verbatim
    return bytes(out)


WORLD_VERT_STRIDE = 36


def conv_world_vertex_grouped(vd0_bytes, groups):
    """Group-aware vd0 conversion. vd0 is NOT a flat 36B array: each surface vertex GROUP is laid
    out as vertexCount*36 bytes then PADDED to a 16-byte boundary. A flat 36-stride pass drifts out
    of alignment after the first padded group and corrupts every later group (this was the cause of
    the warped-geometry hardware result). Instead convert each group's real vertices in place and
    leave the inter-group padding verbatim.

    groups: iterable of (byte_offset, vertex_count) — byte_offset relative to vd0 start, from the
    surface table's vertexDataOffset0; vertex_count = (max index in the group)+1 (0-relative indices).
    Verified group-aware -> 32/36 byte-exact vs the genuine oracle (only the 4 tangent bytes differ,
    a cosmetic packed repack).
    """
    out = bytearray(vd0_bytes)          # padding + any unreferenced bytes kept verbatim
    n = len(vd0_bytes)
    for off, vc in groups:
        block = off + vc * WORLD_VERT_STRIDE
        if block > n:
            vc = max(0, (n - off) // WORLD_VERT_STRIDE)
        conv = conv_world_vertex(bytes(vd0_bytes[off:off + vc * WORLD_VERT_STRIDE]))
        out[off:off + len(conv)] = conv
    return bytes(out)


def conv_world_vertex(pc_bytes):
    """GfxWorld 36-byte world vertex -> console. VALIDATED 32/36 bytes byte-exact:
      +0..12  position 3xf32   -> swap4
      +12     w/const f32      -> swap4
      +16     color RGBA8      -> byte-copy (no swap)
      +20     normal 2x u16    -> swap2
      +24..32 uv 2x f32        -> swap4
      +32     tangent          -> cross-lane 1-bit rotate (lighting_repack.conv_tangent,
                                  POLISH 2026-07-10: raid 162752/162752 byte-exact; the old
                                  swap2 was wrong on ~99.3% of verts = the "renders darker"
                                  root cause)
    """
    import lighting_repack as LR
    n = len(pc_bytes)
    out = bytearray(pc_bytes)
    for b in range(0, n - WORLD_VERT_STRIDE + 1, WORLD_VERT_STRIDE):
        for o in (0, 4, 8, 12, 24, 28):            # 4-byte float fields
            out[b + o:b + o + 4] = pc_bytes[b + o:b + o + 4][::-1]
        # color +16..20 kept verbatim
        for o in (20, 22):                         # normal 2x u16
            out[b + o:b + o + 2] = pc_bytes[b + o:b + o + 2][::-1]
        out[b + 32:b + 36] = LR.conv_tangent(pc_bytes[b + 32:b + 36])
    return bytes(out)

