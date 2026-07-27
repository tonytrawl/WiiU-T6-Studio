"""Runtime-model calibration harness (pipeline hardening, 2026-07-26).

GOAL: make loader_sim's runtime model reproduce the console loader's REAL
block-5 layout bit-exactly, so every dedup/back-ref alias bakes correctly at
build time and maps convert WITHOUT boot dumps.

GROUND TRUTH (already on disk, no new boots needed):
  * _zmnuked_realmap.pkl: 2,815 dump-measured asset runtime starts (boot-5)
  * golden interiors measured during the boot 6-20 campaign:
      file 0x283a8b (XModel#35 comma-stub texdef slot)   -> rt 0x28E054
      defaultweapon attachments[63] array                -> rt 0x77D18F4
      defaultweapon attachmentUniques[95] array          -> rt 0x77D19F0
      defaultweapon inline weapDef                       -> rt 0x77D0BD8

KNOB UNDER TEST: interior allocation alignment. SimEmitter.emit_body clamps
struct alignment to 4 (`min(align,4)`) and emit_array clamps to elem-size<=4.
The skate campaign measured real runtime pads of 0..31 => the loader aligns
some allocations to 8/16/32. Variants:
  clamp4   : current behaviour (baseline)
  clamp8/16/32 : raise the clamp
  true     : use the struct's natural alignment un-clamped; arrays align to
             min(elem_size, cap)

Score = per-measured-asset error distribution (#exact / p50 / p95 / max).
"""
import sys, os, pickle, struct, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loader_sim as LS

ZONE = 'zm_nuked_authored.zone'
GOLD = [
    # (label, file_off, real_rt)  -- slot/array/struct positions
    ('comma-slot', 0x283a8b, 0x28E054),
    ('dw-att63',   None,     0x77D18F4),   # located via file scan below
    ('dw-uniq95',  None,     0x77D19F0),
    ('dw-weapdef', None,     0x77D0BD8),
]

R = pickle.load(open('_zmnuked_realmap.pkl', 'rb'))
S = pickle.load(open('_zmnuked_simmap.pkl', 'rb'))
real = R['real']                          # stream_b5(file-64) -> real rt
ae = S['assets_end']

Z = open(ZONE, 'rb').read()

# locate defaultweapon_mp inline positions (file): variant span -> name ->
# weapDef -> ... arrays follow the weapDef dynamics; we only need FILE offsets
# for golden rows we can pin exactly: weapDef = name_end+1; arrays located by
# 63/95 zero-word runs directly after the (huge) weapDef dynamics -- instead
# pin them from the boot-12 runtime deltas (name_g +0xD2E/+0xE2A) which were
# verified against the dump: file offsets carry the SAME deltas from the name
# only if no pads intervene, which is exactly what we are calibrating -- so
# SKIP file pinning for the arrays and evaluate them as rt-only checks against
# the model's rt at the comma slot + known-good file anchors instead.

def run(policy_label, body_clamp, arr_clamp):
    orig_body = LS.SimEmitter.emit_body
    orig_arr = LS.SimEmitter.emit_array
    def emit_body(self, struct_name, src_file):
        s = self.wk.gs(struct_name)
        a = s.get('align', 4) or 4
        self._alloc_align(min(a, body_clamp))
        return LS.BR.ReEmitter.emit_body(self, struct_name, src_file)
    def emit_array(self, base, count):
        if base not in self.L.structs:
            sz, _ = self.L._resolve(base)
            self._alloc_align(min(sz, arr_clamp))
        return LS.BR.ReEmitter.emit_array(self, base, count)
    LS.SimEmitter.emit_body = emit_body
    LS.SimEmitter.emit_array = emit_array
    try:
        em, spans, _ = LS.simulate(ZONE, verbose=False, policy=dict(gfx_skip=0))
    finally:
        LS.SimEmitter.emit_body = orig_body
        LS.SimEmitter.emit_array = orig_arr
    # omap: stream b5 -> rt. keys/vals sorted
    keys = em.omap_keys if hasattr(em, 'omap_keys') else sorted(em.omap)
    omap = em.omap
    ks = sorted(omap)
    vs = [omap[k] for k in ks]
    def rt_of(stream_b5):
        j = bisect.bisect_right(ks, stream_b5) - 1
        if j < 0: return None
        return vs[j] + (stream_b5 - ks[j])
    errs = []
    n_exact = 0
    for sb5, rrt in real.items():
        m = rt_of(sb5)
        if m is None: continue
        e = m - rrt
        errs.append(e)
        if e == 0: n_exact += 1
    errs.sort()
    n = len(errs)
    comma = rt_of(0x283a8b - 64)
    print('%-8s: %d measured, exact %5d (%.1f%%), p50 %+d, p95 %+d, max|e| %d, comma-slot %s (want 0x28E054, err %s)'
          % (policy_label, n, n_exact, 100.0*n_exact/max(n,1),
             errs[n//2], errs[int(n*0.95)], max(abs(errs[0]), abs(errs[-1])),
             hex(comma) if comma else None,
             hex(comma - 0x28E054) if comma else None))

for label, bc, ac in [('clamp4', 4, 4), ('clamp8', 8, 8), ('clamp16', 16, 16), ('clamp32', 32, 32), ('true128', 128, 128)]:
    run(label, bc, ac)
