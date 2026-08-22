"""core.zone_images -- the textures that live INSIDE a fastfile, not in an ipak.

WHAT THIS IS FOR
----------------
A zone carries two kinds of image. A STREAMED one is a stub: its pixels live in an .ipak and are
bound at runtime by (nameHash, dataHash). An INLINE one carries its pixels in the .ff itself.
Measured: common_zm.ff is 293 inline of 391 records, common_mp.ff 836 of 1684 -- mostly FX
textures. Those are viewable and replaceable from the zone alone, and nothing else in the studio
could see them.

⚠ MOST IMAGES ARE NOT TOP-LEVEL ASSETS. common_zm lists `IMAGE 4` in its asset table while the
census finds 391 records, because images are embedded inside Materials, FX and XModel-inline
materials. So this module locates them structurally (via native_linker/gfximage_census, the
known-good reader) rather than by walking the asset list, which would find almost none of them.

MEASURED FACTS THIS RELIES ON (probe, 2026-08-15, common_zm.ff)
---------------------------------------------------------------
* Inline pixels are GX2-TILED, exactly like an ipak part payload, so core.ipak_image decodes and
  encodes them unchanged. Scored tiled-vs-linear on 10 textures by neighbour correlation:
  9 tiled, 0 linear, 1 tie (e.g. fxt_light_phosphorous 0.999 tiled vs 0.830 linear). A wrong
  tiling interpretation scrambles 4x4 blocks and collapses that score, so the test can fail.

* THE PIXEL BUFFER IS `base_size` LONG, NOT `image_size`. Confirmed on all 14 records examined:
  extent - pixel_start == base_size every time. `image_size` is level 0 only; `base_size` is the
  whole mip chain padded to 0x2000 -- e.g. reticle_center_cross_wiiu is 32x32 BC3, 1,024 bytes
  of pixels inside an 8,192-byte allocation. Using image_size would truncate every mipped
  texture and silently corrupt the tail.

* Pixels begin after the record's inline name: body+328, then skip the NUL-terminated name when
  the record carries one (an alias-named record has no inline name and starts straight at +328).

WHY REPLACE IS SAME-SIZE ONLY
-----------------------------
These pixels sit in the middle of a material or FX body, not in an addressable asset, so nothing
can re-point references around them. Re-encoding to exactly `base_size` changes no offsets, which
is the only edit that cannot disturb the pointer graph. ZoneSession.patch_raw enforces the
length and refuses to combine such a patch with a growing edit.
"""
import os
import struct
import sys

from . import paths  # noqa: F401  (installs native_linker + wiiu_ref on sys.path)
from . import ipak_image as II

import gx2_texture as gx

#: Hard ceiling on how far a texture may be enlarged. Not a formatting preference -- the whole
#: point of an inline texture is that its memory is budgeted, and 2x linear is already 4x the
#: pixels. Above this the studio refuses rather than quietly spending the console's memory.
MAX_GROWTH = 2.0

#: The pixel allocation is padded to this. It is why small textures often grow for FREE:
#: a 32x32 BC1 and a 64x64 BC1 both occupy 8,192 bytes.
ALLOC_ALIGN = 0x2000

#: 328-byte GfxImage body, mixed-endian. Only the SIZE fields are listed -- these are exactly
#: the ones a resize has to rewrite, and nothing else in the record describes the dimensions.
#: (Layout from native_linker/gfximage_census; LE for the GX2 surface header, BE for the T6 tail.)
F_LE_DIM, F_LE_WIDTH, F_LE_HEIGHT, F_LE_DEPTH = 0, 4, 8, 12
F_LE_MIPS, F_LE_IMAGESIZE = 16, 32
F_BE_BASESIZE, F_BE_W16, F_BE_H16, F_BE_LEVELS = 160, 164, 166, 170

