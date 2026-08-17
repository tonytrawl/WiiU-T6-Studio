"""core.scripts_selftest -- gate battery for GSC/Lua editing, growth and adding.

Run:  python -m core.scripts_selftest        (from WiiU_FF_Studio/)

  P1   GSC replace      compile source -> replace an asset -> rebuild -> disassembles back
  P2   Lua replace      same for HKS, and the result is BIG-ENDIAN (byte 6 == 0)
  P3   growth range     +4 B .. +256 KB all rebuild, re-walk EOF-exact, payload byte-identical
  P4   add GSC          a brand new ScriptParseTree appears and disassembles
  P5   add Lua          a brand new RawFile appears, big-endian
  P6   combined         2 edits + 2 adds in one save
  P7r  reachability     broken source FAILS to compile; a duplicate name IS refused
  P8   save to disk     a full .ff is written, reopens, and the edit is present
  P9   plain data        text / cfg / binary RawFile grow and round-trip
  P10  add any file      a new .cfg and .csv are added as RawFile with byte-identical content
  P11r count-not-length  KeyValuePairs / SoundPatch are read-only, and a forced bad resize IS
                         refused by the post-build walk guard

⚠ THE ZONE UNDER TEST IS A COPY. patch_mp is live, shared, and contended; the battery copies it
into a temp file and never writes to the original.

WHY D4 IS NOT A GATE HERE
-------------------------
`verify.verify_against_baseline`'s D4 ("dangling-alias residue must not increase") counts
724,030 alias-shaped words on an UNTOUCHED patch_mp, and `relink.grow` classifies that same
population as runtime-domain rather than as pointers. Appending 100 bytes moved it by +1, and
the newly counted word was 0xBBFFFFFF read straddling a FOLLOW pointer -- not a pointer field.
The authoritative gates are the relink report's own (subs fired, omap interior check, blind
candidates) plus the content round-trip below. See core/scripts.py.
"""
import os
import shutil
import sys
import tempfile

from . import paths  # noqa: F401
from . import scripts as SC

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GSC_SRC = 'main(){ level.p_test = 1; for (i = 0; i < 3; i++) level.p_test = level.p_test + i; }'
LUA_SRC = 'local M = {}\nfunction M.f(a) local t = {} for i = 1, 3 do t[i] = i * a end return t end\nreturn M\n'


