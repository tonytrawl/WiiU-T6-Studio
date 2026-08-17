#!/usr/bin/env python3
"""
No-backbone console-zone ASSEMBLE loop (Track G). Authors a console zone from a PC map
alone. Two modes:

  raid-control : run on raid (has a genuine console oracle). Emit each body from PC via
                 the real converters (+ stubs where mp_skate would stub), diff vs the
                 genuine console body. A KNOWN-EXCEPTION ALLOWLIST masks the converters
                 that are intentionally not byte-identical (material sort-hash, substituted
                 techsets, skinned). Any diff OUTSIDE the allowlist = an assemble-machinery
                 bug (ordering / body dispatch / size). This validates the MACHINERY, not
                 converter byte-perfection.
  build        : run blind on mp_skate -> emit loadable bodies -> (container + pack).

This file: the emit dispatch + per-asset oracle diff (raid) + per-asset log. The container
emission / omap finalize / pack are layered on once the body loop is proven on raid.
"""
import sys, os, struct
sys.path.insert(0, '.'); sys.path.insert(0, os.path.join('..', 'wiiu_ref'))
import struct_layout, walker as W
import pc_zone, wiiu_zone
import material_convert as MC
import fx_pc, xmodel_pc, techset_pc, gfxworld_pc, clipmap_pc, sndbank_pc, lightdef_pc, glasses_pc
import destructibledef_probe as _DP
import gameworldmp_probe as _GW
import xanimparts_probe as _XA

FOLLOW = 0xFFFFFFFF

# ---- PC per-asset body-span walk (Track E dispatch, yields spans) ----
def walk_pc_bodies(PC):
    r = pc_zone.PCZoneReader(PC); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=False)
    wk = W.Walker(PC, L, W.ZoneCode(W.ZC_DIR), r.block_sizes)
    wk.u16 = lambda o: struct.unpack_from('<H', PC, o)[0]
    wk.u32 = lambda o: struct.unpack_from('<I', PC, o)[0]
    wk._scalar = lambda base, o: (lambda s: PC[o] if s == 1 else
                                  struct.unpack_from('<H' if s == 2 else '<I', PC, o)[0])(L._resolve(base)[0])
    # span consumers for inline (FOLLOW/INSERT) asset refs inside other assets
    # (e.g. WeaponDef hud/viewmodel Materials, TracerDef.material) — without
    # these the generic walk under-consumes ZM weapons (zm_transit) and desyncs
    wk.asset_span = {
        'Material':             lambda c: MC.convert_material(PC, c)[1],
        'GfxImage':             lambda c: MC.pc_image_span(PC, c),
        'XModel':               lambda c: xmodel_pc.parse_xmodel_pc(PC, c),
        'FxEffectDef':          lambda c: fx_pc.parse_fx_pc(PC, c)[0],
        'XAnimParts':           lambda c: _XA.parse_xanim(PC, c, '<')[0],
        'MaterialTechniqueSet': lambda c: techset_pc.parse_techset_pc(PC, c),
        'GfxLightDef':          lambda c: lightdef_pc.parse_lightdef_pc(PC, c),
        'SndBank':              lambda c: sndbank_pc.parse_sndbank_pc(PC, c),
        'DestructibleDef':      lambda c: _DP.parse_destructible(PC, c, '<')[0],
        'TracerDef':            lambda c: ZCB.convert_tracerdef(PC, c)[1],
        'WeaponCamo':           lambda c: ZCB.convert_weaponcamo(PC, c)[1],
    }
    cur = r.assets_end
    out = []
    for i, (t, nm, hp) in enumerate(r.assets):
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs or hp != FOLLOW:
            out.append((i, nm, root, None, None, hp)); continue
        start = cur
        try:
            if root == 'FxEffectDef':          cur = fx_pc.parse_fx_pc(PC, start)[0]
            elif root == 'XModel':             cur = xmodel_pc.parse_xmodel_pc(PC, start)
            elif root == 'MaterialTechniqueSet': cur = techset_pc.parse_techset_pc(PC, start)
            elif root == 'Material':           _, cur = MC.convert_material(PC, start)
            elif root == 'DestructibleDef':    cur = _DP.parse_destructible(PC, start, '<')[0]
            elif root == 'GfxLightDef':        cur = lightdef_pc.parse_lightdef_pc(PC, start)
            elif root == 'Glasses':            cur = glasses_pc.parse_glasses_pc(PC, start)
            elif root == 'clipMap_t':          cur = clipmap_pc.parse_clipmap_pc(PC, start)
            elif root == 'SndBank':            cur = sndbank_pc.parse_sndbank_pc(PC, start)
            elif root == 'GfxWorld':           cur = gfxworld_pc.parse_gfxworld_pc(PC, start)
            elif root == 'GameWorldMp':        cur = _GW.Walker(PC, '<', 144).walk(start)[0]
            elif root == 'XAnimParts':         cur = _XA.parse_xanim(PC, start, '<')[0]
            elif root == 'GfxImage':           cur = MC.pc_image_span(PC, start)
            elif root == 'TracerDef':          cur = ZCB.convert_tracerdef(PC, start)[1]
            elif root == 'WeaponCamo':         cur = ZCB.convert_weaponcamo(PC, start)[1]
            else:                              cur = wk.walk(root, cur)
        except Exception as e:
            out.append((i, nm, root, start, None, hp))
            return out, ('walk-break', i, nm, start, str(e))
        out.append((i, nm, root, start, cur, hp))
    return out, None


# =====================================================================
# The assemble loop: emit console bodies in authored order + coverage report
# =====================================================================
import xmodel_convert as XC

# --- content-bisection hooks (temporary): BISECT_LOG captures {pc_off: root};
# BISECT_MAP {pc_off: genuine_body} substitutes those bodies verbatim. Both None = no-op.
BISECT_LOG = None
BISECT_MAP = None
# GfxWorld resident-image resolver (PC-ipak, raw-fallback for the lut) passed to
# emit_gfxworld's ctx['image_source'] for the tail-lut ONLY. Kept SEPARATE from
# material_convert.IMAGE_SOURCE (which drives materialMemory/standalone streamed
# images) so a raw-blob fallback can't reach the material inline-image path.
GFXWORLD_IMAGE_SOURCE = None
import pc_to_console as P2C
import fx_convert as FXC
import smalls_convert as SC
import zmconv_a as ZA
import zmconv_b as ZCB

B5_BASE = 64
BLOCK5_LO, BLOCK5_HI = 0xA0000001, 0xBFFFFFFF

# roots whose PC->console interior mapping is refined by allocation-event
# pairing (alloc_events walkers exist for both endians)
EVENT_FINE = {'clipMap_t', 'GameWorldMp', 'SndBank'}
TS_TRACE = False   # diag: record techset-interior tagged fixups on Omap.ts_trace

# ITEM 5 (arms auto-wire): exact vshaderTail placement delta override. The
# tail's 18 intra-tail attrib-name aliases must rebase by (our_tail_runtime_b5
# − raid_tail_runtime_b5). The AUTO path computes it from omap.rtmap, which is
# EXACT only where the runtime layout model is exact. For skate the measured
# map is a consistent +5 at the tail depth (the deep-interior model gap =
# queue item 1), so the boot-51-PROVEN value must be supplied here until the
# model is exact. Keyed by map name; None → auto-compute (+ a loud warning).
VSHADER_TAIL_DELTA = {'mp_skate': 0xFF4200}


class FatalBakeError(Exception):
    """(EC) a bake failure that must NEVER be downgraded to 'asset skipped'.

    A swallowed post-conversion fixup ships the UNFIXED bytes; emit_one's outer
    handler would instead drop the whole asset body and record a one-line
    `why='EXC:...'`. Both are silent-ish failures for a fixup whose absence has
    no other symptom. emit_one re-raises this class past both."""

# FIX B pipeline policy (glass/skybox handoff 2026-07-18, boot-53 class): emit
# top-level asset NAME fields INLINE (FOLLOW + string) whenever the PC source
# had them as dedup aliases. Aliased names are DIRECT string pointers whose
# payloads cannot be verified statically; two hardware-proven drifts (skybox ->
# default mc/global_black model, lightdefs -> garbage names) each cost a boot.
# Costs a few dozen bytes per map. Set False ONLY for byte-exact genuine-
# reproduction oracle runs (inlining intentionally diverges from genuine zones,
# which do use name aliases).
INLINE_ASSET_NAMES = True

# BOOT-SAFE unresolved pointers (blind ZM build, no genuine oracle to satisfy):
# when True, pointers the assembler can't resolve emit an IN-BOUNDS b5 mirror
# instead of the poison 0xBF00000x tag that faults at render (boot 4, §5b.2).
# Default False so the raid oracle gate still sees poison tags to classify.
# produce_container's ZM path sets this True.
BOOT_SAFE_UNRESOLVED = False

def _gz_assert_ledger_not_resolution(v, r, omap):
    """(GZ) a path that increments `unresolved` must be RETURNING the
    unresolved value — never a real substitute wearing a 'SUBSTITUTED' label.

    Legal returns when `unresolved` just incremented:
      * the poison tag family `0xBF000001 + n`  (default), or
      * the in-bounds block-5 mirror emitted under BOOT_SAFE_UNRESOLVED
        (`_encode(0)`) — ZM builds set that, so 0xBF0000xx NEVER appears there
        and the emitted value can NOT be used to compare maps. The `unres:<T>`
        COUNTER at the bar's pass is the only cross-map discriminator.
    ⛔ Never value-scan a zone for 0xBF000000 — it is -0.5f exactly, and float
    phantoms swamp it.
    """
    if BOOT_SAFE_UNRESOLVED:
        return                      # in-bounds mirror; nothing to distinguish
    if 0xBF000001 <= r <= 0xBF0FFFFF:
        return                      # the poison family: truthfully unresolved
    raise FatalBakeError(
        '(GZ) substitution/ledger conflict: the et<=6 visuals path incremented '
        "stats['unresolved'] for alias 0x%08X but returned 0x%08X, which is not "
        'the unresolved value. A path may EITHER resolve (and not count an '
        'unresolved) OR report unresolved (and return the unresolved value) — '
        'never both, or the bar and the log disagree about what happened.'
        % (v, r))


# (GY) census mode. When True, an asset that emitted no body is COLLECTED and
# the walk continues instead of raising FatalBakeError. Read-only tooling only
# (`region_registration_gate.census`) — a build must NEVER set this, because
# the whole point of the (GY) refusal is that an unregistered asset leaves a
# FOLLOW row with nothing in the stream and desyncs the loader.
GY_CENSUS_MODE = False

# I1 STAGE 1 (dedup_binder.py) — maps that OPT IN to binding XModel
# .materialHandles dedup back-refs by IDENTITY instead of relocating them
# through `Omap.reloc`'s address chain. Deliberately an allow-list: the raid
# and skate lineages must stay byte-identical until each has been re-validated
# on its own, so listing a map is a decision with a boot attached.
# Env T6_I1_MHARR (0/1) overrides for attribution runs.
I1_MHARR_MAPS = {'zm_nuked'}
# SLACK for the chain's L5 gap constraint. Calibrated on TWO zones only
# (max measured PC runtime->stream rise: 533 B raid, 0 B nuked); 1024 and 4096
# give identical answers on raid, 0 and 1024 give identical answers on nuked.
# Two zones is a calibration, not a family law (spec E7).
I1_MHARR_SLACK = 1024

# I1 STAGE 1b (string_identity.py) -- maps that OPT IN to resolving NAME-STRING
# dedup back-refs by IDENTITY (the string VALUE) instead of relocating them
# through `Omap.reloc`'s address chain. Same allow-list discipline as I1_MHARR:
# raid and skate stay byte-identical until each is re-validated on its own.
# Applying an answer is a pure RE-SOURCE (point at our own copy of the string),
# so it is SIZE-NEUTRAL -- no frozen layout moves. Env T6_I1_STRID (0/1)
# overrides for attribution runs.
I1_STRID_MAPS = {'zm_nuked'}

# per-offset cache for the (expensive) Track F GfxWorld emit: bytes+fixups are
# pass-invariant; only the fixup reloc values change between passes
_GFX_EMIT_CACHE = {}
_GFX_PAIR_CACHE = {}


