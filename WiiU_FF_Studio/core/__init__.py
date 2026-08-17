"""WiiU FF Studio -- core.

One import surface over the four backend trees this project grew across. Everything here is
library code with a CLI-reachable equivalent; no GUI logic lives below this line.

    from core import ZoneSession
    s = ZoneSession.open('patch_mp.ff')
    print(s.summary())

Submodules:
    paths       sys.path installation for wiiu_ref / native_linker / fullrelink / tools
    assets      zone_walk-backed asset enumeration (replaces ff_assets' regex scan)
    session     ZoneSession -- open, stage edits, plan, verify, save
    relink      hardened cumulative-delta relink (PLAN §7 fixes)
    verify      mandatory post-build verification
    ipak        IpakSession -- open, browse, edit and save a T6 texture pack
    ipak_image  GX2 detile + BCn decode/encode
    ipak_names  ipak hash -> name/format/dimension metadata

⚠ THE FASTFILE STACK IS IMPORTED LAZILY. `assets` pulls in `zone_walk`, which pulls in the
whole native_linker/fullrelink tree. The ipak tools need none of that, and eagerly importing it
here meant `import core.ipak` failed outright wherever that tree is absent -- which is exactly
what a packaged IPAK Viewer build is (ModuleNotFoundError: no module named 'zone_walk', hit on
a real frozen build). PEP 562 module `__getattr__` keeps `from core import ZoneSession` working
unchanged for callers that do want the fastfile stack, while letting the ipak tools stand alone.
"""
import os as _os

from . import paths          # noqa: F401  -- must be first; installs sys.path

# name -> submodule providing it
_LAZY = {
    'Asset': 'assets', 'Enumeration': 'assets', 'enumerate_zone': 'assets',
    'ZoneSession': 'session', 'SavePlan': 'session', 'Edit': 'session',
    'relink': None, 'verify': None, 'assets': None, 'session': None,
    'ipak': None, 'ipak_image': None, 'ipak_names': None,
}


def __getattr__(name):
    """Import the fastfile (or ipak) layer on first use, with the cwd guard applied.

    ⚠ CWD GUARD -- do not remove.
    `dlc loading/native/fullrelink/ui_splice.py` calls `os.chdir(<root>/native_linker)` at
    MODULE SCOPE. It is reached indirectly: core.assets -> zone_walk -> ui_splice. That is
    harmless for ui_splice's own CLI and destructive for anything else, because every later
    relative path in the process silently resolves against native_linker/ instead of the
    caller's directory. Measured: `import core` moved the cwd out from under the caller, so
    opening a file in the original directory raised FileNotFoundError. The cwd is therefore
    snapshotted around the import and restored afterwards.
    """
    # ⚠ PACKAGERS: this dynamic import is invisible to PyInstaller's dependency scan, so a
    # frozen build MUST list the submodules explicitly (--hidden-import core.session, .assets,
    # .relink, .verify). Measured: without them the EXE died with "No module named
    # core.session", and in a --windowed build that surfaced as a HANG rather than an error,
    # because the crash dialog had no console to appear on. See build_ide.bat.
    if name not in _LAZY:
        raise AttributeError('module %r has no attribute %r' % (__name__, name))
    import importlib
    cwd0 = _os.getcwd()
    try:
        # In a frozen build the backend's module-scope chdir targets a directory that does not
        # exist; neutralise it for the import only. No effect when running from a checkout.
        with paths.tolerant_chdir(active=paths.FROZEN):
            mod = importlib.import_module('.' + (_LAZY[name] or name), __name__)
        val = mod if _LAZY[name] is None else getattr(mod, name)
    finally:
        try:
            if _os.getcwd() != cwd0:
                _os.chdir(cwd0)
        except OSError:
            pass
    globals()[name] = val          # cache: __getattr__ only runs on the first miss
    return val


__all__ = ['paths', 'Asset', 'Enumeration', 'enumerate_zone',
           'ZoneSession', 'SavePlan', 'Edit', 'relink', 'verify',
           'ipak', 'ipak_image', 'ipak_names']
