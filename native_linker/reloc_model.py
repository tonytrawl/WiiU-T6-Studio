#!/usr/bin/env python3
"""reloc_model -- the ONE definition of "where did this pointer's target move to".

CANONICAL: native_linker/reloc_model.py

WHY THIS FILE EXISTS
--------------------
Two relinkers (`dlc loading/native/fullrelink/grow_relink.py` and `WiiU_FF_Studio/core/relink.py`)
each decided relocation by converting a block-5 pointer offset into a FILE offset with
`file = b5 + 64` and comparing that against the substitution's file range. That conversion is
false everywhere except the container region, so both were deciding a real question with a
made-up coordinate. Rule (U) in HANDOFF_pipeline_bake_rules.md has said so since it was banked:

    "Any tool resolving a body-region alias as file = b5 + 64 is UNSOUND. The omap whitelist
     (keyed at span_start - 64) is therefore a coincidence filter, not a pointer oracle, and
     must never gate a relink."

It was banked and never enforced, and zones kept being grown through the unsound path. Per law
(HI) -- a banked law becomes an enforced check or is demoted -- this module is the enforcement.
Both relinkers now ask it, so the rule cannot be re-implemented differently in two places (the
exact failure mode that made stage1_roundtrip and wiiu_zone diverge).

MEASURED, on the stock live patch_zm (10,316,826 B), oracle = content only (a StringTable cell
stores its target's djb2ci hash, so the target is proven by bytes, not by a model):

  D = b5 - file over 483 distinct content-confirmed targets
      min -1,980,635   max +113,055   31 distinct values   ** D == 64 in 0 of 483 **

  D is a STEP FUNCTION. Adjacent steps are as small as 2 bytes
      (+113,033 -> +113,035 -> +113,039 -> +113,045 -> +113,053 -> +113,055)
  and as large as -952,067 where non-virtual data intervenes.

THE TWO FACTS THE MODEL IS BUILT ON
-----------------------------------
1. b5 IS MONOTONIC IN FILE ORDER.  Restricted to targets whose string is >= 10 bytes (short
   strings cannot referee anything -- djb2ci collides, e.g. djb2ci('40') == djb2ci('2r')), there
   are ** 0 rt inversions in 205 ordered targets **. Every apparent inversion came from a short,
   high-multiplicity string ('clip' occurs 98x, '300' 8x, 'zgrief' 6x, 'zclassic' 8x) whose
   "resolution" was a weak hash match.

   ⭐ SO: (b5 >= b5_boundary)  <=>  (file >= file_boundary).  A b5-space threshold is SOUND.
   ⛔ "D is not monotonic, therefore runtime order != file order" IS A NON-SEQUITUR. D is
      (b5 - file); its non-monotonicity says file and b5 advance at different rates, NOT that b5
      ever goes backwards. Both are true at once and only the second would break a threshold.

2. BUT THE BOUNDARY MUST BE EXACT. Because D steps by as little as 2 bytes, a boundary derived
   from the NEAREST anchor inherits that anchor's D and lands wrong whenever a step falls in
   between -- every target in that window is then decided wrongly. That is what a
   nearest-anchor boundary costs, and it is a boundary-precision bug, not evidence against the
   threshold. `b5_spans_from_file_subs` therefore REFUSES unless the anchors bracketing a
   boundary agree on D.

3. AND IN-BODY TARGETS NEED A REAL MAP. Regrowing a table expands its cell array AHEAD of its
   inline strings, so a target inside the regrown body moves by the CELL-ARRAY growth, never by
   the body's own delta (measured +1216, +1219, +1794, +1948, +2860, +2862 against body deltas
   of +2252/+3756). No threshold of any kind can produce those numbers, so they are supplied
   explicitly as intervals or the target is REFUSED.

ORDER OF APPLICATION, and the boundary semantics (kept identical to core.relink._cumdelta_fn):
    interval covers off      -> new_lo + (off - lo)      exact remap
    off inside a span        -> InteriorTarget           refuse; caller must map it
    off == span start        -> unaffected               a span's first byte does not move
    off <  span start        -> unaffected
    off >= span end          -> off + sum(preceding deltas)

Everything here works in BLOCK-5 OFFSETS ONLY and never touches file offsets, encoded pointer
values or block numbers. That is deliberate: a module that cannot see a file offset cannot
reintroduce a file/b5 confusion. Callers decode and encode.

Self-test: python reloc_model.py --selftest
"""
import struct
import sys

FOLLOW = 0xFFFFFFFF
EMPTY_HASH = 5381                 # djb2ci(b'') -- the deduped empty string
MIN_ANCHOR_LEN = 10               # measured floor for a target that can referee D (see above)


