"""ACCEPTANCE TEST for the allocation-exact runtime model (dump-free goal).

The bar is not "smaller average error" — it is: does a sim with NO dump input
predict the exact runtime addresses that the boot 6-20 campaign measured? Those
four points are where the crashes actually were, so they are the operative test.

GOLDEN POINTS (dump-measured during the campaign; all four zone builds are
layout-identical — every campaign patch was value-only under a rewalk gate, so
file offsets and spans are comparable across them):
  comma-stub texdef slot  file 0x283a8b -> rt 0x28E054   (boot-6  dump 38716)
  defaultweapon weapDef   (inline)      -> rt 0x77D0BD8  (boot-19 dump 11188)
  attachments[63] array   (inline)      -> rt 0x77D18F4  (boot-12 dump 17068)
  attachmentUniques[95]                 -> rt 0x77D19F0  (boot-12 dump 17068)

The last three are located structurally (defaultweapon_mp's body), not by a
hardcoded file offset, so this test survives a zone rebuild.

Usage:  python _rt_acceptance.py [zone]
"""
import sys, struct, bisect

sys.path.insert(0, '.')
import loader_sim as LS
import rt_events_exact as RTX

FOLLOW = 0xFFFFFFFF
ZONE = sys.argv[1] if len(sys.argv) > 1 else 'zm_nuked_authored.zone'
GOLD_COMMA_FILE = 0x283a8b
GOLD = {'comma-slot': 0x28E054, 'weapDef': 0x77D0BD8,
        'attachments[63]': 0x77D18F4, 'attachmentUniques[95]': 0x77D19F0}


_KEYS = None            # set by run(): the sim's registered allocation offsets


def sites(Z, spans, keys=None):
    """Locate the four golden sites by structure -> {label: file_offset}."""
    global _KEYS
    _KEYS = keys if keys is not None else _KEYS
    out = {'comma-slot': GOLD_COMMA_FILE}
    for (i, nm, root, s, e) in spans:
        if root != 'WeaponVariantDef' or e <= s:
            continue
        ne = Z.index(b'\x00', s + 716)
        if Z[s + 716:ne] != b'defaultweapon_mp':
            continue
        wd = ne + 1                                  # inline weapDef body
        out['weapDef'] = wd
        # the two ptr arrays: 63 then 95 consecutive ptr-space words after the
        # weapDef dynamics. Locate the 63-run whose successor run is 95 long.
        p = wd
        end = e - (63 + 95) * 4
        while p < end:
            ok = all(struct.unpack_from('>I', Z, p + k * 4)[0] in (0, FOLLOW)
                     or (struct.unpack_from('>I', Z, p + k * 4)[0] >> 28) == 0xA
                     for k in range(63 + 95))
            if ok and struct.unpack_from('>I', Z, p - 4)[0] not in (0, FOLLOW):
                # D7 (2026-07-26): a byte-granular scan can start INSIDE the
                # preceding string's NUL (the arrays follow szXAnims-class
                # strings), landing 1-3 bytes early. One byte early is fatal to
                # the lookup: it falls back to the PREVIOUS registered
                # allocation and interpolates linearly across the inline
                # sub-asset roots between them (those consume file bytes but no
                # virtual space), reading ~7.6 KB high. Snap forward to the real
                # allocation boundary — the caller passes the registered
                # allocation keys, and the true array start is always one.
                for d in range(0, 4):
                    if _KEYS is None or (p + d - 64) in _KEYS:
                        p += d
                        break
                out['attachments[63]'] = p
                out['attachmentUniques[95]'] = p + 63 * 4
                break
            p += 1
        break
    return out


def run(policy, label):
    em, spans, _ = LS.simulate(ZONE, verbose=False, policy=policy)
    ks = sorted(em.omap); vs = [em.omap[k] for k in ks]

    def rt_of(file_off):
        sb5 = file_off - 64
        j = bisect.bisect_right(ks, sb5) - 1
        return None if j < 0 else vs[j] + (sb5 - ks[j])

    Z = open(ZONE, 'rb').read()
    st = sites(Z, spans, keys=set(ks))
    print('== %s ==' % label)
    n_ok = 0
    for k in ('comma-slot', 'weapDef', 'attachments[63]', 'attachmentUniques[95]'):
        fo = st.get(k)
        if fo is None:
            print('  %-22s SITE NOT FOUND' % k); continue
        got = rt_of(fo)
        err = None if got is None else got - GOLD[k]
        n_ok += (err == 0)
        print('  %-22s file %#x  predicted %s  measured %#x  err %s'
              % (k, fo, hex(got) if got else None, GOLD[k],
                 ('%+d' % err) if err is not None else '?'))
    print('  EXACT: %d/4' % n_ok)
    return n_ok


def run_composed(label='composed (measured anchors + exact interiors)'):
    """The map the BUILD uses: measured anchors pin asset starts, the exact
    model supplies interiors. sim_shift is set explicitly because this harness
    simulates the FINAL ZONE FILE (keys file-64) while the class works in body
    offsets; the build's own sim is already in body offsets (shift 0)."""
    import pickle
    from measured_rtmap import MeasuredRuntimeMap
    S = pickle.load(open('_zmnuked_simmap.pkl', 'rb')); ae = S['assets_end']
    anch = RTX.anchor_rt('_zmnuked_simmap.pkl', '_zmnuked_realmap.pkl')
    em, spans, _ = LS.simulate(ZONE, verbose=False,
                               policy=RTX.policy(gfx_skip=0, anchor_rt=anch))
    Z = open(ZONE, 'rb').read()
    st = sites(Z, spans, keys=set(em.omap))
    ov = MeasuredRuntimeMap('_zmnuked_simmap.pkl', '_zmnuked_realmap.pkl',
                            interior_model=True)
    ov.sim = LS.RuntimeMap(em.omap)
    ov.sim_shift = ae - 64
    print('== %s ==' % label)
    n_ok = 0
    for k in ('comma-slot', 'weapDef', 'attachments[63]', 'attachmentUniques[95]'):
        err = int(ov.rt(st[k] - ae)) - GOLD[k]
        n_ok += (err == 0)
        print('  %-22s err %+d' % (k, err))
    print('  EXACT: %d/4' % n_ok)
    return n_ok


if __name__ == '__main__':
    run(dict(gfx_skip=0), 'baseline (linear interior)')
    run(RTX.policy(verbose=True, gfx_skip=0), 'allocation-exact (dump-free)')
    run_composed()
