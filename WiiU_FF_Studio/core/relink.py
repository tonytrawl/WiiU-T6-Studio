"""core.relink -- hardened cumulative-delta relink.

This is a corrected reimplementation of `dlc loading/native/fullrelink/grow_relink.py`. The
original works for small rawfile grows in patch_mp and degrades silently outside that envelope
(6 dead boots). See PLAN_ff_studio_feature_roadmap.md §7 for the full root-cause. The defects
addressed here:

  D1/R2  the tail scan stepped 4 bytes at a time, but the body base `arrEnd = 0x41E1 == 1 mod 4`
         is OFF-GRID, so it was blind to ~77% of real pointer sites. -> BYTE-GRANULAR scan.
  D3     a walk break was PRINTED, not raised: a walk that died at asset 5 of 1533 still returned
         a "successful" zone. -> walk coverage is a hard, refusable field.
  D4/R4  `cumdelta()` only compared against substitution START offsets, so a pointer landing
         INSIDE a resized span silently kept a stale target. -> interior pointers are detected
         and are a hard error unless explicitly accepted.
  D6     it built on `stage1_roundtrip.emit_container`, whose `o = (o+3) & ~3` before the asset
         array is documented-wrong (patch_mp's array starts UNALIGNED at 0x11F9) and round-trips
         only by coincidence. -> the container prefix is copied verbatim and the three size
         fields are patched in place, in ZoneReader coordinates.
  D7     block accounting assumed every byte of growth lands in BLOCK_VIRTUAL. -> asserted.
  R3     words in the un-walked tail that LOOK like block-5 aliases but were skipped for not
         being registered targets were invisible. -> counted and reported as `blind_candidates`.

⛔⛔ D8 (2026-08-17) -- INHERITED FROM THE ORIGINAL AND NOT CAUGHT BY D1-D7: every relocation
decision here was `cum(off + B5_BASE)`, i.e. it converted a block-5 pointer offset to a FILE
offset by adding 64 and compared that against the substitution's FILE range. `file = b5 + 64` is
a CONTAINER-ONLY rule (bake rule (U)). Measured on the stock live patch_zm over 483
content-confirmed targets, D = b5 - file spans -1,980,635 .. +113,055 and equals 64 for NONE of
them. So D1-D7 hardened a decision that was being made in a coordinate system that does not
exist. Four sites were affected: census(), WLEdit.remap_ptr, the tail scan and the headerPtr pass.

The decision now comes from `native_linker/reloc_model.py`, in block-5 space, where the growth
actually happens. Consequence for callers: with no sound model available `grow()` now REFUSES
(rep.ok == False) instead of returning quietly-wrong bytes. That is deliberate -- the previous
behaviour produced a zone that parses, registers, passes every static gate and dies with no
Com_Error, which is the worst possible outcome to hand a tester.

CONTRACT
--------
`grow()` returns (zone_bytes, RelinkReport). It refuses rather than guesses. A report with
`ok == False` means the bytes must not be written.
"""
import os
import struct

from . import paths  # noqa: F401

import zone_facts as ZF
import zone_walk
import reloc_model as RM

ALIAS_LO, ALIAS_HI = 0xA0000000, 0xC0000000
BLOCK_VIRTUAL = 5
FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
#: ⛔ CONTAINER-ONLY (bake rule (U), and D8 above). This is the seed for the ZoneWriter's own
#: block-5 counter, which is contiguous and self-consistent, and that is its ONLY legitimate use.
#: It is NOT a b5 <-> file conversion: measured over 483 content-confirmed targets on patch_zm,
#: D = b5 - file is 64 for exactly none of them. Never write `off + B5_BASE` to locate a target.
B5_BASE = 64
TOTAL_SIZE_OFF = 0
BLOCK_SIZES_OFF = 8


def _import_backend():
    """Import the fullrelink tree without letting its import-time os.chdir leak.

    `ui_splice.py` performs `os.chdir(<root>/native_linker)` at module scope. That is fine for
    its own CLI and fatal for a GUI, so the cwd is saved and restored around the import.
    """
    cwd = os.getcwd()
    try:
        import wiiu_zone
        import struct_layout
        import zone_stream as zs
        import walker as W
        import body_relayout as BR
        import ui_splice as US
        return wiiu_zone, struct_layout, zs, W, BR, US
    finally:
        try:
            os.chdir(cwd)
        except Exception:
            pass


