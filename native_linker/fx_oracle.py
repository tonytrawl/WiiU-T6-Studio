#!/usr/bin/env python3
"""fx_oracle — durable FX-alias oracle scorer + regression gate (registry campaign
Stage 2, baked 2026-07-30).

WHAT THIS MEASURES
==================
For every Material-class FX `visuals` alias in a GENUINE console zone (walk parity
with zone_gates._walk_fx_slots, INCLUDING visuals-array entries and decal
markArray entries), test whether the CURRENT rt model (loader_sim +
rt_events_exact.policy(), the 10-band registry) synthesizes the live handle
byte-exactly:

    live_word == 0xA0000000 + rt(holder_cell) + 1

for some Material occurrence holder cell. Holder census (the transit_v2 shape,
the campaign's widest): FX single/array/mark FOLLOW cells, thermal words (+96)
in inline bodies (recursively), XModel materialHandles entries (top-level AND
FX-inline et7 models), and the asset-array entry ptr word of each bodied
top-level Material (rule AX 4-align shift domain).

MEASURED SEMANTICS (registry campaign Stage 1, HANDOFF_pipeline_bake_rules.md
"THE FX-ALIAS ORACLE RE-MEASURED" .. "RAID GATE RE-BASELINED"):
  * BACKWARDNESS IS JUDGED ON THE HOLDER CELL, NOT THE INLINE BODY — the seagull
    structural-backwardness law (rules AY-BF), measured in the oracle on transit
    (+12 true hits). A holder cell that PRECEDES the alias slot is a legal
    backward target even when its inline body streams after (e.g. a top-level
    Material's asset-array entry cell near the zone start).
  * THE FORWARD-FIRST-INSERT CLASS COUNTS AS EXACT, NOT A MISS — live ==
    synthesized handle of the FIRST occurrence of the material, whose holder
    cell sits FORWARD of the alias slot (G5's genuine-forward class: "the MODEL
    is right and the ORACLE's backwardness bookkeeping mislabels them").
    Reported as its own count (`ffi`); exact_total = backward + ffi.
    ⚠ MEASURED CORRECTION (2026-07-30, this module's first run): the Stage-1
    note "forward-first-inserts dockside x9 ... closing (2) alone puts dockside
    at 326/326" is REFUTED. The true ffi population is raid x5, dockside x5
    (the BZ-scope note's "5 genuine forward first-inserts"), transit x2.
    Dockside's 9 residual misses are a DIFFERENT class: every one is
    live == synth+4 targeting the 729746..730330 window (the zone's FIRST FX
    bodies, comma-extern material region) — a +4 model drift, not a
    bookkeeping mislabel. Nameable follow-up, same family as the dockside
    Glasses +8192 blockSize[5] residual.
  * (BZ) scope: none of the three baseline zones re-inlines a material
    (first-occurrence == most-recent on every backward alias by construction),
    so first-vs-recent is VACUOUS here; the re-inline count is reported so the
    vacuousness stays visible. Handle-equality matching subsumes both.

CONTROLS (both from the Stage-1 battery):
  * ±4 B identity-constrained sharpness: at shift ±4, count raw matches and
    matches that share the SAME material identity as the 0-shift match. Sharp
    model ⇒ identity-constrained count 0 (a ±4 match onto the same intent would
    mean the model is only fuzzily right).
  * blockSize[5] residual: header blockSize[5] vs the sim's final virtual
    cursor. Stage-1 landmarks: raid 0 EXACT (the J-3 oracle satisfied
    byte-for-byte), dockside +8192 (the Glasses 3-level band, identified
    follow-up), transit -24576 (the known >78M tail).

BASELINE / GATE
===============
`_fx_oracle_baseline_2026-07-30.txt` (next to this file) records the Stage-1
regression-battery scores. Running with no arguments scores the three baseline
zones (one subprocess each, the zone_walk_survey pattern) and exits NONZERO if
any zone's exact_total drops below baseline, if a denominator changes (scores
would be incomparable), or if a |blockSize[5] residual| grows.

    python native_linker/fx_oracle.py                  # gate vs baseline
    python native_linker/fx_oracle.py --one <zone>     # single zone, JSON out
    python native_linker/fx_oracle.py --write-baseline # re-baseline (deliberate)

Ported from the Stage-1 measurement scripts (scratchpad fx_oracle_raid*.py /
fx_oracle_dockside3.py / fx_oracle_transit_v2.py / fx_sim_dockside.py),
verified against their recorded numbers: raid 227/242 (222 back + 5 ffi),
dockside 317/326 (312 back + 5 ffi), transit 749/750 on the full walk
population (747 back + 2 ffi; the Stage-1 "580/583" was the P0 +196-parity
subset — 580 back + 2 ffi + 1 tail miss = the same residuals).
"""
import argparse
import bisect
import json
import os
import struct
import subprocess
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.abspath(os.path.join(HERE, '..'))
WR = os.path.join(BASE, 'wiiu_ref')
BASELINE_PATH = os.path.join(HERE, '_fx_oracle_baseline_2026-07-30.txt')