class RelocError(Exception):
    """Base for every refusal in this module. Never caught internally."""


class InteriorTarget(RelocError):
    """A pointer targets the interior of a substituted body and no interval maps it.

    ⛔ NEVER fall back to `off + delta` here. The interior of a regrown body does not shift by
    the body's delta -- measured wrong by 600..2000 bytes -- so a fallback is a silent
    600..2000-byte-wrong pointer, which is exactly the class that parses, registers, passes
    every static gate and dies with no Com_Error.
    """


class BoundaryUnprovable(RelocError):
    """A substitution boundary could not be placed in b5 space to the byte.

    Raised rather than approximated. D steps by as little as 2 bytes, so an approximate
    boundary silently mis-decides every target in the window between the guess and the truth.
    """


# --------------------------------------------------------------------------- the model
class Reloc(object):
    """old block-5 offset -> new block-5 offset, with no coordinate model anywhere.

    b5_spans : [(b5_start, b5_end, delta)]  the OLD b5 extent of each substituted body and the
                                            number of bytes it grew by (may be negative).
    intervals: [(b5_lo, b5_hi, new_b5_lo)]  exact old->new for data CARRIED OVER inside a
                                            regrown body (its name string, every kept inline
                                            string). Half-open, [lo, hi).
    """

    def __init__(self, b5_spans, intervals=(), label=''):
        self.spans = sorted(tuple(s) for s in b5_spans)
        self.intervals = sorted(tuple(i) for i in intervals)
        self.label = label
        for i in range(1, len(self.spans)):
            if self.spans[i][0] < self.spans[i - 1][1]:
                raise RelocError('b5 spans overlap: %r and %r'
                                 % (self.spans[i - 1], self.spans[i]))
        for lo, hi, nlo in self.intervals:
            if hi < lo:
                raise RelocError('interval %r has hi < lo' % ((lo, hi, nlo),))
        self.total_delta = sum(d for _s, _e, d in self.spans)

    # ---- the whole decision, in one place -------------------------------------------
    def new_off(self, off):
        for lo, hi, nlo in self.intervals:
            if lo <= off < hi:
                return nlo + (off - lo)
        d = 0
        for s, e, dl in self.spans:
            if off >= e:
                d += dl
            elif off > s:
                # A length-neutral substitution leaves its interior exactly where it was, so an
                # interior pointer stays correct and refusing it would break the identity case.
                if dl:
                    raise InteriorTarget(
                        'block-5 offset %d is inside substituted span [%d, %d) (delta %+d) and no '
                        'interval maps it. The interior of a regrown body does not move by the '
                        "body's delta, so there is no safe fallback -- supply an interval built "
                        'from the two serialisations, or do not relocate this pointer.'
                        % (off, s, e, dl))
                break
            else:
                break
        return off + d

    def shift(self, off):
        """Convenience: how far this target moved. Raises InteriorTarget like new_off."""
        return self.new_off(off) - off

    def is_identity(self):
        return not self.total_delta and not self.intervals

    def describe(self):
        L = ['Reloc(%s) total_delta %+d' % (self.label or 'unlabelled', self.total_delta)]
        for s, e, d in self.spans:
            L.append('  b5 span [%d, %d)  %d B  delta %+d' % (s, e, e - s, d))
        L.append('  %d interval(s) mapping carried data inside regrown bodies'
                 % len(self.intervals))
        return '\n'.join(L)


class LegacyFileBase64Reloc(object):
    """⛔ THE REFUTED MODEL, kept ONLY so a historical build can be reproduced byte-for-byte.

    Decides relocation as `cumdelta(b5 + 64)` against FILE-space substitution ranges. Measured
    wrong on 0 of 483 targets being at D == 64 -- i.e. wrong at every single one outside the
    container. Census of what it did to one real 2-table grow of patch_zm:

        23 UNEDITED tables:  6328 correct | 225 bumped that must NOT move | 867 not bumped that MUST
        zm/mapstable.csv:      63 correct |  42 wrong
        => ~1,335 wrongly-relocated pointers, a LOWER BOUND

    Never select this because a sound model is inconvenient to build. It exists so that
    `git bisect` over old zones has something to compare against.
    """

    def __init__(self, file_subs, label=''):
        self.file_subs = sorted(tuple(s) for s in file_subs)   # [(start, end, delta)]
        self.label = label
        self.total_delta = sum(d for _s, _e, d in self.file_subs)
        self.intervals = ()
        self.spans = ()

    def new_off(self, off):
        fo = off + 64                       # ⛔ the false conversion, preserved deliberately
        d = 0
        for s, e, dl in self.file_subs:
            if fo >= e:
                d += dl
            else:
                break
        return off + d

    def shift(self, off):
        return self.new_off(off) - off

    def is_identity(self):
        return not self.total_delta

    def describe(self):
        return ('LegacyFileBase64Reloc(%s) total_delta %+d  ** REFUTED MODEL, file = b5 + 64 **'
                % (self.label or 'unlabelled', self.total_delta))


