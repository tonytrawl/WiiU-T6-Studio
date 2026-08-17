"""core.ipak_search -- "which pak has this texture?", answered from the prebuilt dictionary.

WHY THIS EXISTS
---------------
The dictionary already knows the NAME of ~18,700 images and the metadata of ~42,000 parts. What
it could not tell you is the question people actually ask: *where does this thing live, and is
there more than one copy of it?*

That question is not cosmetic. The console binds each image part from the FIRST pak it finds it
in and skips any part already bound, so a same-key copy in an earlier-loading pak permanently
shadows the map's own pak -- an edit into the wrong file reports success and delivers nothing.
Answering "which paks hold this key" up front is the difference between a five-second lookup and
an afternoon of confusion.

TWO SOURCES, JOINED
-------------------
  names      the prebuilt dictionary (core.ipak_names) -- nameHash -> name, and
             (nameHash, dataHash) -> per-part record (format, w/h, levels, mip, size)
  ownership  a scan of the INDEX section of every reachable .ipak -- (nameHash, dataHash) ->
             every pak carrying it

⚠ THE OWNERSHIP SCAN MUST BE RECURSIVE AND MUST SPAN THE WHOLE SEARCH PATH. The previous owner
index globbed `<content_dir>/*.ipak` only: not recursive (so it missed the AOC layout, where a
map's own pak sits in a numbered subfolder like `0010/`) and limited to configured content dirs
(so with no install configured it saw nothing at all and reported no duplicates). Both are why
the "this image is also in another pak" warning stayed silent in the case that motivated this
module. A census is only ever as wide as its search path.
"""
import os
import pickle
import struct
import time

from . import paths  # noqa: F401  (puts wiiu_ref on sys.path)
from . import ipak_names as NM

CACHE_VERSION = 1
DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ipak_owners.cache')

MAX_DEPTH = 4          # deep enough for <content>/<title>/0010/, shallow enough to stay quick


def _iter_paks(dirs=None):
    """Every .ipak under the effective search path, de-duplicated by real path."""
    from . import settings as _st
    seen, out = set(), []
    for d in (dirs if dirs is not None else _st.search_dirs()):
        if not d or not os.path.isdir(d):
            continue
        base = d.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(d):
            if dirpath.rstrip(os.sep).count(os.sep) - base >= MAX_DEPTH:
                dirnames[:] = []
            for fn in sorted(files):
                if not fn.lower().endswith('.ipak'):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    real = os.path.normcase(os.path.realpath(p))
                except OSError:
                    real = os.path.normcase(p)
                if real not in seen:
                    seen.add(real)
                    out.append(p)
    return out


def _read_index(path):
    """(nameHash, dataHash) pairs from a pak's INDEX section, without decoding any payload.

    Deliberately hand-rolled rather than going through IPak.read(): this only needs the key
    list, and a truncated or odd pak must yield nothing instead of taking the scan down.
    """
    keys = []
    with open(path, 'rb') as f:
        head = f.read(16)
        if len(head) < 16 or head[:4] not in (b'IPAK', b'KAPI'):
            return keys
        be = head[:4] == b'IPAK'
        e = '>' if be else '<'
        _ver, _size, nsec = struct.unpack(e + '3I', head[4:16])
        if not (0 < nsec < 64):
            return keys
        table = f.read(16 * nsec)
        if len(table) < 16 * nsec:
            return keys
        for i in range(nsec):
            typ, off, size, count = struct.unpack_from(e + '4I', table, 16 * i)
            if typ != 1:                      # 1 = index
                continue
            if count <= 0 or count > 5_000_000:
                continue
            f.seek(off)
            blob = f.read(16 * count)
            if len(blob) < 16 * count:
                count = len(blob) // 16       # truncated file: take what is really there
            for k in range(count):
                a, b, _eo, _es = struct.unpack_from(e + '4I', blob, 16 * k)
                # The key is one u64 serialised in FILE endianness, so the word order flips:
                # big-endian stores nameHash first, little-endian (PC) stores dataHash first.
                nh, dh = (a, b) if be else (b, a)
                keys.append((nh, dh))
    return keys