#: ⛔ THE REST OF THE GX2 SURFACE HEADER, and it is NOT optional on a resize. The pixels are
#: tiled for a pitch and a tile mode derived from the NEW dimensions, so a record still holding
#: the OLD ones describes a different surface than the bytes that follow it and the GPU walks
#: them at the wrong stride. An earlier cut of this wrote only the eight fields above and
#: claimed "nothing else in the record describes the dimensions" -- that was wrong, and the
#: round-trip test could not see it because our own decoder RE-DERIVES the layout from the
#: dimensions instead of reading these fields, so encoder and decoder agreed with each other
#: while both disagreed with the record.
F_LE_MIPSIZE, F_LE_TILEMODE, F_LE_SWIZZLE = 40, 48, 52
F_LE_ALIGN, F_LE_PITCH, F_LE_MIPOFFS = 56, 60, 64
N_MIPOFFS = 13


class ZoneImageError(Exception):
    pass


def _census(zone):
    import gfximage_census as GC
    return GC.census(zone)


class ZoneImage(object):
    """One image record located inside a zone."""

    __slots__ = ('off', 'name', 'name_hash', 'width', 'height', 'gx2_format', 't6_format',
                 'levels', 'pixel_off', 'pixel_len', 'image_size', 'base_size',
                 'pixel_class', 'alias_named', 'part_hashes', 'depth', 'dim')

    def __init__(self, rec, zone):
        self.off = rec['off']
        self.name = rec.get('name')
        self.name_hash = rec.get('name_hash')
        self.width = rec.get('width') or 0
        self.height = rec.get('height') or 0
        self.gx2_format = rec.get('gx2_format')
        self.t6_format = rec.get('t6_format')
        self.levels = rec.get('mips') or 1
        # ⚠ A CUBE MAP IS SIX FACES (dim 3, depth 6). Dropping these sized every cube map at one
        # sixth of its real allocation, which made `resize_choices` offer a size that
        # `stage_resize` then refused -- the offer-then-refuse that fit_only exists to prevent.
        self.depth = rec.get('depth') or 1
        self.dim = rec.get('dim') or 0
        self.image_size = rec.get('image_size') or 0
        self.base_size = rec.get('base_size') or 0
        self.pixel_class = rec.get('pixel_class')
        self.alias_named = bool(rec.get('alias_named'))
        # For a STREAMED image these are the (per-part) dataHashes the loader binds by, which
        # is what lets us name the pak actually carrying the pixels.
        self.part_hashes = tuple(rec.get('part_hashes') or ())
        self.pixel_off, self.pixel_len = self._locate(zone)

    def _locate(self, zone):
        if self.pixel_class != 'inline':
            return None, 0
        start = self.off + 328
        if not self.alias_named:
            try:
                start = zone.index(b'\x00', start) + 1        # skip the inline name
            except ValueError:
                return None, 0
        length = self.base_size or self.image_size
        if not length or start + length > len(zone):
            return None, 0
        return start, length

    @property
    def inline(self):
        return self.pixel_class == 'inline' and self.pixel_off is not None

    @property
    def label(self):
        return self.name or ('(alias-named @0x%X)' % self.off)

    @property
    def dims(self):
        return '%dx%d' % (self.width, self.height)

    def __repr__(self):
        return ('ZoneImage(%r %s levels=%d %s @0x%X pixels=%s+%d)'
                % (self.name, self.dims, self.levels, self.pixel_class, self.off,
                   self.pixel_off, self.pixel_len))


def list_images(zone, inline_only=False):
    """Every GfxImage record in the zone, located. Inline ones carry pixels we can read."""
    out = []
    for rec in _census(zone):
        img = ZoneImage(rec, zone)
        if inline_only and not img.inline:
            continue
        out.append(img)
    return out


def payload(zone, img):
    """The raw tiled pixel bytes of an inline image."""
    if not img.inline:
        raise ZoneImageError('%s has no inline pixels (it is %s -- its bytes live in an ipak)'
                             % (img.label, img.pixel_class))
    return bytes(zone[img.pixel_off:img.pixel_off + img.pixel_len])


