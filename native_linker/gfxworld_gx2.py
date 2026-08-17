#!/usr/bin/env python3
"""
GfxWorld GX2 texture regions (Track F, Bucket A): draw.reflectionProbes,
draw.lightmaps, outdoorImage inline, tail material inline.

Facts pinned vs the genuine mp_raid oracle (2026-07-10):
  reflectionProbes: 29x 76B probe structs (all-word swap; last word @72 is a
    console-recomputed LOD bias: 0.0 normally, -5.0 on the 4x4 default probe0;
    PC stores -8.0) + per-probe INLINE cube GfxImage: console 328B GX2 body +
    name + RESIDENT tiled pixels. PC source is the same BC3 content, linear,
    FACE-MAJOR (each face's full mip chain consecutively). Console layout =
    gx2_texture.mip_chain(fmt,w,h,tm,mips,depth=6): 6x level0 surfaces then
    level-major mip blocks with 6 slices each; every (face,level) tiles with
    slice_index=face. VERIFIED byte-exact 48/48 face-surfaces on probe1.
    Cube swizzle = 0x10000 (tileMode 4); micro-tiled (tm2, tiny probe) = 0.
  lightmaps: console keeps only the SECONDARY lightmap resident: PC 512x3072
    RGBA8 -> console 1024x1536 BC3, reshaped 512-row tile k -> (row k//2, col
    k%2), then BC3-encoded (real re-encode; console encoder unknown -> ours is
    a range-fit, validated by decode-back diff vs genuine ~= BC3 noise).
    2D world-texture swizzle = 0xd0000.
  outdoorImage: 512x512 single-mip raw copy + tile (tm4, swizzle 0xd0000).
  tail material: standard inline Material stream (gfxworld_regions machinery).

Header allowlist: GX2 words 9/11 (image/mip runtime pointers) are baked link-
time garbage in genuine zones — we emit 0.

Pointer VALUES elsewhere are the assemble session's job (loader_sim omap).
"""
import struct
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, '..', 'wiiu_ref'))

import gx2_texture as gx

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

CUBE_SWIZZLE = 0x10000
WORLD2D_SWIZZLE = 0xd0000

# DXGI (PC GfxImageLoadDef.format) -> (GX2 format, T6 console format byte)
DXGI_MAP = {
    77: (0x33, 0xA),        # BC3_UNORM
    74: (0x32, 0x9),        # BC2_UNORM
    71: (0x31, 0x6),        # BC1_UNORM
    80: (0x34, 0xD),        # BC4_UNORM
    83: (0x35, 0x14),       # BC5_UNORM
    28: (0x1a, 0x3),        # R8G8B8A8_UNORM
    61: (0x01, 0x1),        # R8_UNORM
    65: (0x01, 0x2),        # A8_UNORM (outdoorImage: genuine GX2 R8 / T6 fmt 2)
}


def _u32(d, o, le=True):
    return struct.unpack_from('<I' if le else '>I', d, o)[0]


def parse_pc_image(pc, off):
    """One inline PC GfxImage (80B body + name + GfxImageLoadDef hdr + pixels).
    Returns dict(meta..., name, pixels, end)."""
    b = off
    meta = dict(mapType=pc[b + 4], semantic=pc[b + 5], category=pc[b + 6],
                width=struct.unpack_from('<H', pc, b + 20)[0],
                height=struct.unpack_from('<H', pc, b + 22)[0],
                depth=struct.unpack_from('<H', pc, b + 24)[0],
                levelCount=pc[b + 26],
                hash=_u32(pc, b + 76))
    o = b + 80
    name = b''
    if _u32(pc, b + 72) in PTRS:
        e = pc.index(b'\x00', o)
        name = pc[o:e + 1]
        o = e + 1
    pixels = b''
    if _u32(pc, b) in PTRS:
        lc = pc[o]
        meta['levelCount'] = lc or meta['levelCount']
        meta['dxgi'] = _u32(pc, o + 4)
        rs = _u32(pc, o + 8)
        pixels = pc[o + 12:o + 12 + rs]
        o += 12 + rs
    return dict(meta=meta, name=name, pixels=pixels, end=o)