def _stamp(paks):
    out = []
    for p in paks:
        try:
            stt = os.stat(p)
            out.append((os.path.normcase(os.path.abspath(p)), stt.st_size, int(stt.st_mtime)))
        except OSError:
            continue
    return sorted(out)


def owner_map(dirs=None, progress=None, use_cache=True, rebuild=False):
    """-> ({(nameHash, dataHash): [pak, ...]}, [pak, ...] scanned).

    Scanning ~120 paks' index sections takes a couple of seconds, so the result is cached on
    disk against a (path, size, mtime) stamp and reused until the pak set actually changes.
    """
    paks = _iter_paks(dirs)
    stamp = _stamp(paks)
    read_path, write_path = paths.cache_file(os.path.basename(DEFAULT_CACHE))

    if use_cache and not rebuild:
        for cand in (write_path, read_path):
            try:
                if cand and os.path.exists(cand):
                    with open(cand, 'rb') as f:
                        blob = pickle.load(f)
                    if blob.get('version') == CACHE_VERSION and blob.get('stamp') == stamp:
                        return blob['owners'], blob.get('paks') or paks
            except Exception:
                pass                       # a corrupt or stale cache just means we rescan

    owners = {}
    for i, p in enumerate(paks):
        if progress:
            progress(i, len(paks), p)
        try:
            for key in _read_index(p):
                owners.setdefault(key, []).append(p)
        except Exception:
            continue                       # an unreadable pak is skipped, never fatal
    try:
        with open(write_path, 'wb') as f:
            pickle.dump({'version': CACHE_VERSION, 'stamp': stamp, 'owners': owners,
                         'paks': paks, 'built': time.time()}, f, protocol=4)
    except Exception:
        pass                               # a read-only location is not a reason to fail
    return owners, paks


# ------------------------------------------------------------------ searching
def _as_hash(query):
    """Accept a raw hash as well as a name: '0A0FEE98', '0x0a0fee98'."""
    q = query.strip().lower()
    if q.startswith('0x'):
        q = q[2:]
    if 6 <= len(q) <= 8 and all(c in '0123456789abcdef' for c in q):
        try:
            return int(q, 16)
        except ValueError:
            return None
    return None


# v2 added the image NAME to each record. v1 caches are still accepted and normalised, but a
# search over a v1 index can only find images whose name the PAK dictionary already knew -- which
# measured 105 of 3,944, i.e. it missed 97% of them. See _zrec and search() below.
ZONE_CACHE_VERSION = 2
ZONE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_zone_images.cache')

#: one indexed inline image: (zone basename, width, height, gx2 format, levels, bytes, name)
ZREC_LEN = 7


def _zrec(t):
    """Normalise an indexed record to the current width, so a v1 cache still loads (name=None)."""
    t = tuple(t)
    return t if len(t) >= ZREC_LEN else t + (None,) * (ZREC_LEN - len(t))


def zone_names(zone_images):
    """{nameHash: name} for every indexed inline image that carries a name.

    This is the half of the index that makes inline images SEARCHABLE. Without it the only names
    in play come from pak metadata, and an image that exists solely inside a fastfile has no pak
    metadata by definition -- so it could never be typed into the search box at all.
    """
    out = {}
    for nh, recs in (zone_images or {}).items():
        for r in recs:
            nm = _zrec(r)[6]
            if nm:
                out[nh] = nm
                break
    return out


def _iter_zones(dirs=None):
    from . import settings as _st
    seen, out = set(), []
    for d in (dirs if dirs is not None else _st.search_dirs()):
        if not d or not os.path.isdir(d):
            continue
        base = d.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(d):
            if dirpath.rstrip(os.sep).count(os.sep) - base >= MAX_DEPTH:
                dirnames[:] = []
            for fn in sorted(files):
                if not fn.lower().endswith('.ff'):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    real = os.path.normcase(os.path.realpath(p))
                except OSError:
                    real = os.path.normcase(p)
                if real not in seen:
                    seen.add(real)
                    out.append(p)
    return out


