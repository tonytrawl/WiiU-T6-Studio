#!/usr/bin/env python3
"""I1 STAGE 1 -- carry the PC dedup IDENTITY through the converter.
Scope: XModel.materialHandles ONLY.  FX and XAnimParts keep the current path.

WHY THIS EXISTS
---------------
`Omap.reloc` has the signature word-in / word-out, so the only thing it can do
with a dedup back-reference is arithmetic: `b5 = self.pc_inv.stream(b5)`
(produce_nobackbone.py:446) inverts a PC RUNTIME address through a per-asset
piecewise inverse of the PC runtime simulation and then maps the result over
CONSOLE-side layout.  MEASURED (lane X, IX8): 3,998/3,998 alias words across the
three consumers reach that line and zero take any identity-carrying branch, and
the inversion lands tens of KB away -- frequently inside a different PC asset.
Identity is destroyed, not merely displaced: 431 PC identity classes come out as
477 distinct console values, 59 classes split, 13 values collapse two classes.

THE SHAPE OF THE FIX is the one already in-tree and boot-proven for the et7
class, `Omap.reloc_asset_entry`: resolve the call site BEFORE the generic chain,
decoding the PC word to an ORDINAL identity and re-minting from OUR OWN emitted
cell.  No inverse is constructed anywhere in this module, and no PC RUNTIME
address is ever used as an answer -- the identity a payload resolves to is a PC
FILE offset (rule: identification-without-resolution).

WHAT IS AND IS NOT REUSED
-------------------------
`xsurface_dedup_intent.py` (landed, 290 lines) is NOT used.  It is a PC-side
dedup identity resolver of the same general family, but it answers a DISJOINT
question: it keys XSurface geometry on (vertCount, triCount) at PC XSurface +32
and returns (model, surface index).  A Material handle carries no counts, its
payload set does not intersect the matHandles payload set at all, and its
anchoring rule yields ZERO anchors on matHandles, so the method cannot start
here.  Measured separately (reconcile lane); recorded so the next reader does
not re-derive it.  Its own locator defect is filed as its own task.

THE LAW SET (D2's chain, verbatim in content, with the E0 reachability repair)
------------------------------------------------------------------------------
Candidates are the FOLLOW cells of the same consumer census; payloads are the
distinct PC alias values.  For a payload P[j] the feasible candidate set obeys:
  L1  the target is a FOLLOW cell of the same consumer census
  L2  the target precedes the class's FIRST alias cell that uses that payload
  L3  payload order == target stream order (both strictly increasing)
  L4  two payloads whose targets sit in the SAME contiguous cell array satisfy
      dPayload == dStream EXACTLY
  L5  otherwise 0 < dPayload <= dStream + SLACK
`|feasible| == 1` mints.  Anything else REFUSES -- and a refusal falls back to
today's `reloc`, never to a guess.

E0 REPAIR (blocking precondition, cleared before this module was written): the
DP counts REACHABILITY, never PATHS.  Path counts are unbounded, which forced a
`CAP` saturation, and inclusion-exclusion against a saturated prefix drove `tot`
negative so `max(tot,0)` clamped it to a FALSE ZERO -- deleting a genuinely
feasible candidate and, if it left a singleton, minting a wrong word SILENTLY.
Booleans are bounded by nF, saturation is structurally impossible, and any
negative `tot` is now counted as a hard defect (`neg_events`) instead of clamped.

SLACK is calibrated on two zones only (max measured rise 533 B on raid, 0 on
nuked); 1024 and 4096 give identical answers on raid and 0 vs 1024 give
byte-identical answers on nuked.  That is a two-zone calibration, not a family
law -- see E7.
"""

import bisect
import collections

FOLLOW_VALS = (0xFFFFFFFF, 0xFFFFFFFE)
DEFAULT_SLACK = 1024

# The most recently constructed binder. Diagnostics only: the assembler's fatal
# bar can abort a build AFTER the final emit pass, and the binder's numbers are
# complete at that point, so a post-mortem needs a handle that survives the
# raise. Nothing in the emit path reads this.
LAST = None


def is_alias(v):
    """A block-5 dedup alias word."""
    return 0xA0000001 <= v <= 0xBFFFFFFF


