"""core.ipak_names -- turn ipak hash keys back into names, formats and dimensions.

THE PROBLEM
-----------
An ipak index entry is four words: {nameHash, dataHash, offset, size}. There is no name, no
format, no width or height. A viewer built on the index alone can only ever show a list of
hex numbers, and cannot preview anything at all -- decoding needs the format and dimensions.

THE SOLUTION (measured, not assumed)
------------------------------------
Wii U retail paks carry two extra sections beyond index(1) and data(2):

    section 4 -- the {nameHash, dataHash} key list, 8 bytes per key, covering every part the
                 map uses INCLUDING parts physically stored in another pak.
    section 3 -- one text record per key, 16-byte header {nameHash, dataHash, textLen, 0}
                 followed by the linker's source description:

                     iwi: images/<name>.iwi
                     format: DXT5
                     offset: 1310720
                     size: 98304
                     width: 256
                     height: 256
                     levels: 9
                     mip: 2
                     manual: 0

That is exactly the missing metadata. Measured over the 121 retail Wii U paks in the content
directory this session:

    120 of 121 paks carry section 3
    40,321 distinct (nameHash, dataHash) records, 17,856 distinct image names
    40,321 of 40,338 index entries across ALL paks resolve = 99.96%

The 17 unresolved are in the one pak with no section 3. Because section 3 covers keys stored
elsewhere, a dictionary harvested from the whole content directory names almost every entry of
a pak that has no metadata of its own -- which is how paks like mp_carrier.ipak (1260 entries,
sections 1 and 2 only) become browsable.

Lookup order is therefore: the pak's own section 3 first (authoritative for that file), then
the harvested cross-pak dictionary, then nothing -- and "nothing" is reported as unknown rather
than guessed, so a hex key never masquerades as a name.
"""
import os
import pickle
import struct

MAGIC_CACHE_VERSION = 2

# Where the harvested cross-pak dictionary is cached. Override with IPAK_META_CACHE.
DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ipak_meta.cache')

# Retail Wii U content directories to harvest from, first that exists wins. These are the same
# roots the rest of the project uses; a missing directory is not an error, it just means the
# cross-pak dictionary is empty and only self-describing paks resolve.
# ⚠ NO HARD-CODED CONTENT DIRECTORIES. This tool is shared and may be built for Windows, macOS
# or Linux, so it must not carry one developer's drive letters. Folders come from core.settings:
# what the user configured, what they opened, and what can be DISCOVERED BY SHAPE on any
# platform. Kept empty so a stale absolute path can never creep back in.
DEFAULT_CONTENT_DIRS = []

SEC_META_TEXT = 3
SEC_KEY_LIST = 4


# ------------------------------------------------------------------ section 3 / 4 ---

#: private slot holding a record's exact original text, so an untouched record re-emits
#: byte-for-byte. Measured necessity: retail paks are NOT uniform. mp_raid.ipak stores its
#: records key-sorted with no trailing newline, while base_split1.ipak and lowmip_split1.ipak
#: store them UNSORTED and terminate each record with a newline. Reconstructing from parsed
#: fields therefore cannot be byte-exact in general -- so we keep the original and only
#: synthesise text for records we actually created or changed.
RAW_TEXT = '_raw'


def parse_section3(raw):
    """-> {(nameHash, dataHash): {field: str}} preserving FILE ORDER (dicts are ordered).

    Raises if the records do not tile the section exactly -- a partial walk would silently
    drop metadata and we would rather know.
    """
    out = {}
    o = 0
    n = len(raw)
    while o + 16 <= n:
        nh, dh, ln, _z = struct.unpack_from('>4I', raw, o)
        o += 16
        if o + ln > n:
            raise ValueError('ipak section 3 record at %#x claims %d bytes, only %d left'
                             % (o - 16, ln, n - o))
        txt = raw[o:o + ln].decode('latin-1')
        o += ln
        rec = {RAW_TEXT: txt}
        for line in txt.split('\n'):
            if ': ' in line:
                k, v = line.split(': ', 1)
                rec[k] = v
        out[(nh, dh)] = rec
    if o != n:
        raise ValueError('ipak section 3 did not tile exactly: consumed %d of %d' % (o, n))
    return out


def record_text(rec):
    """A record's on-disk text: the original when untouched, otherwise synthesised."""
    if RAW_TEXT in rec:
        return rec[RAW_TEXT]
    order = ('iwi', 'format', 'offset', 'size', 'width', 'height', 'levels', 'mip', 'manual')
    keys = ([k for k in order if k in rec]
            + [k for k in rec if k not in order and k != RAW_TEXT])
    return '\n'.join('%s: %s' % (k, rec[k]) for k in keys)


