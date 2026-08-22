"""core.settings -- user-configurable locations, persisted next to the executable.

WHY THIS EXISTS
---------------
The name dictionaries were built from two HARD-CODED directories:

    E:\\Wii U Black ops 2\\content
    %APPDATA%\\Cemu\\mlc01\\usr\\title\\0005000e\\1010cf00\\content

That works on exactly one machine. Anyone whose game lives elsewhere gets no names at all, and
even on this machine a pak opened from some other folder (a project build, a DLC extract, a copy
under `dlc loading\\native`) is not covered -- measured: `base_split8.ipak` from the repo has 43
entries, no section 3, and scored 0 named, because none of its keys appear in the retail set.

So the search path has to be the user's, not ours:

    1. directories the user configured (this file)
    2. the directory of whatever file they just opened, and its parent
    3. folders DISCOVERED by shape (see below) -- never a path written into this file

Rule 2 matters as much as rule 1: it means opening a pak from a folder that also holds its
sibling paks and zones just works, with no configuration at all.

The file is JSON so it can be inspected and hand-edited:

    {"content_dirs": ["D:\\\\games\\\\bo2\\\\content"], "auto_scan_opened_dir": true}
"""
import json
import os
import sys

from . import paths

FILENAME = 'wiiu_ff_studio.json'

DEFAULTS = {
    'content_dirs': [],           # user-added, searched first
    'auto_scan_opened_dir': True,  # also harvest the folder a file was opened from
}

# NO MACHINE-SPECIFIC PATHS LIVE IN THIS FILE.
#
# The tool is meant to be shared, so it must not carry one person's drive letters or install
# layout. A content folder is recognised by its SHAPE -- a directory holding .ipak files and/or
# an `english` subfolder of .ff zones -- and is found by looking in places that are meaningful
# on ANY machine:
#
#     1. folders the user configured (settings, below)
#     2. the folder a file was opened from, and its parent
#     3. the folder the program itself sits in, and the current directory
#     4. the standard Cemu data location, derived from %APPDATA% -- an environment variable,
#        not a path -- and only when it actually exists
#     5. the WIIU_CONTENT_DIR environment variable, for scripted use
#
# Anything not found this way is the user's to point at.

TITLE_IDS = ('0005000e', '0005000c')       # Wii U title categories: update, then DLC

# ⚠ THE LOW TITLE ID IS REGION-SPECIFIC, AND A SINGLE VALUE FINDS ONE REGION.
# The high half above is the title CATEGORY; this half identifies the game AND its region. This
# was `GAME_ID = '1010cf00'`, which is NTSC-U only, so a PAL install was never discovered: no
# names, no formats, no shadow warnings, and nothing on screen saying why. Regions we know:
GAME_IDS = (
    '1010cf00',      # NTSC-U  (US)
    '10113400',      # PAL     (UK / EU / FR)
)
# ⭐ A REGION WE DO NOT KNOW IS STILL FOUND, WITHOUT GUESSING AT ITS NUMBER. The Japanese id is
# unknown to us, so rather than invent one, any OTHER title directory sitting beside the known
# ones is accepted when it carries a T6 fingerprint (see _looks_like_t6_content). That covers
# Japan and any future re-release for free. Adding an id above is then an optimisation, not a
# fix. ⛔ Do NOT relax the fingerprint to plain looks_like_content_dir(): a neighbouring Wii U
# Call of Duty also ships .ff zones, and harvesting one would raise false "this texture also
# lives in another pak" warnings against a pak this game never loads.
T6_PAK_PREFIX = 'base_split'                    # base_split0.ipak .. base_split8.ipak
T6_ZONE_PREFIXES = ('patch_', 'common_', 'core_')


def looks_like_content_dir(path):
    """True when a directory holds the shape of a game content folder.

    Shape, not name: at least one .ipak, or an `english` (or any) subdirectory with .ff zones.
    This is what lets discovery work on an install we have never seen.
    """
    try:
        if not os.path.isdir(path):
            return False
        names = os.listdir(path)
    except OSError:
        return False
    for n in names:
        if n.lower().endswith('.ipak'):
            return True
    for sub in names:
        p = os.path.join(path, sub)
        if not os.path.isdir(p):
            continue
        try:
            if any(f.lower().endswith('.ff') for f in os.listdir(p)):
                return True
        except OSError:
            continue
    return False


