#!/usr/bin/env python3
"""zone_gates.py — consolidated PRE-BOOT validator battery for console T6 zones.

Every gate encodes a rule that previously cost one or more boot tests to
discover (the mp_skate gfxtail campaign, boots 23-49). Static analysis only:
no dumps, no emulator. See FINDINGS_pipeline_consolidation.md (CLASS C).

Usage:
    python zone_gates.py <zone> [--spans-cache <pkl>] [--corpus <dir>] [--json <out>]

Exit code = number of FAILed gates.
Acceptance: mp_raid_genuine.zone must PASS everything; a well-converted map
should PASS with only documented WARNs.

Gates:
  G0  structural            XFile header / asset directory sanity
  G1  walk-coverage         loader_sim walks every asset span end-to-end
  G2  material census       walk_material over the zone; silent drops = FAIL
  G3  techset bindings      techSet* -> type-8 entry; prefix==family (WARN)
  G4  demand-subset         three-family: const(0,6) ⊆ consts, sampler(2) ⊆ texdefs
  G5  fx-visuals            per-elemType visual sanity; forward-ref deref aliases;
                            shared default-stamp aliases (the 0xA1CC9351 class)
  G6  xsurface-dedup        dedup surfaces alias tris+verts0+vertList together;
                            verts1==NULL on a dedup surface = WARN (genuine: never)
  G7  techset-name-dups     duplicate techset names (family-9 shadowing) = WARN
  G8  decl-corpus           every inline vertexDecl ∈ genuine corpus; wvf matches
                            corpus same-name techset
  G9  asset-name-words      top-level asset NAME word must be FOLLOW/alias/0;
                            inline names at proven offsets must be printable
                            (the skybox/lightdef name-alias-drift class:
                            aliased names that register garbage cannot be
                            payload-verified statically — the pipeline rule is
                            INLINE_ASSET_NAMES at emit; this gate polices the
                            word class + inline content)
  G10 texdef-shared-target  texdef entries of ONE material with DISTINCT
                            semantic nameHashes sharing ONE alias target = the
                            glass white-pane class (boot-52); genuine: never
  G39-43 walk battery       §2b gates 39-43 (baked 2026-07-30): corrected-walk
                            body count (AA), walk honesty (AB), K derivation
                            (AC), depends-layout refusal, container-parse
                            cross-check (AM). Runs native_linker/zone_walk.py.

RULE (CG) AUDIT 2026-08-08 — "this gate would still pass if ____", per gate.
Hardened this pass (proofs: known-bad _boot24_ref 34f14dbc / _boot27_ref 3d3ac955;
known-good mp_raid_oracle / zm_transit_original / mp_dockside_wiiu):
  G1   ...if loader_sim broke mid-walk and fabricated a synthetic tail span
       (rule (AW), mp_skate) -> now counts+WARNs walk-broke-tail, sets ctx.
  G2   ...if the regex census silently MISSED whole materials (never entering
       `dropped`) -> now FAILs when the census finds fewer materials than the
       zone declares top-level Material bodies.
  G4   ...was unreadable standalone: genuine-sanctioned shadowmap-only rows
       FAILed alongside boot-fatal ACTIONABLE rows -> sanctioned-shape rows
       are WARN now; FAIL == boot-55 crash shape only.
  G5   ...if every FX visuals alias pointed at garbage (boot-24: 242/242 wrong,
       G5 PASSed) -> now consumes the G109 holder-identity census and FAILs
       with it. G109 remains the primary; G5's counters are enumeration.
  G6   ...if all 848 dedup buffer aliases were wrong (boot-27: G6 PASSed and
       printed a healthy dedup-surfs count) -> now consumes the G113
       owner-identity census and FAILs with it.
  G106 ...if a wrong-target alias happened to be naturally aligned (saw 40 of
       102 on boot-27) -> now cross-notes the G112 census so the under-count
       is visible in its own output. A DETECTOR IS NOT A CENSUS.
  G2b  §2b-16 FAILed on GENUINE mp_raid_oracle (2 cid-9 misaligned headerPtr
       aliases, entries 883/885) — a check that fails known-good is a bug in
       the check -> cid-9 misaligned-only violations are WARN with provenance.
Not redesigned (blindness documented, out of scope or covered elsewhere):
  G0/G39-43/G8/G110/G111/G114: already checks, not counters.
  G3 would still pass if a non-wpc material bound the wrong (right-typed)
     techset — needs the registry (G-114 mint-equality, HANDOFF v2 §2.5).
  G7/G9: WARN-grade name polices; alias payloads remain statically
     unverifiable (emit-time INLINE_ASSET_NAMES is the kill).
  G10 would still pass if a shared target were the wrong image for ALL its
     semantics at once; ⚠ MEASURED 2026-08-08: the FX-session claim "223
     novel-pair shares match the genuine partition" is REFUTED at alias
     granularity (mp_raid_authored vs mp_raid_oracle: 307/927 material
     partitions differ; 0 of 223 novel and 0 of 84 sp-cp shares match
     genuine's grouping; nearly all collapse onto ONE handle 0xA0248C56).
     Candidate cause of the OPEN black-textures defect — do NOT suppress.
  G112/G113/G109: the identity checks themselves; superseded only by the
     registry-stage G-114..117 (mint equality), which need the registry.

PM ADDENDUM 2026-08-08 (audit session), two rules now part of this checklist:
(1) A verification that cannot FAIL for a constructed wrong answer is
    DECORATIVE. Constructibility check per gate: G109/G112/G113 fail on any
    single repointed alias (construct: +4 any target) — real checks.
    G106/G111/G114/G2b-16 are ALIGNMENT gates: a wrong value that is a
    multiple of the stride PASSES them. ⚠ Their PASS is therefore NOT value
    evidence — alignment fixes residues, only measurement fixes magnitudes
    (the +5-story-vs-measured−202,731 near-miss). G114's uniform-residue FAIL
    direction is sound; its PASS direction needs the registry mint-equality
    gate (G-114 §2.5) to become one. G4/G-XZ fail on constructed wrong
    demand/identity; G2's floor fails on a constructed missed material.
(2) Both-direction known-answer controls, per gate, with WHICH artifact trips:
    FAIL side — boot-24 34f14dbc trips G109 (242), G5 (via G109), G110 (781),
    G106 (24 f32), G112 (102), G113 (843), G6 (via G113), G4 (2 ACTIONABLE),
    G10 (84 sp-cp); boot-27 3d3ac955 trips the same MINUS G109/G5/G110 (its
    FX holders and streamInfo are fixed) — G112/G113/G6 are its signature.
    boot-26 5039968B is phase-class ONLY (trips G110) — never cite it for
    mint/identity gates. PASS side — mp_raid_oracle / zm_transit_original /
    mp_dockside_wiiu: 0 FAILs battery-wide (verified 2026-08-08).
    A gate never seen to fail is UNFALSIFIED, not validated.
"""
import argparse
import json
import os
import pickle
import re
import struct
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ('.', os.path.join('..', 'wiiu_ref'), os.path.join('..', 'WiiU_FF_Studio'),
           os.path.join('..', 'dlc loading', 'native', 'fullrelink')):
    p = os.path.abspath(os.path.join(_HERE, _p))
    if p not in sys.path:
        sys.path.insert(0, p)

import fx_probe as FP
import shader_probe as SP
import wiiu_zone
import xmodel_probe as XP
from _matconst_map import (CONSTDEF, FOLLOW, PTRS, be16, be32,
                           techset_const_hashes, walk_material, walk_techset,
                           walk_technique)

INSERT = 0xFFFFFFFE
AL = lambda v: 0xA0000000 <= v < 0xC0000000
CONST_TYPES = (0, 6)
SAMPLER_TYPE = 2
VD = 116


class Gate:
    """`cap` bounds how many detail lines are RETAINED (counters are always exact).

    ⚠ 2026-08-08: the fixed cap of 40 was a GATE-INFRASTRUCTURE BUG, not a gate-
    content bug. G4 emits up to two lines per material; with const-unsat=24 and
    sampler-unsat=29 (53 failures) the cap silently dropped 13 rows -- including
    all four fire materials whose unsatisfied `spotLightWei` demand killed boot 55.
    The counter said 24; the log named 20; the four that mattered were among the
    unnamed. A detector that cannot show you its finding is not a detector.
    Gates that can legitimately produce many rows now raise their own cap."""

    def __init__(self, name, cap=40):
        self.name = name
        self.status = 'PASS'
        self.counts = Counter()
        self.details = []
        self.cap = cap
        self.dropped = 0

    def _add(self, kind, msg, a):
        if len(self.details) < self.cap:
            self.details.append((kind, msg % a if a else msg))
        else:
            self.dropped += 1

    def fail(self, msg, *a):
        self.status = 'FAIL'
        self._add('FAIL', msg, a)

    def warn(self, msg, *a):
        if self.status == 'PASS':
            self.status = 'WARN'
        self._add('WARN', msg, a)

    def note(self, msg, *a):
        self._add('note', msg, a)


# ---------------------------------------------------------------------------
def g0_structural(Z, ctx):
    g = Gate('G0 structural')
    try:
        rc = wiiu_zone.ZoneReader(Z)
        rc.read_string_table()
        rc.read_asset_list()
        ctx['rc'] = rc
        g.counts['assets'] = len(rc.assets)
        arr = rc.assets_off - 64
        # rule (AX/AC), baked 2026-07-30: the XAsset array is 4-ALIGNED at runtime, not 8
        # (pad = (-assets_off) mod 4, measured on all 71 genuine zones). The old 8-align
        # coincided on arr%8 in {0,5,6,7} and was off by 4 on {1,2,3,4} -- on those zones
        # every genuine entry alias hit _dec_entry's (pay-lo)%8 == 4 and decoded to None,
        # so G3 counted them 'ts-alias-not-entry' and G4 'skip-no-body': a SILENT
        # under-audit, not a mis-audit. Same defect class as loader_sim.simulate() base_rt.
        ctx['our_arr'] = (arr + 3) & ~3
    except Exception as e:
        g.fail('zone header/asset list unreadable: %s', e)
    return g


def g1_walk_coverage(Z, ctx, path, spans_cache):
    g = Gate('G1 walk-coverage')
    spans = None
    if spans_cache and os.path.exists(spans_cache):
        try:
            spans = pickle.load(open(spans_cache, 'rb'))
            # accept either raw span lists or {'spans': ...} pickles
            if isinstance(spans, dict) and 'spans' in spans:
                spans = spans['spans']
            g.note('spans from cache %s', os.path.basename(spans_cache))
        except Exception:
            spans = None
    if spans is None:
        try:
            import loader_sim as LS
            em, spans, CO = LS.simulate(path, verbose=False)
        except Exception as e:
            g.fail('loader_sim.simulate failed: %s', e)
            return g
    norm = []
    for row in spans:
        if len(row) == 5:
            norm.append(tuple(row))
    ctx['spans'] = norm
    g.counts['spans'] = len(norm)
    by_root = Counter(r[2] for r in norm)
    for k in ('XModel', 'FxEffectDef', 'MaterialTechniqueSet', 'GfxWorld'):
        g.counts[k] = by_root.get(k, 0)
    if not norm:
        g.fail('no spans')
    # SIM-PAIRING DRIFT DETECTOR: span index i must agree with the zone's
    # asset-list type at entry i (techset => type 8). Mismatches mean the
    # simulated body<->entry pairing is shifted (boot-44 class) — G3's
    # prefix check and ALL of G4 read the WRONG techset bodies there.
    rc = ctx.get('rc')
    if rc:
        drift = sum(1 for (i, nm, root, s, e) in norm
                    if root == 'MaterialTechniqueSet' and e > s
                    and 0 <= i < len(rc.assets) and rc.assets[i][0] != 8)
        g.counts['sim-pairing-drift'] = drift
        ctx['sim_drift'] = drift
        if drift:
            g.warn('%d techset spans mispaired with asset entries (sim drift) — '
                   'treat G3-prefix and G4 results as UNRELIABLE in the drifted '
                   'regions until loader_sim pairing is fixed (CLASS B)', drift)
        # rule (CG)/(AW) 2026-08-08: G1 would still PASS if loader_sim broke
        # mid-walk (mp_skate: breaks at GFXWORLD, fabricates a 40 MB synthetic
        # tail span) — every owner attribution above the break is then fiction.
        # G111 guards itself locally; this makes the condition visible at the
        # source and available to every consumer via ctx.
        if norm and (norm[-1][0] + 1) < len(rc.assets):
            ctx['walk_broke'] = True
            g.counts['walk-broke-tail'] = len(rc.assets) - (norm[-1][0] + 1)
            g.warn('walk covers %d of %d assets — the FINAL span is SYNTHETIC '
                   '(rule (AW)); owner attributions above the break are '
                   'UNMEASURED, not clean', norm[-1][0] + 1, len(rc.assets))
    return g


