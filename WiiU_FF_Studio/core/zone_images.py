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
import sys

from . import paths  # noqa: F401  (installs native_linker + wiiu_ref on sys.path)
from . import ipak_image as II


class ZoneImageError(Exception):
    pass


def _census(zone):
    import gfximage_census as GC
    return GC.census(zone)


class ZoneImage(object):
    """One image record located inside a zone."""

    __slots__ = ('off', 'name', 'name_hash', 'width', 'height', 'gx2_format', 't6_format',
                 'levels', 'pixel_off', 'pixel_len', 'image_size', 'base_size',
                 'pixel_class', 'alias_named', 'part_hashes')

    def __init__(self, rec, zone):
        self.off = rec['off']
        self.name = rec.get('name')
        self.name_hash = rec.get('name_hash')
        self.width = rec.get('width') or 0
        self.height = rec.get('height') or 0
        self.gx2_format = rec.get('gx2_format')
        self.t6_format = rec.get('t6_format')
        self.levels = rec.get('mips') or 1
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
