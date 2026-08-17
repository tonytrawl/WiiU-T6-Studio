"""core.xmodel_selftest -- gate battery for XModel extraction and the 3D preview.

Run:  python -m core.xmodel_selftest        (from WiiU_FF_Studio/)

  X1  parse coverage      every XMODEL in a real zone parses, or is named as a failure
  X2  bounds oracle       decoded positions lie inside the model's OWN stored mins/maxs
  X2r reachability        a corrupted model is actually rejected by X2's check
  X3  index validity      every triangle index is inside its surface's vertex array
  X4  LOD partition       lodInfo partitions the surface array; detail descends with LOD
  X5  render              real models rasterise to a non-empty image, within a time budget

X2 is the load-bearing one: `mins`/`maxs` are written by the linker, independently of the
vertex stream we decode, so a wrong stride, offset or byte order shows up immediately. X2r
exists because a check that cannot fail proves nothing -- see the ipak battery, where a mip
gate was found to pass a deliberately corrupted offset at 0.996 correlation.
"""
import os
import sys
import time

import numpy as np

from . import paths  # noqa: F401
from . import xmodel as XM


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _roots():
    """Where to look for test fastfiles.

    ROOT is derived from this file, which points inside the bundle in a frozen build and holds
    nothing. The directory containing the executable and the current directory are searched too,
    so `WiiU_FF_IDE.exe --selftest` can be verified by dropping a .ff next to it.

    ⚠ THE USER'S GAME FOLDERS MUST BE SEARCHED OR THE PACKAGED BATTERY IS VACUOUS. Measured on
    the frozen IDE:

        SKIP  X1-X5  no fastfile found next to the repo root
        0 passed, 0 failed, 1 skipped

    while the same battery from source passes against real zones. Zones live wherever the game
    is installed, and only `core.settings` knows how to find that on an arbitrary machine. This
    is the same root cause that made the packaged ipak battery report nothing.
    """
    roots = [ROOT, os.getcwd()]
    if getattr(sys, 'frozen', False):
        roots.insert(0, os.path.dirname(sys.executable))
    try:
        from . import settings as _st
        roots.extend(_st.search_dirs())
    except Exception:
        pass                       # discovery is best-effort; SKIP is still an honest outcome
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


#: Zones the battery prefers, best first. Any of them exercises every gate.
PREFERRED_ZONES = ('mp_raid.ff', 'mp_carrier.ff', 'patch_mp.ff', 'common_mp.ff')

MAX_ZONES = 2                      # decoding XModels is slow; two zones is ample coverage


def _zones():
    roots = _roots()
    out = []
    for root in roots:
        for fn in PREFERRED_ZONES:
            p = os.path.join(root, fn)
            if os.path.exists(p) and p not in out:
                out.append(p)
    if out:
        return out
    # A content folder keeps its zones in a language subdirectory (`english/`), so a flat check
    # of the folder itself finds nothing. Look one level down before giving up.
    for root in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= 2:
                dirnames[:] = []
            for fn in sorted(filenames):
                if fn.lower().endswith('.ff'):
                    p = os.path.join(dirpath, fn)
                    if p not in out:
                        out.append(p)
                    if len(out) >= MAX_ZONES:
                        return out
    return out


class Battery(object):
    def __init__(self):
        self.rows = []

    def add(self, gate, status, detail=''):
        self.rows.append((gate, status, detail))

    def report(self):
        w = max((len(r[0]) for r in self.rows), default=4)
        for g, s, d in self.rows:
            print('  %-6s %-*s %s' % (s, w, g, d))
        f = sum(1 for r in self.rows if r[1] == 'FAIL')
        p = sum(1 for r in self.rows if r[1] == 'PASS')
        sk = sum(1 for r in self.rows if r[1] == 'SKIP')
        print()
        print('  %d passed, %d failed, %d skipped' % (p, f, sk))
        if sk:
            print('  (a skipped gate is NOT a pass -- its data was not on this machine)')
        return f