def _material_scan(Z, ctx):
    """name-anchored material census (the _nullct_oracle scan, WALKER-correct)."""
    if 'mats' in ctx:
        return ctx['mats'], ctx['mat_dropped']
    isalias = AL
    ptrish = lambda v: v == 0 or v in PTRS or isalias(v)
    mats, dropped = [], []
    last = -1
    for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), Z):
        o = m.start()
        if o < last or o + 104 > len(Z):
            continue
        if be32(Z, o) != FOLLOW:
            continue
        texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
        if not (texc <= 64 and constc <= 64 and sbc <= 64
                and all(ptrish(be32(Z, o + x)) for x in (80, 84, 88, 92, 96))):
            continue
        if not isalias(be32(Z, o + 80)) and be32(Z, o + 80) not in PTRS:
            continue
        try:
            info, nxt = walk_material(Z, o)
        except Exception:
            continue
        nm = info['name']
        if not nm or not (1 <= len(nm) <= 200) or not all(
                32 <= c < 127 for c in nm.encode('latin1', 'replace')):
            continue
        if constc and info.get('ct_off') is not None:
            names = [Z[info['ct_off'] + k * CONSTDEF + 4:info['ct_off'] + k * CONSTDEF + 16]
                     for k in range(constc)]
            if not all(n[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in n)
                       for n in names):
                dropped.append((o, nm, constc))
                continue
        info['_off'] = o
        mats.append(info)
        last = o + 104
    ctx['mats'] = mats
    ctx['mat_dropped'] = dropped
    return mats, dropped


def g2_material_census(Z, ctx):
    g = Gate('G2 material census')
    mats, dropped = _material_scan(Z, ctx)
    g.counts['materials'] = len(mats)
    g.counts['dropped'] = len(dropped)
    if dropped:
        # a silently-dropped material is an unaudited material (THE WALKER BUG)
        g.fail('%d materials dropped by the walker (unaudited!)', len(dropped))
        for (o, nm, cc) in dropped[:6]:
            g.note('dropped @%d constc=%d %s', o, cc, nm[:48])
    if not mats:
        g.fail('no materials found')
    # rule (CG) 2026-08-08: this census would still PASS if the regex scan
    # silently MISSED materials it never even saw (they only reach `dropped`
    # if walk_material was attempted). Floor it against what the zone itself
    # declares: every top-level Material span must have been found.
    top = sum(1 for r in ctx.get('spans', ()) if r[2] == 'Material' and r[4] > r[3])
    g.counts['toplevel-material-spans'] = top
    if len(mats) < top:
        g.fail('census found %d materials but the zone declares %d top-level '
               'Material bodies — at least %d NEVER SCANNED (unaudited, the '
               'silent half of the walker bug)', len(mats), top, top - len(mats))
    # §2b-27 (rule S), baked 2026-07-30: when a zone carries a <mapname>_lut material it must
    # be a SINGLETON and its techniqueSet word (+80) must be INSERT (0xFFFFFFFE).
    luts = [m for m in mats if m['name'].endswith('_lut')]
    if luts:
        g.counts['lut-materials'] = len(luts)
        if len(luts) != 1:
            g.fail('§2b-27: %d _lut materials (must be exactly one): %s',
                   len(luts), [m['name'][:40] for m in luts[:4]])
        for m in luts:
            if m.get('ts') != INSERT:
                g.fail('§2b-27: %s techniqueSet word +80 = 0x%08X, must be INSERT',
                       m['name'][:40], m.get('ts', 0))
    return g


def _ts_index(ctx):
    """asset index k -> techset body span start (from spans)."""
    if 'ts_by_idx' in ctx:
        return ctx['ts_by_idx']
    out = {}
    for (i, nm, root, s, e) in ctx.get('spans', []):
        if root == 'MaterialTechniqueSet' and e > s:
            out[i] = s
    ctx['ts_by_idx'] = out
    return out


def _dec_entry(ctx, alias):
    our_arr = ctx.get('our_arr')
    rc = ctx.get('rc')
    if our_arr is None or rc is None:
        return None
    pay = (alias - 1) & 0x1FFFFFFF
    lo = our_arr + 4
    hi = lo + len(rc.assets) * 8
    if not (lo <= pay < hi) or (pay - lo) % 8:
        return None
    return (pay - lo) // 8


def _refuse_on_sim_drift(g, ctx, what):
    """HARD REFUSAL on nonzero loader_sim pairing drift. Returns True if refused.

    ⛔ Referee ruling 2026-07-20, WIDENED 2026-08-08. The original ruling was
    written for G4 and only G4 ever shipped it — but the drift does not care
    which gate is reading. EVERY gate that maps a material's techniqueSet alias
    to a techset BODY does it the same way (`_dec_entry` -> asset index k ->
    `_ts_index`/spans -> body span), so a shifted span<->entry pairing makes ALL
    of them read the WRONG body and report confident garbage (the boot-44
    class). G1 only WARNed about drift, and a WARN is a gate that ran anyway.

    Widened to: G3 (prefix/type check), G4 (demand audit), G111 (resolvability),
    and every cross-zone demand comparison.

    Rule (CG) falsification — "this gate would still pass if ____":
      before: G3/G111 would still pass if the sim pairing were drifted by any
              amount, because neither one ever consulted ctx['sim_drift'];
              they would silently audit other assets' bodies and PASS.
      after:  they cannot pass at all unless drift was MEASURED and is exactly 0.
    """
    if 'sim_drift' not in ctx:
        g.fail('REFUSING to audit %s: sim pairing drift was never established '
               '(G0/G1 did not set ctx["sim_drift"]) — results are unverifiable.', what)
        g.counts['refused-drift-unmeasured'] = 1
        return True
    if ctx['sim_drift']:
        g.fail('REFUSING to audit %s: loader_sim pairing drift = %d (MUST be 0). '
               'Under drift this gate resolves techset handles to the WRONG bodies; '
               'its numbers are meaningless. Fix the pairing (use a LIVE loader_sim, '
               'not a cached simmap pickle — _skate6_simmap.pkl carries drift 81) '
               'and re-run.', what, ctx['sim_drift'])
        g.counts['refused-sim-drift'] = ctx['sim_drift']
        return True
    return False


def g3_techset_bindings(Z, ctx):
    g = Gate('G3 techset bindings')
    if _refuse_on_sim_drift(g, ctx, 'techset bindings'):
        return g
    mats, _ = _material_scan(Z, ctx)
    rc = ctx.get('rc')
    tsx = _ts_index(ctx)
    for info in mats:
        ts = info['ts']
        nm = info['name']
        if ts in PTRS:
            g.counts['inline-ts'] += 1
            continue
        if not AL(ts):
            g.counts['ts-other'] += 1
            continue
        k = _dec_entry(ctx, ts)
        if k is None:
            g.counts['ts-alias-not-entry'] += 1
            continue
        g.counts['ts-entry'] += 1
        t = rc.assets[k][0]
        if t != 8:
            g.fail('material %s techSet* -> asset %d TYPE %d (want 8)', nm[:40], k, t)
            continue
        # prefix == family — the PROVEN invariant covers wpc/ materials only
        # (raid oracle 351/351); other prefixes legitimately cross families.
        if nm.startswith('wpc/'):
            s = tsx.get(k)
            if s is not None:
                e = Z.find(b'\x00', s + 136)
                tsn = Z[s + 136:e].decode('latin1', 'replace').lstrip(',')
                g.counts['wpc-checked'] += 1
                if not tsn.startswith('wpc_'):
                    g.counts['wpc-prefix-mismatch'] += 1
    if g.counts['wpc-prefix-mismatch']:
        g.warn('%d wpc/ materials bind a non-wpc_ techset (latent render-wrong '
               'class, boots 23-25 family)', g.counts['wpc-prefix-mismatch'])
    return g


def _mat_texhashes(Z, off):
    b = off
    texc = Z[b + 72]
    tsp, ttp = be32(Z, b + 80), be32(Z, b + 84)
    if texc == 0:
        return [], 'none'
    c = XP.Cur(Z, b + 104)
    if be32(Z, b) in PTRS:
        c.cstr()
    if tsp in PTRS:
        c.o, _ = SP.parse_techset(Z, c.o)
    if ttp not in PTRS:
        return None, 'alias'
    defs = c.o
    return [be32(Z, defs + i * 16) for i in range(texc)], 'inline'


def _ts_demands(Z, s, cache):
    if s in cache:
        return cache[s]
    try:
        passes, _ = walk_techset(Z, s)
    except Exception:
        cache[s] = None
        return None
    consts, samplers = set(), set()
    complete = True
    for p in passes:
        if p.get('args_off') is None:
            complete = False
            continue
        for j in range(p['nargs']):
            a = p['args_off'] + j * 8
            at = be16(Z, a)
            if at in CONST_TYPES:
                consts.add(be32(Z, a + 4))
            elif at == SAMPLER_TYPE:
                samplers.add(be32(Z, a + 4))
    cache[s] = (consts, samplers, complete)
    return cache[s]


SANCTIONED_SLOTS = frozenset((2, 3))     # genuine-sanctioned shadowmap-build slots


def _slot_demands(Z, s):
    """{slot_index: (consts, samplers)} for a console techset body at `s`.

    _ts_demands unions demand across ALL passes and loses slot identity, but the
    genuine calibration is per-SLOT: across 8,854 alias-resolved materials in
    mp_raid_genuine / mp_dockside_wiiu / zm_transit_original / mp_hijacked /
    mp_slums, genuine leaves a demand unsatisfied in exactly 10 materials and ONLY
    in slots 2 and 3. So slot identity is what separates 'genuine ships this' from
    'this is the boot-55 crash shape'. Returns None if the walk fails.

    ⚠ walk order mirrors walk_techset: only slots == FOLLOW are real techniques;
    an ALIASED slot consumes zero zone bytes and is invisible here (its demand is
    the alias target's). Absence of demand in this map is therefore NOT proof of
    absence of demand."""
    try:
        slots = [be32(Z, s + 8 + i * 4) for i in range(32)]
        c = SP.Cur(Z, s + 136)
        if be32(Z, s) == FOLLOW:
            c.cstr(160)
        out = {}
        for si, v in enumerate(slots):
            if v != FOLLOW:
                continue
            end, passes = walk_technique(Z, c.o)
            cs, ss = set(), set()
            for p in passes:
                if p.get('args_off') is None:
                    continue
                for j in range(p['nargs']):
                    a = p['args_off'] + j * 8
                    at = be16(Z, a)
                    if at in CONST_TYPES:
                        cs.add(be32(Z, a + 4))
                    elif at == SAMPLER_TYPE:
                        ss.add(be32(Z, a + 4))
            out[si] = (cs, ss)
            c.o = end
        return out
    except Exception:
        return None


def g4_demand_subset(Z, ctx):
    # cap raised: G4 can emit 2 lines per material and the old fixed 40 silently
    # hid the four rows that killed boot 55 (see Gate docstring).
    g = Gate('G4 demand-subset', cap=250)
    # ⛔ HARD-REFUSE on nonzero loader_sim pairing drift (referee ruling 2026-07-20).
    # G4 resolves each material's techSet via body+80 and audits THAT body's demands, so
    # a drifted span<->entry pairing makes it audit the WRONG techset body (boot-44 class)
    # and report confident garbage. The whole "ours 16 / key 29" dispute was exactly this:
    # 5-10 residually mispaired techsets land on 16-29, a CORRECT pairing gives ours 13 /
    # key 0, and full drift gives key 345 / ours 552. The 0 is knife-edge -- any pairing
    # error blows it to 300-450 -- which is why a drifted G4 must not be allowed to speak.
    # Every wrong number in that dispute traces to a gate that ran anyway: refuse, don't warn.
    if _refuse_on_sim_drift(g, ctx, 'demand subset'):
        return g
    mats, _ = _material_scan(Z, ctx)
    tsx = _ts_index(ctx)
    dcache = {}
    scache = {}
    rows = []            # (actionable, kind, name, tsidx, missing, slots)
    for info in mats:
        ts = info['ts']
        if not AL(ts):
            g.counts['skip-nonalias-ts'] += 1
            continue
        k = _dec_entry(ctx, ts)
        s = tsx.get(k) if k is not None else None
        if s is None:
            g.counts['skip-no-body'] += 1
            continue
        dm = _ts_demands(Z, s, dcache)
        if dm is None:
            g.counts['ts-walk-fail'] += 1
            continue
        consts, samplers, complete = dm
        if not complete:
            g.counts['ts-walk-partial'] += 1
        if s not in scache:
            scache[s] = _slot_demands(Z, s)
        sd = scache[s]
        carried_c = set(info.get('consts') or [])
        miss_c = consts - carried_c
        tex, kind = (_mat_texhashes(Z, info['_off']) if samplers else (None, None))
        miss_s = set()
        if samplers:
            if kind == 'alias':
                g.counts['texdef-aliased-skip'] += 1
            else:
                miss_s = samplers - set(tex or [])

        # ⚑ BOOT-55 CORRECTION (measured 2026-08-08). A bare slot-index blacklist
        # is WRONG. `effect_8qf8f359` has ONLY slots 2 and 3 as FOLLOW techniques
        # -- for an FX techset those two ARE the drawing passes, not a shadowmap
        # adjunct. gfx_fxt_fire_anim_lick_z5_eo was unsatisfied in slots [2,3]
        # ONLY, was therefore classed 'sanctioned', and it killed boot 55.
        # Slots 2/3 are sanctioned ONLY when the techset also carries other
        # technique slots (i.e. they really are the shadowmap adjunct). When
        # {2,3} is the WHOLE technique set, nothing is sanctioned.
        sanctioned = SANCTIONED_SLOTS if (sd and not set(sd) <= SANCTIONED_SLOTS) else frozenset()
        if sd and set(sd) <= SANCTIONED_SLOTS:
            g.counts['slots23-only-techsets'] += 1

        def slots_for(h, which):
            if not sd:
                return None
            return sorted(si for si, (cc, sss) in sd.items() if h in (cc if which == 'c' else sss))

        for miss, which, label in ((miss_c, 'c', 'const'), (miss_s, 's', 'sampler')):
            if not miss:
                continue
            g.counts['%s-unsat' % label] += 1
            allslots = set()
            for h in miss:
                sl = slots_for(h, which)
                if sl:
                    allslots |= set(sl)
            act = sorted(allslots - sanctioned)
            if act:
                g.counts['%s-unsat-ACTIONABLE' % label] += 1
            rows.append((bool(act), label, info['name'][:40], k,
                         ['0x%08x' % h for h in sorted(miss)][:3], act, sorted(allslots)))

    # ACTIONABLE first (the boot-fatal subset), then the genuine-sanctioned rest.
    # Genuine console zones leave demand unsatisfied in 10 of 8,854 materials and
    # ONLY in technique slots 2/3 (shadowmap builds) -- so slot 2/3-only rows are
    # NOT evidence of a defect, while a slot>=4 row is the boot-55 crash shape.
    for (act, label, nm, k, miss, actslots, allslots) in sorted(rows, key=lambda r: (not r[0],
                                                                                    r[1], r[2])):
        if act:
            g.fail('ACTIONABLE material %s misses %s hash(es) %s in technique slot(s) %s '
                   '(techset asset %s) -- unsatisfiable outside the genuine-sanctioned '
                   'shadowmap slots 2/3; consumer search is UNBOUNDED (boot-55 class)',
                   nm, label, miss, actslots, k)
        else:
            # rule (CG) 2026-08-08: these rows are by their own message NOT the
            # boot-fatal shape (genuine ships 10 of them in 8,854 materials),
            # yet they FAILed the gate — so a G4 FAIL could not be read as
            # "boot-55 crash shape present" without opening the log. WARN keeps
            # them visible; FAIL now means ACTIONABLE only.
            g.warn('material %s misses %s hash(es) %s (slots %s; genuine-sanctioned '
                   'shadowmap-only -- not the boot-fatal shape)', nm, label, miss,
                   allslots if allslots else 'unknown')
    g.counts['checked'] = len(mats)
    return g