def zone_image_map(dirs=None, progress=None, rebuild=False, limit_zones=None, build=True,
                   should_stop=None):
    """-> ({nameHash: [(zone_path, width, height, fmt, levels, bytes)]}, [zones scanned]).

    ⚠ WITHOUT THIS, SEARCH LIES BY OMISSION. A texture whose pixels are carried INLINE in a
    fastfile is in no pak at all, so a pak-only index reports "in NO pak on this machine" --
    which reads as "missing" when the honest answer is "it is inside common_zm.ff". Measured:
    common_zm is 293 inline of 391 records, common_mp 836 of 1684, so this is most of a zone's
    images, not an edge case.

    Decrypting and censusing every zone is slow, so the result is cached against a
    (path, size, mtime) stamp exactly like the pak owner index.
    """
    zones = _iter_zones(dirs)
    if limit_zones:
        zones = zones[:limit_zones]
    stamp = _stamp(zones)
    read_path, write_path = paths.cache_file(os.path.basename(ZONE_CACHE))
    if not rebuild:
        fallback = None
        for cand in (write_path, read_path):
            try:
                if not cand or not os.path.exists(cand):
                    continue
                with open(cand, 'rb') as f:
                    blob = pickle.load(f)
                # A v1 index is DEGRADED, not useless: its hashes and dimensions are still right,
                # it just carries no names. Accept it (normalised) rather than handing the user an
                # empty index, and let a rebuild upgrade it.
                if blob.get('version') not in (1, ZONE_CACHE_VERSION):
                    continue
                blob['images'] = {nh: [_zrec(r) for r in recs]
                                  for nh, recs in (blob.get('images') or {}).items()}
                if blob.get('stamp') == stamp:
                    return blob['images'], blob.get('zones') or zones
                # ⚠ A SHIPPED INDEX MUST SURVIVE A STAMP MISMATCH. The stamp lists every zone's
                # path, size and mtime, so an index built on the machine that produced the
                # program can NEVER match a user's install -- and discarding it there would
                # hand every new user an empty index and a two-hour rebuild, which is exactly
                # the failure the shipped ipak/sab name caches already had. Records are keyed
                # by zone BASENAME precisely so they still resolve on someone else's disk.
                if blob.get('shipped') and fallback is None:
                    fallback = (blob['images'], blob.get('zones') or [])
            except Exception:
                pass
        if fallback is not None:
            return fallback

    if not build:
        # Cache-only: decrypting and censusing every zone takes minutes, which is not something
        # to start behind a user who just opened a search box. The caller offers it as an
        # explicit action instead.
        return {}, []

    out = {}
    done = []
    aborted = False
    for i, zp in enumerate(zones):
        if should_stop is not None and should_stop():
            aborted = True
            break
        if progress:
            progress(i, len(zones), zp)
        try:
            from . import ZoneSession
            from . import zone_images as ZI
            zone = ZoneSession.open(zp).zone
            for img in ZI.list_images(zone, inline_only=True):
                if not img.name_hash:
                    continue
                # BASENAME, never an absolute path -- see the shipped-index note above.
                # The NAME is carried too: it is the only place an inline-only image is named,
                # so dropping it here is what made 97% of them unsearchable.
                out.setdefault(img.name_hash, []).append(
                    (os.path.basename(zp), img.width, img.height, img.gx2_format,
                     img.levels, img.pixel_len, img.name))
            done.append(zp)
        except Exception:
            continue                       # an unreadable zone is skipped, never fatal
    if progress:
        progress(len(done), len(zones), None)

    # ⚠ NEVER CACHE A CANCELLED RUN. The stamp describes the FULL zone set, so writing partial
    # results under it would look complete on every future load and permanently hide whatever
    # was not scanned. Partial results are still returned for this session -- they are real,
    # just incomplete -- but nothing is persisted unless the sweep finished.
    if not aborted:
        try:
            with open(write_path, 'wb') as f:
                pickle.dump({'version': ZONE_CACHE_VERSION, 'stamp': stamp, 'images': out,
                             'zones': done, 'built': time.time()}, f, protocol=4)
        except Exception:
            pass
    return out, done


