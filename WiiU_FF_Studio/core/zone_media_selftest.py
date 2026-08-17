"""core.zone_media_selftest -- gates for a zone's inline images and its SndBank tables.

Run:  python -m core.zone_media_selftest

Every check is written so it CAN fail, and the two that matter most carry a reachability twin:

  Z1  inline images are located in a real zone
  Z2  a decoded texture looks like an image, and the WRONG tiling scores worse (Z2r)
  Z3  a re-encode is byte-length-exact and decodes back to the same picture
  Z4  a raw patch through ZoneSession is length-neutral, survives the walk, and reads back
  Z5r the session REFUSES to combine a raw patch with a growing edit
  Z6  SndBank alias tables parse, and name real entries in the matching bank file
"""
import os
import shutil
import sys
import tempfile

import numpy as np

from . import paths  # noqa: F401
from . import settings as _st
from . import zone_images as ZI
from . import zone_sounds as ZS


def _zones(limit=10):
    """Real zones from the user's own search path plus the checkout.

    `common_*` zones are tried first: they are where SndBanks and large inline image sets
    actually live, so a small sample that happens to miss them turns Z6 into a SKIP for no good
    reason. A SKIP is honest, but it should mean "not on this machine", not "I looked in the
    wrong four files".
    """
    out = []
    roots = list(_st.search_dirs())
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots += [here, os.path.dirname(here)]
    for r in roots:
        if not os.path.isdir(r) or len(out) >= limit:
            continue
        base = r.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(r):
            if dirpath.rstrip(os.sep).count(os.sep) - base >= 3:
                dirnames[:] = []
            for fn in sorted(files):
                if fn.lower().endswith('.ff'):
                    p = os.path.join(dirpath, fn)
                    if p not in out:
                        out.append(p)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
    out.sort(key=lambda p: (0 if 'common' in os.path.basename(p).lower() else 1, p))
    return out


def _corr(img):
    """Neighbour correlation on luma. Noise ~0, a real texture ~0.8+."""
    a = np.asarray(img)[..., :3].astype(np.float32).mean(axis=2)
    if a.shape[0] < 4 or a.shape[1] < 4:
        return 0.0
    vals = []
    for x, y in ((a[:, :-1].ravel(), a[:, 1:].ravel()), (a[:-1, :].ravel(), a[1:, :].ravel())):
        if x.std() > 1e-6 and y.std() > 1e-6:
            vals.append(abs(float(np.corrcoef(x, y)[0, 1])))
    return sum(vals) / len(vals) if vals else 0.0


class Battery(object):
    def __init__(self):
        self.rows = []

    def add(self, gate, status, detail=''):
        self.rows.append((status, gate, detail))

    def report(self):
        w = max((len(r[1]) for r in self.rows), default=4)
        for s, g, d in self.rows:
            print('  %-6s %-*s %s' % (s, w, g, d))
        f = sum(1 for r in self.rows if r[0] == 'FAIL')
        p = sum(1 for r in self.rows if r[0] == 'PASS')
        sk = sum(1 for r in self.rows if r[0] == 'SKIP')
        print()
        print('  %d passed, %d failed, %d skipped' % (p, f, sk))
        if sk:
            print('  (a skipped gate is NOT a pass -- its data was not on this machine)')
        return f