# ------------------------------------------------------- placing a FILE offset in b5 space
class DMap(object):
    """Content-proven anchors -> D at a file offset, or a refusal. The one bracketing routine.

    Every question of the form "where does file offset X live in block 5" goes through here, so a
    boundary and a registered target can never be placed by two different rules.
    """

    def __init__(self, anchors):
        if not anchors:
            raise BoundaryUnprovable(
                'no content-confirmed (file, b5) anchors were supplied, so nothing can be placed '
                'in b5 space. Without anchors the only available rule is the refuted file = b5 + 64.')
        self.A = sorted(set((int(f), int(b)) for f, b in anchors))

    def bracket(self, fo, what='offset'):
        """-> ((file_lo, b5_lo), (file_hi, b5_hi)). Raises if `fo` is not bracketed."""
        lo = hi = None
        for f, b in self.A:
            if f <= fo:
                lo = (f, b)
            else:
                hi = (f, b)
                break
        if lo is None:
            raise BoundaryUnprovable(
                '%s at file 0x%X lies BEFORE every anchor (first anchor 0x%X), so D there is '
                'unmeasured.' % (what, fo, self.A[0][0]))
        if hi is None:
            raise BoundaryUnprovable(
                '%s at file 0x%X lies AFTER every anchor (last anchor 0x%X), so nothing brackets '
                'it and D there is unmeasured.' % (what, fo, self.A[-1][0]))
        return lo, hi

    def exact_at(self, fo):
        """-> D if the bracketing anchors AGREE, else None.

        ⛔ None means EXCLUDE, never guess. For a registered target an approximate b5 offset is
        worse than no entry: a real pointer at the true offset then fails to match (so it is left
        stale) while a data word at the wrong offset may match (so it is corrupted). Both
        directions are silent.
        """
        try:
            lo, hi = self.bracket(fo)
        except BoundaryUnprovable:
            return None
        d_lo, d_hi = lo[1] - lo[0], hi[1] - hi[0]
        return d_lo if d_lo == d_hi else None


def b5_registered_set(registered_file_offsets, anchors, want_stats=False):
    """The SOUND replacement for grow_relink's omap whitelist. -> set of block-5 offsets.

    ⛔⛔ WHAT WAS WRONG. The tail pass decides whether an alias-shaped word in the un-walked
    verbatim region is a real pointer by testing `off in omap`. `ReEmitter.register` keys omap at
    `src_file - 64`, so those keys are FILE offsets minus 64, while `off` is a genuine block-5
    offset. On stock patch_zm the two spaces differ by ~112,800, so membership was a COINCIDENCE
    TEST -- it hit when some unrelated span's `file - 64` happened to equal this pointer's b5.
    That is the mechanism behind the censused 225-bumped-that-must-not / 867-not-bumped-that-must
    split: a filter that is wrong in both directions at once. Bake rule (U) called the whitelist
    "a coincidence filter, not a pointer oracle" and it kept gating relinks anyway.

    THE FIX. A registered span's FILE offset is real (the walk measured it). Convert each one into
    b5 through the anchor oracle, exactly, and drop the ones whose D is not determined. The result
    is a whitelist in the same coordinate system as the pointers it is tested against.

    Pass the span FILE offsets (recover them from omap keys as `key + 64` -- that is what
    `register` subtracted). Offsets whose D is ambiguous are EXCLUDED and counted, never guessed.
    """
    dm = DMap(anchors)
    out, stats = set(), {'registered': 0, 'placed': 0, 'excluded (D not determined)': 0}
    for fo in sorted(set(int(f) for f in registered_file_offsets)):
        stats['registered'] += 1
        d = dm.exact_at(fo)
        if d is None:
            stats['excluded (D not determined)'] += 1
            continue
        out.add(fo + d)
        stats['placed'] += 1
    return (out, stats) if want_stats else out


