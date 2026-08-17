"""core.session -- one object that owns a fastfile end to end.

    s = ZoneSession.open('patch_mp.ff')
    for a in s.assets: ...
    s.stage(asset, new_bytes)          # queue an edit; nothing is written yet
    s.save('out.ff')                   # plan -> verify -> pack -> write

DESIGN RULE
-----------
No zone bytes are handled in widget code. Both GUI apps drive this object, and every operation
here is reachable headlessly, so a GUI build can always be reproduced from a script.

SAVE PLANNING
-------------
Edits are staged, never applied in place. At save time the session picks a strategy:

    in-place    every staged edit is length-neutral -> straight byte splice
    grow        some edit changes length -> requires the relink (core.relink)
    refuse      the change is outside what we can do soundly, with a stated reason

The `refuse` branch is the point of the whole design. The historical failure mode of this
project was producing a zone that looked fine and died on hardware; a save path that stops and
says why is strictly better than one that guesses.
"""
import hashlib
import os
import struct

from . import paths  # noqa: F401
from . import assets as _assets

import wiiu_ff
import zone_facts as ZF


class Edit(object):
    """A staged change to one asset's payload."""
    __slots__ = ('asset', 'data', 'note')

    def __init__(self, asset, data, note=''):
        self.asset = asset
        self.data = data
        self.note = note

    @property
    def delta(self):
        return len(self.data) - (self.asset.buf_len or 0)


# console XAssetType ids, read off a real zone rather than copied from a header
CONSOLE_TYPE_ID = {'RAWFILE': 42, 'SCRIPTPARSETREE': 50, 'KEYVALUEPAIRS': 51}


def _console_type_id(type_name):
    tid = CONSOLE_TYPE_ID.get(type_name)
    if tid is None:
        raise ValueError('no measured console type id for %r -- refusing to guess one'
                         % type_name)
    return tid


def _rawfile_body(name, payload):
    """RawFile record: same `{name*, len, buffer*} + name + buffer + NUL` shape as a
    ScriptParseTree. Measured on patch_mp: `len` EXCLUDES the single trailing NUL, and
    consecutive bodies are separated by exactly that one byte."""
    if isinstance(name, str):
        name = name.encode('latin-1')
    return (struct.pack('>III', 0xFFFFFFFF, len(payload), 0xFFFFFFFF)
            + name + b'\x00' + payload + b'\x00')


class PendingAdd(object):
    """A queued new asset. Inert until save(), and inert in-game until something calls it."""

    __slots__ = ('name', 'payload', 'kind')

    def __init__(self, name, payload, kind):
        self.name, self.payload, self.kind = name, payload, kind

    @property
    def type_name(self):
        return 'SCRIPTPARSETREE' if self.kind == 'gsc' else 'RAWFILE'

    def __repr__(self):
        return '<PendingAdd %s %r %d B>' % (self.kind, self.name, len(self.payload))


def walk_refusal_help(verdict):
    """Turn a walk verdict into something a user can act on.

    ⚠ "the structural walk did not complete" is TRUE and USELESS on its own. It reads as "your
    file is broken" when the real meaning is "this tool cannot fully parse this particular zone
    yet" -- the file is fine and untouched. Measured across the 155 english zones: 114 walk
    cleanly and 41 refuse, and the refusals cluster on a handful of asset types. Naming the type
    and the population turns a dead end into a known limitation.
    """
    v = (verdict or '').lower()
    known = (('vehicledef', 'VehicleDef', 17),
             ('tracerdef', 'TracerDef', 8),
             ('menulist', 'MenuList', 3),
             ('clipmap', 'clipMap_t', 2))
    for needle, name, count in known:
        if needle in v:
            return ('This is a KNOWN LIMITATION, not damage to your file: the walker cannot yet '
                    'resume past a %s record. It affects about %d of the 155 stock zones. Your '
                    'fastfile is unchanged on disk, and length-neutral edits (replacing an inline '
                    'image with one the same size) still work -- only growing the zone is '
                    'refused.' % (name, count))
    return ('The walker stopped before the end of this zone, so growing it is refused. Your file '
            'is unchanged on disk. About 41 of the 155 stock zones do this; length-neutral edits '
            '(same-size image replacement) still work.')