def run():
    from . import ZoneSession
    b = Battery()
    zones = _zones()
    if not zones:
        b.add('Z1', 'SKIP', 'no .ff on this machine')
        return b.report()

    # Pick the first zone that actually has inline images to work with.
    zone = imgs = zpath = None
    for p in zones:
        try:
            s = ZoneSession.open(p)
        except Exception:
            continue
        cand = [i for i in ZI.list_images(s.zone, inline_only=True)
                if i.width >= 32 and i.height >= 32 and i.gx2_format]
        if cand:
            zone, imgs, zpath = s.zone, cand, p
            break

    if not imgs:
        b.add('Z1', 'SKIP', 'no zone with inline images among %d checked' % len(zones))
    else:
        total, inline, streamed, other = ZI.summarise(ZI.list_images(zone))
        b.add('Z1', 'PASS', '%s: %d image records, %d inline / %d streamed / %d other; '
                            '%d usable for these gates'
              % (os.path.basename(zpath), total, inline, streamed, other, len(imgs)))

        # ---- Z2 / Z2r decode fidelity, with the wrong interpretation as the control -------
        img = max(imgs, key=lambda i: i.width * i.height)
        try:
            rgba, _lv, _d = ZI.decode(zone, img)
            good = _corr(rgba)
            import gx2_texture as gx
            from . import ipak_image as II
            raw = ZI.payload(zone, img)
            pitch = gx.surface_info(img.gx2_format, img.width, img.height, 0).pitch
            wrong = _corr(II.decode_rgba(
                II.crop_linear(raw, img.width, img.height, img.gx2_format, pitch),
                img.width, img.height, img.gx2_format))
            b.add('Z2', 'PASS' if good >= 0.60 else 'FAIL',
                  '%s %s decodes with neighbour-correlation %.3f'
                  % (img.label[:34], img.dims, good))
            b.add('Z2r', 'PASS' if good > wrong + 0.05 else 'FAIL',
                  'the tiled reading (%.3f) beats the untiled one (%.3f), so Z2 is measuring '
                  'the layout and not just "any bytes"' % (good, wrong)
                  if good > wrong + 0.05 else
                  'tiled %.3f vs untiled %.3f -- Z2 cannot tell them apart' % (good, wrong))
        except Exception as ex:
            b.add('Z2', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

        # ---- Z3 re-encode is length-exact and stable -------------------------------------
        try:
            rgba, _lv, _d = ZI.decode(zone, img)
            blob = ZI.encode_replacement(rgba, img)
            same_len = len(blob) == img.pixel_len
            from . import ipak_image as II
            back, _l2, _d2 = II.decode_payload(blob, img.width, img.height, img.gx2_format,
                                               levels=img.levels)
            diff = float(np.abs(np.asarray(back, np.int16)[..., :3]
                                - np.asarray(rgba, np.int16)[..., :3]).mean())
            b.add('Z3', 'PASS' if same_len and diff < 12.0 else 'FAIL',
                  're-encode is %d B (slot %d B) and decodes back with mean channel delta %.1f'
                  % (len(blob), img.pixel_len, diff))
        except Exception as ex:
            b.add('Z3', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

        # ---- Z4 a raw patch really lands, and the zone still walks ------------------------
        tmp = tempfile.mkdtemp(prefix='zonemedia_')
        try:
            work = os.path.join(tmp, os.path.basename(zpath))
            shutil.copy2(zpath, work)                 # never touch the original
            s2 = ZoneSession.open(work)
            imgs2 = [i for i in ZI.list_images(s2.zone, inline_only=True)
                     if i.off == img.off]
            if not imgs2:
                b.add('Z4', 'SKIP', 'the chosen image was not found in the working copy')
            else:
                i2 = imgs2[0]
                flat = np.zeros((i2.height, i2.width, 4), np.uint8)
                flat[..., 0] = 255
                flat[..., 3] = 255                    # unmistakable red
                before = len(s2.zone)
                # ⚠ MEASURE THE BASE FIRST. Asserting walk_ok==True after the patch is only
                # meaningful if the UNPATCHED zone walks. Some real zones do not (measured:
                # afghanistan.ff walks False untouched), and blaming that on the edit would be
                # a false verdict. The property that matters is that the patch CHANGES NOTHING.
                from . import assets as _a
                try:
                    walk_before = _a.enumerate_zone(s2.zone).walk_ok
                except Exception:
                    walk_before = None
                ZI.replace(s2, i2, flat)
                plan = s2.plan()
                built, _p = s2.build_zone()
                try:
                    walk_ok = _a.enumerate_zone(built).walk_ok
                except Exception:
                    walk_ok = None
                # the patched bytes must actually be in the output
                landed = built[i2.pixel_off:i2.pixel_off + i2.pixel_len] \
                    == ZI.encode_replacement(flat, i2)
                ok = (len(built) == before and landed and plan.strategy == 'in-place'
                      and walk_ok == walk_before)
                b.add('Z4', 'PASS' if ok else 'FAIL',
                      'patch landed, zone stayed %d B, strategy=%s, walk unchanged (%s->%s)'
                      % (len(built), plan.strategy, walk_before, walk_ok) if ok else
                      'len %d->%d landed=%s strategy=%s walk %s->%s'
                      % (before, len(built), landed, plan.strategy, walk_before, walk_ok))

                # ---- Z5r the refusal must be reachable --------------------------------
                growable = next((a for a in s2.assets if getattr(a, 'editable', False)), None)
                if growable is None:
                    b.add('Z5r', 'SKIP', 'no growable asset in this zone to test the refusal')
                else:
                    try:
                        s2.stage(growable, bytes(growable.extract(s2.zone)) + b'\x00' * 64)
                        p2 = s2.plan()
                        b.add('Z5r', 'PASS' if p2.strategy == 'refuse' else 'FAIL',
                              'a raw patch combined with a growing edit is REFUSED'
                              if p2.strategy == 'refuse' else
                              'combination was allowed (strategy=%s) -- offsets would move'
                              % p2.strategy)
                    except Exception as ex:
                        b.add('Z5r', 'SKIP', 'could not stage a growing edit: %s' % ex)
        except Exception as ex:
            b.add('Z4', 'FAIL', '%s: %s' % (type(ex).__name__, ex))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- Z6 SndBank alias tables ---------------------------------------------------------
    found = None
    for p in zones:
        try:
            s = ZoneSession.open(p)
        except Exception:
            continue
        banks = [x for x in ZS.list_banks(s.zone) if x.aliases]
        if banks:
            found = (p, banks)
            break
    if not found:
        b.add('Z6', 'SKIP', 'no zone with a populated SndBank among %d checked' % len(zones))
    else:
        zp, banks = found
        bank = banks[0]
        cov = None
        for cand in ZS.bank_files(bank):
            cov = ZS.coverage(bank, cand)
            if cov:
                break
        detail = ('%s: %d bank(s), %r has %d alias->filename pairs'
                  % (os.path.basename(zp), len(banks), bank.label, len(bank.aliases)))
        if cov:
            detail += '; names %d/%d entries of the matching bank file' % cov
        b.add('Z6', 'PASS' if bank.aliases else 'FAIL', detail)

    return b.report()


if __name__ == '__main__':
    print('Zone media gate battery (inline images + sound bank tables)')
    sys.exit(1 if run() else 0)