def _ts_name_at(Z, s):
    """techset asset NAME as stored in its body (same read G3 uses)."""
    try:
        e = Z.find(b'\x00', s + 136)
        if e < 0:
            return None
        return Z[s + 136:e].decode('latin1', 'replace').lstrip(',')
    except Exception:
        return None


def _resolved_ts_identity(Z, ctx):
    """material identity -> RESOLVED TECHSET IDENTITY, for cross-zone comparison.

    ⭐⭐⭐ WHY THIS EXISTS (measured 2026-08-08, the shadowcaster case).
    `wpc/shadowcaster` @78,424,719 is BYTE-IDENTICAL between mp_skate_gfxtail46
    and mp_skate_cm46 (same 104-byte body, md5 43184d84…, techSet@+80 =
    0xA00058C5, texc=1, texdef 0xA0AB1041). Both zones decode that handle to the
    SAME asset index 779, whose span starts at the SAME offset 57,515,001 and
    runs the same 15,186 bytes, and both zones report sim drift 0. Yet G4 called
    the material SATISFIED in gfxtail46 and UNSATISFIED in cm46.

    Neither answer is a bug in G4's arithmetic: 12 bytes DIFFER inside that span
    (at 57,530,036..57,530,061, in technique slot 3's argument table), so slot 3
    demands 1 sampler in one zone and 4 in the other. The material did not
    change; the ASSET IT RESOLVES TO changed underneath it.

    ⛔ THE LESSON: index equality, offset equality and drift==0 are ALL true here
    and ALL three are false comfort. The only sound identity for "the techset
    this handle resolved to" is the CONTENT of the resolved body plus its name.
    Identical data must never yield two answers — when the resolution moved, the
    comparison is not answerable and the gate must REFUSE, not pick a side.

    Returns {(mat_name, md5(body)): {...}} for every alias-techset material.
    """
    import hashlib
    out = {}
    mats, _ = _material_scan(Z, ctx)
    tsx = _ts_index(ctx)
    spans_by_idx = {r[0]: r for r in ctx.get('spans', [])}
    dcache = {}
    for info in mats:
        ts, off = info['ts'], info['_off']
        if not AL(ts):
            continue
        body = Z[off:off + 104]
        key = (info['name'], hashlib.md5(body).hexdigest())
        k = _dec_entry(ctx, ts)
        s = tsx.get(k) if k is not None else None
        rec = {'off': off, 'handle': ts, 'entry': k, 'ts_start': s,
               'ts_name': None, 'ts_md5': None, 'ts_len': None,
               'consts': None, 'samplers': None, 'complete': None,
               'miss_c': None, 'miss_s': None, 'verdict': 'no-body'}
        if s is not None:
            sp = spans_by_idx.get(k)
            e = sp[4] if sp else None
            rec['ts_name'] = _ts_name_at(Z, s)
            if e is not None and e > s:
                rec['ts_len'] = e - s
                rec['ts_md5'] = hashlib.md5(Z[s:e]).hexdigest()
            dm = _ts_demands(Z, s, dcache)
            if dm is not None:
                consts, samplers, complete = dm
                rec['consts'] = sorted(consts)
                rec['samplers'] = sorted(samplers)
                rec['complete'] = complete
                miss_c = consts - set(info.get('consts') or [])
                tex, kind = (_mat_texhashes(Z, off) if samplers else (None, None))
                miss_s = set() if (not samplers or kind == 'alias') else samplers - set(tex or [])
                rec['miss_c'], rec['miss_s'] = sorted(miss_c), sorted(miss_s)
                rec['verdict'] = 'UNSATISFIED' if (miss_c or miss_s) else 'SATISFIED'
            else:
                rec['verdict'] = 'ts-walk-fail'
        # a duplicate (name, body-md5) inside ONE zone is itself un-answerable
        if key in out and out[key]['ts_md5'] != rec['ts_md5']:
            out[key]['_intra_zone_ambiguous'] = True
        else:
            out[key] = rec
    return out


def g_cross_zone_demand_identity(zone_paths, ctxs=None):
    """CROSS-ZONE DEMAND GATE with resolved-techset-identity pinning.

    Any cross-zone demand comparison must pin WHICH techset asset each
    material's handle actually resolved to, PER ZONE, and must REFUSE
    (resolution-moved) when a byte-identical material resolves to a different
    techset across the compared zones — instead of reporting one of the two
    verdicts as if it were a finding about the material.

    Rule (CG) falsification — "this gate would still pass if ____":
      before: the old comparison would still pass (and answer) if the two zones'
              techset bodies had been swapped, rewritten, or re-authored wholesale,
              because it compared material bytes and trusted the resolution. It
              answered SATISFIED/UNSATISFIED for wpc/shadowcaster from identical
              input — a false verdict by construction.
      after:  it cannot answer for ANY material whose resolved techset identity
              (name + content hash + length) is not identical in every compared
              zone; those are REFUSED and counted, never scored. It also cannot
              run at all under sim drift (see _refuse_on_sim_drift).

    Returns a Gate. FAIL = at least one refusal or one genuine demand change.
    """
    g = Gate('G-XZ cross-zone demand identity', cap=250)
    if len(zone_paths) < 2:
        g.fail('cross-zone gate needs >= 2 zones, got %d', len(zone_paths))
        return g

    maps, names = [], []
    for p in zone_paths:
        Z = open(p, 'rb').read()
        ctx = {}
        g0 = g0_structural(Z, ctx)
        if g0.status == 'FAIL':
            g.fail('%s: structural gate failed — cannot compare', os.path.basename(p))
            return g
        g1 = g1_walk_coverage(Z, ctx, p, None)
        if g1.status == 'FAIL':
            g.fail('%s: walk-coverage failed — cannot compare', os.path.basename(p))
            return g
        if _refuse_on_sim_drift(g, ctx, 'cross-zone demand (%s)' % os.path.basename(p)):
            return g
        maps.append(_resolved_ts_identity(Z, ctx))
        names.append(os.path.basename(p))
        if ctxs is not None:
            ctxs.append(ctx)

    common = set(maps[0])
    for m in maps[1:]:
        common &= set(m)
    g.counts['zones'] = len(maps)
    g.counts['materials-identical-in-all-zones'] = len(common)
    for i, m in enumerate(maps):
        g.counts['alias-ts-materials[%s]' % names[i]] = len(m)

    # ⛔ VACUOUS-PASS GUARD (rule CG, found during validation 2026-08-08).
    # "this gate would still pass if the two zones had NOTHING in common" — it did:
    # _boot24_ref.zone vs mp_skate_cm46.zone returned PASS with 0 comparable
    # materials. An empty intersection is not agreement, it is a comparison that
    # never happened; a gate must never report PASS for work it did not do.
    if not common:
        g.fail('REFUSING: the compared zones share NO byte-identical material at all '
               '(%s) — there is nothing to compare and a PASS here would be vacuous. '
               'These are almost certainly different maps/builds.',
               ', '.join('%s=%d' % (names[i], len(maps[i])) for i in range(len(maps))))
        g.counts['REFUSED-vacuous-no-common-materials'] = 1
        return g

    # ⚠ ORDERING IS PART OF THE GATE (Gate.cap docstring: a detector that cannot
    # show you its finding is not a detector). The refusals that MATTER are the
    # ones that previously produced a FALSE VERDICT — identical material, moved
    # resolution, and the two zones disagreeing. Alphabetical order buried
    # wpc/shadowcaster ('w') behind 126 cap-dropped lines on its own proof case.
    # Verdict-splitting refusals are emitted FIRST, always.
    refusals = []
    for key in sorted(common):
        recs = [m[key] for m in maps]
        if any(r.get('_intra_zone_ambiguous') for r in recs):
            g.counts['REFUSED-intra-zone-ambiguous'] += 1
            g.fail('REFUSING %s: two byte-identical copies inside one zone resolve '
                   'to different techsets', key[0][:44])
            continue
        ident = [(r['ts_name'], r['ts_md5'], r['ts_len']) for r in recs]
        if len(set(ident)) != 1:
            g.counts['REFUSED-resolution-moved'] += 1
            split = len(set(r['verdict'] for r in recs)) != 1
            if split:
                g.counts['REFUSED-resolution-moved-VERDICT-SPLIT'] += 1
            refusals.append((not split, key, recs))
            continue
        g.counts['comparable'] += 1
        verdicts = set(r['verdict'] for r in recs)
        if len(verdicts) != 1:
            # identity pinned and STILL divergent => a real defect in our reader
            g.counts['DIVERGENT-under-pinned-identity'] += 1
            g.fail('%s: identical material AND identical resolved techset, but '
                   'verdicts differ %s — reader defect', key[0][:44],
                   ' / '.join('%s=%s' % (names[i], recs[i]['verdict'])
                              for i in range(len(recs))))
        elif verdicts == {'UNSATISFIED'}:
            g.counts['unsat-in-all-zones'] += 1

    for (_low, key, recs) in sorted(refusals, key=lambda r: (r[0], r[1])):
        split = not _low
        g.fail('REFUSING%s %s (body md5 %s, handle 0x%08X): RESOLUTION MOVED — '
               'byte-identical material resolves to a DIFFERENT techset per zone; '
               'a demand comparison here is not answerable. %s',
               ' [VERDICT SPLIT]' if split else '', key[0][:44], key[1][:8],
               recs[0]['handle'],
               ' | '.join('%s: entry=%s @%s name=%s ts_md5=%s len=%s verdict=%s'
                          % (names[i], recs[i]['entry'], recs[i]['ts_start'],
                             recs[i]['ts_name'], (recs[i]['ts_md5'] or '-')[:8],
                             recs[i]['ts_len'], recs[i]['verdict'])
                          for i in range(len(recs))))
    if g.counts['REFUSED-resolution-moved']:
        g.note('%d materials refused: their techset asset changed underneath them. '
               'Re-run the comparison only against zones whose techset bodies match, '
               'or audit each zone standalone with G4.',
               g.counts['REFUSED-resolution-moved'])
    return g


