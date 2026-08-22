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
from bake_errors import FatalBakeError
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import struct_layout, walker as W, zone_stream as zs
import body_relayout as BR
import wiiu_zone

B5_BASE = 64
FOLLOW = 0xFFFFFFFF

# ZERO-HIT S_clip EXEMPTION -- PM ruling (b), 2026-08-17.
#
# A zone listed here is allowed to RETURN a zero-hit (window-edge) S_clip rather
# than refuse, because it has a LIVE build whose frame must not move under it.
# The value is returned byte-identical and the derivation still prints loudly
# and records `ok=False`, so `gate_receipt.require` fails the bake -- the
# exemption buys a NAMED failure, never a silent pass.
#
# ⛔ A map is exempt because it is LIVE, never because it is inconvenient.
# Every other map REFUSES (ruling (a)): a first bake must not inherit a value
# with no evidence behind it. Deleting an entry restores the refusal, which is
# the intended end state for zm_nuked once the S_clip anchor is repaired.
# RETIRED 2026-08-20 -- the defect it covered is FIXED, so the entry is gone.
# It existed because the old S_snd-centred window sweep scored 0/261 on zm_nuked, and the
# map was LIVE, so PM ruling (b) 2026-08-17 kept its values byte-identical and LOUD instead
# of refusing. The window has since been replaced by a candidate-enumeration vote (see the
# S_clip block in derive_pc_policy) and zm_nuked now derives S_clip = -464 at 258/261 hits --
# the exact value the old refusal text recorded as the content-vote answer, and one the old
# window could never have reached: nuked's window was [-178168, -118168].
#
# MEASURED 2026-08-20, all four subjects:
#     mp_raid      S_clip -440  223/223   (positive control: the old sweep's known-good answer)
#     zm_nuked     S_clip -464  258/261   (predicted by the old refusal text, not searched for)
#     zm_highrise  S_clip  -16  286/294   (predicted likewise)
#     zm_transit   REFUSED -- 0 SndBank assets, so S_snd has no population. Correct.
#
# An empty set is deliberate, not a placeholder: a map-name allow-list that outlives its
# defect is how an exemption becomes permanent. Re-adding an entry requires a dated reason.
ZERO_HIT_LIVE_EXEMPT = set()

# HOOK-FIRE COUNTERS (rule GX: a claim about whether a policy knob is CONSUMED
# must be measured, not read off a grep). Incremented at the point of USE, never
# at the point of definition. Behaviour-neutral: nothing reads these but reports.
#   'pre_skip_pc:<root>'  -- pre_skip_pc[root] actually applied to BLOCK_VIRTUAL
#   'pre_skip_pc:<root>:bytes' -- signed total applied
# Reset with policy_hooks_reset(); read with policy_hooks().
_POLICY_HOOKS = {}


def policy_hooks_reset():
    _POLICY_HOOKS.clear()


def policy_hooks():
    return dict(_POLICY_HOOKS)


def _hook(key, n=1):
    _POLICY_HOOKS[key] = _POLICY_HOOKS.get(key, 0) + n


class AnchorStalenessRefusal(FatalBakeError):
    """A dump-measured anchor_rt table whose simmap no longer describes this build.

    Inherits FatalBakeError (NOT PolicyRefusal) so it escapes produce_nobackbone's blanket
    handler via the `except FatalBakeError: raise` guard, and is never mistaken for the
    IndexError-shaped "no GfxWorld in this zone" condition. See `simulate_stream`'s docstring
    for the measured evidence."""


class PolicyRefusal(RuntimeError, IndexError):
    """`derive_pc_policy` cannot derive a policy for this zone: one of the
    derivation's PREMISE POPULATIONS is empty.

    WHY THIS EXISTS (fleet defect, reported 2026-08-16 by Track-F, confirmed by
    the mirage lane). `derive_pc_policy` tallied SndBank alias anchors into a
    Counter and then did `c.most_common(1)[0]` unconditionally -- an IndexError
    for any zone that ships no SndBank. `zm_transit` is exactly that zone: its
    map fastfile carries ZERO `SOUND` assets (every other map zone in the corpus
    carries exactly 1), so every lane that derived a policy for transit took a
    hard crash with a traceback that named a Counter, not a missing population.

    ⛔ THE RULE (instrument-laws / rule GX): an empty tally means the
    derivation's PREMISE IS NOT MET for this map. The function must REFUSE with
    a NAMED reason -- which population, which map, what size -- and must never
    (a) crash on a bare index, nor (b) `.get()` its way to a default policy. A
    defaulted constant is a silently WRONG runtime frame, which is the failure
    class that cost boots 87-88 (see `loader-sim-frame-glasses-24576`).

    Subclasses IndexError as well as RuntimeError ON PURPOSE: the pre-existing
    caller at `produce_container.py:1930` catches IndexError to mean "no
    GfxWorld in this patch zone -> policy None", which is a CORRECT reading of
    the same premise-not-met condition. Keeping IndexError in the MRO preserves
    that caller byte-for-byte while giving new callers a named class and a
    machine-readable `.population` / `.premise` / `.zone` to report on.
    """

    def __init__(self, msg, zone=None, premise=None, population=0):
        super().__init__(msg)
        self.zone = zone
        self.premise = premise
        self.population = population


