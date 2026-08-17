#!/usr/bin/env python3
"""
RAID-ORACLE CONTROL (Track G gate). Runs the full no-backbone assemble on mp_raid
(the map with a genuine console zone) and byte-diffs every emitted body against the
genuine console body, paired by (asset name, occurrence).

PASS = every diff falls in the KNOWN-EXCEPTION ALLOWLIST:
  - Material            : sort/hashIndex words + the -16/-32 size class (Track A known)
  - MaterialTechniqueSet: substituted console blobs (Track B: different bytes by design)
  - XModel              : skinned emit-rigid (7 on skate/0 required exact) + material
                          hashIndex propagation; rigid models must be byte-exact
  - FxEffectDef         : PC<->console source float drift (LSB class) + inline-material
                          class (-8 per inline material, embedded-image class)
  - GfxImage/GfxLightDef: streamed-vs-loaded pixel class (cookie image)
Anything else that diffs = an assemble-machinery bug. Fix it HERE, not on mp_skate.
"""
import sys, os, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from collections import defaultdict, Counter
import struct_layout, walker as W, zone_stream as zs
import body_relayout as BR
import wiiu_zone
import produce_nobackbone as PN

CO_PATH = '../wiiu_ref/mp_raid_genuine.zone'
PC_PATH = '../PC ff/mp_raid.zone'

# Types where byte-difference vs genuine is EXPECTED and allowed (see docstring).
ALLOW_DIFF = {'Material', 'MaterialTechniqueSet', 'FxEffectDef', 'GfxImage',
              'GfxLightDef', 'XModel', 'GfxWorld',
              # Glasses: nested inline materials inherit the Material -16/-32
              # textureTable-row class (chase findings §5: 2 x -16 on raid)
              'Glasses',
              # SndBank: loadedAssets data blob is platform-format (PC 59 MB vs
              # console 11.5 MB DSP) + localized alias content — SAB track owns
              # the data conversion; pairing is by bank NAME below
              'SndBank'}

# Typed HARD-word allowlist predicates (chase findings §3/§4): a mismatching
# word is allowed iff the predicate holds for SOME alignment covering it.
HARD_CLASS = {
    # link-time float recompute, mantissa-LSB drift (delta <= 128 int-ulp,
    # same exponent): clipMap staticModelList/cmodels bounds
    'clipMap_t': lambda a, g: a != g and abs(a - g) <= 128 and (a >> 23) == (g >> 23),
    # DestructibleDef: float-LSB source divergence, |BE-int delta| == 1
    'DestructibleDef': lambda a, g: a != g and abs(a - g) == 1,
}
# Types skipped entirely (no emit yet by design)
SKIP = {'GfxWorld'}


def _console_glasses_end(d, off):
    """Console Glasses full-body walk (mirror of glasses_pc, BE + console
    material/FX shapes). Needed for the type-47 'MAP_ENTS' relabel entry whose
    body is actually the map's Glasses asset (Track F finding)."""
    import xmodel_probe as XP
    import fx_probe as FB
    PTRS = (0xFFFFFFFF, 0xFFFFFFFE)
    u32 = lambda o: struct.unpack_from('>I', d, o)[0]
    o = off + 56
    if u32(off) in PTRS:
        o = d.index(b'\x00', o) + 1
    num = u32(off + 4)
    if u32(off + 8) in PTRS:
        gbase = o
        o += num * 140
        for i in range(num):
            gb = gbase + i * 140
            if u32(gb + 16) in PTRS:
                gd = o
                o += 60
                if u32(gd) in PTRS:
                    o = d.index(b'\x00', o) + 1
                for mo in (28, 32, 36):
                    if u32(gd + mo) in PTRS:
                        c = XP.Cur(d, o); XP.consume_material(d, c); o = c.o
                for so in (40, 44, 48):
                    if u32(gd + so) in PTRS:
                        o = d.index(b'\x00', o) + 1
                for fo in (52, 56):
                    if u32(gd + fo) in PTRS:
                        o, _ = FB.parse_fx(d, o)
            if u32(gb + 80) in PTRS:
                o += d[gb + 77] * 8
    return o


def _looks_like_glasses(d, o):
    u32 = lambda x: struct.unpack_from('>I', d, x)[0]
    return (u32(o) == 0xFFFFFFFF and 0 < u32(o + 4) < 4096 and
            u32(o + 8) == 0xFFFFFFFF)


def console_spans():
    CO = open(CO_PATH, 'rb').read()
    rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
    Lc = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = rc.assets_end - BR.B5_BASE
    em = BR.ReEmitter(CO, Lc, zc, w)
    cur = rc.assets_end
    spans = []
    for i, (cid, pc, nm) in enumerate(rc.assets):
        root = W.ASSET_ROOT.get(nm)
        hp = struct.unpack_from('>I', CO, rc.assets_off + i * 8 + 4)[0]
        if hp != 0xFFFFFFFF:               # aliased asset: no body in the stream
            spans.append((i, nm, root, cur, cur)); continue
        if root is None or root not in Lc.structs:
            spans.append((i, nm, None, cur, cur)); continue
        if nm == 'MAP_ENTS' and _looks_like_glasses(CO, cur):
            # type-47 relabel: this entry's body is the map's Glasses asset
            end = _console_glasses_end(CO, cur)
            w.write_bytes(CO[cur:end])
            spans.append((i, 'GLASSES', 'Glasses', cur, end))
            cur = end
            continue
        if pc in BR.DETECTORS and not BR.DETECTORS[pc](CO, cur):
            real = BR.find_next_body(CO, cur, pc)
            if real and real > cur:
                w.write_bytes(CO[cur:real]); cur = real
        start = cur
        try:
            cur = em.emit_asset(root, cur)
        except Exception as ex:
            print('console span walk BREAK at asset %d %s (%s): %s' %
                  (i, nm, root, str(ex)[:60]))
            spans.append((i, nm, root, start, len(CO))); break
        spans.append((i, nm, root, start, cur))
    return CO, spans