# ---------------------------------------------------------------------------- analysis


class RelinkReport(object):
    def __init__(self):
        self.problems = []
        self.warnings = []
        self.notes = []
        self.total_delta = 0
        self.subs = 0
        # walk
        self.walk_ok = False
        self.walk_verdict = ''
        self.walk_coverage = 0.0
        self.tail_bytes = 0
        self.tail_pct = 0.0
        # pointer census
        self.alias_words = 0
        self.before = 0
        self.after = 0
        self.interior = []          # (file_off, value, target_rt, sub_start) -- ADVISORY
        self.runtime_domain = 0
        self.phantoms = 0           # alias-shaped words inside data payloads (overlap-filtered)
        self.blind_candidates = []  # (file_off, value, target_rt) in the un-walked tail
        self.bumped = 0
        self.headerptrs = 0

    @property
    def ok(self):
        return not self.problems

    def fail(self, msg):
        self.problems.append(msg)
        return self

    def warn(self, msg):
        self.warnings.append(msg)
        return self

    def summary(self):
        return dict(ok=self.ok, total_delta=self.total_delta, subs=self.subs,
                    walk_ok=self.walk_ok, walk_coverage=round(self.walk_coverage, 4),
                    tail_bytes=self.tail_bytes, tail_pct=round(self.tail_pct, 4),
                    alias_words=self.alias_words, before=self.before, after=self.after,
                    interior=len(self.interior), runtime_domain=self.runtime_domain,
                    phantoms=self.phantoms,
                    blind_candidates=len(self.blind_candidates),
                    bumped=self.bumped, headerptrs=self.headerptrs,
                    problems=list(self.problems), warnings=list(self.warnings))

    def report(self):
        L = []
        L.append('relink report  delta=%+d  subs=%d' % (self.total_delta, self.subs))
        L.append('  walk        : %s (coverage %.4f%%)'
                 % ('OK' if self.walk_ok else 'INCOMPLETE -- ' + self.walk_verdict,
                    self.walk_coverage))
        L.append('  verbatim tail: %d B (%.4f%% of zone)' % (self.tail_bytes, self.tail_pct))
        L.append('  alias words  : %d  (before %d / after %d / interior %d / runtime %d)'
                 % (self.alias_words, self.before, self.after,
                    len(self.interior), self.runtime_domain))
        L.append('  phantoms     : %d  <- alias-shaped words inside data payloads, filtered out'
                 % self.phantoms)
        L.append('  blind cands  : %d  <- alias-shaped words in the tail with no registered target'
                 % len(self.blind_candidates))
        L.append('  bumped       : %d  headerPtrs: %d' % (self.bumped, self.headerptrs))
        # NOTES were being collected and never printed, so the relocation model the save actually
        # used was invisible in the dry run. It is the single most important thing on this report.
        for n in self.notes:
            L.append('  MODEL ' + n)
        for w in self.warnings:
            L.append('  WARN  ' + w)
        for p in self.problems:
            L.append('  FAIL  ' + p)
        L.append('  VERDICT: %s' % ('OK' if self.ok else 'REFUSED'))
        return '\n'.join(L)


def _norm_subs(subs):
    """subs: {file_off: (end, new_bytes)} -> sorted [(start, end, new, delta)]."""
    out = []
    for start, (end, new) in subs.items():
        if end < start:
            raise ValueError('substitution at 0x%x has end 0x%x before start' % (start, end))
        out.append((start, end, new, len(new) - (end - start)))
    out.sort()
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][1]:
            raise ValueError('substitutions overlap: 0x%x..0x%x and 0x%x..0x%x'
                             % (out[i - 1][0], out[i - 1][1], out[i][0], out[i][1]))
    return out


