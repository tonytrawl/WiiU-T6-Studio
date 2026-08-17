"""core.paths -- put the four proven backend trees on sys.path, once, in the right order.

WHY THIS EXISTS
---------------
The working code for this project is spread across four trees that grew independently:

    wiiu_ref/                        walker, wiiu_zone, struct_layout, gsc_diff
    native_linker/                   zone_walk (63/63 EOF-exact), zone_gates, zone_facts,
                                     body_relayout, zone_stream, gsc_asm
    dlc loading/native/fullrelink/   grow_relink, add_asset, ui_splice, lua_endian
    tools/                           gsc_spt, gsc_setname, gsc_preboot_check

Several of those modules do their own `sys.path.insert(..., '..', '..')` relative to their own
file, and at least one (`zone_walk`) resolves argv paths against the cwd it was started in. So
importing them from an arbitrary cwd is not automatic. This module makes it deterministic:
absolute paths, inserted once, in dependency order, with the cwd left alone.

Import this before anything else in core.
"""
import os
import sys

# core/ -> WiiU_FF_Studio/ -> repo root
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CORE_DIR)
ROOT = os.path.dirname(STUDIO_DIR)

#: True inside a PyInstaller bundle. There, the backend trees do not exist as directories --
#: the modules that were needed are compiled into the archive and import by plain name -- so
#: `install()` legitimately adds nothing and `missing()` must not report that as a fault.
FROZEN = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')
MEIPASS = getattr(sys, '_MEIPASS', None)

WIIU_REF = os.path.join(ROOT, 'wiiu_ref')
NATIVE_LINKER = os.path.join(ROOT, 'native_linker')
FULLRELINK = os.path.join(ROOT, 'dlc loading', 'native', 'fullrelink')
TOOLS = os.path.join(ROOT, 'tools')

# Order matters: several modules resolve sibling imports by name, and the first hit on sys.path
# wins. native_linker must precede wiiu_ref so `zone_walk` and friends bind to the proven
# native_linker copies rather than any older same-named module in wiiu_ref.
TREES = (STUDIO_DIR, NATIVE_LINKER, WIIU_REF, FULLRELINK, TOOLS)

_installed = False


def cache_file(basename):
    """Where a prebuilt lookup cache lives, and where an updated one may be written.

    Returns (read_path, write_path).

    ⚠ A FROZEN BUILD MUST NOT KEEP ITS CACHE INSIDE THE BUNDLE. `os.path.dirname(__file__)`
    points into the `_MEIxxxx` extraction directory, which is recreated per launch and deleted
    on exit, so a cache written there is thrown away every run. Worse, when the cache is the
    ONLY source of a name dictionary (sab_names opens cache-only), a bundle that ships without
    the file shows no names at all -- which is exactly what happened: the sound bank editor
    listed bare hex ids because `_sab_names.cache` was never packaged.

    So: read from the bundled copy if present, and write beside the executable, falling back to
    %LOCALAPPDATA% when the install directory is read-only.
    """
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), basename)
    if not (FROZEN and MEIPASS):
        return here, here

    # PyInstaller puts `--add-data "core\x.cache;core"` at MEIPASS/core/x.cache, but a bare
    # `;.` target lands at MEIPASS/x.cache. Check both, or a correctly-bundled cache is simply
    # not found and the tool silently shows no names -- which is exactly what shipped.
    bundled = os.path.join(MEIPASS, basename)
    if not os.path.exists(bundled):
        alt = os.path.join(MEIPASS, 'core', basename)
        if os.path.exists(alt):
            bundled = alt
    exedir = os.path.join(os.path.dirname(sys.executable), basename)
    if os.path.exists(exedir):
        return exedir, exedir            # a user-refreshed cache wins over the shipped one
    read = bundled if os.path.exists(bundled) else exedir
    try:
        probe = exedir + '.tmp'
        with open(probe, 'wb'):
            pass
        os.remove(probe)
        return read, exedir
    except OSError:
        # Per-user data dir, resolved per platform from the environment. The tool may be built
        # for Windows, macOS or Linux, so LOCALAPPDATA cannot be assumed to exist.
        home = os.path.expanduser('~')
        if sys.platform.startswith('win'):
            base = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        elif sys.platform == 'darwin':
            base = os.path.join(home, 'Library', 'Application Support')
        else:
            base = os.environ.get('XDG_DATA_HOME') or os.path.join(home, '.local', 'share')
        appdir = os.path.join(base, 'WiiU_FF_Studio')
        try:
            os.makedirs(appdir, exist_ok=True)
        except OSError:
            return read, read
        return read, os.path.join(appdir, basename)