def _cemu_data_roots():
    """Where Cemu keeps `mlc01` on each platform, derived from environment, never hard-coded.

    ⚠ This tool may be compiled for Windows, macOS or Linux, so nothing here may assume one of
    them. Every candidate is built from a standard environment variable or the user's home
    directory, and is only offered if it actually exists. If none do -- a perfectly normal
    outcome on a machine with no Cemu, or a game copied anywhere else -- discovery simply
    returns nothing and the user points at their folder instead.
    """
    home = os.path.expanduser('~')
    roots = []
    if sys.platform.startswith('win'):
        base = os.environ.get('APPDATA')
        if base:
            roots.append(os.path.join(base, 'Cemu'))
        roots.append(os.path.join(home, 'AppData', 'Roaming', 'Cemu'))
    elif sys.platform == 'darwin':
        roots.append(os.path.join(home, 'Library', 'Application Support', 'Cemu'))
    else:
        xdg = os.environ.get('XDG_DATA_HOME') or os.path.join(home, '.local', 'share')
        roots.append(os.path.join(xdg, 'Cemu'))
        roots.append(os.path.join(home, '.cemu'))
        # Cemu under Wine/Proton keeps a Windows-shaped prefix.
        roots.append(os.path.join(home, '.wine', 'drive_c', 'users', os.environ.get('USER', ''),
                                  'AppData', 'Roaming', 'Cemu'))
    return [r for r in roots if r and os.path.isdir(r)]


def _looks_like_t6_content(path):
    """True when a directory holds Black Ops II specifically, not merely 'a game'.

    Used ONLY for title ids we do not recognise, where the alternative is guessing at a region's
    number or missing that region entirely. `base_split*.ipak` is the strongest marker: every
    region ships that set, and no other Wii U title uses those names. The zone-name fallback
    covers a content folder whose paks sit elsewhere.
    """
    try:
        if not os.path.isdir(path):
            return False
        names = os.listdir(path)
    except OSError:
        return False
    for n in names:
        low = n.lower()
        if low.startswith(T6_PAK_PREFIX) and low.endswith('.ipak'):
            return True
    # Zones live under a language folder (english/, french/, japanese/, ...), so look one level
    # down as well as here. The language name is never assumed -- that is the whole point.
    for sub in [''] + [s for s in names if os.path.isdir(os.path.join(path, s))]:
        d = os.path.join(path, sub) if sub else path
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for f in entries:
            low = f.lower()
            if low.endswith('.ff') and low.startswith(T6_ZONE_PREFIXES):
                return True
    return False


def _cemu_content_dirs():
    """Cemu's per-title content folders for this game, on whichever platform we are running.

    Known regions first (cheap, exact), then any unrecognised sibling title that carries the T6
    fingerprint. See the GAME_IDS note above for why the second pass exists.
    """
    out = []
    known = set(GAME_IDS)
    for root in _cemu_data_roots():
        for tid in TITLE_IDS:
            base = os.path.join(root, 'mlc01', 'usr', 'title', tid)
            if not os.path.isdir(base):
                continue
            for gid in GAME_IDS:
                p = os.path.join(base, gid, 'content')
                if os.path.isdir(p) and p not in out:
                    out.append(p)
            try:
                siblings = sorted(os.listdir(base))
            except OSError:
                continue
            for gid in siblings:
                if gid.lower() in known:
                    continue
                p = os.path.join(base, gid, 'content')
                if p not in out and _looks_like_t6_content(p):
                    out.append(p)
    return out


