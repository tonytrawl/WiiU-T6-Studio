#!/usr/bin/env python3
"""
WP-F: GX2 (Wii U / Latte, GPU7) texture de-tiling and tiling.

Converts a tiled GX2 surface (as stored in a console GfxImage, section 0f of
WIIU_UNLINK_STATUS.md) to a linear surface and back. Covers the tile modes and
formats that occur in the genuine BO2 Wii U zones (common_mp, mp_raid,
zm_transit): tileMode 1 (linear aligned), 2 (1D micro-tiled thin1) and
4 (2D macro-tiled thin1), formats BC1(0x31)/BC2(0x32)/BC3(0x33)/BC5(0x35),
RGBA8(0x1a), R8(0x01), R5G6B5(0x07). aa=0 (single sample) everywhere in the
corpus. The addressing math is the AMD R600-era addrlib algorithm with the
Latte parameters (2 pipes, 4 banks, 256 B pipe interleave, 2048 B split size),
the same math implemented by Cemu, decaf-emu and GTX Extractor.

API:
  info = surface_info(fmt, width, height, tile_mode, level)   # pitch, padded
  linear = detile(data, width, height, fmt, tile_mode, swizzle, pitch)
  tiled  = tile(linear, width, height, fmt, tile_mode, swizzle, pitch)
detile/tile work on the FULL padded surface (pitch x padded height) so they
are exact inverses over the whole imageSize buffer; use crop_linear /
pad_linear to go to and from the tight width x height pixel rectangle.

Verification harness: python gx2_texture.py [zone ...]
  For every inline (non-streamed) GfxImage in the zone(s):
   1. recompute pitch / padded size with surface_info and require an exact
      match with the stored GX2Texture pitch and imageSize,
   2. check the address mapping is a bijection onto [0, imageSize),
   3. round-trip detile -> tile and require byte-exact identity,
   4. recompute the whole mip chain layout and require the stored
      mipLevelOffset[] words and mipSize to match exactly.
Run with --dump <name> to write a PNG of a named image for visual checks.

Style: plain, factual, no em dashes.
"""
import struct
import sys
import os
import zlib
from collections import Counter

# Latte / GPU7 addressing parameters
NUM_PIPES = 2
NUM_BANKS = 4
PIPE_INTERLEAVE_BYTES = 256
SPLIT_SIZE = 2048

FOLLOW = 0xFFFFFFFF

# bits per element for the GX2 formats present in the corpus (element = pixel,
# or one 4x4 block for BCn)
BPP_BY_FORMAT = {
    0x01: 8,     # GX2_SURFACE_FORMAT_UNORM_R8
    0x02: 8,
    0x07: 16,    # R5G6B5
    0x08: 16,    # R5G5B5A1
    0x0a: 16,    # R4G4B4A4
    0x19: 32,
    0x1a: 32,    # R8G8B8A8
    0x31: 64,    # BC1: 8 bytes per 4x4 block
    0x32: 128,   # BC2
    0x33: 128,   # BC3
    0x34: 64,    # BC4
    0x35: 128,   # BC5
}
BCN_FORMATS = frozenset((0x31, 0x32, 0x33, 0x34, 0x35))


def is_bcn(fmt):
    return (fmt & 0x3F) in BCN_FORMATS


def block_dims(fmt, width, height):
    """Convert pixel dims to element dims (4x4 blocks for BCn)."""
    if is_bcn(fmt):
        return (width + 3) // 4, (height + 3) // 4
    return width, height


def pow2_align(x, a):
    return (x + a - 1) & ~(a - 1)


def next_pow2(x):
    p = 1
    while p < x:
        p <<= 1
    return p


def surface_thickness(tile_mode):
    if tile_mode in (3, 7, 11, 13, 15):
        return 4
    if tile_mode in (16, 17):
        return 8
    return 1


def macro_tile_dims(tile_mode):
    """Macro tile size in elements for the 2D tile modes."""
    if tile_mode in (5, 9):
        return 16, 32
    if tile_mode in (6, 10):
        return 8, 64
    return 32, 16


# ---------------------------------------------------------------------------
# element address inside a micro tile
# ---------------------------------------------------------------------------