def b5_spans_from_file_subs(file_subs, anchors, max_anchor_gap=None, target_offsets=None):
    """[(file_start, file_end, delta)] + [(file, b5)] anchors -> [(b5_start, b5_end, delta)].

    An anchor is a pair proven by CONTENT, not by a model: a file offset and the b5 offset that
    genuinely addresses it. `anchors_from_stringtables` produces them; the ZM lane's st_read3
    produces a richer set the same way.

    A boundary is accepted only when the anchor at or before it and the anchor after it agree on
    D = b5 - file. Equal D at both brackets means no step was crossed, so D is that value at the
    boundary and b5 = file + D is exact.

    ⚠ THIS IS A BRACKET TEST, NOT A PROOF. D could in principle step up and back down between two
    anchors. It refuses in the ambiguous case and it is the caller's job to supply anchors close
    to the boundary; it will never silently return an approximate boundary, which is the one
    behaviour that matters.

    target_offsets RELAXES the refusal WITHOUT weakening it. When the brackets disagree, D at the
    boundary is only known to lie in [min, max] of the two, so the b5 boundary is only known to
    lie in a window that many bytes wide. That ambiguity is harmless if NO REAL TARGET LIES IN THE
    WINDOW: every boundary in it then yields identical decisions for every pointer that exists, so
    the model is exact where it is used even though D there is unmeasured. Pass the block-5 offsets
    of the targets that must be decided (a SUPERSET is safe -- more offsets can only cause a
    refusal, never a wrong answer) and the window is checked instead of assumed. Measured need for
    this: on stock patch_zm the real ZM globe boundary at file 0x5B15C9 is bracketed by anchors
    8,845 B apart whose D differs by 51, so a blanket refusal would block the one edit that
    actually shipped, while the 51-byte window is provably empty of targets.
    """
    dm = DMap(anchors)
    T = sorted(set(int(t) for t in target_offsets)) if target_offsets is not None else None

    def D_at(fo, what):
        lo, hi = dm.bracket(fo, what)
        d_lo, d_hi = lo[1] - lo[0], hi[1] - hi[0]
        if d_lo != d_hi:
            # D at the boundary is somewhere in [min, max]; the b5 boundary is somewhere in the
            # corresponding window. Harmless iff no target that must be decided lies in it.
            w_lo, w_hi = fo + min(d_lo, d_hi), fo + max(d_lo, d_hi)
            if T is not None:
                inside = [t for t in T if w_lo <= t <= w_hi]
                if not inside:
                    return max(d_lo, d_hi)      # any value in the window decides identically
                raise BoundaryUnprovable(
                    '%s at file 0x%X is bracketed by anchors with DIFFERENT D (0x%X has D %+d, '
                    '0x%X has D %+d), so the b5 boundary is only known to lie in [%d, %d] -- and '
                    '%d target(s) LIE IN THAT WINDOW (first %r), so the choice changes their '
                    'answer. Supply an anchor inside [0x%X, 0x%X).'
                    % (what, fo, lo[0], d_lo, hi[0], d_hi, w_lo, w_hi, len(inside), inside[:6],
                       lo[0], hi[0]))
            raise BoundaryUnprovable(
                '%s at file 0x%X is bracketed by anchors with DIFFERENT D: 0x%X has D %+d and '
                '0x%X has D %+d. A step falls between them, so D at the boundary is not '
                'determined and any b5 boundary here would be a guess. Supply an anchor inside '
                '[0x%X, 0x%X), or pass target_offsets so the %d-byte ambiguity window can be '
                'checked for emptiness instead.'
                % (what, fo, lo[0], d_lo, hi[0], d_hi, lo[0], hi[0], abs(d_hi - d_lo)))
        if max_anchor_gap is not None and (hi[0] - lo[0]) > max_anchor_gap:
            raise BoundaryUnprovable(
                '%s at file 0x%X is bracketed by anchors 0x%X and 0x%X, %d B apart, which exceeds '
                'the caller\'s max_anchor_gap of %d even though both agree on D %+d.'
                % (what, fo, lo[0], hi[0], hi[0] - lo[0], max_anchor_gap, d_lo))
        return d_lo

    out = []
    for s, e, d in sorted(file_subs):
        d_s = D_at(s, 'substitution start')
        d_e = D_at(e, 'substitution end')
        out.append((s + d_s, e + d_e, d))
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][1]:
            raise BoundaryUnprovable(
                'derived b5 spans overlap (%r then %r), which cannot happen for disjoint file '
                'spans -- the anchors are inconsistent.' % (out[i - 1], out[i]))
    return out