def _event_fine(PC, s, body, root, co_cursor):
    """Pair the PC asset's allocation events with our emitted body's events;
    return exact fine regions [(pc_b5, co_b5, min_len)] or None on mismatch."""
    import alloc_events as AE
    try:
        if root == 'clipMap_t':
            import clipmap_pc, clipmap_console as CC
            _, evp = AE.clipmap_events(PC, s, '<', mat_span=clipmap_pc._mat_span)
            _, evo = AE.clipmap_events(body, 0, '>', mat_span=CC._mat_span)
        elif root == 'GameWorldMp':
            _, evp = AE.gwmp_events(PC, s, '<')
            _, evo = AE.gwmp_events(body, 0, '>')
        else:
            _, evp = AE.sndbank_events(PC, s, '<')
            _, evo = AE.sndbank_events(body, 0, '>')
    except Exception:
        return None
    segs_p = [ev for ev in evp if ev[0] in ('seg', 'temp')]
    segs_o = [ev for ev in evo if ev[0] in ('seg', 'temp')]
    if len(segs_p) == len(segs_o) + 1:
        segs_p = segs_p[:-1]               # PC-only trailing zero pad (SndBank)
    if len(segs_p) != len(segs_o):
        return None
    return [(s - B5_BASE + ep[1], co_cursor + eo[1], min(ep[2], eo[2]))
            for ep, eo in zip(segs_p, segs_o)]


