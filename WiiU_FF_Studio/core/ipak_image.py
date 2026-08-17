"""core.ipak_image -- GX2 detile + BCn decode fast enough to browse an ipak interactively.

WHY THIS EXISTS
---------------
The user's ask was "preview the stock Wii U format so the previewer doesn't have to convert
every asset as it displays it". The stock format is GX2-*tiled* BCn: no display stack (Tk, PIL,
a browser) can put that on screen without a conversion, so "show it unconverted" is not
achievable. What IS achievable, and is what the ask actually wants, is:

    never convert anything that is not currently on screen, and make the one conversion
    you do perform cheap enough to feel instant.

Three levers get us there:

  1. LAZY   -- nothing is decoded at open time. Opening a 1260-entry pak reads the 16-byte
               index records only. Decode happens on selection.
  2. NUMPY  -- detiling is a pure permutation: out[y*pitch+x] = data[element_address(x,y,...)].
               That is one vectorised gather instead of a per-element Python loop, and BCn
               decode becomes block math over arrays instead of per-pixel Python. This is where
               essentially all of the speedup comes from.
  3. MIP    -- a part-0 payload carries the whole mip chain. A 512-px preview of a 2048-px
               texture decodes level 2, not level 0, for ~1/16th the work. This is the largest
               lever of the three and the reason browsing feels instant.

The permutation depends ONLY on (bpp, pitch, padded height, tileMode, swizzle, slice) and never
on pixel values, so `_index_map` is also LRU-cached across images that share a geometry.

MEASURED this session (random payloads, full decode to RGBA, this machine):

    case         reference      cold      warm    speedup
    BC1 1024        533 ms     47 ms     43 ms        12x
    BC3 1024        654 ms     57 ms     52 ms        13x
    BC3 2048       2694 ms    246 ms    223 ms        12x
    BC3  256         41 ms      4 ms    3.5 ms        12x

Note warm ~= cold: the index-map cache is a minor contributor, not the main effect. Layered on
top, lever 3 takes a 2048-px BC3 preview from 223 ms to ~14 ms by decoding level 2.

CORRECTNESS
-----------
Speed is worthless if the pixels are wrong, so both halves are gated against the reference
implementation rather than eyeballed:

  * `_index_map` is checked against `gx2_texture.element_address` element by element.
  * `detile` output is compared byte-for-byte with `gx2_texture.detile`.
  * BC1/BC2/BC3 decode is compared byte-for-byte with `gx2_texture.decode_to_rgba`.

`selftest()` runs all three over a format/size sweep plus real retail payloads. BC4/BC5 have no
reference decoder in the codebase (`decode_to_rgba` raises on 0x34/0x35), so those are written
here and are validated only structurally -- that gap is stated, not papered over.

Rule (B) applies as everywhere else in this project: an unmodelled format RAISES. It never
silently falls back to a default, because a wrong-but-plausible preview is worse than a refusal
-- it would send someone hunting a texture bug that does not exist.
"""
import functools
import struct

import numpy as np

from . import paths  # noqa: F401  (registers the wiiu_ref backend on sys.path)

import gx2_texture as gx


# --------------------------------------------------------------------------- formats ---

# The ipak section-3 metadata records name their format as a linker string. These are the
# eight strings observed across all 121 retail Wii U paks (40,338 entries):
#     DXT5 77204 | DXN 59588 | DXT1 46962 | DXT3 3500 | A8L8 570
#     X8R8G8B8 115 | A8R8G8B8 17 | L8 8
# A8L8 was long mapped to None and REFUSED at decode, because no GX2 row for it was confirmed.
# It is now resolved: A8L8 == GX2 0x07 TC_R8_G8_UNORM. Read off 285 retail GfxImage records whose
# ipak metadata names them "A8L8" -- every one declares gx2_format 0x07, no exceptions -- and
# corroborated by the Cafe SDK enum and by the retail RPL's own Image_GetGx2Format table.
# ⚠ EVERY A8L8 TEXTURE IS AN EMBLEM (all 290 names begin em_) AND ALL 285 HAVE ALPHA == 0.
# The artwork lives in LUMINANCE; the emblem shader reads it from the colour channel. A faithful
# RGBA decode therefore looks fully transparent, which is CORRECT and must not be "fixed" inside
# decode_rgba -- forcing alpha=255 there would break the decode->encode round trip and write 255
# into all 285 alpha planes on re-import. The previewer composites instead; see ipak_ide.
FORMAT_STRING_TO_GX2 = {
    'DXT1': 0x31,        # BC1
    'DXT3': 0x32,        # BC2
    'DXT5': 0x33,        # BC3
    'DXN': 0x35,         # BC5 / 3Dc, two-channel normal maps
    'A8R8G8B8': 0x1a,
    'X8R8G8B8': 0x1a,
    'L8': 0x01,
    'A8L8': 0x07,        # GX2 TC_R8_G8_UNORM -- byte0 luminance, byte1 alpha
}