def search(query, index=None, owners=None, limit=200, dirs=None, zone_images=None):
    """Find images whose NAME matches `query`, and report where every part of them lives.

    Returns a list of dicts, best-known first:

        {'name', 'name_hash', 'format', 'parts': [...], 'shadowed': bool, 'pak_count': int}

    where each part is

        {'part', 'data_hash', 'paks': [...], 'width', 'height', 'size', 'levels', 'mip'}

    `shadowed` is True when ANY part is carried by more than one pak -- i.e. editing one copy
    may be overridden by whichever loads first. That flag is the whole point of the feature.
    """
    idx = index if index is not None else NM.load_shared()
    own = owners
    if own is None:
        own, _paks = owner_map(dirs=dirs)
    # ⚠ SELF-LOAD, like `owners` above. This used to default to {} when the caller passed nothing,
    # which silently reduced the feature to a pak-only search -- every inline image reported as
    # "in no pak on this machine", which reads as missing. build=False keeps it cache-only:
    # building the index decrypts every fastfile and takes minutes, which is not something to
    # start behind someone who just typed into a search box.
    zimgs = zone_images
    if zimgs is None:
        zimgs, _z = zone_image_map(dirs=dirs, build=False)
    znames = zone_names(zimgs)

    want_hash = _as_hash(query)
    q = query.strip().lower()

    # THE CANDIDATE SET IS THE UNION OF BOTH NAME SOURCES.
    # Measured before this union: of 3,944 indexed inline images, only 105 had a name the pak
    # dictionary knew -- so 97% of them could not be typed into the search box at all, because
    # an image carried inside a fastfile has no pak metadata to be named from by definition.
    by_nh = dict((idx.by_name_hash or {}) if idx is not None else {})
    for nh, nm in znames.items():
        if nh not in by_nh:
            by_nh[nh] = (nm, None)          # no pak metadata => no format from that side
    if not by_nh:
        return []

    # nameHash -> (name, format), then the parts for each hit out of by_key.
    hits = []
    for nh, val in by_nh.items():
        name = val[0] if isinstance(val, (tuple, list)) else val
        fmt = val[1] if isinstance(val, (tuple, list)) and len(val) > 1 else None
        if want_hash is not None:
            if nh != want_hash:
                continue
        elif not q or q not in (name or '').lower():
            continue
        hits.append((nh, name, fmt))
        if len(hits) >= limit * 4:          # cap the candidate sweep, ranked below
            break

    # Exact name first, then prefix, then the rest -- so a precise query lands on top.
    def rank(h):
        nm = (h[1] or '').lower()
        return (0 if nm == q else 1 if nm.startswith(q) else 2, len(nm), nm)
    hits.sort(key=rank)
    hits = hits[:limit]

    by_hash = {}
    want_nh = set(h[0] for h in hits)
    for (nh, dh), rec in ((idx.by_key or {}) if idx is not None else {}).items():
        if nh in want_nh:
            by_hash.setdefault(nh, []).append((dh, rec))

    out = []
    for nh, name, fmt in hits:
        parts = []
        allp = set()
        shadowed = False
        for dh, rec in sorted(by_hash.get(nh, []), key=lambda x: x[0] >> 29):
            plist = list(own.get((nh, dh), ()))
            allp.update(plist)
            if len(plist) > 1:
                shadowed = True
            parts.append({
                'part': dh >> 29,
                'data_hash': dh,
                'paks': plist,
                'width': rec.get('width'),
                'height': rec.get('height'),
                'size': rec.get('size'),
                'levels': rec.get('levels'),
                'mip': rec.get('mip'),
            })
        zin = [_zrec(r) for r in (zimgs or {}).get(nh, ())]
        if not fmt and zin:
            # An inline-only image has no pak metadata to be typed from, but the zone record
            # carries the GX2 surface format -- so it can still be labelled properly instead of
            # showing '?' for every result the union just made findable.
            try:
                from .ipak_image import FORMAT_LABEL
                fmt = FORMAT_LABEL.get((zin[0][3] or 0) & 0x3F)
            except Exception:
                fmt = None
        out.append({
            'name': name,
            'name_hash': nh,
            'format': fmt,
            'parts': parts,
            'shadowed': shadowed,
            'pak_count': len(allp),
            'zones': zin,          # [(zone, w, h, fmt, levels, bytes, name)] carrying it INLINE
            'zone_count': len(zin),
            # Resolved HERE, against the same `dirs` the search ran over. describe() and the UI
            # have no idea what folder set was used, so resolving there guessed -- and guessed
            # wrong whenever the caller passed explicit dirs.
            'zone_files': [resolve_zone(z[0], dirs=dirs) for z in zin],
        })
    return out