def g5_fx_visuals(Z, ctx):
    g = Gate('G5 fx-visuals')
    spans = [(s, e) for (i, nm, root, s, e) in ctx.get('spans', [])
             if root == 'FxEffectDef' and e > s]
    g.counts['fx'] = len(spans)
    slots = []          # (fx_start, slot_off, value, elemType)
    for (s, e) in spans:
        try:
            end = _walk_fx_slots(Z, s, slots)
        except Exception as ex:
            g.fail('FX walk failed @%d: %s', s, str(ex)[:60])
            continue
        if end != e:
            g.fail('FX walk end mismatch @%d (%d != %d)', s, end, e)
    g.counts['visual-slots'] = len(slots)
    by_val = defaultdict(list)
    for (fs, so, v, et) in slots:
        if AL(v):
            by_val[v].append((fs, so, et))
    fwd_total = 0
    for v, users in by_val.items():
        pay = (v - 1) & 0x1FFFFFFF
        fwd = [u for u in users if u[1] < pay]
        fwd_total += len(fwd)
        distinct_fx = len({u[0] for u in users})
        if len(users) >= 8 and distinct_fx >= 3 and len(fwd) == len(users):
            g.fail('default-stamp alias 0x%08X: %d slots in %d FX, all forward-ref '
                   '(the 0xA1CC9351 class)', v, len(users), distinct_fx)
    g.counts['alias-slots'] = sum(len(u) for u in by_val.values())
    g.counts['forward-ref-alias-slots'] = fwd_total   # informational: genuine
    # zones carry forward FX visual aliases (raid: 145) — only the shared
    # default-stamp pattern above is a defect.
    # rule (CG) 2026-08-08: G5 would still PASS if EVERY visuals alias pointed
    # at garbage — boot-24 (34f14dbc) has 242/242 Material-class visuals wrong
    # and G5 PASSed it. The identity check is G109 (holder identity, computed
    # before G5 in run()); G5 now refuses to present its counters as health
    # when that census says the class is broken.
    oh = ctx.get('g109_off_holder')
    if oh is None:
        g.note('holder-identity census (G109) unavailable — counters above are '
               'ENUMERATION, not validation')
    elif oh and ctx.get('g109_status') == 'FAIL':
        g.fail('%d Material-class visuals aliases land on NO occurrence holder '
               '(G109 census) — the alias/visual counts above enumerate a '
               'BROKEN class (FIX-27 crash shape)', oh)
    return g


def _walk_fx_slots(Z, b, out):
    """fx_probe-parity walk capturing (fx_start, slot_off, value, elemType)."""
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    i16 = lambda o: struct.unpack_from('>h', Z, o)[0]
    c = FP.Cur(Z, b + 76)
    if u32(b) in PTRS:
        c.cstr()

    def cstr():
        e = Z.index(b'\x00', c.o)
        c.o = e + 1

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

        def consume(w):
            if w not in PTRS:
                return
            if et in (FP.TYPE_SOUND, FP.TYPE_RUNNER):
                cstr()
            elif et <= 6:
                XP.consume_material(Z, c)
            elif et == FP.TYPE_MODEL:
                end, _ = XP.parse_xmodel(Z, c.o)
                c.o = end

        if et == FP.TYPE_DECAL:
            if vis in PTRS:
                mb = c.o
                c.o += vcount * 8
                for i in range(vcount):
                    for kk in (0, 4):
                        w = u32(mb + i * 8 + kk)
                        out.append((b, mb + i * 8 + kk, w, et))
                        if w in PTRS:
                            XP.consume_material(Z, c)
        elif vcount > 1:
            if vis in PTRS:
                ab = c.o
                c.o += vcount * 4
                for i in range(vcount):
                    w = u32(ab + i * 4)
                    out.append((b, ab + i * 4, w, et))
                    consume(w)
        else:
            out.append((b, eb + 196, vis, et))
            consume(vis)
        for off in (224, 228, 232, 252):
            if u32(eb + off) in PTRS:
                cstr()
        ext = u32(eb + 256)
        if ext in PTRS:
            if et == FP.TYPE_TRAIL:
                tb = c.o
                c.o += 28
                vc_, ic_ = u32(tb + 12), u32(tb + 20)
                if u32(tb + 16) in PTRS:
                    c.o += vc_ * 20
                if u32(tb + 24) in PTRS:
                    c.o += ic_ * 2
            elif et == FP.TYPE_SPOT_LIGHT:
                c.o += 12
            else:
                raise RuntimeError('ext type %d' % et)
        if u32(eb + 280) in PTRS:
            cstr()
    return c.o


def g6_xsurface_dedup(Z, ctx):
    g = Gate('G6 xsurface-dedup')
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    xm = [(s, e) for (i, nm, root, s, e) in ctx.get('spans', [])
          if root == 'XModel' and e > s]
    g.counts['xmodels'] = len(xm)
    for (s, e) in xm:
        if u32(s) not in PTRS:
            continue
        en = Z.find(b'\x00', s + 244)
        name = Z[s + 244:en].decode('latin1', 'replace')
        nb, nrb, ns = Z[s + 4], Z[s + 5], Z[s + 6]
        o = en + 1
        for (kk, sz) in ((8, 2 * nb), (12, nb - nrb), (16, 8 * (nb - nrb)),
                         (20, 16 * (nb - nrb)), (24, nb), (28, 32 * nb)):
            if u32(s + kk) in PTRS:
                o += sz
        if u32(s + 32) not in PTRS:
            continue
        for i in range(ns):
            b = o + i * 128
            # §2b-5 / rule (D), baked 2026-07-30: a SKINNED surface (flags&0x82, u16@+2) must
            # carry all four skin-stream pointers (+24/+32/+36/+44) non-zero — the engine runs
            # the skin pre-pass off vertInfo and dereferences them with no null check (the
            # teardrop-flag/restroom crash). Genuine census: raid 6 + dockside 5 skinned
            # surfaces, ALL four words FOLLOW on every one; zero dedup-skinned exist.
            sflags = struct.unpack_from('>H', Z, b + 2)[0]
            if sflags & 0x82:
                skinw = [u32(b + 24), u32(b + 32), u32(b + 36), u32(b + 44)]
                if not all(skinw):
                    g.fail('§2b-5: skinned surface (flags=0x%04X) with NULL skin stream '
                           'ptr(s) %s @surf %d (rule D crash class)', sflags,
                           ['0x%08X' % w for w in skinw], i)
            t12, t52, t72, t96 = u32(b + 12), u32(b + 52), u32(b + 72), u32(b + 96)
            any_alias = AL(t12) or AL(t52) or AL(t96)
            if not any_alias:
                g.counts['follow-surfs'] += 1
                continue
            g.counts['dedup-surfs'] += 1
            vlc = Z[b + 1]
            need = [AL(t12), AL(t52)] + ([AL(t96)] if vlc else [])
            if not all(need):
                g.fail('%s si=%d dedup surface mixes alias/non-alias '
                       '(t12=%08X t52=%08X t96=%08X vlc=%d)',
                       name[:40], i, t12, t52, t96, vlc)
            if vlc and not AL(t96) and t96 == 0:
                g.counts['dedup-vertlist-null'] += 1
            if t72 == 0:
                g.counts['dedup-verts1-null'] += 1
    if g.counts['dedup-verts1-null']:
        g.warn('%d dedup surfaces have verts1==NULL (genuine console: never; '
               'lighting-stream risk)', g.counts['dedup-verts1-null'])
    # rule (CG) 2026-08-08 (HANDOFF v2, "THIRD APPLICATION"): G6 would still
    # PASS if all 848 dedup buffer aliases were wrong — boot-27 (3d3ac955) has
    # exactly that and G6 printed a healthy dedup-surfs=212. Where the aliases
    # POINT is G113's owner-identity census (computed before G6 in run());
    # G6 now fails with it instead of advertising an unvalidated count.
    xo = ctx.get('g113_off_owner')
    if xo is None:
        g.note('buffer owner-identity census (G113) unavailable — dedup-surfs '
               'is ENUMERATION, not validation')
    elif xo:
        g.fail('%d dedup buffer aliases land on NO same-field FOLLOW buffer '
               '(G113 census) — the dedup-surfs count above enumerates a '
               'BROKEN class (VERTEX EXPLOSION shape)', xo)
    return g


def g7_ts_name_dups(Z, ctx):
    g = Gate('G7 techset-name-dups')
    names = Counter()
    for (i, nm, root, s, e) in ctx.get('spans', []):
        if root != 'MaterialTechniqueSet' or e <= s:
            continue
        en = Z.find(b'\x00', s + 136)
        n = Z[s + 136:en].decode('latin1', 'replace')
        if not n.startswith(','):
            names[n] += 1
    dups = {n: c for n, c in names.items() if c > 1}
    g.counts['techsets'] = sum(names.values())
    g.counts['dup-names'] = len(dups)
    if dups:
        g.warn('%d duplicated techset names (T6 name-pool: first body wins / '
               'family-9 shadowing)', len(dups))
        for n in list(dups)[:6]:
            g.note('dup: %s x%d', n[:56], dups[n])
    return g


def g8_decl_corpus(Z, ctx, corpus_dir, cache_path):
    g = Gate('G8 decl-corpus')
    if not corpus_dir or not os.path.isdir(corpus_dir):
        g.status = 'SKIP'
        g.note('no corpus dir')
        return g
    lib_full = None
    if cache_path and os.path.exists(cache_path):
        try:
            lib_full, lib_head, tn_decl = pickle.load(open(cache_path, 'rb'))
        except Exception:
            lib_full = None
    if lib_full is None:
        g.status = 'SKIP'
        g.note('corpus decl cache missing (%s) — build with _b47_corpus_decl_audit.py',
               cache_path)
        return g
    ts_bodies = sorted({s for (i, nm, root, s, e) in ctx.get('spans', [])
                        if root == 'MaterialTechniqueSet' and e > s})
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    u16 = lambda o: struct.unpack_from('>H', Z, o)[0]
    checked = missing = 0
    wvf_mismatch = 0
    for s in ts_bodies:
        # wvf vs corpus same-name blob
        en = Z.find(b'\x00', s + 136)
        nm = Z[s + 136:en].decode('latin1', 'replace')
        p = os.path.join(corpus_dir, nm + '.techset')
        if os.path.exists(p):
            cb = open(p, 'rb').read()
            if len(cb) >= 140 and Z[s + 4] != cb[4]:
                wvf_mismatch += 1
                g.fail('techset %s wvf 0x%02X != corpus 0x%02X', nm[:48], Z[s + 4], cb[4])
        # decls
        try:
            slots = [u32(s + 8 + i * 4) for i in range(32)]
            c = SP.Cur(Z, s + 136)
            if u32(s) == FOLLOW:
                c.cstr(160)
            for j, v in enumerate(slots):
                if v != FOLLOW:
                    continue
                t = c.o
                pc = u16(t + 6)
                cc = SP.Cur(Z, t + 8 + pc * 24)
                for i in range(pc):
                    po = t + 8 + i * 24
                    vd, vs, ps = u32(po), u32(po + 4), u32(po + 8)
                    nargs = Z[po + 12] + Z[po + 13] + Z[po + 14]
                    if vs == FOLLOW:
                        SP.parse_shader_ref(cc, 'vs')
                    if vd == FOLLOW:
                        checked += 1
                        if bytes(Z[cc.o:cc.o + VD]) not in lib_full:
                            missing += 1
                            g.fail('decl not in genuine corpus (techset %s slot %d pass %d)',
                                   nm[:40], j, i)
                        cc.skip(VD)
                    if ps == FOLLOW:
                        SP.parse_shader_ref(cc, 'ps')
                    if u32(po + 20) == FOLLOW:
                        cc.skip(nargs * 8)
                c.o = SP.parse_technique(Z, c.o)
        except Exception as ex:
            g.warn('techset walk failed @%d: %s', s, str(ex)[:50])
    g.counts['decls-checked'] = checked
    g.counts['decls-missing'] = missing
    g.counts['wvf-mismatch'] = wvf_mismatch
    return g


# proven CONSOLE name-field offsets that differ from struct_layout (GX2 texture
# header sizes differ from the PC-derived layout); (word_off, str_off or None).
# str_off = where the inline name STRING begins when the name word is FOLLOW
# (campaign-proven: techset s+136 [G7], XModel s+244 [G6], Material s+104 [G2],
# GfxImage s+324/+328 [raid genuine, _fix_texdef_images2 body+328]).
_NAME_PROVEN = {
    'GfxImage': (320, 328),      # FOLLOW @+320, name-hash @+324, string @+328
    'MaterialTechniqueSet': (0, 136),
    'XModel': (0, 244),
    'Material': (0, 104),
}


def _name_offsets():
    """root -> (name_word_off, inline_str_off or None), layout + proven overrides."""
    out = {}
    try:
        import struct_layout
        import walker as W
        Lc = struct_layout.Layout(W.HDR, console=True)
        for root in set(W.ASSET_ROOT.values()):
            try:
                st = Lc.get(root)
            except Exception:
                continue
            if not st or not st.get('fields'):
                continue
            for f in st['fields']:
                if f.get('name') in ('name', 'szInternalName', 'fontName') \
                        and f.get('offset') is not None:
                    # inline-string validation only via proven offsets; the
                    # layout's console sizes are not trustworthy per-struct
                    out[root] = (f['offset'], None)
                    break
    except Exception:
        pass
    out.update(_NAME_PROVEN)
    return out


