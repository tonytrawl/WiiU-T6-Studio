#!/usr/bin/env python3
"""
Material PC(v147, LE) -> console(WiiU v148, BE) body converter  (HANDOFF Track A).

Material is NOT a clean byte-swap: the console struct is 104 B, the PC struct 112 B.
The console layout drops the fields that only exist for the D3D11 runtime:

  divergence (verified against genuine common_mp via a matched-pair oracle — 437/446 shared
  materials convert byte-exact; see validate_material.py):
    * MaterialInfo   48 -> 40 B   : drops `surfaceFlags` (u32 @ PC off 36) + trailing pad;
                                    `contents` (PC @40) moves to console @36 and is copied
                                    VERBATIM (the linker does NOT byte-swap it). drawSurf IS kept
                                    and is ALSO VERBATIM (352/352 raid GfxWorld inline-material
                                    oracle, 2026-07-10). All other scalars byte-swap.
    * stateBitsEntry char[36] -> char[32] : mirrors MaterialTechniqueSet.techniques 36 -> 32
                                    (per-technique state-bits index; console technique enum is a
                                    32-slot reordered subset of PC's 36 — a raw truncation, adequate
                                    for shared materials; full slot remap is shared with Track B).
    * GfxStateBits   20 -> 8 B    : keeps loadBits[2]; drops the 3 D3D state-object pointers
                                    (blendState/depthStencilState/rasterizerState, ZoneCode
                                    condition = never).
    * Material total 112 -> 104 B : counts move 84 -> 72, the 5 pointers 92 -> 80.
  KNOWN GAP: 9/446 "mc/mtl_*" model materials with a non-zero hashIndex/surfaceFlags packing
  diverge in the 2 bytes @ console off 34 — not yet reversed (low impact; hashIndex is a sort hash).

Trailing dynamic data (console stream order, after the 104 B body):
    info.name c-string, then textureTable[textureCount] (MaterialTextureDef, 16 B, image ptr @12),
    constantTable[constantCount] (MaterialConstantDef, 32 B, nameHash + name[12] + vec4 literal),
    stateBitsTable[stateBitsCount] (GfxStateBits -> 8 B loadBits), then (if thermalMaterial FOLLOWs)
    an inline thermal Material.  techniqueSet / thermalMaterial / textureDef.image are asset refs
    (alias or FOLLOW-to-inline); pointer values are remapped through `reloc`.

`reloc(pc_ptr_value) -> console_ptr_value` handles the alias relocation (omap) at integration
time; FOLLOW/INSERT/null are always preserved verbatim.  The default identity reloc is only for
the self-contained round-trip test.
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

PC_MAT_SIZE = 112
CO_MAT_SIZE = 104

# Assemble-installed resolver for INLINE techsets inside materials:
# callable(pc_zone_bytes, pc_inline_techset_off) -> console techset blob bytes
# (Track B substitute) or None. When set, convert_material EMITS the blob in
# place of the PC DXBC techset (console loadability requirement).
INLINE_TECHSET_HOOK = None
PC_SB_SIZE = 20          # GfxStateBits on PC
CO_SB_SIZE = 8           # GfxStateBits on console (loadBits[2] only)
TEXDEF_SIZE = 16
CONSTDEF_SIZE = 32


def _default_reloc(v):
    return v


PC_IMG_BODY = 64
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'wiiu_ref'))
import ipak as _IP

# Optional resolver for STREAMED images: callable(name_hash) -> iwi dict
# (gx2_format/width/height/mips/blob, as from ipak.PcImageSource) or None. When set,
# streamed image bodies are built via ipak_stream.build_streamed_body (real dims,
# GX2 format, ipak part-hash table) instead of the metadata-less stub. Required for
# the image to actually stream on console (hw-confirmed 2026-07-09: stub body =>
# checkerboard, the engine never opens the ipak).
IMAGE_SOURCE = None

# A1-scoped resolver for XMODEL-INLINE-material images ONLY (skybox_<map> and the
# other inline-pixel droppers). Kept SEPARATE from the global IMAGE_SOURCE, which
# corrupts the GfxWorld materialMemory path (16,734 unres:GfxWorld when set globally).
# Consulted by convert_image ONLY while XMODEL_INLINE_ACTIVE is set (parse_xmodel_pc
# brackets its convert_material call). callable(name_hash)->iwi dict|None over the PC
# SOURCE ipaks. When it resolves, the image is emitted INLINE (build_inline_body,
# streaming=0) to match genuine (genuine ships skybox_mp_raid inline, 512x512, 1.5MB).
XMODEL_IMAGE_SOURCE = None
XMODEL_INLINE_ACTIVE = False

# materialMemory-scoped resident-image resolver (emitter #3 — the GfxWorld materialMemory
# inline-material path, gfxworld_regions.conv_material_memory). Same shape/contract as
# XMODEL_IMAGE_SOURCE (callable(name_hash)->iwi|None over the PC source ipaks), consulted by
# convert_image ONLY while MATMEM_INLINE_ACTIVE is set (conv_material_memory brackets its
# convert_material calls). This is the path that NEVER got resident handling: with IMAGE_SOURCE
# None it fell to the 1x1 stub -> metadata-less body -> a lit world-surface material's colorMap
# base-diffuse sampler resolves to a null image -> the skate material NULL+0x10 deref (dump 40004).
# Kept SEPARATE from the global IMAGE_SOURCE, whose raw blobs corrupt this path (16,734
# unres:GfxWorld). Resident (absent from streaming ipak) -> inline pixels; else -> streamed body.
MATMEM_IMAGE_SOURCE = None
MATMEM_INLINE_ACTIVE = False
# matmem-scoped resident override (2026-07-13): calibrates the inline subset to the genuine
# shape (small resident set inline, rest streamed) for maps whose OWN authored ipak makes
# images streamable that the console-universe test calls resident (e.g. skate DLC colorMaps
# present in the deployed mp_skate.ipak). Falls back to RESIDENT_IMAGE_TEST when None.
# MATMEM-LOCAL by design -- do not use for the XModel/standalone scopes (Track A calibration).
MATMEM_RESIDENT_TEST = None
# TRUE full mip cap for matmem STREAMED images (2026-07-13 pool-budget test): drop
# top mips so max(dim) <= cap before build_streamed_body. None = no cap. (The earlier
# varC/varD "caps" left real 512px+ headers on most images — invalid test.)
MATMEM_MIP_CAP = None

# A1 discriminator (2026-07-12): genuine ships an XModel-inline-material image INLINE
# (resident, streaming=0) iff it is NOT present in the map's streaming ipak; otherwise it
# STREAMS it. Type/semantic is NOT a clean signal (same combos appear in both sets) —
# IPAK MEMBERSHIP is. Set by produce_container to callable(name_hash)->bool (True == resident
# == inline). When None, defaults to resident=True (legacy over-inline; clears the null but
# balloons the zone). Consulted ONLY inside the XMODEL_INLINE_ACTIVE branch.
RESIDENT_IMAGE_TEST = None

# Track C (FxEffectDef) scoped inline-material handling. fx_convert brackets its
# convert_material calls with FX_INLINE_ACTIVE; while set, convert_image emits the
# three genuine FX image classes via _convert_image_fx (zeroed stub / streamed /
# inline full-mip pixels with channel compression) — raid FX oracle, 2026-07-12.
# (The FX stateBitsTable compaction is the same universal referenced-rows rule
# convert_material already applies; 157/157 on the FX oracle.)
# FX_IMAGE_SOURCE: callable(name_hash)->iwi|None over the PC source ipaks (same
# shape as XMODEL_IMAGE_SOURCE); set by produce_container.author_zone.
FX_INLINE_ACTIVE = False
FX_IMAGE_SOURCE = None

# Whole-body overlay for TOP-LEVEL Material assets (raid control): dict {material_name: bytes}.
# Some standalone materials ship a big RESIDENT inline image (e.g. compass_map_<map>, the minimap
# drawn every frame: genuine 262620B incl. a 262144B inline texture). convert_image emits those as
# streamed stubs (the A1 inline path is XMODEL-scoped), so the engine loops trying to stream the
# 256KB image from the zone -> black-screen load hang (2026-07-12). Those pixels aren't in base/mp
# (console-resident, like skybox) so we can't resolve them from PC; for a map WITH a genuine ref,
# emit the genuine material body verbatim. Keyed by the material's inline name string.
MATERIAL_BODY_OVERLAY = None

# Same pattern for GfxImage bodies (raid control, Track E): dict {image_name: genuine_body}.
# Standalone GfxImage assets with resident inline pixels absent from base/mp (e.g. the raid
# 48KB dropper) are emitted verbatim from the genuine console zone. Keyed by the image's
# inline name chars, applied at the end of convert_image (walk fully consumed first).
IMAGE_BODY_OVERLAY = None

# Streamed-body style bytes (delayLoadPixels, streaming). Map images use (1, 1)
# (mp_raid byte-exact); frontend/menu images use (0, 2) (dlc0 load-zone oracles).
STREAMED_STYLE = (1, 1)

# console T6 ImageFormat <- GX2 (inverse of ipak_stream.T6_TO_GX2); the PC zone's
# format byte uses the PC enum and must NOT be copied through.
# Rule (B) baked 2026-07-30: RGBA8 row added (GX2 0x1a -> T6 5, the unanimous healthy-record
# value; the engine's own table maps T6 4/5 -> 0x1a) and R8 (0x01 -> T6 1, matching the inline
# gray path's landed convention below). ⚠ gfxworld_gx2.py's own table maps 0x1a->4 and 0x01->2
# for ITS bands (validated in that lane) — the two conventions are both engine-legal; do not
# unify without measuring per band.
GX2_TO_T6 = {0x31: 0x6, 0x32: 0x9, 0x33: 0xA, 0x35: 0x14, 0x07: 0x3,
             0x1a: 0x5, 0x01: 0x1}

# PC GfxImageLoadDef.format is a DXGI code (rule B-sub, measured 14/14 on the leaked records:
# 0x1C=28 RGBA8, 0x47=71 BC1, 0x4D=77 BC3, 0x53=83 BC5; 74 = BC2 by the same table). It must
# NEVER reach a console body untranslated.
DXGI_TO_T6 = {28: 0x5, 71: 0x6, 74: 0x9, 77: 0xA, 83: 0x14}


def _gx2_to_t6_strict(gfmt, ctx=''):
    """Rule (B): translate or RAISE — the old `.get(..., 0)` silently emitted
    IMG_FORMAT_INVALID, the loader skipped Load_WiiuTexture entirely, and the image could
    never satisfy its streaming demand (held for exactly the 4 broken records and 0 of 1004
    healthy ones)."""
    t6 = GX2_TO_T6.get(gfmt)
    if t6 is None:
        raise ImageSpanFail('GX2 format 0x%X has no measured T6 row%s -- rule (B): translate '
                            'or raise, never default' % (gfmt, ' (%s)' % ctx if ctx else ''))
    return t6


def _dxgi_to_t6_strict(dxgi, ctx=''):
    """Rule (B-sub): a PC loadDef format is a DXGI code; translate or RAISE, so a source-side
    enum can never leak to body+180 (the residual-seagulls class: 15 records, 0 pixels)."""
    t6 = DXGI_TO_T6.get(dxgi)
    if t6 is None:
        raise ImageSpanFail('PC loadDef DXGI format %d (0x%X) has no measured T6 row%s -- '
                            'rule (B-sub): translate or raise, never leak'
                            % (dxgi, dxgi, ' (%s)' % ctx if ctx else ''))
    return t6

# When set to a list, every streamed-image ipak entry (name_hash, data_hash,
# payload) built for an embedded body is appended -- lets a caller author an
# ipak that matches the emitted zones exactly.
COLLECT_ENTRIES = None


class ImageSpanFail(Exception):
    pass


def pc_image_span(d, off, window=20):
    """End offset of one inline PC GfxImage that begins at `off` (right after a material texdef whose
    image ptr FOLLOWs). DETERMINISTIC — NOT a hash search (that failed on images with comma-prefixed
    aliased names whose hash@+60 is 0). The GfxImage body sits after a fixed lead (0 or 16 B) and is
    identified by its name-ptr FOLLOW @+56 within a TINY window (so the scan can't run into the next
    asset on dense zones). Then structurally: the reorder emits the inline name string (may carry a
    leading ',' alias marker — kept verbatim to its NUL), then texture.loadDef = a GfxImageLoadDef
    12-B header (levelCount@0/flags@1/format@4/resourceSize@8) + resourceSize pixel bytes (streamed
    images have resourceSize=0 -> a bare 12-B tail; inline-pixel images carry real pixels)."""
    for b in range(off, off + window, 4):
        if b + PC_IMG_BODY > len(d):
            break
        if struct.unpack_from('<I', d, b + 56)[0] != FOLLOW:      # image body's name ptr @+56
            continue
        e = d.index(b'\x00', b + PC_IMG_BODY, b + PC_IMG_BODY + 160)   # inline name string
        o = e + 1
        # texture.loadDef tail is emitted only for REAL inline images (texture/loadDef ptr @body+0
        # non-zero). Aliased stubs (comma-prefixed name, zeroed body, texture@0==0) carry no loadDef.
        if struct.unpack_from('<I', d, b + 0)[0] != 0:
            resource_size = struct.unpack_from('<I', d, o + 8)[0]  # GfxImageLoadDef.resourceSize
            o += 12 + resource_size
        return o
    # No inline-name landmark: a NULL-name streamed image (name@+56 not FOLLOW — the image name is
    # aliased/null, common for top-level-material textures). Body is a plain 64-B GfxImage at `off`;
    # texture.loadDef pixels only if texture@0 is FOLLOW (INSERT/alias -> referenced, no inline data).
    # NULL-name streamed inline image (PC streams pixels; console inlines them). Empirical formula:
    #   span = body(64) + streamedPartCount×GfxStreamedPartInfo(8) + GfxImageLoadDef(12 header +
    #   resourceSize, resourceSize=0 for streamed). streamedPartCount is the byte @27 (struct_layout
    #   GfxImage is the WRONG variant for these — its offsets don't apply; pinned empirically).
    if off + PC_IMG_BODY > len(d):        # truncated buffer (e.g. isolated round-trip test)
        return min(off + PC_IMG_BODY, len(d))
    o = off + PC_IMG_BODY + d[off + 27] * 8
    if struct.unpack_from('<I', d, off + 0)[0] in PTRS:         # texture.loadDef present
        rs = struct.unpack_from('<I', d, o + 8)[0]
        o += 12 + (rs if 0 <= rs < 0x8000000 else 0)           # resourceSize (guard garbage)
    return o


# ---- little helpers over a source buffer with a chosen endianness ----
def _u32(buf, o, le):
    return struct.unpack_from('<I' if le else '>I', buf, o)[0]

def _swap32(buf, o):
    """read LE u32 at o -> BE bytes (and vice-versa; symmetric)."""
    return struct.pack('>I', struct.unpack_from('<I', buf, o)[0])


# =====================================================================
# forward: PC (LE) material body -> console (BE) bytes
# =====================================================================
def _convert_image_fx(pc, off, reloc=_default_reloc):
    """Track C: one FX-inline-material image PC->console, matching the genuine
    console FX emit classes (oracle: all 164 raid FxEffectDefs, 2026-07-12):

      1. PC aliased comma-name stub (hash 0, zeroed body) -> ZEROED 328-B body
         (only the name ptr @320 survives) + name chars verbatim.
      2. real image whose semantic is not colorMap(2), or the sw_radiant_default
         placeholder -> STREAMED body (real GX2 header + part-hash entries,
         T6 dims 1x1, streaming=1), pixels via ipak.
      3. real colorMap resolvable from the PC source ipaks -> INLINE pixels:
         the full IWI mip chain GX2-tiled at gx2_texture.mip_chain offsets, the
         blob tail zero-padded to align(imageSize+mipSize, base_align);
         delayLoadPixels(body[159])=1; swizzle word13=(log2(maxdim)-6,floor 0)<<16.
         RGBA8 content is channel-compressed the way the console linker did:
         all-gray+opaque -> R8 (T6 fmt 1, R channel); gray+alpha -> LA8
         (T6 fmt 3, (R,A) pairs). Verified pixel-exact vs genuine.

    Same PC consume as convert_image (returns (console_bytes, next_pc_off))."""
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'wiiu_ref'))
    import ipak_stream as IS
    import gx2_texture as _GX

    b = off
    name_ptr = _u32(pc, b + 72, True)
    loaddef_ptr = _u32(pc, b + 0, True)
    meta = dict(mapType=pc[b + 4], semantic=pc[b + 5], category=pc[b + 6],
                width=struct.unpack_from('<H', pc, b + 20)[0],
                height=struct.unpack_from('<H', pc, b + 22)[0],
                depth=struct.unpack_from('<H', pc, b + 24)[0],
                levelCount=pc[b + 26],
                hash=_u32(pc, b + 76, True))
    src = b + 80
    name = b''
    if name_ptr in PTRS:
        end = pc.index(b'\x00', src)
        name = pc[src:end + 1]
        src = end + 1
    if loaddef_ptr in PTRS:                    # consume GfxImageLoadDef tail (none observed on FX)
        rs = _u32(pc, src + 8, True)
        src += 12 + rs

    def _zero_stub():
        body = bytearray(328)
        struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
        return bytes(body) + name, src

    iwi = FX_IMAGE_SOURCE(meta['hash']) if FX_IMAGE_SOURCE else None
    if meta['hash'] == 0 or iwi is None or not iwi.get('mips'):
        return _zero_stub()                    # class 1 (or unresolvable fallback)

    # streamed vs inline: console streaming-ipak membership when the discriminator is
    # wired (RESIDENT_IMAGE_TEST over base_split1..7 + <map>.ipak, True == resident ==
    # inline); PC-side fallback: colorMap(2) images are resident, non-colorMaps and the
    # sw_radiant_default placeholder stream (fits the whole raid FX oracle 24/24).
    if RESIDENT_IMAGE_TEST is not None:
        _fx_stream = not RESIDENT_IMAGE_TEST(meta['hash'])
    else:
        _fx_stream = (meta['semantic'] != 2
                      or name.rstrip(b'\x00') == b'sw_radiant_default')
    if _fx_stream:
        # class 2: STREAMED
        metaS = dict(meta, format=_gx2_to_t6_strict(iwi['gx2_format'], 'FX streamed'))
        body, _entries = IS.build_streamed_body(metaS, iwi, meta['hash'])
        if COLLECT_ENTRIES is not None:
            for pi, payload in _entries:
                COLLECT_ENTRIES.append((meta['hash'], _IP.data_hash(payload, pi), payload))
        body = bytearray(body)
        # swizzle word13, same rule as inline (genuine streamed FX bodies carry it too)
        struct.pack_into('<I', body, 52,
                         max(0, max(iwi['width'], iwi['height']).bit_length() - 1 - 6) << 16)
        struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
        return bytes(body) + name, src

    # class 3: INLINE full-mip pixels
    gfmt = iwi['gx2_format']
    w, h = iwi['width'], iwi['height']
    mips = iwi['mips']
    blob = iwi['blob']
    pixdata = [blob[o:o + s] for (_mw, _mh, o, s) in mips]
    if gfmt == 0x1a:                           # RGBA8 -> content channel compression
        rgba = pixdata[0]
        gray = all(rgba[i] == rgba[i + 1] == rgba[i + 2] for i in range(0, len(rgba), 4))
        opaque = all(rgba[i + 3] == 0xFF for i in range(0, len(rgba), 4))
        if gray and opaque:
            gfmt = 0x1                         # R8, T6 fmt 1
            pixdata = [p[0::4] for p in pixdata]
        elif gray:
            gfmt = 0x7                         # RG8 as L8A8, T6 fmt 3
            pixdata = [bytes(x for i in range(0, len(p), 4) for x in (p[i], p[i + 3]))
                       for p in pixdata]
    t6fmt = _gx2_to_t6_strict(gfmt, 'inline gray/RGBA path')
    tm = IS.base_tile_mode(gfmt, w, h)
    img_size, mip_offs, mip_size, infos = _GX.mip_chain(gfmt, w, h, tm, len(mips))
    total = (img_size + mip_size + 0x1FFF) & ~0x1FFF   # blob padded to 0x2000 (oracle: all 21)
    buf = bytearray(total)
    for i, p in enumerate(pixdata):
        mw, mh = mips[i][0], mips[i][1]
        inf = infos[i]
        lin = _GX.pad_linear(p, mw, mh, gfmt, inf.pitch, inf.height)
        tiled = _GX.tile(lin, mw, mh, gfmt, inf.tile_mode, pitch=inf.pitch)
        at = 0 if i == 0 else img_size + (0 if i == 1 else mip_offs[i - 1])
        buf[at:at + len(tiled)] = tiled
    body = bytearray(328)
    body[0:156] = IS.build_gx2_header(gfmt, w, h, len(mips))
    # swizzle (GX2 header word13): (log2(maxdim)-6 floored at 0) << 16 (raid FX oracle;
    # one 512-wide uncompressed outlier observed =13<<16, unexplained -> accepted diff)
    struct.pack_into('<I', body, 52, max(0, max(w, h).bit_length() - 1 - 6) << 16)
    body[156], body[157], body[158] = meta['mapType'], meta['semantic'], meta['category']
    body[159] = 1                              # delayLoadPixels (genuine: 1 on FX inline)
    struct.pack_into('>I', body, 160, total)   # baseSize = padded tiled size
    struct.pack_into('>HHH', body, 164, w, h, max(1, meta.get('depth', 1)))
    body[170] = len(mips)
    body[171] = 0                              # inline
    struct.pack_into('>I', body, 176, FOLLOW)  # pixels follow
    struct.pack_into('>I', body, 180, t6fmt)
    struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
    struct.pack_into('>I', body, 324, meta['hash'])
    return bytes(body) + name + bytes(buf), src


def convert_image(pc, off, reloc=_default_reloc):
    """One inline PC GfxImage (80-B body @off, name@72, texture.loadDef@0 -> 12-B header +
    resourceSize DXT pixels) -> console inline GfxImage stream (328-B GX2 body -> name chars ->
    tiled pixels), via the validated ipak_stream GX2 builder (build_inline_body). Returns
    (console_bytes, next_pc_off).

    Streamed PC images (resourceSize==0 / loadDef aliased): emitted as a streaming console body
    (streaming=1, no inline pixels, 0 part entries) — pixels resolve from the .ipak at runtime by
    hash. Cube/array images are not expected inline in materials (build_inline_body is 2D)."""
    if FX_INLINE_ACTIVE:                       # Track C: FX inline materials have their own
        return _convert_image_fx(pc, off, reloc)   # genuine emit classes (see _convert_image_fx)
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'wiiu_ref'))
    import ipak_stream as IS

    # active scoped resident-image resolver (mutually exclusive; each path brackets its own
    # convert). XModel-inline (A1) or GfxWorld materialMemory (emitter #3). None = neither active.
    _resid_src = (XMODEL_IMAGE_SOURCE if XMODEL_INLINE_ACTIVE else
                  MATMEM_IMAGE_SOURCE if MATMEM_INLINE_ACTIVE else None)

    b = off
    name_ptr = _u32(pc, b + 72, True)
    loaddef_ptr = _u32(pc, b + 0, True)
    meta = dict(mapType=pc[b + 4], semantic=pc[b + 5], category=pc[b + 6],
                width=struct.unpack_from('<H', pc, b + 20)[0],
                height=struct.unpack_from('<H', pc, b + 22)[0],
                depth=struct.unpack_from('<H', pc, b + 24)[0],
                levelCount=pc[b + 26],
                hash=_u32(pc, b + 76, True))
    src = b + 80
    name = b''
    if name_ptr in PTRS:                                  # inline name chars
        end = pc.index(b'\x00', src)
        name = pc[src:end + 1]
        src = end + 1
    pixels = b''
    if loaddef_ptr in PTRS:                               # GfxImageLoadDef: 12-B header + pixels
        meta['levelCount'] = pc[src] or meta['levelCount']
        meta['format'] = _u32(pc, src + 4, True)
        rs = _u32(pc, src + 8, True)
        pixels = pc[src + 12: src + 12 + rs]
        src += 12 + rs
    if pixels:
        # rule (B-sub): meta['format'] here is the PC loadDef DXGI code — translate before it
        # can reach body+180 or the GX2 tiler (build_inline_body treats format as T6 and
        # now raises on a leaked source enum).
        body, tiled = IS.build_inline_body(
            dict(meta, format=_dxgi_to_t6_strict(meta['format'], 'inline pixels path')), pixels)
        body = bytearray(body)
        struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
        out = bytes(body) + name + bytes(tiled)
    elif _resid_src is not None and \
            (lambda _i: _i is not None and _i.get('mips') and 'gx2_format' in _i)(
                _resid_src(meta['hash'])):
        # Resident-image emit for a SCOPED inline-material path (A1 XModel-inline OR GfxWorld
        # materialMemory). `_resid_src` is whichever scope is active (see top of function);
        # the two are mutually exclusive (each brackets its own convert). Resolves the image
        # from the PC source ipaks -> inline if resident, else a real streamed body.
        _iwi = _resid_src(meta['hash'])
        # DISCRIMINATOR: inline iff resident (absent from the map's streaming ipak); else STREAM.
        _res_test = (MATMEM_RESIDENT_TEST if (MATMEM_INLINE_ACTIVE and
                     MATMEM_RESIDENT_TEST is not None) else RESIDENT_IMAGE_TEST)
        # rule (B3) ENFORCED AT THE DECISION, not as an exception at the builder:
        # a CUBE is ALWAYS resident/inline. Genuine console zones carry 3,669/3,669
        # streamed records with ZERO mapType==5, and `IS.build_streamed_body` refuses
        # cubes outright ("route the image resident (rule B3)").
        # WHY THIS GUARD IS NEW (regression, 2026-08-13): the resident test is
        # `hash not in console_universe and hash not in the map's OWN ipaks`. Once a
        # map's own ipaks joined the image feed, a cube present there (zm_nuked's
        # `skybox_zm_nuked_ft`, hash 0xE1A3335F, in zm_nuked.ipak) tested NOT-resident,
        # routed to the streamed branch, and raised -- straight into the bare `except`
        # in the texture-table loop, which emitted NO image while the texdef had
        # ALREADY been written as FOLLOW. That dangling FOLLOW made the loader read the
        # following CONSTANT TABLE as a GfxImage (466,576,046-byte pixel span) and
        # desynced 25 assets / 2,534,491 B of the tail. Deciding it here means the
        # builder's refusal is unreachable by construction instead of load-bearing.
        if meta['mapType'] == 5 or _res_test is None or _res_test(meta['hash']):
            # RESIDENT -> emit INLINE (matching genuine, e.g. skybox_mp_raid, 512x512, 1.5MB).
            # Dims/mip count come from the IWI, not the PC zone body (whose levelCount is 1
            # for streamed PC images — copying it truncated every mip chain, raid 2026-07-12).
            # rule (B): derive strictly from the IWI — the old fallback to meta['format'] leaked
            # the PC/DXGI enum straight into body+180 (the B-sub class).
            meta2 = dict(meta, format=_gx2_to_t6_strict(_iwi['gx2_format'], 'resident inline'),
                         width=_iwi.get('width', meta['width']),
                         height=_iwi.get('height', meta['height']),
                         levelCount=max(1, len(_iwi['mips'])),
                         levelCount_raw=meta['levelCount'])
            if meta['mapType'] == 5:
                # CUBE (skybox): the IWI stores 6 faces of TOP-mip pixels back to back
                # (parse_iwi's tail-walk misreads them as a mip chain); mips stay 1.
                meta2['levelCount'] = 1
                _top = _iwi['mips'][0][3]              # per-face top-mip byte size
                _hdr = len(_iwi['blob']) - 6 * _top
                pcpix = _iwi['blob'][_hdr:_hdr + 6 * _top]
            else:
                pcpix = b''.join(_iwi['blob'][o2:o2 + s2]
                                 for (_lw, _lh, o2, s2) in _iwi['mips'])
            body, tiled = IS.build_inline_body(meta2, pcpix)
            body = bytearray(body)
            if MATMEM_INLINE_ACTIVE:
                # genuine matmem img_pix convention (raid oracle 2026-07-13: all 6 genuine
                # pixel-carrying matmem bodies have delayLoadPixels=1 and levelCount=REAL
                # mip count 1/6/8/9) — build_inline_body's XModel calibration (dlp=0,
                # levelCount=PC raw, often 0) makes the LOADER mis-size the inline pixel
                # run -> stream desync -> data relocated as pointers (deterministic block2
                # wild ptr, dumps 24900/27452). XModel scope untouched.
                body[159] = 1
                body[170] = max(1, meta2.get('levelCount', 1)) & 0xFF
            struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
            struct.pack_into('>I', body, 324, meta['hash'])
            out = bytes(body) + name + bytes(tiled)
        else:
            # STREAMED (in the ipak) -> real streamed body + part-hash entries, NOT the 1x1 stub.
            # This clears the null WITHOUT ballooning the zone (pixels stay in the ipak).
            _iwi2 = _iwi
            if MATMEM_INLINE_ACTIVE and MATMEM_MIP_CAP:
                mm = _iwi['mips']
                drop = 0
                while drop < len(mm) - 1 and max(mm[drop][0], mm[drop][1]) > MATMEM_MIP_CAP:
                    drop += 1
                if drop:
                    _iwi2 = dict(_iwi, mips=mm[drop:],
                                 width=mm[drop][0], height=mm[drop][1])
            metaS = dict(meta, format=_gx2_to_t6_strict(_iwi2['gx2_format'], 'matmem streamed'),
                         width=_iwi2['mips'][0][0], height=_iwi2['mips'][0][1])
            body, _entries = IS.build_streamed_body(metaS, _iwi2, meta['hash'])
            if COLLECT_ENTRIES is not None:
                for pi, payload in _entries:
                    COLLECT_ENTRIES.append((meta['hash'], _IP.data_hash(payload, pi), payload))
            body = bytearray(body)
            body[159] = STREAMED_STYLE[0]
            body[171] = STREAMED_STYLE[1]
            struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
            struct.pack_into('>I', body, 324, meta['hash'])
            out = bytes(body) + name
    else:                                                 # streamed: body only, pixels via ipak
        iwi = IMAGE_SOURCE(meta['hash']) if IMAGE_SOURCE else None
        if iwi is not None:
            meta = dict(meta, format=_gx2_to_t6_strict(iwi['gx2_format'], 'streamed'))
            body, _entries = IS.build_streamed_body(meta, iwi, meta['hash'])
            if COLLECT_ENTRIES is not None:
                for pi, payload in _entries:
                    COLLECT_ENTRIES.append((meta['hash'], _IP.data_hash(payload, pi),
                                            payload))
            body = bytearray(body)
            body[159] = STREAMED_STYLE[0]
            body[171] = STREAMED_STYLE[1]
            struct.pack_into('>I', body, 320,
                             name_ptr if name_ptr in PTRS else reloc(name_ptr))
        elif name.startswith(b','):
            # EXTERN REFERENCE (comma-prefixed): keep the GENUINE extern shape — a zeroed 1x1
            # stub with t6=0, streaming=1, parts=0. These are byte-identical in the boot-proven
            # key, never loaded as pixels (the DB strips the comma), and exempt from the (B2)
            # gate by the nameHash==0 ownership rule.
            body = bytearray(328)
            body[0:156] = IS.build_gx2_header(0x1a, 1, 1, 1)
            body[156], body[157], body[158], body[159] = meta['mapType'], meta['semantic'], meta['category'], 1
            struct.pack_into('>HHH', body, 164, 1, 1, 1)
            body[170] = 1
            body[171] = 1                                 # streaming (genuine extern shape)
            struct.pack_into('>I', body, 180, 0)          # t6=0, genuine extern value
            struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
            struct.pack_into('>I', body, 324, meta['hash'])
        else:
            # OWNED image with NO source. Rule (B-sub2), baked 2026-07-30: NEVER emit a
            # data-less streaming record — R_Stream_EnumForce marks every streaming==1 image
            # REQUIRED with no part-count check, so it can never be satisfied and the load
            # screen never latches (skate: 11 such records capped progress at 2050/2061), and
            # a NULL imagePtr makes the sampler fall through to whatever is resident (the
            # seagull-atlas class). Emit a RESIDENT 1x1 RGBA8 placeholder with real pixels.
            faces = 6 if meta['mapType'] == 5 else 1
            metaP = dict(meta, format=0x5, width=1, height=1, depth=1,
                         levelCount=1, levelCount_raw=1)
            body, tiled = IS.build_inline_body(metaP, b'\x80\x80\x80\xff' * faces)
            body = bytearray(body)
            body[156], body[157], body[158] = meta['mapType'], meta['semantic'], meta['category']
            struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
            struct.pack_into('>I', body, 324, meta['hash'])
            out = bytes(body) + name + bytes(tiled)
            if IMAGE_BODY_OVERLAY and name:
                key = name.rstrip(b'\x00').decode('latin-1', 'replace')
                if key in IMAGE_BODY_OVERLAY:
                    return IMAGE_BODY_OVERLAY[key], src
            return out, src
        out = bytes(body) + name
    # raid control (Track E): genuine-body substitution for named resident-image droppers.
    # src is fully walked above, so the stream stays in sync; only the body is swapped.
    if IMAGE_BODY_OVERLAY and name:
        key = name.rstrip(b'\x00').decode('latin-1', 'replace')
        if key in IMAGE_BODY_OVERLAY:
            return IMAGE_BODY_OVERLAY[key], src
    return out, src


# (EC) ledger for every inline-image FALLBACK: (material_name, image_name, reason).
# A skip that says nothing is indistinguishable from a skip that never happened --
# and this exact silence shipped a zone whose loader would read a constant table as
# a GfxImage. Never reset inside a bake; the assembler reports it and the re-bake
# acceptance requires it EMPTY when the trigger fix holds.
INLINE_IMAGE_FALLBACKS = []


def _placeholder_image_body(pc, off, reloc=_default_reloc):
    """A structurally valid console GfxImage for a texdef whose inline image could
    not be converted. Returns console bytes (never empty).

    ⛔ NOT a streamed record. Rule (B-sub2), boot-proven 2026-07-30:
    `R_Stream_EnumForce` marks every streaming==1 image REQUIRED with no part-count
    check, so a data-less streaming record can NEVER be satisfied and the load screen
    never latches (skate: 11 such records capped progress at 2050/2061). This reuses
    the SAME resident 1x1 RGBA8 placeholder shape that path already emits -- no new
    record shape is introduced, which is why it needs no separate calibration.

    `off` may be None when the PC cursor could not be resynced; the body is then
    emitted with neutral metadata (it still honours the texdef's FOLLOW, which is the
    invariant that matters -- a dangling FOLLOW is a corrupt zone)."""
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), '..', 'wiiu_ref'))
    import ipak_stream as IS
    mapType = semantic = category = 0
    name = b''
    name_ptr = 0
    ihash = 0
    if off is not None:
        try:
            mapType, semantic, category = pc[off + 4], pc[off + 5], pc[off + 6]
            name_ptr = _u32(pc, off + 72, True)
            ihash = _u32(pc, off + 76, True)
            if name_ptr in PTRS:
                name = pc[off + 80: pc.index(b'\x00', off + 80) + 1]
        except Exception:
            mapType = semantic = category = 0
            name, name_ptr, ihash = b'', 0, 0
    faces = 6 if mapType == 5 else 1
    metaP = dict(mapType=mapType, semantic=semantic, category=category,
                 format=0x5, width=1, height=1, depth=1,
                 levelCount=1, levelCount_raw=1, hash=ihash)
    body, tiled = IS.build_inline_body(metaP, b'\x80\x80\x80\xff' * faces)
    body = bytearray(body)
    body[156], body[157], body[158] = mapType, semantic, category
    struct.pack_into('>I', body, 320, name_ptr if name_ptr in PTRS else reloc(name_ptr))
    struct.pack_into('>I', body, 324, ihash)
    return bytes(body) + name + bytes(tiled)


def convert_material(pc, off, reloc=_default_reloc, marks=None, out_base=0):
    """Convert one PC material at stream offset `off`. Returns (console_bytes, next_pc_off).
    `console_bytes` is the full console material region (104 B body + trailing dynamic data).

    `marks` (optional list): FIX A (glass boot-52 class) — appends
    (pc_texture_table_stream_off, out_base + emitted_off, byte_len) for every
    emitted texture table (16-B stride on BOTH platforms -> exact linear
    correspondence). The assembler registers these as EXACT fine regions so
    texdef image dedup aliases targeting holder slots resolve exactly instead
    of through the coarse per-asset map, whose interior drift (console material
    is 48 B smaller than PC) stamped NEIGHBORING materials' slots (the white
    glass panes). `out_base` = this material's offset within the caller's
    output (threads through the thermal recursion)."""
    out = bytearray()
    _nmk = len(marks) if marks is not None else 0

    # ---- MaterialInfo 48 -> 40 : drops `surfaceFlags` (PC u32 @36) + trailing pad; `contents`
    #      (PC @40) moves to console @36. drawSurf IS kept. (verified vs genuine common_mp
    #      matched-pair oracle: console@36 == PC contents@40.) ----
    out += struct.pack('>I', reloc(_u32(pc, off + 0, True)))      # name ptr
    out += _swap32(pc, off + 4)                                   # gameFlags
    out += pc[off + 8: off + 16]                                  # pad/sortKey/atlas + pad (bytes)
    out += pc[off + 16: off + 24]                                 # drawSurf u64 — VERBATIM
    #   (352/352 raid GfxWorld inline-material oracle: the linker does NOT
    #    byte-swap the packed drawSurf; the old >Q swap was wrong)
    out += _swap32(pc, off + 24)                                  # surfaceTypeBits
    out += _swap32(pc, off + 28)                                  # layeredSurfaceTypes
    out += struct.pack('>H', struct.unpack_from('<H', pc, off + 32)[0])  # hashIndex u16
    out += pc[off + 38: off + 40]                                 # surfaceFlags upper half -> @34
    #   (446-pair common_mp oracle: console u16@34 = PC surfaceFlags bytes 2-3 verbatim,
    #    e.g. sf=0x00900000 -> 90 00; sf=0x00400000 -> 40 00. Was mis-modeled as pad.)
    out += pc[off + 40: off + 44]                                 # contents (-> console @36) — VERBATIM,
    #   the linker does NOT byte-swap this field (525/0 across the matched-pair oracle).
    # PC surfaceFlags @36..39 + pad @44..47 dropped

    # ---- stateBitsEntry char[36] -> char[32], stateBitsTable compaction ----
    # The console linker keeps only stateBits rows referenced by the KEPT 32 entries
    # (rows used solely by the 4 dropped techniques are pruned), compacted in index
    # order with sbe remapped (raid Glasses oracle: PC sbc=4, entries[32..35]=
    # [2,ff,ff,3] -> console sbc=2; the 446-pair common_mp oracle stays exact; the
    # FX-inline oracle note above reports the same compact+remap, 157/157).
    texc, constc, sbc = pc[off + 84], pc[off + 85], pc[off + 86]
    sbe = pc[off + 48: off + 48 + 32]
    sb_keep = sorted({e for e in sbe if e != 0xFF and e < sbc})
    sb_remap = {old: new for new, old in enumerate(sb_keep)}
    if sb_keep:
        out += bytes(sb_remap.get(e, 0xFF) if e != 0xFF else 0xFF for e in sbe)
    else:
        out += sbe          # no kept rows (e.g. sbc==0, sbe all zeros) -> verbatim
        #   (raid FX oracle: sbc=0 materials keep their zero sbe; mapping the
        #    zeros through an empty remap wrongly turned them into 0xFF)
    co_sbc = len(sb_keep) if sb_keep else sbc

    # ---- counts / flags (6 bytes) @84 -> @72 ----
    out += pc[off + 84: off + 86] + bytes([co_sbc]) + pc[off + 87: off + 90]
    out += b'\x00' * 2                                            # pad -> pointers @80

    # ---- 5 pointers @92 -> @80 ----
    ts  = _u32(pc, off + 92, True)
    tt  = _u32(pc, off + 96, True)
    ct  = _u32(pc, off + 100, True)
    sbt = _u32(pc, off + 104, True)
    th  = _u32(pc, off + 108, True)
    for v in (ts, tt, ct, sbt, th):
        out += struct.pack('>I', reloc(v))
    out += b'\x00' * 4                                            # tail pad -> 104
    assert len(out) == CO_MAT_SIZE, len(out)

    src = off + PC_MAT_SIZE

    # ---- trailing dynamic data, console stream order ----
    # info.name c-string
    mat_name = ''
    if (ts, tt, ct, sbt, th) and _u32(pc, off + 0, True) in PTRS:
        end = pc.index(b'\x00', src)
        mat_name = pc[src:end].decode('latin-1', 'replace')
        out += pc[src:end + 1]
        src = end + 1

    # techniqueSet: FOLLOW/INSERT -> a full inline MaterialTechniqueSet asset (DXBC shaders
    # and all) precedes the texture table (seen in zm map zones, e.g. mc/mtl_t6_attach_fastmag).
    # The PC DXBC body is never converted; when INLINE_TECHSET_HOOK is set (the
    # assemble installs a Track B blob resolver) the substitute CONSOLE techset
    # blob is emitted in its place — REQUIRED for loadability (the console
    # loader expects an inline techset when the ptr is FOLLOW/INSERT; see
    # CAVEATS_gfxworld_trackF.md §Integration). Without a hook: span-consume
    # only (legacy behavior, stream not loadable at that material).
    if ts in PTRS:
        import techset_pc
        nxt = techset_pc.parse_techset_pc(pc, src)
        if INLINE_TECHSET_HOOK is not None:
            blob = INLINE_TECHSET_HOOK(pc, src)
            if blob is None:
                raise RuntimeError('inline techset: no substitute blob @0x%x'
                                   % src)
            out += blob
        src = nxt

    # textureTable[textureCount] : MaterialTextureDef 16 B (identical size)
    if tt in PTRS:
        if marks is not None and texc:
            marks.append((src, out_base + len(out), texc * TEXDEF_SIZE))
        inline_imgs = []
        for i in range(texc):
            base = src + i * TEXDEF_SIZE
            out += _swap32(pc, base + 0)              # nameHash
            out += pc[base + 4: base + 12]            # nameStart/End, samplerState, semantic, isMature, pad
            imgv = _u32(pc, base + 12, True)
            out += struct.pack('>I', reloc(imgv))     # image ptr
            if imgv in PTRS:
                inline_imgs.append(i)
        src += texc * TEXDEF_SIZE
        # inline images (image ptr FOLLOW): CONVERT each to a console inline GfxImage stream
        # (328-B GX2 body + name + tiled pixels) via convert_image / the validated ipak_stream
        # GX2 builder. Fallback: if the PC image is a shape convert_image mis-parses (span
        # disagreement with the proven pc_image_span), consume-and-skip as before so the walk
        # still resyncs (that material's image then falls back to streamed/ipak resolution).
        # ⛔ EVERY texdef above already wrote its image pointer as FOLLOW, so from here
        # ONE emitted image is owed PER inline_imgs entry. Emitting fewer leaves a
        # DANGLING FOLLOW and the loader reads whatever follows (the constant table)
        # as a GfxImage -- measured: a 466,576,046-byte pixel span that desynced 25
        # assets / 2,534,491 B of zone tail. The old code did this in TWO ways: the
        # `except` emitted nothing, and the `break` abandoned every REMAINING image.
        _left = len(inline_imgs)
        for _ in inline_imgs:
            _left -= 1
            try:
                expected_end = pc_image_span(pc, src)
            except ImageSpanFail:
                expected_end = None
            _det = None
            try:
                img_out, nxt = convert_image(pc, src, reloc)
                if expected_end is not None and nxt != expected_end:
                    raise ImageSpanFail('convert_image span 0x%x != pc_image_span 0x%x'
                                        % (nxt, expected_end))
                out += img_out
                src = nxt
            except Exception as _ex:
                # (AE) the exception text is untrusted for encoding -- force ASCII
                # before it can reach stdout, or a UnicodeEncodeError re-raises here
                # and loses the diagnosis.
                _det = ('%s: %s' % (type(_ex).__name__, _ex)).encode(
                    'ascii', 'backslashreplace').decode('ascii')[:200]
            if _det is None:
                continue
            _iname = ''
            try:
                if _u32(pc, src + 72, True) in PTRS:
                    _iname = pc[src + 80: pc.index(b'\x00', src + 80)].decode(
                        'latin-1', 'replace')
            except Exception:
                pass
            out += _placeholder_image_body(pc, src, reloc)
            INLINE_IMAGE_FALLBACKS.append((name.rstrip(b'\x00').decode('latin-1', 'replace')
                                           if name else '?', _iname, _det))
            print('  !! inline image FALLBACK (1x1 resident placeholder) for %r in %r: %s'
                  % (_iname, (name.rstrip(b'\x00').decode('latin-1', 'replace')
                              if name else '?'), _det))
            if expected_end is None:
                # PC cursor cannot be resynced, so the remaining inline images cannot be
                # located -- but their texdefs are already FOLLOW. Emit a placeholder for
                # EACH so none dangles, then stop consuming.
                for _k in range(_left):
                    out += _placeholder_image_body(pc, None, reloc)
                    INLINE_IMAGE_FALLBACKS.append(
                        (name.rstrip(b'\x00').decode('latin-1', 'replace') if name else '?',
                         '<unlocatable>', 'PC cursor unresyncable after: %s' % _det))
                print('  !! inline image FALLBACK: +%d unlocatable image(s) in the same '
                      'material also placeheld (texdefs were already FOLLOW)' % _left)
                break
            src = expected_end

    # constantTable[constantCount] : MaterialConstantDef 32 B (identical size)
    if ct in PTRS:
        for i in range(constc):
            base = src + i * CONSTDEF_SIZE
            out += _swap32(pc, base + 0)              # nameHash
            out += pc[base + 4: base + 16]            # name char[12] verbatim
            for w in range(4):                        # literal vec4 (4 floats)
                out += _swap32(pc, base + 16 + w * 4)
        src += constc * CONSTDEF_SIZE

    # stateBitsTable[stateBitsCount] : GfxStateBits 20 -> 8 (loadBits[2] only).
    # Emit only the console count (trailing rows used solely by dropped techniques are
    # pruned) but CONSUME all PC rows to keep the walk in sync.
    if sbt in PTRS:
        for i in range(co_sbc):
            base = src + i * PC_SB_SIZE
            out += _swap32(pc, base + 0)              # loadBits[0]
            out += _swap32(pc, base + 4)              # loadBits[1]
        src += sbc * PC_SB_SIZE

    # thermalMaterial (inline) — recurse
    if th in PTRS:
        sub, src = convert_material(pc, src, reloc, marks, out_base + len(out))
        out += sub

    # raid control: replace a resident-inline-image material (compass_map_<map>) with the
    # genuine body verbatim (see MATERIAL_BODY_OVERLAY). src is fully walked, so the stream
    # stays in sync; only the emitted body is swapped for the genuine one.
    if MATERIAL_BODY_OVERLAY and mat_name in MATERIAL_BODY_OVERLAY:
        if marks is not None:
            del marks[_nmk:]     # overlay = genuine-verbatim body; our emitted
        return MATERIAL_BODY_OVERLAY[mat_name], src   # offsets are invalid

    return bytes(out), src


# =====================================================================
# inverse: console (BE) material body -> PC (LE) bytes.  For the self-contained
# round-trip test only (re-inserts drawSurf zeros, expands stateBitsEntry/GfxStateBits).
# =====================================================================
def deconvert_material(co, off, reloc=_default_reloc):
    """console material @off -> (pc_bytes_LE, next_co_off)."""
    out = bytearray()
    def be32(o): return struct.unpack_from('>I', co, o)[0]
    def put_le(v): out.extend(struct.pack('<I', v))

    # MaterialInfo 40 -> 48 (same offsets; re-append dropped contents=0 + pad)
    put_le(reloc(be32(off + 0)))                     # name
    put_le(be32(off + 4))                            # gameFlags
    out += co[off + 8: off + 16]                     # pad/sortKey/atlas + pad
    out += co[off + 16: off + 24]                    # drawSurf (VERBATIM both directions)
    put_le(be32(off + 24))                           # surfaceTypeBits
    put_le(be32(off + 28))                           # layeredSurfaceTypes
    out += struct.pack('<H', struct.unpack_from('>H', co, off + 32)[0])  # hashIndex
    out += b'\x00' * 2                               # PC pad @34
    out += b'\x00' * 2 + co[off + 34: off + 36]      # surfaceFlags: console @34 = PC bytes 38-39
    out += co[off + 36: off + 40]                    # contents (console @36 -> PC @40) — VERBATIM
    out += b'\x00' * 4                               # MaterialInfo pad -> 48

    # stateBitsEntry char[32] -> char[36]
    out += co[off + 40: off + 40 + 32]
    out += b'\x00' * 4

    texc, constc, sbc = co[off + 72], co[off + 73], co[off + 74]
    out += co[off + 72: off + 78]                    # counts/flags
    out += b'\x00' * 2                               # pad -> pointers @92

    ts, tt, ct, sbt, th = (be32(off + 80), be32(off + 84), be32(off + 88),
                           be32(off + 92), be32(off + 96))
    for v in (ts, tt, ct, sbt, th):
        put_le(reloc(v))
    assert len(out) == PC_MAT_SIZE, len(out)

    src = off + CO_MAT_SIZE
    if be32(off + 0) in PTRS:
        end = co.index(b'\x00', src)
        out += co[src:end + 1]; src = end + 1
    if tt in PTRS:
        for i in range(texc):
            base = src + i * TEXDEF_SIZE
            put_le(be32(base + 0))
            out += co[base + 4: base + 12]
            put_le(reloc(be32(base + 12)))
        src += texc * TEXDEF_SIZE
    if ct in PTRS:
        for i in range(constc):
            base = src + i * CONSTDEF_SIZE
            put_le(be32(base + 0))
            out += co[base + 4: base + 16]
            for w in range(4):
                put_le(be32(base + 16 + w * 4))
        src += constc * CONSTDEF_SIZE
    if sbt in PTRS:
        for i in range(sbc):
            base = src + i * CO_SB_SIZE
            put_le(be32(base + 0)); put_le(be32(base + 4))
            out += b'\x00' * 12                       # drop D3D state pointers
        src += sbc * CO_SB_SIZE
    if th in PTRS:
        sub, src = deconvert_material(co, src, reloc)
        out += sub
    return bytes(out), src