# ------------------------------------------------------------------- the anchor oracle
def _stringtables(z):
    """Every StringTable asset body: {FOLLOW, cols, rows, FOLLOW, cellIndex*} + inline name.

    Bodies are NOT 4-aligned, so every FOLLOW sentinel is a candidate. Same shape st_read3 uses.
    """
    out = []
    pos = z.find(b'\xff\xff\xff\xff')
    while pos >= 0:
        hdr = pos
        pos = z.find(b'\xff\xff\xff\xff', pos + 1)
        if hdr + 24 > len(z):
            continue
        np_, cols, rows, vals, _cidx = struct.unpack_from('>5I', z, hdr)
        if np_ != FOLLOW or vals != FOLLOW or not (0 < cols < 64) or not (0 < rows < 4096):
            continue
        e = z.find(b'\x00', hdr + 20)
        if e < 0 or not (4 <= e - (hdr + 20) <= 96):
            continue
        nm = z[hdr + 20:e]
        if not all(32 <= b < 127 for b in nm) or not nm.lower().endswith(b'.csv'):
            continue
        out.append((hdr, cols, rows, nm))
    return out


def _alias_cells(z, hdr, cols, rows):
    """-> [(cell_file_off, b5_off, hash)] for every block-5 alias cell in this table."""
    name_end = z.index(b'\x00', hdr + 20)
    cells0 = name_end + 1
    n = cols * rows
    o = cells0 + n * 8
    if o > len(z):
        return []
    out = []
    for k in range(n):
        p, h = struct.unpack_from('>2I', z, cells0 + k * 8)
        if p == FOLLOW:
            se = z.find(b'\x00', o)
            if se < 0:
                return out
            o = se + 1                       # inline cell: its bytes live here, in stream order
        elif p:
            w = (p - 1) & 0xFFFFFFFF         # banked handle law: b5 = (v-1) & 0x1FFFFFFF
            if (w >> 29) == 5:
                out.append((cells0 + k * 8, w & 0x1FFFFFFF, h))
    return out


def djb2ci(s):
    h = 5381
    for c in s.lower():
        h = ((h * 33) + c) & 0xFFFFFFFF
    return h


def container_edge_anchor(assets_end):
    """The ONE place `file = b5 + 64` is exact, expressed as an anchor -> (file, b5).

    Block 5 starts at stream offset 64, so for every offset in the container region -- the
    script-string table and the asset array -- b5 = file - 64 holds by construction. That is what
    bake rule (Q) says and it is why seeding a ZoneWriter with `assets_end - 64` is correct. The
    rule only becomes false once body-region data starts interleaving and aligning.

    ⭐ WHY THIS MATTERS: without it the anchor set for a zone like mp_raid starts at the first
    StringTable (file 0x3EC4A), so a script body at file 0x8542 lies BEFORE every anchor and no
    boundary can be placed at all -- the relinker would refuse every GSC edit in the zone. With
    it, such a boundary is bracketed by [container edge, first table] and the ambiguity window
    between their two D values can be tested for emptiness instead of refused outright.

    ⚠ Use it as ONE anchor among others, never as a conversion. Rule (U) measured drift 76 at
    mp_raid's FIRST body, so D has already left -64 by the time real bodies begin.
    """
    return (int(assets_end), int(assets_end) - 64)


def preflight_interior(reloc, offsets):
    """-> sorted list of the block-5 offsets in `offsets` that land in a regrown body unmapped.

    ⚠ WHY THIS IS SEPARATE FROM THE RELINK PASS. A relinker only discovers an interior target if
    it actually examines that pointer, and grow_relink examines a pointer only when it passes the
    omap whitelist -- which is keyed in the refuted `file - 64` space and therefore admits a
    largely arbitrary subset. So "no interior target was hit during the walk" means nothing: it
    can equally mean the walk never looked. Measured: a real 2-table grow of patch_zm hit ZERO
    interior targets during the passes while 13 content-proven cells were sitting inside the
    regrown bodies the whole time.

    The refusal therefore has to be driven by the POPULATION, not by the walk. Pass declared
    pointer sites only (StringTable cells, asset-array headerPtrs) -- NOT a blind alias-shaped
    scan, because on a 10 MB zone that carries ~700 K phantom words and any that decode into the
    regrown body's interior would refuse a perfectly safe edit.
    """
    bad = []
    for off in sorted(set(int(o) for o in offsets)):
        try:
            reloc.new_off(off)
        except InteriorTarget:
            bad.append(off)
    return bad


def stringtable_alias_offsets(z):
    """Every block-5 offset addressed by a StringTable cell -> set of b5 offsets.

    This is the population that a StringTable-bearing edit must decide correctly, and it is what
    `target_offsets` wants. It is NOT the whole zone's pointer population; passing a superset is
    safe (it can only cause a refusal), so a caller with a wider structural walk should pass more.
    """
    out = set()
    for hdr, cols, rows, _nm in _stringtables(z):
        try:
            for _off, b5, _h in _alias_cells(z, hdr, cols, rows):
                out.add(b5)
        except (struct.error, ValueError):
            continue
    return out


