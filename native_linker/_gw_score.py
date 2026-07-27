"""CROSS-SPAN error harness for the GfxWorld band (lane: rt_events_gfxworld).

Score = the runtime-error STEP across the GfxWorld span:
  err(a) = model_rt(a) - measured_rt(a)  at dump-measured asset starts
  step   = err(first measured asset AFTER GfxWorld)
         - err(last  measured asset BEFORE GfxWorld)
This is convention-free w.r.t. the per-type linearization bias in
_zmnuked_realmap.pkl ONLY to the extent that the two flanking anchors are of
the same type on both runs -- so we report the SAME anchor pair for every
policy, and additionally a median over the k nearest anchors on each side.
"""
import sys, os, pickle, bisect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loader_sim as LS

ZONE = 'zm_nuked_authored.zone'
REAL = '_zmnuked_realmap.pkl'


def load_real():
    with open(REAL, 'rb') as f:
        r = pickle.load(f)
    return r['real'] if 'real' in r else r


def run(policy, label, verbose=False, k=6):
    em, spans, CO = LS.simulate(ZONE, verbose=verbose, policy=policy)
    real = load_real()
    ks = sorted(em.omap); vs = [em.omap[k2] for k2 in ks]

    def rt_of(sb5):
        j = bisect.bisect_right(ks, sb5) - 1
        return None if j < 0 else vs[j] + (sb5 - ks[j])

    gw = [(s, e) for (i, nm, root, s, e) in spans if root == 'GfxWorld' and e > s]
    gw = gw[0]
    gw_b5 = (gw[0] - 64, gw[1] - 64)
    pts = sorted(real)
    before = [p for p in pts if p < gw_b5[0]]
    after = [p for p in pts if p >= gw_b5[1]]
    errs_b = [(p, rt_of(p) - real[p]) for p in before[-k:]]
    errs_a = [(p, rt_of(p) - real[p]) for p in after[:k]]
    eb = errs_b[-1][1]
    ea = errs_a[0][1]
    print('== %s ==' % label)
    print('  gfx span file 0x%x..0x%x (b5 %d..%d) len=%d'
          % (gw[0], gw[1], gw_b5[0], gw_b5[1], gw[1] - gw[0]))
    print('  before: ' + ' '.join('%d:%+d' % t for t in errs_b))
    print('  after : ' + ' '.join('%d:%+d' % t for t in errs_a))
    print('  STEP across GfxWorld = %+d   (last-before %+d -> first-after %+d)'
          % (ea - eb, eb, ea))
    med_b = sorted(x[1] for x in errs_b)[len(errs_b) // 2]
    med_a = sorted(x[1] for x in errs_a)[len(errs_a) // 2]
    print('  median-step (k=%d) = %+d' % (k, med_a - med_b))
    return ea - eb, spans, em


def band_report(em, spans, label):
    """Regression check: per-root median error over all measured anchors."""
    real = load_real()
    ks = sorted(em.omap); vs = [em.omap[k2] for k2 in ks]

    def rt_of(sb5):
        j = bisect.bisect_right(ks, sb5) - 1
        return None if j < 0 else vs[j] + (sb5 - ks[j])
    per = {}
    for (i, nm, root, s, e) in spans:
        if e <= s:
            continue
        p = s - 64
        if p in real:
            per.setdefault(root, []).append(rt_of(p) - real[p])
    print('-- band medians [%s] --' % label)
    for root in sorted(per):
        v = sorted(per[root])
        print('   %-22s n=%-5d med=%+d' % (root, len(v), v[len(v) // 2]))
    return per


if __name__ == '__main__':
    import rt_events_exact as RTX
    import rt_events_gfxworld as GW
    base = {k: v for k, v in RTX.all_events().items() if k != 'GfxWorld'}
    s0, sp0, em0 = run(dict(extra_events=base, gfx_skip=0),
                       'BEFORE  (GfxWorld = linear interior)')
    band_report(em0, sp0, 'before')
    full = dict(base); full.update(GW.EXTRA)
    s1, sp1, em1 = run(dict(extra_events=full, gfx_skip=0),
                       'AFTER   (GfxWorld = loader-derived allocations)')
    band_report(em1, sp1, 'after')
    print('\nCROSS-SPAN STEP: %+d -> %+d   (delta %+d)' % (s0, s1, s1 - s0))
