"""zone_walk — THE corrected structural walk, landed upstream (2026-07-30 bake).

This is the upstream home of `relink_validation/walk2.py`, whose logic was validated to
**63/63 genuine zones and 16/16 four-block MAP zones EOF-exact** (walk_survey 4 + ADDENDUM 13 of
HANDOFF_pipeline_bake_rules.md). The relink_validation copy is the frozen validated reference; fix
things HERE. `verbatim_gate.asset_spans()` and the zone_gates 39-43 pass delegate to this module,
so every gate now runs the corrected walk instead of the old blind one.

What it fixes over the legacy body_relayout/ui_splice walk (rule letters from the handoff):

  (AA) BODYLESS ENTRIES -- an XAsset whose headerPtr is not FOLLOW/INSERT has no body. The legacy
       walk never consults headerPtr, so it emits one body too many and raises at the end.
  (AB) HONEST FAILURE   -- the legacy walk swallows its exception (`except Exception: break`) and
       returns a partial span list with no error flag. Here the exception is RETURNED (walk) or
       RAISED (asset_spans).
  (AK) WEAPON HINT      -- `body_relayout._weapon_end` needs the module global `WEAPON_NEXT_HINT`:
       the UPCOMING body-bearing (FOLLOW) asset root names after the weapon being walked. Only
       loader_sim.simulate_stream ever set it, so every ZM weapon fell through to the MP
       RAWFILE-cluster heuristic (zm_transit: 505 bodies/2.2% -> 3166/78.1%). VehicleDef too.
  (AL) VALIDATED CONSOLE SPAN PARSERS -- clipMap_t 332 / GfxWorld 1076 / GameWorldMp / SndBank /
       XAnimParts. They already existed (loader_sim uses them); they were absent from the walk
       every gate ran, which under-read maps by megabytes.
  (AN) cid-47 RELABEL   -- console 47 is GLASSES (16-B no_follow stub), not MAP_ENTS. Relabelled
       BY BODY SIGNATURE (loader_sim parity). Biggest single win: EOF-exact 47->55 of 63.
  (AO) GfxWorld: G2 is a LOWER BOUND; the GameWorld SIGNATURE finds the end.
  (AP) generic next-asset resync for any failed delimiter (the follower roots come from rule AA).
  (BA/BU/BV) WeaponVariantDef span parser `weapon_span.py` -- validated 4 zones incl. 80 weapons
       on common_patch_mp, all EOF-exact.
  (BW/BX) MenuList span parser `menulist_span.py`, ONLY correct with NEXT_ROOTS set (see below).

Returns a Walk object; `.spans` is [(start, end, type_name, entry_index)].
"""
import os, sys, struct

_CWD0 = os.getcwd()
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, '..')
for _rel in ('wiiu_ref', 'WiiU_FF_Studio',
             os.path.join('dlc loading', 'native', 'fullrelink')):
    _p = os.path.abspath(os.path.join(BASE, _rel))
    if _p not in sys.path:
        sys.path.insert(0, _p)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import zone_facts as ZF
import wiiu_zone, zone_stream as zs
import walker as W
import struct_layout
import body_relayout as BR
import ui_splice as US

# roots whose delimiter consults WEAPON_NEXT_HINT
HINT_ROOTS = ('WeaponVariantDef', 'VehicleDef')
MLSPAN = None           # menulist_span, if importable -- its NEXT_ROOTS gets the same hint
HINT_DEPTH = 8          # _weapon_end_resync uses depth=3 but tiers down; give it headroom