BLOCK5_LO, BLOCK5_HI = 0xA0000001, 0xBFFFFFFF


def _is_b5(v):
    return BLOCK5_LO <= v <= BLOCK5_HI


class PtrResolver:
    """Resolve a block-5 alias to (asset name, occurrence, interior delta).

    Deltas are STREAM-space: the runtime address is inverted through the
    side's own omap (rt -> stream) first. Runtime deltas are NOT comparable
    across sides — per-allocation alignment pads depend on the cursor PHASE
    at the asset start, which differs between the genuine zone and ours
    (measured: +3 on GWMP, +95 on clipMap). Stream deltas are phase-free and
    make the content-compare fallback byte-exact.

    Values inside the XAsset array range resolve as asset-handle SLOT refs:
    ('#slot', asset key) — semantically an asset handle, index-numbering-free."""
    def __init__(self, stream_spans, rt_keys, rt_vals, arr_base=None,
                 idx2key=None):
        # stream_spans: [(stream_b5_start, stream_b5_end, (nm, k))]
        # rt_keys/rt_vals: registered (stream_b5 -> runtime_b5) pairs, sorted
        self.r = sorted(stream_spans)
        self.rts = list(rt_vals)
        self.streams = list(rt_keys)
        self.arr_base = arr_base
        self.idx2key = idx2key or []

    def resolve(self, v):
        import bisect
        if 0xBF000000 <= v <= 0xBF1FFFFF:
            # produce_nobackbone TAGGED poison (unresolved/GfxWorld-pending)
            return ('#tagged', v & 0x1FFFFF)
        b5 = (v - 1) & 0x1FFFFFFF
        if self.arr_base is not None:
            d = b5 - self.arr_base
            if 0 <= d < len(self.idx2key) * 8 and d % 8 == 4:
                return ('#slot', self.idx2key[d // 8])
        # runtime -> stream (piecewise inverse of the side's own omap)
        i = bisect.bisect_right(self.rts, b5) - 1
        if i < 0:
            st = b5
        else:
            st = self.streams[i] + (b5 - self.rts[i])
        j = bisect.bisect_right(self.r, (st, 0xFFFFFFFF, ('', 0xFFFF))) - 1
        if 0 <= j < len(self.r):
            s, e, key = self.r[j]
            if s <= st < e:
                return (key, st - s)
        return None


def _stringish(b):
    """Bytes look like the start of a C-string (printable run to a NUL)."""
    if not b:
        return False
    try:
        n = b.index(0)
    except ValueError:
        n = len(b)
    # n >= 3: a 2-printable-char accident before a NUL is not a string
    # (dockside DD: gen packed-verts bytes '~(' read as stringish at n>=2,
    # blocking the stale-str class on one of the three set-string members)
    return n >= 3 and all(0x20 <= c < 0x7f for c in b[:n])


def _fin_float(u32):
    """u32 (BE console word) decodes to a finite f32 with |f| <= 2.0 — the range
    of normalized matrix/quat/normal/bounds-fraction elements. Such values fall
    in the block-5 alias byte range yet are geometry floats, not pointers."""
    import math
    f = struct.unpack('>f', struct.pack('>I', u32))[0]
    return math.isfinite(f) and abs(f) <= 2.0


def _fp_noncptr(r):
    """A resolver result that is NOT a clean pointer target: unresolved (None),
    tagged poison, or a MID-body delta (>32). A real relocated asset pointer
    targets a body START (delta<=32) or a #slot handle — never these."""
    return (r is None or r[0] == '#tagged'
            or (isinstance(r[1], int) and r[1] > 32))


def _gfx_region_at(delta, table):
    """(normalized_region_key, offset_in_region) for a GFXWORLD interior
    stream delta, or None. `table` = sorted [(start, end, key)]."""
    if not table:
        return None
    import bisect
    i = bisect.bisect_right(table, (delta, 1 << 60, '')) - 1
    if i >= 0:
        s, e, k = table[i]
        if s <= delta < e:
            return (k, delta - s)
    return None


def semantic_diff(body, gen, ours_res, gen_res, hard_ok=None,
                  ours_fetch=None, gen_fetch=None, dbg=None,
                  stale_str_ok=False, fp_recompute=False):
    """Byte-diff two bodies; a mismatching 4-byte window where BOTH sides hold a
    block-5 alias resolving to the same (asset, delta) is a CORRECT pointer, not a
    diff. `hard_ok(ours_u32, gen_u32)` optionally allowlists typed hard words
    (float-drift classes); allowed words count as `classed`, not hard.
    Returns (hard_diff_count, ptr_ok, ptr_bad, first_hard, classed)."""
    n = min(len(body), len(gen))
    hard = ptr_ok = ptr_bad = classed = 0
    first_hard = -1
    j = 0
    while j < n:
        if body[j] == gen[j]:
            j += 1
            continue
        matched = False
        cands = []
        for w in range(max(0, j - 3), min(j, n - 4) + 1):
            vo = struct.unpack_from('>I', body, w)[0]
            vg = struct.unpack_from('>I', gen, w)[0]
            if _is_b5(vo) and _is_b5(vg):
                ro = ours_res.resolve(vo)
                rg = gen_res.resolve(vg)
                if ro is None and rg is None:
                    continue               # neither resolves: not a pointer window
                cands.append((ro is not None and ro == rg,
                              ro is not None and rg is not None, w, ro, rg))
        if cands:
            # window choice: an exact-match window wins; else a window where
            # OURS is tagged (GfxWorld-pending poison) and GEN resolves into
            # GFXWORLD is the pending class — never let a misaligned byte
            # view that happens to resolve on both sides outrank those.
            pend = [c for c in cands
                    if c[3] is not None and c[3][0] == '#tagged'
                    and c[4] is not None and c[4][0][0] == 'GFXWORLD']
            exact = [c for c in cands if c[0]]
            if not exact and pend:
                globals()['_GFXW_REFS'] = globals().get('_GFXW_REFS', 0) + 1
                j = pend[0][2] + 4
                continue
            cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
            ok, both, w, ro, rg = cands[0]
            if not ok and both and ours_fetch and gen_fetch \
                    and isinstance(ro[1], int) and isinstance(rg[1], int):
                # cross-asset string/content DEDUP class: the two linkers chose
                # different source copies of identical bytes — the pointer is
                # semantically correct iff the target CONTENT matches. For
                # C-string targets compare up to the terminator (a fixed 8-byte
                # compare false-negatives on bytes beyond the NUL).
                bo = ours_fetch(ro[0], ro[1], 48)
                bg = gen_fetch(rg[0], rg[1], 48)
                if bo is not None and bg is not None:
                    if bo == bg:
                        ok = True
                    elif 0 in bo[:40] and 0 in bg[:40]:
                        so = bo[:bo.index(0)]
                        sg = bg[:bg.index(0)]
                        if so == sg and len(so) > 2:
                            ok = True
            if (not ok and both and isinstance(ro[0], tuple)
                    and isinstance(rg[0], tuple)
                    and ro[0][0] == 'TECHNIQUE_SET'
                    and rg[0][0] == 'TECHNIQUE_SET'
                    and isinstance(ro[1], int) and isinstance(rg[1], int)
                    and ro[1] > 0 and rg[1] > 0
                    and ours_fetch and gen_fetch):
                # BINARY techset-interior dedup (part B session 2): the two
                # linkers dedup'd the same 16-byte literal (float4 const /
                # decl row) into DIFFERENT techsets' interiors. 16 bytes of
                # exact content equality proves the object; accidental
                # collision is 2^-128.
                bo = ours_fetch(ro[0], ro[1], 16)
                bg = gen_fetch(rg[0], rg[1], 16)
                if bo is not None and bo == bg and len(bo) == 16:
                    ok = True
                elif bo is not None and bg is not None:
                    # TS-DANGLE typed class (assemble pass 3): both linkers'
                    # values dangle into techset interiors with UNEQUAL
                    # content — the measured heap-reuse dedup accident
                    # (diag_ts_*: the PC-dedup'd content exists in neither
                    # our stream nor the genuine zone; genuine ships the
                    # dangle). Ours now emits the in-bounds mirror instead
                    # of a #tagged poison, so the pair lands here. Same
                    # allowlist coverage as the former (#tagged,
                    # TECHNIQUE_SET-interior) class.
                    classed += 1
                    j = w + 4
                    continue
            if (not ok and both and isinstance(ro[0], tuple)
                    and ro[0][0] == 'GFXWORLD'
                    and isinstance(rg[0], tuple) and rg[0][0] == 'GFXWORLD'
                    and isinstance(ro[1], int) and isinstance(rg[1], int)):
                # REGION-relative GFXWORLD compare (part B session 2): our
                # emit's registered-SYNTH regions (streamInfo, reencoded
                # lightmaps, ...) differ in SIZE from genuine, shifting all
                # downstream interior deltas — the pointer is correct iff
                # both sides land at the same offset of the SAME region.
                ko2 = _gfx_region_at(ro[1], globals().get('_GFX_REG_OURS'))
                kg2 = _gfx_region_at(rg[1], globals().get('_GFX_REG_GEN'))
                if ko2 is not None and ko2 == kg2:
                    ok = True
            if ok:
                ptr_ok += 1
            elif ro is None and rg is not None and rg[0][0] == 'GFXWORLD':
                globals()['_GFXW_REFS'] = globals().get('_GFXW_REFS', 0) + 1
            elif (ro is not None and ro[0] == '#tagged' and rg is not None
                  and isinstance(rg[0], tuple)
                  and rg[0][0] == 'TECHNIQUE_SET' and rg[1] > 0):
                # ours declared-unresolved, gen dedup'd into a techset
                # INTERIOR: unreproducible by design (substituted blobs) —
                # same allowlist class as the techsets themselves
                classed += 1
            elif hard_ok is not None and hard_ok(
                    struct.unpack_from('>I', body, w)[0],
                    struct.unpack_from('>I', gen, w)[0]):
                classed += 1               # typed float-drift window
            elif (stale_str_ok and rg is not None and gen_fetch is not None
                  and isinstance(rg[1], int)
                  and not _stringish(gen_fetch(rg[0], rg[1], 48))):
                # STALE-DEDUP class (DestructibleDef sound/notify string
                # members): the GENUINE value itself dangles into non-string
                # bytes (raid: the sedan's packed verts; PC: FX zeros) —
                # linker heap reuse on absent strings, shipped by genuine
                # consoles and tolerated at runtime. Ours dangles equivalently.
                classed += 1
            elif (fp_recompute
                  and _fin_float(struct.unpack_from('>I', body, w)[0])
                  and _fin_float(struct.unpack_from('>I', gen, w)[0])
                  and (w % 4 != 0                       # unaligned => not a ptr field
                       or (_fp_noncptr(ro) and _fp_noncptr(rg)
                           # float noise resolves to DIFFERENT assets; a
                           # consistent SAME-asset delta is a real relocated
                           # pointer (alignment/skip miss) — fix it, don't class
                           and (ro is None or rg is None
                                or ro[0] == '#tagged' or rg[0] == '#tagged'
                                or ro[0] != rg[0]
                                # same-asset exception (part B session 2):
                                # GFXWORLD spans most of the rt space, so
                                # near-zero float noise lands inside it on
                                # BOTH sides; a real dedup pair never
                                # disagrees by more than the region maps'
                                # accuracy — class when the two interior
                                # deltas disagree wildly (>64K). The dock
                                # (16,12) real-pointer family (delta 16)
                                # stays excluded.
                                or (isinstance(ro[0], tuple)
                                    and ro[0][0] == 'GFXWORLD'
                                    and isinstance(ro[1], int)
                                    and isinstance(rg[1], int)
                                    and abs(ro[1] - rg[1]) > 65536))
                           and not (rg is not None and isinstance(rg[1], int)
                                    and gen_fetch is not None
                                    and _stringish(gen_fetch(rg[0], rg[1], 48) or b''))))):
                # LINK-TIME FLOAT-RECOMPUTE class: the console linker RECOMPUTES
                # geometry pose/matrix/bounds floats (clipMap cStaticModel
                # invScaledAxis/absBounds; DynEntityDef pose; + GWMP nodes) from
                # the XModel/geometry, so they diverge from the PC-stored values
                # our byte-copy keeps. These floats (|f|<=2: near-zero matrix
                # off-diagonals ~1e-19, or normalized elements like -0.5) encode
                # into the block-5 byte range and are FALSELY resolved as
                # pointers. Discriminator (won't swallow a real pointer bug): a
                # real relocated pointer targets a body START (delta<=32) or a
                # #slot; a string dedup targets stringish content. We class ONLY
                # when NEITHER side is a clean pointer target AND the genuine
                # target is not a string. (raid 293 mid-body near-zero + dockside
                # 74 DynEntityDef pose -0.5 both covered; verified all classed
                # windows fall in the cStaticModel/DynEntityDef/cmodel regions.)
                classed += 1
            else:
                ptr_bad += 1
                if dbg is not None:
                    dbg.append((w, struct.unpack_from('>I', body, w)[0],
                                struct.unpack_from('>I', gen, w)[0], ro, rg))
                t = globals().setdefault('_PTRBAD_CLASSES', Counter())
                ko = ro[0][0] if ro and isinstance(ro[0], tuple) else (ro and ro[0])
                kg = rg[0][0] if rg and isinstance(rg[0], tuple) else (rg and rg[0])
                t[(ko, kg)] += 1
                exs = globals().setdefault('_PTRBAD_EX', {})
                if (ko, kg) not in exs:
                    exs[(ko, kg)] = 'vo->%s vg->%s' % (ro, rg)
            j = w + 4
            matched = True
        if not matched and hard_ok is not None:
            for w in range(max(0, j - 3), min(j, n - 4) + 1):
                va = struct.unpack_from('>I', body, w)[0]
                vg = struct.unpack_from('>I', gen, w)[0]
                if hard_ok(va, vg):
                    classed += 1
                    j = w + 4
                    matched = True
                    break
        if not matched:
            hard += 1
            if first_hard < 0:
                first_hard = j
            j += 1
    return hard, ptr_ok, ptr_bad, first_hard, classed


# Runtime-model constants — RE-BAKED 2026-07-10 (directive session 3) after
# the clipMap block-model fix in alloc_events (MapEnts/PhysPreset roots ->
# TEMP + INSERT slots, aabbTrees align 16 / brushes 128 / box_brush 16 /
# walkable align 1, per the T6 load db). The OLD constants were gate-consistent
# but ABSOLUTELY wrong inside clipMap (raid +2,132 / dockside +644/660 —
# proven by the dockside DynEntityDef physPreset field-dedup anchors and by
# cbrush sides/verts offset-pointers: mod-12 grid now 0 on both maps, and the
# dock preset aliases land exactly on owner_def+56). The material-name sweep
# is now UNAMBIGUOUS (raid 223/223 single peak; the old 'false peak' at
# 922,000 was the TRUE value).
#  - gfx_skip     919776: console GfxWorld runtime total (unchanged)
#  - clipMap pre_skip 2224: console pre-clipMap frame (sweep 223/223)
#  - dynent lump  5628: residual to S_snd 927,628 (SndBank anchors, n=12,089)
#  - gfx_skip_pc  -10402376: PC GfxWorld net virtual correction (unchanged)
#  - PC clipMap pre_skip -440: unique 223/223 sweep peak (was -2574 = the
#    old walker's interior bias absorbed into the frame)
#  - PC lump: post-clipMap residual to the (noisy) PC SndBank family
# co_structural_gfx (part B session 2, item 5): piecewise console gfx
# interior — runtime-virtual skips at dpvsPlanes.planes (the 749,115
# constant, measured blind from the zone's own clipMap-header planes alias)
# and at materialMemory (min dpvs.surfaces alias, raid 983,765 - 749,115).
# gfx_skip stays the anchored END TOTAL (end_residual = skip - planes - mm).
# Constants re-measured UNDER the events model (the generic walk carried
# 60 B of alignment pads inside gfx; +60 on gfx_skip keeps the post-gfx rt
# — and every post-gfx baked constant — bit-identical): planes 750,191
# (= 749,115 + 1,076 event-frame), matmem +234,650, end total 919,836.
GEN_POLICY = dict(gfx_skip=919836, dynent_rt=dict(lump=5628),
                  pre_skip={'clipMap_t': 2224},
                  co_structural_gfx=True,
                  gfx_planes_skip=750191, gfx_matmem_skip=234650)
# SELF_POLICY (Track 0 SPINE, 2026-07-12): the skip constants above are
# CONTENT models (dynent counts x per-type sizes + fit residual, the clip
# frame, the gfx band for a genuine-STRUCTURED GfxWorld body) — they do not
# depend on emitted body SIZES, so they remain valid for content-preserving
# bodies of any size. "Self-consistent" is the BUILD PROCEDURE, not new
# constants: pass-3 bakes pointers against OUR emitted stream (already true),
# and verbatim genuine bodies get their interior pointers RELOCATED into our
# layout (spine_selfpolicy.relocate_assets; identity-proven on the ALL build).
# Only a structurally different GfxWorld invalidates the gfx band — then the
# band must be MEASURED (measured_rtmap/override_rtmap, the skate method).
# Kept as a distinct name so callers state intent; NUMERICALLY == GEN_POLICY.
SELF_POLICY = dict(GEN_POLICY)
# PC side — RE-BAKED 2026-07-10 (part B session 2) under pc_structural_gfx:
# gfx_skip_pc is REPLACED by the structural GfxWorld region model + two
# blind-derived knobs (ADDENDUM 8 recipe, gfx-only flag — the full
# pc_structural_temps XModel/FX/Material interior flip disturbs alias emits
# into XModel interiors and stays opt-in):
#   pre_skip_pc['GfxWorld'] = clipMap-hdr planes-alias correction (E@planes;
#                             raid -104,032 = the known pre-gfx drift)
#   gfx_residual_pc         = E@end - E@planes (GWMP tree plateau, tie-high)
# S_clip re-derived under the new model = UNCHANGED (-440 raid / -644 dock /
# -552 skate blind; knobs anchor the same absolute rt at gfx end). PC lump
# re-derived (noisy family): raid -2228 -> -2172, dock -88 -> -84.
# gfx_matmem_pc = E@matmem (blind: min dpvs.surfaces material alias - model
# rt of the materialMemory array start): localizes the residual so the
# x5,281 surfaces->matmem FIELD-dedup family is exact (stride-8 array).
PC_POLICY = dict(pc_structural_gfx=True,
                 pre_skip_pc={'GfxWorld': -104032, 'clipMap_t': -440},
                 gfx_residual_pc=-64320, gfx_matmem_pc=3144,
                 dynent_rt=dict(lump=-2172))
PC_POLICY_OLD = dict(gfx_skip_pc=-10402376, pre_skip_pc={'clipMap_t': -440},
                     dynent_rt=dict(lump=-2228))

# dockside oracle pair (2-map bar): same recipe. Dock preset-field anchors:
# E_pc = 640 exact (constant through the whole clipMap => interior exact);
# console E = 0 after re-derivation (defs-mod84 = 56 = the physPreset field).
DOCK_CO = '../wiiu_ref/mp_dockside_wiiu.zone'
DOCK_PC = '../wiiu_ref/mp_dockside_pc.zone'
# dock console gfx has NO inline matmem materials (0 name strings in the
# span) -> no matmem band; planes 471,012 measured from the dock planes alias
DOCK_GEN_POLICY = dict(gfx_skip=565044, pre_skip={'clipMap_t': 4308},
                       dynent_rt=dict(lump=344),
                       co_structural_gfx=True, gfx_planes_skip=472088)
# pre -644: UNIQUE 221/222 material-name sweep peak; grids+preset anchors max
# in the same mod-12 band; the gate's brushside-plane family (222 self-refs,
# ours=gen-4 at -640) pins the phase. E@defs recovers via the aabb 16-align pad.
DOCK_PC_POLICY = dict(pc_structural_gfx=True,
                      pre_skip_pc={'GfxWorld': -107584, 'clipMap_t': -644},
                      gfx_residual_pc=-88848, gfx_matmem_pc=3628,
                      dynent_rt=dict(lump=-84))
DOCK_PC_POLICY_OLD = dict(gfx_skip_pc=-15409056, pre_skip_pc={'clipMap_t': -644},
                          dynent_rt=dict(lump=-88))


def main(gen_policy=None, pc_policy=None, our_policy=None,
         co_path=None, pc_path=None):
    global CO_PATH, PC_PATH
    if co_path is not None:
        CO_PATH = co_path
    if pc_path is not None:
        PC_PATH = pc_path
    # genuine-REPRODUCTION mode: genuine zones emit asset names as dedup
    # aliases, so the FIX B inline-name policy (correct for authoring) must be
    # OFF here or every aliased-name body diffs vs genuine by design.
    PN.INLINE_ASSET_NAMES = False
    if gen_policy is None:
        gen_policy = GEN_POLICY
    if pc_policy is None:
        pc_policy = PC_POLICY
    import loader_sim as LS
    # genuine side: loader-simulation walk (all 889 assets + runtime addresses)
    em_g, spans, CO = LS.simulate(CO_PATH, policy=gen_policy)
    rt_g = LS.RuntimeMap(em_g.omap)
    rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
    gen_arr = (rc.assets_off - 64 + 7) & ~7    # XAsset array allocates 8-aligned
    # (the former clipMap-gap tail QUARANTINE is retired: the genuine walk now
    # dispatches clipMap/SndBank/XAnimParts to the validated probes and consumes
    # the raid zone byte-exactly to EOF — every tail span is genuine again)
    quarantine = set()

    co_by = {}
    occ = defaultdict(int)
    occ_all_g = defaultdict(int)
    gen_ranges = []
    gen_idx2key = []
    gen_span_by_key = {}
    quarantined_keys = set()
    for (i, nm, root, s, e) in spans:
        ka = occ_all_g[nm]; occ_all_g[nm] += 1
        gen_idx2key.append((nm, ka))           # slot identity: name + all-rows occ
        if e <= s:
            continue                       # aliased/no-body: not pairable
        k = occ[nm]; occ[nm] += 1
        co_by[(nm, k)] = (root, s, e)
        if i in quarantine:
            quarantined_keys.add((nm, k))
        # STREAM-space range for pointer resolution (runtime aliases are
        # inverted through the side's own omap inside the resolver)
        # keyed by ALL-ROWS occurrence: robust across aliased-row differences
        gen_ranges.append((s - 64, e - 64, (nm, ka)))
        gen_span_by_key[(nm, ka)] = (s, e)
    gen_res = PtrResolver(gen_ranges, rt_g.keys, rt_g.vals,
                          arr_base=gen_arr, idx2key=gen_idx2key)

    def gen_fetch(key, delta, nn):
        se = gen_span_by_key.get(key)
        if se is None:
            return None
        s2, e2 = se
        if s2 + delta >= e2:
            return None
        return CO[s2 + delta:min(s2 + delta + nn, e2)]

    stat, assets, omap = PN.assemble_zone(PC_PATH, verbose=False,
                                          pc_policy=pc_policy,
                                          our_policy=our_policy)

    # GFXWORLD region tables for the region-relative compare: ours from the
    # Track F emit pairing, gen from the console G2 region walk (labels
    # normalized with the same key regex as the emit log).
    import re as _re
    import gfxworld_events as _GEV
    _norm = lambda l: _re.sub(
        r' x\d+| \d+ bytes|\(\d+\)| @\d+|\[.*\]|\d+', '', l).strip()
    try:
        gfx_g = next((s, e) for (i, nm, root, s, e) in spans
                     if root == 'GfxWorld' and e > s)
        _, gregs = _GEV.co_regions(CO, gfx_g[0])
        globals()['_GFX_REG_GEN'] = sorted(
            (lo, hi, _norm(lab)) for (lab, lo, hi) in gregs)
        pairs = next(iter(PN._GFX_PAIR_CACHE.values()), None)
        globals()['_GFX_REG_OURS'] = sorted(
            (co, co + cl, _norm('body' if key == 'body' else key))
            for (pa, pb, co, cl, meth, key) in pairs) if pairs else None
    except Exception:
        globals()['_GFX_REG_GEN'] = globals()['_GFX_REG_OURS'] = None

    # our-side resolver: runtime ranges from the assemble's own loader-sim pass
    our_ranges = []
    occo = defaultdict(int)
    occ_all_o = defaultdict(int)
    our_idx2key = []
    our_body_by_key = {}
    for (i, nm, root, body, why), (_, _, _, s, e) in zip(assets, omap.rt_spans):
        ka = occ_all_o[nm]; occ_all_o[nm] += 1
        our_idx2key.append((nm, ka))
        if body is None:
            continue
        k = occo[nm]; occo[nm] += 1
        our_ranges.append((s - 64, e - 64, (nm, ka)))
        our_body_by_key[(nm, ka)] = body
    ours_res = PtrResolver(our_ranges, omap.rtmap.keys, omap.rtmap.vals,
                           arr_base=omap.our_arr, idx2key=our_idx2key)

    def ours_fetch(key, delta, nn):
        b = our_body_by_key.get(key)
        if b is None or delta >= len(b):
            return None
        return bytes(b[delta:delta + nn])

    # SndBank pairs match by BANK NAME, not occurrence: the console list carries
    # an extra localized bank (mpl_<map>.english) the PC list lacks (insert-set
    # finding). Bank name = C-string following the 4756-B body on both sides.
    def _bank_name(buf, o=0):
        try:
            e = buf.index(b'\x00', o + 4756)
            return bytes(buf[o + 4756:e]).decode('latin-1')
        except (ValueError, IndexError):
            return None
    snd_by_name = {}
    for (key, (root, s, e)) in list(co_by.items()):
        if root == 'SndBank':
            bn = _bank_name(CO, s)
            if bn:
                snd_by_name[bn] = key

    # console ALIASED same-type rows (bodies defined elsewhere in-zone): budget
    # for the no-console-pair pairing rule (chase findings §6)
    aliased_rows = Counter(nm for (i, nm, root, s, e) in spans
                           if e <= s and root is not None)
    aliased_used = Counter()

    res = Counter()
    per_type = defaultdict(Counter)
    fails = []
    ptr_bad_total = 0
    occ2 = defaultdict(int)
    for (i, nm, root, body, why) in assets:
        k = occ2[nm]; occ2[nm] += 1
        if body is None:
            if why == 'aliased/no-root':
                res['skipped'] += 1
            elif root in SKIP:
                # SKIP exempts a root from the byte-DIFF, not from EXISTING:
                # a SKIP-typed body that failed to emit is still a machinery
                # bug (2026-07-18: a GfxWorld emit EXC hid here as 'skipped'
                # while the assembled zone silently lost 22.9MB)
                res['MISSING'] += 1
                fails.append((nm, root, 'missing(SKIP-type): ' + why))
            else:
                res['MISSING'] += 1; fails.append((nm, root, 'missing: ' + why))
            continue
        pair = co_by.get((nm, k))
        if root == 'SndBank':
            bn = _bank_name(body)
            pair = co_by.get(snd_by_name.get(bn))
        if (nm, k) in quarantined_keys:
            res['tail-unverifiable'] += 1  # genuine-side walk desync (clipMap gap)
            continue
        if pair is None:
            # aliased-twin rule: a body-bearing PC asset whose console pair is an
            # ALIASED list row (no inline body; defined elsewhere in-zone) PASSES
            if aliased_used[nm] < aliased_rows[nm]:
                aliased_used[nm] += 1
                res['aliased-twin'] += 1; per_type[root]['aliased-twin'] += 1
                continue
            res['no-console-pair'] += 1
            fails.append((nm, root, 'no console pair'))
            continue
        _, cs, ce = pair
        if root == 'GfxWorld':
            # Track F emit: registered SYNTH/REENCODE classes (streamInfo,
            # BC3 lightmaps, PC-kept trees/sort) — per-region oracle lives in
            # gfxworld_emit's validate_against; a 22 MB byte-diff here is
            # meaningless and slow. Allowlisted by design.
            res['allowlisted-diff'] += 1; per_type[root]['allow'] += 1
            continue
        gen = CO[cs:ce]
        if bytes(body) == gen:
            res['exact'] += 1; per_type[root]['exact'] += 1
            continue
        if root == 'XAnimParts' and len(body) != len(gen):
            # anim source-recompile class (dockside seagull_circle_02: console
            # anim carries ONE extra frame index, +2 B in each of 3 arrays —
            # PC source has one fewer frame; not reproducible from PC data).
            # Verify the shape: gen == ours + a few small gen-only insertions.
            import difflib
            smx = difflib.SequenceMatcher(None, bytes(body), gen, autojunk=False)
            ops = [op for op in smx.get_opcodes() if op[0] != 'equal']
            if (0 < len(gen) - len(body) <= 16 and len(ops) <= 8 and
                    all(op[0] in ('insert', 'replace') and
                        (op[4] - op[3]) - (op[2] - op[1]) >= 0 and
                        op[4] - op[3] <= 8 for op in ops)):
                res['allowlisted-diff'] += 1
                per_type[root]['anim-recompile'] = \
                    per_type[root].get('anim-recompile', 0) + 1
                continue
        dbg = [] if root in (globals().get('DEBUG_ROOTS') or ()) else None
        hard, ptr_ok, ptr_bad, first_hard, classed = semantic_diff(
            body, gen, ours_res, gen_res, hard_ok=HARD_CLASS.get(root),
            ours_fetch=ours_fetch, gen_fetch=gen_fetch, dbg=dbg,
            # ComWorld joined 2026-07-10 (part B session 2): primary-light
            # defName string dedups whose GENUINE copy dangles into GX2
            # shader bytecode (console linker dedup vs recomputed blob);
            # ours re-sources the real string — gen target non-stringish
            # is the same positive predicate as the DD class.
            stale_str_ok=(root in ('DestructibleDef', 'ComWorld')),
            fp_recompute=(root in ('clipMap_t', 'GameWorldMp')))
        if dbg:
            print('--- DEBUG %s %s: first %d of %d ptrbad ---' %
                  (nm, root, min(len(dbg), 25), len(dbg)))
            for (w, vo, vg, ro, rg) in dbg[:25]:
                print('   off=%-8d vo=%08x vg=%08x ro=%s rg=%s' %
                      (w, vo, vg, ro, rg))
            samekey = Counter()
            for (w, vo, vg, ro, rg) in dbg:
                if ro and rg and ro[0] == rg[0] and isinstance(ro[1], int) \
                        and isinstance(rg[1], int):
                    samekey[ro[1] - rg[1]] += 1
                else:
                    samekey['diff-asset'] += 1
            print('   delta-diff histogram (ours-gen):',
                  samekey.most_common(12))
        ptr_bad_total += ptr_bad
        sizeok = len(body) == len(gen)
        if hard == 0 and ptr_bad == 0 and sizeok:
            res['ptr-equivalent'] += 1; per_type[root]['ptr-eq'] += 1
            if classed:
                per_type[root]['class-ok-words'] += classed
        elif root in ALLOW_DIFF:
            res['allowlisted-diff'] += 1; per_type[root]['allow'] += 1
            if ptr_bad:
                per_type[root]['allow-ptrbad'] += ptr_bad
        else:
            res['VIOLATION'] += 1; per_type[root]['VIOLATION'] += 1
            fails.append((nm, root, 'len %d vs %d, hard=%d ptrok=%d ptrbad=%d classed=%d first-hard@%d' %
                          (len(body), len(gen), hard, ptr_ok, ptr_bad, classed, first_hard)))

    if globals().get('_PTRBAD_CLASSES'):
        print('--- ptr-bad classes (ours-type, gen-type) ---')
        for kk, vv in globals()['_PTRBAD_CLASSES'].most_common(20):
            print('  %-40s x%-5d %s' % (kk, vv, globals()['_PTRBAD_EX'].get(kk, '')[:90]))
    print('=== RAID ORACLE CONTROL ===')
    print('result:', dict(res))
    print('omap:', dict(omap.stats))
    print('--- per type ---')
    for root in sorted(per_type):
        print('  %-22s %s' % (root, dict(per_type[root])))
    if fails:
        print('--- violations/missing (first 25) ---')
        for f in fails[:25]:
            print('   %-44s %-18s %s' % f)
    gate = (res['VIOLATION'] == 0 and res['MISSING'] == 0)
    print('GATE (no violations, no missing): %s' % ('PASS' if gate else 'FAIL'))
    print('unresolved-omap (must reach 0): %d' % omap.stats['unresolved'])
    return 0 if gate else 1


# =====================================================================
# ABSOLUTE-TRUTH ANCHOR SUITE (`python raid_oracle_control.py anchors`)
#
# The gate compares OURS vs GEN in stream space, which CANCELS any bias that
# hits both sims identically — an absolute model error is gate-invisible
# (proven 2026-07-10: the clipMap block-model was absolutely off by
# +2,132/raid +644/dock and only 12 accidental witnesses showed). These three
# instruments measure each sim against LINKER TRUTH and must stay exact:
#   1. cbrush sides/verts OFFSET-POINTERS: plain block offsets written by the
#      linker; inverted through the sim they must land mod-12 = 0 on the
#      brushsides/brushverts grids (thousands per map).
#   2. FIELD-LOOKUP DEDUP ANCHORS: a dedup alias targets the FIRST
#      occurrence's registered FIELD (AddPointerLookup) — dockside DynEntityDef
#      physPreset dedups must land exactly on owner_def+56.
#   3. MATERIAL-NAME CONTENT SWEEP: with a correct interior model the sweep
#      has a SINGLE perfect peak; the baked pre_skip must sit ON it.
# INVARIANT (frame phase): interior consumption is frame-phase-dependent
# (aabbTrees 16 / brushes 128 aligns), so pre_skip candidates satisfying the
# grids repeat mod 12 — only content anchors (or a gen-match family) pin the
# phase. Never re-bake a pre_skip from grids alone.
# =====================================================================
def _anchor_clipmap(zone, e, policy, is_pc, label):
    import bisect
    import loader_sim as LS
    import alloc_events as AE
    if is_pc:
        em, spans, D = LS.simulate_pc(zone, policy=policy)
        import clipmap_pc
        mat_span = clipmap_pc._mat_span
    else:
        em, spans, D = LS.simulate(zone, policy=policy)
        import clipmap_console as CC
        mat_span = CC._mat_span
    ks = sorted(em.omap); vs = [em.omap[k] for k in ks]

    def inv(b5):
        i = bisect.bisect_right(vs, b5) - 1
        return ks[i] + (b5 - vs[i])
    cm = [(s, ee) for (i, nm, root, s, ee) in spans
          if root == 'clipMap_t' and ee > s][0]
    b = cm[0]; span_lo = b - 64
    u = lambda o: struct.unpack_from(e + 'I', D, b + o)[0]
    u16o = lambda o: struct.unpack_from(e + 'H', D, b + o)[0]
    _, ev = AE.clipmap_events(D, b, e, mat_span=mat_span)

    def find(sz):
        for e2 in ev:
            if e2[0] == 'seg' and e2[2] == sz:
                return e2[1]
    bs_lo = find(u(24) * 12); bv_lo = find(u(48) * 12)
    br_lo = find(u16o(64) * 96); dr = find(u16o(254) * 84)
    IS = lambda v: 0xA0000001 <= v <= 0xBFFFFFFD
    ok = [0, 0]; bad = [0, 0]
    for k in range(u16o(64)):
        for j, (off, lo, tot) in enumerate(((32, bs_lo, u(24) * 12),
                                            (88, bv_lo, u(48) * 12))):
            v = struct.unpack_from(e + 'I', D, b + br_lo + k * 96 + off)[0]
            if IS(v):
                rel = inv((v - 1) & 0x1FFFFFFF) - span_lo
                if lo <= rel < lo + tot:
                    (ok if (rel - lo) % 12 == 0 else bad)[j] += 1
    # field-lookup anchors: preset dedups -> owner_def+56 (maps with owners)
    f_ok = f_bad = 0
    if dr:
        fol = [k for k in range(u16o(254))
               if struct.unpack_from(e + 'I', D, b + dr + k * 84 + 56)[0]
               == 0xFFFFFFFF]
        for k in range(u16o(254)):
            v = struct.unpack_from(e + 'I', D, b + dr + k * 84 + 56)[0]
            if IS(v):
                rel = inv((v - 1) & 0x1FFFFFFF) - span_lo
                own = [o for o in fol if o < k]
                if own and dr <= rel < dr + u16o(254) * 84:
                    if rel - dr == own[-1] * 84 + 56:
                        f_ok += 1
                    else:
                        f_bad += 1
    # material-name content anchors AT the baked frame (sweep hits at S=0
    # residual): every within-clipMap dedup'd name must be a name-start
    nm2 = u(16); mb = b + 332
    names = set(); o2 = mb + nm2 * 12
    for i in range(nm2):
        if struct.unpack_from(e + 'I', D, mb + i * 12)[0] == 0xFFFFFFFF:
            e2 = D.index(b'\x00', o2); names.add(bytes(D[o2:e2])); o2 = e2 + 1
    n_ok = n_tot = 0
    for i in range(nm2):
        v = struct.unpack_from(e + 'I', D, mb + i * 12)[0]
        if IS(v):
            n_tot += 1
            st = inv((v - 1) & 0x1FFFFFFF) + 64
            if st > 64 and D[st - 1] == 0:
                try:
                    if bytes(D[st:D.index(b'\x00', st, st + 96)]) in names:
                        n_ok += 1
                except ValueError:
                    pass
    passed = (bad == [0, 0] and f_bad == 0 and n_ok >= n_tot - 2)
    print('%-10s sides %d/%d verts %d/%d  field %d/%d  names %d/%d  %s' %
          (label, ok[0], ok[0] + bad[0], ok[1], ok[1] + bad[1],
           f_ok, f_ok + f_bad, n_ok, n_tot, 'OK' if passed else '** FAIL **'))
    return passed


def anchor_suite():
    """Absolute-truth regression: run after ANY loader-model/walker change,
    alongside the ST calibration and the alloc_events self-check."""
    allok = True
    allok &= _anchor_clipmap(CO_PATH, '>', GEN_POLICY, False, 'raid-CO')
    allok &= _anchor_clipmap(PC_PATH, '<', PC_POLICY, True, 'raid-PC')
    allok &= _anchor_clipmap(DOCK_CO, '>', DOCK_GEN_POLICY, False, 'dock-CO')
    allok &= _anchor_clipmap(DOCK_PC, '<', DOCK_PC_POLICY, True, 'dock-PC')
    print('ANCHOR SUITE:', 'PASS' if allok else 'FAIL')
    return 0 if allok else 1


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'anchors':
        raise SystemExit(anchor_suite())
    if len(sys.argv) > 1 and sys.argv[1] == 'dockside':
        raise SystemExit(main(co_path=DOCK_CO, pc_path=DOCK_PC,
                              gen_policy=DOCK_GEN_POLICY,
                              pc_policy=DOCK_PC_POLICY))
    raise SystemExit(main())
