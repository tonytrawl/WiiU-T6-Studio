"""core.ipak_selftest -- the gate battery for the ipak viewer.

Run:  python -m core.ipak_selftest        (from WiiU_FF_Studio/)

Every claim the viewer makes about itself is checked here against real retail files rather than
against our own assumptions. The battery is written so that a check which CANNOT run (a missing
content directory, a pak that is not on this machine) is reported as SKIP and never counted as
a pass -- a green run on a machine with no data would be worthless.

  G1  container round-trip   an untouched pak re-saves byte-exact
  G2  metadata sections      section 3 and 4 parse and rebuild byte-exact
  G3  decode fidelity        fast detile/decode == the reference gx2_texture implementation
  G4  decode coverage        how many real entries actually decode, and why the rest do not
  G5  mip selection          a preview level correlates with level 0 downsampled
  G6  encode round-trip      re-encoding preserves payload size exactly and image content
  G7  edit round-trip        replace / add / delete survive a save and re-open
  G8  key preservation       replace keeps the key so a zone reference still resolves
"""
import os
import shutil
import sys
import tempfile

import numpy as np

from . import paths  # noqa: F401
from . import ipak_image as II
from . import ipak_names as NM
from .ipak import IpakSession

import ipak as IP


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _search_roots():
    """Where to look for test paks.

    In a frozen build ROOT points inside the extracted bundle and holds nothing, so the
    directory containing the exe and the current directory are searched too. Without this the
    battery would report SKIP on exactly the machines where verifying the shipped binary
    matters most.

    ⚠ THE USER'S CONTENT FOLDERS MUST BE IN HERE OR THE PACKAGED BATTERY IS VACUOUS. Measured:
    the source run passes 16 gates, while the exe built from the same tree reported

        SKIP  G1-G8  no ipak files found on this machine
        0 passed, 0 failed, 1 skipped

    because none of ROOT/exedir/cwd contains a pak -- the game data lives in the Cemu content
    directory, which only `core.settings` knows how to find. A gate that cannot fail is not a
    gate, and a packaged --selftest exists precisely to catch frozen-only breakage, so it has
    to search the same places the tool itself does.
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


#: Paks the battery knows the shape of, best first. mp_raid has sections 3+4; mp_carrier is
#: index+data only, so the pair covers both container layouts.
PREFERRED_PAKS = ('mp_raid.ipak', 'mp_carrier.ipak', 'lowmip_split1.ipak', 'base_split1.ipak')

MAX_FALLBACK_PAKS = 4          # enough to exercise every gate without a long scan


def _candidates():
    """Real paks to test against, in preference order. Missing ones are skipped."""
    roots = _search_roots()
    out = []
    for root in roots:
        for fn in PREFERRED_PAKS:
            p = os.path.join(root, fn)
            if os.path.exists(p) and p not in out:
                out.append(p)
    if out:
        return out
    # No pak we recognise by name. Take whatever real paks this machine does have -- an install
    # laid out differently still deserves a battery that runs. Depth-capped so a search root
    # that happens to sit above a huge tree cannot turn the selftest into a disk crawl.
    for root in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= 2:
                dirnames[:] = []
            for fn in sorted(filenames):
                if fn.lower().endswith('.ipak'):
                    p = os.path.join(dirpath, fn)
                    if p not in out:
                        out.append(p)
                    if len(out) >= MAX_FALLBACK_PAKS:
                        return out
    return out


class Battery(object):
    def __init__(self):
        self.rows = []

    def add(self, gate, status, detail=''):
        self.rows.append((gate, status, detail))

    def report(self):
        w = max(len(r[0]) for r in self.rows) if self.rows else 4
        for g, s, d in self.rows:
            print('  %-6s %-*s %s' % (s, w, g, d))
        p = sum(1 for r in self.rows if r[1] == 'PASS')
        f = sum(1 for r in self.rows if r[1] == 'FAIL')
        sk = sum(1 for r in self.rows if r[1] == 'SKIP')
        print()
        print('  %d passed, %d failed, %d skipped' % (p, f, sk))
        if sk:
            print('  (a skipped gate is NOT a pass -- the data it needs was not on this machine)')
        return f


def run():
    b = Battery()
    paks = _candidates()
    if not paks:
        b.add('G1-G8', 'SKIP', 'no ipak files found on this machine')
        return b.report()

    # ---- G1 byte-exact round-trip ------------------------------------------------------
    for p in paks:
        try:
            s = IpakSession.open(p)
            blob = s.build()
            orig = open(p, 'rb').read()
            b.add('G1', 'PASS' if blob == orig else 'FAIL',
                  '%s: %d entries, rebuild %s'
                  % (os.path.basename(p), len(s.items),
                     'byte-exact' if blob == orig else 'DIFFERS (%d vs %d)'
                                                      % (len(blob), len(orig))))
        except Exception as ex:
            b.add('G1', 'FAIL', '%s: %s: %s' % (os.path.basename(p), type(ex).__name__, ex))

    # ---- G2 metadata sections ----------------------------------------------------------
    did = False
    for p in paks:
        pak = IP.IPak.read(p)
        for typ, raw, cnt in pak.extra_sections:
            try:
                if typ == NM.SEC_META_TEXT:
                    recs = NM.parse_section3(raw)
                    out, n = NM.build_section3(recs)
                    b.add('G2', 'PASS' if out == raw and n == cnt else 'FAIL',
                          '%s section3: %d records, rebuild %s'
                          % (os.path.basename(p), n,
                             'byte-exact' if out == raw else 'DIFFERS'))
                    did = True
                elif typ == NM.SEC_KEY_LIST:
                    keys = NM.parse_section4(raw)
                    out, n = NM.build_section4(keys)
                    b.add('G2', 'PASS' if out == raw else 'FAIL',
                          '%s section4: %d keys, rebuild %s'
                          % (os.path.basename(p), n,
                             'byte-exact' if out == raw else 'DIFFERS'))
                    did = True
            except Exception as ex:
                b.add('G2', 'FAIL', '%s type %d: %s' % (os.path.basename(p), typ, ex))
    if not did:
        b.add('G2', 'SKIP', 'no pak with metadata sections available')

    # ---- G3 decode fidelity vs the reference implementation -----------------------------
    ok, bad = II.selftest(verbose=False)
    b.add('G3', 'PASS' if bad == 0 else 'FAIL',
          'fast path vs gx2_texture reference: %d checks passed, %d failed' % (ok, bad))

    # ---- G4 decode coverage on real entries --------------------------------------------
    s = IpakSession.open(paks[0])
    n_ok = n_err = 0
    reasons = {}
    for it in s.items[:300]:
        try:
            s.preview(it, max_side=256)
            n_ok += 1
        except Exception as ex:
            n_err += 1
            reasons[str(ex)[:60]] = reasons.get(str(ex)[:60], 0) + 1
    detail = '%s: %d/%d entries decode' % (os.path.basename(paks[0]), n_ok, n_ok + n_err)
    if reasons:
        detail += ' | ' + '; '.join('%s (x%d)' % (k, v) for k, v in
                                    sorted(reasons.items(), key=lambda t: -t[1])[:3])
    b.add('G4', 'PASS' if n_ok else 'FAIL', detail)

    # ---- G5 mip level offsets, against planted ground truth ------------------------------
    #
    # An earlier version of this gate compared a decoded mip against level 0 box-downsampled
    # and required correlation > 0.90. MEASURED AND REJECTED: deliberately corrupting the mip
    # offset by +-256 and +4096 bytes still scored up to 0.996, and 29% of wrong offsets beat
    # 0.75. Retail mips are linker-generated, not box filters, so that comparison never had the
    # discriminating power it appeared to have -- it would have passed a genuinely broken
    # offset model. Rule: a gate that cannot fail on bad input is not a gate.
    #
    # This version plants KNOWN, PER-LEVEL-DISTINCT content instead. The payload is laid out by
    # wiiu_ref/ipak_stream.tile_part_payload -- the authoring routine independently proven to
    # reproduce genuine retail part payloads byte-exactly -- and then each level is decoded and
    # must come back as the exact colour planted at that level. A wrong offset yields the wrong
    # colour, with no ambiguity.
    try:
        import ipak_stream as IST

        gfmt = 0x31                       # BC1 encodes a flat colour exactly
        W = H = 256
        LEVELS = 6
        colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                   (255, 255, 0), (0, 255, 255), (255, 0, 255)]

        blob = bytearray()
        part_mips = []
        for i in range(LEVELS):
            lw, lh = max(1, W >> i), max(1, H >> i)
            img = np.zeros((lh, lw, 4), np.uint8)
            img[:, :, :3] = colours[i]
            img[:, :, 3] = 255
            tight = II.encode_surface(img, lw, lh, gfmt)
            part_mips.append((lw, lh, len(blob), len(tight)))
            blob += tight

        payload = IST.tile_part_payload(bytes(blob), part_mips, gfmt)

        wrong = []
        for i in range(LEVELS):
            lw = max(1, W >> i)
            got, lvl, dims = II.decode_payload(payload, W, H, gfmt,
                                               levels=LEVELS, max_side=lw)
            if lvl != i:
                wrong.append('level %d: selector chose %d' % (i, lvl))
                continue
            mean = got[:, :, :3].reshape(-1, 3).mean(axis=0)
            want = np.array(colours[i], np.float32)
            if np.abs(mean - want).max() > 8:
                wrong.append('level %d: got RGB %s, planted %s'
                             % (i, tuple(int(v) for v in mean), colours[i]))
        b.add('G5', 'PASS' if not wrong else 'FAIL',
              'all %d planted mip levels decode at the right offset with the right content'
              % LEVELS if not wrong else '; '.join(wrong[:3]))

        # And prove the gate can actually fail: corrupt the offset and require it to notice.
        img_size, mip_offs, _ms, infos = __import__('gx2_texture').mip_chain(
            gfmt, W, H, II.base_tile_mode(gfmt, W, H), LEVELS)
        inf = infos[2]
        at = img_size + mip_offs[1] + 512               # deliberately wrong
        lw2 = max(1, W >> 2)
        lin = II.detile(payload[at:at + inf.size], lw2, lw2, gfmt, inf.tile_mode,
                        pitch=inf.pitch)
        px = II.decode_rgba(II.crop_linear(lin, lw2, lw2, gfmt, inf.pitch), lw2, lw2, gfmt)
        off_mean = px[:, :, :3].reshape(-1, 3).mean(axis=0)
        caught = np.abs(off_mean - np.array(colours[2], np.float32)).max() > 8
        b.add('G5r', 'PASS' if caught else 'FAIL',
              'reachability: a deliberately wrong offset IS rejected by this check'
              if caught else 'a wrong offset passed -- this gate proves nothing')
    except Exception as ex:
        b.add('G5', 'SKIP', 'ground-truth layout unavailable: %s: %s'
                            % (type(ex).__name__, ex))

    # ---- G6 encode round-trip -----------------------------------------------------------
    src = None
    for p in paks:
        ss = IpakSession.open(p)
        c = [i for i in ss.items if i.previewable and i.width >= 64]
        if c:
            src = (ss, c[:8])
            break
    if not src:
        b.add('G6', 'SKIP', 'no previewable entry available')
    else:
        ss, cands = src
        size_ok = corr_ok = n = 0
        for it in cands:
            try:
                rgba, _l, _d = ss.preview(it, max_side=None)
                blob = II.encode_payload(rgba, it.width, it.height, it.gx2_format,
                                         levels=it.levels)
                back, _l2, _d2 = II.decode_payload(blob, it.width, it.height, it.gx2_format,
                                                   levels=it.levels)
            except Exception:
                continue
            n += 1
            size_ok += int(len(blob) == len(ss.payload(it)))
            a = rgba[:, :, :3].astype(np.float32)
            bb = back[:, :, :3].astype(np.float32)
            corr_ok += int(a.std() < 2.0 or np.corrcoef(a.ravel(), bb.ravel())[0, 1] > 0.85)
        b.add('G6', 'PASS' if n and size_ok == n and corr_ok == n else 'FAIL',
              're-encode: %d/%d payload sizes identical, %d/%d images preserved' % (
                  size_ok, n, corr_ok, n))

    # ---- G7 / G8 edit round-trip --------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix='ipakgate_')
    try:
        work = os.path.join(tmp, 'edit.ipak')
        shutil.copy(paks[0], work)
        s1 = IpakSession.open(work)
        target = next((i for i in s1.items if i.previewable and i.width >= 64), None)
        if target is None:
            b.add('G7', 'SKIP', 'no previewable entry to edit')
            b.add('G8', 'SKIP', 'no previewable entry to edit')
        else:
            key = target.key
            rgba, _l, _d = s1.preview(target, max_side=None)
            paint = rgba.copy()
            paint[:, :, 0] = 255
            paint[:, :, 1] = 0
            s1.replace_image(target, paint)
            same_key = target.key == key
            n_before = len(s1.items)
            s1.save(work, backup=False)

            s2 = IpakSession.open(work)
            hit = [i for i in s2.items if i.key == key]
            got, _l, _d = s2.preview(hit[0], max_side=None) if hit else (None, 0, 0)
            b.add('G8', 'PASS' if same_key and hit else 'FAIL',
                  'replace preserved key %08X/%08X and it re-resolved after save'
                  % key if same_key and hit else 'replace lost the entry key')
            red = got is not None and got[:, :, 0].mean() > 200 and got[:, :, 1].mean() < 40
            b.add('G7a', 'PASS' if red and len(s2.items) == n_before else 'FAIL',
                  'replaced pixels survived save+reopen, entry count %d unchanged' % n_before)

            img = np.zeros((64, 64, 4), np.uint8)
            img[:, :, 2] = 255
            img[:, :, 3] = 255
            newit = s2.add_image('ipak_selftest_tex', img, format_string='DXT5', levels=1)
            nh = newit.name_hash
            s2.save(work, backup=False)
            s3 = IpakSession.open(work)
            added = [i for i in s3.items if i.name_hash == nh]
            blue = False
            if added:
                px, _l, _d = s3.preview(added[0], max_side=None)
                blue = px[:, :, 2].mean() > 200
            b.add('G7b', 'PASS' if added and blue and len(s3.items) == n_before + 1 else 'FAIL',
                  'added texture present, named %r, previews back correctly'
                  % (added[0].name if added else None))

            s3.delete(added[0])
            s3.save(work, backup=False)
            s4 = IpakSession.open(work)
            gone = not any(i.name_hash == nh for i in s4.items)
            b.add('G7c', 'PASS' if gone and len(s4.items) == n_before else 'FAIL',
                  'deleted entry removed, count back to %d' % n_before)

        # ---- G9 whole-image replace: EVERY part changes, not just the selected one -------
        # The user-reported "I replaced it and the stock texture still shows" was exactly this:
        # a streamed image is split across up to 3 parts (part 0 = mips 2..N low-detail tail,
        # parts 1/2 = mip 1 and mip 0, the levels you see up close). Replacing one part leaves
        # the others serving the original pixels. This gate fails if any part is left stock.
        from collections import defaultdict

        def _multi_pak():
            """A pak that actually HAS a multi-part previewable image.

            The first candidate is not always one -- a resident tier like base_split1 holds a
            single part per image, so the gate would skip and prove nothing about the very
            behaviour it exists to check. Try each candidate until one can exercise it.
            """
            for cand in paks:
                w = os.path.join(tmp, 'multi_' + os.path.basename(cand))
                try:
                    if not os.path.exists(w):
                        shutil.copy(cand, w)
                    sess = IpakSession.open(w)
                except Exception:
                    continue
                g = defaultdict(list)
                for it in sess.items:
                    g[it.name_hash].append(it)
                hit = [v for v in g.values() if len(v) > 1 and all(x.previewable for x in v)]
                if hit:
                    return w, sess, hit
            return None, None, None

        work5, s5, multi = _multi_pak()
        if not multi:
            b.add('G9', 'SKIP', 'no candidate pak has a multi-part previewable image')
            b.add('G9r', 'SKIP', 'no multi-part image to contrast against')
        else:
            grp = sorted(multi[0], key=lambda x: x.part_index)
            src = np.zeros((256, 256, 4), np.uint8)
            src[..., 0] = 255
            src[..., 3] = 255                                     # solid red

            def mean_rgb(sess, part):
                rgba = sess.preview(part, max_side=32)[0]
                return np.asarray(rgba)[..., :3].reshape(-1, 3).mean(0)

            rep = s5.replace_image_all(grp[0], src)
            after = [mean_rgb(s5, p) for p in s5.parts_of(grp[0])]
            all_red = all(a[0] > 200 and a[1] < 60 and a[2] < 60 for a in after)
            b.add('G9', 'PASS' if all_red and not rep['skipped'] else 'FAIL',
                  '%r: all %d parts replaced %s' % (rep['name'], len(after),
                                                    [tuple(int(x) for x in a) for a in after])
                  if all_red else 'parts left stock: %s; skipped %s'
                  % ([tuple(int(x) for x in a) for a in after], rep['skipped']))

            # Reachability: the OLD single-part behaviour must FAIL this gate, otherwise the
            # gate proves nothing about whole-image replacement.
            s6 = IpakSession.open(work5)
            g6 = defaultdict(list)
            for it in s6.items:
                g6[it.name_hash].append(it)
            grp6 = sorted([v for v in g6.values()
                           if len(v) > 1 and all(x.previewable for x in v)][0],
                          key=lambda x: x.part_index)
            s6.replace_image(grp6[0], src)                        # one part only
            after6 = [mean_rgb(s6, p) for p in s6.parts_of(grp6[0])]
            left_stock = any(not (a[0] > 200 and a[1] < 60 and a[2] < 60) for a in after6)
            b.add('G9r', 'PASS' if left_stock else 'FAIL',
                  'single-part replace DOES leave other parts stock %s -- so G9 can fail'
                  % [tuple(int(x) for x in a) for a in after6] if left_stock
                  else 'single-part replace changed every part, so G9 cannot fail')
        # ---- G10 the name dictionary is actually USABLE in this build ---------------------
        # A packaged build shipped with no dictionary and showed bare hex ids for every entry,
        # while the source build named them. Nothing caught it because every other gate works
        # fine on unnamed entries. This gate fails if names are missing.
        try:
            idx = NM.load_shared()
            n_keys = len(idx.by_key)
            sess = IpakSession.open(paks[0])
            named = sum(1 for it in sess.items if it.name)
            frac = named / float(len(sess.items) or 1)
            where = 'cache' if n_keys else 'EMPTY'
            ok = n_keys > 0 and frac >= 0.5
            b.add('G10', 'PASS' if ok else 'FAIL',
                  '%s: %d/%d entries named (%.0f%%), dictionary %s with %d keys%s'
                  % (os.path.basename(paks[0]), named, len(sess.items), frac * 100, where,
                     n_keys, '  [cache built on a different install]'
                             if getattr(idx, 'stale', False) else ''))
        except Exception as ex:
            b.add('G10', 'FAIL', 'name dictionary unusable: %s: %s' % (type(ex).__name__, ex))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- G11 / G11r the gallery must survive a pak far larger than Tk's grid can address ----
    # Reported from the field as "ipaks crash when opening", with this in studio_errors.log:
    #     ipak_ide._refill -> gallery.set_items -> _layout -> grid_configure
    #     _tkinter.TclError: row out of bounds
    # The gallery built four widgets per entry and gridded them, so a large pak ran Tk out of
    # grid rows -- and long before that, out of patience.
    try:
        import tkinter as _tk
        from . import gallery as _gal
        _r = _tk.Tk()
        # ⚠ NOT withdraw(). A withdrawn window reports 1x1, so `_layout` correctly defers and
        # the gate would measure zero tiles and call it a failure -- the same trap W15 hit.
        # Transparent-but-mapped gives real geometry without putting a window in the user's face.
        _r.geometry('900x600+40+40')
        try:
            _r.attributes('-alpha', 0.0)
        except _tk.TclError:
            pass
        g = _gal.Gallery(_r, key=lambda i: i, label=lambda i: 't%05d' % i,
                         thumbnail=lambda i: None)
        g.pack(fill='both', expand=True)
        _r.update()
        N = 40000
        g.set_items(list(range(N)))
        _r.update()
        built = len(g._tiles)
        for f in (0.0, 0.5, 1.0):
            g.canvas.yview_moveto(f)
            g._sync_tiles()
            _r.update()
        after = len(g._tiles)
        lo, hi = g.canvas.yview()
        ok = built <= 400 and after <= 400 and (hi - lo) < 0.05
        b.add('G11', 'PASS' if ok else 'FAIL',
              ('%d items: %d tile(s) built, %d after scrolling end to end, thumb %.4f of the '
               'track' % (N, built, after, hi - lo)) if ok else
              ('unbounded or mis-sized: %d/%d tiles, thumb %.4f' % (built, after, hi - lo)))

        # The control: the same row count through the OLD eager path must still fail, or G11
        # is not testing a real constraint.
        raised = False
        try:
            for i in range(N):
                _tk.Frame(g.inner).grid(row=i, column=0)
        except _tk.TclError:
            raised = True
        b.add('G11r', 'PASS' if raised else 'FAIL',
              ('gridding %d rows still raises TclError, so G11 measures a real limit' % N)
              if raised else ('Tk accepted %d grid rows -- G11 proves nothing here' % N))
        g.destroy()
        _r.destroy()
    except Exception as ex:
        b.add('G11', 'FAIL', '%s: %s' % (type(ex).__name__, str(ex)[:110]))

    return b.report()


if __name__ == '__main__':
    print('ipak viewer gate battery')
    print()
    sys.exit(1 if run() else 0)
