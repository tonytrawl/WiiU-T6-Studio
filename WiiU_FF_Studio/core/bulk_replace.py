"""core.bulk_replace -- match a folder of images to every place they live, and replace them all.

THE PROBLEM THIS SOLVES
-----------------------
A PC texture mod is a pile of files named after the textures they replace. Applying one to the
Wii U build by hand means, for EVERY file: work out which pak or fastfile carries that texture,
open it, find the entry, replace it, save. A single image can be split across three parts in two
different paks AND exist inline in several zones, so "where does this one go" is itself the hard
part. That answer already exists -- it is what core.ipak_search builds -- so this module joins the
two: names in, every owning file resolved, all of them rewritten.

TWO DESIGN POINTS THAT ARE NOT COSMETIC
---------------------------------------
1. WORK IS GROUPED BY TARGET FILE, NOT BY SOURCE IMAGE. Twenty textures that all live in
   base_split1.ipak open and save that pak ONCE. Replacing them one at a time would decode,
   rewrite and re-save a 300 MB pak twenty times -- and every save after the first would be
   rewriting a file the previous save had already changed, which is slow and needlessly risky.
2. ONE FAILURE NEVER STOPS THE RUN. A texture the index cannot name, a format we cannot encode,
   a file that is read-only: each is recorded against that item and the run continues. A bulk
   operation that aborts halfway is worse than useless, because the user cannot tell what landed.

⚠ NOTHING IS WRITTEN UNTIL apply(). plan() is a pure lookup and is safe to show, sort and edit.
"""
import os

from . import ipak_search as SEARCH

#: extensions we will try to load as a replacement image
IMAGE_EXTS = ('.png', '.dds', '.tga', '.bmp', '.jpg', '.jpeg', '.gif', '.tif', '.tiff', '.webp')

_HEXDIGITS = set('0123456789abcdef')


def normalise(name):
    """A texture name -> the key both a game name and a modder's filename collapse onto.

    Game names carry decoration a human never types: a '~' or '-' sigil in front, and for
    deduplicated entries a '~<8 hex>' suffix. `~~-gme_wall_col~49fd738d` and a modder's
    `gme_wall_col.dds` are the same texture, so both must reduce to `gme_wall_col`.
    """
    s = (name or '').strip().lower()
    if not s:
        return ''
    base = os.path.basename(s)
    for ext in IMAGE_EXTS:
        if base.endswith(ext):
            base = base[:-len(ext)]
            break
    base = base.lstrip('~-&')
    # trailing dedup tag: ~xxxxxxxx
    if len(base) > 9 and base[-9] == '~' and all(c in _HEXDIGITS for c in base[-8:]):
        base = base[:-9]
    return base.rstrip('~-&')


def _name_lookup(index, zone_images):
    """{normalised name: [(name, nameHash)]} over BOTH naming sources.

    Same union as core.ipak_search.search -- an inline-only image is named nowhere but the zone
    index, so a pak-only lookup silently cannot match most of a zone's textures.
    """
    out = {}
    seen = set()

    def add(nh, nm):
        k = normalise(nm)
        if not k or (k, nh) in seen:
            return
        seen.add((k, nh))
        out.setdefault(k, []).append((nm, nh))

    for nh, val in ((index.by_name_hash or {}) if index is not None else {}).items():
        add(nh, val[0] if isinstance(val, (tuple, list)) else val)
    for nh, nm in SEARCH.zone_names(zone_images).items():
        add(nh, nm)
    return out


def plan(files, index=None, owners=None, zone_images=None, dirs=None):
    """Resolve local image files to every pak and zone that carries them. Reads nothing but indexes.

    -> [{'src', 'stem', 'matched', 'name', 'name_hash', 'targets': [...], 'status', 'detail'}]

    status is 'ok' (at least one target), 'unmatched' (no such texture name is known) or
    'nowhere' (the name is known but no reachable file carries it).
    """
    from . import ipak_names as NM
    if index is None:
        index = NM.load_shared()
    if owners is None:
        owners, _paks = SEARCH.owner_map(dirs=dirs)
    if zone_images is None:
        zone_images, _z = SEARCH.zone_image_map(dirs=dirs, build=False)

    lookup = _name_lookup(index, zone_images)
    items = []
    for src in files:
        stem = os.path.splitext(os.path.basename(src))[0]
        key = normalise(src)
        cands = list(lookup.get(key, ()))
        # A file named for the raw hash (0A0FEE98.dds) is a legitimate way to target an image
        # whose name the index never recovered -- alias-named zone records have no name at all.
        if not cands:
            h = SEARCH._as_hash(stem)
            if h is not None:
                nm = SEARCH.zone_names(zone_images).get(h)
                cands = [(nm or ('%08X' % h), h)]
        if not cands:
            items.append({'src': src, 'stem': stem, 'matched': False, 'name': None,
                          'name_hash': None, 'targets': [],
                          'status': 'unmatched',
                          'detail': 'no indexed texture is named %r' % stem})
            continue

        name, nh = cands[0]
        targets, seen_paks = [], set()
        for (onh, odh), paks in (owners or {}).items():
            if onh != nh:
                continue
            for p in paks:
                if p in seen_paks:
                    continue
                seen_paks.add(p)
                targets.append({'kind': 'ipak', 'path': p, 'name_hash': nh})
        for i, zrec in enumerate((zone_images or {}).get(nh, ())):
            zrec = SEARCH._zrec(zrec)
            full = SEARCH.resolve_zone(zrec[0], dirs=dirs)
            if full:
                targets.append({'kind': 'zone', 'path': full, 'name_hash': nh,
                                'width': zrec[1], 'height': zrec[2]})
        items.append({
            'src': src, 'stem': stem, 'matched': True, 'name': name, 'name_hash': nh,
            'targets': targets,
            'status': 'ok' if targets else 'nowhere',
            'detail': ('%d file(s)' % len(targets)) if targets
                      else 'known texture, but no reachable pak or fastfile carries it',
            'ambiguous': [c[0] for c in cands[1:]] if len(cands) > 1 else [],
        })
    return items