def _gx2_header(dim, w, h, depth, mips, gfmt, tm, swizzle, img_size, mip_size,
                infos, mip_offs):
    hdr = bytearray(156)

    def le(i, v):
        struct.pack_into('<I', hdr, i * 4, v & 0xFFFFFFFF)
    le(0, dim); le(1, w); le(2, h); le(3, depth); le(4, mips); le(5, gfmt)
    le(6, 0); le(7, 1)
    le(8, img_size); le(9, 0)
    le(10, mip_size); le(11, 0)
    le(12, infos[0].tile_mode); le(13, swizzle)
    le(14, infos[0].baseAlign if hasattr(infos[0], 'baseAlign') else infos[0].base_align)
    le(15, infos[0].pitch)
    mlo = [0] * 13
    if mips > 1:
        mlo[0] = img_size
        for i in range(1, min(mips - 1, 13)):
            mlo[i] = mip_offs[i]
    for i in range(13):
        le(16 + i, mlo[i])
    return bytes(hdr)


def build_inline_image(meta, name, pixels_linear, cube=False, swizzle=None,
                       gfmt=None, t6fmt=None, lc170=None):
    """PC linear pixels -> console inline GfxImage stream (328B body + name +
    resident tiled pixels). cube: PC pixels are FACE-MAJOR full chains."""
    if gfmt is None:
        gfmt, t6fmt = DXGI_MAP[meta['dxgi']]
    w, h = meta['width'], meta['height']
    mips = meta['levelCount']
    if mips == 0:                       # PC levelCount 0 = full chain
        mips = max(w, h).bit_length()
    depth = 6 if cube else 1
    tm = gx and _base_tile_mode(gfmt, w, h)
    if swizzle is None:
        swizzle = 0 if tm in (1, 2, 3) else (CUBE_SWIZZLE if cube else WORLD2D_SWIZZLE)
    img_size, mip_offs, mip_size, infos = gx.mip_chain(gfmt, w, h, tm, mips, depth=depth)
    # resident blob is padded to 4 KB (genuine probe0: baseSize 0x6000 vs
    # img+mip 0x5800, tail zeros)
    total = (img_size + mip_size + 0xFFF) & ~0xFFF
    buf = bytearray(total)
    bpe = gx.BPP_BY_FORMAT[gfmt & 0x3F] >> 3

    def face_chain_sizes():
        out = []
        for l in range(mips):
            we, he = gx.block_dims(gfmt, max(1, w >> l), max(1, h >> l))
            out.append(we * he * bpe)
        return out
    sizes = face_chain_sizes()
    for f in range(depth):
        for l in range(mips):
            mw, mh = max(1, w >> l), max(1, h >> l)
            inf = infos[l]
            if cube:
                src = f * sum(sizes) + sum(sizes[:l])
            else:
                src = sum(sizes[:l])
            tight = pixels_linear[src:src + sizes[l]]
            if len(tight) < sizes[l]:
                tight = tight + b'\x00' * (sizes[l] - len(tight))
            lin = gx.pad_linear(tight, mw, mh, gfmt, inf.pitch, inf.height)
            tiled = gx.tile(lin, mw, mh, gfmt, inf.tile_mode, swizzle=swizzle,
                            pitch=inf.pitch, slice_index=f)
            if l == 0:
                at = f * (img_size // depth)
            else:
                at = img_size + (0 if l == 1 else mip_offs[l - 1]) + f * inf.size
            buf[at:at + len(tiled)] = tiled

    body = bytearray(328)
    body[0:156] = _gx2_header(3 if cube else 1, w, h, depth, mips, gfmt, tm,
                              swizzle, img_size, mip_size, infos, mip_offs)
    body[156] = meta['mapType']; body[157] = meta['semantic']
    body[158] = meta['category']; body[159] = 1          # delayLoadPixels
    struct.pack_into('>I', body, 160, len(buf))          # baseSize
    struct.pack_into('>HHH', body, 164, w, h, max(1, meta.get('depth', 1)))
    # genuine: 0 on single-mip inline-sourced images (outdoor), 1 on the
    # resident-from-streamed lut -> caller override via lc170
    body[170] = lc170 if lc170 is not None else (mips if mips > 1 else 0)
    body[171] = 0                                        # resident
    struct.pack_into('>I', body, 176, FOLLOW)            # pixels follow
    struct.pack_into('>I', body, 180, t6fmt)
    struct.pack_into('>I', body, 320, FOLLOW if name else 0)
    struct.pack_into('>I', body, 324, meta['hash'])
    return bytes(body) + name + bytes(buf)


def _base_tile_mode(gfmt, w, h):
    """Delegate to ipak_stream's calibrated rule."""
    import ipak_stream as IS
    return IS.base_tile_mode(gfmt, w, h)


# ------------------------------------------------------------- BC3 encode ---

def bc3_encode(rgba, w, h):
    """Range-fit BC3 encoder (numpy). Input HxWx4 uint8 -> BC3 block bytes.
    NOT the genuine console encoder (unknown) -> validated by decode-back diff."""
    import numpy as np
    a = rgba.reshape(h, w, 4)
    bw, bh = w // 4, h // 4
    # -> (nblocks, 16, 4)
    blocks = a.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    n = blocks.shape[0]
    out = np.zeros((n, 16), dtype=np.uint8)

    # ---- alpha (BC4-style): endpoints = min/max, 8-step palette ----
    al = blocks[:, :, 3].astype(np.int32)
    amax = al.max(axis=1); amin = al.min(axis=1)
    flat_a = amax == amin
    a0, a1 = amax, amin                      # a0 > a1 -> 8-interp mode
    out[:, 0] = a0; out[:, 1] = a1
    # palette (n,8)
    pal = np.zeros((n, 8), dtype=np.int32)
    pal[:, 0] = a0; pal[:, 1] = a1
    for i in range(1, 7):
        pal[:, i + 1] = ((7 - i) * a0 + i * a1) // 7
    d = np.abs(al[:, :, None] - pal[:, None, :])
    idx = d.argmin(axis=2).astype(np.uint64)          # (n,16) 3-bit indices
    packed = np.zeros(n, dtype=np.uint64)
    for i in range(16):
        packed |= idx[:, i] << np.uint64(3 * i)
    for b in range(6):
        out[:, 2 + b] = (packed >> np.uint64(8 * b)).astype(np.uint8)

    # ---- color (BC1 4-color): endpoints = min/max along luma-extremes ----
    rgb = blocks[:, :, :3].astype(np.int32)
    lum = rgb @ np.array([299, 587, 114])
    hi = rgb[np.arange(n), lum.argmax(axis=1)]
    lo = rgb[np.arange(n), lum.argmin(axis=1)]

    def to565(c):
        return ((c[:, 0] >> 3) << 11) | ((c[:, 1] >> 2) << 5) | (c[:, 2] >> 3)

    def from565(v):
        r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31
        return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4),
                         (b << 3) | (b >> 2)], axis=1)
    c0 = to565(hi); c1 = to565(lo)
    swap = c0 < c1
    c0s = np.where(swap, c1, c0); c1s = np.where(swap, c0, c1)
    eq = c0s == c1s
    p0 = from565(c0s); p1 = from565(c1s)
    p2 = (2 * p0 + p1) // 3; p3 = (p0 + 2 * p1) // 3
    pal4 = np.stack([p0, p1, p2, p3], axis=1)         # (n,4,3)
    d = ((rgb[:, :, None, :] - pal4[:, None, :, :]) ** 2).sum(axis=3)
    cidx = d.argmin(axis=2).astype(np.uint32)
    cidx[eq] = 0
    struct_view = out.view(np.uint8)
    out[:, 8] = c0s & 0xFF; out[:, 9] = c0s >> 8
    out[:, 10] = c1s & 0xFF; out[:, 11] = c1s >> 8
    packedc = np.zeros(n, dtype=np.uint32)
    for i in range(16):
        packedc |= cidx[:, i] << np.uint32(2 * i)
    for b in range(4):
        out[:, 12 + b] = (packedc >> np.uint32(8 * b)).astype(np.uint8)
    return out.tobytes()