def g9_asset_name_words(Z, ctx):
    """The skybox/lightdef class (boot 53): asset NAME fields emitted as
    rtmap-computed aliases drift -> assets register under garbage names ->
    silent DEFAULT-asset substitution. Alias payloads cannot be verified
    statically (needs the loader's allocation model, and externally-baked
    zones use maps we don't have) — the emit-time rule INLINE_ASSET_NAMES
    kills the class; this gate polices word classes + inline name content."""
    g = Gate('G9 asset-name-words')
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    offs = _name_offsets()
    if not offs:
        g.status = 'SKIP'
        g.note('struct_layout unavailable')
        return g
    for (i, nm, root, s, e) in ctx.get('spans', []):
        if e <= s:
            continue
        ent = offs.get(root)
        if ent is None:
            g.counts['no-name-field'] += 1
            continue
        woff, stroff = ent
        if s + woff + 4 > len(Z):
            g.fail('%s span @%d: name word out of range', root, s)
            continue
        w = u32(s + woff)
        if w == FOLLOW:
            g.counts['inline'] += 1
            if stroff is not None:
                t = s + stroff
                en = Z.find(b'\x00', t, t + 256)
                sb = Z[t:en] if en > 0 else b''
                if not (1 <= len(sb) <= 200
                        and all(32 <= c < 127 for c in sb)):
                    g.fail('%s @%d inline name not a clean string: %r',
                           root, s, bytes(sb[:24]))
                else:
                    g.counts['inline-validated'] += 1
        elif AL(w):
            g.counts['alias'] += 1
            g.counts['alias-' + root] += 1
        elif w == 0:
            g.counts['null'] += 1
            g.warn('%s @%d has NULL name word', root, s)
        else:
            g.fail('%s @%d malformed name word 0x%08X (not FOLLOW/alias/0)',
                   root, s, w)
    if g.counts['alias']:
        g.note('%d aliased asset names — legal in genuine zones; pipeline '
               'policy for converted maps is INLINE (alias payloads are '
               'unverifiable statically and drifted twice: skybox boot-53, '
               'lightdefs gfxtail43)', g.counts['alias'])
    return g


def _texdef_table(Z, off):
    """(defs_off, texc) of a material's texdef table; (None, texc) if the
    whole table is aliased. Mirrors _mat_texhashes' cursor (walker-correct)."""
    texc = Z[off + 72]
    if texc == 0:
        return None, 0
    tsp, ttp = be32(Z, off + 80), be32(Z, off + 84)
    c = XP.Cur(Z, off + 104)
    if be32(Z, off) in PTRS:
        c.cstr()
    if tsp in PTRS:
        c.o, _ = SP.parse_techset(Z, c.o)
    if ttp not in PTRS:
        return None, texc
    return c.o, texc


# texdef semantic hashes proven by hardware (glass front, boot 52):
_TEX_SP = 0x34ECCCB3      # specularMap
_TEX_CP = 0xA0AB1041      # colorMap
# within-material co-share hash pairs OBSERVED IN GENUINE (mp_raid_genuine,
# 2026-07-18 census: 45 shares / 8 distinct pairs — layered-material families
# legitimately bind one shared image to several layer semantics). Any pair
# OUTSIDE this set is oracle-anomalous; the (sp,cp) pair is hardware-PROVEN
# broken (glass boot 52) and FAILs.
_RAID_LEGIT_PAIRS = {
    (0xB60D1850, 0xB60D1853), (0x9434AEDD, 0x9434AEDE),
    (0xB6F4DFA4, 0xCAB2F6A7), (0x59D30D0F, 0x9434AEDE),
    (0xB60D1852, 0xB60D1853), (0xB60D1850, 0xB60D1852),
    (0x59D30D0F, 0x9434AEDD), (0xD2866321, 0xD2866322),
}


def g10_texdef_shared_targets(Z, ctx):
    """The glass white-pane class (boot 52): a material's texdef image aliases
    with DISTINCT semantic nameHashes sharing ONE target payload — a drifted
    dedup stamp binds one (wrong) holder slot to several semantics at once.
    Genuine zones DO co-share targets across layered-family semantics
    (raid: 45 shares, 8 pair classes) — so only the hardware-proven
    specularMap+colorMap pair FAILs; pairs unseen in the genuine oracle WARN
    (candidate odd-texture defects — audit against PC-side intent)."""
    g = Gate('G10 texdef-shared-target')
    mats, _ = _material_scan(Z, ctx)
    for info in mats:
        o = info['_off']
        try:
            defs, texc = _texdef_table(Z, o)
        except Exception:
            g.counts['table-walk-fail'] += 1
            continue
        if defs is None:
            if texc:
                g.counts['table-aliased'] += 1
            continue
        g.counts['tables'] += 1
        by_target = {}
        for k in range(texc):
            so = defs + k * 16
            h = be32(Z, so)
            w = be32(Z, so + 12)
            if w in PTRS:
                g.counts['slot-inline'] += 1
                continue
            if w == 0:
                g.counts['slot-null'] += 1
                continue
            if not AL(w):
                g.counts['slot-other'] += 1
                g.fail('material %s texdef[%d] image word 0x%08X malformed',
                       info['name'][:40], k, w)
                continue
            g.counts['slot-alias'] += 1
            by_target.setdefault(w, set()).add(h)
        for w, hashes in by_target.items():
            if len(hashes) < 2:
                continue
            hs = sorted(hashes)
            pairs = {(hs[i], hs[j]) for i in range(len(hs))
                     for j in range(i + 1, len(hs))}
            if (_TEX_SP, _TEX_CP) in pairs:
                g.counts['sp-cp-share'] += 1
                g.fail('material %s: specularMap+colorMap share alias 0x%08X '
                       '(glass white-pane class, boot 52 hardware-proven)',
                       info['name'][:40], w)
                continue
            novel = pairs - _RAID_LEGIT_PAIRS
            if novel:
                g.counts['novel-pair-share'] += 1
                g.warn('material %s: semantics %s share alias 0x%08X (pair '
                       'unseen in genuine oracle — candidate odd-texture '
                       'defect, verify vs PC intent)', info['name'][:40],
                       ['0x%08x' % h for h in hs], w)
            else:
                g.counts['legit-share'] += 1
    return g


# ---------------------------------------------------------------------------
def g39_43_walk_battery(Z, ctx):
    """§2b gates 39-43 (baked 2026-07-30 from relink_validation/gates.py, 0 failures on 4 genuine
    zones). One pass; every constant is derived from the zone under test.

      39 BODY COUNT      len(spans) == #{headerPtr in FOLLOW,INSERT with a known root}     [AA]
      40 WALK HONESTY    coverage AND body count asserted SEPARATELY; exception surfaced   [AB]
      41 K DERIVATION    calibrate()'s K == 63 - ((-assets_off) mod 4); no literal K       [AC]
      42 DEPENDS LAYOUT  refuse dependCount > 1 while batched/interleaved is ambiguous
      43 CONTAINER PARSE depends-aware assets_off == the reader's, else fail LOUDLY        [AM]

    Gate 40 is the one that matters most: before the bake, gate_battery.g0_round_trip asserted only
    `walk_end == len(zone)` -- on patch_mp the walk raised at entry 1527, lost 6 entries, and G0
    still reported PASS. Every gate run before the bake inherited that blind spot."""
    g = Gate('G39-43 walk battery')
    try:
        import zone_facts as ZF
        import zone_walk as ZW
    except Exception as e:
        # an import failure must fail THIS gate, not crash run() and discard G0-G10's output
        g.fail('import failed (zone_facts/zone_walk and their deps): %s: %s',
               type(e).__name__, str(e)[:120])
        return g
    try:
        f = ZF.Facts(Z)
    except Exception as e:
        g.fail('43: container unparseable: %s', e)
        return g

    # --- 43 CONTAINER PARSE ---------------------------------------------------------------
    rc = ctx.get('rc')
    if rc is not None:
        if rc.assets_off != f.assets_off:
            g.fail('43: assets_off reader 0x%x vs depends-aware 0x%x (dependCount=%d) -- '
                   'the reader does not model the container correctly',
                   rc.assets_off, f.assets_off, f.depend_count)
        else:
            g.counts['43-assets-off-ok'] = 1
    else:
        g.fail('43: no ZoneReader in ctx (G0 failed) -- container cross-check impossible')

    # --- 42 DEPENDS LAYOUT ----------------------------------------------------------------
    g.counts['42-depend-count'] = f.depend_count
    if f.depend_count > 1 and 'both' in (f.depend_layout or ''):
        g.fail('42: dependCount=%d with AMBIGUOUS layout (%s) -- refuse until batched vs '
               'interleaved is disambiguated', f.depend_count, f.depend_layout)

    # --- 39/40 WALK -----------------------------------------------------------------------
    try:
        wk = ZW.walk(Z, f)
    except Exception as e:
        g.fail('40: walk itself raised: %s: %s', type(e).__name__, str(e)[:120])
        return g
    g.counts['39-bodies'] = len(wk.spans)
    g.counts['39-expected'] = wk.expected
    if len(wk.spans) != wk.expected:
        g.fail('39: %d bodies, expected %d (#headerPtr in FOLLOW/INSERT with a root)',
               len(wk.spans), wk.expected)
    if wk.err is not None:
        g.fail('40: walk DIED at entry %d (%s): %s', *wk.err)
    if wk.end != len(Z):
        g.fail('40: coverage %.2f%% (%d B unconsumed)', wk.coverage(), len(Z) - wk.end)
    if wk.reader_fixed:
        g.warn('43: zone_walk had to correct the reader assets_off 0x%x -> 0x%x (the two '
               'readers disagree AFTER the depends[] bake -- investigate)', *wk.reader_fixed)
    ctx['walk39'] = wk

    # --- 41 K DERIVATION ------------------------------------------------------------------
    try:
        import container_refs as CR
        Zc = CR.Zone(Z)
        ex = [t for t in CR.exact_refs(Zc) if t[2] == 'Material.techniqueSet']
        if not ex:
            g.note('41: no exact Material.techniqueSet refs -> K not measurable (skipped)')
        else:
            K, residue = CR.calibrate(Zc, verbose=False)
            g.counts['41-K'] = K
            if K != f.K_pred:
                g.fail('41: K measured %d, predicted 63-pad = %d (pad %d, residue %d) -- '
                       'a literal K anywhere in the pipeline is a bug (K is per-zone)',
                       K, f.K_pred, f.pad, residue)
    except Exception as e:
        g.note('41: K not measurable (%s: %s) -> skipped', type(e).__name__, str(e)[:80])
    return g


# ---------------------------------------------------------------------------
# §2b entry 1: valid T6 ImageFormat set observed across 4860 loaded records (unanimous),
# and the measured T6->GX2 agreement rows (only pairs with a measured cross-check are
# asserted: 0x31->6 was 229/229, 0x33->0x0A was 473/473; RGBA8=5<->0x1a is the engine's
# own table row). Rows without a measured cross-check are membership-checked only.
_T6_VALID = {1, 2, 3, 4, 5, 6, 9, 10, 20}
_T6_TO_GX2_MEASURED = {5: 0x1a, 6: 0x31, 0x0A: 0x33}


def g2b_image_and_alias_law(Z, ctx):
    """§2b entries 1, 2 and 16 (baked 2026-07-30) — the cheap always-on halves.

      §2b-1  every OWNED GfxImage body+180 in the valid T6 set; measured T6<->GX2 rows agree
      §2b-2  no OWNED GfxImage with streaming(+171)==1 and streamedPartCount(+316)==0
      §2b-16 XAsset-array alias law: for every entry with headerPtr not in {FOLLOW,INSERT,0}
             and cid not in {7,48} (engine-dead words), ((hp-1)>>29)&7 == 5 and
             ((hp-1)&0x1FFFFFFF)%4 == 0.  179/179 on 20+ genuine retail zones.

    ⚠ Image gates are scoped to OWNED records (nameHash@+324 != 0): comma-prefixed extern
    references are zeroed 1x1 stubs with streaming=1/parts=0 BY DESIGN and byte-identical in
    the boot-proven key (mp_skate: 8 such). Gating them chases a phantom (§2b scope note).

    COVERAGE (baked 2026-07-30, second pass): the rule-(AT) whole-zone census
    `gfximage_census.census()` feeds this gate — TOP-LEVEL and MATERIAL-INLINE records both
    (raid 1127 owned vs the 2 the span walk sees; validated vs the retail ipak index 124/124
    and clean on both genuine oracles while finding all 18 known real defects on the
    defective gfxtail46 key). Falls back to top-level spans if the census import fails.
    ⚠ genuine facts the census measured: streaming byte takes value 2 on 28 genuine transit
    records (never assert {0,1}); genuine zones DO carry alias-named records (48 on transit);
    names may start with '*'."""
    g = Gate('G2b img+alias law')
    wk = ctx.get('walk39')
    if wk is None or not wk.ok:
        g.status = 'SKIP'
        g.note('no complete corrected walk in ctx (G39-43 must run first and pass)')
        return g
    u32be = lambda o: struct.unpack_from('>I', Z, o)[0]
    u32le = lambda o: struct.unpack_from('<I', Z, o)[0]
    recs = None
    try:
        import gfximage_census as GIC
        recs = [(r['off'], r['t6_format'], r['gx2_format'], r['streaming'],
                 r['part_count'], r['name'])
                for r in GIC.census(Z) if r['owned']]
        g.counts['img-census'] = len(recs)
    except Exception as e:
        g.warn('census unavailable (%s: %s) — falling back to TOP-LEVEL image bodies only '
               '(raid: 2 of 1127 records)', type(e).__name__, str(e)[:60])
        recs = []
        for s, e_, nm, i in wk.spans:
            if nm != 'IMAGE' or e_ - s < 328:
                continue
            if u32be(s + 324) == 0:
                g.counts['img-extern'] += 1
                continue
            recs.append((s, u32be(s + 180), u32le(s + 20), Z[s + 171],
                         u32be(s + 316) & 0xFF, None))
    for (s, enum, gx2f, streaming, pc_, nm_) in recs:
        g.counts['img-owned'] += 1
        if enum not in _T6_VALID:
            g.fail('§2b-1: GfxImage @0x%x (%s) body+180 = 0x%X not a valid T6 ImageFormat '
                   '(the (B)/(B-sub) silent-default / DXGI-leak class)', s, nm_ or '?', enum)
        else:
            gx2 = _T6_TO_GX2_MEASURED.get(enum)
            if gx2 is not None and gx2f != gx2:
                g.fail('§2b-1: GfxImage @0x%x (%s) T6 enum 0x%X disagrees with GX2 fmt 0x%X '
                       '(measured law: 0x%X)', s, nm_ or '?', enum, gx2f, gx2)
        if streaming == 1 and pc_ == 0:
            g.fail('§2b-2: GfxImage @0x%x (%s) streaming=1 with streamedPartCount=0 — can '
                   'never satisfy its demand; load gate never latches (the (B2) class)',
                   s, nm_ or '?')
    rc = ctx.get('rc')
    if rc is not None:
        for i, (cid, pc_, nm) in enumerate(rc.assets):
            hp = struct.unpack_from('>I', Z, rc.assets_off + i * 8 + 4)[0]
            if hp in (0xFFFFFFFF, 0xFFFFFFFE, 0) or cid in (7, 48):
                continue
            g.counts['hp-alias'] += 1
            if ((hp - 1) >> 29) & 7 != 5 or ((hp - 1) & 0x1FFFFFFF) % 4:
                # rule (CG) calibration 2026-08-08: GENUINE mp_raid_oracle
                # (9a54d15f) ships 2 cid-9 headerPtr aliases that are block-5
                # but MISALIGNED (entries 883/885) and boots retail — so the
                # "179/179 genuine" sample did not cover cid 9. A check that
                # fails known-good is a bug in the check: cid-9 misaligned-only
                # is WARN; every other shape stays FAIL.
                blk_ok = ((hp - 1) >> 29) & 7 == 5
                if cid == 9 and blk_ok:
                    g.counts['hp-cid9-misaligned'] += 1
                    g.warn('§2b-16: entry %d (cid 9) headerPtr 0x%08X misaligned — '
                           'form measured in GENUINE mp_raid_oracle, not failed', i, hp)
                else:
                    g.fail('§2b-16: entry %d (cid %d) headerPtr 0x%08X violates the alias law '
                           '(block 5, (v-1) 4-aligned; 179/179 genuine)', i, cid, hp)
    else:
        g.warn('§2b-16 skipped: no ZoneReader in ctx')
    return g


