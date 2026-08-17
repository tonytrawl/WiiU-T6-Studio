"""core.ipak -- open, browse, edit and save a T6 ipak texture pack.

This is the session layer the GUI drives, sitting on top of the already-proven container code
in wiiu_ref/ipak.py (which reads and re-emits retail Wii U, 360 and PS3 paks byte-exactly, and
rebuilds mp_raid.ipak byte-for-byte from nothing but its (key, payload) triples).

WHAT THIS ADDS
    * entries joined to their metadata, so the list shows names instead of hashes
    * lazy payload access with a cache, so opening a 1260-entry pak is instant
    * replace / add / delete with the bookkeeping that keeps a pak internally consistent
    * a save path that is byte-exact when you changed nothing

SAVE CONTRACT
-------------
An untouched pak re-saves to the identical bytes it was opened from. That is asserted in
`save()`, not hoped for: if a round-trip through our own writer would change a file the user
did not edit, that is a bug and the save refuses rather than quietly writing a different file.

WHAT EDITING AN IPAK DOES AND DOES NOT DO
-----------------------------------------
An ipak is a pixel store keyed by (nameHash, dataHash). The GfxImage record that names an
image, and declares its format, dimensions and part hashes, lives in the .ff zone, NOT here.

    REPLACING an existing image's pixels works standalone: the key is preserved (see below),
    so the zone's existing reference keeps resolving and the console loads the new pixels.

    ADDING a brand-new image does NOT by itself make it appear in game. Nothing references it
    until a GfxImage record in some zone points at its key. The pak will be valid and the entry
    will be there; wiring it up is zone-side work.

`add_image` therefore returns the key it minted, and the GUI states this plainly, because a
tool that silently implies a new texture is live would send someone hunting a bug in the wrong
file entirely.

THE dataHash PROBLEM, AND WHY REPLACE PRESERVES KEYS
----------------------------------------------------
dataHash is `(partIndex << 29) | crc32(payload)`, and the zone's GfxImage stores that exact
value in its streamedParts table. Change the pixels and the crc changes, so a naive replace
mints a key nothing references -- the image silently disappears in game.

So `replace_image` keeps the ORIGINAL key by default (`rekey=False`). The pak then holds an
entry whose stored dataHash no longer matches its payload's crc. That is deliberate and is
what makes a drop-in texture swap work without touching the zone; the console indexes by key
and does not re-verify the crc at load. `verify_hashes()` reports these on demand so they are
visible rather than hidden, and `rekey=True` is available when the caller is also updating the
zone and wants a canonical pak.
"""
import os
import zlib

_OWNER_CACHE = {}


def _owner_index():
    if 'v' not in _OWNER_CACHE:
        _OWNER_CACHE['v'] = IpakSession._owner_index_impl()
    return _OWNER_CACHE['v']

from . import paths  # noqa: F401  (puts wiiu_ref on sys.path)
from . import ipak_image as II
from . import ipak_names as NM

import ipak as IP


class IpakError(Exception):
    pass


class IpakItem(object):
    """One part payload in the pak, joined to whatever metadata we could resolve."""

    __slots__ = ('name_hash', 'data_hash', 'orig_data_hash', 'part_index', 'stored_size',
                 'name', 'record', 'exact_meta', 'status', '_payload', 'index')

    def __init__(self, name_hash, data_hash, part_index, stored_size, index):
        self.name_hash = name_hash
        self.data_hash = data_hash
        self.orig_data_hash = data_hash
        self.part_index = part_index
        self.stored_size = stored_size
        self.index = index
        self.name = None
        self.record = None
        self.exact_meta = False
        self.status = 'original'          # original | replaced | added
        self._payload = None

    # ---- metadata conveniences (all tolerate a missing record) ----
    def _int(self, field, default=0):
        try:
            return int(self.record[field])
        except (TypeError, KeyError, ValueError):
            return default

    @property
    def format_string(self):
        return (self.record or {}).get('format')

    @property
    def gx2_format(self):
        f = self.format_string
        if not f:
            return None
        try:
            return II.format_from_string(f)
        except II.ImageDecodeError:
            return None

    @property
    def width(self):
        return self._int('width')

    @property
    def height(self):
        return self._int('height')

    @property
    def levels(self):
        return self._int('levels', 1) or 1

    @property
    def mip(self):
        return self._int('mip', 0)

    @property
    def label(self):
        return self.name or '<0x%08X>' % self.name_hash

    @property
    def dims(self):
        return (self.width, self.height) if self.width and self.height else None

    @property
    def previewable(self):
        return bool(self.gx2_format and self.width and self.height)

    @property
    def key(self):
        return (self.name_hash, self.data_hash)

    def __repr__(self):
        return '<IpakItem %s part%d %s %s>' % (self.label, self.part_index,
                                               self.format_string or '?', self.status)