def anchors_from_stringtables(z, min_len=MIN_ANCHOR_LEN, want_stats=False):
    """Derive content-proven (file, b5) anchors from the zone's own StringTables.

    A StringTable alias cell stores its target string's djb2ci hash, so the target can be found
    by CONTENT with no coordinate model at all. Purity rules, each one paid for:

      * min_len (default 10). Measured: at >= 10 bytes there are 0 rt inversions in 205 targets;
        below it every inversion traced to a short high-multiplicity string.
      * the hash must match exactly ONE position in a SUFFIX-AWARE index. An alias may legally
        target the interior of a pool ("zombie_weapon_pistol" + 7 == "weapon_pistol"), so
        indexing only whole NUL-delimited strings mistakes a collision for a unique hit.
      * ⚠ and then a CASE-INSENSITIVE LITERAL re-count over the whole zone. The suffix index
        only walks runs that are entirely printable, so it cannot see a copy of the target that
        sits in binary fill -- 'weapon_pistol' has exactly such a second copy at 0x594C3F inside
        0xFF fill, and without this re-count it is "uniquely" resolved to the WRONG offset,
        producing a D of -307,765 against its neighbours' +113,053. djb2ci is case-insensitive,
        so the re-count must be too.

    -> [(file, b5)] sorted, or ([(file, b5)], stats) when want_stats.
    """
    stats = {'tables': 0, 'alias cells': 0, 'empty hash': 0, 'no candidate': 0,
             'ambiguous in index': 0, 'rejected by literal re-count': 0, 'anchors': 0}

    index = {}
    n, i = len(z), 0
    while i < n:
        j = z.find(b'\x00', i)
        if j < 0:
            break
        run = z[i:j]
        if run and len(run) >= min_len and all(0x20 <= c < 0x7f for c in run):
            for k in range(0, len(run) - min_len + 1):
                index.setdefault(djb2ci(run[k:]), []).append(i + k)
        i = j + 1

    zl = z.lower()
    pairs, seen = [], set()
    for hdr, cols, rows, _nm in _stringtables(z):
        stats['tables'] += 1
        try:
            cells = _alias_cells(z, hdr, cols, rows)
        except (struct.error, ValueError):
            continue
        for _off, b5, h in cells:
            stats['alias cells'] += 1
            if h == EMPTY_HASH:
                stats['empty hash'] += 1
                continue
            cand = index.get(h)
            if not cand:
                stats['no candidate'] += 1
                continue
            if len(cand) > 1:
                stats['ambiguous in index'] += 1
                continue
            f = cand[0]
            e = z.find(b'\x00', f)
            if e < 0:
                continue
            lit = zl[f:e] + b'\x00'
            cnt, p = 0, 0
            while True:
                q = zl.find(lit, p)
                if q < 0:
                    break
                cnt += 1
                if cnt > 1:
                    break
                p = q + 1
            if cnt != 1:
                stats['rejected by literal re-count'] += 1
                continue
            if (f, b5) not in seen:
                seen.add((f, b5))
                pairs.append((f, b5))
                stats['anchors'] += 1
    pairs.sort()
    return (pairs, stats) if want_stats else pairs


