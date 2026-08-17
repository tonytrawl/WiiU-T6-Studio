"""core.assets -- asset enumeration for a decompressed T6 Wii U zone.

Replaces `ff_assets.scan_buffers()` as the primary enumerator.

WHY
---
`ff_assets.scan_buffers()` finds assets by regex-scanning for the byte signature
`FF FF FF FF | len | FF FF FF FF`. That only ever matches the inline-blob shape
(ScriptParseTree, RawFile, KeyValuePairs), so the editor's file list has always been a
*subset* of the zone -- and a heuristic subset at that, with no span end and no type.

`native_linker/zone_walk.walk()` is the walk that is 63/63 zones / 16/16 maps EOF-exact. It
yields `(start, end, type_name, entry_index)` for every body-bearing asset. This module turns
that into an editor-facing asset list, and falls back to the regex scanner (clearly labelled)
only when the walk cannot complete.

EDITABILITY
-----------
Classification is deliberately conservative. An asset is only reported editable when we can
name the exact byte range that holds its payload. Everything else is `opaque` -- browsable and
hex-viewable, but the UI must not offer a structured edit it cannot honour.

⚠ Two types are BOUNDED, NOT MODELLED: GfxWorld (extent-only) and MenuList (follower-bound).
Their spans are correct -- they can be relocated -- but their interiors are not modelled, so
they can never be re-authored. They are marked `readonly=True` with a reason, because a UI that
silently lets you edit them is worse than one that refuses.
"""
import struct

from . import paths  # noqa: F401  -- installs sys.path

import zone_walk
import zone_facts as ZF

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE

# Bounded-only: span is exact, interior is not modelled. See HANDOFF_relink_gap_to_100.md §4.
BOUNDED_ONLY = {
    'GFXWORLD': 'GfxWorld is bounded (extent-only), not modelled -- relocatable, never re-authorable.',
    'MENULIST': 'MenuList is follower-bound: the span is found by scanning for the NEXT asset, '
                'and the menuDef graph is not modelled.',
}

# ⚠ THE SECOND HEADER WORD IS NOT ALWAYS A BYTE LENGTH.
# `_blob_shape` decodes `{name*=FOLLOW, len u32, buffer*=FOLLOW}`, which is correct for
# ScriptParseTree and RawFile. These two types share that *shape* but their middle word is a
# COUNT, so treating it as a byte length yields a payload range that is simply wrong, and a
# length-changing edit desyncs the structural walk. Measured on patch_mp:
#
#   KEYVALUEPAIRS 'patch_mp'        len=16    span=366 B   -> 329 bytes unaccounted for
#   SOUND_PATCH   'sound/patch.csv' len=1042  span=4196 B  -> 3126 bytes unaccounted for
#
# and growing them broke the walk at 3 of 4 tested sizes (the one "OK" size each is a
# coincidence, not safety: 1528 assets became 1236 and 1540 respectively).
# They are therefore reported read-only with the reason, rather than offered as editable.
# Only these two types are known to use the middle word as a BYTE LENGTH. This is a WHITELIST
# on purpose: a blacklist would only exclude the misclassifications we happened to find, and the
# next type with a count-shaped header would silently become "editable" again.
# Measured across 10 zones: 1410 RawFiles + 869 ScriptParseTrees all have exactly 1 trailing byte
# after the payload, while every MenuList / KeyValuePairs / SoundPatch has more than 8 -- i.e.
# the shape matches but the length does not describe the payload.
BLOB_SHAPED = frozenset(('RAWFILE', 'SCRIPTPARSETREE'))

COUNT_NOT_LENGTH = {
    'KEYVALUEPAIRS': 'KeyValuePairs stores a PAIR COUNT where RawFile stores a byte length, so '
                     'the payload range cannot be derived this way and resizing desyncs the '
                     'walk (measured: 1528 assets -> 1236).',
    'SOUND_PATCH': 'SoundPatch stores an ENTRY COUNT, not a byte length; its payload is packed '
                   'binary records despite the .csv name, and resizing desyncs the walk '
                   '(measured: 1528 assets -> 1540).',
}

# Payload magics we can positively identify.
GSC_MAGICS = (b'\x80GSC\r\n', b'GSC\r\n')
HKS_MAGICS = (b'\x1bLua', b'\x1bLuaQ')


