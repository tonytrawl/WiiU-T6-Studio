#!/usr/bin/env python3
"""I1 STAGE 1b -- carry the PC dedup IDENTITY through for NAME-STRING aliases.

Scope: the three name-string dedup classes that block `produce_container.py
zm_nuked` at the assemble fatal bar (6 words, all counted `unres:XModel`):

  * the world name   "maps/mp/<map>.d3dbsp"  -- ComWorld holds the single inline
    copy; GfxWorld.name, GameWorldMp.name, clipMap_t.name and the MapEnts.name
    embedded inside the clipMap body all b5-dedup-alias it        (4 words)
  * two GfxLightDef .name fields, aliasing ComWorld's primaryLights defName
    string run                                                     (2 words)

WHY THIS EXISTS
---------------
Identical root cause to `dedup_binder.py` (XModel.materialHandles): `Omap.reloc`
has the signature word-in / word-out, so the only thing it can do with a dedup
back-reference is arithmetic -- `b5 = self.pc_inv.stream(b5)` inverts a PC
RUNTIME address through the drifted PC runtime simulation.  MEASURED on
zm_nuked: the three payloads invert to positions 38 KB short of the truth,
landing inside unrelated XModel bodies on non-string binary, so `_pc_cstring`
returns None, the content re-sourcing path never fires, and each word ships the
BOOT_SAFE in-bounds mirror 0xA000CCD9 -- a valid-LOOKING pointer to the wrong
bytes.  Two hardware-proven boots were already lost to exactly this failure mode
(skybox -> mc/global_black; lightdefs -> garbage names).

THE FIX SHAPE is `dedup_binder`'s: resolve the identity on PC-side data BEFORE
any address arithmetic.  For a string the identity IS the string value, and both
endpoints are available blind -- the PC zone holds the string inline at its
single holder occurrence (the PC linker emits a string once and aliases every
later use), and our own emitted stream holds our copy of it.  No inverse is
constructed here and no PC RUNTIME address is ever used as an answer; every
answer is a PC FILE offset and a byte string (rule: identification-without-
resolution).  Applying an answer is a pure RE-SOURCE -- point at our own copy --
so it is SIZE-NEUTRAL and cannot disturb any frozen layout.

WHAT THIS IS NOT
----------------
Not a general string-alias resolver.  Payloads outside the two rules below get
NO answer and take exactly the path they take today.  In particular the
XAnimParts name class (I1 spec 6.2, 256 cells on zm_nuked) is untouched: those
aliases target strings this module has no holder rule for, and guessing one
would be the class of defect this module exists to remove.

Not the authority on the runtime FRAME.  This module answers WHICH STRING; the
console address is minted by `Omap._encode`, the same chokepoint every other
minted pointer in the zone uses.  For the world-name class `root_name_fix.py`
(FIX 3) still runs post-assembly against the dump-proven rt frame and remains
the authority there -- and it doubles as a free external check on this module's
mint (it reports a sweep whenever the two frames disagree).

THE LAW SET
-----------
R1  ComWorld.primaryLights defName run (the two GfxLightDef names)
    C1 the run is CLOSED and PROVEN by exact size arithmetic:
       16 + len(name)+1 + count*stride + sum(len(s)+1 for the FOLLOW defNames)
       == body length, with no slack.  (stride, defName offset) is DERIVED by
       requiring that identity to have a UNIQUE solution, not assumed.
    C2 the k-th FOLLOW element's string is the k-th string of the run (the
       loader appends them in element order) -- an ORDINAL identity, no address.
    C3 payload order == run stream order, and gaps match EXACTLY (one contiguous
       run has no interior alignment padding).  `dedup_binder` L3/L4.
    C4 a payload's target must be emitted before the payload's FIRST use --
       `dedup_binder` L2.
    C5 exactly ONE embedding survives C3+C4, else REFUSE.
    C6 CROSS-RULE (rule EB, overlap two lanes): the PC runtime->stream constant
       K implied by R1 must agree with the K implied INDEPENDENTLY by R2 to
       within one alignment pad.  Both strings live in the same ComWorld body,
       so the two K values are the same quantity measured two ways.  Measured on
       zm_nuked: 201,557 vs 201,558 -- the 1-byte difference is exactly the pad
       the odd-addressed name string forces before the 4-aligned light array.
       No K is ever used AS an answer; it is only ever used to falsify one.

R2  the world-name field class (the four world names)
    D1 members are body+0 of ComWorld / GfxWorld / GameWorldMp / clipMap_t.
    D2 exactly one member holds it INLINE (FOLLOW); the rest carry aliases.
    D3 the members carry exactly ONE distinct alias word between them -- so the
       linker itself proved they are all the same string.
    D4 the holder string occurs EXACTLY ONCE in the PC zone (the dedup law;
       the same uniqueness `root_name_fix._find_unique` relies on).
    D5 the string must look like this map's bsp path (contains the map name),
       an external check the zone cannot fabricate.
    Any of D2-D5 failing REFUSES the class outright.

A refusal costs nothing: the caller falls back to exactly today's `reloc` chain.
"""

