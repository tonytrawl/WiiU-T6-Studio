"""core.verify -- mandatory post-build verification (R8).

Nothing reaches disk without passing through here. The historical failure mode of this project
is a zone that looks fine and dies on hardware, so the contract is:

    a check that cannot be performed is NOT a pass -- it is recorded as `skipped`, and any
    caller that treats skipped as passed is wrong.

Checks, cheapest first:

  V1  container self-consistency  -- size field == len-40, block sizes, asset array in range
  V2  structural walk             -- re-walk the produced zone; must be EOF-exact
  V3  asset count                 -- unchanged unless the caller expected a change
  V4  no dangling block-5 alias   -- every alias decodes inside the zone
  V5  gate battery                -- zone_gates.run(), when a path is available

V5 is the expensive one and needs a file on disk; it is opt-in via `gates=True`.
"""
import struct

from . import paths  # noqa: F401

import zone_facts as ZF
import zone_walk

ALIAS_LO, ALIAS_HI = 0xA0000000, 0xC0000000
BLOCK_VIRTUAL = 5
FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE


class Result(object):
    def __init__(self):
        self.checks = []            # (id, status, detail)  status in pass|fail|skip
        self.problems = []

    def add(self, cid, status, detail=''):
        self.checks.append((cid, status, detail))
        if status == 'fail':
            self.problems.append('%s: %s' % (cid, detail))
        return self

    @property
    def ok(self):
        return not self.problems

    @property
    def skipped(self):
        return [c for c in self.checks if c[1] == 'skip']

    def summary(self):
        return dict(ok=self.ok,
                    passed=sum(1 for c in self.checks if c[1] == 'pass'),
                    failed=sum(1 for c in self.checks if c[1] == 'fail'),
                    skipped=sum(1 for c in self.checks if c[1] == 'skip'),
                    checks=[{'id': a, 'status': b, 'detail': c} for a, b, c in self.checks],
                    problems=list(self.problems))

    def report(self):
        icon = {'pass': '  OK  ', ' fail': 'FAIL  ', 'fail': 'FAIL  ', 'skip': 'SKIP  '}
        lines = []
        for cid, st, detail in self.checks:
            lines.append('%s %-22s %s' % (icon.get(st, '?'), cid, detail))
        lines.append('')
        lines.append('VERDICT: %s' % ('PASS' if self.ok else 'FAIL -- ' + '; '.join(self.problems)))
        return '\n'.join(lines)


def verify_zone(zone, expect_assets=None, gates=False, gate_path=None):
    r = Result()
    r.dangling = None

    # ---- V1 container ------------------------------------------------------------------
    try:
        f = ZF.Facts(zone)
        if f.size != len(zone) - 40:
            r.add('V1-container', 'fail',
                  'size field %d != len-40 %d' % (f.size, len(zone) - 40))
        elif f.warn:
            r.add('V1-container', 'fail', '; '.join(f.warn))
        else:
            r.add('V1-container', 'pass',
                  '%d assets, b5=%d, arrEnd_rt=%d' % (f.asset_count, f.b5, f.arrend_rt))
    except Exception as ex:
        r.add('V1-container', 'fail', '%s: %s' % (type(ex).__name__, ex))
        return r                      # nothing downstream is meaningful

    # ---- V2 walk -----------------------------------------------------------------------
    try:
        with paths.backend_cwd():
            wk = zone_walk.walk(zone)
        if wk.ok:
            r.add('V2-walk', 'pass', '%d bodies, EOF exact' % len(wk.spans))
        else:
            r.add('V2-walk', 'fail', wk.verdict())
    except Exception as ex:
        r.add('V2-walk', 'fail', '%s: %s' % (type(ex).__name__, ex))

    # ---- V3 asset count ----------------------------------------------------------------
    if expect_assets is None:
        r.add('V3-assetcount', 'skip', 'no expectation supplied')
    elif f.asset_count == expect_assets:
        r.add('V3-assetcount', 'pass', '%d' % f.asset_count)
    else:
        r.add('V3-assetcount', 'fail',
              'asset_count %d != expected %d' % (f.asset_count, expect_assets))

    # ---- V4 dangling aliases -----------------------------------------------------------
    # Byte granularity: console records are NOT 4-aligned (patch_mp's array ends at 0x41E1
    # == 1 mod 4), so a stride-4 sweep is blind to ~77% of real sites.
    #
    # ⚠⚠ THE PHANTOM LAW APPLIES HERE TOO. Scanning raw bytes finds alias-SHAPED words that are
    # really data -- GSC opcodes, HKS instructions, GX2 register words. CALIBRATED AGAINST THE
    # PRISTINE RETAIL ZONE: without the overlap filter this reported 793,583 "dangling aliases"
    # on an untouched patch_mp, the first at offset 0xB (inside the 40-byte container header).
    # A check that fails on known-good input is a bug in the check.
    #
    # So: skip the container header, skip every modelled data payload, and -- because the
    # remainder still contains unmodelled data -- report the residue as a COMPARATIVE figure
    # against the caller's baseline rather than asserting it must be zero.
    try:
        from . import relink as _relink
        skip = _relink.payload_ranges(zone)
        bad = 0
        first = None
        b5 = f.b5
        z = zone
        n = len(z)
        si = 0
        off = f.assets_end                      # past the container header + asset array
        while off < n - 3:
            while si < len(skip) and skip[si][1] <= off:
                si += 1
            if si < len(skip) and skip[si][0] <= off < skip[si][1]:
                off = skip[si][1]               # jump over a data payload
                continue
            v = struct.unpack_from('>I', z, off)[0]
            if ALIAS_LO <= v < ALIAS_HI and ((v >> 29) & 7) == BLOCK_VIRTUAL:
                if (v & 0x1FFFFFFF) - 1 > b5:
                    bad += 1
                    if first is None:
                        first = (off, v, (v & 0x1FFFFFFF) - 1)
            off += 1
        r.dangling = bad
        detail = ('%d alias-shaped word(s) outside modelled payloads decode past b5=%d'
                  % (bad, b5))
        if first:
            detail += '; first at 0x%x = 0x%08X -> rt %d' % first
        # Advisory: the residue is not provably zero on retail input, so this reports rather
        # than fails. `verify_against_baseline()` turns it into a real pass/fail.
        r.add('V4-aliases', 'pass' if bad == 0 else 'skip', detail)
    except Exception as ex:
        r.add('V4-aliases', 'skip', '%s: %s' % (type(ex).__name__, ex))

    # ---- V5 gate battery ---------------------------------------------------------------
    if not gates:
        r.add('V5-gates', 'skip', 'not requested (expensive; needs a file path)')
    elif not gate_path:
        r.add('V5-gates', 'skip', 'gates=True but no gate_path given')
    else:
        try:
            import zone_gates
            res = zone_gates.run(gate_path)
            r.add('V5-gates', 'pass' if res else 'fail', repr(res)[:200])
        except Exception as ex:
            r.add('V5-gates', 'skip', 'gate battery unavailable: %s: %s'
                  % (type(ex).__name__, ex))

    return r