def decode(zone, img, max_side=None):
    """-> (rgba, level, (level_w, level_h)). Uses the ipak decoder; the layout is the same."""
    if img.gx2_format is None:
        raise ZoneImageError('%s declares no GX2 format' % img.label)
    return II.decode_payload(payload(zone, img), img.width, img.height, img.gx2_format,
                             max_side=max_side, levels=img.levels)


def encode_replacement(rgba, img):
    """(h,w,4) RGBA -> tiled bytes of EXACTLY img.pixel_len, or raise.

    The format and dimensions come from the record, never from the incoming picture: the GPU
    reads this buffer with the declaration stored in the zone.
    """
    if not img.inline:
        raise ZoneImageError('%s has no inline pixels to replace' % img.label)
    sized = II.fit_rgba(rgba, img.width, img.height)
    blob = II.encode_payload(sized, img.width, img.height, img.gx2_format, levels=img.levels)
    if len(blob) != img.pixel_len:
        raise ZoneImageError(
            're-encoded %s to %d bytes but its slot in the zone is %d. Refusing: a raw patch '
            'must be length-neutral or every offset after it moves.'
            % (img.label, len(blob), img.pixel_len))
    return blob


def replace(session, img, rgba):
    """Stage a replacement of this image's pixels on an open ZoneSession. -> bytes written."""
    blob = encode_replacement(rgba, img)
    return session.patch_raw(img.pixel_off, blob)


# --------------------------------------------------------------------------- resizing
#
# WHY A TEXTURE CAN GROW AT ALL
# -----------------------------
# The record's pixel pointer (+176) is FOLLOW in every inline record measured -- 840 of 840
# across faction_cd_mp and common_mp. The pixels are reached by ADJACENCY, not by a stored
# address, so nothing points AT them and enlarging them re-points nothing. What it does do is
# shift every byte after them, which is the ordinary grow the relinker already handles.
#
# So a resize is three things:
#   1. re-encode the picture at the new dimensions
#   2. rewrite the record's SIZE fields (the eight above) in place -- length-neutral
#   3. hand the pixel span to relink.grow() and let the relocation model move everything after
#
# CALIBRATION (840 inline records, faction_cd_mp + common_mp)
#   baseSize  == align(imageSize + mipSize, 0x2000)     840/840
#   pixel_len == baseSize                               840/840
#   imageSize == the PITCH-PADDED level-0 size          839/840
# The single outlier is a 192x64 BC3 whose imageSize is the tight 12,288 rather than the padded
# 16,384; its baseSize still matches. Padded is what this writes, because that is what 839 of
# 840 stock records hold.


class SizeChoice(object):
    """One offerable size for a replacement picture."""
    __slots__ = ('label', 'width', 'height', 'factor', 'image_size', 'base_size', 'delta')

    def __init__(self, label, w, h, factor, image_size, base_size, delta):
        self.label, self.width, self.height, self.factor = label, w, h, factor
        self.image_size, self.base_size, self.delta = image_size, base_size, delta

    @property
    def dims(self):
        return '%dx%d' % (self.width, self.height)

    @property
    def free(self):
        """True when the bigger picture costs no extra bytes (the 0x2000 floor absorbed it)."""
        return self.delta == 0

    def __repr__(self):
        return '<SizeChoice %s %s x%.3f %+d B>' % (self.label, self.dims, self.factor, self.delta)


def block_size(gfmt):
    """Pixel granularity of the format: 4 for the BCn block formats, 1 for uncompressed."""
    try:
        return 4 if gx.is_bcn(gfmt) else 1
    except Exception:
        return 1


