"""Build the SHIPPED zone-image index: which fastfile carries each inline texture.

Run once on a machine with a full game install; the result is bundled with the program so a
user never pays the ~2 hour first-time cost. The button in Find asset then EXPANDS or REPLACES
this baseline for added fastfiles, a different region, or a different copy.

    python tools_build_zone_index.py [--jobs N] [--out PATH] [dir ...]

WHY IT IS PARALLEL
------------------
The work is per-zone and CPU-bound -- decompress a fastfile, then scan it at byte granularity --
so it divides cleanly across processes. Measured serially: 12 zones / 0.21 GB in 274 s, which
extrapolates to ~126 min for all 370 zones / 5.8 GB. A pool turns that into something that
finishes while you are asleep rather than while you are waiting.

WHAT IT STORES
--------------
`{nameHash: [(zone_BASENAME, w, h, gx2_format, levels, pixel_bytes)]}` and `shipped: True`.

⚠ BASENAMES, NEVER ABSOLUTE PATHS. A path from the build machine is meaningless on a user's
disk; core.ipak_search.resolve_zone maps a basename back to their own file. It also costs
nothing in size -- measured 25.9 vs 26.0 bytes/record -- because pickle already deduplicates the
repeated string.

⚠ `shipped: True` IS LOAD-BEARING. The cache is keyed on a stamp of every zone's path/size/
mtime, which can never match another machine. That flag is what tells the loader to serve this
index anyway instead of discarding it and handing a new user an empty search.
"""
import os
import pickle
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_OUT = os.path.join(HERE, 'core', '_zone_images.cache')

# ⚠ SINGLE SOURCE OF TRUTH. This builder and the reader in core.ipak_search must agree on the
# record shape, and they are separate implementations of the same format -- so the version is
# imported, never re-declared. A local copy silently drifts, and the reader then rejects (or
# worse, mis-reads) an index this tool spent an hour building.
from core.ipak_search import ZONE_CACHE_VERSION as CACHE_VERSION       # noqa: E402


def find_zones(dirs):
    """One zone per BASENAME, preferring the earliest directory given.

    ⚠ DEDUPE BY BASENAME OR HALF THE WORK IS WASTED. The install and the emulator's content
    folders hold copies of the same zones: measured 371 files but only 198 distinct names --
    173 redundant scans, 5.41 GB instead of 2.74 GB. Since the index is keyed by basename, the
    second copy adds nothing except a duplicate record and an hour of runtime.

    Preferring the earliest directory matters for correctness, not just speed: pass the retail
    install FIRST so the shipped baseline describes stock game data rather than whatever the
    user has deployed into their emulator folders (`patch_mp.ff` appears four times, and the
    live one is routinely modified).
    """
    seen_real, by_name, order = set(), {}, []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _dd, files in os.walk(d):
            for fn in sorted(files):
                if not fn.lower().endswith('.ff'):
                    continue
                p = os.path.join(root, fn)
                try:
                    real = os.path.normcase(os.path.realpath(p))
                except OSError:
                    real = os.path.normcase(p)
                if real in seen_real:
                    continue
                seen_real.add(real)
                key = fn.lower()
                if key not in by_name:              # first directory wins
                    by_name[key] = p
                    order.append(key)
    out = [by_name[k] for k in order]
    return sorted(out, key=lambda p: -os.path.getsize(p))     # big ones first: better packing


def scan_one(path):
    """-> (basename, [(nameHash, w, h, fmt, levels, size, name)], error or None).

    ⚠ THE NAME IS NOT OPTIONAL. This index is the ONLY place an inline-only image is named -- it
    has no pak metadata to be named from -- so an index without names cannot be searched by name
    at all. Measured on the nameless v1 index: 105 of 3,944 inline images were findable, because
    only those happened to also exist in a pak. Runs in a worker.
    """
    try:
        os.chdir(HERE)
        sys.path.insert(0, HERE)
        from core import ZoneSession
        from core import zone_images as ZI
        zone = ZoneSession.open(path).zone
        rows = [(im.name_hash, im.width, im.height, im.gx2_format, im.levels, im.pixel_len,
                 im.name)
                for im in ZI.list_images(zone, inline_only=True) if im.name_hash]
        return os.path.basename(path), rows, None
    except Exception as ex:                                    # noqa: BLE001
        return os.path.basename(path), [], '%s: %s' % (type(ex).__name__, ex)


def main(argv):
    jobs = 6
    out_path = DEFAULT_OUT
    dirs = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--jobs':
            i += 1
            jobs = int(argv[i])
        elif a == '--out':
            i += 1
            out_path = argv[i]
        else:
            dirs.append(a)
        i += 1

    if not dirs:
        from core import settings as st
        dirs = list(st.search_dirs())
    print('scanning %d folder(s) with %d worker(s)' % (len(dirs), jobs))
    for d in dirs:
        print('   ', d)

    zones = find_zones(dirs)
    total_bytes = sum(os.path.getsize(z) for z in zones)
    print('%d zones, %.2f GB' % (len(zones), total_bytes / 1e9))
    if not zones:
        print('nothing to do')
        return 1

    images = {}
    done, failed = [], []
    t0 = time.time()

    import multiprocessing as mp
    with mp.Pool(processes=jobs) as pool:
        for n, (base, rows, err) in enumerate(pool.imap_unordered(scan_one, zones), 1):
            if err:
                failed.append((base, err))
            else:
                done.append(base)
                for nh, w, h, fmt, lv, sz, nm in rows:
                    images.setdefault(nh, []).append((base, w, h, fmt, lv, sz, nm))
            el = time.time() - t0
            rate = n / el if el else 0
            eta = (len(zones) - n) / rate / 60 if rate else 0
            print('[%4d/%d] %-42s %5d inline   %5.1f min elapsed, ~%.0f min left'
                  % (n, len(zones), base[:42], len(rows), el / 60, eta), flush=True)

    stamp = sorted((os.path.normcase(os.path.abspath(p)), os.path.getsize(p),
                    int(os.path.getmtime(p))) for p in zones)
    blob = {'version': CACHE_VERSION, 'stamp': stamp, 'images': images,
            'zones': done, 'built': time.time(), 'shipped': True}
    with open(out_path, 'wb') as f:
        pickle.dump(blob, f, protocol=4)

    recs = sum(len(v) for v in images.values())
    print()
    print('=' * 70)
    print('wrote %s' % out_path)
    print('  %s distinct textures, %s inline records, %d zones (%d failed)'
          % (format(len(images), ','), format(recs, ','), len(done), len(failed)))
    print('  %.2f MB on disk, %.0f min to build'
          % (os.path.getsize(out_path) / 1e6, (time.time() - t0) / 60))
    for base, err in failed[:20]:
        print('  FAILED %-40s %s' % (base[:40], err))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