# The three baseline zones of the Stage-1 battery. All genuine artifacts
# (mp_raid_genuine is Treyarch output; dockside/transit are the DLC/ZM refs).
ZONES = [
    ('raid', os.path.join(WR, 'mp_raid_genuine.zone')),
    ('dockside', os.path.join(WR, 'mp_dockside_wiiu.zone')),
    ('transit', os.path.join(WR, 'zm_transit_original.zone')),
]

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def _is_alias(v):
    return 0xA0000000 <= v < 0xC0000000


# =====================================================================
# per-zone scorer (in-process)
# =====================================================================
def collect_zone(zone_path):
    """Walk one console zone and return the raw census the scorer runs on.

    Returns a dict with the zone bytes, the rt/enc closures, the Material
    OCCurrence list and the FX alias SITE list. Split out of score_zone
    (2026-08-08) so the FIX 27 re-minter can reuse the IDENTICAL census on
    OUR OWN output — the walk is the registry, and a second copy of it would
    be the fifth bespoke corrector this project has grown.
    Writes nothing.
    """
    sys.path.insert(0, HERE)
    sys.path.insert(0, WR)
    os.chdir(HERE)                       # rt_events_* / corpus relative paths
    import loader_sim as LS
    import rt_events_exact as RTX
    import zone_stream as zs
    import fx_probe as FP
    import xmodel_probe as XP
    import wiiu_zone

    Z = open(zone_path, 'rb').read()
    t0 = time.time()
    em, spans, CO = LS.simulate(Z, verbose=False, policy=RTX.policy())
    rc = wiiu_zone.ZoneReader(CO)
    rc.read_string_table()
    rc.read_asset_list()

    # ---- rt model: rule-AX aware (transit_v2 shape) -------------------
    # The XAsset array allocates 4-ALIGNED (rule AX); below assets_end the
    # runtime offset is identity(+SHIFT past assets_off); above, piecewise
    # over the sim's omap regions.
    arr = rc.assets_off - 64
    SHIFT = ((arr + 3) & ~3) - arr
    ks = sorted(em.omap)
    vs = [em.omap[k] for k in ks]

    def rt(f):
        if f < rc.assets_end:
            return f - 64 + (SHIFT if f >= rc.assets_off else 0)
        j = bisect.bisect_right(ks, f - 64) - 1
        if j < 0:
            return f - 64
        return vs[j] + (f - 64 - ks[j])

    def enc(cell, sh=0):
        return (0xA0000000 + rt(cell) + sh + 1) & 0xFFFFFFFF

    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    i16 = lambda o: struct.unpack_from('>h', Z, o)[0]

    # ---- occurrence recorder (raid3 shape: thermal holder = parent+96) ----
    # OCC rows: (key, holder_cell_or_None, body_off, src). key = ('n', name)
    # for FOLLOW-named bodies, ('a', name_alias_word) otherwise.
    OCC = []
    _mat_stack = []
    _orig_consume = XP.consume_material

    def _rec_consume(d, c, holder=None, src='fx'):
        body = c.o
        if holder is None and _mat_stack:
            holder = _mat_stack[-1] + 96       # thermal word of the parent
        w0 = struct.unpack_from('>I', d, body)[0]
        if w0 in PTRS:
            e = d.index(b'\x00', body + 104)
            key = ('n', d[body + 104:e].decode('latin1', 'replace'))
        else:
            key = ('a', w0)
        OCC.append((key, holder, body, src))
        _mat_stack.append(body)
        try:
            _orig_consume(d, c)                # thermal recursion re-enters us
        finally:
            _mat_stack.pop()

    XP.consume_material = lambda d, c: _rec_consume(d, c)

    # ---- alias sites --------------------------------------------------
    # SITES rows: (slot_off, value, etype, kind, fx_body). Walk parity:
    # zone_gates._walk_fx_slots incl. visuals arrays + decal marks.
    SITES = []

    def walk_fx(b):
        c = FP.Cur(Z, b + 76)
        if u32(b) in PTRS:
            c.cstr(256)

        def cstr():
            c.o = Z.index(b'\x00', c.o) + 1

        n = i16(b + 8) + i16(b + 10) + i16(b + 12)
        if u32(b + 28) not in PTRS:
            return c.o
        base = c.o
        c.o += n * FP.ED
        for k in range(n):
            eb = base + k * FP.ED
            et = Z[eb + 184]
            vcount = Z[eb + 185]
            vic, vsc = Z[eb + 186], Z[eb + 187]
            if u32(eb + 188) in PTRS:
                c.o += (vic + 1) * 96
            if u32(eb + 192) in PTRS:
                c.o += (vsc + 1) * 48
            vis = u32(eb + 196)

            def consume(w, slot_off, kind):
                SITES.append((slot_off, w, et, kind, b))
                if w not in PTRS:
                    return
                if et in (FP.TYPE_SOUND, FP.TYPE_RUNNER):
                    cstr()
                elif et <= 6:
                    _rec_consume(Z, c, holder=slot_off, src='fx.' + kind)
                elif et == FP.TYPE_MODEL:
                    c.o = walk_xmodel(c.o, src='fx.model')

            if et == FP.TYPE_DECAL:
                if vis in PTRS:
                    mb = c.o
                    c.o += vcount * 8
                    for i in range(vcount):
                        for kk in (0, 4):
                            so = mb + i * 8 + kk
                            w = u32(so)
                            SITES.append((so, w, et, 'mark', b))
                            if w in PTRS:
                                _rec_consume(Z, c, holder=so, src='fx.mark')
            elif vcount > 1:
                if vis in PTRS:
                    ab = c.o
                    c.o += vcount * 4
                    for i in range(vcount):
                        consume(u32(ab + i * 4), ab + i * 4, 'array')
            else:
                consume(vis, eb + 196, 'single')
            for off in (224, 228, 232, 252):
                if u32(eb + off) in PTRS:
                    cstr()
            ext = u32(eb + 256)
            if ext in PTRS:
                if et == FP.TYPE_TRAIL:
                    tb = c.o
                    c.o += 28
                    if u32(tb + 16) in PTRS:
                        c.o += u32(tb + 12) * 20
                    if u32(tb + 24) in PTRS:
                        c.o += u32(tb + 20) * 2
                elif et == FP.TYPE_SPOT_LIGHT:
                    c.o += 12
                else:
                    raise RuntimeError('ext type %d' % et)
            if u32(eb + 280) in PTRS:
                cstr()
        return c.o

    def walk_xmodel(o, src='xm'):
        """xmodel_probe.parse_xmodel clone capturing materialHandles cells
        (used for TOP-LEVEL and FX-INLINE et7 models alike, the transit_v2
        census shape)."""
        d = Z
        nb, nrb, ns = d[o + 4], d[o + 5], d[o + 6]
        ptr = {k: u32(o + k) for k in (0, 8, 12, 16, 20, 24, 28, 32, 36,
                                       152, 164, 200, 212, 220, 224)}
        ncoll = u32(o + 156)
        ncollmaps = d[o + 216]
        c = XP.Cur(d, o + XP.BODY)
        if ptr[0] in PTRS:
            c.cstr()
        if ptr[8] in PTRS:
            c.skip(2 * nb)
        if ptr[12] in PTRS:
            c.skip(nb - nrb)
        if ptr[16] in PTRS:
            c.skip(8 * (nb - nrb))
        if ptr[20] in PTRS:
            c.skip(16 * (nb - nrb))
        if ptr[24] in PTRS:
            c.skip(nb)
        if ptr[28] in PTRS:
            c.skip(32 * nb)
        if ptr[32] in PTRS:
            sb = c.o
            c.skip(ns * XP.SURF)
            for i in range(ns):
                XP.parse_surface_dyn(d, sb + i * XP.SURF, c)
        if ptr[36] in PTRS:
            base = c.o
            c.skip(4 * ns)
            for i in range(ns):
                if u32(base + i * 4) in PTRS:
                    _rec_consume(d, c, holder=base + i * 4, src=src + '.mats')
        if ptr[152] in PTRS:
            c.skip(36 * ncoll)
        if ptr[164] in PTRS:
            c.skip(44 * nb)
        if ptr[200] in PTRS:
            c.skip(4 * ns)
        if ptr[212] in PTRS:
            pb = c.o
            c.skip(84)
            if u32(pb) in PTRS:
                c.cstr()
            if u32(pb + 28) in PTRS:
                c.cstr()
        if ptr[220] in PTRS:
            XP.consume_collmaps(d, c, ncollmaps)
        if ptr[224] in PTRS:
            raise XP.Fail('inline physConstraints')
        return c.o

    fx_spans = xm_spans = 0
    try:
        for (i, nm, root, s, e) in spans:
            if e <= s:
                continue
            if root == 'FxEffectDef':
                end = walk_fx(s)
                if end != e:
                    raise RuntimeError('FX walk drift @%d: %d != %d (walker and '
                                       'sim disagree — scores untrustworthy)'
                                       % (s, end, e))
                fx_spans += 1
            elif root == 'XModel':
                end = walk_xmodel(s)
                if end != e:
                    raise RuntimeError('XModel walk drift @%d: %d != %d'
                                       % (s, end, e))
                xm_spans += 1
            elif root == 'Material':
                # top-level Material: the holder is its ASSET-ARRAY entry ptr
                # word (rule AX domain) — cell offset near the zone START, so
                # under cell-order backwardness it precedes every FX slot.
                _rec_consume(Z, XP.Cur(Z, s),
                             holder=rc.assets_off + i * 8 + 4, src='toplevel')
    finally:
        XP.consume_material = _orig_consume

    return dict(zone_path=zone_path, Z=Z, em=em, spans=spans, rc=rc,
                rt=rt, enc=enc, OCC=OCC, SITES=SITES,
                fx_spans=fx_spans, xm_spans=xm_spans, t0=t0)