# ----------------------------------------------------------- region emits ---

def conv_reflection_probes(pc, pstart, count, fixups=None, out_base=0):
    """PC reflectionProbes region -> console bytes. Probe structs word-swap with
    the @72 LOD-bias override (0.0; -5.0 when the probe image is micro-tiled tiny,
    matching genuine probe0); inline images via build_inline_image(cube=True);
    probeVolumes 96B word-swap (raid count=0 -> rule unexercised, all-float
    struct per struct_layout)."""
    if fixups is None:
        fixups = []
    out = bytearray()
    o = pstart + count * 76
    probes = []
    for i in range(count):
        ro = pstart + i * 76
        s = bytearray(76)
        for wd in range(19):
            s[wd * 4:wd * 4 + 4] = pc[ro + wd * 4:ro + wd * 4 + 4][::-1]
        probes.append((s, _u32(pc, ro + 60), _u32(pc, ro + 64), _u32(pc, ro + 68)))
    tail = bytearray()
    for i, (s, img, pv, pvc) in enumerate(probes):
        if img in PTRS:
            im = parse_pc_image(pc, o)
            o = im['end']
            small = max(im['meta']['width'], im['meta']['height']) < 32
            struct.pack_into('>f', s, 72, -5.0 if small else 0.0)
            tail += build_inline_image(im['meta'], im['name'], im['pixels'], cube=True)
        else:
            struct.pack_into('>f', s, 72, 0.0)
            if _is_alias(img):
                fixups.append(out_base + i * 76 + 60)
        if pv in PTRS:
            block = bytearray(pc[o:o + pvc * 96])
            for k in range(0, pvc * 96, 4):
                block[k:k + 4] = pc[o + k:o + k + 4][::-1]
            tail += block
            o += pvc * 96
    for s, _, _, _ in probes:
        out += s
    return bytes(out + tail), o


