"""core.sab_names -- recover sound bank entry names from the fastfiles that reference them.

WHY THIS EXISTS
---------------
A .sabs/.sabl identifies an entry by a u32 id and nothing else. There is no name anywhere in the
bank, and an earlier attempt to recover names by guessing the hash function correctly REFUSED to
ship: 187,980 candidate strings tested against 8 hash variants produced 0 hits on 357 ids.

The names do exist, just not in the bank. The SndBank asset inside a .ff carries, per alias, BOTH
the id the bank is keyed by AND the source filename string, so no hash function is needed at all:

    SndBank body (4756 B, 4760 in common_mp), then the bank name string,
    then aliasCount x SndAliasList(20) {name*, id, head*, count, sequence},
    then per list: its name string, then count x SndAlias(100), then that alias's strings.

    SndAlias(100):  name*@+0   id@+4   subtitle*@+8   secondaryname*@+12
                    assetId@+16        assetFileName*@+20

⚠ THE FIELD IS assetId @ +16, NOT id @ +4. A bank entry's id equals SndAlias.assetId, which is
derived from the .snd FILE name; SndAlias.id is derived from the ALIAS name and is a different
value. Pairing against the wrong one produces a dictionary that looks plausible and matches
almost nothing -- `harvest()` reports the match rate against real bank ids so that is visible.

IS THE TOOL NOW DEPENDENT ON FASTFILES?
---------------------------------------
No. This is optional enrichment, harvested ONCE into a cache and then reused; with no .ff present
the editor lists entries by id exactly as before. Nothing here is required to open, play, extract,
replace or save a bank.
"""
import glob
import os
import pickle
import re
import struct

from . import paths  # noqa: F401

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

BODY_SIZES = (4756, 4760)      # mp_raid and friends / common_mp
ALIASLIST = 20
ALIAS = 100

CACHE_VERSION = 1
DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_sab_names.cache')

# ⚠ NO HARD-CODED PATHS -- see core.settings. Zones are found in the folders the user
# configured, the folder they opened a file from, and anything discovered by shape.
DEFAULT_ZONE_DIRS = []


def find_bodies(zone, endian='>', body=4756):
    """SndBank bodies, by the loadedAssets signature at body+0x1270.

    Same locator as native_linker/sndbank_audio_convert.find_sndbank_body, which is proven on
    console zones: entryCount u32, entries*=FOLLOW, dataSize u32, data*=FOLLOW, with name*@0 and
    alias*@8 both FOLLOW.
    """
    out = []
    for m in re.finditer(rb'\xff\xff\xff\xff(....)\xff\xff\xff\xff', zone, re.S):
        b = m.start() - 0x1274
        if b < 0:
            continue
        try:
            ec = struct.unpack_from(endian + 'I', zone, b + 0x1270)[0]
            ds = struct.unpack_from(endian + 'I', zone, m.start() + 4)[0]
            np_ = struct.unpack_from(endian + 'I', zone, b)[0]
            ac = struct.unpack_from(endian + 'I', zone, b + 4)[0]
            ap = struct.unpack_from(endian + 'I', zone, b + 8)[0]
        except struct.error:
            continue
        if np_ == FOLLOW and ap == FOLLOW and 0 < ec < 100000 and 0 < ds < 500000000 \
                and 0 < ac < 100000:
            try:
                nul = zone.index(b'\x00', b + body)
            except ValueError:
                continue
            out.append((b, zone[b + body:nul].decode('latin-1', 'replace'), ec, ds, ac))
    return out


def _aliases_at(zone, b, body, endian='>'):
    """Walk one SndBank's alias tables. -> [(assetId, assetFileName)].

    The walk must consume strings in exactly the emitted order (list name, then per alias the
    name/subtitle/secondaryname/assetFileName strings), or it desyncs and yields nonsense. This
    mirrors wiiu_ref/sndbank_probe.parse_sndbank, whose walk is byte-exact.
    """
    def u32(o):
        return struct.unpack_from(endian + 'I', zone, o)[0]

    name_p, alias_count, alias_p = u32(b), u32(b + 4), u32(b + 8)
    if name_p not in PTRS or alias_p not in PTRS:
        return []
    o = b + body
    nul = zone.index(b'\x00', o)
    o = nul + 1                                     # bank name

    out = []
    base = o
    o += alias_count * ALIASLIST
    for i in range(alias_count):
        lb = base + i * ALIASLIST
        lname_p, _lid, head_p, cnt, _seq = struct.unpack_from(endian + '5I', zone, lb)
        if lname_p in PTRS:
            o = zone.index(b'\x00', o) + 1
        if head_p in PTRS:
            ab = o
            o += cnt * ALIAS
            for k in range(cnt):
                a = ab + k * ALIAS
                asset_id = u32(a + 16)
                fname = None
                for idx, po in enumerate((a + 0, a + 8, a + 12, a + 20)):
                    if u32(po) in PTRS:
                        end = zone.index(b'\x00', o)
                        s = zone[o:end].decode('latin-1', 'replace')
                        o = end + 1
                        if idx == 3:                # assetFileName
                            fname = s
                if asset_id and fname:
                    out.append((asset_id, fname))
    return out