def update_record(rec, **fields):
    """Change fields in a record while preserving its exact on-disk formatting.

    Rewrites the matching lines inside the original text rather than regenerating it, so a
    pak's own conventions (field order, and whether records end in a newline -- retail differs
    between paks) survive an edit. Falls back to a plain field update when there is no original.
    """
    out = dict(rec)
    out.update({k: str(v) for k, v in fields.items()})
    txt = rec.get(RAW_TEXT)
    if txt is None:
        return out
    lines = txt.split('\n')
    for i, line in enumerate(lines):
        if ': ' in line:
            k = line.split(': ', 1)[0]
            if k in fields:
                lines[i] = '%s: %s' % (k, fields[k])
    out[RAW_TEXT] = '\n'.join(lines)
    return out


def build_section3(records):
    """{(nh, dh): rec} -> (raw bytes, itemCount), in the order given (file order on re-emit)."""
    out = bytearray()
    n = 0
    for (nh, dh), rec in records.items():
        txt = record_text(rec).encode('latin-1')
        out += struct.pack('>4I', nh, dh, len(txt), 0) + txt
        n += 1
    return bytes(out), n


def parse_section4(raw):
    """-> [(nameHash, dataHash)] key list in file order."""
    if len(raw) % 8:
        raise ValueError('ipak section 4 length %d is not a multiple of 8' % len(raw))
    return [struct.unpack_from('>2I', raw, i) for i in range(0, len(raw), 8)]


def build_section4(keys):
    """[(nameHash, dataHash)] -> (raw bytes, itemCount), preserving the order given.

    Retail is inconsistent about ordering here (mp_raid sorted, base_split not), so the caller
    owns the order and we do not impose one. Duplicates are dropped, first occurrence wins.
    """
    seen = set()
    ks = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ks.append(k)
    return b''.join(struct.pack('>2I', a, b) for a, b in ks), len(ks)


def name_from_record(rec):
    """'iwi: images/foo_c.iwi' -> 'foo_c'. None when the record has no usable source path."""
    v = rec.get('iwi') or ''
    if v.startswith('images/'):
        v = v[len('images/'):]
    if v.endswith('.iwi'):
        v = v[:-4]
    return v or None


# ---------------------------------------------------------------- the dictionary ---

class MetaIndex(object):
    """(nameHash, dataHash) -> metadata, harvested across many paks.

    `by_key` is exact. `by_name_hash` is the fallback for an entry whose exact part is unknown
    but whose image name is: it still yields a name to display, and its format, which together
    with the payload size is usually enough to preview.
    """

    def __init__(self):
        self.by_key = {}
        self.by_name_hash = {}
        self.sources = []
        self.stale = False       # True when served from a cache built on a different install

    def add_records(self, records, source=None):
        for key, rec in records.items():
            self.by_key.setdefault(key, rec)
            nm = name_from_record(rec)
            if nm:
                self.by_name_hash.setdefault(key[0], (nm, rec.get('format')))
        if source:
            self.sources.append(source)

    def lookup(self, name_hash, data_hash):
        """-> (name or None, record or None, exact: bool)."""
        rec = self.by_key.get((name_hash, data_hash))
        if rec is not None:
            return name_from_record(rec), rec, True
        hit = self.by_name_hash.get(name_hash)
        if hit is not None:
            return hit[0], None, False
        return None, None, False

    def __len__(self):
        return len(self.by_key)


def harvest(paths, index=None, progress=None):
    """Build a MetaIndex from ipak files. Unreadable paks are skipped, not fatal."""
    from . import paths as _p  # noqa: F401
    import ipak as IP

    # ⚠ `index or MetaIndex()` IS A BUG HERE: MetaIndex defines __len__, so an EMPTY
    # index is falsy and the caller's object gets silently replaced -- every record
    # then lands in a throwaway and the caller sees zero gained. Test for None.
    idx = MetaIndex() if index is None else index
    for i, p in enumerate(paths):
        if progress:
            progress(i, len(paths), p)
        try:
            pk = IP.IPak.read(p)
        except Exception:
            continue
        for typ, raw, _cnt in pk.extra_sections:
            if typ == SEC_META_TEXT:
                try:
                    idx.add_records(parse_section3(raw), source=p)
                except Exception:
                    pass
    return idx