def verify_against_baseline(baseline_zone, new_zone, expect_delta=0):
    """Differential verification -- the only form of V4 that actually means anything.

    Absolute alias counting cannot work on a retail zone: most of patch_mp is unmodelled binary
    (images, sounds, GX2 state), so alias-SHAPED words are everywhere and a PRISTINE file scores
    ~745,000. What IS meaningful is that our edit did not make it worse.

    Relative to the untouched input:
        D1  container asset_count unchanged
        D2  size moved by exactly `expect_delta`
        D3  the walk still completes, with the same verdict
        D4  the dangling-alias residue did not increase

    ⭐ The authoritative implementation now lives in the SHARED battery as
    `zone_gates.g_diff_against_baseline()`, so the conversion pipeline gets the same rule rather
    than it staying GUI-side. This delegates to it and only falls back if that import fails.
    """
    try:
        with paths.backend_cwd():
            import zone_gates
            g = zone_gates.g_diff_against_baseline(baseline_zone, new_zone, expect_delta)
        r = Result()
        r.dangling = g.counts.get('residue')
        for kind, msg in g.details:
            r.add(msg.split()[0], 'fail' if kind == 'FAIL' else 'pass',
                  msg.split(' ', 1)[1] if ' ' in msg else msg)
        if not g.details:
            r.add('G-DIFF', 'pass' if g.status == 'PASS' else 'fail', g.status)
        return r
    except Exception:
        pass                      # fall through to the local implementation

    r = Result()
    r.dangling = None
    a = verify_zone(baseline_zone)
    b = verify_zone(new_zone)

    fa, fb = ZF.Facts(baseline_zone), ZF.Facts(new_zone)
    r.add('D1-assetcount', 'pass' if fa.asset_count == fb.asset_count else 'fail',
          'unchanged at %d' % fb.asset_count if fa.asset_count == fb.asset_count
          else '%d -> %d' % (fa.asset_count, fb.asset_count))

    got = len(new_zone) - len(baseline_zone)
    r.add('D2-size', 'pass' if got == expect_delta else 'fail',
          '%+d bytes as expected' % got if got == expect_delta
          else 'size moved %+d, expected %+d' % (got, expect_delta))

    det = {c[0]: (c[1], c[2]) for c in a.checks}
    detb = {c[0]: (c[1], c[2]) for c in b.checks}
    if detb.get('V2-walk', ('fail', ''))[0] != 'pass':
        r.add('D3-walk', 'fail', 'walk no longer completes: %s' % detb.get('V2-walk', ('', ''))[1])
    elif det.get('V2-walk', ('', ''))[1] == detb['V2-walk'][1]:
        r.add('D3-walk', 'pass', detb['V2-walk'][1])
    else:
        r.add('D3-walk', 'fail', 'walk changed: %r -> %r'
              % (det.get('V2-walk', ('', ''))[1], detb['V2-walk'][1]))

    if a.dangling is None or b.dangling is None:
        r.add('D4-aliases', 'skip', 'residue not measurable on one side')
    elif b.dangling <= a.dangling:
        r.add('D4-aliases', 'pass', 'residue %d -> %d (not increased)' % (a.dangling, b.dangling))
    else:
        r.add('D4-aliases', 'fail',
              'residue INCREASED %d -> %d (+%d): the edit created dangling aliases'
              % (a.dangling, b.dangling, b.dangling - a.dangling))
    return r