def score_zone(zone_path, verbose=False):
    """Score one genuine console zone. Returns the result dict.

    Pure measurement: reads the zone, runs loader_sim under the exact-band
    policy, walks FX/XModel/Material spans with _walk_fx_slots-parity walkers,
    and scores every Material-class visuals alias. Writes nothing.
    """
    import zone_stream as zs
    C = collect_zone(zone_path)
    Z, em, enc = C['Z'], C['em'], C['enc']
    OCC, SITES = C['OCC'], C['SITES']
    fx_spans, xm_spans, t0 = C['fx_spans'], C['xm_spans'], C['t0']

    # ---- score --------------------------------------------------------
    # by-handle occurrence index; first occurrence per material key BY CELL.
    by_handle = defaultdict(list)         # synth handle -> [(cell, key)]
    first_cell = {}                       # key -> lowest holder cell
    occ_per_key = Counter()
    for (key, holder, body, src) in OCC:
        occ_per_key[key] += 1
        if holder is None:
            continue
        by_handle[enc(holder)].append((holder, key))
        if key not in first_cell or holder < first_cell[key]:
            first_cell[key] = holder
    reinlined = sum(1 for k, n in occ_per_key.items() if n > 1)

    mat_alias = [t for t in SITES
                 if _is_alias(t[1]) and (t[2] <= 6 or t[3] == 'mark')]

    back = ffi = fwd_nonfirst = 0
    misses = []
    hit_pairs = {}                        # slot -> (cell, key) of the 0-shift hit
    for (so, v, et, kind, fxb) in mat_alias:
        cands = by_handle.get(v)
        if not cands:
            misses.append((so, v, et, kind, fxb))
            continue
        bk = [t for t in cands if t[0] < so]      # holder CELL precedes slot
        if bk:
            back += 1
            hit_pairs[so] = bk[0]
            continue
        # forward match: EXACT iff the matched occurrence is the FIRST
        # occurrence of its material (G5 genuine-forward first-insert class)
        f = [t for t in cands if first_cell.get(t[1]) == t[0]]
        if f:
            ffi += 1
            hit_pairs[so] = f[0]
        else:
            fwd_nonfirst += 1                     # report-only; counts as miss
            misses.append((so, v, et, kind, fxb))
    exact_total = back + ffi

    # ---- ±4 identity-constrained sharpness control --------------------
    sharp = {}
    for sh in (-4, 4):
        raw = same = 0
        for (so, v, et, kind, fxb) in mat_alias:
            ms = [t for t in by_handle.get((v - sh) & 0xFFFFFFFF, ())
                  if t[0] < so]
            if not ms:
                continue
            raw += 1
            k0 = hit_pairs.get(so)
            if k0 is not None and k0[1] in {t[1] for t in ms}:
                same += 1
        sharp['%+d' % sh] = dict(raw=raw, identity=same)

    # ---- blockSize[5] residual ----------------------------------------
    hdr = struct.unpack_from('>10I', Z, 0)
    b5_declared = hdr[2 + 5]
    b5_sim = em.w.block_size[zs.BLOCK_VIRTUAL]

    # ---- miss shift-recovery histogram (diagnostic only) --------------
    shifts = [256, -256, 8192, -8192, 16384, -16384, 24576, -24576]
    rec = Counter()
    for (so, v, et, kind, fxb) in misses:
        got = None
        for sh in shifts:
            if any(t[0] < so for t in by_handle.get((v - sh) & 0xFFFFFFFF, ())):
                got = sh
                break
        rec['%+d' % got if got is not None else 'UNRECOVERED'] += 1

    res = dict(
        zone=os.path.basename(zone_path), bytes=len(Z),
        fx_spans=fx_spans, xm_spans=xm_spans,
        occurrences=len(OCC), reinlined_materials=reinlined,
        denom=len(mat_alias),
        back=back, ffi=ffi, fwd_nonfirst=fwd_nonfirst,
        exact_total=exact_total, misses=len(misses),
        by_kind={k: [sum(1 for t in mat_alias if t[3] == k and t[0] in hit_pairs),
                     sum(1 for t in mat_alias if t[3] == k)]
                 for k in ('single', 'array', 'mark')},
        sharpness=sharp,
        b5_declared=b5_declared, b5_sim=b5_sim,
        b5_residual=b5_sim - b5_declared,
        miss_shift_recovery=dict(rec),
        secs=round(time.time() - t0, 1),
        # per-site detail (additive, FIX 18 instrumentation): the full scored
        # site list and the raw miss tuples, each (slot_off, value, et, kind,
        # fx_body_off). Scoring above is untouched.
        sites=mat_alias, miss_sites=misses,
    )
    if verbose:
        print('%s: %d/%d exact (%d backward + %d forward-first-insert), '
              '%d misses; ±4 identity %s/%s; blockSize[5] residual %+d; %.0fs'
              % (res['zone'], exact_total, res['denom'], back, ffi,
                 res['misses'], sharp['-4']['identity'], sharp['+4']['identity'],
                 res['b5_residual'], res['secs']))
    return res