class IpakSession(object):
    def __init__(self, path, pak, items, shared_meta=None):
        self.path = path
        self.pak = pak
        self.items = items
        self.shared_meta = shared_meta
        self.deleted = []
        self._dirty = False
        self._orig_bytes = pak.raw

    # ------------------------------------------------------------------ open ---
    @classmethod
    def open(cls, path, shared_meta=None, use_shared=True):
        """Open a pak. `shared_meta` is a MetaIndex; when omitted and `use_shared`, the cached
        cross-pak dictionary is loaded so paks with no metadata of their own still show names.
        """
        pak = IP.IPak.read(path)

        own = {}
        for typ, raw, _cnt in pak.extra_sections:
            if typ == NM.SEC_META_TEXT:
                try:
                    own.update(NM.parse_section3(raw))
                except Exception:
                    pass                      # malformed metadata degrades the view, not the file

        if shared_meta is None and use_shared:
            try:
                shared_meta = NM.load_shared()
            except Exception:
                shared_meta = None

        items = []
        for i, en in enumerate(pak.entries):
            it = IpakItem(en.name_hash, en.data_hash, en.part_index, en.size, i)
            rec = own.get((en.name_hash, en.data_hash))
            if rec is not None:
                it.record, it.name, it.exact_meta = rec, NM.name_from_record(rec), True
            elif shared_meta is not None:
                nm, srec, exact = shared_meta.lookup(en.name_hash, en.data_hash)
                it.name, it.record, it.exact_meta = nm, srec, exact
            items.append(it)
        return cls(path, pak, items, shared_meta)

    # -------------------------------------------------------------- payloads ---
    def payload(self, item):
        """Decompressed payload bytes, cached. Handles retail raw and PS3 LZO entries."""
        if item._payload is None:
            if item.status == 'added':
                raise IpakError('added item has no stored payload')
            en = self.pak.entries[item.index]
            item._payload = self.pak.extract(en)
        return item._payload

    def preview(self, item, max_side=512):
        """-> (rgba array, level, (w, h)). Raises ImageDecodeError with a readable reason."""
        if not item.previewable:
            missing = []
            if not item.gx2_format:
                missing.append('format' if not item.format_string
                               else 'a decoder for %s' % item.format_string)
            if not (item.width and item.height):
                missing.append('dimensions')
            raise II.ImageDecodeError('no preview: this entry has no %s' % ' or '.join(missing))
        return II.decode_payload(self.payload(item), item.width, item.height,
                                 item.gx2_format, max_side=max_side, levels=item.levels)

    # ----------------------------------------------------------------- edits ---
    @property
    def dirty(self):
        return self._dirty

    # Cached across sessions: scanning the INDEX section of ~126 paks takes ~2 s and yields
    # 43,399 keys, so it is built once per process rather than per query.
    @staticmethod
    def _owner_index_impl():
        """(owners, dupes) over EVERY reachable pak.

        ⚠ THIS USED TO GLOB `<content_dir>/*.ipak` AND THAT IS WHY THE SHADOW WARNING WAS
        SILENT. Two defects in one line: not recursive, so it missed the AOC layout where a
        map's own pak sits in a numbered subfolder (`.../content/0010/mp_skate.ipak`); and
        limited to `content_dirs()`, so with no game folder configured it scanned nothing and
        cheerfully reported no duplicates. An index that cannot see a duplicate will never warn
        about one. core.ipak_search walks the whole search path recursively and caches.
        """
        from . import ipak_search as _search
        omap, _paks = _search.owner_map()
        owners = {k: v[0] for k, v in omap.items() if v}
        dupes = {k: list(v) for k, v in omap.items() if len(v) > 1}
        return owners, dupes

    def resolution_report(self, item):
        """Why an edit to this entry may or may not be visible in game. -> list of lines.

        SETTLED BY DISASSEMBLY of wiiu_ref/t6mp_cafef_rpl.rpl (it carries a full .symtab):

          * The console NEVER hashes the payload. `IPak_BuildAdjacencyInfo` (0x0271A930) does an
            exact 64-bit compare of the zone's (nameHash, dataHash) against the index entry
            (cmplw at 0x271AE2C / 0x271AE38, full 32 bits, no 0x1FFFFFFF mask), then stores only
            offset and size into the part. `clrlwi rX,rY,3` appears 12 times in all of .text and
            never in ipak or streaming code; the one crc32 table is used solely by LiveAntiCheat.
            So keeping the original key while changing the bytes is SOUND -- that is why
            `replace_payload` defaults to rekey=False.

          * BUT the bind loop walks up to 32 packfiles and SKIPS any part already bound, so the
            EARLIEST-LOADED pak holding a key wins and a later one can never override it.

        Hence this report: it names the pak that actually owns the key, flags any duplicate that
        could win first, and lists the image's other parts -- because replacing one part changes
        only that part's mip levels.
        """
        lines = []
        try:
            owners, dupes = _owner_index()
        except Exception as ex:
            return ['(could not build the owner index: %s: %s)' % (type(ex).__name__, ex)]
        key = (item.name_hash, item.data_hash)
        own = owners.get(key)
        here = os.path.abspath(self.path) if self.path else None
        if own is None:
            lines.append('This key is in no pak under the known content dirs, so nothing on '
                         'disk currently serves it.')
        else:
            same = here and os.path.abspath(own) == here
            lines.append('Owned by %s%s' % (os.path.basename(own),
                                            '  (this file)' if same else
                                            '  <- NOT the file you are editing'))
        for other in dupes.get(key, [])[1:]:
            lines.append('⚠ the same key is also in %s; whichever pak loads first wins and the '
                         'other is skipped' % os.path.basename(other))
        sibs = [it for it in self.items if it.name_hash == item.name_hash]
        if len(sibs) > 1:
            where = []
            for s in sorted(sibs, key=lambda x: x.part_index):
                o = owners.get((s.name_hash, s.data_hash))
                where.append('part %d in %s' % (s.part_index,
                                                os.path.basename(o) if o else '?'))
            lines.append('This image has %d parts: %s. A part is one slice of the mip chain, so '
                         'replacing one changes only those levels -- replace every part to '
                         'change the texture at all distances.' % (len(sibs), ', '.join(where)))
        if not item.exact_meta:
            lines.append('⚠ this entry\'s name came from the shared dictionary, not from this '
                         'pak, so it is a best guess -- confirm you are editing the entry you '
                         'think you are.')
        lines.append('After deploying, COLD-START the game: resolved parts are cached until '
                     'IPak_InvalidateImages runs, so a hot reload can keep serving the old bytes.')
        return lines

    def replace_payload(self, item, blob, rekey=False):
        """Swap raw payload bytes. See the module docstring on why rekey defaults to False."""
        item._payload = bytes(blob)
        item.status = 'replaced' if item.status == 'original' else item.status
        if rekey:
            item.data_hash = IP.data_hash(item._payload, item.part_index)
        if item.record is not None:
            # Rewrite the size line inside the original text rather than regenerating it, so
            # the pak's own record formatting survives the edit.
            item.record = NM.update_record(item.record, size=len(item._payload))
        self._dirty = True
        return item

    def replace_image(self, item, rgba, rekey=False):
        """Replace with decoded pixels, re-encoded into the entry's EXISTING format.

        The format is taken from the entry, never from the incoming file: the zone's GfxImage
        declares how the GPU will read these bytes, so a replacement must match it exactly.
        """
        if not item.previewable:
            raise IpakError('cannot re-encode %s: its format/dimensions are unknown, so there '
                            'is no way to know what the console expects here' % item.label)
        blob = II.encode_payload(rgba, item.width, item.height, item.gx2_format,
                                 levels=item.levels)
        old = self.payload(item)
        if len(blob) != len(old):
            raise IpakError('re-encoded payload is %d B but the original is %d B -- refusing, '
                            'the console reads this buffer at fixed offsets'
                            % (len(blob), len(old)))
        return self.replace_payload(item, blob, rekey=rekey)

    def parts_of(self, item):
        """Every part of the same image that lives in THIS pak, lowest part index first."""
        return sorted((it for it in self.items if it.name_hash == item.name_hash),
                      key=lambda x: x.part_index)

    def replace_image_all(self, item, rgba, rekey=False):
        """Replace EVERY part of this image that lives in this pak, from one source image.

        ⭐ THIS IS WHAT "REPLACE A TEXTURE" ACTUALLY MEANS. A streamed image is split across up
        to three parts and each part carries a different slice of the mip chain:

            part 0  mips 2..N, the low-detail tail -- ships in lowmip_split*, stays resident
            part 1  mip 1
            part 2  mip 0, the full-resolution surface you see up close

        Replacing a single part therefore changes only those mip levels, and the untouched
        levels keep serving the ORIGINAL pixels -- which is exactly the "I replaced it but the
        stock image still shows" symptom. Measured on mp_raid: 78% of streamed images have parts
        in more than one pak, and the map pak carries the high-detail parts (126 of 2,250).

        Each part is re-encoded at ITS OWN dimensions and level count, taken from that part's
        own metadata, not from the part you happened to select.

        Returns a report dict: replaced, skipped, elsewhere.
        """
        parts = self.parts_of(item)
        if not parts:
            raise IpakError('no parts found for %s' % item.label)

        rep = {'replaced': [], 'skipped': [], 'elsewhere': [], 'name': item.name or item.label}
        for p in parts:
            if not p.previewable:
                rep['skipped'].append((p.part_index, 'format/dimensions unknown'))
                continue
            try:
                sized = II.fit_rgba(rgba, p.width, p.height)
                blob = II.encode_payload(sized, p.width, p.height, p.gx2_format,
                                         levels=p.levels)
                old = self.payload(p)
                if len(blob) != len(old):
                    rep['skipped'].append(
                        (p.part_index, 're-encoded to %d B but the slot is %d B'
                         % (len(blob), len(old))))
                    continue
                self.replace_payload(p, blob, rekey=rekey)
                rep['replaced'].append(p.part_index)
            except Exception as ex:
                rep['skipped'].append((p.part_index, '%s: %s' % (type(ex).__name__, ex)))

        # Parts of this image that are NOT in this file: the caller must be told, because those
        # mip levels will keep serving the original pixels no matter what we write here.
        try:
            owners = _owner_index()
            here = os.path.abspath(self.path) if self.path else None
            mine = {p.part_index for p in parts}
            for (nh, dh), owner in owners.items():
                if nh != item.name_hash:
                    continue
                if here and os.path.abspath(owner) == here:
                    continue
                pi = dh >> 29
                if pi not in mine:
                    rep['elsewhere'].append((pi, os.path.basename(owner)))
            rep['elsewhere'] = sorted(set(rep['elsewhere']))
        except Exception:
            pass
        return rep

    def add_payload(self, name, part_index, blob, record=None):
        """Add a brand-new entry. Returns the item; its key is (nameHash, dataHash)."""
        nh = IP.r_hash_string(name)
        dh = IP.data_hash(blob, part_index)
        if any(i.name_hash == nh and i.data_hash == dh for i in self.items):
            raise IpakError('an entry with key (%08X, %08X) is already present' % (nh, dh))
        it = IpakItem(nh, dh, part_index, len(blob), index=-1)
        it._payload = bytes(blob)
        it.status = 'added'
        it.name = name
        it.record = record
        it.exact_meta = record is not None
        self.items.append(it)
        self._dirty = True
        return it

    def add_image(self, name, rgba, format_string='DXT5', levels=1, part_index=0):
        """Encode and add a new texture. See the docstring: this does not wire it into a zone."""
        gfmt = II.format_from_string(format_string)
        if gfmt is None:
            raise IpakError('format %s is recognised but not encodable here' % format_string)
        h, w = rgba.shape[0], rgba.shape[1]
        blob = II.encode_payload(rgba, w, h, gfmt, levels=levels)
        rec = NM.synth_record(name, format_string, w, h, levels, part_index, len(blob))
        return self.add_payload(name, part_index, blob, record=rec)

    def delete(self, item):
        if item not in self.items:
            raise IpakError('item is not in this pak')
        self.items.remove(item)
        self.deleted.append(item)
        self._dirty = True

    # ------------------------------------------------------------------ save ---
    def verify_hashes(self):
        """-> [(item, stored_dataHash, actual)] where the stored key no longer matches the crc.

        Expected and intentional after a replace that preserved keys; surfaced so it is a
        visible property of the pak rather than a hidden inconsistency.
        """
        out = []
        for it in self.items:
            try:
                blob = self.payload(it)
            except Exception:
                continue
            actual = IP.data_hash(blob, it.part_index)
            if actual != it.data_hash:
                out.append((it, it.data_hash, actual))
        return out

    def build(self):
        """-> the bytes this session would write."""
        if not self._dirty:
            # Untouched: reproduce the original exactly, via the proven writer.
            trip = [(en.name_hash, en.data_hash, self.pak.extract(en))
                    for en in sorted(self.pak.entries, key=lambda x: x.offset)]
            blob = IP.write_ipak(trip, endian=self.pak.e,
                                 extra_sections=self.pak.extra_sections, keep_order=True)
            if blob != self._orig_bytes:
                raise IpakError('refusing to save: a no-op round-trip of an UNMODIFIED pak '
                                'produced different bytes (%d vs %d). That is a writer bug, '
                                'and writing would corrupt a file you did not edit.'
                                % (len(blob), len(self._orig_bytes)))
            return blob

        # Modified: keep the original data order for untouched entries so diffs stay small.
        ordered = sorted(self.items, key=lambda i: (i.status == 'added', i.index))
        trip = [(i.name_hash, i.data_hash, self.payload(i)) for i in ordered]

        extras = []
        for typ, raw, cnt in self.pak.extra_sections:
            if typ == NM.SEC_KEY_LIST:
                # This list covers every part the map uses, INCLUDING parts physically stored
                # in other paks. Those foreign keys must survive untouched or the map loses its
                # cross-pak references; only keys belonging to entries of THIS pak are restated.
                try:
                    old = NM.parse_section4(raw)
                except Exception:
                    old = []
                # Walk the ORIGINAL list and rewrite in place: a key whose entry we rekeyed is
                # substituted, a deleted entry's key is dropped, foreign keys pass through
                # untouched. Only genuinely new entries are appended. This keeps the ordering
                # the pak shipped with -- retail is not consistent about it (mp_raid is sorted,
                # base_split is not), so imposing our own would be a gratuitous diff.
                remap = {(i.name_hash, i.orig_data_hash): (i.name_hash, i.data_hash)
                         for i in self.items if i.index >= 0}
                dropped = {(d.name_hash, d.orig_data_hash) for d in self.deleted}
                keys = []
                for k in old:
                    if k in dropped:
                        continue
                    keys.append(remap.get(k, k))
                # Append ONLY genuinely new entries. Retail omits some of its own entries from
                # this list (mp_raid lists 124 of its 126), so "not present" is a deliberate
                # state to preserve, not a gap to fill.
                have = set(keys)
                for i in self.items:
                    k = (i.name_hash, i.data_hash)
                    if i.status == 'added' and k not in have:
                        keys.append(k)
                        have.add(k)
                blob, n = NM.build_section4(keys)
                extras.append((typ, blob, n))
            elif typ == NM.SEC_META_TEXT:
                # Start from the original, in its original order, and touch only what changed.
                # Records here can describe parts stored in OTHER paks, so anything we did not
                # edit must be left exactly as it was -- including its byte-exact text.
                try:
                    recs = NM.parse_section3(raw)
                except Exception:
                    recs = {}
                for it in self.deleted:
                    recs.pop((it.name_hash, it.orig_data_hash), None)
                for it in self.items:
                    if it.status == 'original':
                        continue                  # untouched: leave the original record alone
                    recs.pop((it.name_hash, it.orig_data_hash), None)
                    if it.record:
                        recs[(it.name_hash, it.data_hash)] = it.record
                blob, n = NM.build_section3(recs)
                extras.append((typ, blob, n))
            else:
                extras.append((typ, raw, cnt))

        return IP.write_ipak(trip, endian=self.pak.e, extra_sections=extras, keep_order=True)

    def save(self, path=None, backup=True):
        """Write the pak. Overwriting an existing file makes a .bak first by default."""
        path = path or self.path
        blob = self.build()
        if backup and os.path.exists(path):
            bak = path + '.bak'
            if not os.path.exists(bak):
                with open(path, 'rb') as f_in, open(bak, 'wb') as f_out:
                    f_out.write(f_in.read())
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(blob)
        os.replace(tmp, path)

        # Re-open what we just wrote and confirm every payload reads back. A save that produced
        # an unreadable pak must fail loudly here, not on the console.
        chk = IP.IPak.read(path)
        if len(chk.entries) != len(self.items):
            raise IpakError('post-save check: wrote %d entries, read back %d'
                            % (len(self.items), len(chk.entries)))
        for en in chk.entries:
            chk.extract(en)
        return path

    # ------------------------------------------------------------- reporting ---
    def stats(self):
        n_named = sum(1 for i in self.items if i.name)
        n_prev = sum(1 for i in self.items if i.previewable)
        return {'entries': len(self.items),
                'named': n_named,
                'previewable': n_prev,
                'edited': sum(1 for i in self.items if i.status != 'original'),
                'deleted': len(self.deleted),
                'endian': 'BE (console)' if self.pak.e == '>' else 'LE (PC)',
                'bytes': len(self._orig_bytes)}


def crc_of(blob):
    return zlib.crc32(blob) & 0x1FFFFFFF