class SavePlan(object):
    def __init__(self, strategy, edits, total_delta, reasons):
        self.strategy = strategy          # 'in-place' | 'grow' | 'refuse'
        self.edits = edits
        self.total_delta = total_delta
        self.reasons = reasons            # why this strategy / why refused

    @property
    def ok(self):
        return self.strategy != 'refuse'

    def __repr__(self):
        return '<SavePlan %s delta=%+d edits=%d>' % (self.strategy, self.total_delta,
                                                     len(self.edits))


class ZoneSession(object):
    def __init__(self, zone, name, ff_path=None, header=None, chunks=None):
        self.zone = zone
        self.name = name
        self.ff_path = ff_path
        self.header = header
        self.chunks = chunks
        self.base_md5 = hashlib.md5(zone).hexdigest()
        self._edits = {}                  # id(asset) -> Edit
        self._adds = []                   # PendingAdd, applied at build time
        # offset -> replacement bytes, SAME LENGTH ONLY. Used for edits that are not asset
        # payloads at all -- inline GfxImage pixels, which live in the middle of a material or
        # FX body rather than in an editable asset. See patch_raw().
        self._raw = {}
        self.last_relink_report = None    # authoritative gates from the last build_zone()
        self.last_add_stats = None
        self.enumeration = _assets.enumerate_zone(zone)
        self.facts = ZF.Facts(zone, name=name)

    # ---------------------------------------------------------------- construction
    @classmethod
    def open(cls, path, progress=None):
        """Open a .ff (decrypt+decompress) or a raw .zone."""
        raw = open(path, 'rb').read()
        if path.lower().endswith('.zone'):
            name = os.path.splitext(os.path.basename(path))[0]
            return cls(raw, name, ff_path=path)
        if not wiiu_ff.is_wiiu_fastfile(raw):
            raise ValueError('Not a Wii U fastfile (TAff0100 / version 148 expected): %s' % path)
        hdr, zone, n = wiiu_ff.decrypt(raw, progress=progress)
        return cls(zone, hdr['name'], ff_path=path, header=hdr, chunks=n)

    # ---------------------------------------------------------------- assets
    @property
    def assets(self):
        return self.enumeration.assets

    def find(self, name):
        """First asset whose name matches exactly, else None."""
        for a in self.assets:
            if a.name == name:
                return a
        return None

    def search(self, needle, in_content=False):
        needle_l = needle.lower()
        hits = []
        for a in self.assets:
            if a.name and needle_l in a.name.lower():
                hits.append(a)
            elif in_content and a.buf_off is not None:
                if needle.encode('latin1', 'replace') in a.extract(self.zone):
                    hits.append(a)
        return hits

    def extract(self, asset):
        return asset.extract(self.zone)

    # ---------------------------------------------------------------- editing
    def stage(self, asset, data, note=''):
        """Queue a payload replacement. Nothing is written until save()."""
        if isinstance(data, str):
            data = data.encode('latin1', 'replace')
        if asset.readonly:
            raise ValueError('%s is read-only: %s' % (asset.label, asset.reason))
        if asset.buf_off is None:
            raise ValueError('%s has no modelled payload range -- hex edit the span instead'
                             % asset.label)
        self._edits[id(asset)] = Edit(asset, data, note)
        return self._edits[id(asset)]

    def unstage(self, asset):
        self._edits.pop(id(asset), None)

    def clear_edits(self):
        self._edits.clear()

    @property
    def edits(self):
        return list(self._edits.values())

    @property
    def dirty(self):
        return bool(self._edits) or bool(self._adds)

    @property
    def total_delta(self):
        return sum(e.delta for e in self._edits.values())

    # ---------------------------------------------------------------- save
    def plan(self):
        """Decide how this save would be performed, without performing it."""
        edits = self.edits
        reasons = []
        if not edits and not self._adds and not self._raw:
            return SavePlan('in-place', [], 0, ['no edits staged'])

        delta = sum(e.delta for e in edits)
        if self._raw:
            # Raw patches are absolute offsets into the CURRENT zone. Anything that relocates
            # bodies invalidates them, so refuse the combination rather than write a zone whose
            # pixels landed in the wrong place.
            if self._adds or delta != 0:
                return SavePlan('refuse', edits, delta,
                                ['%d raw byte patch(es) are staged (inline image pixels), and '
                                 'those are absolute offsets into the zone as it stands. A '
                                 'growing edit or a new asset relinks the zone and moves every '
                                 'body, so the two cannot be saved together -- save the image '
                                 'change first, reopen, then make the growing edit.'
                                 % len(self._raw)])
            reasons.append('%d raw byte patch(es), length-neutral' % len(self._raw))
            if not edits:
                return SavePlan('in-place', [], 0, reasons)
        if self._adds:
            # Container growth. add_asset() refuses on a broken walk for the same reason grow()
            # does, so surface that here rather than at build time.
            if not self.enumeration.walk_ok:
                return SavePlan('refuse', edits, delta,
                                ['adding an asset re-authors the XAssetList array, which needs '
                                 'a complete structural walk; this zone walks %s'
                                 % self.enumeration.walk_verdict])
            reasons.append('%d new asset(s) appended (+8 bytes of array each, uniform block-5 '
                           'shift)' % len(self._adds))
            if edits:
                reasons.append('%d edit(s) change length by %+d bytes' % (len(edits), delta))
            return SavePlan('grow', edits, delta, reasons)

        if delta == 0 and all(e.delta == 0 for e in edits):
            return SavePlan('in-place', edits, 0,
                            ['every edit is length-neutral -- straight byte splice'])

        # Growth. Everything below is a refusal reason, deliberately.
        if not self.enumeration.walk_ok:
            reasons.append('the structural walk did not complete on this zone (%s), so a relink '
                           'would fall back to a blind scan -- refusing to grow'
                           % self.enumeration.walk_verdict)
            reasons.append(walk_refusal_help(self.enumeration.walk_verdict))
            return SavePlan('refuse', edits, delta, reasons)

        reasons.append('%d edit(s) change length by %+d bytes -- requires relink' %
                       (len(edits), delta))
        return SavePlan('grow', edits, delta, reasons)

    # ---------------------------------------------------------------- adding assets
    def add_asset(self, name, payload, kind='gsc'):
        """Queue a BRAND NEW asset. Nothing is written until save().

        Adding an asset grows the XAssetList array, which sits BEFORE every block-5 payload
        byte, so the relink degenerates to a uniform +8-per-asset shift -- the easiest growth
        case in the zone. That is `fullrelink/add_asset.py`, which is used verbatim here rather
        than reimplemented; it already avoids the `emit_container` alignment landmine by
        splicing in ZoneReader coordinates (patch_mp's array starts UNALIGNED at 0x11F9).

        ⚠ A new script is INERT. Adding it makes the zone carry it; nothing calls it until
        something references the name.
        """
        if any(a.name == name for a in self.assets):
            raise ValueError('%r is already in this zone' % name)
        add = PendingAdd(name, payload, kind)
        self._adds.append(add)
        return add

    def unadd(self, add):
        if add in self._adds:
            self._adds.remove(add)

    @property
    def adds(self):
        return list(self._adds)

    def _apply_adds(self, zone):
        """Append every queued asset. Returns (zone, stats) -- stats is None when nothing added."""
        if not self._adds:
            return zone, None
        with paths.backend_cwd():
            import add_asset as AA
        new = []
        for a in self._adds:
            if a.kind == 'gsc':
                body = AA.build_spt_body(a.name, a.payload)
                type_id = _console_type_id('SCRIPTPARSETREE')
            elif a.kind in ('lua', 'hks', 'rawfile'):
                body = _rawfile_body(a.name, a.payload)
                type_id = _console_type_id('RAWFILE')
            else:
                raise ValueError('cannot add an asset of kind %r' % a.kind)
            new.append((type_id, body))
        zone, stats = AA.add_assets(zone, new, verbose=False)
        return zone, stats

    def rebuild_body(self, asset, data):
        """The asset's WHOLE body with its payload replaced and its length field corrected.

        ⚠ THIS IS WHAT A GROWING EDIT MUST SUBSTITUTE, not the payload range.
        `ui_splice.MultiEdit.emit_asset(root, src_file)` keys substitutions on the asset's START
        offset and swaps the entire body, then resumes reading the source at the recorded end.
        Passing the payload offset instead means the emit never encounters the key: measured,
        every growing edit failed with "only 0/1 substitutions fired" -- a refusal, not a
        corruption, but a total block on growth.

        The body shape (see assets._blob_shape) is:

            +0  name*   = FOLLOW
            +4  len     u32          <- must track the new payload length
            +8  buffer* = FOLLOW
            +12 name, NUL-terminated
                payload, `len` bytes
                [any trailing bytes that belong to this asset's span]

        Bytes after the payload but inside the span are preserved verbatim, so per-asset
        alignment tails survive the rebuild.
        """
        z = self.zone
        span = z[asset.start:asset.end]
        head_len = asset.buf_off - asset.start          # 12 + len(name) + 1
        tail = span[head_len + asset.buf_len:]          # padding/alignment inside the span
        return (struct.pack('>III', 0xFFFFFFFF, len(data), 0xFFFFFFFF)
                + span[12:head_len]
                + data
                + tail)

    def patch_raw(self, off, data):
        """Stage a SAME-LENGTH byte replacement at an absolute zone offset.

        ⚠ SAME LENGTH IS NOT A CONVENIENCE, IT IS THE SAFETY PROPERTY. This bypasses the asset
        model entirely, so nothing here can fix up pointers: inline GfxImage pixels sit inside a
        material or FX body, not in an addressable asset, and the only edit that cannot disturb
        the pointer graph is one that changes no offsets at all.

        For the same reason `plan()` REFUSES to combine raw patches with a growing edit -- a
        relink moves every body, and these offsets were measured against the zone as it is now.
        """
        data = bytes(data)
        if off < 0 or off + len(data) > len(self.zone):
            raise ValueError('raw patch at %d (%d bytes) falls outside the zone (%d bytes)'
                             % (off, len(data), len(self.zone)))
        # Remember what was there BEFORE the first stage at this offset, so the edit can be
        # taken back without reopening the file. Only the first is kept: re-staging the same
        # image twice must still undo to the ORIGINAL bytes, not to the intermediate one.
        if not hasattr(self, '_raw_undo'):
            self._raw_undo = {}
        self._raw_undo.setdefault(int(off), bytes(self.zone[int(off):int(off) + len(data)]))
        self._raw[int(off)] = data
        return len(data)

    def unstage_raw(self, off=None):
        """Drop a staged raw patch (or all of them) and forget its undo record. -> count dropped.

        Staged edits live only in memory until Save, so this is a true undo: nothing has been
        written, and the zone bytes were never mutated in place.
        """
        if not hasattr(self, '_raw_undo'):
            self._raw_undo = {}
        if off is None:
            n = len(self._raw)
            self._raw.clear()
            self._raw_undo.clear()
            return n
        off = int(off)
        self._raw_undo.pop(off, None)
        return 1 if self._raw.pop(off, None) is not None else 0

    def staged_raw(self):
        """[(offset, nbytes)] currently staged -- so the GUI can show and offer to undo them."""
        return sorted((o, len(d)) for o, d in self._raw.items())

    def apply_in_place(self):
        """Return new zone bytes with every length-neutral edit spliced in."""
        b = bytearray(self.zone)
        for e in self.edits:
            a = e.asset
            if len(e.data) != a.buf_len:
                raise ValueError('%s: in-place splice needs exactly %d bytes, got %d'
                                 % (a.label, a.buf_len, len(e.data)))
            b[a.buf_off:a.buf_off + a.buf_len] = e.data
        for off, data in sorted(self._raw.items()):
            b[off:off + len(data)] = data
        return bytes(b)

    def build_zone(self):
        """Produce the edited zone bytes according to plan(). Raises on refuse."""
        p = self.plan()
        if not p.ok:
            raise RuntimeError('cannot save: ' + '; '.join(p.reasons))
        if p.strategy == 'in-place':
            return self.apply_in_place(), p
        if self._adds and not p.edits:
            # Pure addition: no body resizing, so the uniform-shift path is all that is needed.
            zone, stats = self._apply_adds(self.zone)
            self.last_add_stats = stats
            return zone, p
        from . import relink
        subs = {}
        for e in p.edits:
            a = e.asset
            subs[a.start] = (a.end, self.rebuild_body(a, e.data))
        zone, report = relink.grow(self.zone, subs)
        # Kept so callers can gate on the AUTHORITATIVE fields (subs fired, omap-registered
        # interior pointers, blind candidates) instead of on the phantom-dominated residue
        # count -- see core/scripts.py for the measurement behind that choice.
        self.last_relink_report = report
        if not report.ok:
            raise RuntimeError('relink refused: ' + '; '.join(report.problems))
        if self._adds:
            # Resize first, then append: add_asset re-harvests the omap from the zone it is
            # given, so it must run on the already-relinked bytes, not the original.
            zone, stats = self._apply_adds(zone)
            self.last_add_stats = stats
        self._assert_walk_survived(zone)
        return zone, p

    def _assert_walk_survived(self, zone):
        """Refuse a build that breaks the structural walk.

        ⚠ THIS IS THE BACKSTOP FOR "the payload range was wrong". A resize is only sound if the
        length field we patched really is the payload's byte length. When it is not -- e.g. an
        asset whose middle header word is a COUNT -- the rebuilt zone is well-formed enough to
        return but no longer walks, and the asset count silently changes (measured: 1528 -> 1236
        for KeyValuePairs, 1528 -> 1540 for SoundPatch). Those two types are now classified
        read-only, but this guard catches the whole CLASS rather than the two instances we
        happened to find, so a future misclassification fails loudly instead of shipping.

        Only enforced when the ORIGINAL zone walked: we never promise to improve a zone that was
        already un-walkable.
        """
        if not self.enumeration.walk_ok:
            return
        try:
            after = _assets.enumerate_zone(zone)
        except Exception as ex:
            raise RuntimeError('the rebuilt zone could not be enumerated (%s: %s) -- refusing '
                               'to hand back bytes that cannot be re-read'
                               % (type(ex).__name__, ex))
        if not after.walk_ok:
            raise RuntimeError(
                'the rebuilt zone no longer walks (%s). The edit resized something whose length '
                'field is not a byte length, so the asset stream desynced. Refusing.'
                % after.walk_verdict)
        expected = len(self.assets) + len(self._adds)
        if len(after.assets) != expected:
            raise RuntimeError(
                'asset count changed unexpectedly: %d -> %d (expected %d). The walk resynced on '
                'different boundaries, which means the edit corrupted the asset stream. Refusing.'
                % (len(self.assets), len(after.assets), expected))

    def save(self, out_path=None, verify=True):
        """Build, verify, pack and write. Returns a dict describing what happened."""
        out_path = out_path or self.ff_path
        if not out_path:
            raise ValueError('no output path')
        zone, plan = self.build_zone()

        result = dict(strategy=plan.strategy, delta=plan.total_delta,
                      edits=len(plan.edits), zone_bytes=len(zone), verified=False)

        if verify:
            from . import verify as _verify
            # ⚠ Compare the CONTAINER asset_count, not len(self.assets). Six assets in
            # patch_mp legitimately have no body (bodyless / no root struct) and so never
            # appear in the walked list -- comparing 1533 against 1527 failed on a pristine
            # retail zone.
            v = _verify.verify_zone(zone, expect_assets=self.facts.asset_count
                                    if plan.strategy == 'in-place' else None)
            result['verify'] = v.summary()
            if not v.ok:
                raise RuntimeError('verification failed, refusing to write: %s'
                                   % '; '.join(v.problems))
            result['verified'] = True

        if out_path.lower().endswith('.zone'):
            blob = zone
        else:
            blob = wiiu_ff.pack(zone, self.name)
        with open(out_path, 'wb') as fh:
            fh.write(blob)

        result.update(path=out_path, ff_bytes=len(blob),
                      zone_md5=hashlib.md5(zone).hexdigest(),
                      ff_md5=hashlib.md5(blob).hexdigest())

        # re-seat the session on the saved bytes so further edits stay valid
        self.zone = zone
        self.ff_path = out_path
        self.base_md5 = result['zone_md5']
        self._edits.clear()
        self._raw.clear()
        self.enumeration = _assets.enumerate_zone(zone)
        self.facts = ZF.Facts(zone, name=self.name)
        return result

    # ---------------------------------------------------------------- misc
    def summary(self):
        d = dict(name=self.name, ff_path=self.ff_path, zone_bytes=len(self.zone),
                 zone_md5=self.base_md5, dirty=self.dirty, delta=self.total_delta)
        d.update(self.enumeration.summary())
        return d

    def __repr__(self):
        return '<ZoneSession %s %d assets %d B>' % (self.name, len(self.assets), len(self.zone))