# ------------------------------------------------------------------------------ self-test
def selftest():
    rows, failed = [], 0

    def check(tag, cond, detail):
        rows.append(('PASS' if cond else 'FAIL', tag, detail))
        return 0 if cond else 1

    # R1 identity
    r = Reloc([], [], 'identity')
    failed += check('R1', r.is_identity() and r.new_off(1234) == 1234,
                    'no spans => identity; new_off(1234) = %d' % r.new_off(1234))

    # R2 threshold semantics, including the two boundary edges
    r = Reloc([(1000, 2000, +8)], [], 'one span')
    got = [r.new_off(x) for x in (999, 1000, 2000, 2001)]
    failed += check('R2', got == [999, 1000, 2008, 2009],
                    'before/at-start/at-end/after -> %r (want [999, 1000, 2008, 2009])' % got)

    # R3 interior refuses, and says why
    try:
        r.new_off(1500)
        failed += check('R3', False, 'interior offset 1500 did NOT raise')
    except InteriorTarget as ex:
        failed += check('R3', 'no safe fallback' in str(ex),
                        'interior raised InteriorTarget naming the reason')

    # R4 a length-neutral span does NOT refuse its interior
    r0 = Reloc([(1000, 2000, 0)], [], 'neutral')
    failed += check('R4', r0.new_off(1500) == 1500,
                    'length-neutral interior stays put (%d)' % r0.new_off(1500))

    # R5 intervals win over the threshold and are exact
    r = Reloc([(1000, 2000, +2252)], [(1500, 1510, 4000)], 'interval')
    failed += check('R5', r.new_off(1500) == 4000 and r.new_off(1505) == 4005,
                    'in-body carried data remapped exactly: 1500->%d, 1505->%d'
                    % (r.new_off(1500), r.new_off(1505)))

    # R6 cumulative over several spans
    r = Reloc([(100, 200, +10), (300, 400, +20)], [], 'multi')
    got = [r.new_off(x) for x in (50, 250, 450)]
    failed += check('R6', got == [50, 260, 480], 'cumulative -> %r (want [50, 260, 480])' % got)

    # R7 overlapping spans refused at construction
    try:
        Reloc([(100, 300, +1), (200, 400, +1)])
        failed += check('R7', False, 'overlapping b5 spans were accepted')
    except RelocError:
        failed += check('R7', True, 'overlapping b5 spans refused at construction')

    # R8 boundary derivation: equal bracketing D accepted, exactly
    anchors = [(1000, 1064), (5000, 5064)]
    spans = b5_spans_from_file_subs([(2000, 3000, +16)], anchors)
    failed += check('R8', spans == [(2064, 3064, 16)],
                    'equal bracketing D +64 -> %r' % (spans,))

    # R9 unequal bracketing D REFUSED rather than approximated
    try:
        b5_spans_from_file_subs([(2000, 3000, +16)], [(1000, 1064), (5000, 5000)])
        failed += check('R9', False, 'a D step between the brackets was NOT refused')
    except BoundaryUnprovable as ex:
        failed += check('R9', 'DIFFERENT D' in str(ex),
                        'D step between brackets refused, message names both Ds')

    # R10 no anchors at all is a refusal, not a fallback to +64
    try:
        b5_spans_from_file_subs([(2000, 3000, +16)], [])
        failed += check('R10', False, 'empty anchor set was NOT refused')
    except BoundaryUnprovable as ex:
        failed += check('R10', 'b5 + 64' in str(ex),
                        'empty anchors refused, message names the refuted rule')

    # R11 a boundary outside the anchor range is refused (nothing brackets it)
    try:
        b5_spans_from_file_subs([(6000, 7000, +16)], anchors)
        failed += check('R11', False, 'an unbracketed boundary was NOT refused')
    except BoundaryUnprovable as ex:
        failed += check('R11', 'AFTER every anchor' in str(ex),
                        'boundary past the last anchor refused')

    # R12 the legacy model is still exactly the refuted rule (so a bisect reproduces old bytes)
    lg = LegacyFileBase64Reloc([(2000, 3000, +16)], 'legacy')
    failed += check('R12', lg.new_off(2936) == 2952 and lg.new_off(2935) == 2935,
                    'legacy decides at b5+64 vs file end: 2936->%d, 2935->%d'
                    % (lg.new_off(2936), lg.new_off(2935)))

    # R13 the two models genuinely disagree -- proves the fix is not cosmetic.
    # Same edit, expressed soundly: file span (2000, 3000) at D = +64 is b5 span (2064, 3064).
    sound = Reloc([(2064, 3064, +16)], [], 'sound')
    agree = all(sound.new_off(x) == lg.new_off(x) for x in range(3064, 3164))
    silent = 0
    for x in range(2936, 3064):
        try:
            sound.new_off(x)
        except InteriorTarget:
            silent += lg.new_off(x) != x        # legacy moved a target the sound model refuses
    failed += check('R13', agree and silent == 128,
                    'past the span both models agree (%r); inside it legacy silently relocates '
                    '%d target(s) that the sound model refuses as interior' % (agree, silent))

    # R14 unequal brackets + an EMPTY ambiguity window -> accepted, because every boundary in the
    # window decides every existing target identically
    anch = [(1000, 1100), (5000, 5064)]          # D +100 then +64: a 36 B window at the boundary
    spans = b5_spans_from_file_subs([(2000, 3000, +16)], anch,
                                    target_offsets=[1500, 4000, 4500])
    failed += check('R14', spans == [(2100, 3100, 16)],
                    'empty ambiguity window accepted -> %r' % (spans,))

    # R15 unequal brackets + a target INSIDE the window -> still refused, and it names the target.
    # The window for the start boundary is [2000+64, 2000+100] = [2064, 2100], so 2080 is in it.
    try:
        b5_spans_from_file_subs([(2000, 3000, +16)], anch, target_offsets=[2080])
        failed += check('R15', False, 'a target inside the ambiguity window was NOT refused')
    except BoundaryUnprovable as ex:
        failed += check('R15', 'LIE IN THAT WINDOW' in str(ex) and '2080' in str(ex),
                        'target inside the window refused, message names it')

    # R16 the relaxation must not weaken the equal-D path: same answer with or without targets
    a2 = [(1000, 1064), (5000, 5064)]
    failed += check('R16',
                    b5_spans_from_file_subs([(2000, 3000, +16)], a2)
                    == b5_spans_from_file_subs([(2000, 3000, +16)], a2, target_offsets=[2064]),
                    'equal-D brackets are unaffected by target_offsets')

    # ---- the b5-space registered set (D8b: the tail whitelist) ----------------------------
    # Anchors: D +64 up to file 5000, then D +100 from 5000 on. So a span at 2000 is bracketed by
    # two anchors that AGREE (+64) and is placeable; a span at 6000 is bracketed by +100/+100 and
    # is placeable; a span at 4000 straddles the step and must be EXCLUDED, not guessed.
    ra = [(1000, 1064), (4500, 4564), (5000, 5100), (9000, 9100)]
    reg, rs = b5_registered_set([2000, 6000, 4800], ra, want_stats=True)
    failed += check('R17', reg == {2064, 6100} and rs['placed'] == 2
                    and rs['excluded (D not determined)'] == 1,
                    'placed %r, excluded %d (the span straddling the D step is dropped, not '
                    'guessed)' % (sorted(reg), rs['excluded (D not determined)']))

    # R18 outside the anchor range -> excluded, never extrapolated
    reg2, rs2 = b5_registered_set([50, 99999], ra, want_stats=True)
    failed += check('R18', not reg2 and rs2['excluded (D not determined)'] == 2,
                    'offsets before the first and after the last anchor are both excluded (%d)'
                    % rs2['excluded (D not determined)'])

    # R19 the whitelist really did change coordinate system: the old rule keyed at file-64, so it
    # would have produced {1936, 5936} for these spans, sharing NOTHING with the sound answer.
    old = set(fo - 64 for fo in (2000, 6000))
    failed += check('R19', not (old & reg),
                    'sound registered set %r shares no member with the refuted file-64 keys %r'
                    % (sorted(reg), sorted(old)))

    # R20 DMap.exact_at agrees with the set-builder and refuses the same cases
    dm = DMap(ra)
    failed += check('R20', dm.exact_at(2000) == 64 and dm.exact_at(6000) == 100
                    and dm.exact_at(4800) is None and dm.exact_at(50) is None,
                    'exact_at: 2000->%r 6000->%r 4800->%r 50->%r'
                    % (dm.exact_at(2000), dm.exact_at(6000), dm.exact_at(4800), dm.exact_at(50)))

    for st, tag, detail in rows:
        print('  %-4s %-5s %s' % (st, tag, detail))
    print('\n  %d passed, %d failed' % (len(rows) - failed, failed))
    return 1 if failed else 0