# ---------------------------------------------------------------------------
def g106_array_alias_natural_align(Z, ctx):
    """§2b entries G-106/G-107 (rule (CA), ADDENDUM 18) — HARDWARE-PROVEN.

    An emitted array-pointer alias must decode to a block-5 runtime offset that is a multiple of
    the NATURAL ALIGNMENT OF THE POINTED-TO ELEMENT TYPE. Not a blanket 4, and not "whatever the
    stream gives".

    ⚑ THIS IS THE REAL-HARDWARE SPAWN CRASH. PowerPC (Espresso) services misaligned INTEGER loads
    in hardware, but a misaligned FLOATING-POINT load (lfs/lfd) raises an alignment interrupt.
    __CalcSkelNonRootBones reads XModel.trans (4 x f32/bone) and XModel.baseMat (DObjAnimMat,
    8 x f32/bone) with lfs, so a misaligned alias there is fatal on console. x86 permits unaligned
    loads of every width, so CEMU CAN NEVER REPRODUCE THIS -- a green Cemu boot proves nothing
    about alignment (gate G-108 is the human rule; this is the machine half).

    Measured 2026-07-30, `?`-named XModel bodies, name at body+243, pointer members at +8:
        genuine zm_transit_original : 0 violations across ALL SIX fields
        our mp_skate cm45           : baseMat 52/73 BAD, trans 3/3 BAD, boneNames 41/73, quats 3/3
    Retail is perfect; we are not. f32 violations FAIL, sub-word violations WARN (PPC tolerates
    misaligned integer loads, so they are wrong-but-survivable and must not mask the fatal class).

    ⚠ Measure the RUNTIME offset ((v-1) & 0x1FFFFFFF), never the file offset. Genuine retail's
    inline bone arrays ARE misaligned in the FILE (transit: 165 of 420) -- that is normal and was a
    refuted hypothesis. The loader aligns as it streams; only the emitted alias can be wrong."""
    g = Gate('G106 array-alias align')
    # (field offset from body, element name, natural alignment, fatal?)
    XM_FIELDS = ((8, 'boneNames', 2, False), (12, 'parentList', 1, False),
                 (16, 'quats', 2, False), (20, 'trans', 4, True),
                 (24, 'partClassification', 1, False), (28, 'baseMat', 4, True))
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    FOLLOW = 0xFFFFFFFF
    NAME_OFF = 243
    worst = []
    for m in re.finditer(rb'\x00\?[A-Za-z0-9_\-/]{3,80}\x00', Z):
        body = m.start() + 1 - NAME_OFF
        if body < 0 or u32(body) != FOLLOW:
            continue
        nb, nrb = Z[body + 4], Z[body + 5]
        if not (0 < nb < 220 and nrb <= nb):
            continue
        for (fo, fname, need, fatal) in XM_FIELDS:
            v = u32(body + fo)
            if not (0xA0000000 <= v < 0xC0000000):
                continue
            d = v - 1
            g.counts['aliases'] += 1
            if ((d >> 29) & 7) != 5:
                g.fail('%s: alias 0x%08X is block %d, not 5', fname, v, (d >> 29) & 7)
                continue
            if (d & 0x1FFFFFFF) % need:
                nm = Z[m.start() + 1:m.end() - 1].decode('ascii', 'replace')
                g.counts['bad-' + fname] += 1
                if fatal:
                    g.counts['FATAL-f32'] += 1
                    if len(worst) < 12:
                        worst.append((nm, fname, v, (d & 0x1FFFFFFF) % need, need))
    for (nm, fname, v, rem, need) in worst:
        g.fail('%s.%s = 0x%08X -> rt%%%d = %d  (f32 array; lfs will trap on console)',
               nm[:40], fname, v, need, rem)
    nfatal = g.counts.get('FATAL-f32', 0)
    nsub = sum(v for k, v in g.counts.items() if k.startswith('bad-')) - nfatal
    if nfatal:
        g.fail('%d f32-array aliases violate natural alignment -> HARDWARE SPAWN CRASH '
               '(invisible on Cemu). Genuine retail scores 0.', nfatal)
    if nsub:
        g.warn('%d sub-word (u16/s16) array aliases misaligned — PPC tolerates these, but genuine '
               'retail scores 0, so the emitter is still wrong.', nsub)
    if not g.counts['aliases']:
        g.note('no XModel array-pointer aliases in this zone (nothing to check)')
    # rule (CG) 2026-08-08: alignment is a DETECTOR, not a census — on boot-27
    # it saw 40 of 102 wrong bone-array aliases (the other 62 were just as
    # wrong and happened to be aligned). Surface the G112 owner-identity
    # census here so this gate's own output carries the true class size.
    census = ctx.get('g112_off_owner')
    if census is not None and census > (nfatal + nsub):
        g.note('G112 owner-identity census: %d bone-array aliases WRONG; '
               'alignment alone saw %d — read G112, not this count, for class '
               'size', census, nfatal + nsub)
    return g


def g109_fx_visuals_holder_identity(Z, ctx, path):
    """G-109 (FIX 27, raid boot-25) — SELF-DERIVABLE, no genuine oracle needed.

    The loader resolves a dedup back-reference by `slot := *(target)`. So a
    Material-class FX `visuals` alias is only meaningful if its target cell is
    itself a cell that HOLDS a Material pointer. If it lands anywhere else the
    loader copies raw bytes into the Material* and the first sprite draw dies
    in R_AddCodeMeshDrawSurf (`lwz r8,0x50(mat)` -> `lwz r10,8(ts+type*4)`).

    LAW: every Material-class visuals alias payload must equal
         rt(holder_cell) + {0 | 256}
    for some Material OCCURRENCE holder cell of THIS zone (fx_oracle's census:
    FX single/array/mark FOLLOW cells, thermal words (+96) of inline bodies,
    XModel materialHandles entries incl. FX-inline et7 models, and the
    asset-array entry ptr word of bodied top-level Materials). The +256 class
    is the measured rt under-count inside the destructible/fx-chunk models
    (+256 LAW, boot-21).

    Scores on GENUINE console output, which is what makes it a law and not a
    preference: raid 242/242, dockside 317/326, transit 749/750 (the dockside
    +4 drift and transit tail are known model debts, reported as WARN).
    A blind map has no oracle — this gate still applies to it in full.
    """
    g = Gate('G109 fx-visuals-holder')
    try:
        import fx_oracle as FO
    except Exception as ex:                       # pragma: no cover
        g.note('fx_oracle unavailable (%s)', str(ex)[:60])
        return g
    cwd = os.getcwd()
    try:
        C = FO.collect_zone(path)
    except Exception as ex:
        g.fail('FX census failed: %s', str(ex)[:120])
        return g
    finally:
        os.chdir(cwd)
    enc = C['enc']
    holders = set()
    for (key, holder, body, src) in C['OCC']:
        if holder is None:
            continue
        holders.add(enc(holder))
        holders.add((enc(holder) + 256) & 0xFFFFFFFF)
    sites = [t for t in C['SITES']
             if FO._is_alias(t[1]) and (t[2] <= 6 or t[3] == 'mark')]
    bad = [t for t in sites if t[1] not in holders]
    g.counts['material-class-aliases'] = len(sites)
    g.counts['occurrence-holders'] = sum(1 for o in C['OCC'] if o[1] is not None)
    g.counts['off-holder'] = len(bad)
    ctx['g109_off_holder'] = len(bad)          # consumed by G5 (rule (CG))
    if bad:
        frac = len(bad) / max(1, len(sites))
        lvl = g.fail if frac > 0.05 else g.warn
        lvl('%d/%d Material-class FX visuals aliases land on NO Material '
            'occurrence holder -> the loader copies raw bytes into the '
            'Material* (FX_GenSpriteVerts crash class). e.g. %s',
            len(bad), len(sites),
            ['@%d=0x%08X et%d %s' % (t[0], t[1], t[2], t[3]) for t in bad[:3]])
    ctx['g109_status'] = g.status
    return g