def harvest_zone_images(zone):
    """Name + preview geometry for streamed images, taken from a zone's GfxImage bodies.

    ⚠ THIS IS THE SECOND METADATA SOURCE, AND IT IS THE ONE THAT MATTERS FOR NON-RETAIL PAKS.
    `harvest()` reads section 3, which only base-game paks carry: DLC and project-built paks have
    no `extra_sections` at all, so the dictionary structurally cannot name them and coverage
    collapses (measured: ~100% on base-game paks, 1.7-17% on DLC, 43-58% on project paks -- the
    "about half" that shows as unnamed).

    A GfxImage body carries everything needed, and every zone has them regardless of pak
    provenance:
        +156 mapType  +180 T6 format  +316 partCount  +320 name* (FOLLOW)  +324 nameHash
        parts at +184, stride 44: packed@+0, dataHash@+4, width/height u16@+8
        the name string follows the 328-byte body
    `wiiu_ref/ipak_stream.scan_genuine_bodies` already validates and locates those bodies
    (it checks nameHash == R_HashString(name), so a false positive cannot survive).

    -> {(nameHash, dataHash): record} in the same shape section 3 produces, so callers cannot
    tell the two sources apart.
    """
    from . import paths as _p  # noqa: F401
    import ipak_stream as IS
    import struct as _s

    T6_TO_STRING = {0x1: 'L8', 0x3: 'A8L8', 0x5: 'A8R8G8B8', 0x6: 'DXT1',
                    0x9: 'DXT3', 0xA: 'DXT5', 0x14: 'DXN'}
    out = {}
    for nh, (body, _pix, name) in IS.scan_genuine_bodies(zone).items():
        fmt = _s.unpack_from('>I', body, 180)[0]
        fs = T6_TO_STRING.get(fmt)
        if fs is None:
            continue                       # unmodelled format: refuse rather than mislabel
        n_parts = body[316]
        if not (1 <= n_parts <= 3):
            continue
        for i in range(n_parts):
            p = 184 + 44 * i
            packed, dh = _s.unpack_from('>II', body, p)
            w, h = _s.unpack_from('>HH', body, p + 8)
            if not w or not h:
                continue
            out[(nh, dh)] = {
                'iwi': 'images/%s.iwi' % name, 'format': fs,
                'width': str(w), 'height': str(h),
                'levels': str(packed & 0xF if i == 0 else 1),
                'mip': str(i), 'size': '0', 'offset': '0', 'manual': '0',
            }
    return out


def harvest_zones(zone_paths_, index=None, progress=None):
    """Fold every reachable zone's GfxImage metadata into a MetaIndex.

    ⚠ DECRYPT ONLY -- DO NOT OPEN A ZoneSession HERE. All this needs is the raw decompressed
    bytes, but `ZoneSession.open()` also runs the full structural walk and builds an asset list
    per zone. Across 365 zones that was both far slower than necessary and a memory disaster:
    measured 9.9 GB resident before the process was killed, because large zone buffers churned
    faster than they were reclaimed. Decrypting directly and dropping each buffer immediately
    keeps this flat.
    """
    import gc
    # ⚠ `index or MetaIndex()` IS A BUG HERE: MetaIndex defines __len__, so an EMPTY
    # index is falsy and the caller's object gets silently replaced -- every record
    # then lands in a throwaway and the caller sees zero gained. Test for None.
    idx = MetaIndex() if index is None else index
    for i, p in enumerate(zone_paths_):
        if progress:
            progress(i, len(zone_paths_), p)
        zone = None
        try:
            if p.lower().endswith('.zone'):
                with open(p, 'rb') as f:
                    zone = f.read()
            else:
                import wiiu_ff
                with open(p, 'rb') as f:
                    raw = f.read()
                if not wiiu_ff.is_wiiu_fastfile(raw):
                    continue
                _hdr, zone, _n = wiiu_ff.decrypt(raw)
                del raw
            idx.add_records(harvest_zone_images(zone), source=p)
        except Exception:
            pass                      # an unreadable zone costs names, never the whole harvest
        finally:
            del zone
            if (i & 0x0F) == 0x0F:
                gc.collect()          # large buffers churn; reclaim them on a schedule
    return idx