FORMAT_LABEL = {
    0x01: 'R8', 0x07: 'A8L8', 0x1a: 'RGBA8',
    0x31: 'BC1', 0x32: 'BC2', 0x33: 'BC3', 0x34: 'BC4', 0x35: 'BC5',
}

DECODABLE = frozenset((0x01, 0x07, 0x1a, 0x31, 0x32, 0x33, 0x34, 0x35))


class ImageDecodeError(ValueError):
    """A payload could not be turned into pixels. Carries why, so the GUI can say so."""


def for_display(rgba):
    """(h,w,4) RGBA -> (h,w,4) safe to show on screen. -> (array, note or None)

    ⚠ A DISPLAY AID, NEVER A DECODE FIX. Every retail A8L8 emblem stores its artwork in
    LUMINANCE and leaves ALPHA identically zero (measured: 285 of 285), because the emblem
    shader reads the colour channel. Composited honestly, all 285 are invisible -- which is a
    true rendering of the bytes and completely useless to look at.

    So the fully-transparent case is made opaque HERE, at the point of display, and the caller
    is handed a note it can show. decode_rgba stays faithful, so the decode->encode round trip
    is unaffected and a re-import never writes 255 into an alpha plane that was zero.
    """
    if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
        return rgba, None
    if rgba[:, :, 3].max() == 0:
        out = rgba.copy()
        out[:, :, 3] = 255
        return out, ('alpha is entirely zero, so this is shown opaque. The artwork is in the '
                     'luminance channel -- normal for emblems, and the stored bytes are unchanged.')
    return rgba, None


def format_from_string(s):
    """'DXT5' -> 0x33. Unknown strings raise; known-but-unmodelled return None."""
    if s not in FORMAT_STRING_TO_GX2:
        raise ImageDecodeError('unknown ipak format string %r (known: %s)'
                               % (s, ', '.join(sorted(FORMAT_STRING_TO_GX2))))
    return FORMAT_STRING_TO_GX2[s]