def snap_dims(w, h, factor, gfmt):
    """Scale by `factor` and land on the format's block grid, preserving the aspect ratio.

    ⚠ TWO REASONS THIS IS NOT JUST `round(w * f)`.
    1. BCn compresses in 4x4 blocks, and all 803 inline BC records measured in common_mp.ff have
       both dimensions a multiple of 4. An off-grid size like 130 makes the encoder pad out to a
       132-pixel block grid while the record declares 130 -- a disagreement of exactly the kind
       this module now goes out of its way to avoid.
    2. `round()` is half-to-even, so scaling 192x64 by ~1.0078 gave 194x64: the width moved and
       the height did not, quietly changing an aspect ratio the dialog still described as a
       uniform enlargement. Snapping both axes on the same grid keeps them in step.
    """
    b = block_size(gfmt)
    nw = max(b, int(round(w * factor / b)) * b)
    nh = max(b, int(round(h * factor / b)) * b)
    return nw, nh


def surface_layout(w, h, gfmt, levels, depth=1):
    """Every layout field the record stores, recomputed for these dimensions.

    -> dict(image_size, base_size, mip_size, tile_mode, pitch, align, mip_offsets)

    Calibrated against 852 stock inline records: imageSize, mipSize, tileMode, align and pitch
    reproduce on 850, and mipOffsets on all 852. The two that differ are micro-tiled records our
    tile-mode rule calls macro; `layout_matches_record` finds them so a resize refuses instead of
    writing a header that contradicts the pixels.
    """
    lv = max(1, int(levels or 1))
    d = depth if depth and depth > 1 else 1
    tm = II.base_tile_mode(gfmt, w, h)
    img, offs, mip, infos = gx.mip_chain(gfmt, w, h, tm, lv, depth=d)
    return dict(image_size=img,
                base_size=(img + mip + ALLOC_ALIGN - 1) & ~(ALLOC_ALIGN - 1),
                mip_size=mip, tile_mode=infos[0].tile_mode, pitch=infos[0].pitch,
                align=infos[0].base_align,
                # convention measured on stock records: [0] is imageSize (the mip buffer sits
                # straight after level 0), [i>=1] is level i+1 relative to the mip buffer start
                mip_offsets=([img] + list(offs)) if lv > 1 else [])


def surface_sizes(w, h, gfmt, levels, depth=1):
    """-> (imageSize, baseSize). Thin wrapper on `surface_layout` for size-only callers."""
    L = surface_layout(w, h, gfmt, levels, depth)
    return L['image_size'], L['base_size']


def read_record_layout(zone, img):
    """The layout fields as the record ACTUALLY stores them."""
    B = img.off
    le = lambda o: struct.unpack_from('<I', zone, B + o)[0]      # noqa: E731
    return dict(image_size=le(F_LE_IMAGESIZE), mip_size=le(F_LE_MIPSIZE),
                tile_mode=le(F_LE_TILEMODE), align=le(F_LE_ALIGN), pitch=le(F_LE_PITCH),
                mip_offsets=[le(F_LE_MIPOFFS + 4 * i) for i in range(N_MIPOFFS)])


def layout_matches_record(zone, img):
    """Can we reproduce THIS record's own layout? -> (ok, [field names that differ])

    ⛔ THE GATE ON EVERY RESIZE. If our model cannot reproduce the header the stock linker wrote
    for the size the texture already is, then the header we would write for a NEW size is not
    trustworthy either -- so we refuse rather than emit a record that disagrees with its pixels.
    Measured: 850 of 852 stock records reproduce; the 2 that do not are stored micro-tiled where
    our rule picks macro.
    """
    want = surface_layout(img.width, img.height, img.gx2_format, img.levels, img.depth)
    got = read_record_layout(zone, img)
    bad = [k for k in ('image_size', 'mip_size', 'tile_mode', 'align', 'pitch')
           if want[k] != got[k]]
    stored = [x for x in got['mip_offsets'] if x]
    computed = [x for x in want['mip_offsets'] if x]
    if stored[:len(computed)] != computed[:len(stored)]:
        bad.append('mip_offsets')
    return (not bad), bad