class Omap:
    """Asset-granular PC->console block-5 offset map + fixup accounting.
    Exact for alias-to-asset-START; interior aliases map linearly within the asset
    span (exact for same-size structural swaps; APPROXIMATE for size-changing types —
    counted separately so the risk stays visible; unresolved is FATAL at finalize)."""
    def __init__(self):
        self.regions = []          # (pc_b5_start, pc_b5_end, co_b5_start, exact_interior)
        self.fine = []             # (pc_b5_start, pc_b5_end, co_b5_start) EXACT sub-regions
        self._prior = None         # pass-1 (fine, regions) (two-pass: forward refs)
        self.rtmap = None          # loader_sim.RuntimeMap: our stream b5 -> runtime addr
        self.pc_inv = None         # loader_sim.InverseMap: PC runtime b5 -> PC stream b5
        self.pc_arr = None         # (pc_assets_off_b5, count): PC XAsset array range
        self.our_arr = 0           # our XAsset array runtime base (container layout)
        self.pc_spans = None       # [(pc_b5_start, pc_b5_end, root)] for unresolved diagnostics
        self.dropped_spans = None  # [(pc_b5_start, pc_b5_end)] for DROPPED assets:
                                   # a ref into one resolves to NULL, not poison
        self.gfx_pc_span = None    # (pc_b5_start, pc_b5_end) of PC GfxWorld: refs stay tagged
        self.gfx_pc_rt_span = None  # same span in PC RUNTIME space (pre-inversion check)
        # PC-runtime guard margin below GfxWorld start: the verbatim techsets
        # right before GfxWorld under-consume virtual vs stream (~26K on raid),
        # so true GfxWorld-interior rt values (e.g. clipMap plane ptrs) can sit
        # below the simulated start. Nothing outside the techsets legitimately
        # targets that window (techset blobs are substituted, not relocated).
        self.gfx_guard_lo_margin = 196608
        self.PC = None             # PC zone bytes (string content re-sourcing)
        self.ts_spans = []         # PC techset spans: interiors not mappable
        self.ts_co = {}            # pc_ts_b5_start -> our co start (ts-dangle mirror)
        self.ts_olen = {}          # pc_ts_b5_start -> our substitute blob length
        self._prev_stream = None   # previous pass's emitted stream (search)
        self.cur_stream = None     # this pass's growing emitted stream
        self.scaled = []           # element-scaled regions (xmodel verts0)
        self._fine_idx = None
        self.ctx = None            # (idx, name, root, pc_off) of asset being emitted
        # diag: list of (ctx, raw_v, pc_b5, ts_s, ts_e); enabled via module flag
        self.ts_trace = [] if TS_TRACE else None
        self.idx_remap = lambda i: i   # PC array idx -> console array idx (inserts)
        self.name_hint = None      # (rt_lo, rt_hi, name) skybox rule (FIX B)
        self.pc_assets = None      # PC asset rows [(type_id, type_name, hp)] (et7 class)
        self.container_prefix = 0  # block-5 offset of our asset array (et7 cross-check)
        self.subst_ledger = []     # rule (K): (tag, ctx, pc_word, emitted) per substitution
        self.strid = None          # I1 stage 1b: string_identity.StringIdentity
        self.stats = dict(start=0, interior_exact=0, interior_approx=0, unresolved=0,
                          sentinel=0, other=0)
        # (HB) per-pass archive. `stats` is REASSIGNED at each new resolver
        # pass, so without this the resets are invisible and a receipt cannot
        # prove which pass it is quoting. See begin_pass()/branch_histogram().
        self._pass_unres = []

    def begin_pass(self):
        """(HB) start a new resolver pass, archiving the outgoing totals.

        The counters are per-pass by design, and **the fatal assembler bar
        judges the LAST pass**. Archiving here is what lets
        `branch_histogram()` report `pass` / `passes_run` / `per_pass_unres`
        from the DATA instead of from prose — so a histogram taken from the
        wrong pass cannot be mistaken for the right one.
        """
        if self.stats:
            self._pass_unres.append(self.stats.get('unresolved', 0))
        self.stats = dict(start=0, interior_exact=0, interior_approx=0,
                          unresolved=0, sentinel=0, other=0)

    def _encode(self, co_b5):
        """Encode a pointer to our-stream offset co_b5. With a runtime map (the
        loader-simulation pass) the value is the loader's RUNTIME address —
        the address space genuine alias pointers use; without it, stream-linear.

        ⚠ G-80 / rule (BL) ("every asset-POINTER alias: block 5, (v-1) 4-aligned") canNOT
        be asserted HERE: this chokepoint also mints STRING dedup aliases, whose targets
        are byte-granular and legitimately misaligned. Discriminating pointer-slot targets
        from string targets needs per-field role information — exactly the record-registry
        gap (§3). Only the in-range half is safe to assert unconditionally."""
        if self.rtmap is not None:
            co_b5 = self.rtmap.rt(co_b5)
        if not (0 <= co_b5 < 0x20000000):
            raise AssertionError('minted alias b5 target 0x%X out of block-5 range' % co_b5)
        return 0xA0000000 + co_b5 + 1

    def _unres_value(self):
        """Value for a pointer the assembler could not resolve. Default = a
        POISON tag (0xBF000001+n, top of block-5, far past any real address) so
        the raid oracle GATE can detect/classify it. But at RUNTIME that tag
        resolves to b5+~0x1F000000 (0.5GB out of bounds) and a deref FAULTS —
        boot 4 crashed here in the render/fetch-shader path (§5b.2, GfxWorld 2 +
        techset-interior 154 poison tags). In BOOT_SAFE mode (blind ZM build, no
        oracle to satisfy) emit an in-bounds b5 mirror (offset 0) instead: a
        render deref then reads valid (garbage) memory rather than a wild ptr.
        Not byte-correct — a boot-through, refine visuals later."""
        if BOOT_SAFE_UNRESOLVED:
            return self._encode(0)
        return 0xBF000001 + (self.stats['unresolved'] & 0xFFFFF)

    def reloc_asset_entry(self, v, expect_type='XMODEL', tag='FX et7 visuals'):
        """Stage 2 (registry campaign 2026-07-30): the et7/asset-entry alias
        class. A PC FX MODEL-visuals alias resolves to the XAsset-ARRAY ENTRY
        SLOT (the headerPtr word) of the target asset's entry — the STEP 0
        positive control (37/37 key-free EXACT on skate; census 2026-07-30:
        237/237 slot-exact across raid 68 / skate 62 / transit 34 / nuked 52 /
        nuketown 21, arr%8 phases {0,5,6,7}, every target an XMODEL entry,
        zero et<=6 collisions). PC side: the array allocates 8-ALIGNED, slots at
        align8(arr) + 8*i + 4. Console side: handle = 0xA0000000 +
        (B + 8*idx + 4) + 1 with B = align4(assets_off) - 64 — the boot-proven
        gfxworld_dynent_fix.derive_physpreset_handle arithmetic (rule BT),
        cross-checked against the container-layout our_arr (align8) value;
        the two agree on every measured map (raid prefix 13768, skate 16487 ->
        both align4 == align8) and a disagreement REFUSES rather than guessing.
        RAISES if the PC word does not decode to a valid PC asset-array entry
        of the expected type."""
        if not (BLOCK5_LO <= v <= BLOCK5_HI):
            raise AssertionError(
                'et7/asset-entry resolver: %s word 0x%08X is not a block-5 '
                'alias' % (tag, v))
        if self.pc_arr is None:
            raise AssertionError(
                'et7/asset-entry resolver: pc_arr not set — cannot decode %s '
                'alias 0x%08X' % (tag, v))
        b5 = (v - 1) & 0x1FFFFFFF
        a0, n = self.pc_arr
        # ⭐⭐ align4, NOT align8 — landed 2026-08-16 (PM-ruled; two independent
        # instruments). The old `(a0 + 7) & ~7` "SHIFT8 rule (STEP 0 validated)"
        # was never VALIDATED, only never CONTRADICTED: align4 and align8 differ
        # exactly when `raw % 8 in {1,2,3,4}`, and the 237/237 census's own
        # documented phase set is {0,5,6,7} — the EXACT COMPLEMENT. All five
        # census maps agree under both rules by construction:
        #   raid 13768(0) · skate 16487(7) · transit 22152(0) · nuked 27158(6)
        #   · nuketown 13469(5)      -> align4 == align8 on every one
        # mirage 16027(3) and downhill 12852(4) are the first maps to reach a
        # differing phase, and under align8 EVERY one of their et7 aliases
        # decodes to phase 0 (the entry's TYPE word) instead of phase 4 (the
        # headerPtr): mirage 0/69 -> 69/69, downhill 0/82 -> 82/82, all targets
        # XMODEL. Whole-array decode confirms the array itself starts at
        # `assets_off`: mirage 948/954 valid there vs 0/954 at +4.
        # ⇒ this is a MEASURED NO-OP on all five legacy maps (237/237 under
        # either base) and INDEX-PRESERVING ((b5-a0_8)//8 == (b5-a0_4)//8), so
        # it cannot move a resolution that works today; it only stops the phase
        # guard rejecting correct decodes.
        # ⭐ The console half of this same function already uses align4
        # (`b4 = (self.container_prefix + 3) & ~3`, rule BT) and even REFUSES
        # when the two disagree — the two halves contradicted each other.
        # ⚠ The sibling SHIFT8 at the `pc_arr` slot branch in `reloc` carries
        # the identical assumption and is deliberately NOT changed here: it is
        # dormant on mirage (stats['slot'] never appears) so the change would
        # be unmeasurable on this map. Reported for scoping, not silently
        # widened.
        a0 = (a0 + 3) & ~3

        if not (a0 <= b5 < a0 + n * 8) or (b5 - a0) % 8 != 4:
            raise AssertionError(
                'et7/asset-entry resolver: %s alias 0x%08X (payload %d) does '
                'not decode to a PC XAsset-array entry slot (a0=%d n=%d '
                'phase=%s) — the et7 law says it must; refusing to guess'
                % (tag, v, b5, a0, n,
                   (b5 - a0) % 8 if a0 <= b5 < a0 + n * 8 else 'out-of-range'))
        pc_idx = (b5 - a0) // 8
        if self.pc_assets is not None and expect_type is not None:
            tn = self.pc_assets[pc_idx][1]
            if tn != expect_type:
                raise AssertionError(
                    'et7/asset-entry resolver: %s alias 0x%08X decodes to PC '
                    'entry %d of type %s, expected %s (census: 237/237 '
                    'measured et7 targets are XMODEL) — refusing'
                    % (tag, v, pc_idx, tn, expect_type))
        idx = self.idx_remap(pc_idx)
        h8 = 0xA0000000 + (self.our_arr + idx * 8 + 4) + 1
        # ⛔⛔ THE align4-vs-align8 CONSISTENCY GUARD WAS REMOVED 2026-08-17,
        # DELIBERATELY, AFTER TWO FAILED ATTEMPTS TO MAKE IT MEAN SOMETHING.
        # It is NOT replaced by a weaker check — it is replaced by EVIDENCE
        # (named below), because no in-process comparison here can be
        # independent. The history, so nobody re-adds a version of it:
        #
        # v1 (original): compared `h8` (from `our_arr`) vs `h4`
        # (`align4(container_prefix)`, "rule BT"). ⚠ BOTH SIDES WERE GUESSES —
        # `our_arr` was `align8(container_prefix)` BY CONSTRUCTION, never a
        # value read back from a produced layout. It agreed on five maps only
        # because align4 == align8 at their phases ({0,5,6,7}); on mirage
        # (prefix%8==3) it disagreed and refused, which is how the real defect
        # was found — the guard was right to fire and wrong about which side
        # was broken.
        #
        # v2 (mine, WRONG): compared `our_arr` vs `rtmap.rt(container_prefix)`.
        # `RuntimeMap` maps OUR EMITTED STREAM's b5 -> runtime; `container_prefix`
        # is a CONTAINER-frame offset (past the 24-byte xlist and the string
        # region). Feeding one into the other is a frame mismatch: it read
        # 39,654 against 16,028, delta +23,626 — not an alignment at all — and
        # it silently blocked 18 legitimate FX in the final pass.
        # [[alias-coordinate-system-law]], written into a guard this time.
        #
        # v3 (why there is no v3): the only author-side value reachable HERE on
        # a build that dies at the assembler bar is `container_prefix` itself.
        # Call order in produce_container.author_zone:
        #     :739  prefix = len(ptrs) * 4 + len(str_body)      <- before
        #     :856  PN.assemble_zone(...)                       <- dies here
        #     :920-923  content += arr ; assert len(content) == 24+prefix+narr*8
        # The produced container — the only INDEPENDENT witness of where the
        # array really lands — does not exist yet when this code runs. And
        # `our_arr` is now `align4(container_prefix)` by construction, so any
        # check against `align4(container_prefix)` is one formula compared to
        # itself: it can never fail, and a gate that cannot fail is worse than
        # no gate because it reports confidence it has not earned.
        #
        # ⇒ THE REPLACEMENT EVIDENCE FOR THIS BASE (all outside this function):
        #   * P5, verified against the LIVE artefacts: the five legacy zones
        #     byte-identical before/after (align4 == align8 at their phases, so
        #     a move would mean something else changed). mirage and downhill
        #     (phases 3 and 4) are EXPECTED to change and have no baseline.
        #   * the GENUINE 16-zone console census: on every phase where raw /
        #     align4 / align8 predict different residues, align4 wins by
        #     60-200x (nightclub arr%8=3 — mirage's exact phase — align4 8664
        #     entry-slot hits vs align8 43; overflow 4726 vs 71; express 4044
        #     vs 417; hijacked 3719 vs 260).
        #   * rule AX/AC in loader_sim, "measured on all 71 genuine zones",
        #     plus zone_gates and fx_oracle — three independent implementations
        #     that already used align4. This writer was the last holdout.
        #   * a BYTE CHECK on the produced container whenever one exists
        #     (big-endian decode of {int32 type; ptr} at the candidate offsets;
        #     on discriminating phases our own output reads 16/16 entry-shaped
        #     at raw prefix and 0/16 at +4).
        self.stats['et7-entry'] = self.stats.get('et7-entry', 0) + 1
        return h8

    def branch_histogram(self):
        """(HB) THE OMAP BRANCH HISTOGRAM — the named export for provenance.

        Returns a copy of THIS OMAP'S FINAL-PASS counters: start /
        interior_exact / interior_approx / interior_scaled / unresolved /
        unres:<Type> / sentinel / other / slot / et7-entry / strid-* / ...

        ⚠⚠ WHY THIS ACCESSOR EXISTS RATHER THAN "just read omap.stats":
        **`stats` is RESET between resolver passes on the SAME Omap object.**
        A build runs THREE passes (measured on mirage: pass 0 = 44,115 unres
        incl. clipMap_t 15,652 + GameWorldMp 508; passes 1 and 2 = 27,394 each)
        and **the fatal assembler bar judges the LAST**. So a reader that
        samples `stats` mid-run gets a different population from the one the
        bar acted on, and the two are not comparable. After `assemble_zone`
        returns, `stats` holds the FINAL pass — which is the one (HB) wants and
        the only one any receipt should quote.

        ⇒ any consumer MUST state WHICH PASS its numbers come from. This
        accessor is defined to mean the final pass, and exists so provenance
        depends on a named contract instead of an incidental attribute.

        b91's histogram was lost and it is the single most diagnostic artifact
        a bake produces: missing histogram = missing receipt = build failure.

        SHAPE (the consumer reads the pass from the DATA, never from prose):
            {'pass': 2, 'passes_run': 3,
             'per_pass_unres': [44115, 27394, 27394],
             'hist': {...final-pass counters...}}
        `pass` is 0-based and is the pass `hist` came from — i.e. the LAST one
        run, which is the one the bar judged. `per_pass_unres` makes the RESET
        itself visible: on mirage it reads [44115, 27394, 27394], and the drop
        from 44,115 is the reset, not a repair.
        """
        per_pass = list(self._pass_unres) + [self.stats.get('unresolved', 0)]
        return {'pass': len(per_pass) - 1,
                'passes_run': len(per_pass),
                'per_pass_unres': per_pass,
                'hist': dict(self.stats)}

    def add_scaled(self, entries):
        """Element-scaled regions from xmodel_convert marks:
        ('scaled', pc_b5, co_b5, count, pc_stride, co_stride) — a pointer to
        PC element k maps to console element k (verts0 32B -> 24B)."""
        for e in entries:
            self.scaled.append(e)
        self.scaled.sort(key=lambda e: e[1])

    def _scaled_lookup(self, b5):
        import bisect
        i = bisect.bisect_right(self.scaled, b5,
                                key=lambda e: e[1]) - 1
        if i >= 0:
            _, ps, cs, cnt, sp, sc = self.scaled[i]
            if ps <= b5 < ps + cnt * sp:
                k, within = divmod(b5 - ps, sp)
                return cs + k * sc + min(within, sc - 1)
        return None

    def _pc_cstring(self, pc_b5):
        """C-string at a PC stream offset, or None if not string-like."""
        o = pc_b5 + B5_BASE
        if o < 64 or o >= len(self.PC):
            return None
        try:
            e = self.PC.index(b'\x00', o, o + 96)
        except ValueError:
            return None
        s = self.PC[o:e]
        if len(s) < 3 or any(c < 0x20 or c > 0x7e for c in s):
            return None
        return bytes(s)

    def _cstring_resource(self, pc_b5):
        """The CONTENT answer for a PC pointer: if the PC target is a plausible
        C-string AND that exact NUL-bounded string is already present in our own
        emitted stream, return OUR offset for it, else None.

        This is the single strongest form of evidence available for a string
        pointer -- it matches the bytes themselves rather than assuming a layout
        correspondence -- so it is allowed to outrank the two LOSSY mappings
        (`_scaled_lookup` and the approximate region match). It is NOT allowed to
        outrank an EXACT mapping, which is why callers consult it only on the
        lossy paths."""
        if self.PC is None or self._prev_stream is None:
            return None
        tgt = self._pc_cstring(pc_b5)
        if tgt is None:
            return None
        hit = self._prev_stream.find(b'\x00' + tgt + b'\x00')
        return (hit + 1) if hit >= 0 else None

    def pc_name(self, v):
        """FIX B resolver: PC b5-alias word -> the name string it targets, or
        None. Installed on the converters' INLINE_NAME_RESOLVER hooks so
        aliased root names are re-emitted INLINE (FOLLOW + string).

        Hardened: the inverted position must sit INSIDE a string whose start
        is NUL/sentinel-bounded — a drifted inversion landing on random
        printable bytes must not fabricate a name. Landing MID-string snaps
        back to the string start (recovers the full name from the mid-string
        suffix-dedup/drift class, e.g. 'box_mp_skate' -> 'skybox_mp_skate')."""
        if not (BLOCK5_LO <= v <= BLOCK5_HI) or self.pc_inv is None:
            return None
        o = self.pc_inv.stream((v - 1) & 0x1FFFFFFF) + B5_BASE
        if o <= B5_BASE or o >= len(self.PC):
            return None
        lo = o
        while lo > B5_BASE and o - lo < 96 \
                and self.PC[lo - 1] not in (0, 0xFF):
            lo -= 1
        if self.PC[lo - 1] not in (0, 0xFF):
            return None
        try:
            e = self.PC.index(b'\x00', lo, lo + 96)
        except ValueError:
            return None
        s = self.PC[lo:e]
        if len(s) < 3 or o > e or any(c < 0x20 or c > 0x7e for c in s):
            return None
        return bytes(s)

    def pc_name_xmodel(self, v):
        """XModel-name resolver: generic resolution, else the SKYBOX rule —
        the only XModel name sourced from the GfxWorld interior is the
        skyBoxModel (its payload lands inside the PC GfxWorld runtime span,
        which pc_inv cannot invert; boot-53 root cause). name_hint is set by
        assemble_zone from the PC GfxWorld's own skyBoxModel string."""
        nm = self.pc_name(v)
        if nm is not None:
            return nm
        if self.name_hint and BLOCK5_LO <= v <= BLOCK5_HI:
            lo, hi, s = self.name_hint
            b5 = (v - 1) & 0x1FFFFFFF
            # the guard margin below the simulated GfxWorld runtime start
            # covers the verbatim-techset under-consumption drift (the true
            # GfxWorld-interior rt can sit below the simulated start — the
            # skate skybox payload lands 137K below it)
            if lo is not None and hi is not None \
                    and lo - self.gfx_guard_lo_margin <= b5 < hi:
                return s
        return None

    def add(self, pc_b5, pc_len, co_b5, exact):
        self.regions.append((pc_b5, pc_b5 + pc_len, co_b5, exact))

    def add_fine(self, entries):
        """Exact sub-asset regions from PCConverter._reg: (pc_b5, co_b5, len)."""
        for (ps, cs, ln) in entries:
            self.fine.append((ps, ps + ln, cs))
        self._fine_idx = None

    def _fine_lookup(self, b5):
        """Bisect lookup over self.fine + prior fine (rebuilt lazily)."""
        import bisect
        if getattr(self, '_fine_idx', None) is None:
            prior_fine = self._prior[0] if self._prior else ()
            merged = sorted(set(self.fine) | set(prior_fine))
            self._fine_idx = ([t[0] for t in merged], merged)
        starts, merged = self._fine_idx
        i = bisect.bisect_right(starts, b5) - 1
        if i >= 0:                      # regions are disjoint in PC-stream space
            ps, pe, cs = merged[i]
            if ps <= b5 < pe:
                return ps, pe, cs
        return None

    def reloc(self, v):
        if v in (FOLLOW, 0xFFFFFFFE, 0):
            self.stats['sentinel'] += 1
            return v
        if not (BLOCK5_LO <= v <= BLOCK5_HI):
            self.stats['other'] += 1
            return v                       # non-block-5 alias classes (block-bump handled elsewhere)
        b5 = (v - 1) & 0x1FFFFFFF
        if self.pc_arr is not None:
            # asset-HANDLE references alias the XAsset array entry's header-ptr
            # slot (arr + idx*8 + 4) — temp-rooted assets have no persistent
            # body address, so the loader's alias lookup keys on the slot.
            # PC aliases encode PC-RUNTIME addresses: the array allocates
            # 8-ALIGNED, so the slot base is align8(arr) (raid is phase 0 so
            # the shift is invisible there; mp_skate is not).
            a0, n = self.pc_arr
            a0 = (a0 + 7) & ~7
            if a0 <= b5 < a0 + n * 8 and (b5 - a0) % 8 == 4:
                idx = self.idx_remap((b5 - a0) // 8)
                self.stats['slot'] = self.stats.get('slot', 0) + 1
                return 0xA0000000 + (self.our_arr + idx * 8 + 4) + 1
        # I1 STAGE 1b -- NAME-STRING dedup IDENTITY (string_identity.py). Placed
        # here, ahead of every ADDRESS route, for the same reason the et7 branch
        # is: an identity carried from the PC side beats any arithmetic over a
        # drifted inverse (rule DX). The table is keyed on the PRE-inversion
        # payload and holds ONLY payloads whose target string was PROVEN on
        # PC-side structure; everything else falls through untouched, so the
        # branch is a no-op for every pointer that resolves today. Applying it
        # is a pure RE-SOURCE onto our own copy of the same string -- size- and
        # layout-neutral. A miss (our stream has no copy yet, e.g. pass 1)
        # REFUSES and takes today's path rather than minting a guess.
        if self.strid is not None and b5 in self.strid.answers:
            co = self.strid.resource(b5, self._prev_stream, self._encode)
            if co is not None:
                self.stats['strid-resourced'] = \
                    self.stats.get('strid-resourced', 0) + 1
                return self._encode(co)
            self.stats['strid-unfound'] = \
                self.stats.get('strid-unfound', 0) + 1
        if self.gfx_pc_rt_span is not None and self.gfx_pc_rt_span[0] is not None \
                and self.gfx_pc_rt_span[0] - self.gfx_guard_lo_margin <= b5 \
                < self.gfx_pc_rt_span[0]:
            # PC-RUNTIME address in the guard window BELOW GfxWorld start:
            # verbatim-techset under-consumption noise — stays tagged. (The
            # IN-span branch is gone, part B session 2: pc_inv is now
            # region-accurate inside GfxWorld under pc_structural_gfx, so
            # interior values invert safely and resolve via the region-paired
            # fine map below.)
            self.stats['unresolved'] += 1
            self.stats['unres:GfxWorld'] = self.stats.get('unres:GfxWorld', 0) + 1
            return self._unres_value()
        if self.pc_inv is not None:
            # PC alias values encode PC RUNTIME addresses (PC linker: temp roots
            # + alloc alignment) — reverse to PC STREAM offsets before mapping
            b5 = self.pc_inv.stream(b5)
        if self.gfx_pc_span is not None and \
                self.gfx_pc_span[0] <= b5 < self.gfx_pc_span[1]:
            # interior ref INTO GfxWorld: resolve ONLY through the region-
            # paired fine map (exact same-size regions + region starts +
            # the materialMemory array) or scaled entries. NO fall-through
            # to coarse linear region mapping — a miss stays TAGGED (fatal
            # discipline: never approximate inside gfx).
            sc = self._scaled_lookup(b5)
            if sc is not None:
                self.stats['interior_scaled'] = \
                    self.stats.get('interior_scaled', 0) + 1
                return self._encode(sc)
            hit = self._fine_lookup(b5)
            if hit is not None:
                ps, pe, cs = hit
                self.stats['start' if b5 == ps else 'interior_exact'] += 1
                return self._encode(cs + (b5 - ps))
            self.stats['unresolved'] += 1
            self.stats['unres:GfxWorld'] = self.stats.get('unres:GfxWorld', 0) + 1
            return self._unres_value()
        # PC techset INTERIORS are opaque: our techsets are substituted console
        # blobs with different interior layout, so a linear delta is garbage.
        # Re-source string targets from our own stream; else fall to tagging.
        for (ts_s, ts_e) in self.ts_spans:
            if ts_s < b5 < ts_e:
                if self.PC is not None and self._prev_stream is not None:
                    tgt = self._pc_cstring(b5)
                    if tgt is not None:
                        # NUL-preceded first; else any in-string hit — the PC
                        # linker dedups SUFFIXES of longer strings too
                        # ('...postFxControlA' -> 'olA'), and a pointer to
                        # string content is valid at any offset.
                        hit = self._prev_stream.find(b'\x00' + tgt + b'\x00')
                        if hit >= 0:
                            self.stats['resourced'] = \
                                self.stats.get('resourced', 0) + 1
                            return self._encode(hit + 1)
                        hit = self._prev_stream.find(tgt + b'\x00')
                        if hit >= 0:
                            self.stats['resourced'] = \
                                self.stats.get('resourced', 0) + 1
                            return self._encode(hit)
                # TS-DANGLE typed class (pass 3, measured per-field on raid,
                # diag_ts_*.py): the remaining binary targets are PC linker
                # heap-reuse/content accidents dedup'ing real pointer fields
                # (XModel materialHandles / surf headers / bone arrays, FX +
                # Material texdefs) INTO DXBC bytes. Measured (2 oracles):
                # the dedup'd PC content exists in NEITHER our stream NOR
                # the genuine console zone (146/150 unique targets absent)
                # — genuine consoles ship equivalently DANGLING values here
                # and run. Disposition: not a substitution gap, a typed
                # dangle. Emit the boot-safe in-bounds mirror — same
                # techset, same interior delta clamped into OUR substitute
                # blob (genuine ships small in-block offsets; a poison tag
                # would be a ~0.5 GB out-of-block offset). Predicate: source
                # root in the measured family whitelist AND target inside a
                # techset span; anything else still tags below (a NEW
                # source family must be measured before it may dangle).
                if (self.ctx is not None
                        and self.ctx[2] in ('XModel', 'FxEffectDef',
                                            'Material', 'GfxWorld')
                        and self.ts_co.get(ts_s) is not None):
                    co = self.ts_co[ts_s]
                    olen = self.ts_olen.get(ts_s, 16)
                    delta = min(b5 - ts_s, max(olen - 16, 0))
                    self.stats['ts-dangle'] = \
                        self.stats.get('ts-dangle', 0) + 1
                    if self.ts_trace is not None:
                        self.ts_trace.append((self.ctx, v, b5, ts_s, ts_e))
                    return self._encode(co + delta)
                # TS-NOISE verbatim class (measured per-root, diag session):
                # clipMap_t (dock x74: cStaticModel recomputed-float words —
                # the fp_recompute family), ComWorld (skate x19 / dock x8:
                # float noise + stale primary-light defName dedups) and
                # Glasses words that decode into ts spans are DATA, not
                # pointers (no pointer field in these walks targets techset
                # content; real material refs resolve via slots/starts).
                # Verbatim preserves the genuine float content the old
                # poison tag corrupted; for the stale defName words verbatim
                # IS the genuine behavior (genuine ships its own stale
                # value). Unknown roots still tag below.
                if self.ctx is not None and self.ctx[2] in (
                        'clipMap_t', 'ComWorld', 'Glasses'):
                    self.stats['ts-noise-verbatim'] = \
                        self.stats.get('ts-noise-verbatim', 0) + 1
                    return v
                # ts456 campaign (2026-07-30): this tag is a TRIPWIRE, not a
                # disposition. When the raid lane showed unres:techset-interior
                # 456, ALL 456 were GameWorldMp words (pathnode nodeTree
                # children + kin) whose pc_inv inversion had drifted +14,032
                # into the first post-GWMP techset's DXBC bytes -- the PC-side
                # basenodes blk5 charge had been deleted from
                # alloc_events.gwmp_events together with the (oracle-proven)
                # console-side deletion. Genuine raid ships all 516 nodeTree
                # child words as RELOCATED aliases (c1==self+1 law, measured
                # 258/258), so REAL pointers can land here on a model bug:
                # never absorb a new source root into the ts-dangle mirror or
                # the verbatim class above without per-root measurement --
                # poison keeps the fault class visible (it is what exposed
                # this one). Fixed by the endian split in gwmp_events ('<'
                # keeps the charge); unresolved back to 0.
                self.stats['unresolved'] += 1
                self.stats['unres:techset-interior'] = \
                    self.stats.get('unres:techset-interior', 0) + 1
                if self.ts_trace is not None:
                    self.ts_trace.append((self.ctx, v, b5, ts_s, ts_e))
                return self._unres_value()
        prior_fine, prior_regions = self._prior if self._prior else ((), ())
        sc = self._scaled_lookup(b5)       # element-scaled XModel regions
        if sc is not None:
            # ⚠ MEASURED NO-OP, REVERTED 2026-08-14. I added a content-first
            # check here on the theory that `_scaled_lookup` was claiming C-string
            # pointers. It fired ZERO times: b76 came out BYTE-IDENTICAL to b75
            # and `resourced-over-scaled` never appeared. The PC targets of the
            # mis-resolved weapon pointers are NOT C-strings (one is 00ff0000
            # repeating), so `_pc_cstring` returns None and content cannot help.
            # Removed rather than left in: on another map it would be an
            # UNCALIBRATED redirect on the shipping path, which is exactly what
            # this lane refuses elsewhere (see stage 2 in xmodel_mharr_simframe).
            # The real mechanism is still open -- see FINDINGS.
            self.stats['interior_scaled'] = \
                self.stats.get('interior_scaled', 0) + 1
            return self._encode(sc)
        hit = self._fine_lookup(b5)        # exact converter/event sub-regions first
        if hit is not None:
            ps, pe, cs = hit
            self.stats['start' if b5 == ps else 'interior_exact'] += 1
            return self._encode(cs + (b5 - ps))
        # ⛔⛔ AN APPROXIMATE REGION MATCH MUST NOT OUTRANK CONTENT (boot 74).
        # An `exact=False` region says only "this PC offset lies inside a span we
        # mapped", and answers `cs + (b5 - ps)` -- i.e. it ASSUMES the console
        # body reproduces the PC body's internal layout. For a C-string target
        # that assumption is strictly worse evidence than the string ITSELF,
        # which the re-sourcing block below matches by content.
        #
        # MEASURED (boot 74, zm_nuked): 235 WEAPON string pointers resolved into
        # arbitrary bodies -- XMODEL, XANIMPARTS, TECHNIQUE_SET, FX, WEAPON_CAMO
        # -- with many distinct PC strings collapsing onto the SAME console
        # offset (0xA394FE43 repeatedly). In b70 all 98 weapons' string slots
        # resolved into STRINGTABLE with ZERO garbage. Nothing about the string
        # logic changed; b71 simply ADDED regions (rule D bodies, the restored
        # skybox, the camo align), so PC string addresses that used to fall
        # through to content re-sourcing now land inside some region and are
        # claimed by it first. The engine caught it cleanly:
        #   "could not find altWeapon '<garbage>' for weapon 'defaultweapon_mp'"
        # ⇒ Same disease as rule (EX) in zmconv_b: a fallback that GUESSES
        # instead of deferring. Here the fix is order, not refusal -- try the
        # approximate answer LAST, after content has had its chance.
        approx_hit = None
        for regs in (self.regions, prior_regions):
            for (ps, pe, cs, exact) in regs:
                if ps <= b5 < pe:
                    if b5 == ps:
                        self.stats['start'] += 1
                        return self._encode(cs + (b5 - ps))
                    if exact:
                        self.stats['interior_exact'] += 1
                        return self._encode(cs + (b5 - ps))
                    approx_hit = (ps, cs)       # FIRST match wins, as before
                    break
            if approx_hit is not None:
                break
        # CONTENT RE-SOURCING: the PC linker dedups strings into interiors we
        # cannot map (substituted techset blobs; drift bands). If the PC
        # target is a C-string, point at the same string in OUR OWN emitted
        # stream (previous pass: same content, same offsets) — semantically
        # identical to what both linkers did, just with our copy.
        if self.PC is not None and self._prev_stream is not None:
            tgt = self._pc_cstring(b5)
            if tgt is not None:
                hit = self._prev_stream.find(b'\x00' + tgt + b'\x00')
                if hit >= 0:
                    if approx_hit is not None:
                        # the case boot 74 died on: an approximate region had
                        # claimed this string. Count it so the swing is visible.
                        self.stats['resourced-over-approx'] = \
                            self.stats.get('resourced-over-approx', 0) + 1
                    self.stats['resourced'] = self.stats.get('resourced', 0) + 1
                    return self._encode(hit + 1)
        # Content could not place it -> fall back to the approximate region
        # answer, i.e. EXACTLY today's behaviour for everything that is not a
        # re-sourceable string. This keeps the change surgical: the only
        # pointers that move are the ones content can identify better.
        if approx_hit is not None:
            ps, cs = approx_hit
            self.stats['interior_approx'] += 1
            return self._encode(cs + (b5 - ps))
        # DROPPED-ASSET REFS -> NULL. When a whole asset class is dropped
        # (author_zone drop_names), pointers to it from OTHER assets would
        # otherwise dangle. A NULL is the engine-blessed "none" for these
        # (FxEffectDef* is null-checked at every consumer), so resolve them
        # EXPLICITLY rather than leaving them unresolved -- the fatal bar must
        # keep meaning "silent dangling pointer", so we do not hide them in it.
        if self.dropped_spans:
            for (ps, pe) in self.dropped_spans:
                if ps <= b5 < pe:
                    # ⛔ NOT NULL. Boot 42 resolved these to 0 on the premise that
                    # FxEffectDef* is null-checked at every consumer — REFUTED by
                    # a fault at guest 0x00000000. At least one consumer derefs it
                    # unconditionally. Use the BOOT_SAFE in-bounds mirror instead:
                    # the documented blind-ZM fallback, where a deref reads valid
                    # (garbage) in-zone memory rather than a wild or null pointer.
                    self.stats['dropped-mirror'] = self.stats.get('dropped-mirror', 0) + 1
                    return self._unres_value()
        # diagnostic: attribute the unresolved target to its PC asset type
        if self.pc_spans is not None:
            for (ps, pe, root) in self.pc_spans:
                if ps <= b5 < pe:
                    key = 'unres:' + (root or '?')
                    self.stats[key] = self.stats.get(key, 0) + 1
                    break
            else:
                # outside every PC span: almost certainly FLOAT data that
                # happens to parse in the alias range (e.g. -1.0 = 0xBF800000)
                # — poisoning would corrupt content; pass through unchanged.
                self.stats['outside-passthrough'] = \
                    self.stats.get('outside-passthrough', 0) + 1
                return v
        self.stats['unresolved'] += 1
        # emit a TAGGED poison value (top of block-5 range, far beyond any real
        # runtime address) instead of passing the PC value through: a passthrough
        # PC alias can accidentally land inside a legitimate range and masquerade
        # as a (wrong) pointer. Tags resolve to None on our side; the gate then
        # classes gen-GfxWorld pairs as pending (Track F), anything else as bad.
        return self._unres_value()


def assemble_zone(pc_path, verbose=True, pc_policy=None, our_policy=None,
                  container_prefix=0, container_narr=None,
                  inserts=None, idx_remap=None, override_rtmap=None, drops=None):
    """`container_prefix`/`container_narr`: for REAL container authoring the
    emitted block-5 stream is preceded by the script-string table + XAsset
    array, so body pointers must encode runtime addresses relative to the true
    block-5 start (genuine formula: base = assets_end-64 + 8-align shift). Pass
    container_prefix = align4(string_region) (block-5 offset of the array) and
    container_narr = console asset count. Default 0/None keeps the synthetic
    array-at-0 model the stream-space gate relies on (invariant either way).
    `inserts` = {pc_asset_index: (name, root, body_bytes)} console-only bodies
    emitted immediately AFTER that PC asset's body (e.g. the mpl_<map>.english
    SndBank after the main SOUND). Threaded through EVERY pass incl. the pass-3
    runtime sim — an insert shifts every downstream runtime address, so it must
    never be patched in post-hoc. `idx_remap`: PC array index -> console array
    index (slot-handle relocation across inserted rows).

    APPEND KEY -1 (2026-08-08, zm_nuked_patch _zm_ai_dogs transplant):
    `inserts[-1] = [(name, root, body_bytes), ...]` appends console-only bodies
    AFTER the last PC body, i.e. at the very END of the stream. Deliberately
    distinct from a positional insert: nothing follows, so NO downstream runtime
    address moves and `idx_remap` needs no term at all — the matching array rows
    are appended at the end of the row list by the container author. Use this
    (not a positional key) for additive assets that nothing points at."""
    """TWO-PASS body-emission loop over a PC map zone in authored order. Pass 1
    builds the complete PC->console offset map (omap) from every emitted body's
    size; pass 2 re-emits with the full map so FORWARD alias references resolve
    (converters bake pointer values inline — one pass can only see backward refs).
    Returns (stats, emitted_assets, omap); emitted_assets =
    [(idx, name, root, bytes|None, why)]. Coverage-first: a type without a
    converter emits None and is COUNTED — the loop names the remaining gaps."""
    import struct_layout as SL
    PC = open(pc_path, 'rb').read()
    bodies, brk = walk_pc_bodies(PC)
    # map name from pc_path — derived HERE because the rule-(D) install below
    # needs it to locate this map's OAT dump. ⛔ Was an inline
    # `.replace('_pc', '')`, a GLOBAL replace that would also eat an interior
    # '_pc'; oat_ptrtable owns the suffix-only form so it stays the inverse of
    # ptrtable_path(). Byte-identical on every current fleet map.
    import oat_ptrtable as _OP
    _map_name = _OP.map_name_from_zone_path(pc_path)

    # ---- rule (D): dedup-skinned XSurface resolution state ----
    # PC-derived and pass-invariant, so build ONCE here, before any emit pass.
    # UNCONDITIONAL by design, not opt-in: a zone with no dedup-skinned surface
    # never reaches a lookup, so this is a strict no-op everywhere else (gate:
    # raid/transit/dockside emit byte-identically). Where a lookup DOES happen and
    # cannot uniquely resolve, convert_surface_header still raises rule (D) — the
    # refusal is narrowed, never removed. SPEC_ruleD_inline_dedup_skinned.md.
    #
    # ⛔ ONE INSTALLER, TWO CALL SITES (here and ruleD_xmodel_census.py). Do not
    # inline the index build again: a census that installs different state from
    # the build reports a different number with neither erroring, which is the
    # defect FINDINGS_mirage_last_two.md §6.1 records. `map_name` additionally
    # arms the OAT third selector where a dump exists; without one the behaviour
    # is exactly the two-selector behaviour that shipped before.
    XC.install_dedup_skin_state(PC, bodies, map_name=_map_name, verbose=verbose)
    Lp = SL.Layout(W.HDR, console=False)
    zc = W.ZoneCode(W.ZC_DIR)
    # ITEM 5: per-map vshaderTail delta override
    _tail_delta_override = VSHADER_TAIL_DELTA.get(_map_name)

    # ---- I1 stage 1b: NAME-STRING dedup-alias IDENTITY table ----
    # OPT-IN PER MAP, same discipline as the matHandles binder below. Built here
    # (PC-side data only, before any emit pass can act on it) and FROZEN: the
    # answers are a function of the PC zone alone. Size-neutral by construction
    # -- every answer is applied as a re-source onto our own copy of the string.
    _sid_env = os.environ.get('T6_I1_STRID')
    _sid_on = (_sid_env == '1') if _sid_env is not None else (_map_name in I1_STRID_MAPS)
    strid = None
    if _sid_on:
        import string_identity as SID
        strid = SID.build(PC, bodies, _map_name, verbose=verbose)
        if not strid.answers:
            # A table with no answers must not masquerade as "clean" (rule EC:
            # a gate that skips must SHOUT).
            # ASCII ONLY (rule AE): a non-ASCII char in a printed string raises
            # UnicodeEncodeError on a cp1252 stdout, i.e. on every redirected or
            # logged build, and inside a broad `except` that skips the rest of
            # the block and silently bakes different bytes.
            print('  I1 string-identity: NO answers for %s -- every name-string '
                  'alias keeps the address chain (see refusals above)'
                  % _map_name)
            strid = None

    # ---- I1 stage 1: XModel.materialHandles dedup-alias IDENTITY binder ----
    # OPT-IN PER MAP. Any map not listed keeps the generic reloc chain and is
    # byte-identical to before I1, so the raid/skate lineages cannot move.
    # T6_I1_MHARR=0 forces it off everywhere (attribution kill switch);
    # T6_I1_MHARR=1 forces it on for the map being built.
    _i1_env = os.environ.get('T6_I1_MHARR')
    _i1_on = (_i1_env == '1') if _i1_env is not None else (_map_name in I1_MHARR_MAPS)
    mharr = None
    if _i1_on:
        import dedup_binder as DBIND
        mharr = DBIND.MharrBinder(slack=I1_MHARR_SLACK, verbose=verbose)
        DBIND.LAST = mharr             # post-mortem handle for diagnostics
        if verbose:
            print('  I1 mharr binder: ENABLED for %s (slack %d) -- matHandles '
                  'dedup back-refs bind by IDENTITY, not by address'
                  % (_map_name, I1_MHARR_SLACK))

    # ---- techset substitution lookup (Track B): asset start offset -> console blob bytes ----
    # Manifest (pc_name -> console corpus name) from the per-zone JSON if present, else translate().
    ts_by_off = {}
    ts_gap = None
    try:
        import json
        import techset_translate as TT
        corpus = TT.load_corpus()
        zbase = os.path.basename(pc_path).rsplit('.', 1)[0].replace('_pc', '')
        man_path = os.path.join(TT.CORPUS_DIR, zbase + '_subst.json')
        if os.path.exists(man_path):
            man = json.load(open(man_path))['map']
        else:
            man = TT.emit_manifest(pc_path, corpus=corpus, verbose=False)['map']
        # Source techset offsets from the MAIN body walk (walk_pc_bodies), which
        # carries the inline asset_span consumers and reaches EOF. The legacy
        # TT.pc_techset_names_walk lacks those hooks and DRIFTS mid-zone (e.g.
        # zm_transit: dies at asset 506 on an FX type-62 inline visual), which
        # left every techset after the drift point with no ts_by_off entry ->
        # ~180 spurious "techset-subst: no blob". bodies already has the exact
        # console-loadable body offset (s) and the inline name is at s+0.
        _sidx_off = TT.build_struct_index(set(corpus))
        for (_i, _nm, _root, _s, _e, _hp) in bodies:
            if _root != 'MaterialTechniqueSet' or _s is None or _e is None:
                continue
            name = TT._pc_name(PC, _s)
            if name is None:
                continue
            name = name.lstrip(',')
            entry = man.get(name)
            cn = entry['console'] if entry else (name if name in corpus else None)
            if cn is None:
                fb = TT.struct_fallback(name, _sidx_off, set(corpus))
                if fb is None:
                    # hash-named family (effect_<hash> etc.): same-family
                    # pi-sig substitute (zm_nuked step-0 gap; choice logged)
                    fb = TT.family_sig_fallback(PC, _s, name, corpus)
                    if fb:
                        # ⛔ PRINT fb[2], NOT a hardcoded label. This line used
                        # to say 'family-sig' unconditionally; once the relaxed
                        # first-token branch landed (techset_translate 2026-08-17)
                        # that would have logged a WEAKER substitution under the
                        # STRICT branch's name -- the silent widening the relaxed
                        # branch was explicitly written to avoid, defeated by its
                        # own caller's log string. A distinct how-string is only
                        # worth returning if somebody prints it.
                        print('  techset %s subst: %s -> %s'
                              % (fb[2], name, fb[0]))
                cn = fb[0] if fb else None
            if cn is None:
                continue
            cpath = corpus[cn]['path']
            if not os.path.isabs(cpath):
                cpath = os.path.join(TT.ROOT, cpath)
            ts_by_off[_s] = open(cpath, 'rb').read()
        # INLINE-techset blob hook: materials with a FOLLOW/INSERT techniqueSet
        # (GfxWorld materialMemory/sunflare/tail, zm attachment materials) get
        # the Track B substitute blob EMITTED IN PLACE — console loadability
        # requirement (CAVEATS_gfxworld_trackF.md §Integration item 1).
        _corp_names = set(corpus)
        _sidx = TT.build_struct_index(_corp_names)
        _ts_cache = {}

        def _inline_ts_hook(pcb, off2):
            nm = TT._pc_name(pcb, off2)
            if nm is None:
                return None
            nm = nm.lstrip(',')
            if nm in _ts_cache:
                return _ts_cache[nm]
            ent = man.get(nm)
            cn = ent['console'] if ent else (nm if nm in _corp_names else None)
            if cn is None:
                fb = TT.struct_fallback(nm, _sidx, _corp_names)
                if fb is None:
                    fb = TT.family_sig_fallback(pcb, off2, nm, corpus)
                cn = fb[0] if fb else None
            blob = None
            if cn is not None:
                p2 = corpus[cn]['path']
                if not os.path.isabs(p2):
                    p2 = os.path.join(TT.ROOT, p2)
                blob = open(p2, 'rb').read()
            _ts_cache[nm] = blob
            return blob
        MC.INLINE_TECHSET_HOOK = _inline_ts_hook
    except Exception as ex:
        ts_gap = 'techset lookup failed: %s' % str(ex)[:80]

    from collections import defaultdict

    def emit_pass(omap):
        """One emit pass over all walked bodies with the given omap. Returns
        (stat, out_assets, conv). omap regions are (re)added as bodies emit."""
        reloc = omap.reloc
        if omap.ts_trace is not None:
            del omap.ts_trace[:]           # keep only the final pass's trace
        conv = P2C.PCConverter(PC, Lp, zc)
        conv.regions = []
        conv.ext_reloc = reloc             # cross-type alias fallback (shared omap)
        conv.encode = omap._encode         # runtime-address pointer encoding (pass 3)
        conv.pc_inv = omap.pc_inv          # PC-runtime -> PC-stream reverse map
        # FIX B: inline-name policy — install the alias->string resolver on
        # every converter that emits root NAME words (None disables = byte-
        # exact legacy behavior for oracle-reproduction runs).
        conv.inline_names = INLINE_ASSET_NAMES
        _resolver = omap.pc_name if INLINE_ASSET_NAMES else None
        XC.INLINE_NAME_RESOLVER = (omap.pc_name_xmodel if INLINE_ASSET_NAMES
                                   else None)   # + skybox rule
        SC.INLINE_NAME_RESOLVER = _resolver
        FXC.INLINE_NAME_RESOLVER = _resolver
        # FIX B lightdef-name repair (PM-ruled re-bake ingredient, 2026-08-13).
        # Installed HERE, inside emit_pass, so all three passes see the same
        # table: the branch it gates CHANGES BODY LENGTH (the old code appends
        # name+NUL), so a pass-1/pass-3 disagreement would silently corrupt the
        # PC->console offset map. `strid` is None for every map outside
        # I1_STRID_MAPS, which is what keeps raid/skate byte-identical.
        SC.LIGHTDEF_STRID = strid
        # Stage 2 (registry campaign): et7 (MODEL) visuals aliases resolve to
        # XAsset-entry slots via the targeted resolver — BEFORE the generic
        # reloc, and refusing (not guessing) on any word outside the law.
        # T6_ET7_OFF=1 is an ATTRIBUTION-ONLY kill switch (falls back to the
        # generic reloc, which on every measured map mints the identical
        # handles via the pc_arr branch) — used to prove the resolver
        # byte-neutral on the raid gate; never set it for a shipping build.
        FXC.ET7_VISUAL_RESOLVER = (None if os.environ.get('T6_ET7_OFF') == '1'
                                   else omap.reloc_asset_entry)
        # rule (K) ledger wrapper for et<=6 visuals aliases: resolution is the
        # generic reloc UNCHANGED, but a boot-safe/poison substitution is
        # ledgered + printed (SUBSTITUTED not DERIVED) instead of vanishing
        # into the bytes. (BN): the retired fxnull nearest-backward stamp is
        # never minted in this path.
        del omap.subst_ledger[:]

        def _et6_ledger_reloc(v, _o=omap, _r=reloc):
            u0 = _o.stats['unresolved']
            r = _r(v)
            # ledger only the pass whose bytes SHIP (rtmap set = final pass);
            # pass 1/2 legitimately fail forward refs before the prior map
            # exists and would spam false SUBSTITUTED lines.
            if _o.stats['unresolved'] > u0 and _o.rtmap is not None:
                # ⛔ (GZ) TRUTH CHECK, landed 2026-08-16 — A SUBSTITUTION MAY
                # NEVER SHARE ITS VALUE WITH THE UNRESOLVED COUNTER.
                # This wrapper is a LEDGER, not a resolver: `r = _r(v)` is the
                # generic reloc unchanged, and the line it prints says
                # "SUBSTITUTED ... placeholder" for a pointer that was in fact
                # UNRESOLVED — `_unres_value()` returns
                # `0xBF000001 + stats['unresolved']`, so the "placeholder" IS
                # the unresolved value and the increment is the same event.
                # A whole design ruling was once built on reading that log line
                # as evidence of a resolution the assembler bar counts. It is
                # not. Enforce the invariant rather than trusting the label:
                # if a future edit ever returns a REAL substitute here while
                # still incrementing `unresolved`, that is the confusion (GZ)
                # exists to stop, and it fails loudly instead of silently
                # inflating an apparent resolve rate.
                _gz_assert_ledger_not_resolution(v, r, _o)
                _o.subst_ledger.append(('FxElemDef.visuals et<=6', _o.ctx, v, r))
                print('  rule-K SUBSTITUTED (not DERIVED): FX et<=6 visuals '
                      'alias 0x%08X -> 0x%08X in %s -- %s placeholder, '
                      'ledgered' % (v, r, (_o.ctx or ('?',) * 4)[1],
                                    'boot-safe' if BOOT_SAFE_UNRESOLVED
                                    else 'poison'))
            return r
        FXC.ET6_VISUAL_RELOC = _et6_ledger_reloc
        # I1 stage 1: the matHandles identity binder. Installed only when the
        # map opted in; otherwise XC.MHARR_BINDER stays None and
        # convert_xmodel_materialhandles behaves exactly as it did before I1.
        # Pass 1 builds the PC census; from pass 2 on it also answers. The
        # holder registry is our-stream-offset-valued, so it is rebuilt from
        # scratch every pass.
        XC.MHARR_BINDER = mharr
        if mharr is not None:
            mharr.reset_pass()
            mharr.encode = omap._encode
            mharr.pc_arr = omap.pc_arr
        if omap.strid is not None:
            omap.strid.reset_pass()        # mint trace is per-pass, like mharr's
        co_cursor = 0                      # console block-5 write position
        omap._prev_stream = omap.cur_stream   # last pass's stream (re-sourcing)
        emitted = bytearray()
        out_assets = []
        stat = defaultdict(lambda: [0, 0, 0])  # root -> [emitted, bytes, missing]
        omap._prior = (omap.fine, omap.regions)   # pass-1 maps serve forward refs
        omap.fine = []
        omap.regions = []                  # rebuilt this pass
        omap.scaled = []
        omap._fine_idx = None
        # (GY) receipt: every asset that produced no body, with its shape. The
        # bake now REFUSES on the first one (see below), so this carries at most
        # one entry per run -- but it is the named record the receipt prints,
        # and it stays a list so a future `drops`-declared skip can be counted
        # here without changing the shape of the receipt.
        _emit_skips = []
        for (i, nm, root, s, e, hp) in bodies:
            if drops and i in drops:
                # DROPPED asset (e.g. the ZM map SndBank): no array row (the
                # container author skipped it) and no body in the stream, so
                # array rows and emitted bodies stay in lockstep + block5 shrinks.
                out_assets.append((i, nm, root, None, 'dropped'))
                continue
            if s is None or e is None:
                out_assets.append((i, nm, root, None, 'aliased/no-root'))
                continue
            nreg = len(conv.regions)
            omap.ctx = (i, nm, root, s)
            if mharr is not None:
                mharr.begin_asset(co_cursor)
            body, why = emit_one(root, s, e, conv, reloc, co_cursor)
            # --- bisection support (temporary; BISECT_* default None = no-op) ---
            if BISECT_LOG is not None and s is not None:
                BISECT_LOG.setdefault(s, (root, len(body) if body is not None else 0))
            if body is not None and BISECT_MAP is not None and s in BISECT_MAP:
                body = BISECT_MAP[s]
                conv.xc_scaled = None; conv.xc_fine = None
                conv.regions = conv.regions[:nreg]
                why = 'bisect-transplant'
            if body is not None:
                if len(conv.regions) > nreg:       # P2C emit: exact sub-regions
                    omap.add_fine(conv.regions[nreg:])
                if getattr(conv, 'xc_scaled', None):
                    omap.add_scaled(conv.xc_scaled)
                    conv.xc_scaled = None
                if getattr(conv, 'xc_fine', None):
                    omap.add_fine(conv.xc_fine)
                    conv.xc_fine = None
                if root in EVENT_FINE:
                    # event-paired fine regions: PC and console serialize the
                    # same allocation sequence, so seg k on the PC side maps
                    # exactly to seg k of our emitted body (fixes the linear
                    # cross-platform drift, e.g. clipMap's -92 early class)
                    fine = _event_fine(PC, s, bytes(body), root, co_cursor)
                    if fine:
                        omap.add_fine(fine)
                exact = root in P2C.SIMPLE or root in P2C.WORLD
                if root == 'MaterialTechniqueSet':
                    omap.ts_co[s - B5_BASE] = co_cursor
                    omap.ts_olen[s - B5_BASE] = len(body)
                omap.add(s - B5_BASE, e - s, co_cursor, exact)
                co_cursor += len(body)
                emitted += body
                stat[root][0] += 1
                stat[root][1] += len(body)
            else:
                stat[root][2] += 1
                # ⛔⛔ (GY)/(GZ) ANTI-LAUNDERING, landed 2026-08-16 (mirage lane;
                # Track-F lane reviewing). A `body is None` HERE is an
                # UNDECLARED silent content loss, and it is never benign:
                #
                #   * DECLARED drops never reach this point -- they `continue`
                #     at the `drops` branch above, which skips row AND body so
                #     they "stay in lockstep" (that comment is the invariant).
                #   * so everything arriving here has an XAsset ROW already
                #     authored by produce_container.author_rows (:79-98), which
                #     writes 'follow' unconditionally and never consults whether
                #     a body emitted. hp == 0xFFFFFFFF with no body in the
                #     stream DESYNCS THE LOADER.
                #
                # MEASURED, three maps, three different diseases from ONE
                # launderer (the blanket `except` in emit_one):
                #   mirage  21 (GfxWorld 27.75 MB + 18 FX + MTS + XModel) --
                #           would have desynced at asset 916 of 954
                #   highrise 11 (10 rule-(D) refusals + 1 techset miss), ALL
                #           hp=0xFFFFFFFF -- Die Rise latent-desynced BY DESIGN
                #   nuked/raid/skate/transit: 0 -- shipping lanes are clean,
                #           which is why this can be fatal today
                #
                # THREE SHAPES REACH HERE and all three must be caught -- a fix
                # written only around the `except` misses the third:
                #   1. an exception from the converter itself
                #      (gfxworld_gx2 "lightmap shape 512x1536 not 512-block-pairable")
                #   2. an exception raised INSIDE reloc
                #      (reloc_asset_entry's et7/asset-entry AssertionError)
                #   3. a MISS that never enters the `except` at all
                #      (`body = ts_by_off.get(s)` -> why='techset-subst: no blob')
                #
                # Extends the precedent 5 lines up in emit_one: `except
                # FatalBakeError: raise` -- "never downgrade a fatal fixup
                # failure to a dropped asset". This is that comment applied to
                # the class it was written for.
                #
                # TO ALLOW ONE: declare it in `drops`. That is the NAMED SKIP
                # path and it is lockstep-correct by construction (row and body
                # both skipped). ⛔ Do NOT add an allowlist here -- an allowlist
                # at this site would drop the body while author_rows still
                # writes the FOLLOW row, i.e. the same desync with a label on
                # it. A named skip needs genuine evidence, per (GG).
                _emit_skips.append((i, nm, root, why))
                # ⚠ CENSUS MODE (region_registration_gate) — the ONLY way to
                # enumerate the FULL unaccounted set. With the fatal armed a
                # real build dies on the FIRST one (mirage: asset 158) and can
                # never show you the other 20, so the "21" calibration arm
                # would be unmeasurable. Off by default; the build-time
                # refusal below is untouched.
                if GY_CENSUS_MODE:
                    continue
                raise FatalBakeError(
                    'emit produced NO BODY for asset %d %r (root=%s) and it was '
                    'not a declared drop -- refusing to bake a zone whose XAsset '
                    'row says FOLLOW with nothing in the stream (loader desync). '
                    'reason: %s  [(GY) anti-laundering; declare it in `drops` if '
                    'this skip is intended and has genuine evidence] '
                    '(NOTE: a body of the WRONG LENGTH is NOT caught here -- this '
                    'checks EXISTENCE only; surviving this check does NOT mean the '
                    'row/body stream is in lockstep)'
                    % (i, nm, root, why or '<none recorded>'))
            if mharr is not None:
                # promote this asset's holder-cell offsets ONLY if its body is
                # actually going into the stream at co_cursor. A rejected or
                # transplanted body occupies different (or no) bytes, so its
                # staged offsets would be lies.
                mharr.commit_asset(body is not None
                                   and why != 'bisect-transplant')
            out_assets.append((i, nm, root, body, why))
            if inserts and i in inserts:
                # console-only insert body: no PC span/omap region (nothing in
                # the PC zone points at it) — only occupies stream + runtime
                inm, iroot, ibody = inserts[i]
                co_cursor += len(ibody)
                emitted += ibody
                stat[iroot][0] += 1
                stat[iroot][1] += len(ibody)
                out_assets.append((None, inm, iroot, ibody, 'console-insert'))
        if inserts and -1 in inserts:
            # END-OF-STREAM appends (see docstring). Nothing follows them, so no
            # runtime address shifts; the container author appends the matching
            # array rows at the end of `rows` so rows/bodies stay in lockstep.
            for (inm, iroot, ibody) in inserts[-1]:
                co_cursor += len(ibody)
                emitted += ibody
                stat[iroot][0] += 1
                stat[iroot][1] += len(ibody)
                out_assets.append((None, inm, iroot, ibody, 'console-append'))
        omap._prior = None
        omap.cur_stream = bytes(emitted)
        return stat, out_assets, conv

    def emit_one(root, s, e, conv, reloc, co_cursor):
        body = None
        why = ''
        try:
            if root == 'XModel':
                marks = []
                body, _ = XC.convert_xmodel(PC, s, reloc, marks=marks)
                conv.xc_scaled = []
                conv.xc_fine = []
                for m in marks:
                    if m[0] == 'scaled':
                        _, ps, co, cnt, sp2, sc2 = m
                        conv.xc_scaled.append(
                            ('scaled', ps - B5_BASE, co_cursor + co,
                             cnt, sp2, sc2))
                    else:
                        _, ps, co, ln = m
                        conv.xc_fine.append((ps - B5_BASE, co_cursor + co, ln))
            elif root == 'FxEffectDef':
                body, _, _ = FXC.convert_fx(PC, s, reloc)
            elif root == 'SndBank':
                body, _ = SC.convert_sndbank(PC, s, reloc)
            elif root == 'XAnimParts':
                body, _ = SC.convert_xanim(PC, s, reloc)
            elif root == 'DestructibleDef':
                body, _ = SC.convert_destructible(PC, s, reloc)
            elif root == 'PhysPreset':
                body, _ = SC.convert_physpreset(PC, s, reloc)
            elif root == 'GfxLightDef':
                body, _ = SC.convert_lightdef(PC, s, reloc)
            elif root == 'Glasses':
                body, _ = SC.convert_glasses(PC, s, reloc)
            elif root == 'clipMap_t':
                import clipmap_convert as CLC
                body, _ = CLC.convert_clipmap(PC, s, reloc)
            elif root == 'GameWorldMp':
                body, _ = SC.convert_gameworldmp(PC, s, reloc)
            elif root == 'ScriptParseTree':
                body, _ = SC.convert_scriptparsetree(PC, s, reloc)
            elif root == 'MenuList':
                body, _ = SC.convert_menulist(PC, s, e, reloc)
            elif root == 'SkinnedVertsDef':
                body, _ = SC.convert_skinnedverts(PC, s, reloc)
            elif root == 'ZBarrierDef':
                body, _ = ZA.convert_zbarrier(PC, s, reloc)
            elif root == 'XGlobals':
                body, _ = ZA.convert_xglobals(PC, s, reloc)
            elif root == 'FootstepTableDef':
                body, _ = ZA.convert_footsteptable(PC, s, reloc)
            elif root == 'FootstepFXTableDef':
                body, _ = ZA.convert_footstepfxtable(PC, s, reloc)
            elif root == 'TracerDef':
                body, _ = ZCB.convert_tracerdef(PC, s, reloc)
            elif root == 'WeaponCamo':
                body, _ = ZCB.convert_weaponcamo(PC, s, reloc)
            elif root == 'VehicleDef':
                import zmconv_c as VC
                body, _ = VC.convert_vehicledef_span(PC, s, e, reloc)
            elif root == 'WeaponVariantDef':
                import weapon_convert as WVC
                body, _ = WVC.convert_weapon(PC, s, reloc)
            elif root == 'Material':
                # FIX A (glass boot-52 class): texture tables register as EXACT
                # fine regions so texdef image dedup aliases resolve onto the
                # true holder slot, never a coarse-map neighbor.
                mmk = []
                body, _ = MC.convert_material(PC, s, reloc, marks=mmk)
                if mmk:
                    conv.xc_fine = [(ps - B5_BASE, co_cursor + co, ln)
                                    for (ps, co, ln) in mmk]
            elif root == 'GfxImage':
                body, _ = MC.convert_image(PC, s, reloc)
            elif root in P2C.SIMPLE or root in P2C.WORLD:
                body, _ = conv.convert(root, s, co_cursor + B5_BASE, keep_regions=True)
                if root == 'RawFile':
                    # boot-39: '.atr' payloads carry a u32 uncompressed-size
                    # prefix the console reads BIG-endian; pc_to_console copies
                    # the payload verbatim so PC's LE word would ship through.
                    # Size-preserving, so conv.regions / omap stay valid --
                    # which is why this stays INSIDE the SIMPLE branch.
                    body = SC.fix_rawfile_atr_prefix(body)
                if getattr(conv, 'unresolved', 0):
                    why = 'p2c-unresolved:%d' % conv.unresolved
            elif root == 'MaterialTechniqueSet':
                body = ts_by_off.get(s)               # Track B: self-contained console blob
                if body is None:
                    why = ts_gap or 'techset-subst: no blob for asset @0x%x' % s
            elif root == 'GfxWorld':
                # Track F emit: console GfxWorld bytes with PC alias values at
                # `fixups` offsets — rewrite each through the shared omap.
                # Emit once per offset (expensive), re-reloc every pass.
                import gfxworld_emit as GEM
                # image_source (PC-ipak resolver) is REQUIRED for GfxWorld resident
                # images (tail lut ~262KB + materialMemory inline images ~133KB) or
                # they fall back to stubs -> the material stream is short and the
                # console loader mis-relocates (CAVEATS_gfxworld_trackF.md item 4).
                ck = (len(PC), s)
                cached = _GFX_EMIT_CACHE.get(ck)
                if cached is None:
                    cached = GEM.emit_gfxworld(
                        PC, s, ctx={'image_source': GFXWORLD_IMAGE_SOURCE,
                                    'sampler_lookup': getattr(MC, 'SAMPLER_LOOKUP', None),
                                    # the assemble rebases the vshaderTail post-
                                    # emit (once rtmap exists), so silence the
                                    # emit-time "not rebased" warning here.
                                    'defer_tail_rebase': True})
                    _GFX_EMIT_CACHE[ck] = cached
                data, fx, _log = cached
                # region-pair the fine map (part B session 2, item 3): PC
                # marked regions <-> our emitted console regions. Same-size
                # regions map linearly (exact); size-changing regions map
                # start-only; materialMemory additionally pairs its leading
                # (Material*, memory) ARRAY (stride 8 on both sides) — the
                # target of the dpvs.surfaces field-dedup family.
                pairs = _GFX_PAIR_CACHE.get(ck)
                if pairs is None:
                    pairs = GEM.region_pairs(PC, s, _log)
                    _GFX_PAIR_CACHE[ck] = pairs
                conv.xc_fine = []
                for (pa, pb, co, cl, meth, key) in pairs:
                    if pb - pa == cl:
                        conv.xc_fine.append((pa - B5_BASE, co_cursor + co, cl))
                    else:
                        if key == 'materialMemory':
                            nmm = struct.unpack_from('<I', PC, s + 572)[0]
                            conv.xc_fine.append(
                                (pa - B5_BASE, co_cursor + co, nmm * 8))
                        else:
                            conv.xc_fine.append((pa - B5_BASE, co_cursor + co, 4))
                b = bytearray(data)
                for f in fx:
                    struct.pack_into('>I', b, f,
                                     reloc(struct.unpack_from('>I', b, f)[0]))
                # ITEM 5 (arms fix auto-wire, boot-50/51): the vshaderTail's 18
                # intra-tail attrib-name aliases hold RAID's b5 payloads; they
                # must be rebased to OUR runtime tail placement or the multi-
                # bone gpuskin fetch shaders bake semantic 0xFF and every map in
                # the process explodes. The GfxWorld emit is CACHED before the
                # runtime map exists, so we cannot rebase at emit time — do it
                # here, in the final (rtmap-set) pass. Size-neutral, so pass 1/2
                # (rtmap None -> skipped) keep identical layout.
                if omap.rtmap is not None:
                    _tp = os.path.join(os.path.dirname(GEM.__file__),
                                       'gfxworld_vshader_tail.bin')
                    # (AE)+(EC), 2026-08-13 defect: this block prints NOTHING
                    # while it works and swallows NOTHING when it fails. The
                    # warning below used to sit mid-block carrying a U+26A0, so
                    # on a cp1252 stdout (any redirected build log) the print
                    # raised UnicodeEncodeError, the blanket `except` ate it,
                    # and the rebase two lines down NEVER RAN -- a logged build
                    # and an interactive build baked different bytes, with the
                    # only trace a stats key nobody reads. Messages are queued
                    # as ASCII and printed AFTER the work; failures are fatal.
                    _msg = []
                    try:
                        _tail = open(_tp, 'rb').read()
                        ti = b.find(_tail)
                        _dup = ti >= 0 and b.find(_tail, ti + 1) >= 0
                        if ti < 0 or _dup:
                            # (EC) a gate that skips must SHOUT: not rebasing is
                            # indistinguishable, in the zone, from rebasing by 0.
                            _why = ('AMBIGUOUS: >1 tail template match'
                                    if _dup else
                                    'tail template NOT FOUND in emitted body')
                            omap.stats['vshader-tail-SKIP'] = _why
                            _msg.append('  !! vshaderTail NOT REBASED (%r): %s'
                                        ' -- arms/characters will explode if '
                                        'this zone ships the raid tail'
                                        % (_map_name, _why))
                        else:
                            if _tail_delta_override is not None:
                                delta = _tail_delta_override
                            else:
                                # runtime b5 of vsin_bone0 (@tail+1910) in OUR
                                # zone -- EXACT only where the runtime model is
                                # exact (queue item 1); warn so it is never
                                # silently trusted (a wrong delta re-explodes
                                # the arms without any other symptom).
                                bone0_rt = int(omap.rtmap.rt(co_cursor + ti + 1910))
                                delta = bone0_rt - GEM.RAID_BONE0_RT
                                omap.stats['vshader-tail-COMPUTED'] = delta
                                if verbose:
                                    _msg.append(
                                        '  !! vshaderTail delta COMPUTED from '
                                        'model (0x%x) -- precision-dependent; '
                                        'set VSHADER_TAIL_DELTA[%r] if arms '
                                        'explode' % (delta, _map_name))
                            b[ti:ti + len(_tail)] = GEM.rebase_vshader_tail(
                                bytes(_tail), delta)
                            omap.stats['vshader-tail-rebased'] = delta
                    except Exception as _ex:
                        # the exception TEXT is itself untrusted for encoding --
                        # a UnicodeEncodeError quotes the character it choked on,
                        # so printing it raw would re-raise here and lose the
                        # fatal. Force it to ASCII before it touches stdout.
                        _det = ('%s: %s' % (type(_ex).__name__, _ex)).encode(
                            'ascii', 'backslashreplace').decode('ascii')
                        omap.stats['vshader-tail-ERR'] = _det[:60]
                        for _m in _msg:
                            print(_m)
                        print('!!!!!! FATAL: vshaderTail rebase FAILED on %r: %s'
                              % (_map_name, _det))
                        raise FatalBakeError('vshaderTail rebase failed on %r: %s'
                                             % (_map_name, _det))
                    for _m in _msg:
                        print(_m)
                body = bytes(b)
            else:
                why = 'NO CONVERTER'
        except FatalBakeError:
            # (EC) never downgrade a fatal fixup failure to a dropped asset:
            # `why='EXC:...'` is one line in a per-asset table, which is not a
            # shout for something that silently changes what the zone bakes.
            raise
        except Exception as ex:
            body = None
            why = 'EXC:%s' % str(ex)[:60]
        return body, why

    # PC-side loader simulation: PC alias values -> PC stream offsets
    import loader_sim as LS
    em_pc, _pc_spans, _ = LS.simulate_pc(PC, verbose=verbose, policy=pc_policy)
    omap0_pc_inv = LS.InverseMap(em_pc.omap)
    rp = pc_zone.PCZoneReader(PC); rp.read_string_table(); rp.read_asset_list()
    n_assets = len(rp.assets)

    # PASS 1: sizes/regions only (forward refs unresolved). PASS 2: full map.
    omap = Omap()
    omap.pc_inv = omap0_pc_inv
    omap.pc_arr = (rp.assets_off - B5_BASE, n_assets)
    omap.pc_assets = rp.assets             # et7/asset-entry class: type check
    omap.strid = strid                     # I1 stage 1b name-string identities
    omap.container_prefix = container_prefix
    _narr = container_narr if container_narr is not None else n_assets
    # ⭐⭐ align4, NOT align8 — landed 2026-08-17 (PM-ruled, option 1: writer
    # matches reader). MEASURED, not assumed:
    #   * the container writes the array at RAW `prefix` with NO pad — its own
    #     `assert len(content) == 24 + prefix + narr * 8` proves it, and a
    #     big-endian decode of our output on DISCRIMINATING phases confirms it
    #     (dockside_ours arr%4=1: raw 16/16 entry-shaped, align4 0/16, align8 0/16).
    #   * the console loader places it at align4 at RUNTIME. 16 genuine Wii U
    #     zones; on every phase where the three candidates differ, align4 wins
    #     by 60–200x:  nightclub arr%8=3 (MIRAGE'S PHASE) align4 8664 hits vs
    #     align8 43 · overflow(2) 4726 vs 71 · express(1) 4044 vs 417 ·
    #     hijacked(4) 3719 vs 260.
    #   * non-8-aligned genuine arrays are ORDINARY: phases 1,2,3,4 all occur.
    # ⭐ AND THE READER HALF WAS ALREADY RIGHT: loader_sim's rule AX/AC uses
    #   `((arr + 3) & ~3)` "measured on all 71 genuine zones", as do
    #   zone_gates and fx_oracle. This writer was the ONLY holdout, so this is
    #   a correction to match a measured rule, not a new derivation.
    # ⚠ BLAST RADIUS: `_shift` also feeds `_base_rt` below — the runtime base
    #   of the ENTIRE body stream. On any map with prefix%8 in {1,2,3,4} every
    #   rt-derived alias moves by 4. That is a NO-OP on the five legacy maps
    #   (align4 == align8 at their phases) — but VERIFY it against the live
    #   artefacts (P5), never by construction. mp_downhill (phase 4) is
    #   EXPECTED to change and has no baseline.
    _shift = ((container_prefix + 3) & ~3) - container_prefix
    omap.our_arr = container_prefix + _shift   # array runtime base (align4, rule AX/AC)
    if idx_remap is not None:
        omap.idx_remap = idx_remap
    omap.pc_spans = [(s - B5_BASE, e - B5_BASE, root)
                     for (i, nm, root, s, e, hp) in bodies if s is not None and e]
    omap.dropped_spans = [(s - B5_BASE, e - B5_BASE)
                          for (i, nm, root, s, e, hp) in bodies
                          if drops and i in drops and s is not None and e]
    omap.PC = PC
    for (i, nm, root, s, e, hp) in bodies:
        if root == 'GfxWorld' and s is not None and e:
            omap.gfx_pc_span = (s - B5_BASE, e - B5_BASE)
        if root == 'MaterialTechniqueSet' and s is not None and e:
            omap.ts_spans.append((s - B5_BASE, e - B5_BASE))
    if omap.gfx_pc_span is not None:
        import bisect
        ks = sorted(em_pc.omap)
        s5, e5 = omap.gfx_pc_span
        i = bisect.bisect_left(ks, e5)
        omap.gfx_pc_rt_span = (em_pc.omap.get(s5),
                               em_pc.omap[ks[i]] if i < len(ks) else None)
        # FIX B skybox rule: the skyBoxModel XModel's NAME aliases the string
        # INSIDE the PC GfxWorld (uninvertable interior). Extract the string
        # from the PC GfxWorld itself so pc_name_xmodel can resolve it.
        if INLINE_ASSET_NAMES and omap.gfx_pc_rt_span[0] is not None:
            try:
                import gfxworld_emit as GEM
                for (k, a, b) in GEM._pc_marks(PC, s5 + B5_BASE):
                    if k == 'skyBoxModel' and b > a:
                        nmb = PC[a:b].split(b'\x00')[0]
                        if 3 <= len(nmb) <= 96 and all(
                                0x20 <= c <= 0x7e for c in nmb):
                            omap.name_hint = (omap.gfx_pc_rt_span[0],
                                              omap.gfx_pc_rt_span[1],
                                              bytes(nmb))
                        break
            except Exception:
                omap.name_hint = None
    emit_pass(omap)
    # I1 stage 1: pass 1 has now walked every emitted XModel, so the PC
    # matHandles census is complete. Resolve it ONCE, on PC-side data only,
    # before any pass that can act on the answers. From here the census is
    # FROZEN: a later pass observing a cell it does not contain is a defect and
    # is counted (F-W1), never silently absorbed.
    if mharr is not None:
        _st = mharr.freeze_and_resolve(omap.pc_arr, slack=I1_MHARR_SLACK)
        if verbose:
            print('  I1 mharr resolve: %s' % _st)
        if _st.get('infeasible_at') is not None:
            print('  I1 mharr resolve: CHAIN INFEASIBLE at payload %s -- the '
                  'binder mints NOTHING this build (loud, not a fallback guess)'
                  % _st['infeasible_at'])
        if _st.get('neg_events'):
            raise AssertionError(
                'I1 mharr resolve: %d negative-tot events -- the E0 defect '
                'class (a false zero deletes a feasible candidate and can mint '
                'a WRONG word silently). Refusing to build.'
                % _st['neg_events'])
    omap.begin_pass()          # (HB) archive the outgoing pass's totals
    stat, out_assets, conv = emit_pass(omap)

    # PASS 3 — loader-simulation runtime pointer pass: walk OUR emitted stream
    # with the calibrated console-loader allocation model (loader_sim) to map
    # every stream position to its RUNTIME block-5 address, then re-emit with
    # pointers encoding runtime addresses (what genuine alias values encode).
    import loader_sim as LS
    stream = bytearray(b'\x00' * B5_BASE)      # keep b5 == offset-64 convention
    meta = []
    for (i, nm, root, body, why) in out_assets:
        if body is None:
            meta.append((nm, False, None))
        else:
            meta.append((nm, True, None, len(body)))   # authoritative span length
            stream += body
    # content runtime base sits after the (real or synthetic) container prefix +
    # XAsset array. container_prefix=0 -> synthetic array-at-0 model (n*8).
    _base_rt = container_prefix + _narr * 8 + _shift
    em_rt, spans_rt = LS.simulate_stream(bytes(stream), meta, B5_BASE, _base_rt,
                                         verbose=verbose, policy=our_policy)
    walked = sum(1 for sp in spans_rt if sp[4] > sp[3])
    omap.rtmap = LS.RuntimeMap(em_rt.omap)
    if override_rtmap is not None:
        # DUMP-CALIBRATED runtime map: the sim's console runtime model is off by
        # the (un-derivable) gfx/structural band; a full-memory dump of a boot
        # gives the loader's REAL per-asset block-5 layout, which does NOT depend
        # on our (wrong) alias values. Bake pointers against the measured layout.
        override_rtmap.sim = omap.rtmap        # fallback for unmeasured spans
        omap.rtmap = override_rtmap
    omap.rt_spans = spans_rt                   # our-stream asset spans (for the gate)
    omap.block_size = list(em_rt.w.block_size)  # runtime-inclusive block sizes (header)
    omap.external_size = getattr(em_rt.w, 'external_size', 0)
    omap.begin_pass()          # (HB) archive the outgoing pass's totals
    stat, out_assets, conv = emit_pass(omap)
    if verbose:
        print("  runtime pass: sim walked %d/%d emitted assets" %
              (walked, sum(1 for m in meta if m[1])))
    # I1 stage 1b ACCEPTANCE GATE: every name-string site minted this (shipping)
    # pass must point, in OUR OWN emitted stream, at a NUL-terminated copy of
    # exactly the string the PC zone holds inline for that payload. This is the
    # deliverable's acceptance test, enforced in-build rather than reported:
    # a wrong-target mint is precisely the defect class this module removes, so
    # it must abort the build, not decorate it.
    if strid is not None:
        _bad = strid.verify(omap.cur_stream)
        omap.stats['i1-strid-minted'] = strid.stats['resourced']
        if strid.stats['unfound']:
            omap.stats['i1-strid-unfound'] = strid.stats['unfound']
        omap.strid_obj = strid
        if verbose:
            print('  I1 string-identity (final pass): %d sites re-sourced onto '
                  'our own copy, %d refused (no copy in our stream), %d '
                  'acceptance failures'
                  % (strid.stats['resourced'], strid.stats['unfound'], len(_bad)))
        if _bad:
            raise AssertionError(
                'I1 string-identity: %d minted name-string sites do NOT name '
                'the expected string in our own stream: %s'
                % (len(_bad), _bad[:6]))
    # I1 stage 1: report the FINAL (shipping) pass and un-install the hook so a
    # second author_zone in the same process cannot inherit it.
    if mharr is not None:
        mharr.print_summary('(final pass)')
        omap.stats['i1-mharr-minted'] = mharr.minted
        omap.stats['i1-mharr-refused'] = (mharr.refuse_ambiguous
                                          + mharr.refuse_no_answer
                                          + mharr.refuse_unreached)
        omap.mharr = mharr
        _dp = os.environ.get('T6_I1_MHARR_DUMP')
        if _dp:
            import json as _json
            _json.dump(dict(summary=mharr.summary(),
                            answers={'0x%08X' % k: v
                                     for k, v in sorted(mharr.answers.items())},
                            feasible_size={'0x%08X' % k: v for k, v
                                           in sorted(mharr.feasible_size.items())},
                            cells=[[c[0], c[1], c[2], c[3]] for c in mharr.cells],
                            owners=mharr.owners,
                            minted=[list(t) for t in mharr.trace]),
                       open(_dp, 'w'))
            print('  I1 mharr binder: dumped answers to %s' % _dp)
    XC.MHARR_BINDER = None
    SC.LIGHTDEF_STRID = None       # same reason: a second author_zone in this
                                   # process must not inherit this map's table

    # FATAL BAR: every unresolved pointer must be attributable to the (expected,
    # Track F pending) GfxWorld refs or the tiny pass-through oddball class.
    # Anything else is a silent dangling pointer — refuse to hand the zone on.
    accounted = (omap.stats.get('unres:GfxWorld', 0) +
                 omap.stats.get('unres:techset-interior', 0) +
                 omap.stats.get('unres:<outside>', 0) +
                 # ZM weapons (zm_transit): console WeaponVariantDef layout is
                 # not header-derivable and has NO converter — its interior
                 # refs stay unresolved by design (raid/dockside have 0
                 # weapons, so this term is 0 on the MP oracle bar)
                 omap.stats.get('unres:WeaponVariantDef', 0))
    if omap.stats['unresolved'] > accounted:
        raise AssertionError(
            'assemble: %d unresolved pointers NOT attributable to GfxWorld '
            '(stats: %s)' % (omap.stats['unresolved'] - accounted,
                             {k: v for k, v in omap.stats.items() if v}))

    if verbose:
        total_e = sum(v[1] for v in stat.values())
        print("=== assemble coverage: %s ===" % os.path.basename(pc_path))
        for root, (n, b, miss) in sorted(stat.items(), key=lambda kv: -kv[1][1]):
            flag = '' if miss == 0 else '   MISSING x%d' % miss
            print("  %-22s emitted x%-4d %10.1f KB%s" % (root, n, b / 1e3, flag))
        print("  emitted total: %.1f MB   omap: %s" % (total_e / 1e6, dict(omap.stats)))
        if omap.subst_ledger:              # rule (K): the count must surface
            print("  rule-K subst ledger: %d entries (SUBSTITUTED not DERIVED)"
                  % len(omap.subst_ledger))
        if brk:
            print("  walk break:", brk)
    return stat, out_assets, omap


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '../PC ff/mp_raid.zone'
    assemble_zone(path)


if __name__ == '__main__':
    # rule (AE) closure: force utf-8 BEFORE any work, so no print in this
    # process can raise UnicodeEncodeError on a redirected (cp1252) stdout and
    # silently skip the statements after it. Entry point only -- never at
    # import, which would impose a process-global side effect on our importers.
    import stream_utf8
    stream_utf8.force()
    main()