def _is_alias(v):
    return 0xA0000000 <= v < 0xC0000000


def conv_lightmaps(pc, pstart, count, fixups=None, out_base=0):
    """PC lightmaps region -> console. Entries 8B (2 image ptrs) swapped; the
    FOLLOW secondary image re-encoded: 512-row tiles k -> (k//2, k%2) reshape,
    BC3 range-fit, tiled (2D world swizzle). Raid shape (RGBA8 Wx(2N*W)) pinned;
    other shapes raise so a new map surfaces loudly."""
    import numpy as np
    if fixups is None:
        fixups = []
    out = bytearray()
    o = pstart + count * 8
    tail = bytearray()
    for i in range(count):
        for k in (0, 4):
            v = _u32(pc, pstart + i * 8 + k)
            out += pc[pstart + i * 8 + k:pstart + i * 8 + k + 4][::-1]
            if _is_alias(v):
                fixups.append(out_base + i * 8 + k)
            if v in PTRS:
                im = parse_pc_image(pc, o)
                o = im['end']
                m = im['meta']
                if m.get('dxgi') != 28:
                    raise ValueError('lightmap not RGBA8: dxgi=%r' % m.get('dxgi'))
                w, h = m['width'], m['height']
                # ⭐⭐ THE CONSOLE LIGHTMAP RESTACK RULE — generalised 2026-08-17
                # (mirage lane; derived + adopted by the Track-F lane from 9/9
                # genuine PC<->console pairs; lightmap_pair_gate.py is the gate).
                #
                #   ch    = 1536 ALWAYS -- every genuine console lightmap is
                #           exactly THREE rows of 512 (21/21 surveyed)
                #   ncols = nt // 3          (nt = source 512-row block count)
                #   cw    = w * ncols
                #   block k -> (row k // ncols, col k % ncols)
                #   invariant = nt divisible by THREE (NOT h divisible by 1024)
                #
                # ⚠ MOSTLY a generalisation: for ncols == 2 the block map
                # `k -> (k//ncols, k%ncols)` IS the old `divmod(t, 2)`, so those
                # shapes convert to the SAME BYTES:
                #     raid     512x3072  nt=6 ncols=2 -> 1024x1536  (as pinned)
                #     dockside 1024x3072 nt=6 ncols=2 -> 2048x1536  (as pinned)
                #
                # ⛔⛔ BUT IT IS **NOT** A PURE GENERALISATION, AND THIS IS THE
                # SINGLE MOST IMPORTANT FACT ABOUT THIS CHANGE:
                #     nt == 2  WAS ACCEPTED BEFORE (old test `h % (2*B)` passes;
                #              it produced a 1024x512 lightmap) AND IS REFUSED NOW.
                # For that one shape the refusal is WIDENED, not narrowed. An
                # earlier draft of this comment claimed "every shape that
                # converted before converts to the same bytes" -- that was FALSE
                # and is retracted here rather than only in the acceptance doc,
                # because this is the text a future editor of conv_lightmaps
                # actually reads.
                #
                # WHY WIDENING IT IS CORRECT (measured, both directions):
                #   * a 512-height console lightmap exists in **0 of 21** genuine
                #     console lightmaps surveyed (Track-F) -- every genuine one is
                #     1536 = three 512-rows. So the old nt=2 branch emitted a
                #     shape THE CONSOLE NEVER SHIPS: accepting it was laundering.
                #   * **0 of 13** lightmap shapes across **8 fleet maps** carry
                #     nt=2 (fleet census 2026-08-17; nt histogram {3: 2, 6: 11}).
                #     So no map we build is regressed by the refusal.
                # ⇒ a loud refusal beats a silently wrong shape -- the same
                #   principle as the (GY) anti-laundering fatal.
                #
                # ⚠ IF YOU EVER SEE nt == 2 RAISE HERE: that is a HUMAN DECISION,
                # not a bug to tune away. Ask whether that map was ever BOOTED
                # with the 512-height lightmap the old branch produced. See
                # `lightmap_pair_gate.py` clause (b) (POST-FIX-REFUSED), which
                # gates on exactly this and carries the same do-not-tune warning.
                # ⭐ And the case this unblocks is the IDENTITY:
                #     mirage   512x1536  nt=3 ncols=1 -> 512x1536
                # i.e. mirage's lightmap needs NO RESTACK AT ALL, only
                # RGBA8->BC3 + tile. The old `h % (2*B)` test refused it because
                # it demanded an EVEN number of 512-blocks; the real invariant is
                # divisibility by 3, and 3 itself is legal.
                #
                # WHY IT REFUSED FOR SO LONG: the old rule was pinned on two maps
                # that both happen to have nt=6, where "pair the blocks" and
                # "3 rows of 512" are indistinguishable. mp_nuketown_2020 carries
                # the identical nt=3 shape and gets this fix free (a SECOND
                # SUBJECT, never a witness for the rule).
                # ⛔ A shape whose nt is not divisible by 3 RAISES. ⚠ For nt=3
                # and nt=9 that is a NARROWING (they used to raise, they now
                # convert); for nt=2 it is a WIDENING (see above). Do not
                # summarise this as "the refusal is narrowed, never removed" —
                # an earlier draft did, and it is only half true.
                B = 512
                if h % B:
                    raise ValueError('lightmap height %d is not a multiple of the '
                                     '512-row block (%dx%d)' % (h, w, h))
                nt = h // B
                if nt % 3:
                    raise ValueError('lightmap shape %dx%d has %d 512-row blocks, '
                                     'not divisible by 3 -- console lightmaps are '
                                     'always 3 rows of 512, so nt must be 3*ncols'
                                     % (w, h, nt))
                src = np.frombuffer(im['pixels'], dtype=np.uint8).reshape(h, w, 4)
                ncols = nt // 3
                cw, ch = w * ncols, B * 3
                dst = np.zeros((ch, cw, 4), dtype=np.uint8)
                for t in range(nt):
                    r, c = divmod(t, ncols)
                    dst[r * B:(r + 1) * B, c * w:(c + 1) * w] = src[t * B:(t + 1) * B]
                blocks = bc3_encode(dst, cw, ch)
                cm = dict(m, width=cw, height=ch, levelCount=1, dxgi=77)
                tail += build_inline_image(cm, im['name'], blocks, cube=False)
    return bytes(out + tail), o


