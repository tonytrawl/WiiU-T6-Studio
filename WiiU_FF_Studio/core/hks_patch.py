"""core.hks_patch -- targeted edits to a HavokScript chunk, with the proofs that make them safe.

Everything here exists because an HKS chunk cannot be edited by hand. Instruction arrays are
4-aligned and the padding is implied by stream position rather than stored, so a byte splice
shifts every downstream proto off its alignment; the game reports the result as
"LUI: Out of Memory", which names neither the file nor the cause. So edits go
parse -> mutate the model -> re-emit, and each one carries a proof.

WHAT EACH PROOF IS FOR
----------------------
`insert_instructions`  inserting words renumbers instructions, so every branch that STRADDLES
                       the insertion point needs its sBx adjusted. It is not enough to adjust
                       and hope: the function decodes the word at each branch's OLD target in
                       the OLD code and at its NEW target in the NEW code and requires them to
                       be the same word. An off-by-one in the target convention cannot survive
                       that unless the neighbouring words happen to be identical.

`prove_product`        turns "I edited one function" into a checkable claim about the other N:
                       the product re-parses, re-emits byte-identically, tiles, and every proto
                       outside the declared edit set has an unchanged fingerprint.

`canary`               a deliberately broken variant of the SAME LENGTH. Booting one converts
                       "it booted" into "it booted AND a broken variant would not have", which
                       is the only thing that rules out a patch that did nothing at all.

`hks.require_one`      a pattern-located target must match exactly once. Measured reason: the
                       `startLocIcon` fetch that appears once in the PC build of
                       selectstartloczombie.lua appears TWICE in the Wii U build.

⛔⛔ BEFORE YOU TRIAGE A LUI ERROR, READ THIS
--------------------------------------------
HKS NAMES RUNTIME ERRORS OFF NEARBY CONSTANTS, so a LUI traceback points at the wrong function.
A reported site of `GameMapZombie.lua:15` was measured to be a red herring for a fault that was
actually in `GoToStartLoc`. Treat the file and line in a LUI error as A HINT ABOUT WHICH
CONSTANTS ARE NEARBY, never as the fault location. Chasing the named file directly cost the ZM
globe lane a day.

The corollary matters as much: "LUI: Out of Memory" is what a STRUCTURAL break in a chunk looks
like from the game side. It is not an allocation problem, and nothing in the message will tell
you which file broke. If you see it after editing a script, suspect the edit's alignment before
you suspect anything else -- and note that everything in this module is built so that failure
mode cannot be reached by accident.
"""
import struct

from . import paths  # noqa: F401
from . import hks as H
from . import hks_dis as D

require_one = H.require_one

#: sBx is 17 bits biased by SBX_BIAS, so this is the representable jump distance
SBX_MIN, SBX_MAX = -D.SBX_BIAS, 0x1FFFF - D.SBX_BIAS

DATA = next((o for o, n in D.MNEMONIC.items() if n == 'DATA'), None)


class PatchError(H.HksError):
    pass


class Proof(object):
    """The result of a proof. Falsy when it failed, so `if not proof: ...` reads correctly."""

    def __init__(self, name):
        self.name = name
        self.problems = []
        self.notes = {}

    def fail(self, msg):
        self.problems.append(msg)
        return self

    @property
    def ok(self):
        return not self.problems

    def __bool__(self):
        return self.ok

    __nonzero__ = __bool__

    def raise_if_failed(self):
        if not self.ok:
            raise PatchError('%s failed: %s' % (self.name, '; '.join(self.problems[:4])))
        return self

    def __repr__(self):
        return '<Proof %s %s%s>' % (self.name, 'OK' if self.ok else 'FAILED',
                                    '' if self.ok else ' ' + repr(self.problems[:3]))


# --------------------------------------------------------------------------- insertion

def _branch_indices(words):
    """[(i, sbx)] for every branch instruction, 0-based."""
    out = []
    for i, w in enumerate(words):
        op = (w >> D.OP_SHIFT) & D.OP_MASK
        if D.FORMAT.get(op) in ('sBx', 'iAsBx'):
            out.append((i, ((w >> 8) & 0x1FFFF) - D.SBX_BIAS))
    return out


def _target(i, sbx):
    """0-based target of a branch at 0-based index i. See `hks_dis.jump_target`."""
    return i + sbx + 1


def _reencode_sbx(word, sbx):
    if not (SBX_MIN <= sbx <= SBX_MAX):
        raise PatchError('jump offset %d does not fit in sBx (%d..%d)' % (sbx, SBX_MIN, SBX_MAX))
    return (word & ~(0x1FFFF << 8)) | (((sbx + D.SBX_BIAS) & 0x1FFFF) << 8)