import itertools
import re
import struct

FOLLOW_VALS = (0xFFFFFFFF, 0xFFFFFFFE)
BLOCK5_LO, BLOCK5_HI = 0xA0000001, 0xBFFFFFFF
B5_BASE = 64

# R2 members, in the order their bodies are searched for the inline holder.
WORLD_NAME_ROOTS = ('ComWorld', 'GfxWorld', 'GameWorldMp', 'clipMap_t')

# C6 tolerance, in bytes. One alignment pad ahead of a 4-aligned array is at
# most 3; 64 leaves room for a larger alignment class without admitting a
# coincidence at the 38 KB scale of the drift this module exists to bypass.
K_TOL = 64

# R1 derivation search space for the PC ComPrimaryLight element. The measured
# PC stride is 196 with defName as the last word (console is 168 --
# body_relayout.py:409); the search must nevertheless PRODUCE that, not assume
# it, and a non-unique solution refuses.
STRIDE_RANGE = range(64, 512, 4)

# The most recently built table. DIAGNOSTICS ONLY (same role as
# `dedup_binder.LAST`): the assembler and the container author can both abort
# after the final emit pass, and a post-mortem needs a handle that survives the
# raise. Nothing in the emit path reads this.
LAST = None


def is_alias(v):
    return BLOCK5_LO <= v <= BLOCK5_HI


def payload_of(v):
    return (v - 1) & 0x1FFFFFFF


def _u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]


def _printable(s):
    return len(s) >= 1 and all(0x20 <= c <= 0x7e for c in s)


def _split_run(tail):
    """`tail` must be exactly k NUL-terminated printable strings, nothing left
    over. Returns the list of strings, or None."""
    if not tail or tail[-1] != 0:
        return None
    parts = tail.split(b'\x00')
    if parts[-1] != b'':
        return None
    parts = parts[:-1]
    if not parts or not all(_printable(p) for p in parts):
        return None
    return parts