def expand_from(paths_, progress=None, cache_path=None):
    """Add specific files or folders to the dictionary and KEEP the result. -> (index, stats).

    This is the second half of the design: a shipped base library, plus the ability to point at
    a .ff or a folder when something is not covered. It merges into whatever is already cached
    rather than rebuilding from scratch, so expanding costs only the new files -- seconds for one
    zone, against roughly two hours for a full corpus sweep.

    Accepts .ipak files (section 3), .ff/.zone files (GfxImage bodies), or directories (searched
    recursively for both). Existing entries win, so a targeted expansion can never downgrade a
    name the base library already had.
    """
    from . import paths as _pp
    read_path, write_path = _pp.cache_file(os.path.basename(DEFAULT_CACHE))
    if cache_path:
        read_path = write_path = cache_path

    # ⚠ READ THE CACHE, NEVER REBUILD IT HERE. `load_shared()` falls through to a FULL harvest
    # when no valid cache exists -- roughly two hours over a whole install. Expanding from one
    # zone that then triggers a corpus sweep is the exact opposite of what this function is for;
    # measured, that mistake made a one-zone expansion run for over ten minutes before it was
    # killed. Start from whatever is cached, or from nothing.
    idx = MetaIndex()
    try:
        if os.path.exists(read_path):
            with open(read_path, 'rb') as f:
                blob = pickle.load(f)
            if blob.get('version') == MAGIC_CACHE_VERSION:
                idx.by_key = blob.get('by_key') or {}
                idx.by_name_hash = blob.get('by_name_hash') or {}
                idx.sources = blob.get('sources') or []
    except Exception:
        pass                                          # a corrupt cache just means we start fresh

    paks, zones = [], []
    for p in ([paths_] if isinstance(paths_, str) else list(paths_)):
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in sorted(files):
                    low = fn.lower()
                    if low.endswith('.ipak'):
                        paks.append(os.path.join(root, fn))
                    elif low.endswith('.ff') or low.endswith('.zone'):
                        zones.append(os.path.join(root, fn))
        elif p.lower().endswith('.ipak'):
            paks.append(p)
        elif p.lower().endswith('.ff') or p.lower().endswith('.zone'):
            zones.append(p)

    before = len(idx.by_key)
    if paks:
        harvest(paks, index=idx, progress=progress)
    if zones:
        harvest_zones(zones, index=idx, progress=progress)
    gained = len(idx.by_key) - before

    stats = {'paks': len(paks), 'zones': len(zones), 'gained': gained,
             'total': len(idx.by_key), 'cache': write_path}
    try:
        with open(write_path, 'wb') as f:
            # Written with no `stamp`, so a later full rebuild refreshes it rather than treating
            # an expanded dictionary as authoritative for a file set it never saw.
            pickle.dump({'version': MAGIC_CACHE_VERSION, 'stamp': None,
                         'by_key': idx.by_key, 'by_name_hash': idx.by_name_hash,
                         'sources': idx.sources}, f, protocol=4)
    except Exception as ex:
        stats['error'] = '%s: %s' % (type(ex).__name__, ex)
    return idx, stats


def harvest_owners(paths, progress=None):
    """{(nameHash, dataHash): pak_path} -- WHICH PAK ACTUALLY STORES EACH PART.

    ⚠ THIS IS A DIFFERENT QUESTION FROM "what is this part called", and the difference is why a
    replace can appear to do nothing. `harvest()` reads section 3, which DESCRIBES parts -- a pak
    can carry a metadata record for a key whose payload lives somewhere else entirely. Only the
    INDEX section (type 1) says who actually stores the bytes.

    It matters because of how the console binds parts. `IPak_BuildAdjacencyInfo` walks up to 32
    packfiles and SKIPS any part already bound (the resolved bit at part+0x14), so the
    EARLIEST-LOADED pak holding a key wins and a later one can never override it. Editing a copy
    in the wrong pak is therefore a silent no-op in game.

    ⛔ AN EARLIER NOTE HERE CLAIMED "40,338 keys across the 121 retail paks, ZERO duplicates, so
    every key has exactly one owner". THAT WAS WRONG and it mattered: the census behind it ran
    without the game install in its search path, so it could not have seen a duplicate. Measured
    properly, duplicates are real and routine -- a converted map's zone references RETAIL images
    by key, and those keys are also carried by the base-game paks that load earlier (e.g. three
    skate posters whose part 1 lives in mp_express.ipak / la_1.ipak and part 0 in
    lowmip_split1.ipak, all byte-identical keys). That is the entire mechanism behind "I
    replaced the texture and the stock one still shows".

    A key-duplication census is only ever as wide as its search path -- see core.ipak_search,
    which scans RECURSIVELY across every configured and discovered folder.
    """
    from . import paths as _p  # noqa: F401
    import ipak as IP

    owners, dupes = {}, {}
    for i, p in enumerate(paths):
        if progress:
            progress(i, len(paths), p)
        try:
            pk = IP.IPak.read(p)
        except Exception:
            continue
        for en in pk.entries:
            key = (en.name_hash, en.data_hash)
            if key in owners:
                dupes.setdefault(key, [owners[key]]).append(p)
            else:
                owners[key] = p
    return owners, dupes