def _console_spans():
    """VALIDATED console span parsers for the roots the generic ZoneCode walk gets wrong.

    These are not new code -- they already exist and `loader_sim.simulate_stream` already uses them.
    They were simply absent from the body_relayout/ui_splice walk that verbatim_gate.asset_spans
    (and therefore every gate) ran, which is why a map walk under-consumed by megabytes (measured:
    GfxWorld 10.1 MB, SndBank 11.9 MB on mp_carrier).

    ⚠ ROOT SIZES: the validated parsers use clipMap_t = 332 and GfxWorld = 1076. `struct_layout`
    reports 328 and 1016 (it drops clipMap's triIndices). Classifying pointer fields with the
    struct_layout sizes reads shifted garbage -- that mistake is what made GfxWorld look exonerated
    for a whole diagnostic pass (rule AL).
    """
    out = {}
    try:
        import clipmap_console as CC
        out['clipMap_t'] = lambda z, o: CC.parse_clipmap_console(z, o)
    except Exception:
        pass
    try:
        import gfxworld_console_span as GCS
        import gfxworld_probe2 as G2P

        def _gfx(z, o):
            """GfxWorld span, with a fallback when the G2 region walk cannot be trusted.

            `parse_gfxworld_console` = g2_console_end() + a forward scan for the next asset's
            GameWorld body signature. The KEY observation (rule AO): even on zones that walk
            perfectly, G2 does NOT reach the true end -- on mp_carrier G2 stops at 0x300aecb while
            the real GfxWorld ends at 0x39aad8d, and the signature scan silently covers the 10.4 MB
            gap. So G2's value is only ever a LOWER BOUND for where to start scanning; it is the
            SIGNATURE that finds the end. When G2 overruns (mp_slums claims +203 MB) or raises
            (mp_nuketown_2020, mp_skate), scan from a conservative lower bound instead of giving up.

            Lower bound = the GfxWorld root end, which is always valid.
            The end-to-end check is what validates the choice: a wrong signature hit cannot make
            the whole walk land on EOF with the right body count.
            """
            try:
                e = GCS.parse_gfxworld_console(z, o)
                if o < e <= len(z):
                    return e
            except Exception:
                pass
            lo = o + G2P.CFG['wiiu']['bodysize']
            for t in range(lo, len(z) - 44):
                if GCS._gameworld_body_at(z, t):
                    return t
            raise RuntimeError('GfxWorld: no GameWorld signature after 0x%x' % lo)

        out['GfxWorld'] = _gfx
    except Exception:
        pass
    try:
        import xanimparts_probe as XA
        out['XAnimParts'] = lambda z, o: XA.parse_xanim(z, o, '>')[0]
    except Exception:
        pass
    try:
        import alloc_events as AE
        out['GameWorldMp'] = lambda z, o: AE.gwmp_events(z, o, '>')[0]
        out['SndBank'] = lambda z, o: AE.sndbank_events(z, o, '>')[0]
    except Exception:
        pass
    try:
        # rule (BA): a REAL WeaponVariantDef span from lane D's structure, replacing
        # _weapon_end's resync/RAWFILE-cluster guesses. Validated (ADDENDUM 13):
        # mp_nuketown_2020 1 weapon 5218 B, zm_transit_dr_patch 19, zm_transit_patch 25,
        # common_patch_mp 80 weapons 921679 B -- all four zones EOF-exact.
        import weapon_span as WSPAN
        out['WeaponVariantDef'] = lambda z, o: WSPAN.parse_weapon_console(z, o)
    except Exception:
        pass
    try:
        # rule (BW): `_menulist_end`'s method is right (bound a non-empty MenuList at the next
        # asset's header) but it only recognises a follower MenuList named '*.txt' or a StringTable
        # named '*.csv'. en_patch_loc_mp ends MENULIST -> MENULIST('.menu') -> RAWFILE, so it
        # matched nothing across 373,939 B and raised. menulist_span adds those signatures and is
        # ONLY correct with NEXT_ROOTS set (rule BX) -- see the walk loop.
        global MLSPAN
        import menulist_span
        MLSPAN = menulist_span
        out['MenuList'] = lambda z, o: MLSPAN.parse_menulist_console(z, o)
    except Exception:
        pass
    return out


def _glasses():
    """cid-47 relabel detector + span. See RELABEL_47 below."""
    try:
        import raid_oracle_control as RC
        return RC._looks_like_glasses, RC._console_glasses_end
    except Exception:
        return None, None


# RELABEL_47 -- `console_to_pc()` maps console type 47 to MAP_ENTS, but 47 is really GLASSES
# (rule AN; MapEnts is cid 17). A Glasses body is a 16-byte no_follow stub; walking it as MapEnts
# (36-byte root + follows) desyncs everything after it.
#
# MEASURED, perfect correlation over 6 map zones:
#     has a cid-47 entry -> walk FAILS : mp_hijacked, mp_village, mp_turbine
#     no cid-47 entry    -> walk PASSES: mp_carrier, mp_la, mp_socotra
# The failures surface LATER as "clipMap returned root-only 332" or "clipMap overran past EOF",
# because clipMap is simply the next big asset reading from a wrong offset. The clipMap parser was
# never at fault on those zones. Triage by FIRST DIVERGENCE, never by death site.
#
# loader_sim already handles this (`glasses = nm == 'MAP_ENTS' and RC._looks_like_glasses(...)`);
# the legacy body_relayout/ui_splice walk did not. NOTE: console_to_pc keeps the 'MAP_ENTS' label
# on cid 47 (documented there) because this signature relabel is the configuration validated 63/63.
LOOKS_GLASSES, GLASSES_END = _glasses()