def group_by_target(items):
    """[plan items] -> {(kind, path): [(item, target)]}. The unit of work is the FILE, see header."""
    jobs = {}
    for it in items:
        if it['status'] != 'ok':
            continue
        for t in it['targets']:
            jobs.setdefault((t['kind'], t['path']), []).append((it, t))
    return jobs


def _load_rgba(path):
    import numpy as np
    from PIL import Image
    im = Image.open(path)
    im.load()                        # force decode HERE so a bad file fails with its own name
    return np.asarray(im.convert('RGBA'), np.uint8)


def apply(items, progress=None, should_stop=None, backup=True):
    """Perform the planned replacements. -> {'written', 'failed', 'replaced', 'log'}

    `progress(done, total, label)` is called per target file. `should_stop()` aborts BETWEEN
    files, never mid-write -- a half-written pak is not something to hand a user.
    """
    jobs = group_by_target(items)
    total = len(jobs)
    written, failed, log = [], [], []
    replaced_count = 0
    cache = {}

    def rgba_for(src):
        if src not in cache:
            cache[src] = _load_rgba(src)
        return cache[src]

    for n, ((kind, path), work) in enumerate(sorted(jobs.items())):
        if should_stop is not None and should_stop():
            log.append('STOPPED before %s' % os.path.basename(path))
            break
        if progress:
            progress(n, total, os.path.basename(path))
        try:
            if kind == 'ipak':
                done = _apply_ipak(path, work, rgba_for, log, backup)
            else:
                done = _apply_zone(path, work, rgba_for, log, backup)
            if done:
                written.append(path)
                replaced_count += done
            else:
                log.append('%s: nothing replaced' % os.path.basename(path))
        except Exception as ex:                       # one bad file must not end the run
            failed.append((path, '%s: %s' % (type(ex).__name__, ex)))
            log.append('FAILED %s -- %s: %s' % (os.path.basename(path), type(ex).__name__, ex))
    if progress:
        progress(total, total, None)
    return {'written': written, 'failed': failed, 'replaced': replaced_count,
            'log': log, 'targets': total}


def _apply_ipak(path, work, rgba_for, log, backup):
    from .ipak import IpakSession
    s = IpakSession.open(path)
    n = 0
    for it, _t in work:
        matches = [i for i in s.items if i.name_hash == it['name_hash']]
        if not matches:
            log.append('%s: %s not in this pak after all' % (os.path.basename(path), it['name']))
            continue
        try:
            # replace_image_all, never replace_image: a streamed image is split across up to
            # three parts holding different mip tiers, and doing one leaves the rest stock.
            rep = s.replace_image_all(matches[0], rgba_for(it['src']))
        except Exception as ex:
            log.append('%s: %s failed -- %s' % (os.path.basename(path), it['name'], ex))
            continue
        if rep.get('replaced'):
            n += len(rep['replaced'])
            log.append('%s: %s -> part(s) %s'
                       % (os.path.basename(path), it['name'],
                          ','.join(str(i) for i in rep['replaced'])))
        for skipped in rep.get('skipped') or ():
            log.append('%s: %s part %s skipped -- %s'
                       % (os.path.basename(path), it['name'], skipped[0], skipped[1]))
    if n:
        s.save(path, backup=backup)
    return n


def _apply_zone(path, work, rgba_for, log, backup):
    from . import ZoneSession
    from . import zone_images as ZI
    sess = ZoneSession.open(path)
    images = {im.name_hash: im for im in ZI.list_images(sess.zone, inline_only=True)
              if im.name_hash}
    n = 0
    for it, _t in work:
        img = images.get(it['name_hash'])
        if img is None:
            log.append('%s: %s not inline in this zone' % (os.path.basename(path), it['name']))
            continue
        try:
            ZI.replace(sess, img, rgba_for(it['src']))
            n += 1
            log.append('%s: %s (%dx%d)'
                       % (os.path.basename(path), it['name'], img.width, img.height))
        except Exception as ex:
            log.append('%s: %s failed -- %s' % (os.path.basename(path), it['name'], ex))
    if n:
        if backup:
            _backup_once(path)
        sess.save(path)
    return n


def _backup_once(path):
    """Write-once .orig beside a zone. Same reasoning as the RPL .stock backup: a backup that
    refreshes itself replaces the pristine copy with an edited one on the second run."""
    import shutil
    bp = path + '.orig'
    if not os.path.exists(bp):
        shutil.copy2(path, bp)
    return bp


def summarise(items):
    """One line per outcome class, for the dialog header."""
    ok = [i for i in items if i['status'] == 'ok']
    un = [i for i in items if i['status'] == 'unmatched']
    nw = [i for i in items if i['status'] == 'nowhere']
    files = len(group_by_target(items))
    return ('%d of %d matched, touching %d file(s)%s%s'
            % (len(ok), len(items), files,
               ' -- %d not found by name' % len(un) if un else '',
               ' -- %d found but in no reachable file' % len(nw) if nw else ''))