def derive_reloc(zone, norm, anchors=None, auto_anchors=True):
    """Establish HOW targets move for this edit, or raise RelocError. -> (reloc, note, anchors).

    One definition, shared by grow() and census(), so a dry run can never classify pointers by a
    different rule than the save that follows it. The anchors come back because the verbatim-tail
    whitelist needs them too, and it must not be placed by a different rule than the boundary was.
    """
    anc = anchors
    note = 'caller-supplied anchors'
    if anc is None and auto_anchors:
        anc, st = RM.anchors_from_stringtables(zone, want_stats=True)
        # The container edge is exact by construction (rule Q) and is usually the only anchor
        # BEFORE the first StringTable, so without it a script body early in the zone cannot be
        # bracketed at all and every edit to it would refuse.
        anc = [RM.container_edge_anchor(ZF.Facts(zone).assets_end)] + list(anc)
        note = ('derived %d content-proven anchor(s) from %d StringTable(s) plus the exact '
                'container-edge anchor; %d ambiguous, %d rejected by the literal re-count'
                % (st['anchors'], st['tables'], st['ambiguous in index'],
                   st['rejected by literal re-count']))
    file_subs = [(s, e, d) for (s, e, _n, d) in norm]
    spans = RM.b5_spans_from_file_subs(file_subs, anc or (),
                                       target_offsets=RM.stringtable_alias_offsets(zone))
    return RM.Reloc(spans, (), 'auto-anchored'), note, list(anc or ())


# ⛔ `_cumdelta_fn` WAS HERE AND HAS BEEN DELETED, NOT DEPRECATED (D8).
#
# It took a FILE offset and returned (delta, interior_of). Its logic was right; every caller fed
# it `b5 + 64`, which is not a file offset outside the container, so the function could only ever
# answer a question about a coordinate that does not exist. Leaving it in place as dead code would
# leave a correct-looking helper one call away from reintroducing the defect, so it is gone.
#
# Its boundary semantics were the one part worth keeping and now live in reloc_model.Reloc.new_off,
# stated in BLOCK-5 space:
#     off <  span start -> unaffected
#     off == span start -> unaffected (a span's first byte does not move)
#     inside the span   -> InteriorTarget, unless an interval maps it (length-neutral spans are
#                          exempt: their interior does not move, so the identity case still works)
#     off >= span end   -> shifted by the sum of all preceding deltas


_PAYLOAD_CACHE = {}