def resize_choices(img, src_w, src_h, cap=MAX_GROWTH, fit_only=True):
    """What sizes may this picture be stored at? -> [original] or [original, enhanced].

    ⛔ `fit_only` (the default) RETURNS ONLY SIZES THAT CAN ACTUALLY BE DELIVERED -- ones that
    fit the allocation the texture already has. Offering a size and then refusing it is the
    worst of both worlds: the caller shows the user a choice, the user picks it, and the edit
    fails. Measured on patch_zm with a 1000x1000 source: 6 of 12 textures can be enlarged inside
    their existing buffer and 6 cannot, and without this flag all 12 were offered.

    A texture that needs MORE space than it has can only grow by relinking the zone, which
    `resize_zone` does and which many zones refuse (their relocation model cannot bracket the
    site). Pass `fit_only=False` to see that option; the caller then owns the refusal.

    The enhanced size is whatever the SUPPLIED PICTURE ACTUALLY SUPPORTS, capped at `cap` --
    there is no ladder of preset steps. A 32x32 texture given a 35x35 picture is offered 35x35;
    given a 58x58 it is offered 58x58; given a 640x640 it is offered 64x64, because that is the
    cap. Upscaling beyond the source would only invent detail, so the source bounds it.

    The original texture's aspect ratio is preserved: the factor is the SMALLER of the two
    source ratios, so neither axis is stretched.
    """
    if img.gx2_format is None:
        raise ZoneImageError('%s declares no GX2 format' % img.label)
    w, h = int(img.width), int(img.height)
    if w <= 0 or h <= 0:
        raise ZoneImageError('%s has no usable dimensions' % img.label)

    base_img, base_alloc = surface_sizes(w, h, img.gx2_format, img.levels, img.depth)
    have = img.pixel_len or base_alloc
    out = [SizeChoice('original', w, h, 1.0, base_img, base_alloc, 0)]

    f_max = min(float(cap), float(src_w) / w, float(src_h) / h)
    if f_max <= 1.0:
        return out                     # the picture is no bigger than the slot; nothing to offer

    # ⭐ AIM AT THE ALLOCATION, NOT AT THE ZONE. The pixel buffer is padded to 0x2000, so a
    # texture can usually be enlarged SEVERAL TIMES OVER inside the space it already occupies --
    # 32x32 -> 64x64 BC1 is still 8,192 bytes. That costs nothing, needs no relink, and works in
    # every zone. Growing the zone itself needs the relocation model to bracket the site, which
    # measured zones frequently cannot do, so the free ceiling is what we look for first.
    best = None
    lo, hi = 1.0, f_max
    for _ in range(40):
        mid = (lo + hi) / 2.0
        mw, mh = snap_dims(w, h, mid, img.gx2_format)
        try:
            m_img, m_alloc = surface_sizes(mw, mh, img.gx2_format, img.levels, img.depth)
        except Exception:
            hi = mid
            continue
        if m_alloc <= have:
            if mw > w or mh > h:
                best = (mw, mh, mid, m_img, m_alloc)
            lo = mid
        else:
            hi = mid

    if best is not None:
        mw, mh, mf, m_img, m_alloc = best
        out.append(SizeChoice('enhanced', mw, mh, mf, m_img, m_alloc, m_alloc - have))
        return out

    # Nothing bigger fits the existing allocation. Under `fit_only` that is the end of it: the
    # picture will be stored at the texture's own size, and the caller has nothing to ask.
    if fit_only:
        return out

    # Otherwise offer the true source-supported size, which needs the zone to GROW -- and say
    # so, because that is the part a given zone may refuse.
    nw, nh = snap_dims(w, h, f_max, img.gx2_format)
    if nw <= w and nh <= h:
        return out
    try:
        e_img, e_alloc = surface_sizes(nw, nh, img.gx2_format, img.levels, img.depth)
    except Exception as ex:
        raise ZoneImageError('cannot size %dx%d for %s: %s' % (nw, nh, img.label, ex))
    out.append(SizeChoice('enhanced', nw, nh, f_max, e_img, e_alloc, e_alloc - have))
    return out