def insert_instructions(blob, proto_index, at, words, allow_unsafe_site=False):
    """Insert `words` before 0-based instruction `at` of proto #`proto_index`.

    Returns (new_blob, Proof). The proof carries the branch-by-branch target comparison.

    Refuses two site classes outright, because both split an instruction from something the VM
    requires to follow it:
      * `at` landing on a DATA pseudo-word, which belongs to the instruction before it
      * `at` landing immediately after a test-mode opcode, whose next instruction must be a JMP
    """
    proof = Proof('insert %d word(s) at proto #%d:%d' % (len(words), proto_index, at))
    f = H.parse(blob)
    if f.trailing:
        # A tail means the chunk was extracted by asset SPAN rather than by RawFile length, so
        # the buffer carries slop that is not part of the script. Growing such a buffer and
        # writing it back would ship the slop too. Re-extract by the header length instead.
        raise PatchError('refusing to insert into a chunk with a %d-byte tail: it was extracted '
                         'by span, not by RawFile length' % f.trailing)
    pr = f.proto(proto_index)
    old = list(pr.code_words())
    k = len(words)
    if not (0 <= at <= len(old)):
        raise PatchError('insertion point %d outside 0..%d' % (at, len(old)))
    if k == 0:
        return blob, proof

    if not allow_unsafe_site:
        if at < len(old) and DATA is not None and \
                ((old[at] >> D.OP_SHIFT) & D.OP_MASK) == DATA:
            raise PatchError('refusing to insert at %d: that word is a DATA pseudo-word and '
                             'belongs to the instruction before it' % at)
        if at > 0 and D.TEST.get((old[at - 1] >> D.OP_SHIFT) & D.OP_MASK):
            raise PatchError('refusing to insert at %d: the previous instruction is a test, and '
                             'its next instruction must be the JMP' % at)

    new = old[:at] + list(words) + old[at:]

    def shift(idx):
        return idx + (k if idx >= at else 0)

    # ⚠ TWO PASSES, AND THE ORDER IS THE WHOLE POINT.
    # The proof is "every branch still lands on the same instruction". Comparing target words
    # against a half-re-based array cannot express that: if branch A targets branch B, then once
    # B has been re-based its word LEGITIMATELY differs, and A gets reported as broken -- while
    # a branch processed before its target is compared against the old word and passes. The
    # verdict would depend on iteration order, which is not a proof of anything.
    # So: prove the index mapping first, against the array BEFORE any re-basing, then re-base.
    placed = list(new)
    for i, sbx in _branch_indices(old):
        t = _target(i, sbx)
        nt = shift(t)
        old_word = old[t] if 0 <= t < len(old) else None
        new_word = placed[nt] if 0 <= nt < len(placed) else None
        if old_word != new_word:
            proof.fail('branch at %d (sBx %d): target %d -> %d is a different instruction '
                       '(0x%s vs 0x%s)'
                       % (i, sbx, t, nt,
                          'NONE' if old_word is None else '%08X' % old_word,
                          'NONE' if new_word is None else '%08X' % new_word))

    for i, sbx in _branch_indices(old):
        ni, nt = shift(i), shift(_target(i, sbx))
        nsbx = nt - ni - 1
        if nsbx != sbx:
            new[ni] = _reencode_sbx(new[ni], nsbx)
            proof.notes.setdefault('rebased', []).append((i, sbx, nsbx))
        # and the re-based branch must now resolve to the index we just proved
        if _target(ni, nsbx) != nt:
            proof.fail('branch at %d re-based to sBx %d resolves to %d, not %d'
                       % (i, nsbx, _target(ni, nsbx), nt))

    # any branch among the INSERTED words must also land somewhere real
    for j, w in enumerate(words):
        op = (w >> D.OP_SHIFT) & D.OP_MASK
        if D.FORMAT.get(op) in ('sBx', 'iAsBx'):
            t = _target(at + j, ((w >> 8) & 0x1FFFF) - D.SBX_BIAS)
            if not (0 <= t < len(new)):
                proof.fail('inserted branch at %d targets %d, outside 0..%d'
                           % (at + j, t, len(new) - 1))

    pr.set_code_words(new)
    out = f.serialize()

    # ⚠ Check the product AGAINST THE INPUT, not on its own. A walk that stops early and happens
    # to end at the buffer end is indistinguishable from a shorter valid chunk, so a standalone
    # check cannot catch a product that quietly lost protos. `prove_product` compares the proto
    # count and every fingerprint against `blob`, which can.
    prod = prove_product(blob, out, expect_changed={proto_index})
    for p in prod.problems:
        proof.fail(p)
    proof.notes.update(prod.notes)
    proof.notes['grew'] = len(out) - len(blob)
    # Never hand back a product whose proof failed: a caller that forgets to look at the second
    # element would ship it. Callers who want to inspect a failure catch PatchError.
    proof.raise_if_failed()
    return out, proof


# --------------------------------------------------------------------------- product proof