CONSOLE_SPANS = None


class Walk(object):
    def __init__(self):
        self.spans = []            # (start, end, type_name, entry_index)
        self.end = 0
        self.err = None            # (entry_index, type_name, message) or None
        self.expected = 0          # bodies expected == #{headerPtr in FOLLOW,INSERT} with a root
        self.bodyless = []         # entry indices with no body
        self.noroot = []           # entry indices whose type has no root struct
        self.overridden = []       # entry indices spanned by a validated console parser
        self.relabelled = []       # entry indices relabelled MAP_ENTS -> GLASSES (cid 47)
        self.resynced = []         # entry indices whose delimiter failed and was resynced
        self.reader_fixed = None   # (readers_assets_off, correct_assets_off) if depends[] fixed
        self.zone_len = 0

    @property
    def ok(self):
        return (self.err is None and self.end == self.zone_len
                and len(self.spans) == self.expected)

    def coverage(self):
        return (100.0 * self.end / self.zone_len) if self.zone_len else 0.0

    def verdict(self):
        if self.ok:
            return 'OK  %d bodies, EOF exact' % len(self.spans)
        bits = []
        if self.err:
            bits.append('DIED at entry %d (%s): %s' % self.err)
        if self.end != self.zone_len:
            bits.append('coverage %.2f%% (%d B unconsumed)'
                        % (self.coverage(), self.zone_len - self.end))
        if len(self.spans) != self.expected:
            bits.append('%d bodies, expected %d' % (len(self.spans), self.expected))
        return '; '.join(bits)