def _zone_key(zone):
    """Cheap fingerprint: enough to tell two zones apart without hashing 130 MB every call."""
    n = len(zone)
    return (n, bytes(zone[:48]), bytes(zone[n // 2:n // 2 + 48]), bytes(zone[-48:]))


def image_payload_ranges(zone):
    """Pixel spans of every INLINE GfxImage. Pure data by definition.

    ⚠ THESE ARE NOT ASSETS, so the asset walk below cannot see them, and until this existed
    every pixel byte was scanned for pointers. Measured on common_mp.ff: 30,736,384 bytes of
    inline pixels hold 3,560,842 words that decode as a plausible block-5 alias -- more than
    half of the whole zone's apparent pointer population, every one of them a phantom.
    """
    try:
        from . import zone_images as _ZI
        return sorted((im.pixel_off, im.pixel_off + im.pixel_len)
                      for im in _ZI.list_images(zone, inline_only=True)
                      if im.pixel_off is not None and im.pixel_len)
    except Exception:
        return []


def payload_ranges(zone, use_cache=True):
    """Byte ranges that are pure DATA, not pointer fields (the PHANTOM LAW overlap-filter).

    GSC bytecode, HavokScript bytecode and rawfile bodies routinely contain byte sequences that
    decode as a plausible block-5 alias. Measured on faction_cd_mp: 56 words inside one script's
    bytecode all "point" at rt 4135, which is itself inside that same bytecode -- they are GSC
    opcodes (1c 28 0a ...), not pointers.

    INLINE TEXTURE PIXELS ARE THE SAME CLASS and are included here too -- see
    `image_payload_ranges` for what leaving them out costs.

    A census that does not exclude these reports phantoms as real pointers, and would refuse
    edits that are perfectly safe. Returns a sorted list of (start, end), non-overlapping.
    """
    key = _zone_key(zone) if use_cache else None
    if key is not None and key in _PAYLOAD_CACHE:
        return _PAYLOAD_CACHE[key]

    from . import assets as _A
    out = []
    try:
        for a in _A.enumerate_zone(zone).assets:
            if a.buf_off is not None and a.buf_len:
                out.append((a.buf_off, a.buf_off + a.buf_len))
    except Exception:
        pass
    out.extend(image_payload_ranges(zone))
    out.sort()

    # `_in_ranges` binary-searches, which needs disjoint ranges; an inline image inside an
    # asset body would otherwise make the search skip past a later range.
    merged = []
    for s, e in out:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    if key is not None:
        if len(_PAYLOAD_CACHE) > 8:
            _PAYLOAD_CACHE.clear()
        _PAYLOAD_CACHE[key] = merged
    return merged


def _in_ranges(ranges, off):
    lo, hi = 0, len(ranges)
    while lo < hi:
        mid = (lo + hi) // 2
        s, e = ranges[mid]
        if off < s:
            hi = mid
        elif off >= e:
            lo = mid + 1
        else:
            return True
    return False


def census(zone, subs, walk_end=None, exclude_payloads=True, reloc=None):
    """Byte-granular pointer census. No emit, no mutation -- pure analysis.

    Answers: how many pointers are there, where do they point relative to the edit, and how
    many would a stride-4 scan have missed.

    ⚠ ADVISORY. It cannot fully distinguish a real pointer from data that looks like one,
    except via the payload overlap-filter, so its interior findings are reported rather than
    enforced. The authoritative check is the population pre-flight inside grow().

    ⛔ `reloc` IS REQUIRED TO CLASSIFY. before/after/interior are relative to the substitution
    boundary, and locating that boundary needs a block-5 model. Without one this counts pointers
    and REFUSES TO SPLIT THEM, rather than splitting them with the refuted `file = b5 + 64` rule
    -- a wrong split is worse than an absent one because it reads as information.
    """
    rep = RelinkReport()
    norm = _norm_subs(subs)
    rep.subs = len(norm)
    rep.total_delta = sum(d for (_s, _e, _n, d) in norm)
    if reloc is None and rep.total_delta:
        # Derive the SAME model grow() will use, so a dry run never classifies by a different rule
        # than the save that follows it. If it cannot be derived, say so instead of guessing.
        try:
            reloc, note, _anc = derive_reloc(zone, norm)
            rep.notes.append(note)
        except RM.RelocError as ex:
            rep.warn('pointer classification (before/after/interior) is UNAVAILABLE because the '
                     'substitution boundary cannot be placed in block-5 space: %s. Counts below '
                     'are totals only -- the one rule that needs no model is the refuted '
                     'file = b5 + 64, and a wrong split is worse than an absent one.' % ex)

    f = ZF.Facts(zone)
    b5 = f.b5
    n = len(zone)
    ranges = payload_ranges(zone) if exclude_payloads else []

    # BYTE granularity is mandatory here (rule: console records are not 4-aligned; a stride-4
    # sweep is blind to ~77% of real sites because arrEnd == 1 mod 4).
    for off in range(0, n - 3):
        v = struct.unpack_from('>I', zone, off)[0]
        if not (ALIAS_LO <= v < ALIAS_HI):
            continue
        if v in (FOLLOW, INSERT):
            continue
        blk = (v >> 29) & 7
        if blk != BLOCK_VIRTUAL:
            continue
        if ranges and _in_ranges(ranges, off):
            rep.phantoms += 1          # inside a data payload: cannot be a pointer field
            continue
        rt = (v & 0x1FFFFFFF) - 1
        rep.alias_words += 1
        if rt > b5:
            rep.runtime_domain += 1
            continue
        if reloc is not None:
            try:
                moved = reloc.new_off(rt) != rt
            except RM.InteriorTarget:
                rep.interior.append((off, v, rt, None))
                continue
            if moved:
                rep.after += 1
            else:
                rep.before += 1
        if walk_end is not None and off >= walk_end:
            # tail word: would the omap whitelist have covered it? We can't know without the
            # omap, so record it as a candidate for the blind-region report.
            rep.blind_candidates.append((off, v, rt))

    return rep


# ---------------------------------------------------------------------------- relink


def grow(zone, subs, allow_interior=False, tail_relink=True, require_walk=True,
         verbose=False, reloc=None, anchors=None, auto_anchors=True):
    """Cumulative-delta relink with the §7 defects fixed.

    subs: {file_off: (end_off, new_bytes)}
    Returns (new_zone_bytes, RelinkReport). Check `report.ok` before writing anything.

    reloc / anchors / auto_anchors select the block-5 relocation model (see D8 in the module
    docstring and native_linker/reloc_model.py). The default derives content-proven anchors from
    the zone's own StringTables and refuses if a substitution boundary cannot be placed to the
    byte; it never falls back to the refuted file = b5 + 64.
    """
    wiiu_zone, struct_layout, zs, W, BR, US = _import_backend()

    norm = _norm_subs(subs)
    rep = RelinkReport()
    rep.subs = len(norm)
    rep.total_delta = sum(d for (_s, _e, _n, d) in norm)

    # ---- D8: establish HOW targets move, or refuse ----------------------------------------
    RL = None
    RL_anchors = list(anchors) if anchors else []
    if reloc is not None:
        RL = reloc
    elif not rep.total_delta:
        RL = RM.Reloc([], [], 'identity')     # nothing grew, so nothing moved
    else:
        try:
            RL, note, RL_anchors = derive_reloc(zone, norm, anchors, auto_anchors)
            rep.notes.append(note)
        except RM.RelocError as ex:
            rep.fail('cannot place the substitution boundary in block-5 space, so this zone '
                     'cannot be grown soundly: %s' % ex)
    if RL is None:
        return zone, rep

    f = ZF.Facts(zone)

    # ---- pre-flight: the proven walk decides whether a relink is safe at all (D2/D3) -----
    try:
        with paths.backend_cwd():
            wk = zone_walk.walk(zone)
        rep.walk_ok = wk.ok
        rep.walk_verdict = wk.verdict()
        rep.walk_coverage = wk.coverage()
        rep.tail_bytes = max(0, wk.zone_len - wk.end)
        rep.tail_pct = 100.0 * rep.tail_bytes / wk.zone_len if wk.zone_len else 0.0
    except Exception as ex:
        rep.walk_verdict = '%s: %s' % (type(ex).__name__, ex)
        rep.fail('structural walk raised: ' + rep.walk_verdict)

    if require_walk and not rep.walk_ok:
        rep.fail('the structural walk did not complete (%s). Relinking on top of an incomplete '
                 'walk means blind-scanning the remainder -- that is the documented 77%%-blind '
                 'failure mode. Refusing.' % rep.walk_verdict)

    # ---- D7: every substitution must live in block 5 -------------------------------------
    for (s, e, _new, _d) in norm:
        if s < f.assets_end:
            rep.fail('substitution at 0x%x starts inside the container (assets_end=0x%x); '
                     'growing the container is add_asset() territory, not grow()'
                     % (s, f.assets_end))
        if e > len(zone):
            rep.fail('substitution at 0x%x ends past EOF' % s)

    # ---- D4/R4: interior-pointer detection ----------------------------------------------
    cen = census(zone, subs, reloc=RL)
    rep.alias_words = cen.alias_words
    rep.before, rep.after = cen.before, cen.after
    rep.interior = cen.interior
    rep.runtime_domain = cen.runtime_domain
    rep.phantoms = cen.phantoms
    if cen.interior:
        # ADVISORY only. The census cannot prove a word is a pointer, so a hard refusal here
        # would block safe edits on phantom evidence. The AUTHORITATIVE check is the population
        # pre-flight below.
        show = ', '.join('0x%x->rt%d' % (o, t) for o, _v, t, _s in cen.interior[:4])
        msg = ('%d alias-shaped word(s) target the INTERIOR of a resized span (advisory; the '
               'population pre-flight below is authoritative). First: %s'
               % (len(cen.interior), show))
        rep.warn(msg + (' -- allow_interior=True' if allow_interior else ''))

    # ---- D8: the AUTHORITATIVE interior check, driven by the POPULATION -------------------
    # ⚠ NOT by the walk. A relinker only discovers an interior target if it examines that
    # pointer, and the passes below examine one only when it clears the omap whitelist -- which
    # is keyed at `src_file - 64`, i.e. in the same coordinate system D8 refutes, so it admits a
    # largely arbitrary subset. Measured on a real 2-table grow of patch_zm: the passes hit ZERO
    # interior targets while 43 declared pointer sites sat inside the regrown bodies. Declared
    # sites only (StringTable cells + asset-array headerPtrs); a blind alias-shaped scan would
    # drag in ~700 K phantom words and refuse safe edits.
    if rep.total_delta and not RL.is_identity():
        pop = set(RM.stringtable_alias_offsets(zone))
        for i in range(f.asset_count):
            h = struct.unpack_from('>I', zone, f.assets_off + i * 8 + 4)[0]
            if ALIAS_LO < h < ALIAS_HI:
                if ((h >> 29) & 7) == BLOCK_VIRTUAL:
                    pop.add((h & 0x1FFFFFFF) - 1)
        bad = RM.preflight_interior(RL, pop)
        rep.notes.append('interior pre-flight: %d declared pointer site(s) checked, %d inside a '
                         'regrown body' % (len(pop), len(bad)))
        if bad and not allow_interior:
            rep.fail('%d declared pointer site(s) target the INTERIOR of a resized span and no '
                     'interval maps them (first %r). Regrowing a body re-lays-out its inside, so '
                     'these do NOT move by the body delta -- measured +1216..+2862 against body '
                     'deltas of +2252/+3756. Supply reloc= with intervals built from the two '
                     'serialisations of each regrown body.' % (len(bad), bad[:8]))
        elif bad:
            rep.warn('%d interior pointer site(s) left UNRELOCATED (allow_interior=True). They '
                     'now point into a body that has been re-laid-out.' % len(bad))
    if cen.runtime_domain:
        rep.warn('%d alias word(s) decode past block-5 (runtime domain: SndBank zone*/language* '
                 'and friends). They are NOT relinked here and need an rt-model re-bake (D5).'
                 % cen.runtime_domain)

    if not rep.ok:
        return zone, rep
    if rep.total_delta == 0:
        # Zero-delta control (T1): a relink that moves nothing must return the input untouched.
        rep.notes.append('zero delta -- returning input unchanged')
        return bytes(zone), rep

    # ---- structural relink, two passes ---------------------------------------------------
    r = wiiu_zone.ZoneReader(zone)
    r.read_string_table()
    r.read_asset_list()
    if r.assets_off != f.assets_off:
        r.assets_off = f.assets_off
        r.assets = []
        r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True)
    zc = W.ZoneCode(W.ZC_DIR)

    class WLEdit(US.MultiEdit):
        """Omap-whitelisted remap with explicit boundary semantics."""
        def remap_ptr(self, v):
            if v in (zs.FOLLOW, zs.INSERT, 0):
                return v
            if not (ALIAS_LO <= v < ALIAS_HI):
                return v
            blk, off = zs.decode_ptr(v)
            if blk == zs.BLOCK_VIRTUAL and self.delta:
                # ⛔ THE OMAP WHITELIST IS GONE FROM THIS PATH (D8b). remap_ptr is only ever applied
                # to words the STRUCT LAYOUT declares as pointers, so there is no phantom to guard
                # against and the whitelist bought nothing -- while costing 867 pointers that had to
                # move and wrongly bumping 225 that had to stay, because omap is keyed at
                # `src_file - 64`. The blind TAIL scan still has a whitelist; this path needs none.
                # The interior refusal lives in the population pre-flight, not here.
                try:
                    n = RL.new_off(off)
                except RM.InteriorTarget:
                    return v                      # already refused by the pre-flight
                if n != off:
                    return zs.encode_ptr(blk, n)
            return v

    def emit_pass(omap0):
        w = zs.ZoneWriter()
        w.push_block(zs.BLOCK_VIRTUAL)
        w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - B5_BASE
        em = (WLEdit if omap0 is not None else US.MultiEdit)(zone, L, zc, w, subs)
        em.delta = 1
        if omap0 is not None:
            em.omap.update(omap0)
        cur = r.assets_end
        brk = None
        for (cid, pc_, nm) in r.assets:
            root = W.ASSET_ROOT.get(nm)
            if root is None or root not in L.structs:
                continue
            try:
                cur = em.emit_asset(root, cur)
            except Exception as ex:
                brk = (nm, cur, '%s: %s' % (type(ex).__name__, str(ex)[:80]))
                break
        return em, w, cur, brk

    em1, _w1, end1, brk1 = emit_pass(None)
    omap = dict(em1.omap)
    em, w, cur, brk = emit_pass(omap)

    if len(em.subbed) != len(subs):
        rep.fail('only %d/%d substitutions fired; emit broke at %r'
                 % (len(em.subbed), len(subs), brk))
        return zone, rep
    if end1 != cur:
        rep.fail('pass-1 and pass-2 emits disagree on the walk end (0x%x vs 0x%x) -- the omap '
                 'changed the walk, which means the relink is not deterministic' % (end1, cur))
        return zone, rep
    if brk is not None:
        # D3: this used to be a print. It is a refusal now.
        rep.fail('the emit walk broke at %r (offset 0x%x): %s. Everything after that point '
                 'would be relinked by blind scan.' % (brk[0], brk[1], brk[2]))
        return zone, rep

    # ---- D1/R2: BYTE-GRANULAR whitelisted tail relink -------------------------------------
    tail_src = cur
    tail = bytearray(zone[tail_src:])
    bumped = 0
    blind = []
    if tail_relink and rep.total_delta:
        b5 = f.b5
        # ⭐ D8b: THE TAIL WHITELIST IS NOW IN BLOCK-5 SPACE. The tail is a blind byte scan, so
        # unlike the structural pass it genuinely needs a whitelist -- a data word can decode as a
        # plausible alias. But omap is keyed at `src_file - 64`, so testing a real block-5 offset
        # against it was a COINCIDENCE (the two spaces differ by ~112,800 on patch_zm), wrong in
        # both directions at once. Recover each registered span's FILE offset (`key + 64`, undoing
        # what `register` subtracted) and place it in b5 through the same anchor oracle that placed
        # the boundary, excluding -- never guessing -- any span whose D is not determined.
        try:
            reg, rstats = RM.b5_registered_set([k + B5_BASE for k in omap], RL_anchors,
                                               want_stats=True)
            rep.notes.append('tail whitelist: %d registered span(s), %d placed in b5 exactly, '
                             '%d excluded because D was not determined there'
                             % (rstats['registered'], rstats['placed'],
                                rstats['excluded (D not determined)']))
        except RM.RelocError as ex:
            rep.fail('the verbatim tail cannot be relinked soundly: its whitelist has to be placed '
                     'in block-5 space and that failed (%s). Pass tail_relink=False to leave the '
                     'tail alone, but note that any real pointer in it then stays stale.' % ex)
            return zone, rep
        # step 1, not 4. The body base is 1 mod 4 on patch_mp; a stride-4 sweep starting at the
        # tail origin samples the wrong phase and misses ~77% of sites.
        for fo in range(0, len(tail) - 3):
            v = struct.unpack_from('>I', tail, fo)[0]
            if not (ALIAS_LO <= v < ALIAS_HI):
                continue
            if v in (FOLLOW, INSERT):
                continue
            blk, off = zs.decode_ptr(v)
            if blk != zs.BLOCK_VIRTUAL:
                continue
            if off not in reg:
                if off <= b5:
                    blind.append((tail_src + fo, v, off))
                continue
            try:
                n = RL.new_off(off)
            except RM.InteriorTarget:
                rep.fail('interior pointer in the verbatim tail at 0x%x -> rt %d'
                         % (tail_src + fo, off))
                return zone, rep
            if n != off:
                struct.pack_into('>I', tail, fo, zs.encode_ptr(blk, n))
                bumped += 1
    rep.bumped = bumped
    rep.blind_candidates = blind
    if blind:
        rep.warn('%d alias-shaped word(s) in the %d B verbatim tail decode to a plausible block-5 '
                 'offset but are NOT in the b5-space registered set, so they were left alone. Each is '
                 'either GPU data that merely looks like a pointer (fine) or a real pointer the '
                 'walk never registered (a latent dangling alias). This count was previously '
                 'invisible.' % (len(blind), len(tail)))

    # ---- D6: container prefix copied verbatim; size fields patched in place ---------------
    out = bytearray(zone[:r.assets_end]) + bytes(w.buf) + bytes(tail)

    size = struct.unpack_from('>I', out, TOTAL_SIZE_OFF)[0]
    struct.pack_into('>I', out, TOTAL_SIZE_OFF, size + rep.total_delta)
    bs_off = BLOCK_SIZES_OFF + BLOCK_VIRTUAL * 4
    bs = struct.unpack_from('>I', out, bs_off)[0]
    struct.pack_into('>I', out, bs_off, bs + rep.total_delta)

    # ---- XAssetList headerPtr relink (cumulative) ----------------------------------------
    nhp = 0
    for i in range(r.asset_count):
        ho = r.assets_off + i * 8 + 4
        h = struct.unpack_from('>I', out, ho)[0]
        if ALIAS_LO < h < ALIAS_HI:
            blk, off = zs.decode_ptr(h)
            if blk == zs.BLOCK_VIRTUAL:
                try:
                    n = RL.new_off(off)
                except RM.InteriorTarget:
                    continue                      # already refused by the pre-flight
                if n != off:
                    struct.pack_into('>I', out, ho, zs.encode_ptr(blk, n))
                    nhp += 1
    rep.headerptrs = nhp

    if len(out) != len(zone) + rep.total_delta:
        rep.fail('output is %d B, expected %d (input %d %+d)'
                 % (len(out), len(zone) + rep.total_delta, len(zone), rep.total_delta))
        return zone, rep

    if verbose:
        print(rep.report())
    return bytes(out), rep