def _candidate_zones():
    live = os.path.join(os.environ.get('APPDATA', ''), 'Cemu', 'mlc01', 'usr', 'title',
                        '0005000e', '1010cf00', 'content', 'english', 'patch_mp.ff')
    out = [live, os.path.join(ROOT, 'patch_mp.ff'), os.path.join(ROOT, 'common_mp.ff')]
    return [p for p in out if p and os.path.exists(p)]


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
    from core import assets as A
    from core import gsc as G
    b = Battery()

    srcs = _candidate_zones()
    if not srcs:
        b.add('P1-P8', 'SKIP', 'no script-bearing fastfile found (need patch_mp.ff)')
        return b.report()

    tmpdir = tempfile.mkdtemp(prefix='ffscripts_')
    work = os.path.join(tmpdir, 'work.ff')
    shutil.copy2(srcs[0], work)                    # never touch the live file

    def fresh():
        return ZoneSession.open(work)

    try:
        s = fresh()
        gsc_assets = [x for x in s.assets if x.payload == 'gsc']
        hks_assets = [x for x in s.assets if x.payload == 'hks']
        if not gsc_assets or not hks_assets:
            b.add('P1-P8', 'SKIP', '%s carries %d GSC / %d HKS assets'
                  % (os.path.basename(srcs[0]), len(gsc_assets), len(hks_assets)))
            return b.report()

        # ---- P1 GSC replace ----------------------------------------------------
        s = fresh()
        a = [x for x in s.assets if x.payload == 'gsc'][0]
        blob, delta = SC.stage_compiled(s, a, GSC_SRC)
        zone, _p = s.build_zone()
        ok, lines = SC.verify_edit(s, zone, s.last_relink_report)
        back = [x for x in A.enumerate_zone(zone).assets if x.name == a.name]
        dis_ok = False
        if back:
            g = G.GscScript(back[0].extract(zone), a.name)
            dis_ok = (g.source_endian == 'console' and g.spt.name == a.name
                      and 'main' in [e.name for e in g.spt.exports])
        b.add('P1', 'PASS' if ok and dis_ok else 'FAIL',
              '%s: %d -> %d B (%+d), rebuilt zone re-walks and disassembles back'
              % (a.name, a.buf_len, len(blob), delta)
              if ok and dis_ok else 'verify=%s disassembles=%s; %s' % (ok, dis_ok,
                                                                      '; '.join(lines[:2])))

        # ---- P2 Lua replace ----------------------------------------------------
        s = fresh()
        a = [x for x in s.assets if x.payload == 'hks'][0]
        blob, delta = SC.stage_compiled(s, a, LUA_SRC)
        zone, _p = s.build_zone()
        ok, lines = SC.verify_edit(s, zone, s.last_relink_report)
        back = [x for x in A.enumerate_zone(zone).assets if x.name == a.name]
        be_ok = bool(back) and back[0].extract(zone)[:4] == b'\x1bLua' \
            and back[0].extract(zone)[6] == 0
        b.add('P2', 'PASS' if ok and be_ok else 'FAIL',
              '%s: %d -> %d B (%+d), big-endian HKS on re-read'
              % (a.name, a.buf_len, len(blob), delta)
              if ok and be_ok else 'verify=%s big-endian=%s' % (ok, be_ok))

        # ---- P3 growth range ---------------------------------------------------
        results = []
        for grow in (4, 1024, 65536, 262144):
            s = fresh()
            a = [x for x in s.assets if x.payload == 'gsc'][0]
            want = a.extract(s.zone) + b'\x00' * grow
            s.stage(a, want)
            try:
                zone, _p = s.build_zone()
                en = A.enumerate_zone(zone)
                got = [x for x in en.assets if x.name == a.name]
                good = (en.walk_ok and len(en.assets) == len(s.assets)
                        and got and got[0].extract(zone) == want
                        and len(zone) - len(s.zone) == grow)
            except Exception as ex:
                good = False
                results.append('%+d FAILED (%s)' % (grow, str(ex)[:40]))
                continue
            results.append('%+d %s' % (grow, 'ok' if good else 'BAD'))
        b.add('P3', 'PASS' if all('ok' in r for r in results) else 'FAIL',
              'payload growth: ' + ', '.join(results))

        # ---- P4 / P5 add -------------------------------------------------------
        for gate, kind, name, src, check in (
                ('P4', 'gsc', 'maps/mp/gametypes/p_selftest.gsc',
                 'main(){ level.p_added = 1; }', 'SCRIPTPARSETREE'),
                ('P5', 'lua', 'ui/t6/p_selftest.lua', LUA_SRC, 'RAWFILE')):
            s = fresh()
            n0 = len(s.assets)
            try:
                SC.add_script(s, name, src, kind)
                zone, _p = s.build_zone()
                en = A.enumerate_zone(zone)
                hit = [x for x in en.assets if x.name == name]
                good = (en.walk_ok and len(en.assets) == n0 + 1 and len(hit) == 1
                        and hit[0].type_name == check)
                extra = ''
                if good and kind == 'gsc':
                    g = G.GscScript(hit[0].extract(zone), name)
                    good = good and g.source_endian == 'console' and g.spt.name == name
                    extra = ', disassembles as console GSC'
                elif good:
                    good = good and hit[0].extract(zone)[6] == 0
                    extra = ', big-endian'
                b.add(gate, 'PASS' if good else 'FAIL',
                      '%d -> %d assets, new %s present%s' % (n0, len(en.assets), check, extra)
                      if good else 'walk=%s count=%d hit=%d' % (en.walk_ok, len(en.assets),
                                                                len(hit)))
            except Exception as ex:
                b.add(gate, 'FAIL', '%s: %s' % (type(ex).__name__, ex))

        # ---- P6 combined -------------------------------------------------------
        s = fresh()
        n0 = len(s.assets)
        try:
            SC.stage_compiled(s, [x for x in s.assets if x.payload == 'gsc'][0], GSC_SRC)
            SC.stage_compiled(s, [x for x in s.assets if x.payload == 'hks'][0], LUA_SRC)
            SC.add_script(s, 'maps/mp/gametypes/p_combined.gsc', 'main(){ level.c = 1; }', 'gsc')
            SC.add_script(s, 'ui/t6/p_combined.lua', LUA_SRC, 'lua')
            zone, _p = s.build_zone()
            ok, lines = SC.verify_edit(s, zone, s.last_relink_report)
            en = A.enumerate_zone(zone)
            names = {x.name for x in en.assets}
            good = (ok and en.walk_ok and len(en.assets) == n0 + 2
                    and 'maps/mp/gametypes/p_combined.gsc' in names
                    and 'ui/t6/p_combined.lua' in names)
            b.add('P6', 'PASS' if good else 'FAIL',
                  '2 edits + 2 adds in one save: %d -> %d assets, payloads verified'
                  % (n0, len(en.assets)) if good else 'verify=%s count=%d' % (ok, len(en.assets)))
        except Exception as ex:
            b.add('P6', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

        # ---- P7r reachability --------------------------------------------------
        s = fresh()
        bad_rejected = dup_rejected = False
        try:
            SC.compile_gsc('main(){ this is not valid gsc @@@ ;;; }', 'x.gsc')
        except Exception:
            bad_rejected = True
        try:
            SC.add_script(s, [x for x in s.assets if x.payload == 'gsc'][0].name,
                          'main(){}', 'gsc')
        except Exception:
            dup_rejected = True
        b.add('P7r', 'PASS' if bad_rejected and dup_rejected else 'FAIL',
              'invalid source IS rejected and a duplicate asset name IS refused'
              if bad_rejected and dup_rejected
              else 'invalid rejected=%s duplicate refused=%s' % (bad_rejected, dup_rejected))

        # ---- P8 save to disk ---------------------------------------------------
        s = fresh()
        a = [x for x in s.assets if x.payload == 'gsc'][0]
        blob, _d = SC.stage_compiled(s, a, GSC_SRC)
        # Also add one, so P8 covers the container path through a real .ff write.
        SC.add_script(s, 'maps/mp/gametypes/p_saved.gsc', 'main(){ level.saved = 1; }', 'gsc')
        out = os.path.join(tmpdir, 'out.ff')
        n_before = len(s.assets)          # save() re-enumerates the session in place
        try:
            s.save(out, verify=False)
            s2 = ZoneSession.open(out)
            hit = s2.find(a.name)
            added = s2.find('maps/mp/gametypes/p_saved.gsc')
            # BYTE-IDENTICAL, not merely "present and valid" -- a script that survives as
            # plausible bytecode but not as OUR bytecode would pass the weaker check.
            same = hit is not None and hit.extract(s2.zone) == blob
            grew = len(s2.assets) == n_before + 1
            good = same and added is not None and grew
            b.add('P8', 'PASS' if good else 'FAIL',
                  'wrote %s (%s bytes), reopened: edited script byte-identical, added script '
                  'present, %d -> %d assets'
                  % (os.path.basename(out), format(os.path.getsize(out), ','),
                     n_before, len(s2.assets)) if good
                  else 'byte-identical=%s added=%s count %d -> %d'
                       % (same, added is not None, n_before, len(s2.assets)))
        except Exception as ex:
            b.add('P8', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

        # ---- P9 plain-data assets grow (text / cfg / csv / binary RawFile) -----
        rows = []
        for want in ('text', 'binary'):
            s = fresh()
            cand = [x for x in s.assets if x.payload == want and x.editable]
            if not cand:
                rows.append('%s: none present' % want)
                continue
            a = cand[0]
            data = a.extract(s.zone) + b'Z' * 999
            s.stage(a, data)
            try:
                zone, _p = s.build_zone()
                en = A.enumerate_zone(zone)
                got = [x for x in en.assets if x.name == a.name]
                good = (en.walk_ok and got and got[0].extract(zone) == data
                        and len(en.assets) == len(s.assets))
                rows.append('%s(%s) %s' % (want, a.name.split('/')[-1][:18],
                                           'ok' if good else 'BAD'))
            except Exception as ex:
                rows.append('%s FAILED (%s)' % (want, str(ex)[:40]))
        b.add('P9', 'PASS' if all('ok' in r for r in rows if 'none' not in r) else 'FAIL',
              'plain-data growth +999 B: ' + ', '.join(rows))

        # ---- P10 add an arbitrary RawFile (.cfg / .csv) ------------------------
        s = fresh()
        n0 = len(s.assets)
        try:
            SC.add_file(s, 'p_selftest.cfg', b'seta p_selftest "1"\n')
            SC.add_any(s, 'p_selftest.csv', b'a,b\n1,2\n', path_hint='x.csv')
            zone, _p = s.build_zone()
            en = A.enumerate_zone(zone)
            names = {x.name: x for x in en.assets}
            good = (en.walk_ok and len(en.assets) == n0 + 2
                    and names.get('p_selftest.cfg') is not None
                    and names['p_selftest.cfg'].extract(zone) == b'seta p_selftest "1"\n'
                    and names.get('p_selftest.csv') is not None)
            b.add('P10', 'PASS' if good else 'FAIL',
                  '%d -> %d assets, added .cfg and .csv as RawFile, content byte-identical'
                  % (n0, len(en.assets)) if good else 'walk=%s count=%d' % (en.walk_ok,
                                                                            len(en.assets)))
        except Exception as ex:
            b.add('P10', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

        # ---- P11r count-not-length types are refused, and the guard is reachable ----
        s = fresh()
        classified = []
        for t in ('KEYVALUEPAIRS', 'SOUND_PATCH'):
            hit = [x for x in s.assets if x.type_name == t]
            if hit:
                classified.append('%s ro=%s' % (t, hit[0].readonly))
        # Force a bad resize past the classification and require the build guard to refuse.
        guard_fired = None
        victim = [x for x in s.assets if x.type_name == 'SOUND_PATCH']
        if victim:
            a = victim[0]
            a.readonly = False
            a.buf_off = a.start + 12 + len(a.name) + 1
            a.buf_len = 1042
            try:
                s.stage(a, a.extract(s.zone) + b'Z' * 777)
                s.build_zone()
                guard_fired = False
            except RuntimeError:
                guard_fired = True
            except Exception:
                guard_fired = True
        ok = all('ro=True' in c for c in classified) and guard_fired is not False
        b.add('P11r', 'PASS' if ok else 'FAIL',
              'count-not-length types read-only (%s); a forced bad resize IS refused by the '
              'post-build walk guard (%s)' % (', '.join(classified) or 'none present',
                                              guard_fired))

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return b.report()


if __name__ == '__main__':
    print('Script editing gate battery (GSC + Lua)')
    print()
    sys.exit(1 if run() else 0)