def g111_techset_resolvable(Z, ctx):
    """G111 (boots 53/54): every Material.techniqueSet must RESOLVE to a real
    MaterialTechniqueSet the loader can actually deliver. Two fatal classes, both
    measured on live crashes in the same renderer-init function (0x02988ce8/f4,
    `lwz r10,0x18(rN)` after `lwz rN,0x50(r4)`):

      (a) FORWARD ENTRY REF (boot 53, 2 materials). techniqueSet is a block-5
          asset-array ENTRY alias, so the loader does
              *dest = *(&assetArray[i].header)
          and that header column is 0xFFFFFFFF until asset i loads. If i is not
          strictly BEFORE the asset whose span contains the material body, the
          material captures the raw 0xFFFFFFFF (the project's BACKWARD-refs-only
          law). zm_nuked shipped 2: mc/p6_zm_wood_planks_ember and
          mc/mtl_p6_zm_wood_fence_burnt, both -> idx 391, owners 19 and 355.

      (b) MISALIGNED HANDLE (boot 54, 18 words / 17 real materials). The payload
          violates the natural-alignment handle law ((h-1) & 0x1FFFFFFF) % 4 == 0.
          Cause: a PC mid-body dedup alias that relocation could not resolve
          structurally (all three zm_nuked PC targets land inside the PC GfxWorld
          interior), so it landed 1-3 bytes off. The loader still derefs it and
          copies whatever 4 bytes are there, so the field holds stable garbage
          (zm_nuked: 0xB7D13800 / 0x2F7FEC1F / 0x084A9776).

    *** WHY THE TEST IS ALIGNMENT AND **NOT** "must decode to an array entry" ***
    A techniqueSet alias has TWO legitimate forms: the asset-array ENTRY alias
    (the common one) and a mid-body STREAM dedup alias pointing at an earlier
    field that already holds the resolved pointer. Measured on genuine console
    zones, alias-form Material.techniqueSet:
        mp_raid_genuine       926 aliases   non-entry  0   misaligned 0
        mp_dockside_wiiu      939 aliases   non-entry  0   misaligned 0
        zm_transit_original  1910 aliases   non-entry 19   misaligned 0
        (ours, zm_nuked)     1301 aliases   non-entry 18   misaligned 18
    So "non-entry" is a GENUINE, BOOT-PROVEN form (transit ships 19) and failing
    on it would reject a known-good zone. Alignment is the discriminator that is
    0 on every genuine zone and exactly our defect set. Non-entry-but-aligned is
    therefore COUNTED, not failed.

    Inline techsets (techniqueSet == FOLLOW/INSERT) are legitimate and skipped --
    those are validated by the walkers, not here. NULL is skipped too.
    """
    import bisect
    g = Gate('G111 techset-resolvable')
    if _refuse_on_sim_drift(g, ctx, 'techset resolvability'):
        return g
    mats, _ = _material_scan(Z, ctx)
    rc = ctx.get('rc')
    spans = ctx.get('spans', [])
    if rc is None or not spans:
        g.fail('no asset list / spans available')
        return g
    starts = [s[3] for s in spans]

    def owner(off):
        j = bisect.bisect_right(starts, off) - 1
        if j < 0:
            return None
        (i, en, root, s, e) = spans[j]
        return (i, root) if s <= off < e else None

    for info in mats:
        ts, nm, off = info['ts'], info['name'], info['_off']
        if ts in PTRS:
            g.counts['inline-ts'] += 1
            continue
        if ts == 0:
            g.counts['null-ts'] += 1
            continue
        if not AL(ts):
            g.counts['ts-not-alias'] += 1
            g.fail('material %s techSet* = 0x%08X is neither an alias, FOLLOW/'
                   'INSERT nor NULL', nm[:44], ts)
            continue
        pay = (ts - 1) & 0x1FFFFFFF
        if pay % 4:
            # (b) the boot-54 class. Genuine scores 0 on every zone measured.
            g.counts['ts-misaligned'] += 1
            g.fail('material %s techSet* = 0x%08X payload 0x%08X is MISALIGNED '
                   '(%%4=%d) -> handle-alignment law violated; loader derefs a '
                   'wild address and copies garbage (the boot-54 class)',
                   nm[:44], ts, pay, pay % 4)
            continue
        k = _dec_entry(ctx, ts)
        if k is None:
            # aligned but not an array entry = the mid-body STREAM dedup form.
            # Genuine zm_transit_original ships 19 of these -> NOT a failure.
            g.counts['ts-stream-alias'] += 1
            continue
        g.counts['ts-entry'] += 1
        if rc.assets[k][0] != 8:
            g.counts['ts-wrong-class'] += 1
            g.fail('material %s techSet* -> asset %d is TYPE %d, not 8 '
                   '(MaterialTechniqueSet)', nm[:44], k, rc.assets[k][0])
            continue
        ow = owner(off)
        if ow is None:
            g.counts['ts-owner-unknown'] += 1
            continue
        if k >= ow[0]:
            # ⚠⚠ GUARD (added 2026-07-30, rule (AW)) — DO NOT REMOVE.
            # The forward-ref test depends ENTIRELY on the owner attribution, and the owner
            # comes from `spans`. But `loader_sim` BREAKS at GFXWORLD and FABRICATES a final
            # span running to EOF. On mp_skate that synthetic span is asset 801 covering
            # 61,250,249..101,404,242 -- 40 MB, ~40% of the zone. EVERY material above the
            # break is then falsely attributed to asset 801, so any techset asset with an
            # index > 801 reads as a "forward ref" when its real owner is asset 802..841 and
            # the reference is perfectly backward.
            # This produced 4 FALSE FAILURES on mp_skate cm46 (glass_clear_shatter_mp_model,
            # mtl_skybox_mp_skate, compass_map_mp_skate, compass_map_mp_skate_scrambled) --
            # all four of which are HARDWARE-PROVEN to render correctly. Acting on them would
            # have meant repointing four working materials.
            # Detection: the walk is incomplete when it covers fewer assets than the XAssetList
            # declares, and in that case the LAST span is synthetic.
            n_assets = len(getattr(rc, 'assets', ()) or ())
            walk_broke = bool(spans) and n_assets and (spans[-1][0] + 1) < n_assets
            if walk_broke and ow[0] >= spans[-1][0]:
                g.counts['ts-owner-UNRELIABLE'] += 1
                g.warn('material %s techSet* -> asset %d vs owner %d: owner comes from the '
                       'SYNTHETIC tail span (walk covers %d of %d assets, rule (AW)) -> owner '
                       'attribution is NOT MEASURED. Not counted as a defect.',
                       nm[:44], k, ow[0], spans[-1][0] + 1, n_assets)
                continue
            # (a) the boot-53 class. k == owner is also unsafe: the array header
            # is written only AFTER the asset finishes loading.
            g.counts['ts-forward-ref'] += 1
            g.fail('material %s techSet* -> asset %d is NOT BACKWARD of its owner '
                   'asset %d (%s) -> loader copies the unfilled 0xFFFFFFFF header '
                   '(the boot-53 class)', nm[:44], k, ow[0], ow[1])
    g.counts['UNRESOLVABLE'] = (g.counts['ts-misaligned'] + g.counts['ts-forward-ref']
                                + g.counts['ts-wrong-class'] + g.counts['ts-not-alias'])
    return g


def g112_bonearray_owner_identity(Z, ctx, path):
    """G-112 (FIX 29, raid boot-27) — SELF-DERIVABLE, no genuine oracle needed.
    The SUPERSET of G-106: G-106 tests alignment, this tests IDENTITY.

    An XModel bone-array dedup alias resolves as `slot := *(target)`, so it must
    land EXACTLY on some model's FOLLOW bone-array start. Land it elsewhere and
    the model gets another model's (or no model's) skeleton — `baseMat`
    (8 f32/bone) and `trans` (4 f32/bone) feed __CalcSkelNonRootBones, so the
    visible result is EXPLODING VERTICES, and on real hardware a misaligned
    subset also traps (rule (CA)).

    ⭐⭐ WHY THIS EXISTS ALONGSIDE G-106: on the raid boot-27 zone G-106 reported
    40 violations (24 FATAL-f32 + 16 sub-word) while the true wrong population
    was **102/102**. The other 62 aliases were just as wrong and merely happened
    to be naturally aligned. Sizing the class from the alignment count
    under-reports it by 60%.

    Genuine scores: raid 0/102 off-owner, transit 0/96.
    ⚠ genuine mp_dockside_wiiu scores 10/... because some owners are bone arrays
    inside FX-inline et7 models, which this walk does not enumerate — a REAL
    coverage gap in the walker, reported as WARN rather than hidden.
    """
    g = Gate('G112 bonearray-owner')
    try:
        import xmodel_bonearray_remint as XB
    except Exception as ex:                       # pragma: no cover
        g.note('module unavailable (%s)', str(ex)[:60])
        return g
    cwd = os.getcwd()
    try:
        _Z, rm, M = XB._walk(path)
    except Exception as ex:
        g.fail('XModel bone-array walk failed: %s', str(ex)[:120])
        return g
    finally:
        os.chdir(cwd)
    owners = set()
    for (_s, _nb, _nrb, rec) in M:
        for f, (kind, a, _sz) in rec.items():
            if kind == 'FOLLOW':
                owners.add(rm.rt(a - 64))
    align = {f: al for (_o, f, _s, al) in XB.FIELDS}
    naliases = bad = misal = 0
    ex = []
    for (s, _nb, _nrb, rec) in M:
        for f, (kind, v, _sz) in rec.items():
            if kind != 'ALIAS':
                continue
            naliases += 1
            pay = (v - 1) & 0x1FFFFFFF
            if pay % align[f]:
                misal += 1
            if pay not in owners and (pay - 256) not in owners:
                bad += 1
                if len(ex) < 3:
                    ex.append('@%d %s=0x%08X' % (s, f, v))
    g.counts['bone-aliases'] = naliases
    g.counts['off-owner'] = bad
    g.counts['misaligned'] = misal
    ctx['g112_off_owner'] = bad                # consumed by G106 (rule (CG))
    if bad:
        # STOPGAP THRESHOLD, not a measurement: genuine dockside scores 10/153
        # purely because this walk misses FX-inline et7 owners, so a bare
        # `if bad: fail` would fail a correct retail zone. Retire the fraction
        # once the walk enumerates FX-inline models (then any bad -> FAIL).
        lvl = g.fail if bad > 0.25 * max(1, naliases) else g.warn
        lvl('%d/%d XModel bone-array aliases land on NO model FOLLOW bone '
            'array -> wrong/garbage skeleton (EXPLODING VERTICES; %d of them '
            'are also misaligned, which is all G-106 can see). e.g. %s',
            bad, naliases, misal, ex)
    elif misal:
        g.warn('%d/%d bone-array aliases are on-owner but violate natural '
               'alignment (rule (CA) hardware trap)', misal, naliases)
    return g


def g114_gfxworld_surface_material(Z, ctx):
    """G114 (boot 57): every GfxWorld.dpvs.surfaces[k].material must be a
    block-5 alias obeying the 4-align handle law.

    THE CONSUMER, measured on the boot-57 dump: a 4-way-UNROLLED walk over
    dpvs.surfaces (320 = 4 x 80; GfxSurface is 80 B, `material` at +48 --
    gfxworld_probe2.CFG['wiiu']['surf_mat']). The two faulting reads at
    group+0x30 and group+0x80 are surface[0].material and surface[1].material.
    2466 iterations x 4 = 9,864 of our 9,867 surfaces -- a 3-surface remainder,
    exactly what an unrolled-by-4 loop leaves (789,120 vs 789,360 bytes = 3 x 80).

    THE TARGETS are GfxWorld.materialMemory[i].material: the distinct payloads
    form a contiguous 8-STRIDED table (T6 MaterialMemory = {Material*; int} = 8 B),
    so the loader resolves each surface via `*dest = *(&matmem[i].material)`.

    CALIBRATION (this is a FAIL-grade invariant, not a heuristic):
        mp_raid_genuine       5,281 surfaces  5,281 alias  misaligned 0   352 distinct  gap 8
        zm_transit_original  18,251 surfaces 18,251 alias  misaligned 0   959 distinct  gap 8
        ours (zm_nuked)       9,867 surfaces  9,867 alias  misaligned 9,867 (100%)  465 distinct  gap 8
    Genuine is perfect; ours violates on EVERY element, uniformly at payload%4==3
    (genuine: 0). Uniform residue + uniform 8-gap = ONE wrong base (the runtime
    address of materialMemory) replicated across all 9,867 handles -- the rt()
    linear-interior-carry family (rule X / G106 / rule CA), NOT an emit omission:
    the file holds structurally valid aliases, so a runtime NULL here is
    `*(wrong address)`, not a never-written pointer.

    ⚠ The base is located by SIGNATURE (_alias_decode.find_surfaces), not from the
    gfxworld_probe2 region walk -- that walk is exact on raid but 15.8 MB adrift on
    zm_nuked."""
    g = Gate('G114 gfxworld-surface-material')
    spans = ctx.get('spans') or []
    gws = [s for s in spans if s[2] == 'GfxWorld' and s[4] > s[3]]
    if not gws:
        g.counts['no-gfxworld'] = 1
        return g                                   # patch zones have none: PASS
    try:
        import _alias_decode as AD
    except Exception as ex:
        g.warn('locator unavailable: %s', str(ex)[:60])
        return g
    (i, en, r, s, e) = gws[0]
    base, n = AD.find_surfaces(Z, s, e)
    if base is None:
        g.warn('dpvs.surfaces not locatable by signature in the GfxWorld span '
               '-- cannot audit (this is itself suspicious)')
        return g
    a = AD.surface_material_audit(Z, base, n)
    g.counts['surfaces'] = a['total']
    g.counts['alias'] = a['alias']
    g.counts['distinct-matmem'] = a['distinct']
    for k in ('zero', 'other', 'misaligned'):
        if a[k]:
            g.counts[k] = a[k]
    g.counts['base'] = '0x%08X' % base
    if a['gap_set'] and a['gap_set'] != [8]:
        g.warn('matmem target gaps are %s, expected [8] (MaterialMemory is 8 B)',
               a['gap_set'])
    if a['zero'] or a['other']:
        g.fail('%d surface material handle(s) are NULL or not block-5 aliases',
               a['zero'] + a['other'])
    if a['misaligned']:
        g.fail('%d/%d surface material handles violate the 4-align handle law '
               '(genuine: 0/5281 raid, 0/18251 transit) -- one wrong materialMemory '
               'base replicated across the array; rt() interior-carry family, and '
               'the SAME rt defect blocks the optic-lens techsets',
               a['misaligned'], a['total'])
    return g