def stage_resize(session, img, rgba, width, height):
    """Stage a resize that fits the EXISTING allocation, through length-neutral raw patches.

    -> (bytes_written, SizeChoice-ish note). Raises if the new size does not fit, because that
    case needs `resize_zone` and a relink, which is a different operation with different risks.

    Both edits are length-neutral: the 328-byte record body is rewritten in place, and the pixel
    buffer is replaced with one of exactly the same length. Nothing moves, so no pointer can go
    stale -- the same safety property `patch_raw` already relies on.
    """
    if not img.inline:
        raise ZoneImageError('%s has no inline pixels to replace' % img.label)
    ok, bad = layout_matches_record(session.zone, img)
    if not ok:
        raise ZoneImageError(
            'refusing to resize %s: this record stores a layout we cannot reproduce (%s differ '
            'at its CURRENT size), so a header written for a new size would not be trustworthy '
            'either.' % (img.label, ', '.join(bad)))
    image_size, base_size = surface_sizes(width, height, img.gx2_format, img.levels, img.depth)
    if base_size != img.pixel_len:
        raise ZoneImageError(
            '%dx%d needs a %s-byte allocation but %s has %s. A size that does not fit the '
            'existing buffer requires growing the zone, which is a relink, not a raw patch.'
            % (width, height, format(base_size, ','), img.label, format(img.pixel_len, ',')))

    blob = encode_at(rgba, img, width, height)
    if len(blob) != img.pixel_len:
        raise ZoneImageError('encoded to %d bytes but the slot is %d'
                             % (len(blob), img.pixel_len))

    rec = bytearray(session.zone[img.off:img.off + 328])
    hold = img.off
    try:
        img.off = 0                       # rewrite_record addresses from the buffer start
        rewrite_record(rec, img, width, height, image_size, base_size)
    finally:
        img.off = hold

    n = session.patch_raw(img.off, bytes(rec))
    n += session.patch_raw(img.pixel_off, blob)
    return n


def encode_at(rgba, img, width, height):
    """(h,w,4) RGBA -> tiled bytes for THIS image's format at the given dimensions."""
    if img.gx2_format is None:
        raise ZoneImageError('%s declares no GX2 format' % img.label)
    sized = II.fit_rgba(rgba, width, height)
    return II.encode_payload(sized, width, height, img.gx2_format, levels=img.levels)


def rewrite_record(buf, img, width, height, image_size=None, base_size=None, levels=None):
    """Patch the record's size fields in `buf` (a bytearray). Length-neutral. -> fields written.

    ⚠ The record is MIXED-ENDIAN: the GX2 surface header is little-endian, the T6 tail is big.
    Writing either one in the other's byte order leaves a record that still parses and describes
    a texture of an absurd size, so both halves are written explicitly here rather than through
    one helper.
    """
    levels = int(levels if levels is not None else img.levels or 1)
    B = img.off
    L = surface_layout(width, height, img.gx2_format, levels, img.depth)
    if image_size is None:
        image_size = L['image_size']
    if base_size is None:
        base_size = L['base_size']

    # -- little-endian GX2 surface header ------------------------------------------------
    struct.pack_into('<I', buf, B + F_LE_WIDTH, int(width))
    struct.pack_into('<I', buf, B + F_LE_HEIGHT, int(height))
    struct.pack_into('<I', buf, B + F_LE_MIPS, levels)
    struct.pack_into('<I', buf, B + F_LE_IMAGESIZE, int(image_size))
    # ⛔ THE LAYOUT FIELDS. `encode_payload` tiles for a pitch and tile mode derived from the NEW
    # dimensions, so these describe the bytes that follow. Leaving them at the old values gives
    # the GPU the wrong stride and the wrong block order for pixels that are already correct.
    struct.pack_into('<I', buf, B + F_LE_MIPSIZE, int(L['mip_size']))
    struct.pack_into('<I', buf, B + F_LE_TILEMODE, int(L['tile_mode']))
    struct.pack_into('<I', buf, B + F_LE_ALIGN, int(L['align']))
    struct.pack_into('<I', buf, B + F_LE_PITCH, int(L['pitch']))
    mo = list(L['mip_offsets'])[:N_MIPOFFS]
    for i in range(N_MIPOFFS):
        struct.pack_into('<I', buf, B + F_LE_MIPOFFS + 4 * i, int(mo[i]) if i < len(mo) else 0)
    # swizzle (+52) is deliberately NOT touched: every value observed across 852 stock records
    # decodes to pipe=0/bank=0, the same tiling our encoder uses, so it carries no layout
    # meaning we would be changing.

    # -- big-endian T6 tail ---------------------------------------------------------------
    struct.pack_into('>I', buf, B + F_BE_BASESIZE, int(base_size))
    struct.pack_into('>H', buf, B + F_BE_W16, int(width))
    struct.pack_into('>H', buf, B + F_BE_H16, int(height))
    buf[B + F_BE_LEVELS] = levels & 0xFF
    return 12 + N_MIPOFFS