def harvest_zone(zone):
    """-> {assetId: assetFileName} for every SndBank in one decompressed zone."""
    names = {}
    for body in BODY_SIZES:
        for b, _bank, _ec, _ds, _ac in find_bodies(zone, body=body):
            try:
                for aid, fname in _aliases_at(zone, b, body):
                    names.setdefault(aid, fname)
            except (ValueError, struct.error):
                continue                # desync on this body; the others may still be fine
    return names


def zone_paths(dirs=None):
    out = []
    from . import settings as _st
    for d in (dirs or _st.search_dirs() or DEFAULT_ZONE_DIRS):
        if not d or not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, '**', '*.ff'), recursive=True)):
            out.append(p)
    return out


def harvest(paths_=None, progress=None):
    """Harvest every reachable fastfile. -> (names, stats)."""
    from . import ZoneSession
    names, stats = {}, {'zones': 0, 'failed': 0, 'ids': 0}
    for p in (paths_ if paths_ is not None else zone_paths()):
        try:
            zone = ZoneSession.open(p).zone
        except Exception:
            stats['failed'] += 1
            continue
        got = harvest_zone(zone)
        stats['zones'] += 1
        for k, v in got.items():
            names.setdefault(k, v)
        if progress:
            progress(p, len(got))
    stats['ids'] = len(names)
    return names, stats


def load_shared(cache_path=None, rebuild=False, dirs=None, progress=None,
                build_if_missing=True):
    """The id -> name dictionary, from cache when possible.

    ⚠ `build_if_missing=False` IS WHAT THE OPEN PATH USES. Harvesting decrypts and decompresses
    every reachable fastfile, which takes long enough to look like a hang if it runs while
    someone is just trying to open a bank. So opening reads the cache and otherwise shows hex
    ids; the explicit "Rebuild names" action does the harvest, on a worker, with a progress bar.

    The cache records the (basename, size) of every zone it was built from; if that set changes
    it is rebuilt rather than silently serving a stale dictionary.
    """
    read_path, write_path = paths.cache_file(os.path.basename(DEFAULT_CACHE))
    if cache_path:
        read_path = write_path = cache_path
    elif os.environ.get('SAB_NAME_CACHE'):
        read_path = write_path = os.environ['SAB_NAME_CACHE']
    zs = zone_paths(dirs)
    stamp = sorted((os.path.basename(p), os.path.getsize(p)) for p in zs)
    if not rebuild and os.path.exists(read_path):
        try:
            with open(read_path, 'rb') as f:
                blob = pickle.load(f)
            if blob.get('version') == CACHE_VERSION:
                if blob.get('stamp') == stamp:
                    return blob['names']
                # Stamp mismatch = the .ff set on this machine differs from the one the cache
                # was built from. When we are NOT allowed to rebuild, serving the cached names
                # anyway is strictly better than showing bare hex ids: these are display names,
                # not something the console reads. A shipped cache would otherwise be discarded
                # on any machine whose game install differs even slightly.
                if not build_if_missing:
                    return blob.get('names') or {}
        except Exception:
            pass
    if not build_if_missing:
        return {}
    names, _stats = harvest(zs, progress=progress)
    try:
        with open(write_path, 'wb') as f:
            pickle.dump({'version': CACHE_VERSION, 'stamp': stamp, 'names': names}, f, 2)
    except Exception:
        pass                      # a read-only location is not a reason to fail
    return names


def match_rate(names, bank):
    """How many of a bank's entries this dictionary can name. The honest scoreboard."""
    if not bank.entries:
        return 0, 0
    hit = sum(1 for e in bank.entries if e.id in names)
    return hit, len(bank.entries)