def walk(zone, facts=None, hint=True, console_spans=True, relabel47=True, span_delta=None):
    """span_delta: {entry_index: bytes} added to that asset's computed span end.

    DIAGNOSTIC ONLY -- it is a localiser, never a fix. Adding N to one asset and re-walking answers
    "is the missing N owned by this asset?" with a yes/no the EOF check validates: only the true
    owner can make the whole zone land on EOF exactly.
    """
    global CONSOLE_SPANS
    if CONSOLE_SPANS is None:
        CONSOLE_SPANS = _console_spans()
    spanfn = CONSOLE_SPANS if console_spans else {}
    f = facts or ZF.Facts(zone)
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    # wiiu_zone.read_string_table() now models depends[] (rule AM, baked 2026-07-30), so this
    # correction should never fire; kept as a belt-and-braces cross-check between the two readers.
    # A mismatch is recorded in .reader_fixed and the depends-aware value wins.
    fixed_off = None
    if r.assets_off != f.assets_off:
        fixed_off = (r.assets_off, f.assets_off)
        r.assets_off = f.assets_off
        r.assets = []
        r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True)
    zc = W.ZoneCode(W.ZC_DIR)
    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - BR.B5_BASE
    em = US.MultiEdit(zone, L, zc, w, {}); em.delta = 0

    hp = f.header_ptrs()
    out = Walk()
    out.zone_len = len(zone)
    out.reader_fixed = fixed_off

    # classify every entry once: does it have a body, and does its type have a root?
    plan = []      # (entry_index, root, name) for entries that WILL be walked, in order
    for i, (cid, pc_, nm) in enumerate(r.assets):
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            out.noroot.append(i)
            continue
        if hp[i] not in (ZF.FOLLOW, ZF.INSERT):          # rule (AA)
            out.bodyless.append(i)
            continue
        plan.append((i, root, nm))
    out.expected = len(plan)

    cur = r.assets_end
    for k, (i, root, nm) in enumerate(plan):
        if hint:
            # the hint is the UPCOMING body-bearing roots -- exactly the tail of `plan` (rule AK).
            # Only weapon/vehicle delimiters read it; others ignore it, and an MP weapon walked
            # with a hint set falls through to the legacy heuristic (documented in _weapon_end).
            nxt_roots = [p[1] for p in plan[k + 1:k + 1 + HINT_DEPTH]]
            BR.WEAPON_NEXT_HINT = nxt_roots if root in HINT_ROOTS else None
            # rule (BW/BX): the MenuList span parser scans for the signature of the asset that
            # ACTUALLY comes next, never a union. Without this it can sail past a signature-less
            # follower (a TECHNIQUE_SET on common_patch_mp entry 5) and match the zone's FINAL
            # RawFile 2.7 MB downstream -- and by SUCCEEDING it suppresses the (AP) resync that
            # was right. A span parser that answers when it should raise is worse than one that
            # raises.
            if MLSPAN is not None:
                MLSPAN.NEXT_ROOTS = nxt_roots if root == 'MenuList' else None
        s = cur
        fn = spanfn.get(root)
        # RELABEL_47: a MAP_ENTS-labelled entry whose body matches the Glasses signature IS Glasses.
        if (relabel47 and nm == 'MAP_ENTS' and LOOKS_GLASSES is not None
                and LOOKS_GLASSES(zone, cur)):
            try:
                cur = GLASSES_END(zone, cur)
                out.relabelled.append(i)
                out.spans.append((s, cur, 'GLASSES', i))
                continue
            except Exception as e:
                out.err = (i, nm, 'glasses relabel: %s: %s' % (type(e).__name__, e))
                break
        def _resync(exc):
            """GENERIC next-asset resync when a delimiter fails (rule AP).

            `body_relayout._weapon_end_resync` scans forward for the first offset at which the next
            `depth` assets all parse cleanly with the validated console span parsers. It was written
            for weapons, but the mechanism is type-agnostic -- and the follower roots are already
            known, because `plan` holds exactly the body-bearing entries in stream order (rule AA).

            ⚠ LIMIT: it refuses followers not in `_RESYNC_SPAN`. ⚠ RISK: it accepts an offset on
            evidence, so it CAN accept a wrong one -- the end-to-end EOF check is the only guard.
            """
            roots = [p[1] for p in plan[k + 1:k + 1 + 6]]
            if not roots:
                raise exc
            return BR._weapon_end_resync(zone, s, roots, scan_lo=16)

        try:
            if fn is not None:
                # validated console parser: take the EXTENT and copy the body verbatim.
                # ⚠ do NOT assign to `cur` before validating -- an overrunning parser would leave a
                # past-EOF cursor in the result and report coverage like 335% or 5055%, which is
                # nonsense. Validate first, keep `cur` at the last good position on failure.
                nxt = fn(zone, cur)
                if not (s < nxt <= len(zone)):
                    raise RuntimeError('console span parser returned end 0x%x for start 0x%x '
                                       '(zone is 0x%x) -- OVERRUN by %d B'
                                       % (nxt, s, len(zone), nxt - len(zone)))
                cur = nxt
                out.overridden.append(i)
            else:
                cur = em.emit_asset(root, cur)
            if span_delta and i in span_delta:
                cur += span_delta[i]                 # DIAGNOSTIC localiser, see docstring
                if not (s < cur <= len(zone)):
                    raise RuntimeError('span_delta put entry %d out of range' % i)
        except Exception as e:
            try:
                nxt = _resync(e)
                if not (s < nxt <= len(zone)):
                    raise e
                cur = nxt
                out.resynced.append(i)
            except Exception:
                out.err = (i, nm, '%s: %s' % (type(e).__name__, e))
                break
        out.spans.append((s, cur, nm, i))
    BR.WEAPON_NEXT_HINT = None
    if MLSPAN is not None:
        MLSPAN.NEXT_ROOTS = None
    out.end = cur
    return out


def asset_spans(zone):
    """verbatim_gate-compatible shape: [(start, end, type_name, is_verbatim)].

    ⚠ Unlike the legacy verbatim_gate.asset_spans this RAISES on walk failure instead of returning
    a partial list (rule AB). Callers that want partial data should use walk() and inspect .err.
    """
    wk = walk(zone)
    if not wk.ok:
        raise RuntimeError('walk incomplete: %s' % wk.verdict())
    return [(s, e, nm, W.ASSET_ROOT.get(nm) in BR.DELIMITERS)
            for s, e, nm, i in wk.spans]


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--no-hint', action='store_true')
    a = ap.parse_args()
    for p in a.paths:
        p = p if os.path.isabs(p) else os.path.join(_CWD0, p)
        z = ZF.load_ff(p)
        f = ZF.Facts(z)
        for tag, use in (('no hint', False), ('WITH hint', True)):
            if a.no_hint and use:
                continue
            wk = walk(z, f, hint=use)
            print('%-26s %-10s bodies %5d/%-5d  end 0x%08x/%08x  %s'
                  % (os.path.basename(p), tag, len(wk.spans), wk.expected,
                     wk.end, wk.zone_len, wk.verdict()[:90]))
        print('   bodyless %d, noroot %d, overrides %d, cid47 %d, resyncs %d'
              % (len(wk.bodyless), len(wk.noroot), len(wk.overridden),
                 len(wk.relabelled), len(wk.resynced)))