# =====================================================================
# baseline record
# =====================================================================
def parse_baseline(path=BASELINE_PATH):
    """Baseline lines: `zone exact_total denom b5_residual` (comments = #)."""
    bl = {}
    if not os.path.exists(path):
        return bl
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        f = line.split()
        bl[f[0]] = dict(exact_total=int(f[1]), denom=int(f[2]),
                        b5_residual=int(f[3]))
    return bl


def check_vs_baseline(results, bl):
    """Return (ok, messages). Regression rules: exact_total below baseline,
    denominator change (incomparable), or |b5 residual| growth."""
    ok = True
    msgs = []
    for name, r in results.items():
        b = bl.get(name)
        if b is None:
            msgs.append('%-9s NO BASELINE — informational only' % name)
            continue
        if r.get('error'):
            ok = False
            msgs.append('%-9s **FAIL** scorer error: %s' % (name, r['error'][:120]))
            continue
        line = ('%-9s exact %d/%d (baseline %d/%d)  b5 %+d (baseline %+d)'
                % (name, r['exact_total'], r['denom'],
                   b['exact_total'], b['denom'],
                   r['b5_residual'], b['b5_residual']))
        if r['denom'] != b['denom']:
            ok = False
            line += '  **DENOMINATOR CHANGED — walker/population drift**'
        if r['exact_total'] < b['exact_total']:
            ok = False
            line += '  **REGRESSION below baseline**'
        if abs(r['b5_residual']) > abs(b['b5_residual']):
            ok = False
            line += '  **blockSize[5] residual GREW**'
        msgs.append(line)
    return ok, msgs