class StringIdentity(object):
    """PC-side payload -> string answer table. Built once, before any pass that
    can act on the answers; read-only thereafter."""

    def __init__(self, map_name, verbose=True):
        self.map_name = map_name
        self.verbose = verbose
        self.answers = {}        # payload (b5, PRE-inversion) -> string bytes
        self.provenance = {}     # payload -> (rule, pc_holder_file_off, K)
        self.refusals = []       # (rule, reason)
        self.notes = []
        self.stats = dict(r1_payloads=0, r2_payloads=0, resourced=0,
                          unfound=0, sites=0)
        self.trace = []          # (payload, our_b5) per mint, reset each pass
        self.minted_words = {}   # payload -> {word}: post-mortem site locator
        self._k_world = None

    def reset_pass(self):
        self.stats['resourced'] = 0
        self.stats['unfound'] = 0
        del self.trace[:]
        self.minted_words = {}

    # ------------------------------------------------------------------ build
    def build(self, PC, bodies):
        """`bodies` = walk_pc_bodies output rows (i, name, root, s, e, hp)."""
        spans = {}
        for (i, nm, root, s, e, hp) in bodies:
            if s is None or not e:
                continue
            spans.setdefault(root, []).append((i, s, e))
        self._rule_world_name(PC, spans)
        self._rule_comworld_lightdefs(PC, spans)
        if self.verbose:
            self.print_summary()
        return self

    # --- R2 ---------------------------------------------------------------
    def _rule_world_name(self, PC, spans):
        holders, aliases, bad = [], set(), []
        for root in WORLD_NAME_ROOTS:
            for (i, s, e) in spans.get(root, ()):
                w = _u32(PC, s)
                if w in FOLLOW_VALS:
                    if root != 'ComWorld':
                        # D2: only ComWorld's inline-name offset is a documented
                        # layout here (header 16: name/isInUse/count/lights).
                        # Refuse rather than guess another type's header size.
                        bad.append('%s[%d] holds the name INLINE but this '
                                   'module has no header rule for it' % (root, i))
                        continue
                    if _u32(PC, s + 12) not in FOLLOW_VALS:
                        bad.append('ComWorld[%d]+12 (primaryLights) is not '
                                   'FOLLOW -- layout not as modelled' % i)
                        continue
                    o = s + 16
                    z = PC.find(b'\x00', o, o + 128)
                    if z < 0 or not _printable(PC[o:z]):
                        bad.append('ComWorld[%d] inline name is not a printable '
                                   'C-string' % i)
                        continue
                    holders.append((o, bytes(PC[o:z])))
                elif is_alias(w):
                    aliases.add(w)
                else:
                    bad.append('%s[%d]+0 is neither FOLLOW nor a b5 alias '
                               '(0x%08X)' % (root, i, w))
        if bad:
            return self._refuse('R2', '; '.join(bad))
        if len(holders) != 1:
            return self._refuse('R2', 'D2: expected exactly 1 inline holder, '
                                      'found %d' % len(holders))
        if len(aliases) != 1:
            return self._refuse('R2', 'D3: the class carries %d distinct alias '
                                      'words %s -- cannot tell them apart'
                                % (len(aliases),
                                   sorted('0x%08X' % a for a in aliases)))
        off, s_bytes = holders[0]
        if self.map_name.encode() not in s_bytes or not s_bytes.startswith(b'maps/'):
            return self._refuse('R2', 'D5: holder string %r is not a bsp path '
                                      'for map %r' % (s_bytes, self.map_name))
        n_occ = len(re.findall(re.escape(s_bytes + b'\x00'), PC))
        if n_occ != 1:
            return self._refuse('R2', 'D4: holder string %r occurs %d times in '
                                      'the PC zone (dedup law says 1)'
                                % (s_bytes, n_occ))
        word = aliases.pop()
        p = payload_of(word)
        k = (off - B5_BASE) - p
        if k <= 0:
            return self._refuse('R2', 'implied K=%d is not positive' % k)
        self.answers[p] = s_bytes
        self.provenance[p] = ('R2', off, k)
        self._k_world = k
        self.stats['r2_payloads'] = 1
        self.notes.append('R2 world name: 0x%08X -> %r (holder @%d, K=%d)'
                          % (word, s_bytes, off, k))

    # --- R1 ---------------------------------------------------------------
    def _rule_comworld_lightdefs(self, PC, spans):
        cw = spans.get('ComWorld', ())
        if len(cw) != 1:
            return self._refuse('R1', 'expected exactly 1 ComWorld body, found '
                                      '%d' % len(cw))
        (_i, s, e) = cw[0]
        body = PC[s:e]
        if _u32(body, 0) not in FOLLOW_VALS or _u32(body, 12) not in FOLLOW_VALS:
            return self._refuse('R1', 'ComWorld name/primaryLights are not both '
                                      'FOLLOW -- layout not as modelled')
        count = _u32(body, 8)
        if not (1 <= count <= 4096):
            return self._refuse('R1', 'primaryLightCount %d out of range' % count)
        z = body.find(b'\x00', 16, 16 + 128)
        if z < 0:
            return self._refuse('R1', 'no inline ComWorld name string')
        arr_off = z + 1

        # C1: DERIVE (stride, defName offset) by requiring the closed-run
        # identity to hold exactly. A non-unique solution refuses.
        sols = []
        for stride in STRIDE_RANGE:
            arr_end = arr_off + count * stride
            if arr_end >= len(body):
                break
            run = _split_run(body[arr_end:])
            if run is None:
                continue
            # BYTE granularity, not 4 (instrument law 5 + the standing "scan
            # structs at BYTE granularity" rule): a 4-aligned-only search makes
            # an ALIGNMENT CLAIM about a field whose alignment is exactly what
            # is being derived. Widening it is also the shift test -- if a shifted
            # read produced a second self-consistent solution, C1 would refuse
            # rather than silently pick the aligned one.
            for d in range(0, stride):
                words = [_u32(body, arr_off + i * stride + d)
                         for i in range(count)]
                if any(w not in FOLLOW_VALS and w != 0 and not is_alias(w)
                       for w in words):
                    continue
                nf = sum(1 for w in words if w in FOLLOW_VALS)
                if nf != len(run) or nf == 0:
                    continue
                if not any(is_alias(w) for w in words):
                    continue
                sols.append((stride, d, arr_end, run, words))
        if len(sols) != 1:
            return self._refuse(
                'R1', 'C1: closed-run identity has %d solutions %s -- refusing '
                      'to pick one' % (len(sols),
                                       [(t[0], t[1]) for t in sols[:8]]))
        stride, dfn, arr_end, run, words = sols[0]

        # C2: k-th FOLLOW element <-> k-th run string (ordinal, no address).
        run_off, run_b5, k = arr_end, [], 0
        for t in run:
            run_b5.append(s + run_off - B5_BASE)
            run_off += len(t) + 1
        follow_elem = [i for i, w in enumerate(words) if w in FOLLOW_VALS]

        first_use, pays = {}, []
        for i, w in enumerate(words):
            if is_alias(w):
                p = payload_of(w)
                if p not in first_use:
                    first_use[p] = i
                    pays.append(p)
        pays.sort()
        m, n = len(pays), len(run)
        if m == 0:
            return self._refuse('R1', 'no alias defNames to resolve')
        if m > n or n > 64:
            return self._refuse('R1', 'C3: %d payloads vs %d run strings' % (m, n))

        # C3 + C4: order-preserving, EXACT-gap, precedence-respecting embeddings.
        emb = []
        for combo in itertools.combinations(range(n), m):
            if any(run_b5[combo[t]] - run_b5[combo[0]] != pays[t] - pays[0]
                   for t in range(m)):
                continue
            if any(follow_elem[combo[t]] >= first_use[pays[t]]
                   for t in range(m)):
                continue
            emb.append(combo)
        if len(emb) != 1:
            return self._refuse('R1', 'C5: %d embeddings survive the exact-gap '
                                      'and precedence laws -- refusing' % len(emb))
        combo = emb[0]

        # C6: cross-rule agreement on the PC runtime->stream constant.
        k_run = run_b5[combo[0]] - pays[0]
        if self._k_world is None:
            return self._refuse('R1', 'C6: R2 produced no independent K to '
                                      'cross-check against')
        if abs(k_run - self._k_world) > K_TOL:
            return self._refuse(
                'R1', 'C6: K disagreement -- R1 implies %d, R2 implies %d '
                      '(|delta| %d > %d). The gap embedding is a coincidence; '
                      'refusing both light-def names.'
                % (k_run, self._k_world, abs(k_run - self._k_world), K_TOL))

        for t in range(m):
            p, j = pays[t], combo[t]
            if p in self.answers:
                return self._refuse('R1', 'payload 0x%08X already answered by '
                                          '%s' % (p, self.provenance[p][0]))
            self.answers[p] = run[j]
            self.provenance[p] = ('R1', run_b5[j] + B5_BASE, k_run)
        self.stats['r1_payloads'] = m
        self.notes.append(
            'R1 ComWorld defName run: stride %d, defName +%d, %d strings, '
            '%d payloads, K=%d (R2 K=%d, delta %d)'
            % (stride, dfn, n, m, k_run, self._k_world,
               k_run - self._k_world))
        for t in range(m):
            self.notes.append('   0x%08X -> %r (run[%d], holder elem %d)'
                              % (pays[t] + 1 + 0xA0000000, run[combo[t]],
                                 combo[t], follow_elem[combo[t]]))

    def _refuse(self, rule, reason):
        self.refusals.append((rule, reason))
        return None

    # ------------------------------------------------------------------ apply
    def resource(self, payload, stream, encode=None):
        """Our-stream block-5 offset of OUR copy of the string `payload` names,
        or None to REFUSE (the caller then does exactly what it does today).
        `stream` is indexed in block-5 offsets (index 0 == b5 0).
        `encode` (optional, diagnostics): the caller's `Omap._encode`, used only
        to record the word actually minted so a post-mortem can find the sites
        in the finished zone. It never influences the answer."""
        tgt = self.answers.get(payload)
        if tgt is None or stream is None:
            return None
        hit = stream.find(b'\x00' + tgt + b'\x00')
        if hit < 0:
            hit = stream.find(tgt + b'\x00')
        else:
            hit += 1
        if hit < 0:
            self.stats['unfound'] += 1
            return None
        self.stats['resourced'] += 1
        self.trace.append((payload, hit))
        if encode is not None:
            self.minted_words.setdefault(payload, set()).add(encode(hit))
        return hit

    def verify(self, stream):
        """ACCEPTANCE GATE. For every site minted this pass, the target our word
        names must hold, in OUR OWN stream, a NUL-terminated copy of exactly the
        string the PC zone holds inline for that payload. Returns a list of
        failures (empty == pass). This gate is reachable in both directions: a
        mint at a stale offset, a target that is not NUL-terminated, or a
        content mismatch each produce an entry."""
        bad = []
        for (p, co) in self.trace:
            want = self.answers.get(p)
            if want is None:
                bad.append((p, co, 'no answer for a minted payload'))
                continue
            got = stream[co:co + len(want)]
            if got != want:
                bad.append((p, co, 'stream holds %r, expected %r'
                            % (bytes(got[:48]), want)))
            elif stream[co + len(want):co + len(want) + 1] != b'\x00':
                bad.append((p, co, 'target string is not NUL-terminated'))
        return bad

    # ----------------------------------------------------------------- report
    def summary(self):
        r = dict(self.stats)
        r.update(answers=len(self.answers), refusals=len(self.refusals))
        return r

    def print_summary(self, tag=''):
        print('  I1 string-identity %s: %d answers (R2 world-name %d, R1 '
              'lightdef-run %d), %d refusals'
              % (tag, len(self.answers), self.stats['r2_payloads'],
                 self.stats['r1_payloads'], len(self.refusals)))
        for n in self.notes:
            print('    %s' % n)
        for (rule, why) in self.refusals:
            print('    REFUSED %s: %s' % (rule, why))


def build(PC, bodies, map_name, verbose=True):
    global LAST
    LAST = StringIdentity(map_name, verbose=verbose).build(PC, bodies)
    return LAST