def _cstr(zone, off, maxlen=256):
    """NUL-terminated printable ASCII at `off`, or None."""
    end = zone.find(b'\x00', off, min(off + maxlen, len(zone)))
    if end < 0 or end == off:
        return None
    raw = zone[off:end]
    if not all(0x20 <= c < 0x7f for c in raw):
        return None
    try:
        return raw.decode('ascii')
    except UnicodeDecodeError:
        return None


def _blob_shape(zone, start, span_end):
    """Decode the inline-blob header `{name*=FOLLOW, len u32, buffer*=FOLLOW}` at `start`.

    Returns (name, buf_off, buf_len) or None. This is the shape used by ScriptParseTree,
    RawFile and KeyValuePairs -- the assets that carry an editable payload.
    """
    if start + 12 > span_end:
        return None
    w0, ln, w2 = struct.unpack_from('>III', zone, start)
    if w0 != FOLLOW or w2 != FOLLOW:
        return None
    if ln == 0 or ln > 0x4000000:          # 0..64 MB sanity
        return None
    name = _cstr(zone, start + 12)
    if not name:
        return None
    buf_off = start + 12 + len(name) + 1
    if buf_off + ln > span_end:
        return None
    return name, buf_off, ln


def _probable_name(zone, start, span_end, window=512):
    """First plausible NUL-terminated identifier inside the head of a span.

    Used for asset types whose struct layout we don't model here. Reported as `probable` so the
    UI can say so rather than implying we parsed the struct.
    """
    lo, hi = start, min(start + window, span_end)
    i = lo
    while i < hi:
        s = _cstr(zone, i, maxlen=192)
        if s:
            # A struct field byte immediately before the string is often itself printable
            # (0x2C ',' is a common low byte of a pointer word), which used to prepend junk
            # to the reported name. Trim to the first identifier-ish character.
            j = 0
            while j < len(s) and not (s[j].isalnum() or s[j] in '_/\\$'):
                j += 1
            s = s[j:]
            if len(s) >= 3 and any(c.isalpha() for c in s):
                return s
        nxt = zone.find(b'\x00', i, hi)
        if nxt < 0:
            break
        i = nxt + 1
    return None


def _looks_text(buf):
    if not buf:
        return True
    sample = buf[:4096]
    printable = sum(1 for c in sample if 0x20 <= c < 0x7f or c in (9, 10, 13))
    return printable / len(sample) >= 0.92


def _payload_kind(buf):
    """What is this blob? -> 'gsc' | 'hks' | 'text' | 'binary'"""
    if not buf:
        return 'text'
    for m in GSC_MAGICS:
        if buf.startswith(m):
            return 'gsc'
    for m in HKS_MAGICS:
        if buf.startswith(m):
            return 'hks'
    return 'text' if _looks_text(buf) else 'binary'


class Asset(object):
    """One asset in a zone, with an exact span.

    start/end   file offsets of the whole asset body (from the proven walk)
    buf_off/len byte range of the editable payload, or None for opaque assets
    """
    __slots__ = ('index', 'type_name', 'type_id', 'start', 'end', 'name', 'name_probable',
                 'buf_off', 'buf_len', 'payload', 'readonly', 'reason', 'source')

    def __init__(self, index, type_name, start, end, type_id=None):
        self.index = index
        self.type_name = type_name
        self.type_id = type_id
        self.start = start
        self.end = end
        self.name = None
        self.name_probable = False
        self.buf_off = None
        self.buf_len = None
        self.payload = 'opaque'
        self.readonly = False
        self.reason = ''
        self.source = 'walk'

    @property
    def span_len(self):
        return self.end - self.start

    @property
    def editable(self):
        return self.buf_off is not None and not self.readonly

    @property
    def label(self):
        return self.name or '<%s #%d>' % (self.type_name.lower(), self.index)

    def extract(self, zone):
        """The editable payload bytes, or the whole span for opaque assets."""
        if self.buf_off is not None:
            return zone[self.buf_off:self.buf_off + self.buf_len]
        return zone[self.start:self.end]

    def __repr__(self):
        return '<Asset %s %s 0x%x..0x%x %s>' % (self.type_name, self.label,
                                                self.start, self.end, self.payload)


class Enumeration(object):
    """Result of enumerating a zone: the assets plus an honest account of how they were found."""

    def __init__(self):
        self.assets = []
        self.walk_ok = False
        self.walk_verdict = ''
        self.coverage = 0.0
        self.fallback = False
        self.unwalked_bytes = 0
        self.warn = []

    def __len__(self):
        return len(self.assets)

    def __iter__(self):
        return iter(self.assets)

    def by_type(self):
        out = {}
        for a in self.assets:
            out.setdefault(a.type_name, []).append(a)
        return out

    def summary(self):
        return dict(assets=len(self.assets), walk_ok=self.walk_ok,
                    coverage=round(self.coverage, 4), fallback=self.fallback,
                    unwalked_bytes=self.unwalked_bytes,
                    verdict=self.walk_verdict, warn=list(self.warn))