def g110_gfxworld_streaminfo_phase(Z, ctx):
    """G-110 (FIX 28, raid boot-26) — SELF-DERIVABLE, no genuine oracle needed.

    The console GfxWorld struct is 1096 B and its streamInfo aabbTree node
    array starts IMMEDIATELY at body+1096 (the loader assigns the FOLLOW at
    body+24 from the cursor sitting exactly there). Anything between the two
    shifts every node, and `R_StreamUpdateAabbNode_r` recurses on the shifted
    childCount/firstChild until it walks off the array and derefs a garbage
    leafRef.

    Two checks, both from this zone alone:
      * body[1076:1096] == 4x FOLLOW + u32 0 (the MaterialVertexShader handle
        quad + scalar — the struct-tail signature that pins the 1096 size);
      * the node array at body+1096 must be self-consistent: every node's
        firstChild+childCount <= treeCount, and every leaf's
        firstItem+itemCount <= leafRefCount. A 20-byte phase error fails this
        immediately (boot-26: node[0] childCount 26308 vs treeCount 785).

    Node fields: +0x20 streamDist2 (f32) · +0x24 firstItem u16 ·
    +0x26 itemCount u16 · +0x28 firstChild u16 · +0x2a childCount u16 ·
    +0x2c a SECOND per-leaf count.
    ⚠ MEASURED, and it corrects a natural misreading of the disassembly:
    `R_StreamUpdateAabbNode_r`'s leaf loop reads its count from **+0x2c**, not
    +0x26 — but +0x26 is still the field that PARTITIONS the leafRef array
    (genuine raid: 588 leaves, +0x26 sums to 9751 == leafRefCount as an exact
    contiguous partition; +0x2c sums to 4668 == the smodel count and does NOT
    partition). So gfxworld_streaminfo.py's fi/ic/fc/cc pin is right and +0x2c
    is a distinct sub-count the streamer walks. Both are bounds-checked below;
    neither is assumed to be the other.
    """
    g = Gate('G110 gfxworld-streaminfo')
    gw = [(s, e) for (i, nm, root, s, e) in ctx.get('spans', [])
          if root == 'GfxWorld' and e > s]
    if len(gw) != 1:
        g.note('%d GfxWorld spans — nothing to check', len(gw))
        return g
    b, e = gw[0]
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    u16 = lambda o: struct.unpack_from('>H', Z, o)[0]
    STRUCT = 1096
    tail = Z[b + 1076:b + STRUCT]
    if tail != b'\xff' * 16 + b'\x00' * 4:
        g.fail('struct-tail signature at body+1076 is %s, not 4x FOLLOW + 0 — '
               'the 1096 B struct pin is wrong or the body is mis-sized',
               tail.hex())
        return g
    tc, rc = u32(b + 20), u32(b + 28)
    g.counts['treeCount'] = tc
    g.counts['leafRefCount'] = rc
    if u32(b + 24) != 0xFFFFFFFF or u32(b + 32) != 0xFFFFFFFF:
        g.fail('streamInfo aabbTrees/leafRefs are not FOLLOW (%08X/%08X)',
               u32(b + 24), u32(b + 32))
        return g
    nb = b + STRUCT
    if nb + tc * 48 + rc * 4 > e:
        g.fail('streamInfo region (%d nodes + %d refs) overruns the GfxWorld '
               'span', tc, rc)
        return g
    badc = badi = 0
    leafspan = 0
    for k in range(tc):
        n = nb + k * 48
        fc, cc = u16(n + 0x28), u16(n + 0x2a)
        fi, ic, sc = u16(n + 0x24), u16(n + 0x26), u16(n + 0x2c)
        if cc and fc + cc > tc:
            badc += 1
        if not cc:
            leafspan += ic
            if fi + ic > rc or fi + sc > rc:
                badi += 1
    g.counts['child-range-bad'] = badc
    g.counts['leaf-item-range-bad'] = badi
    g.counts['leaf-item-total'] = leafspan
    if leafspan != rc:
        g.warn('leaf itemCount(+0x26) totals %d, leafRefCount is %d — the leaf '
               'ranges do not partition the leafRef array (genuine does)',
               leafspan, rc)
    if badc or badi:
        n0 = nb
        g.fail('streamInfo node array is out of phase: %d nodes with '
               'firstChild+childCount > treeCount, %d leaves with '
               'firstItem+itemCount > leafRefCount (node[0] firstChild=%d '
               'childCount=%d itemCount=%d vs treeCount=%d) -> runaway '
               'recursion in R_StreamUpdateAabbNode_r',
               badc, badi, u16(n0 + 0x28), u16(n0 + 0x2a), u16(n0 + 0x2c), tc)
    return g


def g113_xsurface_buffer_owner(Z, ctx, path):
    """G-113 (FIX 30, raid boot-28) — SELF-DERIVABLE. The census G6 never was.

    A dedup'd XSurface back-references an earlier surface's geometry buffers.
    Each alias must land on some surface's FOLLOW buffer OF THE SAME FIELD.
    `verts0` is the vertex buffer and `triIndices` the index buffer, so a
    drifted alias makes the GPU fetch arbitrary bytes as positions ->
    VERTEX EXPLOSION.

    ⚠ G6 `xsurface-dedup` PASSES on a zone where all 848 of these are wrong,
    and even reports `dedup-surfs=212`. It counts the class and checks the
    skinned-null rule; it never checks the TARGETS. Rule (CG).

    Genuine: raid 0/848, transit 0/672, dockside 0/1592 off-owner.
    """
    g = Gate('G113 xsurface-buffer')
    try:
        import xsurface_buffer_remint as XS
    except Exception as ex:                       # pragma: no cover
        g.note('module unavailable (%s)', str(ex)[:60])
        return g
    cwd = os.getcwd()
    try:
        _Z, rm, S = XS._surfaces(path)
    except Exception as ex:
        g.fail('XSurface walk failed: %s', str(ex)[:120])
        return g
    finally:
        os.chdir(cwd)
    owners = {}
    for (_b, rec) in S:
        for f, t in rec.items():
            if t[0] == 'FOLLOW':
                owners.setdefault(f, set()).add(rm.rt(t[1] - 64))
    n = bad = 0
    ex = []
    for (b, rec) in S:
        for f, t in rec.items():
            if t[0] != 'ALIAS':
                continue
            n += 1
            pay = (t[1] - 1) & 0x1FFFFFFF
            o = owners.get(f, ())
            if pay not in o and (pay - 256) not in o:
                bad += 1
                if len(ex) < 3:
                    ex.append('@%d %s=0x%08X' % (b, f, t[1]))
    g.counts['buffer-aliases'] = n
    g.counts['off-owner'] = bad
    ctx['g113_off_owner'] = bad                # consumed by G6 (rule (CG))
    if bad:
        g.fail('%d/%d XSurface buffer aliases land on NO surface FOLLOW buffer '
               'of the same field -> the GPU fetches arbitrary bytes as vertex '
               'positions (VERTEX EXPLOSION). e.g. %s', bad, n, ex)
    return g


def run(path, spans_cache=None, corpus=None, json_out=None):
    Z = open(path, 'rb').read()
    ctx = {}
    gates = []
    gates.append(g0_structural(Z, ctx))
    gates.append(g1_walk_coverage(Z, ctx, path, spans_cache))
    # rule (CG) 2026-08-08: the identity censuses (G109/G112/G113) are COMPUTED
    # before G5/G6/G106 so those gates can consume their results via ctx and
    # refuse to present counters as health. PRINT order below is unchanged.
    _g109 = g109_fx_visuals_holder_identity(Z, ctx, path)
    _g112 = g112_bonearray_owner_identity(Z, ctx, path)
    _g113 = g113_xsurface_buffer_owner(Z, ctx, path)
    gates.append(g2_material_census(Z, ctx))
    gates.append(g3_techset_bindings(Z, ctx))
    gates.append(g4_demand_subset(Z, ctx))
    gates.append(g5_fx_visuals(Z, ctx))
    gates.append(g6_xsurface_dedup(Z, ctx))
    gates.append(g7_ts_name_dups(Z, ctx))
    corpus = corpus or os.path.abspath(os.path.join(_HERE, '..', 'wiiu_ref', 'techset_corpus'))
    cache = os.path.join(_HERE, '_b47_corpus_decl_lib.pkl')
    gates.append(g8_decl_corpus(Z, ctx, corpus, cache))
    gates.append(g9_asset_name_words(Z, ctx))
    gates.append(g10_texdef_shared_targets(Z, ctx))
    gates.append(g39_43_walk_battery(Z, ctx))
    gates.append(g2b_image_and_alias_law(Z, ctx))
    gates.append(g106_array_alias_natural_align(Z, ctx))
    gates.append(_g109)
    gates.append(g111_techset_resolvable(Z, ctx))
    gates.append(g114_gfxworld_surface_material(Z, ctx))
    gates.append(g110_gfxworld_streaminfo_phase(Z, ctx))
    gates.append(_g112)
    gates.append(_g113)

    print('=' * 78)
    print('ZONE GATES: %s' % path)
    print('=' * 78)
    fails = 0
    for g in gates:
        mark = {'PASS': 'PASS', 'WARN': 'WARN', 'FAIL': '*FAIL*', 'SKIP': 'skip'}[g.status]
        cs = '  '.join('%s=%s' % (k, v) for k, v in sorted(g.counts.items()))
        print('%-26s %-7s %s' % (g.name, mark, cs))
        for (lvl, msg) in g.details:
            print('      [%s] %s' % (lvl, msg))
        if getattr(g, 'dropped', 0):
            print('      [....] %d further detail line(s) SUPPRESSED by the %d-line cap '
                  '-- counters above are exact; raise Gate(cap=) to see them'
                  % (g.dropped, g.cap))
        if g.status == 'FAIL':
            fails += 1
    print('-' * 78)
    print('RESULT: %d gate(s) FAILED' % fails)
    if json_out:
        rep = [{'gate': g.name, 'status': g.status, 'counts': dict(g.counts),
                'details': g.details} for g in gates]
        json.dump(rep, open(json_out, 'w'), indent=1)
        print('report -> %s' % json_out)
    return fails


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('zone')
    ap.add_argument('--spans-cache')
    ap.add_argument('--corpus')
    ap.add_argument('--json')
    a = ap.parse_args()
    sys.exit(run(a.zone, a.spans_cache, a.corpus, a.json))


# ---------------------------------------------------------------------------------------------
# G-DIFF — differential gate: an EDITED zone against the zone it was built FROM.
#
# Added 2026-08-07 on the map-conversion audit's recommendation, so the rule lives in the shared
# battery rather than GUI-side.
#
# ⚠⚠ WHY THIS EXISTS: an ABSOLUTE "every block-5 alias must resolve inside b5" check is
# MEANINGLESS on a retail zone. Most of patch_mp is unmodelled binary (images, sounds, GX2 state)
# and alias-SHAPED words are everywhere: run that check on a PRISTINE, UNTOUCHED patch_mp and it
# reports 793,583 dangling aliases, the first at offset 0xB -- inside the 40-byte container
# header. Even after filtering every modelled data payload the residue is 745,239.
#
# The PHANTOM LAW therefore applies to VERIFICATION, not just to the relink. What IS decidable is
# whether OUR EDIT made things worse, measured against the input we started from.
#
# ⭐ GENERAL RULE THIS ENCODES: calibrate a new check against known-good input before trusting it.
# A check that fails on pristine retail is a bug in the check, not a finding about the zone.

def g_diff_against_baseline(base_Z, new_Z, expect_delta=0):
    """Compare an edited zone with its baseline. Returns a Gate.

        D1  container asset_count unchanged
        D2  size moved by exactly expect_delta
        D3  the structural walk still completes, with the same verdict
        D4  the dangling-alias residue did not INCREASE
    """
    g = Gate('G-DIFF baseline')
    try:
        import zone_facts as _ZF
        import zone_walk as _ZW
    except Exception as ex:
        g.fail('cannot import walk/facts: %s: %s', type(ex).__name__, ex)
        return g

    try:
        fa, fb = _ZF.Facts(base_Z), _ZF.Facts(new_Z)
    except Exception as ex:
        g.fail('container parse failed: %s: %s', type(ex).__name__, ex)
        return g

    g.counts['assets'] = fb.asset_count
    if fa.asset_count != fb.asset_count:
        g.fail('D1 asset_count %d -> %d', fa.asset_count, fb.asset_count)
    else:
        g.note('D1 asset_count unchanged at %d', fb.asset_count)

    got = len(new_Z) - len(base_Z)
    if got != expect_delta:
        g.fail('D2 size moved %+d, expected %+d', got, expect_delta)
    else:
        g.note('D2 size %+d as expected', got)

    try:
        wa, wb = _ZW.walk(base_Z), _ZW.walk(new_Z)
        if not wb.ok:
            g.fail('D3 walk no longer completes: %s', wb.verdict())
        elif wa.ok and len(wa.spans) != len(wb.spans):
            g.fail('D3 body count changed %d -> %d', len(wa.spans), len(wb.spans))
        else:
            g.note('D3 walk %d bodies, EOF exact', len(wb.spans))
        g.counts['bodies'] = len(wb.spans)
    except Exception as ex:
        g.fail('D3 walk raised: %s: %s', type(ex).__name__, ex)

    da, db = _dangling_residue(base_Z, fa), _dangling_residue(new_Z, fb)
    g.counts['residue'] = db
    if db > da:
        g.fail('D4 dangling-alias residue INCREASED %d -> %d (+%d)', da, db, db - da)
    else:
        g.note('D4 residue %d -> %d (not increased)', da, db)
    return g


def _dangling_residue(Z, f):
    """Alias-shaped words outside modelled payloads whose target is past block 5.

    Byte-granular (console records are not 4-aligned), skipping the container prefix and every
    inline {name,len,buffer} payload. The absolute number is NOISE -- only its change matters.
    """
    import struct as _s
    ALO, AHI, BV = 0xA0000000, 0xC0000000, 5
    skip = []
    try:
        import re as _re
        for m in _re.finditer(b'\xff\xff\xff\xff', Z):
            o = m.start()
            if Z[o + 8:o + 12] != b'\xff\xff\xff\xff':
                continue
            ln = _s.unpack_from('>I', Z, o + 4)[0]
            if not (0 < ln <= 0x4000000):
                continue
            e = Z.find(b'\x00', o + 12)
            if e < 0 or e - (o + 12) > 192:
                continue
            b0 = e + 1
            if b0 + ln <= len(Z):
                skip.append((b0, b0 + ln))
    except Exception:
        pass
    skip.sort()
    bad, si, off, n, b5 = 0, 0, f.assets_end, len(Z), f.b5
    while off < n - 3:
        while si < len(skip) and skip[si][1] <= off:
            si += 1
        if si < len(skip) and skip[si][0] <= off < skip[si][1]:
            off = skip[si][1]
            continue
        v = _s.unpack_from('>I', Z, off)[0]
        if ALO <= v < AHI and ((v >> 29) & 7) == BV and (v & 0x1FFFFFFF) - 1 > b5:
            bad += 1
        off += 1
    return bad