def _anchor_demo(path):
    """Derive anchors from a real zone and report their purity. Diagnostic, not a gate."""
    import os
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _p in ('wiiu_ref', 'WiiU_FF_Studio'):        # wiiu_ff lives in WiiU_FF_Studio
        _q = os.path.join(_root, _p)
        if os.path.isdir(_q) and _q not in sys.path:
            sys.path.insert(0, _q)
    if path.lower().endswith('.zone'):
        z = open(path, 'rb').read()
    else:
        import wiiu_ff
        z = bytes(wiiu_ff.decrypt(open(path, 'rb').read())[1])
    pairs, stats = anchors_from_stringtables(z, want_stats=True)
    print('zone %s  %d B' % (os.path.basename(path), len(z)))
    for k in ('tables', 'alias cells', 'empty hash', 'no candidate', 'ambiguous in index',
              'rejected by literal re-count', 'anchors'):
        print('  %-30s %d' % (k, stats[k]))
    if not pairs:
        return 1
    ds = [b - f for f, b in pairs]
    print('  D min %+d  max %+d  distinct %d   D == 64: %d'
          % (min(ds), max(ds), len(set(ds)), sum(1 for d in ds if d == 64)))
    inv = sum(1 for i in range(1, len(pairs)) if pairs[i][1] < pairs[i - 1][1])
    print('  rt inversions in file order: %d  <- 0 means a b5 threshold is sound here' % inv)
    return 0


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    if len(sys.argv) > 1:
        sys.exit(_anchor_demo(sys.argv[1]))
    print(__doc__)
    sys.exit(selftest())