def resize_zone(zone, img, rgba, width, height, verbose=False):
    """Return (new_zone_bytes, RelinkReport) with this image stored at width x height.

    Same length is still the cheap path: when the new allocation happens to equal the old one --
    which the 0x2000 floor makes common for small textures -- no relink is needed at all and the
    zone is edited in place.
    """
    from . import relink

    if not img.inline:
        raise ZoneImageError('%s has no inline pixels to resize' % img.label)
    ok, bad = layout_matches_record(zone, img)
    if not ok:
        raise ZoneImageError(
            'refusing to resize %s: this record stores a layout we cannot reproduce (%s differ '
            'at its CURRENT size).' % (img.label, ', '.join(bad)))
    blob = encode_at(rgba, img, width, height)
    image_size, base_size = surface_sizes(width, height, img.gx2_format, img.levels, img.depth)
    if len(blob) != base_size:
        raise ZoneImageError('encoded %s at %dx%d to %d bytes but the layout says %d'
                             % (img.label, width, height, len(blob), base_size))

    buf = bytearray(zone)
    rewrite_record(buf, img, width, height, image_size, base_size)

    if base_size == img.pixel_len:
        # length-neutral: splice and done, no pointer can have moved
        buf[img.pixel_off:img.pixel_off + base_size] = blob
        rep = relink.RelinkReport()
        rep.notes.append('same allocation (%d B) -- spliced in place, no relink needed'
                         % base_size)
        return bytes(buf), rep

    subs = {img.pixel_off: (img.pixel_off + img.pixel_len, blob)}
    return relink.grow(bytes(buf), subs, verbose=verbose)


def export_png(zone, img, path, max_side=None):
    from PIL import Image
    rgba, _lvl, _dims = decode(zone, img, max_side=max_side)
    Image.fromarray(rgba, 'RGBA').save(path)
    return path


def streamed_sources(img, owners=None):
    """Which pak(s) carry a STREAMED image's parts. -> [(part_index, [pak, ...])]

    A streamed record is a stub: the zone declares (nameHash, dataHash) per part and the loader
    binds the bytes from the first pak holding that key. Showing the owner turns "streamed --
    edit it in a texture pak" into "streamed from mp_express.ipak", which is the difference
    between knowing it is elsewhere and knowing WHERE.
    """
    if img.pixel_class != 'streamed' or not img.name_hash:
        return []
    if owners is None:
        try:
            from . import ipak_search as _s
            owners, _paks = _s.owner_map()
        except Exception:
            return []
    out = []
    for dh in img.part_hashes:
        out.append((dh >> 29, list(owners.get((img.name_hash, dh), ()))))
    return out


def summarise(images):
    """Counts for a status line: (total, inline, streamed, other)."""
    inline = sum(1 for i in images if i.pixel_class == 'inline')
    streamed = sum(1 for i in images if i.pixel_class == 'streamed')
    return len(images), inline, streamed, len(images) - inline - streamed