def conv_outdoor_image(pc, pstart, fixups=None, out_base=0):
    """Single inline 2D image, raw pixel copy + tile."""
    im = parse_pc_image(pc, pstart)
    return build_inline_image(im['meta'], im['name'], im['pixels'], cube=False), im['end']


GX2_TO_T6 = {0x31: 0x6, 0x32: 0x9, 0x33: 0xA, 0x35: 0x14, 0x07: 0x3, 0x1a: 0x4,
             0x01: 0x2}


# FIX 14 (2026-07-31, raid BOOT-10/11): verbatim genuine tail-material region.
# Set to bytes by produce_container for genuine-reference maps whose PC ipak is
# absent (raid), where image_source cannot resolve the LUT pixels and the
# converter falls back to a RESIDENT 1x1 PLACEHOLDER (measured: our
# mp_raid_lut image body carries w=1 h=1 + 4 grey bytes vs genuine 64x1024 +
# 262,144 B). The region is POINTER-FREE (measured: all 3,401 alias-range
# words lie inside the RGBA payload — pixel data, alpha 0xff), so a verbatim
# transplant needs no re-minting; it grows the GfxWorld by 253,952 B and every
# downstream offset is recomputed from our own layout by construction.
TAIL_MATERIAL_OVERLAY = None


def conv_tail_material(pc, pstart, image_source, reloc=None):
    """Tail lut material -> console stream. Genuine keeps the LUT image RESIDENT
    (64x1024 RGBA8) while PC streams it (1024x64 in the PC ipak): resolve pixels
    via `image_source(name_hash)`, restack the 16 horizontal 64x64 tiles
    vertically (validated vs raid oracle: identical content mod +-1 platform
    requantization), and emit an inline image. Material body/name/texdefs/
    consts/statebits via the mm machinery (drawSurf verbatim + sampler remap);
    inline techsets are EXCLUDED (Track B substitution supplies them at
    assemble). Returns (console_bytes, pc_end)."""
    import numpy as np
    import material_convert as MC
    import gfxworld_regions as GR
    if reloc is None:
        reloc = lambda v: v
    body, src = MC.convert_material(pc, pstart, reloc=reloc)
    if TAIL_MATERIAL_OVERLAY is not None:
        return bytes(TAIL_MATERIAL_OVERLAY), src      # FIX 14 (see module note)
    body = bytearray(body)
    body[16:24] = pc[pstart + 16:pstart + 24]          # drawSurf verbatim
    GR._remap_sampler_states(body)
    # convert_material INJECTS an inline console techset when MC.INLINE_TECHSET_HOOK
    # is set (assemble) -> the piece parser must skip it (include_techset=True) or it
    # mis-aligns and misses the img_body (leaving the resident lut stubbed).
    inc_ts = MC.INLINE_TECHSET_HOOK is not None
    pieces, _ = GR._console_material_pieces(bytes(body), 0, include_techset=inc_ts)
    out = bytearray()
    i = 0
    n = len(pieces)
    while i < n:
        tag, off, sz = pieces[i]
        if tag == 'img_body':
            # consume this img group (body [+name][+pix]) and replace
            hash_ = struct.unpack_from('>I', body, off + 324)[0]
            name = b''
            j = i + 1
            while j < n and pieces[j][0] in ('img_name', 'img_pix'):
                if pieces[j][0] == 'img_name':
                    name = bytes(body[pieces[j][1]:pieces[j][1] + pieces[j][2]])
                j += 1
            try:
                iwi = image_source(hash_) if image_source else None
            except Exception:
                iwi = None            # resolver format error (e.g. raw-pixel lut, not IWI) -> stub
            if iwi is None or not iwi.get('blob'):
                out += body[off:pieces[j - 1][1] + pieces[j - 1][2]]  # keep as-is
            else:
                # dims/format from the IWI meta, else from the console img_body's GX2
                # header (word1=w, word2=h, word5=gfmt; LE) — the latter handles
                # raw-pixel resident entries (e.g. the lut) that carry no IWI header.
                if iwi.get('width') and iwi.get('height'):
                    w, h, gfmt = iwi['width'], iwi['height'], iwi['gx2_format']
                else:
                    w = struct.unpack_from('<I', body, off + 4)[0]
                    h = struct.unpack_from('<I', body, off + 8)[0]
                    gfmt = struct.unpack_from('<I', body, off + 20)[0]
                pix = iwi['blob'][-_tight_size(gfmt, w, h):]
                if w > h and w % h == 0 and gfmt == 0x1a:   # LUT tile restack
                    a = np.frombuffer(pix, dtype=np.uint8).reshape(h, w, 4)
                    a = np.concatenate([a[:, k * h:(k + 1) * h] for k in range(w // h)], axis=0)
                    w, h = h, (w // h) * h
                    pix = a.tobytes()
                meta = dict(mapType=body[off + 156], semantic=body[off + 157],
                            category=body[off + 158], width=w, height=h, depth=1,
                            levelCount=1, hash=hash_)
                out += build_inline_image(meta, name, pix, cube=False,
                                          gfmt=gfmt, t6fmt=GX2_TO_T6.get(gfmt, 0),
                                          lc170=1)
            i = j
        else:
            out += body[off:off + sz]
            i += 1
    return bytes(out), src


def _tight_size(gfmt, w, h):
    we, he = gx.block_dims(gfmt, w, h)
    return we * he * (gx.BPP_BY_FORMAT[gfmt & 0x3F] >> 3)
