#!/usr/bin/env python3
"""(GM) GLASSES EVENT MODEL — landed 2026-08-16, the fix for the 24,576 step.

SPEC_glasses_event_model_rebake.md §3, wired at last. The probe
(`_glasses_events_probe.py`) was validated in 2026-08-10 and then sat unarmed
because arming it was rebake-scoped. It is armed now because the b88 crash proved
the cost of leaving it off.

THE DEFECT IT FIXES. loader_sim modelled a Glasses span as a LINEAR copy, so
inline Material/GfxImage roots were charged to block 5 (they belong in TEMP) and
the loader's 0x2000 PIXEL PADS were not charged at all. Everything downstream of a
Glasses asset inherited the error.

MEASURED ON zm_nuked AGAINST REAL GUEST MEMORY (b88 dump, 519 pointer-free probes
compared byte-for-byte at candidate offsets -- an oracle the float phantom cannot
touch, unlike the range-scan that got `_gfx_bisect` refuted):

    file < ~116.3M   sim rt EXACT            261 probes
    file > ~120.2M   sim rt 24,576 bytes LOW  93 probes

and the step was localised to the Glasses span itself:
    GLASSES start   D=0       the dump shows "glasses(n=1)" exactly where the sim says
    CLIPMAP start   D=+24576  the sim's address holds zeros; the real content is 24 KB on

⇒ every alias minted from a sim rt past the Glasses span resolved 24 KB early.
That is precisely what crashed b88: WeaponDef+916 landed on the cstring
"viewmodel_default_idle" instead of the all-zero XModel*[16] it named, and
XModelGetName dereferenced "view" = 0x76696577.

⛔⛔ WHY EVERY GATE STILL PASSED — (FN) ONE LEVEL UP. Every gate checked that our
handles resolve to the right object IN loader_sim's FRAME. loader_sim's frame was
the defect. We validated the producer against a model and the model was wrong;
only real guest memory could see it. When a whole campaign's gates agree, ask what
they all share.

⭐ RULE (BZ) EARNED ITS KEEP: D was a STEP, not a residual. Adding 24,576 at the
mint would have papered over a missing allocation and broken the moment a zone's
asset mix changed. The deliverable was the structure at the boundary.

USAGE (opt in per map -- OFF by default, raid/skate are PARKED on measured
constants and must not silently change model):
    import rt_events_exact as RTE, glasses_events as GE
    pol = RTE.policy(glasses_events=GE.events_for_loader_sim)
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'wiiu_ref'))

GLASS_ROOT = 56


def events_for_loader_sim(d, off, e='>'):
    """loader_sim's CONSOLE_EVENTS contract is (end, events).

    ⚠ The validated probe returns (events, end) -- the OPPOSITE order. Adapting it
    here rather than editing the probe keeps the validated generator untouched and
    keeps the adapter's one job visible; silently swapping a tuple at the call site
    is how a 'wired' model ends up walking the wrong span.
    """
    import _glasses_events_probe as P
    events, end = P.glasses_events(d, off, e)
    return end, events


def selftest(zone='zm_nuked_authored_b88.zone'):
    """Both arms: the span must close EXACTLY on the delimiter that defines it
    (file consumption cannot drift), and the model must charge MORE block-5 than
    the linear path it replaces (that extra is the whole point)."""
    import struct
    import zone_walk, raid_oracle_control as RC
    import _glasses_events_probe as P

    Z = open(zone, 'rb').read()
    w = zone_walk.walk(Z)
    spans = [s for s in sorted(w.spans) if s[2] in ('GLASSES', 'MAP_ENTS')]
    ok = fail = 0
    for (a, b, kind) in [(s[0], s[1], s[2]) for s in spans]:
        if not RC._looks_like_glasses(Z, a):
            continue
        delim_end = RC._console_glasses_end(Z, a)
        end, evts = events_for_loader_sim(Z, a)
        if end == delim_end:
            ok += 1
            print('   PASS file closure: walker end %d == delimiter end %d'
                  % (end, delim_end))
        else:
            fail += 1
            print('   FAIL file closure: walker %d != delimiter %d (drift %+d)'
                  % (end, delim_end, end - delim_end))
        b5 = P.block5(evts) if hasattr(P, 'block5') else None
        if isinstance(b5, tuple):
            b5 = b5[0]
        linear = delim_end - a - GLASS_ROOT
        if b5 is not None:
            print('      block-5 charged %d vs linear %d  => %+d'
                  % (b5, linear, b5 - linear))
            if b5 > linear:
                ok += 1
                print('      PASS: the event model charges MORE than the linear '
                      'path (the pixel pads are now accounted)')
            else:
                fail += 1
                print('      FAIL: no extra charge -- the pads are still missing')
    if not (ok or fail):
        print('   ⛔ NO GLASSES ASSET IN %s -- that is a SKIP, not a PASS.' % zone)
        return False
    print('   selftest: %d pass, %d FAIL' % (ok, fail))
    return fail == 0


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    sys.exit(__doc__)