def pixel_index_in_micro_tile(x, y, bpp):
    """Element index inside the 8x8 micro tile, non-depth, thin surface."""
    x0 = x & 1
    x1 = (x >> 1) & 1
    x2 = (x >> 2) & 1
    y0 = y & 1
    y1 = (y >> 1) & 1
    y2 = (y >> 2) & 1
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
    return (bits[0] | (bits[1] << 1) | (bits[2] << 2)
            | (bits[3] << 3) | (bits[4] << 4) | (bits[5] << 5))


# ---------------------------------------------------------------------------
# per-element byte address, thin 2D surfaces, aa=0, not depth
# ---------------------------------------------------------------------------

def addr_linear(x, y, bpp, pitch):
    return (y * pitch + x) * bpp >> 3


def addr_micro_tiled(x, y, bpp, pitch):
    """tileMode 2 (1D_TILED_THIN1). 8x8 element micro tiles, row-major."""
    micro_tile_bytes = (64 * bpp) >> 3
    micro_tiles_per_row = pitch >> 3
    tile_offset = micro_tile_bytes * ((x >> 3) + (y >> 3) * micro_tiles_per_row)
    return tile_offset + ((bpp * pixel_index_in_micro_tile(x, y, bpp)) >> 3)


def tile_rotation(tile_mode):
    """Bank/pipe rotation per slice (reference
    computeSurfaceRotationFromTileMode)."""
    if 4 <= tile_mode <= 11:
        return 2
    if 12 <= tile_mode <= 15:
        return 1
    return 0