def base_tile_mode(gfmt, w, h):
    """Which tile mode the stock linker used for a level-0 surface.

    Mirrors ConsoleWriterT6 / ipak_stream.base_tile_mode exactly: macro (4) unless the padded
    surface is too small to fill one macro tile, in which case micro (2).
    """
    bpp = gx.BPP_BY_FORMAT[gfmt & 0x3F]
    we, he = gx.block_dims(gfmt, w, h)
    micro_tile_bytes = (64 * bpp) >> 3
    waf = max(1, 256 // micro_tile_bytes) if micro_tile_bytes < 256 else 1
    if gx.next_pow2(we) < waf * 32 or gx.next_pow2(he) < 16:
        return 2
    return 4


# ----------------------------------------------------------------- vectorised detile ---

@functools.lru_cache(maxsize=64)
def _index_map(bpp, pitch, h_pad, tile_mode, pipe_sw, bank_sw, slice_index):
    """Element-index permutation for one surface geometry: dst element i <- src element map[i].

    This is the whole speed story. It is pure geometry -- no pixel data enters -- so it is
    cached and shared by every image with the same (bpp, pitch, height, tileMode). The cache is
    keyed on the full tuple, so two geometries can never alias each other.

    Vectorised transcription of gx2_texture.element_address. `selftest` proves it agrees with
    that scalar reference on every element of a format/size sweep; if you touch this, run it.
    """
    y, x = np.divmod(np.arange(pitch * h_pad, dtype=np.int64), pitch)

    if tile_mode in (0, 1, 16):                       # linear
        return (y * pitch + x).astype(np.int64)

    # --- element index inside the 8x8 micro tile (pixel_index_in_micro_tile) ---
    x0, x1, x2 = x & 1, (x >> 1) & 1, (x >> 2) & 1
    y0, y1, y2 = y & 1, (y >> 1) & 1, (y >> 2) & 1
    if bpp == 8:
        bits = (x0, x1, x2, y1, y0, y2)
    elif bpp == 16:
        bits = (x0, x1, x2, y0, y1, y2)
    elif bpp in (32, 96):
        bits = (x0, x1, y0, x2, y1, y2)
    elif bpp == 64:
        bits = (x0, y0, x1, x2, y1, y2)
    elif bpp == 128:
        bits = (y0, x0, x1, x2, y1, y2)
    else:
        bits = (x0, x1, y0, x2, y1, y2)
    pix = (bits[0] | (bits[1] << 1) | (bits[2] << 2)
           | (bits[3] << 3) | (bits[4] << 4) | (bits[5] << 5))

    micro_tile_bytes = (64 * bpp) >> 3
    elem_offset = (bpp * pix) >> 3

    if tile_mode in (2, 3):                           # micro tiled
        tiles_per_row = pitch >> 3
        tile_offset = micro_tile_bytes * ((x >> 3) + (y >> 3) * tiles_per_row)
        addr = tile_offset + elem_offset
        return (addr // (bpp >> 3)).astype(np.int64)

    # --- macro tiled (4..7) ---
    pipe = ((y >> 3) ^ (x >> 3)) & 1
    bank = (((y >> 5) ^ (x >> 3)) & 1) | (2 * (((y >> 4) ^ (x >> 4)) & 1))
    rot = gx.tile_rotation(tile_mode)
    bank_pipe = (pipe + 2 * bank) ^ (pipe_sw + 2 * bank_sw + slice_index * rot)
    bank_pipe %= gx.NUM_PIPES * gx.NUM_BANKS
    pipe = bank_pipe % gx.NUM_PIPES
    bank = bank_pipe // gx.NUM_PIPES

    macro_w, macro_h = gx.macro_tile_dims(tile_mode)
    macro_tiles_per_row = pitch // macro_w
    macro_tile_bytes = (bpp * macro_h * macro_w) >> 3
    macro_offset = ((x // macro_w) + macro_tiles_per_row * (y // macro_h)) * macro_tile_bytes

    total = elem_offset + (macro_offset >> 3)
    addr = (bank << 9) | (pipe << 8) | (total & 255) | ((total & ~255) << 3)
    return (addr // (bpp >> 3)).astype(np.int64)


def detile(data, width, height, gfmt, tile_mode, swizzle=0, pitch=None, slice_index=0):
    """Tiled surface bytes -> padded linear bytes. Byte-identical to gx2_texture.detile."""
    inf = gx.surface_info(gfmt, width, height, tile_mode)
    if pitch is None:
        pitch = inf.pitch
    bpp = inf.bpp
    bpe = bpp >> 3
    h_pad = len(data) * 8 // (pitch * bpp)
    if h_pad <= 0:
        raise ImageDecodeError('payload of %d B is too small for a %d-wide %d-bpp surface'
                               % (len(data), pitch, bpp))
    pipe_sw, bank_sw = (swizzle >> 8) & 1, (swizzle >> 9) & 3
    idx = _index_map(bpp, pitch, h_pad, tile_mode, pipe_sw, bank_sw, slice_index)

    src = np.frombuffer(data, dtype=np.uint8)
    n_elem = len(src) // bpe
    src = src[:n_elem * bpe].reshape(n_elem, bpe)
    # A tiled surface can address slightly past the supplied buffer on the last macro tile;
    # clamp rather than raise, then zero those rows so we never present another image's bytes
    # as if they belonged to this one.
    over = idx >= n_elem
    safe = np.where(over, 0, idx)
    out = src[safe]
    if over.any():
        out = out.copy()
        out[over] = 0
    return out.reshape(-1).tobytes()


def crop_linear(linear, width, height, gfmt, pitch):
    """Padded linear -> tight width x height element rectangle (vectorised crop_linear)."""
    we, he = gx.block_dims(gfmt, width, height)
    bpe = gx.BPP_BY_FORMAT[gfmt & 0x3F] >> 3
    a = np.frombuffer(linear, dtype=np.uint8)
    need = he * pitch * bpe
    if len(a) < need:
        a = np.concatenate([a, np.zeros(need - len(a), np.uint8)])
    return a[:need].reshape(he, pitch * bpe)[:, :we * bpe].tobytes()


# ------------------------------------------------------------------- block decoders ---

def _rgb565(c):
    r = ((c >> 11) & 31).astype(np.uint16)
    g = ((c >> 5) & 63).astype(np.uint16)
    b = (c & 31).astype(np.uint16)
    return (np.stack([(r * 255 + 15) // 31,
                      (g * 255 + 31) // 63,
                      (b * 255 + 15) // 31], -1)).astype(np.uint8)


def _bc1_colors(blocks, force_opaque):
    """blocks: (N,8) uint8 -> (rgb (N,16,3) uint8, alpha (N,16) uint8).

    BCn block words are LITTLE-endian even inside a big-endian console file: the GPU consumes
    them natively, the linker never byte-swaps them. Matches gx2_texture._bc1_block.
    """
    n = len(blocks)
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    bits = (blocks[:, 4].astype(np.uint32) | (blocks[:, 5].astype(np.uint32) << 8)
            | (blocks[:, 6].astype(np.uint32) << 16) | (blocks[:, 7].astype(np.uint32) << 24))
    p0, p1 = _rgb565(c0).astype(np.uint16), _rgb565(c1).astype(np.uint16)

    pal = np.zeros((n, 4, 3), np.uint16)
    pal[:, 0], pal[:, 1] = p0, p1
    opaque = (c0 > c1) | force_opaque
    # 4-colour block (opaque): 1/3 and 2/3 lerps. 3-colour block: midpoint + transparent black.
    pal[:, 2] = np.where(opaque[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    pal[:, 3] = np.where(opaque[:, None], (p0 + 2 * p1) // 3, 0)

    sel = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3
    rgb = np.take_along_axis(pal, sel[:, :, None].astype(np.intp), axis=1).astype(np.uint8)

    alpha = np.full((n, 16), 255, np.uint8)
    if not force_opaque:
        alpha[(~opaque)[:, None] & (sel == 3)] = 0
    return rgb, alpha


def _bc3_alpha(ab):
    """ab: (N,8) uint8 -> (N,16) uint8. Matches gx2_texture._bc3_alpha."""
    n = len(ab)
    a0 = ab[:, 0].astype(np.int32)
    a1 = ab[:, 1].astype(np.int32)
    bits = np.zeros(n, np.uint64)
    for i in range(6):
        bits |= ab[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)

    pal = np.zeros((n, 8), np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    wide = a0 > a1
    for i in range(1, 7):                      # 8-value mode
        pal[:, i + 1] = np.where(wide, ((7 - i) * a0 + i * a1) // 7, pal[:, i + 1])
    for i in range(1, 5):                      # 6-value mode
        pal[:, i + 1] = np.where(~wide, ((5 - i) * a0 + i * a1) // 5, pal[:, i + 1])
    pal[:, 6] = np.where(wide, pal[:, 6], 0)
    pal[:, 7] = np.where(wide, pal[:, 7], 255)

    sel = ((bits[:, None] >> (3 * np.arange(16, dtype=np.uint64))[None, :])
           & np.uint64(7)).astype(np.intp)
    return np.take_along_axis(pal, sel, axis=1).astype(np.uint8)


def _blocks_to_image(px, w, h):
    """(nblocks,16,C) in block-raster order -> (h,w,C), cropping the 4x4 padding."""
    wb, hb = (w + 3) // 4, (h + 3) // 4
    c = px.shape[-1]
    img = px.reshape(hb, wb, 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(hb * 4, wb * 4, c)
    return img[:h, :w]


def decode_rgba(tight, w, h, gfmt):
    """Tight linear surface -> (h, w, 4) uint8 RGBA. Raises on unmodelled formats (rule B)."""
    f = gfmt & 0x3F
    a = np.frombuffer(tight, dtype=np.uint8)

    if f == 0x1a:                                            # RGBA8
        need = w * h * 4
        a = _fit(a, need)
        return a[:need].reshape(h, w, 4)
    if f == 0x01:                                            # R8 / L8
        a = _fit(a, w * h)
        g = a[:w * h].reshape(h, w)
        return np.dstack([g, g, g, np.full((h, w), 255, np.uint8)])
    if f == 0x07:                                            # R8_G8 == A8L8
        # ⚠ 0x07 IS TWO 8-BIT CHANNELS, NOT R5G6B5. Measured from the Cafe SDK enum:
        #   GX2_SURFACE_FORMAT_TC_R8_G8_UNORM    = 0x07
        #   GX2_SURFACE_FORMAT_TCS_R5_G6_B5_UNORM = 0x08     <- what this branch used to decode
        # An off-by-one row. Confirmed independently against 285 retail GfxImage records whose
        # ipak metadata names them "A8L8": every one declares gx2_format 0x07, no exceptions.
        #
        # BYTE ORDER IS MEASURED, not assumed: the retail RPL's own Image_GetGx2Format maps the
        # T6 image-format enum to (GX2 format, GX2CompSel), and row t6=3 gives gx2 0x07 with
        # compSel GX2_GET_COMP_SEL(X,X,X,Y) -- destination R,G,B all take source component X
        # (the FIRST byte, luminance) and destination A takes Y (the SECOND byte). Visual
        # controls agree: distfont's glyphs live in byte 1 while byte 0 is a flat 255, and
        # camo_inner_shadow is the exact mirror.
        a = _fit(a, w * h * 2)
        px = a[:w * h * 2].reshape(h, w, 2)
        lum, alpha = px[:, :, 0], px[:, :, 1]
        return np.dstack([lum, lum, lum, alpha])

    if f in (0x31, 0x32, 0x33, 0x34, 0x35):
        bs = 8 if f in (0x31, 0x34) else 16
        nb = ((w + 3) // 4) * ((h + 3) // 4)
        a = _fit(a, nb * bs)
        blk = a[:nb * bs].reshape(nb, bs)

        if f == 0x31:
            rgb, al = _bc1_colors(blk, force_opaque=False)
        elif f == 0x32:                                      # BC2: explicit 4-bit alpha
            rgb, _ = _bc1_colors(blk[:, 8:], force_opaque=True)
            nib = blk[:, :8]
            al = np.zeros((nb, 16), np.uint8)
            al[:, 0::2] = (nib & 15) * 17
            al[:, 1::2] = (nib >> 4) * 17
        elif f == 0x33:                                      # BC3: BC4 alpha + BC1 colour
            rgb, _ = _bc1_colors(blk[:, 8:], force_opaque=True)
            al = _bc3_alpha(blk[:, :8])
        elif f == 0x34:                                      # BC4: single channel
            r = _bc3_alpha(blk)
            rgb = np.repeat(r[:, :, None], 3, axis=2)
            al = np.full((nb, 16), 255, np.uint8)
        else:                                                # BC5 / DXN: two BC4 channels
            r = _bc3_alpha(blk[:, :8])
            g = _bc3_alpha(blk[:, 8:])
            # Reconstruct Z so a normal map previews as a recognisable normal map rather than
            # as a red/green smear. Display aid only -- the stored payload is untouched.
            x = r.astype(np.float32) / 127.5 - 1.0
            y = g.astype(np.float32) / 127.5 - 1.0
            z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
            b = ((z + 1.0) * 127.5).astype(np.uint8)
            rgb = np.stack([r, g, b], -1)
            al = np.full((nb, 16), 255, np.uint8)

        rgba = np.concatenate([rgb, al[:, :, None]], axis=2)
        return _blocks_to_image(rgba, w, h)

    raise ImageDecodeError('no decoder for GX2 format 0x%X (%s) -- rule (B): refuse rather '
                           'than present a wrong preview'
                           % (f, FORMAT_LABEL.get(f, 'unknown')))


def _fit(a, need):
    if len(a) < need:
        return np.concatenate([a, np.zeros(need - len(a), np.uint8)])
    return a


# ------------------------------------------------------------------- top-level entry ---

def fit_rgba(rgba, width, height):
    """Resize an (h,w,4) RGBA array to exactly width x height.

    Each part of a streamed image carries a different slice of the mip chain and therefore its
    OWN dimensions, so a whole-image replace has to resample the one source picture per part
    rather than assume it already matches. Returns the input untouched when it already fits.
    """
    from PIL import Image
    a = np.asarray(rgba, np.uint8)
    if a.ndim != 3 or a.shape[2] != 4:
        raise ImageEncodeError('expected an (h,w,4) RGBA array, got shape %r' % (a.shape,))
    if a.shape[1] == width and a.shape[0] == height:
        return a
    im = Image.fromarray(a, 'RGBA').resize((int(width), int(height)), Image.LANCZOS)
    return np.asarray(im, np.uint8)


def pick_level(width, height, levels, max_side):
    """Smallest mip level that still fills `max_side`. Lever 3 -- see the module docstring.

    Returns 0 when there is no chain to exploit or no bound was asked for.
    """
    if not max_side or levels <= 1:
        return 0
    lvl = 0
    while lvl + 1 < levels and max(max(1, width >> (lvl + 1)),
                                   max(1, height >> (lvl + 1))) >= max_side:
        lvl += 1
    return lvl


def decode_payload(payload, width, height, gfmt, tile_mode=None, max_side=None, levels=1):
    """One ipak part payload -> (h, w, 4) RGBA array. The single call the GUI needs.

    `levels` is the part's mip count from its metadata record. When >1 the payload holds the
    whole chain, so a bounded preview decodes a SMALL level instead of level 0 -- the big win.
    `max_side` then trims any remainder by striding, which costs nothing.

    Returns (rgba, level, (level_w, level_h)) so the caller can tell the user what it is
    actually looking at rather than implying it is the full-resolution surface.
    """
    if gfmt is None:
        raise ImageDecodeError('this entry has no modelled GX2 format')
    if (gfmt & 0x3F) not in DECODABLE:
        raise ImageDecodeError('GX2 format 0x%X has no decoder' % gfmt)
    if width <= 0 or height <= 0:
        raise ImageDecodeError('bad dimensions %dx%d' % (width, height))
    if tile_mode is None:
        tile_mode = base_tile_mode(gfmt, width, height)

    levels = max(1, int(levels or 1))
    lvl = pick_level(width, height, levels, max_side)

    if lvl:
        # Mip offsets inside a part payload, mirroring ipak_stream.tile_part_payload exactly:
        # level 0 sits at 0, level 1 at imageSize, level n>1 at imageSize + mip_offs[n-1].
        img_size, mip_offs, _mip_size, infos = gx.mip_chain(gfmt, width, height,
                                                            tile_mode, levels)
        if lvl >= len(infos):
            lvl = 0
        else:
            inf = infos[lvl]
            at = img_size + (0 if lvl == 1 else mip_offs[lvl - 1])
            lw, lh = max(1, width >> lvl), max(1, height >> lvl)
            if at + inf.size <= len(payload):
                lin = detile(payload[at:at + inf.size], lw, lh, gfmt, inf.tile_mode,
                             pitch=inf.pitch)
                tight = crop_linear(lin, lw, lh, gfmt, inf.pitch)
                img = decode_rgba(tight, lw, lh, gfmt)
                return _stride(img, max(lw, lh), max_side), lvl, (lw, lh)
            lvl = 0                       # chain shorter than advertised: fall back to level 0

    inf = gx.surface_info(gfmt, width, height, tile_mode)
    if len(payload) < inf.size:
        raise ImageDecodeError('payload %d B < surface %d B for %dx%d %s tm%d'
                               % (len(payload), inf.size, width, height,
                                  FORMAT_LABEL.get(gfmt & 0x3F, '?'), tile_mode))
    lin = detile(payload[:inf.size], width, height, gfmt, tile_mode)
    tight = crop_linear(lin, width, height, gfmt, inf.pitch)
    img = decode_rgba(tight, width, height, gfmt)
    return _stride(img, max(width, height), max_side), 0, (width, height)


def _stride(img, side, max_side):
    if max_side and side > max_side:
        step = int(np.ceil(side / float(max_side)))
        return img[::step, ::step]
    return img


# --------------------------------------------------------------------------- encoding ---

# BCn compression is done by Pillow's C encoder rather than hand-rolled here: it is faster and
# better quality than a naive min/max endpoint fit, and it is already a dependency. Pillow
# reaches it through the DDS writer, so we encode to an in-memory DDS and slice the payload off.
# BC5 goes out through the DX10 extended header, which is 20 bytes longer.
_DDS_HDR = 128
_DDS_DX10_EXTRA = 20

_GX2_TO_PIL_DDS = {
    0x31: ('DXT1', 'RGBA'),
    0x32: ('DXT3', 'RGBA'),
    0x33: ('DXT5', 'RGBA'),
    0x35: ('BC5', 'RGB'),      # Pillow refuses BC5 from an RGBA image
}


class ImageEncodeError(ValueError):
    """A replacement image could not be encoded into the entry's stored format."""


def encode_surface(rgba, w, h, gfmt):
    """(h,w,4) uint8 RGBA -> tight linear surface bytes in `gfmt`.

    The format is NOT negotiable on a replace: the GfxImage record inside the .ff declares it,
    and the GPU reads the ipak payload with that declaration. Handing back a different format
    would be read as garbage, so callers must pass the entry's existing format and we encode to
    exactly it.
    """
    from PIL import Image
    f = gfmt & 0x3F
    arr = np.ascontiguousarray(rgba[:h, :w])
    if arr.shape[0] != h or arr.shape[1] != w:
        pad = np.zeros((h, w, 4), np.uint8)
        pad[:arr.shape[0], :arr.shape[1]] = arr[:h, :w]
        arr = pad

    if f == 0x1a:
        return arr.tobytes()
    if f == 0x01:                                   # R8 / L8: luminance
        g = (arr[:, :, :3].astype(np.uint32).sum(axis=2) // 3).astype(np.uint8)
        return g.tobytes()
    if f == 0x07:                                   # R8_G8 == A8L8; see decode_rgba
        out = np.empty((arr.shape[0], arr.shape[1], 2), np.uint8)
        out[:, :, 0] = (arr[:, :, :3].astype(np.uint32).sum(axis=2) // 3).astype(np.uint8)
        out[:, :, 1] = arr[:, :, 3]
        return out.tobytes()

    if f in _GX2_TO_PIL_DDS:
        import io as _io
        pf, mode = _GX2_TO_PIL_DDS[f]
        # BCn works on whole 4x4 blocks; pad up so the last partial block is well-defined.
        bw, bh = ((w + 3) // 4) * 4, ((h + 3) // 4) * 4
        if (bw, bh) != (w, h):
            p = np.zeros((bh, bw, 4), np.uint8)
            p[:h, :w] = arr
            arr = p
        im = Image.fromarray(arr, 'RGBA')
        if mode == 'RGB':
            im = im.convert('RGB')
        buf = _io.BytesIO()
        im.save(buf, format='DDS', pixel_format=pf)
        blob = buf.getvalue()
        off = _DDS_HDR + (_DDS_DX10_EXTRA if blob[84:88] == b'DX10' else 0)
        payload = blob[off:]
        expect = ((bw + 3) // 4) * ((bh + 3) // 4) * (8 if f == 0x31 else 16)
        if len(payload) != expect:
            raise ImageEncodeError('Pillow produced %d B for %dx%d %s, expected %d'
                                   % (len(payload), bw, bh, pf, expect))
        return payload

    raise ImageEncodeError('no encoder for GX2 format 0x%X (%s)'
                           % (f, FORMAT_LABEL.get(f, 'unknown')))


def encode_payload(rgba, width, height, gfmt, levels=1, tile_mode=None):
    """(h,w,4) RGBA -> a complete GX2-tiled ipak part payload.

    Reproduces ipak_stream.tile_part_payload's layout exactly, because the console reads this
    buffer with the offsets the stock linker would have produced:
        allocation = align(imageSize + mipSize, 0x2000), zero filled
        level 0 at 0, level 1 at imageSize, level n>1 at imageSize + mip_offs[n-1]
    Mips are generated by successive box-filter halving when `levels` > 1.
    """
    from PIL import Image
    if tile_mode is None:
        tile_mode = base_tile_mode(gfmt, width, height)
    levels = max(1, int(levels or 1))
    img_size, mip_offs, mip_size, infos = gx.mip_chain(gfmt, width, height, tile_mode, levels)
    total = (img_size + mip_size + 0x1FFF) & ~0x1FFF
    buf = bytearray(total)

    src = Image.fromarray(np.ascontiguousarray(rgba[:, :, :4]), 'RGBA')
    for i in range(min(levels, len(infos))):
        lw, lh = max(1, width >> i), max(1, height >> i)
        lvl = src if i == 0 else src.resize((lw, lh), Image.LANCZOS)
        tight = encode_surface(np.asarray(lvl, np.uint8), lw, lh, gfmt)
        inf = infos[i]
        lin = gx.pad_linear(tight, lw, lh, gfmt, inf.pitch, inf.height)
        tiled = gx.tile(lin, lw, lh, gfmt, inf.tile_mode, pitch=inf.pitch)
        at = 0 if i == 0 else img_size + (0 if i == 1 else mip_offs[i - 1])
        buf[at:at + len(tiled)] = tiled
    return bytes(buf)


# -------------------------------------------------------------------------- selftest ---

def selftest(verbose=True):
    """Gate the fast path against the reference implementation. Returns (passed, failed)."""
    import io as _io
    log = _io.StringIO()
    ok = bad = 0

    def check(name, cond, detail=''):
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            log.write('FAIL %s %s\n' % (name, detail))

    rng = np.random.default_rng(1234)
    cases = [(0x31, 64, 64), (0x31, 256, 128), (0x33, 128, 128), (0x33, 1024, 512),
             (0x32, 64, 32), (0x35, 128, 256), (0x1a, 64, 64), (0x01, 32, 32),
             (0x31, 16, 16), (0x33, 8, 8), (0x1a, 4, 4), (0x31, 300, 140)]

    for gfmt, w, h in cases:
        for tm in (2, 4):
            inf = gx.surface_info(gfmt, w, h, tm)
            data = rng.integers(0, 256, inf.size, dtype=np.uint8).tobytes()

            # 1. index map == scalar reference, element by element
            bpe = inf.bpp >> 3
            h_pad = len(data) * 8 // (inf.pitch * inf.bpp)
            idx = _index_map(inf.bpp, inf.pitch, h_pad, tm, 0, 0, 0)
            mism = 0
            for yy in range(0, h_pad, max(1, h_pad // 8)):
                for xx in range(0, inf.pitch, max(1, inf.pitch // 8)):
                    ref = gx.element_address(xx, yy, inf.bpp, inf.pitch, tm, 0, 0, 0)
                    if int(idx[yy * inf.pitch + xx]) * bpe != ref:
                        mism += 1
            check('indexmap 0x%X %dx%d tm%d' % (gfmt, w, h, tm), mism == 0,
                  '%d mismatched elements' % mism)

            # 2. detile bytes == reference detile
            mine = detile(data, w, h, gfmt, tm)
            ref = gx.detile(data, w, h, gfmt, tm)
            check('detile 0x%X %dx%d tm%d' % (gfmt, w, h, tm), mine == ref,
                  'len %d vs %d' % (len(mine), len(ref)))

            # 3. crop == reference crop
            mc = crop_linear(mine, w, h, gfmt, inf.pitch)
            rc = gx.crop_linear(ref, w, h, gfmt, inf.pitch)
            check('crop 0x%X %dx%d tm%d' % (gfmt, w, h, tm), mc == rc)

            # 4. decode == reference decode (only where a reference exists)
            if (gfmt & 0x3F) in (0x31, 0x32, 0x33, 0x1a, 0x01, 0x07):
                mine_px = decode_rgba(mc, w, h, gfmt).tobytes()
                ref_px = gx.decode_to_rgba(rc, w, h, gfmt)
                if mine_px != ref_px:
                    d = sum(1 for a, b in zip(mine_px, ref_px) if a != b)
                    check('decode 0x%X %dx%d tm%d' % (gfmt, w, h, tm), False,
                          '%d/%d bytes differ' % (d, len(ref_px)))
                else:
                    check('decode 0x%X %dx%d tm%d' % (gfmt, w, h, tm), True)
            else:
                # BC4/BC5 have no reference decoder: structural check only, stated as such.
                px = decode_rgba(mc, w, h, gfmt)
                check('decode-shape 0x%X %dx%d tm%d' % (gfmt, w, h, tm),
                      px.shape == (h, w, 4) and px.dtype == np.uint8, repr(px.shape))

    if verbose:
        print(log.getvalue(), end='')
        print('ipak_image selftest: %d passed, %d failed' % (ok, bad))
    return ok, bad


if __name__ == '__main__':
    import sys
    sys.exit(0 if selftest()[1] == 0 else 1)