def _install_frozen_data():
    """Point the walker at the reference data bundled into the executable.

    `wiiu_ref/walker.py` needs OAT's `T6_Assets.h` (192 KB) and the ZoneCode `XAssets`
    directory (121 KB) to know the asset struct layouts. From source it finds them relative to
    its own file; in a frozen build there is no source tree, so the build copies them into the
    bundle and this points the two documented environment overrides at them.

    ⚠ This must happen BEFORE walker is imported. It is done here because core.paths is
    guaranteed to be the first thing core imports. Setting them is deliberately conditional:
    an override the user already exported wins, so a developer can still redirect it.
    """
    if not (FROZEN and MEIPASS):
        return
    hdr = os.path.join(MEIPASS, 'T6_Assets.h')
    if os.path.isfile(hdr):
        os.environ.setdefault('OAT_T6_ASSETS_H', hdr)
    zc = os.path.join(MEIPASS, 'XAssets')
    if os.path.isdir(zc):
        os.environ.setdefault('OAT_ZC_DIR', zc)


def install():
    """Idempotently put every backend tree on sys.path. Returns the list of trees added."""
    global _installed
    added = []
    _install_frozen_data()
    trees = list(TREES)
    if FROZEN and MEIPASS:
        # A bundle may also ship loose backend files next to the archive; harmless if absent.
        trees = [MEIPASS] + trees
    for t in trees:
        if os.path.isdir(t) and t not in sys.path:
            sys.path.insert(0, t)
            added.append(t)
    _installed = True
    return added


class backend_cwd(object):
    """Run a backend call with the working directory the backend actually expects.

    ⚠⚠ `zone_walk` (via `body_relayout`) opens `../tools/ref_oat/src/Common/Game/T6/T6_Assets.h`
    by a path RELATIVE TO THE CWD, so it only resolves when the cwd is `native_linker/`. That is
    what `ui_splice`'s module-scope `os.chdir` was silently providing.

    Restoring the caller's cwd (which `keep_cwd` does, correctly, so a GUI's relative paths keep
    working) therefore BREAKS the walk -- and it breaks it QUIETLY: the walk returns a PARTIAL
    result instead of raising. Measured on live patch_mp: 754 assets from the wrong cwd versus
    1527 from the right one, with `walk_ok` False but no exception.

    So every call into the walk/relink backend is wrapped in this, which sets the cwd for the
    duration and restores it afterwards.
    """
    def __enter__(self):
        self._cwd = os.getcwd()
        try:
            os.chdir(NATIVE_LINKER)
        except OSError:
            pass
        return self

    def __exit__(self, *exc):
        try:
            os.chdir(self._cwd)
        except OSError:
            pass
        return False


class tolerant_chdir(object):
    """Make a module-scope `os.chdir` to a non-existent directory a no-op, for the duration.

    ⚠ NEEDED ONLY IN A FROZEN BUILD, and only because of a module we do not own.
    `dlc loading/native/fullrelink/ui_splice.py` calls `os.chdir(<root>/native_linker)` at
    MODULE SCOPE, and it is reached indirectly (core.assets -> zone_walk -> ui_splice). From a
    checkout that directory exists. Inside a PyInstaller bundle there is no source tree, the
    path it computes from its own __file__ is meaningless, and the import dies with
    FileNotFoundError before anything else can run -- measured:

        FileNotFoundError: [WinError 2] ... 'C:\\Users\\...\\AppData\\native_linker'

    Editing ui_splice is not an option here: it is shared with the conversion pipeline, where
    that chdir is load-bearing. So the chdir is neutralised for the import only, and only when
    the target does not exist -- a real chdir to a real directory still works normally.
    """

    def __init__(self, active=True):
        self.active = active
        self._orig = None

    def __enter__(self):
        if not self.active:
            return self
        self._orig = os.chdir

        def _chdir(path):
            try:
                return self._orig(path)
            except (OSError, ValueError):
                return None            # the directory does not exist in a bundle; ignore
        os.chdir = _chdir
        return self

    def __exit__(self, *exc):
        if self._orig is not None:
            os.chdir = self._orig
        return False


class keep_cwd(object):
    """Context manager restoring the working directory.

    Needed because `ui_splice.py` (and anything importing it, which includes `zone_walk`) calls
    `os.chdir(<root>/native_linker)` at MODULE SCOPE. Wrap any import or call that might reach
    that tree:

        with paths.keep_cwd():
            import zone_walk
    """
    def __enter__(self):
        self._cwd = os.getcwd()
        return self

    def __exit__(self, *exc):
        try:
            if os.getcwd() != self._cwd:
                os.chdir(self._cwd)
        except OSError:
            pass
        return False


def missing():
    """Trees that don't exist on disk -- used by the selftest to explain an ImportError.

    Always empty in a frozen build: the needed modules are inside the archive, so a missing
    source tree there is expected rather than a fault.
    """
    if FROZEN:
        return []
    return [t for t in TREES if not os.path.isdir(t)]


install()