def addr_macro_tiled(x, y, bpp, pitch, tile_mode, pipe_swizzle, bank_swizzle,
                     slice_index=0):
    """tileMode 4..7 thin (2D_TILED). Latte: 2 pipes, 4 banks, 256 B groups.
    Returns the FACE-LOCAL address (the caller adds slice offsets); the slice
    index still rotates the bank/pipe selection."""
    micro_tile_bytes = (64 * bpp) >> 3
    elem_offset = (bpp * pixel_index_in_micro_tile(x, y, bpp)) >> 3

    pipe = ((y >> 3) ^ (x >> 3)) & 1
    bank = (((y >> 5) ^ (x >> 3)) & 1) | (2 * (((y >> 4) ^ (x >> 4)) & 1))
    bank_pipe = (pipe + 2 * bank) ^ (pipe_swizzle + 2 * bank_swizzle
                                     + slice_index * tile_rotation(tile_mode))
    bank_pipe %= NUM_PIPES * NUM_BANKS
    pipe = bank_pipe % NUM_PIPES
    bank = bank_pipe // NUM_PIPES

    macro_w, macro_h = macro_tile_dims(tile_mode)
    macro_tiles_per_row = pitch // macro_w
    macro_tile_bytes = (bpp * macro_h * macro_w) >> 3
    macro_offset = ((x // macro_w) + macro_tiles_per_row * (y // macro_h)) * macro_tile_bytes

    total = elem_offset + (macro_offset >> 3)
    return (bank << 9) | (pipe << 8) | (total & 255) | ((total & ~255) << 3)


def element_address(x, y, bpp, pitch, tile_mode, pipe_swizzle=0, bank_swizzle=0,
                    slice_index=0):
    if tile_mode in (0, 1, 16):
        return addr_linear(x, y, bpp, pitch)
    if tile_mode in (2, 3):
        return addr_micro_tiled(x, y, bpp, pitch)
    return addr_macro_tiled(x, y, bpp, pitch, tile_mode, pipe_swizzle,
                            bank_swizzle, slice_index)


# ---------------------------------------------------------------------------
# surface info (pitch and padded height the GX2 linker used)
# ---------------------------------------------------------------------------

class SurfInfo(object):
    __slots__ = ('pitch', 'height', 'size', 'tile_mode', 'bpp',
                 'base_align', 'pitch_align', 'height_align')

    def __repr__(self):
        return ('SurfInfo(pitch=%d, height=%d, size=0x%x, tileMode=%d, bpp=%d, '
                'baseAlign=0x%x)' % (self.pitch, self.height, self.size,
                                     self.tile_mode, self.bpp, self.base_align))


def _mip_tile_mode(base_tile_mode, bpp, level, width_e, height_e):
    """Tile mode degradation for small mip levels (thin, aa=0, not depth).
    A mip level whose padded dims no longer fill a macro tile drops from
    2D tiling to 1D micro tiling (reference computeSurfaceMipLevelTileMode)."""
    tm = base_tile_mode
    if tm == 3:  # thick modes collapse for single-slice surfaces
        tm = 2
    if tm == 7:
        tm = 4
    if level == 0:
        return tm
    w = next_pow2(width_e)
    h = next_pow2(height_e)
    micro_tile_bytes = (64 * bpp) >> 3
    width_align_factor = max(1, 256 // micro_tile_bytes) if micro_tile_bytes < 256 else 1
    if tm in (4, 5, 6):
        macro_w, macro_h = macro_tile_dims(tm)
        if w < width_align_factor * macro_w or h < macro_h:
            tm = 2
    return tm


def surface_info(fmt, width, height, tile_mode, level=0):
    """Pitch (elements), padded height (elements), byte size and effective
    tile mode of one mip level of a 2D aa=0 texture."""
    bpp = BPP_BY_FORMAT[fmt & 0x3F]
    w = max(1, width >> level)
    h = max(1, height >> level)
    if level:
        w = next_pow2(w)
        h = next_pow2(h)
    we, he = block_dims(fmt, w, h)

    tm = _mip_tile_mode(tile_mode, bpp, level, we, he)

    out = SurfInfo()
    out.bpp = bpp
    out.tile_mode = tm
    if tm in (0, 16):
        pitch_align, height_align, base_align = (1 if bpp != 1 else 8), 1, 1
    elif tm == 1:
        pitch_align = max(0x40, 2048 // bpp)
        height_align = 1
        base_align = PIPE_INTERLEAVE_BYTES
    elif tm in (2, 3):
        # reference computeSurfaceAlignmentsMicroTiled (numSamples=1, thin)
        pitch_align = max(8, 256 // bpp)
        height_align = 8
        base_align = PIPE_INTERLEAVE_BYTES
    else:
        # reference computeSurfaceAlignmentsMacroTiled (numSamples=1, thin)
        macro_w, macro_h = macro_tile_dims(tm)
        pitch_align = max(macro_w, macro_w * (256 // bpp // 8))
        height_align = macro_h
        macro_tile_bytes = (bpp * macro_h * macro_w + 7) >> 3
        base_align = max(macro_tile_bytes, (height_align * bpp * pitch_align + 7) >> 3)

    out.pitch = pow2_align(we, pitch_align)
    out.height = pow2_align(he, height_align)
    out.size = (out.pitch * out.height * bpp) >> 3
    out.base_align = base_align
    out.pitch_align = pitch_align
    out.height_align = height_align
    return out


def mip_chain(fmt, width, height, tile_mode, mips, depth=1):
    """Byte offsets of each level in the GX2 layout. Returns
    (image_size, mip_offsets, mip_size, infos). mip_offsets follows the
    GX2Texture.mipLevelOffset convention: entry for level 1 is the offset of
    level 1 data from the START OF THE MIP BUFFER... except the first entry,
    which GX2 stores as the offset of the whole mip buffer from the image
    base when mipLevelOffset[0] is used to place the buffer. Empirically in
    BO2 zones mipLevelOffset[i] for i>=1 are offsets of level i+1 relative to
    the mip buffer start, and mipLevelOffset[0] equals imageSize (mip buffer
    directly appended). Cube maps (depth=6): level 0 stores 6 faces, mip
    levels pad the slice count to nextPow2(6)=8. Verified by the harness."""
    infos = [surface_info(fmt, width, height, tile_mode, lv) for lv in range(mips)]
    slices0 = depth if depth > 1 else 1
    slices_mip = next_pow2(slices0) if slices0 > 1 else 1
    image_size = infos[0].size * slices0
    offs = []
    pos = 0
    for lv in range(1, mips):
        inf = infos[lv]
        pos = pow2_align(pos, inf.base_align)
        offs.append(pos)
        pos += inf.size * slices_mip
    return image_size, offs, pos, infos


# ---------------------------------------------------------------------------
# detile / tile (full padded surface, exact inverses)
# ---------------------------------------------------------------------------

def _swizzle_bits(swizzle):
    pipe_sw = (swizzle >> 8) & 1
    bank_sw = (swizzle >> 9) & 3
    return pipe_sw, bank_sw


def detile(data, width, height, fmt, tile_mode, swizzle=0, pitch=None,
           slice_index=0):
    """Tiled surface bytes -> linear (row-major, pitch x padded-height
    elements). width/height are PIXEL dims of the level; pitch overrides the
    computed pitch when the GX2Texture supplies one (level 0). For cube maps
    pass one face at a time with its slice_index (0..5)."""
    inf = surface_info(fmt, width, height, tile_mode)
    if pitch is None:
        pitch = inf.pitch
    bpp = inf.bpp
    bpe = bpp >> 3
    h_pad = len(data) * 8 // (pitch * bpp)
    pipe_sw, bank_sw = _swizzle_bits(swizzle)
    out = bytearray(pitch * h_pad * bpe)
    tm = tile_mode
    for y in range(h_pad):
        row = y * pitch
        for x in range(pitch):
            src = element_address(x, y, bpp, pitch, tm, pipe_sw, bank_sw,
                                  slice_index)
            dst = (row + x) * bpe
            out[dst:dst + bpe] = data[src:src + bpe]
    return bytes(out)


def tile(data, width, height, fmt, tile_mode, swizzle=0, pitch=None,
         slice_index=0):
    """Linear (pitch x padded-height, row-major) -> tiled surface bytes.
    Exact inverse of detile over the same buffer size."""
    inf = surface_info(fmt, width, height, tile_mode)
    if pitch is None:
        pitch = inf.pitch
    bpp = inf.bpp
    bpe = bpp >> 3
    h_pad = len(data) * 8 // (pitch * bpp)
    pipe_sw, bank_sw = _swizzle_bits(swizzle)
    out = bytearray(pitch * h_pad * bpe)
    tm = tile_mode
    for y in range(h_pad):
        row = y * pitch
        for x in range(pitch):
            dst = element_address(x, y, bpp, pitch, tm, pipe_sw, bank_sw,
                                  slice_index)
            src = (row + x) * bpe
            out[dst:dst + bpe] = data[src:src + bpe]
    return bytes(out)


def crop_linear(linear, width, height, fmt, pitch):
    """Padded linear surface -> tight width x height element rectangle."""
    we, he = block_dims(fmt, width, height)
    bpe = BPP_BY_FORMAT[fmt & 0x3F] >> 3
    rows = []
    for y in range(he):
        o = y * pitch * bpe
        rows.append(linear[o:o + we * bpe])
    return b''.join(rows)


def pad_linear(tight, width, height, fmt, pitch, padded_height):
    """Tight width x height element rectangle -> padded linear surface,
    padding bytes zero."""
    we, he = block_dims(fmt, width, height)
    bpe = BPP_BY_FORMAT[fmt & 0x3F] >> 3
    out = bytearray(pitch * padded_height * bpe)
    for y in range(he):
        src = y * we * bpe
        dst = y * pitch * bpe
        out[dst:dst + we * bpe] = tight[src:src + we * bpe]
    return bytes(out)


# ---------------------------------------------------------------------------
# console GfxImage scan (section 0f layout) for the harness
# ---------------------------------------------------------------------------

NAME_CHARS = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_/~$#&+.-")


def _name_run(d, o, minlen=2, maxlen=112):
    e = o
    n = len(d)
    while e < n and d[e] in NAME_CHARS and e - o <= maxlen:
        e += 1
    if e - o < minlen or e >= n or d[e] != 0:
        return None
    return d[o:e].decode('latin-1'), e + 1


def scan_zone_images(d):
    """Yield dicts for every console GfxImage body in a big-endian zone.
    Inline images (streaming==0, pixels FOLLOW) include the pixel bytes."""
    out = []
    o = d.find(b'\xff\xff\xff\xff')
    n = len(d)
    le32 = lambda p: struct.unpack('<I', d[p:p + 4])[0]
    be32 = lambda p: struct.unpack('>I', d[p:p + 4])[0]
    be16 = lambda p: struct.unpack('>H', d[p:p + 2])[0]
    while o != -1 and o + 12 < n:
        body = o - 320
        if body >= 0 and body + 340 < n:
            r = _name_run(d, body + 328)
            if r:
                name, name_end = r
                dim = le32(body); w = le32(body + 4); h = le32(body + 8)
                depth = le32(body + 12)
                mips = le32(body + 16); fmt = le32(body + 20); aa = le32(body + 24)
                isz = le32(body + 32); mip_size = le32(body + 40)
                tm = le32(body + 48); sw = le32(body + 52)
                align = le32(body + 56); pitch = le32(body + 60)
                base_size = be32(body + 160)
                streaming = d[body + 171]
                if (w == be16(body + 164) and h == be16(body + 166)
                        and 0 < w <= 8192 and 0 < h <= 8192
                        and dim <= 7 and tm <= 16 and mips <= 14):
                    img = dict(off=body, name=name, dim=dim, depth=depth, w=w, h=h,
                               mips=mips, fmt=fmt, aa=aa, imageSize=isz,
                               mipSize=mip_size, tileMode=tm, swizzle=sw,
                               align=align, pitch=pitch, baseSize=base_size,
                               streaming=streaming,
                               mipOffsets=[le32(body + 64 + 4 * i) for i in range(13)],
                               pixels=None)
                    if streaming == 0 and be32(body + 176) == FOLLOW and dim:
                        img['pixels'] = d[name_end:name_end + base_size]
                    out.append(img)
        o = d.find(b'\xff\xff\xff\xff', o + 4)
    return out


# ---------------------------------------------------------------------------
# minimal BCn / raw decoders and PNG writer for visual verification
# ---------------------------------------------------------------------------

def _bc1_block(block, out, ox, oy, w, h, alpha_from_bc3=None):
    c0, c1, bits = struct.unpack('<HHI', block)
    def col(c):
        r = (c >> 11) & 31; g = (c >> 5) & 63; b = c & 31
        return ((r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31)
    p0, p1 = col(c0), col(c1)
    if c0 > c1 or alpha_from_bc3 is not None:
        pal = [p0, p1,
               tuple((2 * a + b) // 3 for a, b in zip(p0, p1)),
               tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]
        alphas = [255, 255, 255, 255]
    else:
        pal = [p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1)), (0, 0, 0)]
        alphas = [255, 255, 255, 0]
    for py in range(4):
        for px in range(4):
            X, Y = ox + px, oy + py
            if X >= w or Y >= h:
                continue
            i = (bits >> (2 * (4 * py + px))) & 3
            a = alpha_from_bc3[4 * py + px] if alpha_from_bc3 is not None else alphas[i]
            o = 4 * (Y * w + X)
            out[o:o + 4] = bytes(pal[i] + (a,))


def _bc3_alpha(ab):
    a0, a1 = ab[0], ab[1]
    bits = int.from_bytes(ab[2:8], 'little')
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


def decode_to_rgba(linear_tight, w, h, fmt):
    """Decode a tight linear surface to RGBA8 bytes (enough for eyeballing)."""
    fmt &= 0x3F
    out = bytearray(4 * w * h)
    if fmt == 0x1a:
        for i in range(w * h):
            out[4 * i:4 * i + 4] = linear_tight[4 * i:4 * i + 4]
    elif fmt == 0x01:
        for i in range(w * h):
            v = linear_tight[i]
            out[4 * i:4 * i + 4] = bytes((v, v, v, 255))
    elif fmt == 0x07:
        for i in range(w * h):
            c = struct.unpack_from('<H', linear_tight, 2 * i)[0]
            r = ((c >> 11) & 31) * 255 // 31
            g = ((c >> 5) & 63) * 255 // 63
            b = (c & 31) * 255 // 31
            out[4 * i:4 * i + 4] = bytes((r, g, b, 255))
    elif fmt in (0x31, 0x32, 0x33):
        wb, hb = (w + 3) // 4, (h + 3) // 4
        bs = 8 if fmt == 0x31 else 16
        for by in range(hb):
            for bx in range(wb):
                blk = linear_tight[(by * wb + bx) * bs:(by * wb + bx) * bs + bs]
                if fmt == 0x31:
                    _bc1_block(blk, out, 4 * bx, 4 * by, w, h)
                elif fmt == 0x33:
                    _bc1_block(blk[8:], out, 4 * bx, 4 * by, w, h,
                               alpha_from_bc3=_bc3_alpha(blk[:8]))
                else:  # BC2: explicit 4-bit alpha
                    al = []
                    for i in range(16):
                        v = blk[i // 2]
                        a = (v >> 4) if (i & 1) else (v & 15)
                        al.append(a * 17)
                    _bc1_block(blk[8:], out, 4 * bx, 4 * by, w, h, alpha_from_bc3=al)
    else:
        raise ValueError('no decoder for fmt 0x%x' % fmt)
    return bytes(out)


def write_png(path, rgba, w, h):
    def chunk(tag, payload):
        c = tag + payload
        return struct.pack('>I', len(payload)) + c + struct.pack('>I', zlib.crc32(c))
    raw = b''.join(b'\x00' + rgba[4 * w * y:4 * w * (y + 1)] for y in range(h))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 6))
           + chunk(b'IEND', b''))
    open(path, 'wb').write(png)


# ---------------------------------------------------------------------------
# verification harness
# ---------------------------------------------------------------------------

def verify_image(img):
    """Run all structural checks on one inline image. Returns list of
    failure strings (empty = pass)."""
    fails = []
    fmt, tm, sw = img['fmt'], img['tileMode'], img['swizzle']
    w, h, pitch = img['w'], img['h'], img['pitch']
    if (fmt & 0x3F) not in BPP_BY_FORMAT:
        return ['unsupported format 0x%x' % fmt]
    if img['aa']:
        return ['aa=%d unsupported' % img['aa']]

    n_faces = 6 if img.get('dim') == 3 else 1
    inf = surface_info(fmt, w, h, tm)
    if inf.pitch != pitch:
        fails.append('pitch: computed %d, stored %d' % (inf.pitch, pitch))
    if inf.size * n_faces != img['imageSize']:
        fails.append('imageSize: computed 0x%x, stored 0x%x'
                     % (inf.size * n_faces, img['imageSize']))
    if inf.tile_mode != tm:
        fails.append('tileMode: computed %d, stored %d' % (inf.tile_mode, tm))

    # mip chain layout vs stored mipLevelOffset words
    if img['mips'] > 1:
        _, offs, mip_size, _ = mip_chain(fmt, w, h, tm, img['mips'],
                                         depth=img.get('depth', 1) if n_faces > 1 else 1)
        stored = img['mipOffsets']
        # convention check: stored[0] is the offset of mip 1 from the image
        # base (= imageSize when the mip buffer is appended directly),
        # stored[i>=1] are offsets of level i+1 from the mip buffer start
        exp = [img['imageSize'] + offs[0]] + offs[1:]
        got = stored[:img['mips'] - 1]
        if exp != got:
            # alternate convention: all relative to mip buffer start
            if offs != got:
                fails.append('mipOffsets: computed %s / %s, stored %s'
                             % (exp, offs, got))
        if img['mipSize'] and img['mipSize'] != mip_size:
            fails.append('mipSize: computed 0x%x, stored 0x%x'
                         % (mip_size, img['mipSize']))

    data = img['pixels']
    if data is None:
        return fails
    if len(data) < img['imageSize']:
        fails.append('pixel blob short: %d < imageSize 0x%x'
                     % (len(data), img['imageSize']))
        return fails
    face_size = inf.size
    bpe = inf.bpp >> 3
    h_pad = face_size // (pitch * bpe)
    pipe_sw, bank_sw = _swizzle_bits(sw)
    for face in range(n_faces):
        base = data[face * face_size:(face + 1) * face_size]
        # bijection check on the face-local address map
        seen = bytearray(face_size)
        ok = True
        for y in range(h_pad):
            for x in range(pitch):
                a = element_address(x, y, inf.bpp, pitch, tm,
                                    pipe_sw, bank_sw, face)
                if a + bpe > face_size:
                    fails.append('face %d: address 0x%x out of range at (%d,%d)'
                                 % (face, a, x, y))
                    ok = False
                    break
                if seen[a]:
                    fails.append('face %d: address collision at (%d,%d) addr 0x%x'
                                 % (face, x, y, a))
                    ok = False
                    break
                seen[a] = 1
            if not ok:
                break
        if ok and not all(seen[i] for i in range(0, face_size, bpe)):
            fails.append('face %d: address map does not cover the buffer' % face)
            ok = False
        # round trip
        if ok:
            lin = detile(base, w, h, fmt, tm, sw, pitch, face)
            back = tile(lin, w, h, fmt, tm, sw, pitch, face)
            if back != base:
                fails.append('face %d: round-trip mismatch' % face)
        if fails:
            break
    return fails


def run_harness(zone_paths, dump_name=None, dump_dir='.', dump_mips=False):
    total = passed = 0
    combo_pass = Counter()
    combo_fail = Counter()
    for zp in zone_paths:
        d = open(zp, 'rb').read()
        imgs = scan_zone_images(d)
        inline = [i for i in imgs if i['pixels'] is not None]
        print('%s: %d GfxImage bodies, %d inline with pixels'
              % (zp, len(imgs), len(inline)))
        for img in inline:
            key = (img['tileMode'], img['fmt'])
            fails = verify_image(img)
            total += 1
            if fails:
                combo_fail[key] += 1
                print('  FAIL %-40s %4dx%-4d fmt=0x%02x tm=%d sw=0x%x: %s'
                      % (img['name'], img['w'], img['h'], img['fmt'],
                         img['tileMode'], img['swizzle'], '; '.join(fails)))
            else:
                passed += 1
                combo_pass[key] += 1
            if dump_name and img['name'] == dump_name:
                _dump_image(img, dump_dir, dump_mips)
    print('\n%d/%d inline images pass all checks '
          '(pitch+imageSize+mipOffsets exact, address bijection, '
          'detile->tile byte-exact)' % (passed, total))
    print('per (tileMode, format):')
    for k in sorted(set(combo_pass) | set(combo_fail)):
        print('  tm=%d fmt=0x%02x: pass %d fail %d'
              % (k[0], k[1], combo_pass[k], combo_fail[k]))
    return passed == total


def _dump_image(img, dump_dir, dump_mips=False):
    fmt, tm, sw = img['fmt'], img['tileMode'], img['swizzle']
    w, h, pitch = img['w'], img['h'], img['pitch']
    base = img['pixels'][:img['imageSize']]
    lin = detile(base, w, h, fmt, tm, sw, pitch)
    tight = crop_linear(lin, w, h, fmt, pitch)
    rgba = decode_to_rgba(tight, w, h, fmt)
    path = os.path.join(dump_dir, '%s.png' % img['name'].replace('/', '_'))
    write_png(path, rgba, w, h)
    print('  wrote %s (%dx%d fmt=0x%x tm=%d)' % (path, w, h, fmt, tm))
    if dump_mips and img['mips'] > 1:
        _, offs, mip_size, infos = mip_chain(fmt, w, h, tm, img['mips'])
        mipbuf = img['pixels'][img['imageSize']:]
        for lv in range(1, img['mips']):
            inf = infos[lv]
            o = offs[lv - 1]
            mdata = mipbuf[o:o + inf.size]
            if len(mdata) < inf.size:
                print('  mip %d: blob short, skip' % lv)
                continue
            mw, mh = max(1, w >> lv), max(1, h >> lv)
            mlin = detile(mdata, next_pow2(mw), next_pow2(mh), fmt,
                          inf.tile_mode, sw, inf.pitch)
            mtight = crop_linear(mlin, mw, mh, fmt, inf.pitch)
            rgba = decode_to_rgba(mtight, mw, mh, fmt)
            path = os.path.join(dump_dir, '%s.mip%d.png'
                                % (img['name'].replace('/', '_'), lv))
            write_png(path, rgba, mw, mh)
            print('  wrote %s (%dx%d tm=%d)' % (path, mw, mh, inf.tile_mode))


def main():
    args = [a for a in sys.argv[1:]]
    dump = None
    dump_dir = '.'
    dump_mips = False
    zones = []
    i = 0
    while i < len(args):
        if args[i] == '--dump':
            dump = args[i + 1]
            i += 2
        elif args[i] == '--dump-dir':
            dump_dir = args[i + 1]
            i += 2
        elif args[i] == '--mips':
            dump_mips = True
            i += 1
        else:
            zones.append(args[i])
            i += 1
    if not zones:
        here = os.path.dirname(os.path.abspath(__file__))
        zones = [os.path.join(here, '..', 'common_mp.zone'),
                 os.path.join(here, 'mp_raid_genuine.zone'),
                 os.path.join(here, 'zm_transit_original.zone')]
    ok = run_harness(zones, dump, dump_dir, dump_mips)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