def _premise(seq, premise, zone, note=''):
    """Return seq[0], or REFUSE naming the population that came up empty.

    Every `[0]` in derive_pc_policy is a PREMISE: the derivation is only
    defined for a zone that actually carries that population. Rule (GX) -- the
    refusal cites the file:line where the population is DEFINED and prints its
    size, so a refusal row is self-explaining without re-reading this source.
    The file:line is taken from the CALLER'S FRAME rather than a hand-written
    constant, so it can never go stale as this file is edited.
    """
    if seq:
        return seq[0]
    f = sys._getframe(1)
    where = '%s:%d' % (os.path.basename(f.f_code.co_filename), f.f_lineno)
    raise PolicyRefusal(
        'derive_pc_policy REFUSED for %s: premise population %r is EMPTY '
        '(size 0). Defined at %s.%s No policy exists for this zone -- a '
        'defaulted constant would be a silently wrong runtime frame.'
        % (zone, premise, where, (' ' + note) if note else ''),
        zone=zone, premise=premise, population=0)


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
    simulated runtime block-5 offsets; spans = (idx, name, root, start, end).

    ANCHOR STALENESS GATE (2026-08-20). A dump-measured `policy['anchor_rt']` is built from a
    STORED simmap. Its INDEX DOMAIN is sound -- measured, not assumed: the simmap keeps every
    row's `out_assets` position (3,089 body spans + 71 skipped bodyless rows == max index
    3,159 + 1) and `meta` appends bodyless rows too, so both sides index the same positions.
    (This lane previously claimed a +3 domain fault here. RETRACTED; the +3 belonged to
    `produce_nobackbone.py:277`'s `idx_remap`, a different mapping read as this one.)

    What is NOT sound is the table's VALUES once the asset list changes shape. Measured: the
    stored `_zmnuked_simmap.pkl` matches b94 exactly at 3,089 body spans, while b102 carries
    3,099 -- the ten aliased-techset bodies b96 emitted. Every anchor still names the right
    asset, and every value downstream of the first new body was measured against a stream ten
    bodies shorter. Re-phasing to those values walks assets at addresses that no longer exist,
    and NOTHING detected it: the build succeeded and the error landed in minted pointers.

    So this REFUSES rather than re-measuring (PM ruling: no re-measure campaign; the bake-time
    derivation stage supersedes anchor dependence). It compares the manifest against THIS
    build's ACTUAL asset list -- not a derivation from the same source -- on two properties:
        (1) row count        -- `assets` must be long enough for the simmap's highest index
        (2) per-row identity -- `assets[i][0]` must be the name the simmap recorded at i
    That also closes, without a separate proof, whether pass-2's `out_assets` can differ from
    the final one: if it can, the divergence trips this instead of passing unnoticed.

    Raises `AnchorStalenessRefusal`, which inherits `FatalBakeError` SPECIFICALLY so it
    survives `produce_nobackbone.py`'s `except FatalBakeError: raise` guard ahead of the
    blanket handler below it -- and NOT `PolicyRefusal`, which subclasses IndexError and would
    be swallowed by `produce_container.py`'s `except IndexError` as "no GfxWorld -> policy
    None". A refusal is only a refusal if every handler between it and the driver lets it out."""
    _anch = (policy or {}).get('anchor_rt')
    _man = getattr(_anch, 'manifest', None)
    if _anch and _man:
        if _man['max_index'] >= len(assets):
            raise AnchorStalenessRefusal(
                'anchor_rt is STALE: its simmap %s (md5 %s) records asset index %d, but this '
                'build has only %d assets. The stored anchors were measured against a '
                'DIFFERENT asset list, so their values do not describe this stream. '
                'Re-measure the simmap/realmap pair for this build, or drop anchor_rt.'
                % (_man['source'], _man['md5'][:12], _man['max_index'], len(assets)))
        _bad = [(i, nm, assets[i][0]) for (i, nm) in _man['rows'] if assets[i][0] != nm]
        if _bad:
            raise AnchorStalenessRefusal(
                'anchor_rt is STALE: its simmap %s (md5 %s) disagrees with this build at %d '
                'of %d recorded spans. First 5: %s. The index domains match by construction, '
                'so a NAME mismatch means the asset list CHANGED SHAPE since the simmap was '
                'taken -- every anchor value downstream of the first change was measured '
                'against a different stream. Re-measure, or drop anchor_rt.'
                % (_man['source'], _man['md5'][:12], len(_bad), _man['n_spans'],
                   '; '.join('i=%d simmap=%r now=%r' % b for b in _bad[:5])))
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
            # ⭐ ROOT SIZE 1096, NOT 1076 (corrected 2026-08-20, zm_nuked lane item 4).
            # This is the SECOND copy of the gfxworld_probe2 constants defect, and it is the
            # one that reaches the runtime model: espec[1] is the asset ROOT SIZE handed to
            # replay_events, so at 1076 every GfxWorld interior was replayed with the cursor
            # 20 B short of the loader's. Load_GfxWorld opens Load_Stream(r5=0x448) = 1096
            # (rt_events_gfxworld.py:84 has said so since 2026-07-14); gfxworld_emit.py:135
            # already EMITS 1096. Found by the mirage lane while A/B-ing the probe2 table.
            CONSOLE_EVENTS['GfxWorld'] = (_gfx_console_events, 1096)
        CONSOLE_DELIM = {
            'XAnimParts': (lambda z, o: XA.parse_xanim(z, o, '>')[0], None),
            # generic walk under-consumes non-raid GfxWorlds (streamInfo
            # 20B prefix + skyBox string + SSkinShaders tail): G2 region walk
            'GfxWorld': (lambda z, o: GCS.parse_gfxworld_console(z, o), None),
        }
        glasses = nm == 'MAP_ENTS' and RC._looks_like_glasses(CO, cur)
        # ⭐⭐⭐⭐ (GM) THE GLASSES EVENT MODEL — policy-gated, SPEC_glasses_event_
        # model_rebake.md §3. The delimiter path models a Glasses span as a LINEAR
        # copy, so inline Material/GfxImage roots are charged to block 5 (they
        # belong in TEMP) and the loader's 0x2000 PIXEL PADS are not charged at
        # all. Everything downstream of a Glasses asset then inherits the error.
        # MEASURED ON zm_nuked AGAINST REAL GUEST MEMORY (b88 dump, 519 probes):
        # the sim is EXACT before the Glasses span and 24,576 bytes LOW after it,
        # a clean step -- so every alias we mint past that point resolves 24 KB
        # early. That is what crashed b88: WeaponDef+916 landed on the cstring
        # "viewmodel_default_idle" instead of the all-zero XModel*[16] it named.
        # ⛔ Left OFF by default: this changes the model for every Glasses-bearing
        # zone, and raid/skate are PARKED on measured constants. Opt in per map
        # with policy['glasses_events'].
        was_glasses = False
        gspec = em.policy.get('glasses_events')
        if glasses and gspec is not None:
            espec, glasses, was_glasses = (gspec, 56), False, True
        else:
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
            # keep the delimiter path's labelling: detection and span naming are
            # unchanged by (GM), which touches consumption accounting only
            spans.append((i, 'GLASSES' if was_glasses else nm,
                          'Glasses' if was_glasses else root, start, end))
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
        # 4-ALIGNED, not 8 (rule AX/AC): pad = (-assets_off) mod 4, K = 63-pad,
        # measured on all 71 genuine zones. The old 8-align coincided with 4 on
        # the calibration zones (raid/transit arr%8==0, dockside/la arr%8==5)
        # and was off by exactly 4 on the arr%8 in {1,2,3,4} class -- the whole
        # difference between 0% and 99.9% on mp_hijacked/slums/meltdown/drone/
        # turbine (0/6986 -> 6979/6986 on hijacked; carrier 99.91% unchanged).
        arr = rc.assets_off - B5_BASE
        shift = ((arr + 3) & ~3) - arr
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
            # pc_xmodel_packed=False restores the REAL per-array aligns inside
            # XModel interiors (pc-map session: under structural TEMP the
            # packed model under-allocates by 100-900 B/model, the alignment
            # signature). Default True preserves the part-A-proven behaviour.
            _xm_packed = (policy or {}).get('pc_xmodel_packed', True)
            PC_EVENTS.update({
                'XModel':      (lambda z, o: AE.xmodel_events(
                    z, o, '<', packed=_xm_packed), 0),
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
            _hook('pre_skip_pc:%s' % root)
            _hook('pre_skip_pc:%s:bytes' % root, pre[root])
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
    """PC runtime b5 -> PC stream b5 (piecewise inverse of emitter.omap).

    ⛔⛔ THIS IS AN INTERPOLATION, AND OVER MOST OF THE ADDRESS SPACE THERE IS NOTHING TO
    INTERPOLATE BETWEEN. Measured 2026-08-20 on three PC zones:

        zone         omap entries   address span inside gaps >= 64KB   two widest gaps
        zm_nuked        19,547              93.5 %                    172.4 MB + 67.2 MB
        mp_mirage       34,242              93.5 %                     47.7 MB + 27.8 MB
        mp_raid         28,302              94.2 %                     58.5 MB + 24.8 MB

    The median gap is 8-10 B, so the map is dense where it is dense and then simply STOPS.
    `stream()` brackets a target with bisect and extends linearly from the last mapped point
    below it -- across holes tens of megabytes wide.

    ⭐ WHAT THAT COSTS, MEASURED AGAINST GROUND TRUTH: all ten of zm_nuked's aliased-techset
    bodies fall inside ONE 67.2 MB hole. The hole's endpoints are nearly consistent (1,028 B
    of drift over 67 MB), so the linear model LOOKS reasonable -- and the real offsets are up
    to 47,992,536 B away from what it returns. That is how the b99 registration spent four
    builds registering pixel data as techset spans and only RAISED once, when one row's
    garbage happened to exceed the buffer (b103 diagnostic bake).

    ⛔ A WIDE GAP IS NOT PROOF OF AN ERROR -- interpolation is exact if stream and runtime
    advance together through it. The defect is that NOTHING DISTINGUISHES THE TWO CASES. So
    this does not pretend to fix the arithmetic (you cannot interpolate where there is no
    data); it REPORTS the gap it interpolated across so a caller can refuse. Callers that
    resolve INTERIOR addresses should use `stream_checked` and refuse on a wide gap; the
    sound alternative, where an identity is available, is to locate by NAME instead
    (rule DX -- see aliased_techset_locate.py).

    ⭐ NOT the bug, so nobody re-runs it: rt monotonicity in stream order was CHECKED, not
    assumed -- 0 descents over all 19,547 zm_nuked entries. The bisect is valid.

    ⚠ `stream()` keeps its exact previous behaviour and return type. Every existing caller is
    unchanged by this landing.
    """
    #: gap width (bytes) beyond which an interpolated answer is unbacked by data. Not a law --
    #: a threshold, chosen because the measured holes are 16-172 MB and real inter-entry gaps
    #: are 8-10 B median. State it when you quote a result that depends on it.
    WIDE_GAP = 1 << 16

    def __init__(self, omap):
        pairs = sorted(omap.items())            # (stream, rt); rt monotonic -- verified above
        self.rts = [rt for (_, rt) in pairs]
        self.streams = [st for (st, _) in pairs]

    def _bracket(self, rt_b5):
        """-> (index, gap_width_or_None). gap is None past the last mapped point, where the
        interpolation is unbounded in principle."""
        import bisect
        i = bisect.bisect_right(self.rts, rt_b5) - 1
        if i < 0:
            return -1, None
        gap = (self.rts[i + 1] - self.rts[i]) if i + 1 < len(self.rts) else None
        return i, gap

    def stream(self, rt_b5):
        i, _gap = self._bracket(rt_b5)
        if i < 0:
            return rt_b5
        return self.streams[i] + (rt_b5 - self.rts[i])

    def stream_gap(self, rt_b5):
        """Width of the unmapped gap this answer was interpolated across, or None past the
        end of the map. 0 means the target IS a mapped point."""
        _i, gap = self._bracket(rt_b5)
        return gap

    def stream_checked(self, rt_b5, wide=None):
        """-> (stream_b5, gap). `gap` is the evidence; the CALLER decides.

        Deliberately not a raise: this class has callers that legitimately resolve addresses
        which are mapped points or near them, and turning their working path into an exception
        would be a different change from the one this landing is scoped to."""
        i, gap = self._bracket(rt_b5)
        st = rt_b5 if i < 0 else self.streams[i] + (rt_b5 - self.rts[i])
        return st, gap

    def is_backed(self, rt_b5, wide=None):
        """True when the answer rests on data rather than on extension into a hole."""
        gap = self.stream_gap(rt_b5)
        return gap is not None and gap <= (self.WIDE_GAP if wide is None else wide)


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
        # ORACLE SEMANTICS (rule BZ, baked 2026-07-30): ONE interleaved pass in cell order;
        # a FOLLOW occurrence OVERWRITES the hash slot (linker hash-table semantics — the
        # alias resolves to the MOST RECENT preceding FOLLOW occurrence). See calibrate().
        byhash = {}
        o = cells0 + n * 8
        for k in range(n):
            p, h = struct.unpack_from('<2I', PC, cells0 + k * 8)
            if p == FOLLOW:
                byhash[h] = o
                o = PC.index(b'\x00', o) + 1
                continue
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
        # ORACLE SEMANTICS (rule BZ, baked 2026-07-30 — REFUTES ADDENDUM 10's "one mis-sized
        # late allocation" hypothesis): the T6 cell hash is CASE-INSENSITIVE (djb2-tolower)
        # while the linker dedups case-sensitively; every MP configstrings table carries both
        # 'KILLSTREAK_NULL' and 'killstreak_null' (hash 0x464C04D5, the only zone-wide
        # collision). The linker's hash slot is OVERWRITTEN on each FOLLOW insert, so an
        # alias resolves to the MOST RECENT preceding FOLLOW occurrence — the old two-pass
        # first-occurrence oracle mis-scored exactly those 6-8 aliases per zone (residual ==
        # the stream distance between the two casings, verified 9/9). With this ONE
        # interleaved pass the rt model scores 100.00% of the ST-dedup sample on ALL NINE
        # genuine map zones, ±4 sharpness collapses to 0 both directions, and the per-zone
        # "residual constant" is GONE — it never existed.
        byhash = {}
        o = cells0 + n * 8
        for k in range(n):
            p, h = struct.unpack_from('>2I', CO, cells0 + k * 8)
            if p == FOLLOW:
                byhash[h] = o
                o = CO.index(b'\x00', o) + 1
                continue
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
    """Derive the PC policy, leaving a GATE RECEIPT, or raise PolicyRefusal.

    The receipt is `pc-policy:<zone>` and its POPULATION is the SndBank
    alias-anchor tally that S_snd is the mode of — so `gate_receipt.require`
    fails a build that consumed a policy derived from nothing, and a REFUSED
    zone records population 0 rather than vanishing. See PolicyRefusal for why
    an empty population must never fall back to a default.
    """
    import gate_receipt
    zname = os.path.basename(pc_path)
    try:
        pol, pop, hits, nmats = _derive_pc_policy(pc_path, verbose=verbose)
    except PolicyRefusal as e:
        gate_receipt.record('pc-policy:%s' % zname, False, population=0,
                            detail='REFUSED: premise %r empty' % e.premise)
        raise
    # `ok` is False on a zero-hit S_clip sweep: the policy is still RETURNED
    # unchanged (see the note at the sweep), but the receipt must not read as a
    # clean derivation when the clipMap anchor is a window-edge artifact.
    gate_receipt.record('pc-policy:%s' % zname, hits > 0, population=pop,
                        detail='S_snd tally=%d (SndBank alias anchors); '
                               'S_clip hits=%d/%d%s'
                               % (pop, hits, nmats,
                                  ' *** ZERO-HIT: S_clip is the window lower '
                                  'bound, an artifact ***' if hits <= 0 else ''))
    return pol


def _derive_pc_policy(pc_path, verbose=True):
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

    zname = os.path.basename(pc_path)

    # --- pass 0: CHEAP structural precondition, from the zone's own asset
    # index, BEFORE the two expensive sims.
    #
    # Everything this policy carries is GfxWorld-scoped, so a zone with no
    # GFXWORLD asset can never derive one. Detecting that from the index costs
    # one read_asset_list; detecting it from the sim's span list costs a full
    # structural simulate_pc first. MEASURED on common_mp (206 MB, 6,082
    # assets): the sim route was still running after ~30 min at 18.3 GB RSS
    # before being killed, for a refusal that is structurally guaranteed. The
    # refusal is the SAME premise either way -- this just reaches it without
    # burning the machine (and without OOM-risking other lanes' sessions).
    import pc_zone as _PZ
    _r = _PZ.PCZoneReader(open(pc_path, 'rb').read())
    _r.read_string_table()
    _r.read_asset_list()
    _kinds = [nm for (t, nm, hp) in _r.assets]
    if _kinds.count('GFXWORLD') == 0:
        raise PolicyRefusal(
            'derive_pc_policy REFUSED for %s: premise population %r is EMPTY '
            '(size 0 of %d assets; counted from the zone asset index via '
            'pc_zone.PCZoneReader.read_asset_list, before any simulation). '
            'Every constant this policy carries is GfxWorld-scoped, so a '
            'patch/common zone has no policy -- None is the correct answer '
            'for such a zone, not a derived one.'
            % (zname, 'GFXWORLD asset', len(_kinds)),
            zone=zname, premise='GFXWORLD asset', population=0)
    del _r, _kinds

    # --- pass 1: knobs off -> measure E@planes and E@end ---
    em, spans, PC = _sim(dict(pc_structural_gfx=True))
    rtmap = RuntimeMap(em.omap)
    u32 = lambda o: struct.unpack_from('<I', PC, o)[0]
    gw = _premise([(s, e) for (i, nm, root, s, e) in spans
                   if root == 'GfxWorld' and e > s], 'GfxWorld span', zname,
                  'A patch/common zone has no GfxWorld; every constant this '
                  'policy carries is GfxWorld-scoped.')
    cm = _premise([(s, e) for (i, nm, root, s, e) in spans
                   if root == 'clipMap_t' and e > s], 'clipMap_t span', zname)
    gwmp = _premise([(s, e) for (i, nm, root, s, e) in spans
                     if root == 'GameWorldMp' and e > s],
                    'GameWorldMp span', zname)
    pv = u32(cm[0] + 12)
    if not (0xA0000001 <= pv <= 0xBFFFFFFF):
        raise PolicyRefusal(
            'derive_pc_policy REFUSED for %s: clipMap planes pointer 0x%08X is '
            'not a block-5 alias -- the absolute planes anchor (A) is undefined.'
            % (zname, pv), zone=zname, premise='clipMap planes alias',
            population=0)
    _, regions = GE.pc_regions(PC, gw[0])
    pr = _premise([(lo, hi) for (lab, lo, hi) in regions
                   if lab.startswith('dpvsPlanes.planes')],
                  'GfxWorld dpvsPlanes.planes region', zname)
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
    if not cg:
        raise PolicyRefusal(
            'derive_pc_policy REFUSED for %s: premise population %r is EMPTY '
            '(size 0; %d GWMP tree nodes, %d aliases, none within the +/-400000 '
            'window at loader_sim.py:929). E@gfx-end is undefined, so the '
            'gfx_residual_pc constant B cannot be derived.'
            % (zname, 'GWMP tree-anchor alias deltas', len(nodes), len(aliases)),
            zone=zname, premise='GWMP tree-anchor alias deltas', population=0)
    E_end = max(cg, key=lambda k: (cg[k], k))
    B = E_end - A

    # --- E@matmem: dpvs.surfaces material aliases are FIELD dedups into the
    # materialMemory ARRAY (stride 8, every entry referenced on all zones);
    # E = min distinct alias - model rt of the array start ---
    mm = _premise([(lo, hi) for (lab, lo, hi) in regions
                   if lab.startswith('materialMemory')],
                  'GfxWorld materialMemory region', zname)
    sfr = _premise([(lo, hi) for (lab, lo, hi) in regions
                    if lab.startswith('dpvs.surfaces')],
                   'GfxWorld dpvs.surfaces region', zname)
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
    cm = _premise([(s, e) for (i, nm, root, s, e) in spans
                   if root == 'clipMap_t' and e > s], 'clipMap_t span', zname)
    # POPULATION (rule GX): SndBank-root spans -> alias anchors, one tally entry
    # per (alias value, list-name stream offset) pair returned by
    # _measure_runtime.sndbank_anchors (_measure_runtime.py:20). S_snd is the
    # MODE of those deltas -- it is the frame phase that both the S_clip sweep
    # window below and the dynent_rt lump are measured against.
    n_banks = 0
    c = _C()
    _LN_SND = sys._getframe().f_lineno + 1   # the population loop, never stale
    for (i, nm, root, s, e) in spans:
        if root == 'SndBank' and e > s:
            n_banks += 1
            for (v, noff) in MR.sndbank_anchors(PC, s, '<'):
                c[((v - 1) & 0x1FFFFFFF) - rtmap.rt(noff - B5_BASE)] += 1
    if not c:
        # zm_transit is the witnessed case: its map fastfile ships ZERO SOUND
        # assets (`pc_zone.PCZoneReader.read_asset_list`; every other map zone
        # in the corpus ships exactly 1), so there is no SndBank span to anchor
        # against and no frame phase to measure S_clip or the lump from.
        raise PolicyRefusal(
            'derive_pc_policy REFUSED for %s: premise population %r is EMPTY '
            '(size 0; %d SndBank spans with a body). Population defined at '
            'loader_sim.py:%d (SndBank-root spans -> '
            '_measure_runtime.sndbank_anchors, _measure_runtime.py:20). '
            'S_snd is the frame phase for BOTH the S_clip sweep window and '
            'dynent_rt.lump, so no policy exists for this zone. %s'
            % (zname, 'SndBank alias anchors', n_banks, _LN_SND,
               'This zone carries no SndBank asset at all.' if n_banks == 0
               else 'SndBank spans are present but yielded no block-5 aliases '
                    '— inspect sndbank_anchors against this zone.'),
            zone=zname, premise='SndBank alias anchors', population=0)
    S_snd, n_snd = c.most_common(1)[0]
    nmats = u32(cm[0] + 16)
    mb = cm[0] + 332
    names = set()
    name_offs = []                       # (file offset of the string, the string)
    o2 = mb + nmats * 12
    for i in range(nmats):
        if u32(mb + i * 12) == FOLLOW:
            e2 = PC.index(b'\x00', o2)
            names.add(bytes(PC[o2:e2]))
            name_offs.append(o2)         # <- the S_clip vote needs POSITIONS, not just text
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
    # ------------------------------------------------------------------ 2026-08-20
    # S_clip BY CANDIDATE ENUMERATION, NOT BY A WINDOW SWEEP.
    #
    # THE OLD CODE swept S over [S_snd - 30000, S_snd + 30000] and argmax'd `_hits`. Two
    # defects, one fatal:
    #   (a) `best` seeded at (-1, None) with a strict `>`, so a ZERO-HIT sweep returned the
    #       FIRST candidate -- the window's lower bound -- and pinned lump to +30000. An
    #       argmax over nothing is a value with no evidence.
    #   (b) THE WINDOW IS CENTRED ON S_snd, WHICH IS AN MP-SHAPED PREMISE. Measured:
    #       mp_raid 223/223 inside the window, but zm_nuked 0/261 and zm_highrise 0/294 --
    #       BOTH big ZM maps -- with their true anchors (S=-464, S=-16) far outside it.
    #       zm_transit, the only genuine ZM map for which we hold BOTH the PC source and the
    #       shipped Wii U build, cannot even reach this code: it carries ZERO SndBank assets,
    #       so S_snd -- the window's centre -- has no population on it at all. A derivation
    #       whose centre is undefined for an entire class of map is the wrong derivation.
    #
    # THE FIX IS TO STOP SEARCHING. This function's SIBLING constant, S_snd, is already
    # derived correctly a few lines above: it is the MODE of `alias_rt - rtmap.rt(offset)`
    # over every (alias value, stream offset) pair. The true S is in that tally BY
    # CONSTRUCTION, so no window is needed and none can exclude the answer. S_clip is now
    # derived the same way its sibling always was -- every (clipMap material alias, inline
    # material-name offset) pair contributes one candidate, the tally is voted, and the top
    # candidates are then CONFIRMED with the same `_hits` content check as before.
    #
    # A window cannot exclude the answer if there is no window. See rule (GX)/instrument-laws
    # -- "a window is not a population" -- and FINDINGS_i4/i5 for the same technique used to
    # derive implied displacements elsewhere in this lane.
    cc = _C()
    for i in idxs:
        _rt = (u32(mb + i * 12) - 1) & 0x1FFFFFFF
        for _no in name_offs:
            cc[_rt - rtmap.rt(_no - B5_BASE)] += 1
    best = (-1, None)
    for _S, _votes in cc.most_common(64):
        _h = _hits(_S)
        if _h > best[0]:
            best = (_h, _S)
    S_clip = best[1]
    if S_clip is None:
        raise PolicyRefusal(
            'derive_pc_policy REFUSED for %s: the S_clip candidate tally is EMPTY (%d alias '
            'candidates x %d inline names). With no candidate there is nothing to vote on, '
            'and a window sweep would only have manufactured a value.'
            % (zname, len(idxs), len(name_offs)), zone=zname,
            premise='S_clip candidate pairs', population=0)
    # ⚠ SAME DEFECT CLASS AS THE SndBank REFUSAL ABOVE, FOUND 2026-08-16 while
    # sweeping the fleet — REPORTED, DELIBERATELY NOT CHANGED.
    #
    # `best` starts at (-1, None) and the test is a strict `>`, so when the
    # sweep scores ZERO hits at every S the FIRST candidate tried still wins:
    # S_clip silently becomes the window's lower bound (S_snd - 30000) and the
    # lump becomes exactly +30000. That is a window-edge ARTIFACT, not the
    # content anchor the docstring describes.
    #
    # MEASURED: zm_nuked -> hits 0/261, S_clip = -178168, lump = +30000.
    #           zm_highrise -> lump = +30000 (same signature).
    #           mp_raid 223/223, mp_skate/nuketown/mirage/downhill all > 0.
    # So it is the two big ZM zones, one of which (zm_nuked) is the LIVE lane's
    # map, whose derived clipMap pre-skip is an artifact.
    #
    # PM RULING 2026-08-17 (blast radius is the PM's, not this function's):
    #   (a) any map with NO live build: zero hits => REFUSE. A window-edge value
    #       is not a derived policy, and a FIRST bake must never inherit one
    #       silently.
    #   (b) zm_nuked ONLY (LIVE b92): values stay BYTE-IDENTICAL and LOUD until
    #       the characterisation lands. The receipt's ok=False fails the bake
    #       via gate_receipt.require -- there is no silent waiver row.
    # The exemption is an explicit, dated, single-entry allow list: a map is
    # exempt because it is LIVE, never because it is inconvenient. Re-calibrate
    # it every time it changes; deleting the entry restores the refusal.
    # NB: ASCII only. This prints on every bake, including build contexts whose
    # stdout is cp1252 -- a non-ASCII glyph here would raise UnicodeEncodeError
    # and turn a warning into a new crash.
    if best[0] > 0 and os.path.basename(pc_path) in ZERO_HIT_LIVE_EXEMPT:
        # SELF-RETIRING EXEMPTION. The allow-list exists only because the old S_snd-centred
        # window scored 0 hits on this map. Now that the candidate-enumeration vote resolves
        # it, the entry is dead weight -- and a stale allow-list entry is exactly how a
        # map-name exemption outlives the defect it was written for. Say so LOUDLY rather
        # than recording the debt in prose: a debt recorded in prose is not discharged.
        # ASCII only -- this prints on every bake, including cp1252 stdout contexts.
        print('  *** %s: S_clip now derives with %d/%d hits (S=%d). Its '
              'ZERO_HIT_LIVE_EXEMPT entry is NO LONGER NEEDED -- REMOVE IT from '
              'loader_sim.ZERO_HIT_LIVE_EXEMPT.'
              % (os.path.basename(pc_path), best[0], len(idxs), S_clip))
    if best[0] <= 0:
        if os.path.basename(pc_path) not in ZERO_HIT_LIVE_EXEMPT:
            raise PolicyRefusal(
                'derive_pc_policy REFUSED for %s: S_clip sweep scored 0 hits '
                'over %d clipMap material alias candidates in the window '
                '[%d, %d]. `best` seeds at (-1, None) with a strict `>`, so a '
                'zero-hit sweep silently returns the FIRST candidate -- the '
                'window lower bound S_snd-30000 (%d) -- and pins lump to '
                '+30000. An argmax over nothing is a value with no evidence. '
                'MEASURED CAUSE: the window is centred on S_snd, which is '
                'MP-shaped; the true anchor is recoverable by content vote '
                '(nuked S=-464 258/261, highrise S=-16 286/294) far outside '
                'it. Refusing per PM ruling (a) 2026-08-17.'
                % (os.path.basename(pc_path), len(idxs), S_snd - 30000,
                   S_snd + 30000, S_clip),
                zone=os.path.basename(pc_path),
                premise='S_clip sweep hits', population=0)
        print('  *** %s: S_clip sweep scored %d hits over %d clipMap material '
              'aliases -- S_clip=%d is the WINDOW LOWER BOUND (S_snd-30000), '
              'an artifact, not a content anchor; lump pinned to %d. Policy '
              'returned UNCHANGED and flagged under PM ruling (b) 2026-08-17 '
              '(LIVE map exemption). See loader_sim.py:%d.'
              % (os.path.basename(pc_path), best[0], len(idxs), S_clip,
                 S_snd - S_clip, sys._getframe().f_lineno))
    pol = dict(pc_structural_gfx=True,
               pre_skip_pc={'GfxWorld': A, 'clipMap_t': S_clip},
               gfx_residual_pc=B, gfx_matmem_pc=E_mm,
               dynent_rt=dict(lump=S_snd - S_clip))
    if verbose:
        print('%s [PC blind]: A=%d B=%d E_mm=%d S_snd=%d (n=%d) S_clip=%d '
              '(hits %d/%d) -> %s' %
              (os.path.basename(pc_path), A, B, E_mm, S_snd, n_snd, S_clip,
               best[0], len(idxs), pol))
    return pol, n_snd, best[0], len(idxs)


if __name__ == '__main__':
    calibrate(sys.argv[1] if len(sys.argv) > 1 else '../wiiu_ref/mp_raid_genuine.zone')