def prove_product(before, after, expect_changed=()):
    """Prove `after` is a well-formed product that changed only the protos it was meant to.

    `expect_changed` is the set of proto indices the edit was allowed to touch. Anything else
    changing is a failure -- that is the whole point, and it is what turns "I edited one
    function" into a claim about the other N.
    """
    proof = Proof('product self-proof')
    fb = H.parse(before) if not isinstance(before, H.HksFile) else before
    try:
        fa = H.parse(after)
    except Exception as ex:
        return proof.fail('product does not parse: %s: %s' % (type(ex).__name__, ex))

    # the product may carry exactly the tail the input had, and no other
    ok, probs = fa.check(allow_tail=bool(fb.trailing))
    if not ok:
        proof.fail('product does not tile / re-emit: %s' % probs[:3])
    if fa.trailing != fb.trailing:
        proof.fail('tail changed: %d -> %d bytes' % (fb.trailing, fa.trailing))
    try:
        n, sc = D.selfcheck(fa, strict=True)
        proof.notes['instructions'] = n
        if sc:
            proof.fail('product fails selfcheck: %s' % sc[:3])
    except D.UnknownOpcode as ex:
        proof.fail(str(ex))

    if len(fa.protos) != len(fb.protos):
        proof.fail('proto count changed %d -> %d' % (len(fb.protos), len(fa.protos)))
    else:
        want = set(expect_changed)
        fpb, fpa = fb.fingerprints(), fa.fingerprints()
        changed = {i for i in fpb if fpb[i] != fpa.get(i)}
        proof.notes['changed'] = sorted(changed)
        stray = sorted(changed - want)
        missing = sorted(want - changed)
        if stray:
            proof.fail('protos changed that should not have: %s' % stray[:8])
        if missing:
            proof.fail('protos declared edited but unchanged: %s' % missing[:8])
    return proof


# --------------------------------------------------------------------------- negative control

CANARY_KINDS = ('opcode', 'jump', 'const_index')


def canary(blob, kind='opcode'):
    """Return (bytes, description) for a deliberately broken variant of the SAME LENGTH.

    Same length matters: it keeps every downstream offset identical, so the only difference
    between the product and its canary is the corruption itself. A canary the game REJECTS while
    it accepts the product is what rules out a patch that quietly did nothing.

    All three corruptions are length-preserving by construction -- they rewrite one 32-bit
    instruction word in place. (Flipping a constant's TYPE TAG would change the constant's
    encoded size and therefore the file length, so it is deliberately not one of the options.)
    """
    if kind not in CANARY_KINDS:
        raise PatchError('unknown canary kind %r (want one of %r)' % (kind, CANARY_KINDS))
    f = H.parse(blob)
    for pr in f.protos:
        words = list(pr.code_words())
        if not words:
            continue
        if kind == 'opcode':
            # an opcode above the table's top: the field is 7 bits, the enum ends at 91
            top = max(D.MNEMONIC) if D.MNEMONIC else 91
            if top >= D.OP_MASK:
                continue
            i = 0
            words[i] = (words[i] & ~(D.OP_MASK << D.OP_SHIFT)) | (D.OP_MASK << D.OP_SHIFT)
            desc = 'proto #%d instr 0 opcode -> %d (above the %d-entry table)' % (
                pr.index, D.OP_MASK, top + 1)
        elif kind == 'jump':
            br = _branch_indices(words)
            if not br:
                continue
            i = br[0][0]
            words[i] = _reencode_sbx(words[i], SBX_MAX)
            desc = 'proto #%d instr %d jump retargeted past the end of the code array' % (
                pr.index, i)
        else:
            cand = [j for j, w in enumerate(words)
                    if D.FORMAT.get((w >> D.OP_SHIFT) & D.OP_MASK) == 'iABx'
                    and D.BMODE.get((w >> D.OP_SHIFT) & D.OP_MASK) == 'K']
            if not cand:
                continue
            i = cand[0]
            words[i] = (words[i] & ~(0x1FFFF << 8)) | (0x1FFFF << 8)
            desc = 'proto #%d instr %d constant index -> %d (>= %d constants)' % (
                pr.index, i, 0x1FFFF, len(pr.constants))
        pr.set_code_words(words)
        out = f.serialize()
        if len(out) != len(blob):
            raise PatchError('canary changed the length (%d -> %d); it must not'
                             % (len(blob), len(out)))
        return out, desc
    raise PatchError('no site in this chunk supports a %r canary' % kind)


def canary_is_rejected(blob, kind='opcode'):
    """Build a canary and confirm OUR OWN checks reject it. Returns (Proof, canary_bytes).

    A checker that has never been seen to fail is not a checker. This runs before any scoring.
    """
    proof = Proof('canary %r is rejected' % kind)
    bad, desc = canary(blob, kind)
    proof.notes['corruption'] = desc
    if len(bad) != len(blob):
        proof.fail('canary length differs')
    caught = []
    try:
        n, sc = D.selfcheck(H.parse(bad), strict=True)
        if sc:
            caught.append(sc[0])
    except (D.UnknownOpcode, H.HksError) as ex:
        caught.append('%s: %s' % (type(ex).__name__, str(ex)[:80]))
    if not caught:
        proof.fail('the canary passed every check -- the checks do not discriminate')
    proof.notes['caught_by'] = caught[:2]
    return proof, bad