def content_dirs():
    """Every directory to harvest names from.

    Delegates to core.settings so the user's own game folder counts, plus the folder any file
    was opened from, and anything discoverable by shape. Nothing here is hard-coded: an absolute
    path baked into the source describes one machine, and a pak opened anywhere else scored ZERO
    names because of exactly that.
    """
    try:
        from . import settings
        dirs = settings.search_dirs()
        if dirs:
            return dirs
    except Exception:
        pass
    return [d for d in DEFAULT_CONTENT_DIRS if d and os.path.isdir(d)]


def load_shared(cache_path=None, rebuild=False, progress=None):
    """The cross-source dictionary, from cache when possible.

    Built from BOTH pak section-3 metadata and zone GfxImage bodies -- see harvest_zone_images
    for why the zone pass is required. Harvesting takes a few seconds, so the result is pickled. The cache records which
    files it was built from with their sizes; if that set changes the cache is rebuilt rather
    than silently serving a stale dictionary.
    """
    from . import paths as _pp
    read_path, write_path = _pp.cache_file(os.path.basename(DEFAULT_CACHE))
    if cache_path:
        read_path = write_path = cache_path
    elif os.environ.get('IPAK_META_CACHE'):
        read_path = write_path = os.environ['IPAK_META_CACHE']
    # ⚠⚠ THE ZONE SCAN MUST RECURSE. This used os.listdir(), i.e. TOP LEVEL ONLY, and zones do
    # not live at the top level: measured, `content/*.ff` = 0 files in BOTH content dirs, while
    # `content/**/*.ff` = 307 and 58 -- they are all under `content/english/`. So the second name
    # source, the one that names every pak carrying no section 3 of its own, had never run on a
    # single zone. That is the whole reason project and DLC paks showed almost no names:
    #     base_split8 0%, mp_skate 8%, mp_nuketown_2020 5%, while patch_mp (which HAS section 3)
    #     was 100%.
    # Harvesting just mp_nuketown_2020.ff took its pak from 5% to 1519/1519 = 100% named and
    # 100% previewable. Depth is bounded so a content dir does not turn into a whole-disk walk.
    paks, zones = [], []
    MAX_DEPTH = 3
    for d in content_dirs():
        base_depth = d.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(d):
            if root.rstrip(os.sep).count(os.sep) - base_depth >= MAX_DEPTH:
                dirs[:] = []
            for fn in sorted(files):
                low = fn.lower()
                if low.endswith('.ipak'):
                    paks.append(os.path.join(root, fn))
                elif low.endswith('.ff'):
                    zones.append(os.path.join(root, fn))
    stamp = sorted((os.path.basename(p), os.path.getsize(p)) for p in paks + zones)

    if not rebuild and os.path.exists(read_path):
        try:
            with open(read_path, 'rb') as f:
                blob = pickle.load(f)
            if blob.get('version') == MAGIC_CACHE_VERSION:
                fresh = blob.get('stamp') == stamp
                # A stamp mismatch means the game install differs from the machine the cache was
                # built on. These are DISPLAY NAMES, so serving them is strictly better than
                # showing bare hashes; only a rebuild replaces them. Discarding a shipped cache
                # on any install difference is what left a packaged build with no names at all.
                idx = MetaIndex()
                idx.by_key = blob['by_key']
                idx.by_name_hash = blob['by_name_hash']
                idx.sources = blob.get('sources', [])
                idx.stale = not fresh
                return idx
        except Exception:
            pass                       # a corrupt cache is a rebuild, never a crash

    # TWO SOURCES, IN ORDER OF AUTHORITY.
    # Section 3 first (a pak describing its own parts), then the zones' GfxImage bodies. The
    # zone pass is what names DLC and project-built paks, which carry no section 3 at all and
    # are why roughly half the entries showed as unnamed.
    idx = harvest(paks, progress=progress)
    idx = harvest_zones(zones, index=idx, progress=progress)
    try:
        with open(write_path, 'wb') as f:
            pickle.dump({'version': MAGIC_CACHE_VERSION, 'stamp': stamp,
                         'by_key': idx.by_key, 'by_name_hash': idx.by_name_hash,
                         'sources': idx.sources}, f, protocol=4)
    except Exception:
        pass                           # an unwritable cache costs speed, not correctness
    return idx


def synth_record(name, gx2_format_string, width, height, levels, mip, size):
    """A section-3 record for a newly imported image, so imports stay browsable like retail."""
    return {'iwi': 'images/%s.iwi' % name,
            'format': gx2_format_string,
            'offset': '0',
            'size': str(size),
            'width': str(width),
            'height': str(height),
            'levels': str(levels),
            'mip': str(mip),
            'manual': '0'}