def write_baseline(results, path=BASELINE_PATH):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# FX-alias oracle baseline — registry campaign Stage 2 '
                '(2026-07-30)\n')
        f.write('# Scorer: native_linker/fx_oracle.py (rt model = loader_sim + '
                'rt_events_exact.policy(), post 8192-pad band fix).\n')
        f.write('# exact_total = backward-exact + forward-first-insert (the G5 '
                'genuine-forward class counts as EXACT).\n')
        f.write('# Stage-1 recorded scores for reference: raid 227/242 '
                '(+15 = the +256 MTS phase-lock class, unclaimed), dockside '
                '317/326 (+9 ffi), transit 580/583 (+2 ffi; -24576 >78M tail).\n')
        f.write('# columns: zone exact_total denom b5_residual  '
                '(then informational: back ffi secs)\n')
        for name, r in results.items():
            if r.get('error'):
                continue
            f.write('%s %d %d %d  # back=%d ffi=%d secs=%s\n'
                    % (name, r['exact_total'], r['denom'], r['b5_residual'],
                       r['back'], r['ffi'], r['secs']))
    print('baseline -> %s' % path)


# =====================================================================
# CLI (zone_walk_survey pattern: one subprocess per zone)
# =====================================================================
def _run_sub(name, path, timeout):
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__),
                            '--one', path],
                           capture_output=True, text=True, timeout=timeout,
                           cwd=HERE)
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''
        if line.startswith('{'):
            return json.loads(line)
        return dict(zone=os.path.basename(path),
                    error=(r.stderr or r.stdout)[-400:] or 'no output')
    except subprocess.TimeoutExpired:
        return dict(zone=os.path.basename(path), error='TIMEOUT %ds' % timeout)
    except Exception as e:
        return dict(zone=os.path.basename(path), error=str(e)[-400:])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--one', help='score ONE zone in-process, print JSON')
    ap.add_argument('--write-baseline', action='store_true',
                    help='record current scores as the baseline (deliberate '
                         're-baseline only)')
    ap.add_argument('--timeout', type=int, default=3600)
    ap.add_argument('--jobs', type=int, default=3)
    a = ap.parse_args()
    if a.one:
        try:
            print(json.dumps(score_zone(a.one)))
        except Exception:
            import traceback
            print(json.dumps(dict(zone=os.path.basename(a.one),
                                  error=traceback.format_exc()[-600:])))
        return 0

    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {name: ex.submit(_run_sub, name, path, a.timeout)
                for (name, path) in ZONES}
        for name, fut in futs.items():
            r = fut.result()
            results[name] = r
            if r.get('error'):
                print('%-9s **ERROR** %s' % (name, r['error'][:200]))
            else:
                print('%-9s exact %d/%d = %.1f%%  (back %d + ffi %d; '
                      'fwd-nonfirst %d)  ±4 identity %d/%d  b5 %+d  '
                      'reinlined %d  %ss'
                      % (name, r['exact_total'], r['denom'],
                         100.0 * r['exact_total'] / max(1, r['denom']),
                         r['back'], r['ffi'], r['fwd_nonfirst'],
                         r['sharpness']['-4']['identity'],
                         r['sharpness']['+4']['identity'],
                         r['b5_residual'], r['reinlined_materials'],
                         r['secs']))
                print('          by kind: %s  miss shifts: %s'
                      % (r['by_kind'], r['miss_shift_recovery']))

    if a.write_baseline:
        if any(r.get('error') for r in results.values()):
            print('NOT writing baseline: a zone errored')
            return 1
        write_baseline(results)
        return 0

    bl = parse_baseline()
    if not bl:
        print('no baseline file (%s) — run --write-baseline first'
              % BASELINE_PATH)
        return 1
    ok, msgs = check_vs_baseline(results, bl)
    print('\n== baseline gate ==')
    for m in msgs:
        print(' ', m)
    print('GATE: %s' % ('PASS' if ok else '**FAIL**'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
