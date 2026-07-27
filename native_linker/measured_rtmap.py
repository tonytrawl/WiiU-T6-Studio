"""DUMP-CALIBRATED runtime map for the pass-3 pointer bake (skate boot fix).

The loader-sim's console runtime model is off by an un-derivable ~1.2 MB
(gfx/structural band). A full-memory dump of a boot gives the loader's REAL
per-asset block-5 layout (measured, `_skate_realmap.pkl`). The layout does NOT
depend on our alias values (only on FOLLOW pointers + the loader's allocator),
so baking pointers against the measured layout is correct.

rt(dom): dom = emitted-body block-5 offset (= disk_offset - assets_end, the
co_cursor domain the omap uses). Returns the measured runtime block-5 offset:
real_start(asset) + (dom - asset_body_offset). Assets we couldn't measure
(no unique needle / relocated) fall back to sim + interpolated divergence.

INTERIOR anchors (2026-07-16, skate surfaces[].material root cause): a realmap
may carry an 'interior' dict {stream_b5(interior point) -> runtime b5} measured
inside large assets (backbone + jump bisection + alias-target needles from a
boot dump). Without them rt() is LINEAR inside every asset ("mrs + (dom-mlo)"),
which drifts for any alias into an asset interior whose runtime layout expands
(GfxWorld: +773,276 by the surfaces array — boot-27 measured). With them the
carry restarts at every measured interior point, making rt() piecewise-correct
inside the asset. Raid never uses this class (no override_rtmap) — unaffected.
"""
import bisect, pickle


class MeasuredRuntimeMap:
    def __init__(self, simmap_pkl='_skate_simmap.pkl', realmap_pkl='_skate_realmap.pkl',
                 interior_model=False):
        S = pickle.load(open(simmap_pkl, 'rb'))
        R = pickle.load(open(realmap_pkl, 'rb'))
        self.ae = S['assets_end']
        real = R['real']                     # stream_b5(asset start) -> real rt b5
        # per-asset: (body_off_lo, body_off_hi, real_start or None)
        self.spans = []
        for (i, nm, root, s, e) in S['spans']:
            lo = s - self.ae; hi = e - self.ae   # co_cursor domain
            rs = real.get(s - 64)                # measured real runtime start
            self.spans.append((lo, hi, rs))
        self.spans.sort()
        self._lo = [t[0] for t in self.spans]
        # interior anchors: stream_b5 -> rt b5, converted to the dom domain
        # (dom = stream_b5 - (ae - 64))
        self.interior = [(k - (self.ae - 64), v)
                         for k, v in sorted((R.get('interior') or {}).items())]
        # divergence anchors (measured spans only) for interpolating misses:
        # divergence(body_off) = real_start - sim_rt(body_off_start)
        self.sim = None                      # set by assemble_zone (RuntimeMap)
        self.interior_model = interior_model  # compose anchors with sim interiors
        self.sim_shift = 0                    # dom -> self.sim's key domain
        self._div = None
        self._meas = None; self._meas_lo = None
        self.stats = dict(measured=0, interp=0, simfallback=0)
        # max runtime END across measured assets — the header's block-5 size
        # MUST cover this, else late pointers land out-of-block and the loader
        # resolves them to null (the accessed=0 host-null crash).
        self.max_rt = max((rs + (hi - lo) for (lo, hi, rs) in self.spans
                           if rs is not None), default=0)
        for (lo, rs) in self.interior:
            i = bisect.bisect_right(self._lo, lo) - 1
            hi = self.spans[i][1] if i >= 0 else lo
            self.max_rt = max(self.max_rt, rs + max(0, hi - lo))

    def _build_div(self):
        # divergence table keyed by body_off_lo, from measured spans
        xs, ys = [], []
        for (lo, hi, rs) in self.spans:
            if rs is not None and self.sim is not None:
                xs.append(lo); ys.append(rs - self.sim.rt(lo))
        self._div = (xs, ys)

    def _interp_div(self, dom):
        if self._div is None:
            self._build_div()
        xs, ys = self._div
        if not xs:
            return 0
        j = bisect.bisect_right(xs, dom) - 1
        if j < 0:
            return ys[0]
        if j >= len(xs) - 1:
            return ys[-1]
        # linear interpolation between measured neighbors
        x0, x1 = xs[j], xs[j + 1]; y0, y1 = ys[j], ys[j + 1]
        return y0 + (y1 - y0) * (dom - x0) / (x1 - x0) if x1 > x0 else y0

    def rt(self, dom):
        # carry-forward from the nearest MEASURED anchor at/before dom: exact
        # inside a measured asset, near-exact for a following unmeasured one
        # (divergence is constant until the next inter-asset gap; with 83%
        # coverage those gaps are tiny). Purely measured — no sim, no drift.
        # Interior anchors participate as first-class carry points.
        if self._meas_lo is None:
            self._meas = sorted(dict(
                [(lo, rs) for (lo, hi, rs) in self.spans if rs is not None]
                + self.interior).items())
            self._meas_lo = [t[0] for t in self._meas]
        j = bisect.bisect_right(self._meas_lo, dom) - 1
        if j >= 0:
            mlo, mrs = self._meas[j]
            # is dom inside this measured asset's own span? (exact vs carry)
            i = bisect.bisect_right(self._lo, dom) - 1
            if i >= 0 and self.spans[i][0] == mlo and self.spans[i][2] is not None:
                self.stats['measured'] += 1
            else:
                self.stats['interp'] += 1
            if self.interior_model and self.sim is not None:
                # COMPOSED map (2026-07-26): measured anchors pin each asset's
                # START; the allocation-exact sim (rt_events_*) supplies the
                # INTERIOR delta from that anchor. The plain `dom - mlo` carry
                # below is LINEAR inside the asset — exactly the assumption that
                # mis-targeted every interior alias in boots 6-20 (comma-stub
                # -979, weapon interiors -716, attachment arrays). Requires
                # self.sim to be built with the exact policy; with the linear
                # sim the two lookups cancel back to the old carry (harmless).
                # DOMAIN (sim_shift): this class works in dom = body position =
                # file_off - assets_end. The BUILD's sim (assemble_zone's
                # RuntimeMap over the emitted body stream, B5_BASE=64) is keyed
                # in that SAME domain, so sim_shift stays 0 there. A sim built
                # by simulating the FINAL ZONE FILE is keyed file_off-64 and
                # needs sim_shift = assets_end-64. Getting this wrong samples
                # the sim ~52 KB away and reads as a -7,663 B "model defect"
                # that is nothing of the sort (cost a full debug cycle).
                try:
                    sh = self.sim_shift
                    return mrs + (self.sim.rt(dom + sh) - self.sim.rt(mlo + sh))
                except Exception:
                    pass
            return mrs + (dom - mlo)
        self.stats['simfallback'] += 1
        return self.sim.rt(dom) if self.sim else dom