class MharrBinder(object):
    """Census + resolver + our-side holder registry for XModel.materialHandles.

    Lifecycle, driven by the assembler:
      pass 1 : observe() builds the census (and registers holders, unused)
      freeze_and_resolve()  -- once, on the PC census only
      pass 2+: observe() registers holders in OUR stream; bind_cell() answers

    A cell is bound only when ALL of these hold, and every failure is counted
    and printed rather than absorbed:
      * the payload has a UNIQUE feasible candidate            (else refuse_ambiguous)
      * that candidate is a cell of the frozen census          (else refuse_no_answer)
      * that cell is ALREADY registered in our emitted stream  (else refuse_unreached)
    """

    def __init__(self, slack=DEFAULT_SLACK, verbose=True):
        self.slack = slack
        self.verbose = verbose
        # Omap._encode: our stream offset -> runtime handle. Set by the
        # assembler. Deliberately the SAME chokepoint every other minted
        # pointer uses, so I1 inherits the rt model rather than forking it.
        self.encode = None
        self.pc_arr = None         # (assets_off_b5, n_assets), et7 exclusion
        # --- PC census (frozen after pass 1) ---
        self.cells = []            # (pc_slot_off, owner, idx, word) in observe order
        self.by_off = {}           # pc_slot_off -> position in self.cells
        self.owners = []           # owner keys in first-seen order (XModel ordinal)
        self._owner_ord = {}
        self.frozen = False
        self.answers = {}          # payload(b5 rt) -> pc_slot_off of the unique holder
        self.feasible_size = {}    # payload -> |feasible|  (diagnostics)
        # payload -> [pc_slot_off, ...]: the full feasible set. Kept so SOUNDNESS
        # (is the truth inside the set?) can be measured DIRECTLY against an
        # external oracle instead of proxied by the mint. Small: one short list
        # per distinct payload, a few hundred per zone.
        self.feasible_off = {}
        self.resolve_stats = {}
        # --- our-side registry, rebuilt every pass ---
        self._reg = {}             # pc_slot_off -> our console b5 offset
        self._staged = {}          # same, for the asset currently being emitted
        self._reg_alias = {}       # (EW/FIX 35) pc ALIAS cell off -> our console b5
        self._staged_alias = {}    # same, for the asset currently being emitted
        self._co_origin = None
        # --- permanent defect counters (never reset) ---
        self.census_drift = 0      # F-W1: unknown cell offset seen after freeze
        self.census_word_drift = 0  # F-W1: censused word changed between passes
        self.trace = []            # (pc_slot_off, payload, answer_off, our_co) minted
        # --- counters, reset every pass ---
        self.reset_pass()

    # ------------------------------------------------------------ census
    def reset_pass(self):
        self.n_cells = 0
        self.n_holders = 0
        self.n_alias = 0
        self.minted = 0
        self.refuse_ambiguous = 0
        self.refuse_no_answer = 0
        self.refuse_unreached = 0
        self.refuse_arrayslot = 0
        self._reg = {}
        self._staged = {}
        self._reg_alias = {}
        self._staged_alias = {}
        del self.trace[:]

    def begin_asset(self, co_origin):
        """Console block-5 offset at which the asset about to be emitted starts."""
        self._co_origin = co_origin
        self._staged = {}
        self._staged_alias = {}

    def commit_asset(self, accepted):
        """Promote this asset's holder cells into the registry, or discard them
        if the body was rejected / replaced (a discarded body occupies no stream,
        so its offsets would be lies)."""
        if accepted:
            self._reg.update(self._staged)
            self._reg_alias.update(self._staged_alias)
        self._staged = {}
        self._staged_alias = {}
        self._co_origin = None

    def observe(self, owner, pc_base, words, co_base):
        """One XModel's materialHandles cell array.
        owner   : PC file offset of the owning XModel body (stable identity)
        pc_base : PC file offset of cell 0
        words   : the ns little-endian PC words
        co_base : offset of cell 0 WITHIN the console body being emitted
        """
        if owner not in self._owner_ord:
            self._owner_ord[owner] = len(self.owners)
            self.owners.append(owner)
        for i, w in enumerate(words):
            off = pc_base + i * 4
            pos = self.by_off.get(off)
            if pos is None:
                if self.frozen:
                    self.census_drift += 1
                    continue
                self.by_off[off] = len(self.cells)
                self.cells.append((off, owner, i, w))
            elif self.cells[pos][3] != w:
                self.census_word_drift += 1
            self.n_cells += 1
            if w in FOLLOW_VALS:
                self.n_holders += 1
                if self._co_origin is not None and co_base is not None:
                    self._staged[off] = self._co_origin + co_base + i * 4
            elif is_alias(w):
                self.n_alias += 1
                # (EW) FIX 35: stage OUR console b5 of the ALIAS cell itself,
                # by the same formula the holder uses. bind_cell mints through
                # the IN-ASSEMBLY encode frame, which diverges from the finished
                # zone's sim (measured: 201/201 offsets, median |d| 503,082), so
                # xmodel_mharr_simframe re-mints these cells post-assembly from
                # the identity recorded here. Staged/committed in lockstep with
                # the holders so a rejected body never contributes a lie.
                if self._co_origin is not None and co_base is not None:
                    self._staged_alias[off] = self._co_origin + co_base + i * 4

    # ------------------------------------------------------------ resolver
    def freeze_and_resolve(self, pc_arr, slack=None):
        """Run the chain over the frozen PC census. `pc_arr` = (assets_off_b5,
        n_assets); payloads that decode to a PC XAsset-array ENTRY SLOT are
        excluded -- those are the et7 class and are already handled correctly by
        `Omap.reloc`'s own pc_arr branch, which this module must not shadow."""
        self.frozen = True
        if slack is None:
            slack = self.slack
        cells = sorted(self.cells)                    # PC stream order
        N = len(cells)
        fo, fown, fidx = [], [], []
        F = []                                        # positions in `cells`
        for n, (off, owner, idx, w) in enumerate(cells):
            if w in FOLLOW_VALS:
                F.append(n)
                fo.append(off)
                fown.append(owner)
                fidx.append(idx)
        nF = len(F)
        by_own = collections.defaultdict(list)
        for k in range(nF):
            by_own[fown[k]].append(k)

        def sarr(kp, k):
            return (fown[kp] == fown[k]
                    and fo[k] - fo[kp] == 4 * (fidx[k] - fidx[kp]))

        first_alias, cells_of = {}, collections.Counter()
        for n, (off, owner, idx, w) in enumerate(cells):
            if is_alias(w):
                p = (w - 1) & 0x1FFFFFFF
                cells_of[p] += 1
                first_alias.setdefault(p, n)
        a0 = n_as = None
        if pc_arr is not None:
            a0, n_as = pc_arr
            a0 = (a0 + 7) & ~7
        P = []
        n_arrayslot = 0
        for p in sorted(first_alias):
            if a0 is not None and a0 <= p < a0 + n_as * 8 and (p - a0) % 8 == 4:
                n_arrayslot += 1
                continue
            P.append(p)
        M = len(P)
        NA = sum(cells_of[p] for p in P)
        st = dict(cells=N, candidates=nF, payloads=M, alias_cells=NA,
                  arrayslot_payloads=n_arrayslot, slack=slack,
                  neg_events=0, infeasible_at=None, proven=0, covered=0)
        self.resolve_stats = st
        if M == 0 or nF == 0:
            return st
        A = [bisect.bisect_left(F, first_alias[p]) - 1 for p in P]

        # ---- forward reachability (E0: booleans, never path counts) ----
        neg = 0
        fw = [[0] * nF for _ in range(M)]
        for k in range(max(A[0] + 1, 0)):
            fw[0][k] = 1
        if not any(fw[0]):
            st['infeasible_at'] = 0
            return st
        for j in range(1, M):
            d = P[j] - P[j - 1]
            pref = [0] * (nF + 1)
            for k in range(nF):
                pref[k + 1] = pref[k] + fw[j - 1][k]
            row = fw[j]
            prev = fw[j - 1]
            for k in range(1, min(A[j], nF - 1) + 1):
                lim = min(bisect.bisect_right(fo, fo[k] - d + slack) - 1, k - 1)
                tot = pref[lim + 1] if lim >= 0 else 0
                for kp in by_own[fown[k]]:
                    if kp >= k:
                        break
                    if not sarr(kp, k):
                        continue
                    if kp <= lim:
                        tot -= prev[kp]
                    if fo[k] - fo[kp] == d:
                        tot += prev[kp]
                if tot < 0:
                    neg += 1
                row[k] = 1 if tot > 0 else 0
            if not any(row):
                st['neg_events'] = neg
                st['infeasible_at'] = j
                return st

        # ---- backward reachability ----
        bw = [[0] * nF for _ in range(M)]
        for k in range(nF):
            bw[M - 1][k] = 1 if fw[M - 1][k] else 0
        for j in range(M - 2, -1, -1):
            d = P[j + 1] - P[j]
            suf = [0] * (nF + 2)
            nxt = bw[j + 1]
            for k in range(nF - 1, -1, -1):
                suf[k] = suf[k + 1] + (nxt[k] if k <= A[j + 1] else 0)
            row = bw[j]
            for k in range(nF):
                lo = max(bisect.bisect_left(fo, fo[k] + d - slack), k + 1)
                tot = suf[lo] if lo < nF else 0
                for kn in by_own[fown[k]]:
                    if kn <= k or kn > A[j + 1] or not sarr(k, kn):
                        continue
                    if kn >= lo:
                        tot -= nxt[kn]
                    if fo[kn] - fo[k] == d:
                        tot += nxt[kn]
                if tot < 0:
                    neg += 1
                row[k] = 1 if tot > 0 else 0
        st['neg_events'] = neg

        proven = covered = 0
        sizes = collections.Counter()
        for j in range(M):
            feas = [k for k in range(nF) if fw[j][k] and bw[j][k]]
            sizes[len(feas)] += 1
            self.feasible_size[P[j]] = len(feas)
            self.feasible_off[P[j]] = [fo[k] for k in feas]
            if len(feas) == 1:
                self.answers[P[j]] = fo[feas[0]]
                proven += 1
                covered += cells_of[P[j]]
        st['proven'] = proven
        st['covered'] = covered
        st['feasible_sizes'] = dict(sorted(sizes.items()))
        return st

    # ------------------------------------------------------------ bind
    def bind_cell(self, pc_slot_off, word, pc_arr=None):
        """The alias cell at `pc_slot_off` holds PC word `word`.
        Returns OUR console block-5 offset of the identified holder cell, or
        None to REFUSE (the caller then does exactly what it does today)."""
        if not is_alias(word):
            return None
        p = (word - 1) & 0x1FFFFFFF
        if pc_arr is None:
            pc_arr = self.pc_arr
        if pc_arr is not None:
            a0, n_as = pc_arr
            a0 = (a0 + 7) & ~7
            if a0 <= p < a0 + n_as * 8 and (p - a0) % 8 == 4:
                # et7 asset-array entry slot: `Omap.reloc`'s own branch already
                # keeps identity here (36/36 control). Never shadow it.
                self.refuse_arrayslot += 1
                return None
        tgt = self.answers.get(p)
        if tgt is None:
            if self.feasible_size.get(p, 0) > 1:
                self.refuse_ambiguous += 1
            else:
                self.refuse_no_answer += 1
            return None
        co = self._staged.get(tgt)
        if co is None:
            co = self._reg.get(tgt)
        if co is None:
            # answer known, but the holder cell has not been emitted yet in OUR
            # stream: binding it would need an address we do not have. REFUSE.
            self.refuse_unreached += 1
            return None
        self.minted += 1
        self.trace.append((pc_slot_off, p, tgt, co))
        return co

    # ------------------------------------------------------------ report
    def identity_of(self, pc_slot_off):
        """(owner ordinal, cell index) for a censused cell -- the ordinal form,
        for scoring against an external table without quoting any address."""
        pos = self.by_off.get(pc_slot_off)
        if pos is None:
            return None
        off, owner, idx, w = self.cells[pos]
        return (self._owner_ord[owner], idx)

    def summary(self):
        r = dict(self.resolve_stats)
        r.update(cells_seen=self.n_cells, holders=self.n_holders,
                 alias_cells=self.n_alias, minted=self.minted,
                 refuse_ambiguous=self.refuse_ambiguous,
                 refuse_no_answer=self.refuse_no_answer,
                 refuse_unreached=self.refuse_unreached,
                 refuse_arrayslot=self.refuse_arrayslot,
                 census_drift=self.census_drift,
                 census_word_drift=self.census_word_drift,
                 registry=len(self._reg))
        return r

    def print_summary(self, tag=''):
        s = self.summary()
        print('  I1 mharr binder %s: census cells %s holders %s aliases %s ; '
              'payloads %s proven %s ; MINTED %s ; refused amb %s / no-answer %s '
              '/ not-yet-emitted %s / array-slot %s'
              % (tag, s.get('cells'), s.get('candidates'), s.get('alias_cells'),
                 s.get('payloads'), s.get('proven'), s['minted'],
                 s['refuse_ambiguous'], s['refuse_no_answer'],
                 s['refuse_unreached'], s['refuse_arrayslot']))
        if s.get('infeasible_at') is not None:
            print('  I1 mharr binder %s: CHAIN INFEASIBLE at payload %s -- '
                  'NO answers minted (loud abort of the resolver, not a guess)'
                  % (tag, s['infeasible_at']))
        if s.get('neg_events'):
            print('  I1 mharr binder %s: DEFECT negative-tot events %d (E0 must '
                  'be 0)' % (tag, s['neg_events']))
        if s['census_drift'] or s['census_word_drift']:
            print('  I1 mharr binder %s: DEFECT census drift %d / word drift %d '
                  '(F-W1 FIRED -- the census is not an image of the emit)'
                  % (tag, s['census_drift'], s['census_word_drift']))
        if s['refuse_unreached']:
            print('  I1 mharr binder %s: NOTE %d answers name a holder cell not '
                  'yet emitted in our stream (F-W5) -- refused, not guessed'
                  % (tag, s['refuse_unreached']))
