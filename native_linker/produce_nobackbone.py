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
        self.stats = dict(start=0, interior_exact=0, interior_approx=0, unresolved=0,
                          sentinel=0, other=0)

    def _encode(self, co_b5):
        """Encode a pointer to our-stream offset co_b5. With a runtime map (the
        loader-simulation pass) the value is the loader's RUNTIME address —
        the address space genuine alias pointers use; without it, stream-linear."""
        if self.rtmap is not None:
            co_b5 = self.rtmap.rt(co_b5)
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
                self.stats['unresolved'] += 1
                self.stats['unres:techset-interior'] = \
                    self.stats.get('unres:techset-interior', 0) + 1
                if self.ts_trace is not None:
                    self.ts_trace.append((self.ctx, v, b5, ts_s, ts_e))
                return self._unres_value()
        prior_fine, prior_regions = self._prior if self._prior else ((), ())
        sc = self._scaled_lookup(b5)       # element-scaled XModel regions
        if sc is not None:
            self.stats['interior_scaled'] = \
                self.stats.get('interior_scaled', 0) + 1
            return self._encode(sc)
        hit = self._fine_lookup(b5)        # exact converter/event sub-regions first
        if hit is not None:
            ps, pe, cs = hit
            self.stats['start' if b5 == ps else 'interior_exact'] += 1
            return self._encode(cs + (b5 - ps))
        for regs in (self.regions, prior_regions):
            for (ps, pe, cs, exact) in regs:
                if ps <= b5 < pe:
                    if b5 == ps:
                        self.stats['start'] += 1
                    else:
                        self.stats['interior_exact' if exact else 'interior_approx'] += 1
                    return self._encode(cs + (b5 - ps))
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
                    self.stats['resourced'] = self.stats.get('resourced', 0) + 1
                    return self._encode(hit + 1)
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
    index (slot-handle relocation across inserted rows)."""
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
    Lp = SL.Layout(W.HDR, console=False)
    zc = W.ZoneCode(W.ZC_DIR)
    # ITEM 5: per-map vshaderTail delta override (map name from pc_path)
    _map_name = os.path.basename(pc_path).rsplit('.', 1)[0].replace('_pc', '')
    _tail_delta_override = VSHADER_TAIL_DELTA.get(_map_name)

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
                        print('  techset family-sig subst: %s -> %s' % (name, fb[0]))
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
                    try:
                        _tail = open(_tp, 'rb').read()
                        ti = b.find(_tail)
                        if ti >= 0 and b.find(_tail, ti + 1) < 0:
                            if _tail_delta_override is not None:
                                delta = _tail_delta_override
                            else:
                                # runtime b5 of vsin_bone0 (@tail+1910) in OUR
                                # zone — EXACT only where the runtime model is
                                # exact (queue item 1); warn so it is never
                                # silently trusted (a wrong delta re-explodes
                                # the arms without any other symptom).
                                bone0_rt = int(omap.rtmap.rt(co_cursor + ti + 1910))
                                delta = bone0_rt - GEM.RAID_BONE0_RT
                                omap.stats['vshader-tail-COMPUTED'] = delta
                                if verbose:
                                    print('  ⚠ vshaderTail delta COMPUTED from '
                                          'model (0x%x) — precision-dependent; '
                                          'set VSHADER_TAIL_DELTA[%r] if arms '
                                          'explode' % (delta, _map_name))
                            b[ti:ti + len(_tail)] = GEM.rebase_vshader_tail(
                                bytes(_tail), delta)
                            omap.stats['vshader-tail-rebased'] = delta
                    except Exception as _ex:
                        omap.stats['vshader-tail-ERR'] = str(_ex)[:60]
                body = bytes(b)
            else:
                why = 'NO CONVERTER'
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
    _narr = container_narr if container_narr is not None else n_assets
    _shift = ((container_prefix + 7) & ~7) - container_prefix
    omap.our_arr = container_prefix + _shift   # array runtime base (8-aligned)
    if idx_remap is not None:
        omap.idx_remap = idx_remap
    omap.pc_spans = [(s - B5_BASE, e - B5_BASE, root)
                     for (i, nm, root, s, e, hp) in bodies if s is not None and e]
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
    omap.stats = dict(start=0, interior_exact=0, interior_approx=0, unresolved=0,
                      sentinel=0, other=0)
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
    omap.stats = dict(start=0, interior_exact=0, interior_approx=0, unresolved=0,
                      sentinel=0, other=0)
    stat, out_assets, conv = emit_pass(omap)
    if verbose:
        print("  runtime pass: sim walked %d/%d emitted assets" %
              (walked, sum(1 for m in meta if m[1])))

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
        if brk:
            print("  walk break:", brk)
    return stat, out_assets, omap


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '../PC ff/mp_raid.zone'
    assemble_zone(path)


if __name__ == '__main__':
    main()