def discovered_dirs():
    """Content folders found without any user configuration."""
    out = []
    env = os.environ.get('WIIU_CONTENT_DIR')
    if env:
        for part in env.split(os.pathsep):
            if part and os.path.isdir(part):
                out.append(os.path.abspath(part))
    # ⛔ NEVER CLIMB TO THE PARENT DIRECTORY. This used to also test os.path.dirname(base),
    # which on a development machine matched the checkout root ("Testing enviroment") because
    # that folder happens to hold .ipak files and zone subfolders -- so the shipped tool
    # advertised a source tree that will never exist on an end user's computer, and quietly
    # harvested names from whatever build artefacts were lying around next to the program.
    #
    # A discovered folder must be one the USER would recognise as their game: the directory the
    # program sits in, or a `content` folder inside it (the portable case, exe dropped into the
    # game folder). Anything else is the user's to point at via Tools > Game folders.
    here = [os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, 'frozen', False) else os.getcwd(), os.getcwd()]
    for base in here:
        for cand in (base, os.path.join(base, 'content')):
            if cand and looks_like_content_dir(cand):
                out.append(os.path.abspath(cand))
    out += _cemu_content_dirs()
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq

_cache = None


def config_path():
    """Where settings live. Beside the executable when frozen, else beside the source."""
    read, write = paths.cache_file(FILENAME)
    return write if os.path.exists(write) or not os.path.exists(read) else read


def load(force=False):
    global _cache
    if _cache is not None and not force:
        return _cache
    cfg = dict(DEFAULTS)
    p = config_path()
    try:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f) or {})
    except Exception:
        pass                       # a corrupt config must not stop the tool from opening
    cfg['content_dirs'] = [d for d in cfg.get('content_dirs') or [] if d]
    _cache = cfg
    return cfg


def save(cfg=None):
    global _cache
    cfg = cfg if cfg is not None else load()
    _cache = cfg
    p = config_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except Exception:
        pass
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    return p


def add_content_dir(path):
    """Add a game/content folder. Returns the updated list."""
    cfg = load()
    path = os.path.abspath(path)
    if path not in cfg['content_dirs']:
        cfg['content_dirs'].insert(0, path)
        save(cfg)
    return cfg['content_dirs']


def remove_content_dir(path):
    cfg = load()
    cfg['content_dirs'] = [d for d in cfg['content_dirs']
                           if os.path.abspath(d) != os.path.abspath(path)]
    save(cfg)
    return cfg['content_dirs']


_opened = []


def note_opened(path):
    """Remember the folder a file was opened from, so its siblings can be harvested.

    This is what makes a pak from an unconfigured folder resolve: the paks and zones sitting
    beside it are exactly the ones most likely to describe it.
    """
    if not path:
        return
    d = os.path.dirname(os.path.abspath(path))
    for cand in (d, os.path.dirname(d)):
        if cand and os.path.isdir(cand) and cand not in _opened:
            _opened.insert(0, cand)
    del _opened[8:]


def search_dirs(include_opened=True):
    """Every directory to harvest, in priority order, de-duplicated and existing only."""
    cfg = load()
    out = []
    for group in (cfg['content_dirs'],
                  _opened if (include_opened and cfg.get('auto_scan_opened_dir', True)) else [],
                  discovered_dirs()):
        for d in group:
            if not d:
                continue
            d = os.path.abspath(d)
            if os.path.isdir(d) and d not in out:
                out.append(d)
    return out


def describe():
    cfg = load()
    lines = ['config: %s' % config_path(),
             'configured content dirs: %s' % (', '.join(cfg['content_dirs']) or 'none')]
    if _opened:
        lines.append('opened-from dirs: %s' % ', '.join(_opened[:3]))
    lines.append('effective search path:')
    for d in search_dirs():
        lines.append('   %s' % d)
    return '\n'.join(lines)


# --- per-document-kind view preference -------------------------------------------------------
# Which browser view ('list' or 'gallery') the user last used FOR EACH KIND. Remembered per kind
# rather than globally because the right default genuinely differs: a pak of emblems is worth
# browsing visually, a zone's asset list is not.
def get_view(kind, default='list'):
    try:
        return (load().get('views') or {}).get(kind, default)
    except Exception:
        return default


def set_view(kind, mode):
    try:
        cfg = load()
        cfg.setdefault('views', {})[kind] = mode
        save(cfg)
    except Exception:
        pass