def _decorate(zone, a):
    """Fill in name / payload / editability for one asset."""
    if a.type_name in BOUNDED_ONLY:
        a.readonly = True
        a.reason = BOUNDED_ONLY[a.type_name]

    shape = _blob_shape(zone, a.start, a.end) if a.type_name in BLOB_SHAPED else None
    if shape is None:
        # Not a blob-shaped type. The NAME may still be readable: it sits at a fixed offset
        # (start+12) whenever the head is {FOLLOW, u32, FOLLOW}, and that is true regardless of
        # whether the middle word is a byte length or a count. Take the name, never the range.
        head_ok = False
        if a.start + 12 <= a.end:
            w0, _mid, w2 = struct.unpack_from('>III', zone, a.start)
            head_ok = (w0 == FOLLOW and w2 == FOLLOW)
        if head_ok:
            nm = _cstr(zone, a.start + 12)
            if nm:
                a.name = nm
        if a.type_name in COUNT_NOT_LENGTH:
            a.readonly = True
            a.reason = COUNT_NOT_LENGTH[a.type_name]
            a.payload = 'opaque'
            return a
        if head_ok and a.name:
            a.payload = 'opaque'
            if not a.readonly:
                a.readonly = True
                a.reason = ('%s is not a modelled {name, byteLength, buffer} asset, so its '
                            'payload range is unknown.' % a.type_name.title())
            return a
    if shape:
        a.name, a.buf_off, a.buf_len = shape
        a.payload = _payload_kind(zone[a.buf_off:a.buf_off + min(a.buf_len, 4096)])
        return a

    nm = _probable_name(zone, a.start, a.end)
    if nm:
        a.name = nm
        a.name_probable = True
    a.payload = 'opaque'
    if not a.reason:
        a.reason = ('no inline {name,len,buffer} header -- struct layout for %s is not modelled '
                    'here; hex edit only' % a.type_name)
    return a


def enumerate_zone(zone, allow_fallback=True):
    """Enumerate every asset in a decompressed zone. Returns an Enumeration.

    Primary path is the proven walk. If the walk cannot complete, and `allow_fallback`, the
    regex scanner fills in the inline-blob assets and the result is flagged `fallback=True` --
    the caller MUST surface that, because a fallback list is a subset with no span ends.
    """
    out = Enumeration()
    try:
        with paths.backend_cwd():
            wk = zone_walk.walk(zone)
    except Exception as ex:
        wk = None
        out.warn.append('walk raised: %s: %s' % (type(ex).__name__, ex))

    if wk is not None:
        out.walk_ok = wk.ok
        out.walk_verdict = wk.verdict()
        out.coverage = wk.coverage()
        out.unwalked_bytes = max(0, wk.zone_len - wk.end)
        try:
            tids = ZF.Facts(zone).asset_types()
        except Exception:
            tids = []
        for (s, e, nm, i) in wk.spans:
            a = Asset(i, nm, s, e, type_id=(tids[i] if i < len(tids) else None))
            out.assets.append(_decorate(zone, a))
        if wk.ok:
            return out
        out.warn.append('walk incomplete: %s' % wk.verdict())

    if not allow_fallback:
        return out

    # ---- fallback: regex signature scan, clearly labelled -------------------------------
    found = {a.start for a in out.assets}
    try:
        import ff_assets
        for e in ff_assets.scan_buffers(zone):
            if e['struct_off'] in found:
                continue
            a = Asset(-1, e['kind'].upper(), e['struct_off'],
                      e['buf_off'] + e['len'])
            a.name = e['name']
            a.buf_off, a.buf_len = e['buf_off'], e['len']
            a.payload = _payload_kind(zone[a.buf_off:a.buf_off + min(a.buf_len, 4096)])
            a.source = 'regex-fallback'
            a.reason = ('found by signature scan, not the structural walk -- span end is '
                        'inferred, not measured')
            out.assets.append(a)
        out.fallback = True
    except Exception as ex:
        out.warn.append('fallback scan failed: %s: %s' % (type(ex).__name__, ex))

    out.assets.sort(key=lambda a: a.start)
    return out