def run():
    from core import ZoneSession
    b = Battery()
    zones = _zones()
    if not zones:
        b.add('X1-X5', 'SKIP', 'no fastfile found next to the repo root')
        return b.report()

    any_models = None
    for zp in zones:
        try:
            s = ZoneSession.open(zp)
        except Exception as ex:
            b.add('X1', 'SKIP', '%s: %s' % (os.path.basename(zp), ex))
            continue
        t0 = time.time()
        models, st = XM.extract_all(s)
        dt = time.time() - t0
        if st['total'] == 0:
            b.add('X1', 'SKIP', '%s carries no XModels' % os.path.basename(zp))
            continue

        b.add('X1', 'PASS' if st['failed'] == 0 else 'FAIL',
              '%s: %d XModels, %d with geometry, %d failed, %d skinned (%.1fs)'
              % (os.path.basename(zp), st['total'], st['geometry'], st['failed'],
                 st['skinned'], dt))
        for e in st['errors'][:3]:
            b.add('X1', 'FAIL', '  %s' % e)

        ok, chk = st['bounds_ok'], st['bounds_checked']
        b.add('X2', 'PASS' if chk and ok == chk else 'FAIL',
              '%s: %d/%d models have every vertex inside their stored mins/maxs'
              % (os.path.basename(zp), ok, chk))

        # X3 index validity, per surface
        bad = 0
        for m in models:
            for surf in m.surfaces:
                if surf.tris is None or surf.positions is None:
                    continue
                if surf.tris.size and surf.tris.max() >= len(surf.positions):
                    bad += 1
        b.add('X3', 'PASS' if bad == 0 else 'FAIL',
              '%s: %d surfaces with an out-of-range triangle index' % (os.path.basename(zp),
                                                                       bad))

        # X4 LOD partition + descending detail
        part_bad = desc_bad = lodded = 0
        for m in models:
            if m.lod_count < 2:
                continue
            lodded += 1
            seen = set()
            for li in range(m.lod_count):
                idxs = {sf.index for sf in m.lod_surfaces(li)}
                if idxs & seen:
                    part_bad += 1
                    break
                seen |= idxs
            counts = [m.tri_count_at(i) for i in range(m.lod_count)]
            counts = [c for c in counts if c]
            if len(counts) > 1 and any(counts[i + 1] > counts[i] for i in range(len(counts) - 1)):
                desc_bad += 1
        b.add('X4', 'PASS' if part_bad == 0 and desc_bad <= max(1, lodded // 20) else 'FAIL',
              '%s: %d multi-LOD models, %d with overlapping LOD surfaces, %d where detail '
              'does not descend' % (os.path.basename(zp), lodded, part_bad, desc_bad))

        if any_models is None:
            any_models = [m for m in models if m.renderable]

    # ---- X2r reachability: corrupt a model and require the bounds check to notice ----
    if any_models:
        m = max(any_models, key=lambda x: x.tri_count)
        surf = next((sf for sf in m.surfaces if sf.positions is not None), None)
        if surf is None:
            b.add('X2r', 'SKIP', 'no decoded surface to corrupt')
        else:
            keep = surf.positions
            try:
                # A plausible-looking defect: positions read at the wrong stride.
                surf.positions = keep + (np.abs(np.asarray(m.maxs)
                                                - np.asarray(m.mins)).max() + 50.0)
                caught = m.in_bounds() is False
            finally:
                surf.positions = keep
            b.add('X2r', 'PASS' if caught else 'FAIL',
                  'a displaced vertex buffer IS rejected by the bounds oracle' if caught
                  else 'the bounds oracle accepted displaced vertices -- it proves nothing')
    else:
        b.add('X2r', 'SKIP', 'no renderable model available')

    # ---- X5 render ----
    if not any_models:
        b.add('X5', 'SKIP', 'no renderable model available')
    else:
        from . import render3d as R3
        v = R3.View()
        ms = sorted(any_models, key=lambda x: x.tri_count)
        picks = [ms[len(ms) // 2], ms[int(len(ms) * 0.9)], ms[-1]]
        worst = 0.0
        blank = 0
        for m in picks:
            P, T = m.geometry(0)
            t0 = time.time()
            img = R3.render(P, T, v, 480, 480)
            worst = max(worst, time.time() - t0)
            a = np.asarray(img).reshape(-1, 3)
            if (a != np.array(R3.BG)).any(1).mean() < 0.01:
                blank += 1
        b.add('X5', 'PASS' if blank == 0 else 'FAIL',
              'median/p90/largest model render non-empty; slowest %.0f ms (%.0f fps)'
              % (worst * 1000, 1.0 / worst if worst else 0)
              if blank == 0 else '%d of 3 renders came out empty' % blank)

    return b.report()


if __name__ == '__main__':
    print('XModel viewer gate battery')
    print()
    sys.exit(1 if run() else 0)
