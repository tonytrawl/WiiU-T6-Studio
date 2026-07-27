"""zm_nuked measured-rtmap loop (the boot-5 runtime-layout front).

Same 3-step loop as measure_band.py, but wired to the ZM build path
(zm_nuked_build, which applies the ZM deltas: SndBank drop, idx_remap, drops,
BOOT_SAFE_UNRESOLVED, SKINNEDVERTS_MAX, RUNTIME_BAND). The generic
measure_band.cmd_simmap/cmd_rebake call author_zone WITHOUT mode='zm', so they
can't be used directly. The measure step (measure_band.cmd_measure) is
zone-agnostic and is reused verbatim.

  simmap : describe the DEPLOYED/DUMPED zone's true body layout (loader walk of
           zm_nuked_authored.zone == the exact bytes that produced the boot-5
           dump) -> _zmnuked_simmap.pkl (+ _zmnuked_authored.zone needle copy).
           spans/assets_end are the same file-offset domain cmd_simmap emits;
           MeasuredRuntimeMap consumes them identically.
  measure: measure_band.cmd_measure(dump, '_zmnuked', window_mb) -> _zmnuked_realmap.pkl
  rebake : zm_nuked_build(override_rtmap=MeasuredRuntimeMap(...)) -> pointers
           re-encoded against the MEASURED layout; block5 floored to max_rt.

Usage:
  python _zmnuked_measure.py simmap
  python _zmnuked_measure.py measure <dump.dmp> [window_mb=210]
  python _zmnuked_measure.py rebake
"""
import os, sys, struct, pickle, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PREFIX = '_zmnuked'
ZONE = 'zm_nuked_authored.zone'
DEPLOYED_MD5 = '15c19f5b49fa0799c1d97637f902436a'   # boot-5 build (the dumped zone)


def cmd_simmap():
    import loader_sim as LS
    Z = open(ZONE, 'rb').read()
    md5 = hashlib.md5(Z).hexdigest()
    if md5 != DEPLOYED_MD5:
        raise SystemExit('%s md5 %s != deployed %s — this is NOT the dumped '
                         'zone; measurement would be against the wrong bytes.'
                         % (ZONE, md5, DEPLOYED_MD5))
    em, spans, _ = LS.simulate(Z, verbose=False, policy=dict(gfx_skip=0))
    bodies = [(i, nm, root, s, e) for (i, nm, root, s, e) in spans if e > s]
    ae = min(s for (i, nm, root, s, e) in bodies)
    mx = max(e for (i, nm, root, s, e) in bodies)
    assert mx == len(Z), 'walk not EOF-exact (%d != %d)' % (mx, len(Z))
    # contiguity (each body starts where the previous ended)
    srt = sorted(bodies, key=lambda t: t[3])
    assert all(a[4] == b[3] for a, b in zip(srt, srt[1:])), 'body gaps'
    pickle.dump(dict(assets_end=ae, spans=bodies, rt_keys=[], rt_vals=[]),
                open(PREFIX + '_simmap.pkl', 'wb'))
    open(PREFIX + '_authored.zone', 'wb').write(Z)   # needle-match copy
    print('simmap: %d body spans, ae=%d (%#x), md5 OK -> %s_simmap.pkl + '
          '%s_authored.zone' % (len(bodies), ae, ae, PREFIX, PREFIX))


def cmd_rebake(exact=False):
    """exact=True -> COMPOSED map: dump-measured anchors pin each asset START,
    the allocation-exact interior model (rt_events_*) supplies every offset
    INSIDE the asset. The default linear carry is what mis-targeted the
    comma-stub (-979) and every weapon-interior alias (+716) across boots 6-20.
    Both halves must be enabled together: exact_rt=True makes the build's own
    sim allocation-exact (that sim is what MeasuredRuntimeMap composes with)."""
    import produce_container as PC
    from measured_rtmap import MeasuredRuntimeMap
    ov = MeasuredRuntimeMap(PREFIX + '_simmap.pkl', PREFIX + '_realmap.pkl',
                            interior_model=exact)
    print('measured rtmap: %d spans, max_rt=%d (%.1f MB)%s'
          % (len(ov.spans), ov.max_rt, ov.max_rt / 1e6,
             '  [COMPOSED: exact interiors]' if exact else '  [linear interiors]'))
    zone, info = PC.zm_nuked_build(override_rtmap=ov, exact_rt=exact)
    b5 = struct.unpack_from('>8I', zone, 8)[5]
    print('rebake: stats=%s  block5=%d (%.1f MB, max_rt %.1f MB)'
          % (ov.stats, b5, b5 / 1e6, ov.max_rt / 1e6))
    print('wrote zm_nuked_authored.zone (%d bytes, md5 %s)'
          % (len(zone), hashlib.md5(zone).hexdigest()))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'simmap':
        cmd_simmap()
    elif cmd == 'measure':
        import measure_band
        measure_band.cmd_measure(sys.argv[2], PREFIX,
                                 float(sys.argv[3]) if len(sys.argv) > 3 else 210)
    elif cmd == 'rebake':
        cmd_rebake(exact=('--exact' in sys.argv))
    else:
        raise SystemExit(__doc__)