_ZONE_PATHS = {}


def resolve_zone(basename, dirs=None, refresh=False):
    """A zone basename from the index -> the real file on THIS machine, or None.

    The index stores basenames so it stays valid on any install; this turns one back into a
    path the user can actually open. Cached per process because it is called per result row.

    ⚠ CACHED PER `dirs`. A single global cache was filled by whichever caller ran first, so a
    later lookup against a different folder set silently got the FIRST caller's answer -- which
    presents as "this copy of the game does not have that fastfile" for a file that is right
    there in the folder the caller actually passed.
    """
    key = tuple(dirs) if dirs is not None else None
    if refresh or key not in _ZONE_PATHS:
        m = {}
        for p in _iter_zones(dirs):
            m.setdefault(os.path.basename(p).lower(), p)
        _ZONE_PATHS[key] = m
    return _ZONE_PATHS[key].get(os.path.basename(basename).lower())


def describe(hit):
    """Human-readable lines for one search hit, including the shadow warning."""
    lines = ['%s   (nameHash %08X%s)'
             % (hit['name'], hit['name_hash'],
                ', ' + hit['format'] if hit['format'] else '')]
    if not hit['parts']:
        lines.append('   no part records in the dictionary for this image')
    for p in hit['parts']:
        dims = ('%sx%s' % (p['width'], p['height'])) if p['width'] else '?'
        lines.append('   part %d  %-9s %-10s dataHash %08X'
                     % (p['part'], dims,
                        (str(p['size']) + ' B') if p['size'] else '', p['data_hash']))
        if not p['paks']:
            lines.append('        in no pak%s'
                         % (' -- but see the inline copies below' if hit.get('zones')
                            else ' on this machine'))
        for q in p['paks']:
            lines.append('        %s' % q)
    zfiles = hit.get('zone_files') or ()
    for i, zrec in enumerate(hit.get('zones') or ()):
        zname, w, h, _f, lv, nbytes, _nm = _zrec(zrec)
        lines.append('   INLINE in %s   %sx%s levels=%s  %s bytes'
                     % (os.path.basename(zname), w, h, lv, nbytes))
        full = zfiles[i] if i < len(zfiles) else resolve_zone(zname)
        lines.append('        %s' % (full or '(not found in your folders -- the shipped index '
                                             'lists it, but this copy of the game does not '
                                             'have that fastfile where the tool can see it)'))
        lines.append('        (open that fastfile and use Textures to preview or replace it)')
    if hit['shadowed']:
        lines.append('   ⚠ a part of this image is in MORE THAN ONE pak. The console binds the '
                     'first pak it finds a part in and skips it thereafter, so editing a later '
                     'one has no effect -- change the copy that loads first.')
    return lines
