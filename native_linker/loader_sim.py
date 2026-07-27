#!/usr/bin/env python3
"""
LOADER SIMULATION (Track G pointer pass, stage 1: the simulator core).

Maps every stream position of a console zone to the LOADER's runtime (block,
offset) address — the address space genuine alias pointers encode. Proven facts
it must reproduce (HANDOFF_assemble_pointer_model.md):
  * genuine StringTable dedup aliases = source_stream − 53 on mp_raid (7009 refs)
  * different regions sit at different phases -> per-span policy, not a constant
  * console linker packs FOLLOW arrays (NO defensive align-4; body_relayout note)

POLICY (fit empirically, validated by calibrate()): the asset ROOT STRUCT bytes
load into the reusable TEMP block (consume no block-5 space); ALL follower data
(names, arrays, strings) is VIRTUAL and consumes 1:1. Knobs stay explicit so the
calibration can falsify them per zone.

Built on body_relayout.ReEmitter (round-trip-proven structural walk) via
subclassing — body_relayout itself is untouched (shared file).
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import struct_layout, walker as W, zone_stream as zs
import body_relayout as BR
import wiiu_zone

B5_BASE = 64
FOLLOW = 0xFFFFFFFF


class SimWriter(zs.ZoneWriter):
    """ZoneWriter that can route the next write(s) to the TEMP space (block 0,
    cursor never persists) and records (stream_src, size, rt_block, rt_off)
    segments for every virtual write."""
    def __init__(self):
        super().__init__()
        self.temp_next = 0          # bytes of upcoming writes to swallow as TEMP
        self.segments = []          # (rt_off_start, size) parallel to emitter's src

    def write_bytes(self, b):
        n = len(b)
        if self.temp_next > 0:
            take = min(self.temp_next, n)
            self.temp_next -= take
            self.block_size[zs.BLOCK_TEMP] += take
            if take < n:
                self.block_size[self.cur_block] += (n - take)
            return
        self.block_size[self.cur_block] += n

    def align(self, a):
        bs = self.block_size[self.cur_block]
        self.block_size[self.cur_block] = (bs + a - 1) & ~(a - 1)


class SimEmitter(BR.ReEmitter):
    """ReEmitter with the temp-root policy: each top-level asset's root struct
    bytes are TEMP; everything else virtual. The inherited `register` fills
    self.omap: source_b5 -> virtual runtime offset at that point."""

    # console-true root struct sizes where struct_layout/PC headers are wrong
    CONSOLE_ROOT_SIZE = {
        'Material': 104,            # console Material body (Track A)
        'SkinnedVertsDef': 24,      # 8 + 4 extra FOLLOW words (assemble session)
    }

    def __init__(self, zone, layout, zc, writer, policy=None):
        super().__init__(zone, layout, zc, writer)
        self.policy = policy or {}

    def _root_size(self, root):
        if root in self.CONSOLE_ROOT_SIZE:
            return self.CONSOLE_ROOT_SIZE[root]
        s = self.wk.gs(root)
        return s['size']

    # ---- per-allocation runtime alignment (loader Alloc(align) semantics;
    #      the STREAM stays packed — only the runtime cursor aligns) ----
    def _alloc_align(self, a):
        if self.policy.get('alloc_align', True) and a > 1 and self.w.temp_next == 0:
            self.w.align(a)

    def emit_body(self, struct_name, src_file):
        s = self.wk.gs(struct_name)
        self._alloc_align(min(s.get('align', 4) or 4, 4))
        return super().emit_body(struct_name, src_file)

    def emit_array(self, base, count):
        if base not in self.L.structs:
            sz, _ = self.L._resolve(base)
            self._alloc_align(min(sz, 4))
        return super().emit_array(base, count)

    def emit_asset(self, root, src_file):
        if self.policy.get('temp_roots', True):
            self.w.temp_next = self._root_size(root)
        if self.policy.get('root_align', 0):
            # calibrated OFF: asset roots do NOT align the virtual cursor
            # (root_align=4 shifts raid/dockside/transit by -4; kept as a knob)
            bs = self.w.block_size[zs.BLOCK_VIRTUAL]
            self.w.block_size[zs.BLOCK_VIRTUAL] = (bs + 3) & ~3
        blkname = self.zc.default_block.get(root, 'XFILE_BLOCK_TEMP')
        self.w.push_block(BR.BLOCKMAP.get(blkname, zs.BLOCK_VIRTUAL))
        self.src = src_file
        delim = BR.DELIMITERS.get(root)
        if delim is not None:
            end = delim(self.z, src_file)
            self.register(src_file)
            self.w.write_bytes(self.z[src_file:end])
            self.src = end
            self.w.pop_block()
            return self.src
        ov = self.wk.CONSOLE_OVERRIDE.get(root)
        if ov:
            self.register(src_file)
            self.w.write_bytes(self.z[src_file:src_file + ov['size']])
            self.src = src_file + ov['size']
            if ov.get('no_follow'):
                self.w.pop_block()
                return self.src
        else:
            self.emit_body(root, src_file)
        self.follow(root, src_file, {})
        trail = BR.CONSOLE_TRAIL.get(root)
        if trail:
            self._alloc_align(self.policy.get('trail_align', 4))
            self.w.write_bytes(self.z[self.src:self.src + trail])
            self.src += trail
        self.w.pop_block()
        return self.src


def replay_events(em, w, CO, start, root_size, events):
    """Event replay (runtime/interior model): each 'seg' is one loader
    allocation — align the RUNTIME cursor, register the stream position,
    consume file bytes; each 'skip' allocates runtime-virtual space with NO
    file bytes. The root seg loads into TEMP (temp-root policy)."""
    if em.policy.get('temp_roots', True):
        w.temp_next = root_size
    do_align = em.policy.get('alloc_align', True)
    end = start
    for ev in events:
        if ev[0] == 'seg':
            _, rel, size, align = ev
            if do_align and align > 1 and w.temp_next == 0:
                w.align(align)
            em.register(start + rel)
            w.write_bytes(CO[start + rel:start + rel + size])
            end = start + rel + size
        elif ev[0] == 'temp':
            # file bytes into the TEMP block (inline-asset roots: MapEnts /
            # PhysPreset per the T6 load db): stream advances, virtual doesn't.
            _, rel, size = ev
            em.register(start + rel)
            tn0 = w.temp_next
            w.temp_next = size
            w.write_bytes(CO[start + rel:start + rel + size])
            w.temp_next = tn0
            end = start + rel + size
        else:                                  # runtime allocation
            _, size, align = ev
            if align > 1:
                w.align(align)
            w.block_size[w.cur_block] += size
    return end


def simulate_stream(CO, assets, start_off, base_rt, verbose=False, policy=None):
    """Core walk: `assets` = [(type_name, is_follow, pc_type_id_or_None)] —
    optionally 4-tuples with a KNOWN body length as authoritative fallback
    (our own emitted streams know every span; a per-asset parse gap then
    degrades to a verbatim linear region instead of desyncing the walk).
    Body stream begins at start_off, virtual runtime cursor starts at base_rt.
    Returns (emitter, spans): emitter.omap maps stream block-5 offsets ->
    simulated runtime block-5 offsets; spans = (idx, name, root, start, end)."""
    Lc = struct_layout.Layout(W.HDR, console=True)
    zc = W.ZoneCode(W.ZC_DIR)
    w = SimWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = base_rt
    em = SimEmitter(CO, Lc, zc, w, policy=policy)
    cur = start_off
    spans = []
    import raid_oracle_control as RC
    for i, entry in enumerate(assets):
        nm, is_follow, pc = entry[0], entry[1], entry[2]
        klen = entry[3] if len(entry) > 3 else None

        def _fallback(start, ex, snap=None):
            # authoritative-length resync: verbatim linear region
            if verbose:
                print('sim RESYNC @%d %s (klen %s): %s' % (i, nm, klen, str(ex)[:60]))
            if snap is not None:            # undo partial writes/registrations
                bs, tn0, nomap = snap
                w.block_size[:] = bs
                w.temp_next = tn0
                for kk in list(em.omap)[nomap:]:
                    del em.omap[kk]
            if em.policy.get('temp_roots', True):
                try:
                    w.temp_next = em._root_size(W.ASSET_ROOT.get(nm))
                except Exception:
                    w.temp_next = 0
            em.register(start)
            w.write_bytes(CO[start:start + klen])
            em.src = start + klen
            return start + klen

        root = W.ASSET_ROOT.get(nm)
        if not is_follow:
            spans.append((i, nm, root, cur, cur)); continue
        if root is None or root not in Lc.structs:
            spans.append((i, nm, None, cur, cur)); continue
        if root in ('WeaponVariantDef', 'VehicleDef'):
            # feed the weapon/vehicle delimiter the upcoming body-bearing
            # asset roots up to and INCLUDING the next weapon (ZM weapons and
            # vehicles resync on the follower chain landing exactly on the
            # next strong-start signature, not on an MP RAWFILE cluster)
            nxt = [W.ASSET_ROOT.get(a[0]) for a in assets[i + 1:i + 40] if a[1]]
            if 'WeaponVariantDef' in nxt:
                nxt = nxt[:nxt.index('WeaponVariantDef') + 1]
            hint = {'roots': nxt[:16]}
            psz = em.policy.get('weapon_pc_size', {}).get(i)
            if psz and root == 'WeaponVariantDef':
                # PC-pair size priors: console full weapon = PC + 388,
                # variant-only (no inline WeaponDef) = PC size (measured)
                hint['ends'] = [cur + psz + 388, cur + psz]
            BR.WEAPON_NEXT_HINT = hint
        else:
            BR.WEAPON_NEXT_HINT = None
        pre = em.policy.get('pre_skip')
        if pre and root in pre:
            # piecewise runtime correction BEFORE this asset (measured constants)
            w.block_size[zs.BLOCK_VIRTUAL] += pre[root]
        # console delimiter dispatches (verbatim body, linear interior):
        # the generic ZoneCode walk under-consumes clipMap (~2.17 MB) and
        # SndBank (~12.9 MB incl. inline loadedAssets) and over-consumes
        # XAnimParts — the validated probes walk them byte-exact (raid walks
        # the whole 86 MB zone to EOF with zero leftover).
        import clipmap_console as CC
        import sndbank_probe as SP
        import xanimparts_probe as XA
        import alloc_events as AE
        # event-modeled types: per-allocation interior alignment + runtime
        # skips (HANDOFF_assemble_runtime_interior_model item 1/2)
        CONSOLE_EVENTS = {
            'clipMap_t': (lambda z, o: AE.clipmap_events(
                z, o, '>', mat_span=CC._mat_span,
                dynent_rt=em.policy.get('dynent_rt')), CC.CLIPMAP_ROOT),
            'SndBank':    (lambda z, o: AE.sndbank_events(z, o, '>'), SP.BODY),
            'GameWorldMp': (lambda z, o: AE.gwmp_events(z, o, '>'), 44),
        }
        # exact-rt seam (2026-07-26 pipeline hardening): policy-provided event
        # generators override/extend the built-ins (and win over CONSOLE_DELIM
        # because CONSOLE_EVENTS dispatches first). Entries: root -> (fn, root_size).
        CONSOLE_EVENTS.update(em.policy.get('extra_events') or {})
        import gfxworld_console_span as GCS
        if em.policy.get('co_structural_gfx'):
            # console-side gfx interior model (part B session 2, item 5):
            # linear G2 regions + runtime-virtual knob skips at planes /
            # materialMemory / end. Replaces the single end-loaded gfx_skip
            # (which stays as the anchored END TOTAL: end_residual =
            # gfx_skip - planes_skip - matmem_skip).
            import gfxworld_events as GEV

            def _gfx_console_events(z, o):
                span_end = GCS.parse_gfxworld_console(z, o)
                pol = em.policy
                pk = pol.get('gfx_planes_skip', 0)
                mk = pol.get('gfx_matmem_skip', 0)
                return GEV.gfxworld_console_events(
                    z, o, span_end, planes_skip=pk, matmem_skip=mk,
                    end_residual=pol.get('gfx_skip', 0) - pk - mk)
            CONSOLE_EVENTS['GfxWorld'] = (_gfx_console_events, 1076)
        CONSOLE_DELIM = {
            'XAnimParts': (lambda z, o: XA.parse_xanim(z, o, '>')[0], None),
            # generic walk under-consumes non-raid GfxWorlds (streamInfo
            # 20B prefix + skyBox string + SSkinShaders tail): G2 region walk
            'GfxWorld': (lambda z, o: GCS.parse_gfxworld_console(z, o), None),
        }
        glasses = nm == 'MAP_ENTS' and RC._looks_like_glasses(CO, cur)
        espec = CONSOLE_EVENTS.get(root)
        if espec is not None and not glasses:
            start = cur
            try:
                end, evts = espec[0](CO, cur)
                if klen is not None and end != start + klen:
                    raise RuntimeError('span drift %d != %d' % (end - start, klen))
            except Exception as ex:
                if klen is not None:
                    cur = _fallback(start, ex)
                    spans.append((i, nm, root, start, cur))
                    continue
                if verbose:
                    print('sim BREAK @%d %s: %s' % (i, nm, str(ex)[:70]))
                spans.append((i, nm, root, start, len(CO))); break
            # ANCHOR RE-PHASE (2026-07-26, policy['anchor_rt'] = {asset_idx:
            # measured runtime offset}). Interior alignment pads depend on the
            # ABSOLUTE cursor, so an asset walked at a drifted address computes
            # a different pad than the console does at its real one (measured:
            # a single align-8 site inside a weapon paid 5 bytes instead of 1
            # because the cursor sat at 4 mod 8 instead of 0 mod 8 — the whole
            # residual at the weapon golden points). When a MEASURED anchor
            # exists, walk the asset AT that address so every interior resolves
            # in the console's own phase, then hand the outer walk back the
            # bytes consumed so downstream assets are unaffected.
            # Absent the policy key this is a no-op (raid/skate unchanged).
            anch = (em.policy.get('anchor_rt') or {}).get(i)
            if anch is None:
                replay_events(em, w, CO, start, espec[1], evts)
            else:
                blk = w.cur_block
                v0 = w.block_size[blk]
                w.block_size[blk] = anch
                replay_events(em, w, CO, start, espec[1], evts)
                w.block_size[blk] = v0 + (w.block_size[blk] - anch)
            spans.append((i, nm, root, start, end))
            em.src = cur = end
            continue
        spec = CONSOLE_DELIM.get(root)
        if spec or glasses:
            start = cur
            try:
                if glasses:
                    end, tn, lbl = RC._console_glasses_end(CO, cur), 56, 'GLASSES'
                    root2, nm2 = 'Glasses', lbl
                else:
                    end = spec[0](CO, cur)
                    tn = spec[1] if spec[1] is not None else em._root_size(root)
                    root2, nm2 = root, nm
            except Exception as ex:
                if klen is not None:
                    cur = _fallback(start, ex)
                    spans.append((i, nm, root, start, cur))
                    continue
                if verbose:
                    print('sim BREAK @%d %s: %s' % (i, nm, str(ex)[:70]))
                spans.append((i, nm, root, start, len(CO))); break
            if klen is not None and end != start + klen:
                cur = _fallback(start, 'span drift %d != %d' % (end - start, klen))
                spans.append((i, nm, root, start, cur))
                continue
            if em.policy.get('temp_roots', True):
                w.temp_next = tn
            em.register(cur)
            w.write_bytes(CO[cur:end])
            spans.append((i, nm2, root2, cur, end))
            em.src = cur = end
            if root == 'GfxWorld' and em.policy.get('gfx_skip'):
                w.block_size[zs.BLOCK_VIRTUAL] += em.policy['gfx_skip']
            continue
        if pc is not None and pc in BR.DETECTORS and not BR.DETECTORS[pc](CO, cur):
            real = BR.find_next_body(CO, cur, pc)
            if real and real > cur:
                w.write_bytes(CO[cur:real]); cur = real
        start = cur
        snap = (list(w.block_size), w.temp_next, len(em.omap))
        try:
            cur = em.emit_asset(root, cur)
            if klen is not None and cur != start + klen:
                raise RuntimeError('span drift %d != %d' % (cur - start, klen))
        except Exception as ex:
            if klen is not None:
                cur = _fallback(start, ex, snap)
                spans.append((i, nm, root, start, cur))
                continue
            if verbose:
                print('sim BREAK @%d %s: %s' % (i, nm, str(ex)[:70]))
            spans.append((i, nm, root, start, len(CO))); break
        if root == 'GfxWorld' and em.policy.get('gfx_skip'):
            # GfxWorld runtime allocations (DPVS etc.): per-zone constant,
            # empirically measured from SndBank anchors (handoff item 3)
            w.block_size[zs.BLOCK_VIRTUAL] += em.policy['gfx_skip']
        spans.append((i, nm, root, start, cur))
    return em, spans


def simulate(zone_path_or_bytes, verbose=False, policy=None,
             base_includes_tables=True):
    """Walk a genuine console zone container. See simulate_stream."""
    CO = (zone_path_or_bytes if isinstance(zone_path_or_bytes, (bytes, bytearray))
          else open(zone_path_or_bytes, 'rb').read())
    rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
    base_rt = 0
    if base_includes_tables:
        # loader-visible block-5 space starts with the script-string table +
        # asset array (stream 64..assets_end). The XAsset ARRAY allocates
        # 8-aligned (calibrated: raid/transit phase 0, dockside/la phase 5 ->
        # +3 runtime shift reproduces all four zones' genuine alias values).
        arr = rc.assets_off - B5_BASE
        shift = ((arr + 7) & ~7) - arr
        base_rt = rc.assets_end - B5_BASE + shift
    assets = []
    for i, (cid, pc, nm) in enumerate(rc.assets):
        hp = struct.unpack_from('>I', CO, rc.assets_off + i * 8 + 4)[0]
        assets.append((nm, hp == FOLLOW, pc))
    em, spans = simulate_stream(CO, assets, rc.assets_end, base_rt,
                                verbose=verbose, policy=policy)
    return em, spans, CO


class RuntimeMap:
    """stream b5 offset -> simulated runtime b5 offset, from emitter.omap region
    starts (piecewise: region start + linear within until the next region)."""
    def __init__(self, omap):
        self.keys = sorted(omap)
        self.vals = [omap[k] for k in self.keys]

    def rt(self, src_b5):
        import bisect
        i = bisect.bisect_right(self.keys, src_b5) - 1
        if i < 0:
            return src_b5                      # before first region: identity
        return self.vals[i] + (src_b5 - self.keys[i])


# =====================================================================
# PC-side loader simulation: PC alias values are PC-RUNTIME addresses under
# the SAME linker model (proven: PC mp_raid KVP dedup alias 20956 = stream
# 20968 minus the 12-byte temp root; name at rt 0). Needed to reverse-map PC
# alias targets to PC stream offsets before the PC->console omap applies.
# =====================================================================
class PCSimEmitter(SimEmitter):
    CONSOLE_ROOT_SIZE = {}      # PC roots: trust the PC layout sizes

    def __init__(self, zone, layout, zc, writer, policy=None):
        super().__init__(zone, layout, zc, writer, policy=policy)
        import struct as _s
        self.u32 = lambda o: _s.unpack_from('<I', zone, o)[0]
        self.wk.u16 = lambda o: _s.unpack_from('<H', zone, o)[0]
        self.wk.u32 = lambda o: _s.unpack_from('<I', zone, o)[0]
        L = layout
        self.wk._scalar = lambda base, o: (lambda s: zone[o] if s == 1 else
                                           _s.unpack_from('<H' if s == 2 else '<I', zone, o)[0])(L._resolve(base)[0])

    def emit_asset(self, root, src_file):
        """PC variant: pure structural walk — console-only quirks (DELIMITERS,
        CONSOLE_OVERRIDE, CONSOLE_TRAIL) do NOT apply to PC v147 zones."""
        if self.policy.get('temp_roots', True):
            self.w.temp_next = self._root_size(root)
        blkname = self.zc.default_block.get(root, 'XFILE_BLOCK_TEMP')
        self.w.push_block(BR.BLOCKMAP.get(blkname, zs.BLOCK_VIRTUAL))
        self.src = src_file
        self.emit_body(root, src_file)
        self.follow(root, src_file, {})
        self.w.pop_block()
        return self.src


def simulate_pc(pc_path_or_bytes, verbose=False, policy=None):
    """Walk a PC (v147 LE) zone with the calibrated loader model. Returns
    (emitter, spans, PC): emitter.omap maps PC stream b5 -> PC runtime b5."""
    import pc_zone
    import material_convert as MC
    import fx_pc, xmodel_pc, techset_pc, gfxworld_pc, clipmap_pc, sndbank_pc
    import lightdef_pc, glasses_pc
    import destructibledef_probe as _DP
    import gameworldmp_probe as _GW
    import xanimparts_probe as _XA
    PC = (pc_path_or_bytes if isinstance(pc_path_or_bytes, (bytes, bytearray))
          else open(pc_path_or_bytes, 'rb').read())
    r = pc_zone.PCZoneReader(PC); r.read_string_table(); r.read_asset_list()
    Lp = struct_layout.Layout(W.HDR, console=False)
    zc = W.ZoneCode(W.ZC_DIR)
    w = SimWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    arr = r.assets_off - B5_BASE
    w.block_size[zs.BLOCK_VIRTUAL] = (r.assets_end - B5_BASE) + (((arr + 7) & ~7) - arr)
    em = PCSimEmitter(PC, Lp, zc, w, policy=policy)
    # PC span parsers double as delimiters (verbatim interior, temp root)
    PC_DELIM = {
        'FxEffectDef':          lambda z, o: fx_pc.parse_fx_pc(z, o)[0],
        'XModel':               lambda z, o: xmodel_pc.parse_xmodel_pc(z, o),
        'MaterialTechniqueSet': lambda z, o: techset_pc.parse_techset_pc(z, o),
        'Material':             lambda z, o: MC.convert_material(z, o)[1],
        'DestructibleDef':      lambda z, o: _DP.parse_destructible(z, o, '<')[0],
        'GfxLightDef':          lambda z, o: lightdef_pc.parse_lightdef_pc(z, o),
        'Glasses':              lambda z, o: glasses_pc.parse_glasses_pc(z, o),
        'clipMap_t':            lambda z, o: clipmap_pc.parse_clipmap_pc(z, o),
        'SndBank':              lambda z, o: sndbank_pc.parse_sndbank_pc(z, o),
        'GfxWorld':             lambda z, o: gfxworld_pc.parse_gfxworld_pc(z, o),
        'GameWorldMp':          lambda z, o: _GW.Walker(z, '<', 144).walk(o)[0],
        'XAnimParts':           lambda z, o: _XA.parse_xanim(z, o, '<')[0],
        'GfxImage':             lambda z, o: MC.pc_image_span(z, o),
    }
    # ZM zones: WeaponVariantDef is not generically walkable (inline weapDef /
    # attachment / anim content — the Lane D class). Without a consumer the PC
    # sim BREAKS at the first weapon (zm_nuked @1158) and every post-weapon
    # anchor (SndBank @3132) is unreachable -> derive_pc_policy pass 2 dies.
    # weapon_convert.convert_weapon's pc_end is the proven span (107/107
    # transit). Same delimiter treatment as XModel/FxEffectDef.
    import weapon_convert as _WVC
    PC_DELIM['WeaponVariantDef'] = lambda z, o: _WVC.convert_weapon(z, o)[1]
    # same class: 3 TracerDefs / 8 WeaponCamos carry inline sub-assets the
    # generic walk can't span (zm_nuked sim drifted -94B at TRACER @1151)
    import zmconv_b as _ZCB
    PC_DELIM['TracerDef'] = lambda z, o: _ZCB.convert_tracerdef(z, o)[1]
    PC_DELIM['WeaponCamo'] = lambda z, o: _ZCB.convert_weaponcamo(z, o)[1]
    import alloc_events as AE
    import clipmap_pc
    PC_EVENTS = {
        'clipMap_t': (lambda z, o: AE.clipmap_events(
            z, o, '<', mat_span=clipmap_pc._mat_span,
            dynent_rt=(policy or {}).get('dynent_rt')), 332),
        'SndBank':    (lambda z, o: AE.sndbank_events(z, o, '<'), 4756),
        'GameWorldMp': (lambda z, o: AE.gwmp_events(z, o, '<'), 44),
    }
    if (policy or {}).get('pc_structural_temps') or \
            (policy or {}).get('pc_structural_gfx'):
        # part B (IN PROGRESS, opt-in): structural TEMP walkers — inline
        # Material roots + inline GfxImage bodies + PhysPreset roots load
        # into TEMP (T6 load db); segs packed (part-A-proven); roots
        # self-marked -> root_size 0. GfxWorld = per-region events (image
        # regions TEMP) + the per-zone residual knobs. NOT yet default: the
        # pre-gfx/interior residual knobs and all post-gfx constants must be
        # re-derived under this model before it can replace gfx_skip_pc
        # (see FINDINGS_runtime_interior_model ADDENDUM 8).
        # pc_structural_gfx: the GfxWorld-only subset (region events + the two
        # blind-derived knobs). The full pc_structural_temps additionally flips
        # XModel/FX/Material interiors to structural TEMP walkers — measured
        # 2026-07-10 (part B session 2) to disturb alias emits INTO XModel
        # interiors (dockside clipMap cStaticModel family, 98 ptrbad; raid same
        # words go unresolved) — that interior model stays opt-in until the
        # XModel/FX/techset interior event model is solved.
        import gfxworld_events as GE
        if (policy or {}).get('pc_structural_temps'):
            PC_EVENTS.update({
                'XModel':      (lambda z, o: AE.xmodel_events(z, o, '<'), 0),
                'FxEffectDef': (lambda z, o: AE.fx_events(z, o, '<'), 0),
                'Material':    (lambda z, o: AE.material_events(z, o, '<'), 0),
            })
        PC_EVENTS.update({
            'GfxWorld':    (lambda z, o: GE.gfxworld_pc_events(
                z, o,
                interior_residual=(policy or {}).get('gfx_residual_pc', 0),
                matmem_residual=(policy or {}).get('gfx_matmem_pc', 0)),
                0),
        })
    cur = r.assets_end
    spans = []
    for i, (t, nm, hp) in enumerate(r.assets):
        root = W.ASSET_ROOT.get(nm)
        if hp != FOLLOW or root is None or root not in Lp.structs:
            spans.append((i, nm, root, cur, cur)); continue
        start = cur
        pre = em.policy.get('pre_skip_pc')
        if pre and root in pre:
            w.block_size[zs.BLOCK_VIRTUAL] += pre[root]
        try:
            espec = PC_EVENTS.get(root)
            delim = PC_DELIM.get(root)
            if espec is not None:
                end, evts = espec[0](PC, start)
                replay_events(em, w, PC, start, espec[1], evts)
                cur = end
            elif delim is not None:
                if em.policy.get('temp_roots', True):
                    w.temp_next = em._root_size(root)
                end = delim(PC, start)
                em.register(start)
                w.write_bytes(PC[start:end])
                cur = end
            else:
                cur = em.emit_asset(root, start)
            if root == 'GfxWorld' and em.policy.get('gfx_skip_pc'):
                w.block_size[zs.BLOCK_VIRTUAL] += em.policy['gfx_skip_pc']
        except Exception as ex:
            if verbose:
                print('pc-sim BREAK @%d %s: %s' % (i, nm, str(ex)[:70]))
            spans.append((i, nm, root, start, len(PC))); break
        spans.append((i, nm, root, start, cur))
    return em, spans, PC


class InverseMap:
    """PC runtime b5 -> PC stream b5 (piecewise inverse of emitter.omap)."""
    def __init__(self, omap):
        pairs = sorted(omap.items())            # (stream, rt), both monotonic
        self.rts = [rt for (_, rt) in pairs]
        self.streams = [st for (st, _) in pairs]

    def stream(self, rt_b5):
        import bisect
        i = bisect.bisect_right(self.rts, rt_b5) - 1
        if i < 0:
            return rt_b5
        return self.streams[i] + (rt_b5 - self.rts[i])


def calibrate_pc(pc_path, verbose=True, policy=None):
    """PC-side model check: PC StringTable dedup aliases (PC-runtime encoded)
    must equal the simulated PC runtime address of their source strings."""
    em, spans, PC = simulate_pc(pc_path, verbose=verbose, policy=policy)
    rtmap = RuntimeMap(em.omap)
    ok = bad = 0
    diffs = {}
    for (i, nm, root, cs, ce) in spans:
        if root != 'StringTable' or ce <= cs:
            continue
        n = (struct.unpack_from('<i', PC, cs + 4)[0] *
             struct.unpack_from('<i', PC, cs + 8)[0])
        cells0 = PC.index(b'\x00', cs + 20) + 1
        byhash = {}
        o = cells0 + n * 8
        for k in range(n):
            p, h = struct.unpack_from('<2I', PC, cells0 + k * 8)
            if p == FOLLOW:
                if h not in byhash:
                    byhash[h] = o
                o = PC.index(b'\x00', o) + 1
        for k in range(n):
            p, h = struct.unpack_from('<2I', PC, cells0 + k * 8)
            if not (0xA0000001 <= p <= 0xBFFFFFFF) or h not in byhash:
                continue
            want = zs.encode_ptr(zs.BLOCK_VIRTUAL, rtmap.rt(byhash[h] - B5_BASE))
            if p == want:
                ok += 1
            else:
                bad += 1
                d = ((p - 1) & 0x1FFFFFFF) - rtmap.rt(byhash[h] - B5_BASE)
                diffs[d] = diffs.get(d, 0) + 1
    if verbose:
        print('%s [PC]: ST-dedup ok=%d bad=%d %s' %
              (os.path.basename(pc_path), ok, bad,
               sorted(diffs.items(), key=lambda kv: -kv[1])[:5] if diffs else ''))
    return dict(st_ok=ok, st_bad=bad, diffs=diffs)


def calibrate(zone_path, verbose=True, policy=None):
    """Score the simulator against the zone's own genuine alias values:
    for every b5 alias in the stream, the genuine value should equal
    encode(5, rt(target)) for SOME source stream position the linker meant.
    Direct check: StringTable dedup dataset — alias value vs rt(source string).
    Generic check: fraction of all aliases whose value lands exactly on a
    REGISTERED region start (allocation starts are the only legal targets)."""
    em, spans, CO = simulate(zone_path, verbose=verbose, policy=policy)
    rtmap = RuntimeMap(em.omap)
    rt_starts = set(em.omap.values())

    # ---- direct dataset: StringTable dedup aliases ----
    ok = bad = 0
    diffs = {}
    for (i, nm, root, cs, ce) in spans:
        if root != 'StringTable' or ce <= cs:
            continue
        rows = struct.unpack_from('>i', CO, cs + 8)[0]
        cols = struct.unpack_from('>i', CO, cs + 4)[0]
        n = rows * cols
        cells0 = CO.index(b'\x00', cs + 20) + 1
        byhash = {}
        o = cells0 + n * 8
        for k in range(n):
            p, h = struct.unpack_from('>2I', CO, cells0 + k * 8)
            if p == FOLLOW:
                if h not in byhash:
                    byhash[h] = o
                o = CO.index(b'\x00', o) + 1
        for k in range(n):
            p, h = struct.unpack_from('>2I', CO, cells0 + k * 8)
            if not (0xA0000001 <= p <= 0xBFFFFFFF) or h not in byhash:
                continue
            want = zs.encode_ptr(zs.BLOCK_VIRTUAL, rtmap.rt(byhash[h] - B5_BASE))
            if p == want:
                ok += 1
            else:
                bad += 1
                d = ((p - 1) & 0x1FFFFFFF) - rtmap.rt(byhash[h] - B5_BASE)
                diffs[d] = diffs.get(d, 0) + 1

    # ---- generic: all aliases land on allocation starts ----
    hit = miss = 0
    for q in range(B5_BASE, len(CO) - 4):
        v = struct.unpack_from('>I', CO, q)[0]
        if 0xA0000001 <= v <= 0xBFFFFFFF:
            if ((v - 1) & 0x1FFFFFFF) in rt_starts:
                hit += 1
            else:
                miss += 1
    if verbose:
        print('%s: ST-dedup ok=%d bad=%d %s' %
              (os.path.basename(zone_path), ok, bad,
               ('residuals ' + str(sorted(diffs.items(), key=lambda kv: -kv[1])[:5])) if diffs else ''))
        print('  all-alias allocation-start hits: %d / %d (%.1f%%)' %
              (hit, hit + miss, 100.0 * hit / max(hit + miss, 1)))
    return dict(st_ok=ok, st_bad=bad, hit=hit, miss=miss, diffs=diffs)


def derive_gen_policy(zone_path, pc_path=None, verbose=True):
    """Derive the genuine-zone runtime constants from the zone's OWN anchors
    (2-map validated method; raid gives 919776/92/7816, dockside 564984/4308/152):
      gfx_skip      : GWMP tree-anchor plateau center (mod-16 grid; within-asset
                      deltas cancel the residue, so any plateau member works)
      pre_skip clip : S_clip - gfx_skip, where S_clip = the EXACT clipMap-start
                      shift from the material-name dedup string-start sweep
      dynent lump   : S_snd - S_clip, where S_snd = the EXACT SndBank shift
                      (SndAlias name -> list-name anchors, single constant)"""
    import bisect as _b
    from collections import Counter as _C
    em, spans, CO = simulate(zone_path, verbose=False, policy=dict(gfx_skip=0))
    ks = sorted(em.omap); vs = [em.omap[k] for k in ks]

    def inv(b5):
        i = _b.bisect_right(vs, b5) - 1
        return ks[i] + (b5 - vs[i])
    rtmap = RuntimeMap(em.omap)
    # --- S_snd: SndBank alias-name anchors (exact) ---
    import _measure_runtime as MR
    c = _C()
    for (i, nm, root, s, e) in spans:
        if root == 'SndBank' and e > s:
            for (v, noff) in MR.sndbank_anchors(CO, s, '>'):
                c[((v - 1) & 0x1FFFFFFF) - rtmap.rt(noff - 64)] += 1
    S_snd, n_snd = c.most_common(1)[0]
    # --- S_clip: material-name string-start sweep around S_snd ---
    cm = [(s, e) for (i, nm, root, s, e) in spans
          if root == 'clipMap_t' and e > s][0]
    u32 = lambda o: struct.unpack_from('>I', CO, o)[0]
    nmats = u32(cm[0] + 16)
    mb = cm[0] + 332
    vals = [u32(mb + i * 12) for i in range(nmats)
            if 0xA0000001 <= u32(mb + i * 12) <= 0xBFFFFFFF]
    # inline material names of this clipMap: dedup'd alias targets must be
    # string-starts whose CONTENT is one of these names. CAVEAT: any shift
    # that lands every target on some other name start also scores (raid has
    # a false perfect peak at +2132) — take the SMALLEST max-hit shift (2-map
    # consistent: raid 919868 = gate-confirmed, dockside 569292 unique) and
    # let the oracle gate confirm.
    names = set()
    o2 = mb + nmats * 12
    for i in range(nmats):
        if u32(mb + i * 12) == 0xFFFFFFFF:
            e2 = CO.index(b'\x00', o2)
            names.add(bytes(CO[o2:e2]))
            o2 = e2 + 1
    idxs = [i for i in range(nmats)
            if 0xA0000001 <= u32(mb + i * 12) <= 0xBFFFFFFF]

    def _hits(S):
        h = 0
        for i in idxs:
            v = u32(mb + i * 12)
            st = inv(((v - 1) & 0x1FFFFFFF) - S) + 64
            if CO[st - 1] != 0:
                continue
            try:
                if bytes(CO[st:CO.index(b'\x00', st, st + 96)]) in names:
                    h += 1
            except ValueError:
                pass
        return h
    best = (-1, None)
    for S in range(max(0, S_snd - 20000), S_snd + 1):
        hits = _hits(S)
        if hits > best[0]:                 # strict > keeps the SMALLEST peak
            best = (hits, S)
    S_clip = best[1]
    # --- G: GWMP tree plateau (16-grid; pick the max-count center) ---
    gw = [(s, e) for (i, nm, root, s, e) in spans
          if root == 'GameWorldMp' and e > s][0]
    nodes, aliases = MR.gwmp_tree_anchors(CO, gw[0], '>')
    rts = [rtmap.rt(n - 64) for n in nodes]
    cg = _C()
    for v in aliases:
        b5 = (v - 1) & 0x1FFFFFFF
        for r in rts:
            d = b5 - r
            if S_clip - 65536 < d <= S_clip:
                cg[d] += 1
    G = max(cg, key=lambda k: (cg[k], k)) if cg else S_clip
    pol = dict(gfx_skip=G, pre_skip={'clipMap_t': S_clip - G},
               dynent_rt=dict(lump=S_snd - S_clip))
    if verbose:
        print('%s: S_snd=%d (n=%d) S_clip=%d (hits %d/%d) G=%d -> %s' %
              (os.path.basename(zone_path), S_snd, n_snd, S_clip,
               best[0], len(vals), G, pol))
    return pol


def derive_pc_policy(pc_path, verbose=True):
    """Blind-derive the PC-side runtime constants from the PC zone alone,
    under the structural GfxWorld model (pc_structural_gfx) — part B recipe
    (FINDINGS_runtime_interior_model ADDENDUM 8, baked 2026-07-10 session 2):
      A = pre_skip_pc['GfxWorld'] : clipMap-header planes-alias correction
          (the alias is a dedup into gfx dpvsPlanes.planes — absolute anchor)
      B = gfx_residual_pc         : E@gfx-end − E@planes, E@gfx-end from the
          GWMP tree-anchor plateau (max count, tie-high — within-asset deltas
          cancel the residue)
      S_clip (pre_skip_pc clipMap): material-name string-start sweep around
          the PC SndBank family mode (smallest max-hit peak — content anchor,
          pins the frame phase per the mod-12 invariant)
      lump : S_snd − S_clip (the PC SndBank family is the noisiest input)."""
    import bisect as _b
    from collections import Counter as _C
    import _measure_runtime as MR
    import gfxworld_events as GE

    def _sim(pol):
        em, spans, PC = simulate_pc(pc_path, verbose=False, policy=pol)
        return em, spans, PC

    # --- pass 1: knobs off -> measure E@planes and E@end ---
    em, spans, PC = _sim(dict(pc_structural_gfx=True))
    rtmap = RuntimeMap(em.omap)
    u32 = lambda o: struct.unpack_from('<I', PC, o)[0]
    gw = [(s, e) for (i, nm, root, s, e) in spans
          if root == 'GfxWorld' and e > s][0]
    cm = [(s, e) for (i, nm, root, s, e) in spans
          if root == 'clipMap_t' and e > s][0]
    gwmp = [(s, e) for (i, nm, root, s, e) in spans
            if root == 'GameWorldMp' and e > s][0]
    pv = u32(cm[0] + 12)
    if not (0xA0000001 <= pv <= 0xBFFFFFFF):
        raise RuntimeError('clipMap planes not aliased — no planes anchor')
    _, regions = GE.pc_regions(PC, gw[0])
    pr = [(lo, hi) for (lab, lo, hi) in regions
          if lab.startswith('dpvsPlanes.planes')][0]
    A = ((pv - 1) & 0x1FFFFFFF) - rtmap.rt(gw[0] + pr[0] - B5_BASE)
    nodes, aliases = MR.gwmp_tree_anchors(PC, gwmp[0], '<')
    rts = [rtmap.rt(n - B5_BASE) for n in nodes]
    cg = _C()
    for v in aliases:
        b5 = (v - 1) & 0x1FFFFFFF
        for r in rts:
            d = b5 - r
            if -400000 < d < 400000:
                cg[d] += 1
    E_end = max(cg, key=lambda k: (cg[k], k))
    B = E_end - A

    # --- E@matmem: dpvs.surfaces material aliases are FIELD dedups into the
    # materialMemory ARRAY (stride 8, every entry referenced on all zones);
    # E = min distinct alias - model rt of the array start ---
    mm = [(lo, hi) for (lab, lo, hi) in regions
          if lab.startswith('materialMemory')][0]
    sfr = [(lo, hi) for (lab, lo, hi) in regions
           if lab.startswith('dpvs.surfaces')][0]
    sb = gw[0] + sfr[0]
    dmin = None
    for k in range((sfr[1] - sfr[0]) // 80):
        v = u32(sb + k * 80 + 48)
        if 0xA0000001 <= v <= 0xBFFFFFFF:
            b5 = (v - 1) & 0x1FFFFFFF
            dmin = b5 if dmin is None else min(dmin, b5)
    E_mm = (dmin - (rtmap.rt(gw[0] + mm[0] - B5_BASE) + A)
            if dmin is not None else 0)

    # --- pass 2: knobs on -> S_snd, S_clip sweep, lump ---
    em, spans, PC = _sim(dict(pc_structural_gfx=True, gfx_residual_pc=B,
                              gfx_matmem_pc=E_mm,
                              pre_skip_pc={'GfxWorld': A}))
    rtmap = RuntimeMap(em.omap)
    ks = sorted(em.omap); vs = [em.omap[k] for k in ks]

    def inv(b5):
        i = _b.bisect_right(vs, b5) - 1
        return ks[i] + (b5 - vs[i])
    cm = [(s, e) for (i, nm, root, s, e) in spans
          if root == 'clipMap_t' and e > s][0]
    c = _C()
    for (i, nm, root, s, e) in spans:
        if root == 'SndBank' and e > s:
            for (v, noff) in MR.sndbank_anchors(PC, s, '<'):
                c[((v - 1) & 0x1FFFFFFF) - rtmap.rt(noff - B5_BASE)] += 1
    S_snd, n_snd = c.most_common(1)[0]
    nmats = u32(cm[0] + 16)
    mb = cm[0] + 332
    names = set()
    o2 = mb + nmats * 12
    for i in range(nmats):
        if u32(mb + i * 12) == FOLLOW:
            e2 = PC.index(b'\x00', o2)
            names.add(bytes(PC[o2:e2]))
            o2 = e2 + 1
    idxs = [i for i in range(nmats)
            if 0xA0000001 <= u32(mb + i * 12) <= 0xBFFFFFFF]

    def _hits(S):
        h = 0
        for i in idxs:
            v = u32(mb + i * 12)
            st = inv(((v - 1) & 0x1FFFFFFF) - S) + B5_BASE
            if st < 1 or st >= len(PC) or PC[st - 1] != 0:
                continue
            try:
                if bytes(PC[st:PC.index(b'\x00', st, st + 96)]) in names:
                    h += 1
            except ValueError:
                pass
        return h
    best = (-1, None)
    for S in range(S_snd - 30000, S_snd + 30001):
        hits = _hits(S)
        if hits > best[0]:                 # strict > keeps the SMALLEST peak
            best = (hits, S)
    S_clip = best[1]
    pol = dict(pc_structural_gfx=True,
               pre_skip_pc={'GfxWorld': A, 'clipMap_t': S_clip},
               gfx_residual_pc=B, gfx_matmem_pc=E_mm,
               dynent_rt=dict(lump=S_snd - S_clip))
    if verbose:
        print('%s [PC blind]: A=%d B=%d E_mm=%d S_snd=%d (n=%d) S_clip=%d '
              '(hits %d/%d) -> %s' %
              (os.path.basename(pc_path), A, B, E_mm, S_snd, n_snd, S_clip,
               best[0], len(idxs), pol))
    return pol


if __name__ == '__main__':
    calibrate(sys.argv[1] if len(sys.argv) > 1 else '../wiiu_ref/mp_raid_genuine.zone')
