"""core.hks_selftest -- gate battery for the HavokScript container model, decoder and patcher.

Run:  python -m core.hks_selftest

Every gate is written so it CAN fail, and the ones that matter carry a negative control, because
a checker that has never been seen to fail is not a checker.

  H1  the opcode table is a dense enum with every column populated
  H2  instruction encode/decode round-trips over the whole operand space
  H3  every chunk in the corpus parses, TILES EXACTLY, and re-emits byte-identically
  H4  every chunk decodes strictly -- no unknown opcodes -- and passes the structural checks
  H5  BE->LE->BE transcode is byte-identical
  H6  a constant edit re-emits, tiles, and changes EXACTLY the proto it was aimed at
  H7  an instruction insert re-bases every straddling branch onto the same target word
  H8  negative controls: three canaries, a byte splice, and a truncated chunk are all REJECTED
  H9  the identification oracle for SELF, reported as a measurement
  H10 the splice refusal raises
  H11 every constant type the loader accepts round-trips, and one it rejects is refused (H11r)
  H12 seen-to-fail for the size audit and for a structural corruption

⚠ WHAT H3 IS AND IS NOT. Region CONTIGUITY is a tautology -- the regions come from the same
cursor the parse advances, so they always tile. The discriminating parts are the size audit
inside `tiling_report()` (region lengths recomputed from the model), the byte-identical re-emit,
and the refusal of any tail. H12 is the seen-to-fail control for the first of those; without it
nothing here had ever exercised a size or a count, because every H8 canary rewrites one word.
"""
import os
import struct
import sys

from . import paths  # noqa: F401
from . import hks as H
from . import hks_dis as D
from . import hks_patch as P
from . import settings as _st

try:
    from . import _opcodes_hks as T
except Exception:
    T = None

MAX_CHUNKS = 60


class Battery(object):
    def __init__(self):
        self.rows = []

    def add(self, gate, status, detail=''):
        self.rows.append((status, gate, detail))

    def report(self):
        w = max((len(r[1]) for r in self.rows), default=4)
        for s, g, d in self.rows:
            print('  %-6s %-*s %s' % (s, w, g, d))
        f = sum(1 for r in self.rows if r[0] == 'FAIL')
        p = sum(1 for r in self.rows if r[0] == 'PASS')
        sk = sum(1 for r in self.rows if r[0] == 'SKIP')
        print()
        print('  %d passed, %d failed, %d skipped' % (p, f, sk))
        if sk:
            print('  (a skipped gate is NOT a pass -- its data was not on this machine)')
        return f


# ------------------------------------------------------------------ corpus discovery

def _ff_files(limit=14):
    """.ff files from the user's own search path plus the checkout, LUI-bearing ones first."""
    out = []
    roots = list(_st.search_dirs())
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots += [here, os.path.dirname(here)]
    for r in roots:
        if not os.path.isdir(r):
            continue
        base = r.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(r):
            if dirpath.rstrip(os.sep).count(os.sep) - base >= 3:
                dirnames[:] = []
            for fn in sorted(files):
                if fn.lower().endswith('.ff'):
                    p = os.path.join(dirpath, fn)
                    if p not in out:
                        out.append(p)
    # LUI scripts live in the patch zones; try those first so the battery does not spend its
    # budget on map zones that carry no HavokScript at all.
    out.sort(key=lambda p: (0 if 'patch' in os.path.basename(p).lower() else 1, p))
    return out[:limit]


def _rawfiles(z):
    """[(name, chunk_bytes)] using the RawFile header length, not an asset span.

    The header length is the boundary the loader itself uses, so a chunk extracted this way is
    exactly what the game reads -- which is what makes an exact-tiling claim meaningful.
    """
    out, o = [], 0
    while True:
        i = z.find(H.MAGIC, o)
        if i < 0:
            break
        j = i - 1
        if j >= 0 and z[j] == 0:
            j -= 1
        st = j
        while st > 0 and 32 <= z[st - 1] < 127:
            st -= 1
        name = (z[st:i - 1] if i and z[i - 1] == 0 else z[st:i]).decode('latin1', 'replace')
        hd = st - 12
        ln = struct.unpack_from('>I', z, hd + 4)[0] if hd >= 0 else -1
        if 0 < ln < 4_000_000 and i + ln <= len(z):
            out.append((name, bytes(z[i:i + ln])))
            o = i + ln
        else:
            o = i + 4
    return out


def _corpus(limit=MAX_CHUNKS):
    from . import ZoneSession
    chunks = []
    for p in _ff_files():
        if len(chunks) >= limit:
            break
        try:
            z = ZoneSession.open(p).zone
        except Exception:
            continue
        for name, b in _rawfiles(z):
            chunks.append((os.path.basename(p), name, b))
            if len(chunks) >= limit:
                break
    return chunks


# ------------------------------------------------------------------ gates

def _h1(b):
    if T is None:
        return b.add('H1', 'FAIL', 'core/_opcodes_hks.py did not import')
    ops = sorted(T.MNEMONIC)
    probs = []
    if ops != list(range(len(ops))):
        holes = sorted(set(range(max(ops) + 1)) - set(ops))
        probs.append('not dense: %d hole(s) %s' % (len(holes), holes[:12]))
    # ⚠ Presence is not enough: a column full of the right KEYS and the wrong VALUES passes a
    # coverage test and then mis-drives every mode-keyed check downstream. Test the domain too.
    domains = {'FORMAT': {'iABC', 'iABx', 'iAsBx', 'sBx'},
               'BMODE': {'R', 'K', 'RK', 'N', 'U', 'UK'},
               'CMODE': {'R', 'K', 'RK', 'N', 'U', 'UK'},
               'TEST': {0, 1}, 'USE_RA': {0, 1}}
    for col, dom in domains.items():
        tbl = getattr(T, col)
        missing = sorted(set(ops) - set(tbl))
        if missing:
            probs.append('%s missing %d entr(y/ies) %s' % (col, len(missing), missing[:6]))
        odd = sorted({v for v in tbl.values() if v not in dom})
        if odd:
            probs.append('%s has value(s) outside %s: %s' % (col, sorted(dom), odd[:6]))
    names = list(T.MNEMONIC.values())
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        probs.append('duplicate mnemonics %s (a name->opcode map would silently lose one)'
                     % dupes[:6])
    for op in sorted(T.ISBK):
        if op & 1 and T.MNEMONIC[op] != T.MNEMONIC[op - 1] + '_BK':
            probs.append('ISBK pair broken at %d: %s / %s'
                         % (op, T.MNEMONIC.get(op - 1), T.MNEMONIC[op]))
    b.add('H1', 'FAIL' if probs else 'PASS',
          '; '.join(probs) if probs else
          '%d opcodes, dense 0..%d, all columns populated, %d ISBK pairs'
          % (len(ops), ops[-1], len(T.ISBK) // 2))


def _h2(b):
    bad = []
    for op in (0, 8, 10, 28, 66, 76):
        for a in (0, 1, 255):
            for bb in (0, 7, 255):
                for c in (0, 0x100, 0x1FF):
                    w = D.encode(op, a, bb, c)
                    d = D.decode(w)
                    if d[:4] != (op, a, bb, c):
                        bad.append('ABC %r -> %r' % ((op, a, bb, c), d[:4]))
    for op in (25, 6, 27):
        for bx in (0, 1, 0x1FFFE, 0x1FFFF):
            d = D.decode(D.encode_bx(op, 3, bx))
            if (d[0], d[1], d[4]) != (op, 3, bx):
                bad.append('ABx %r -> %r' % ((op, 3, bx), d))
    for sbx in (-D.SBX_BIAS, -1, 0, 1, 0x1FFFF - D.SBX_BIAS):
        d = D.decode(D.encode_sbx(28, 0, sbx))
        if d[5] != sbx:
            bad.append('sBx %d -> %d' % (sbx, d[5]))
    b.add('H2', 'FAIL' if bad else 'PASS',
          '; '.join(bad[:3]) if bad else 'ABC/ABx/sBx round-trip over the operand extremes')


def _h3(b, corpus):
    if not corpus:
        return b.add('H3', 'SKIP', 'no HavokScript chunks on this machine')
    bad, tails, footers = [], 0, {}
    for ff, name, blob in corpus:
        try:
            f = H.parse(blob)
        except Exception as ex:
            bad.append('%s: parse %s: %s' % (name[-40:], type(ex).__name__, str(ex)[:60]))
            continue
        for k, v in f.footer_sizes.items():
            footers[k] = footers.get(k, 0) + v
        if f.trailing:
            tails += 1
        # allow_tail defaults False, so a chunk whose walk stopped early FAILS here instead of
        # having the deficit absorbed into a `tail` region and still tiling
        ok, probs = f.check()
        if not ok:
            bad.append('%s: %s' % (name[-40:], probs[0][:90]))
    b.add('H3', 'FAIL' if bad else 'PASS',
          '; '.join(bad[:2]) if bad else
          '%d chunk(s) parse, tile exactly and re-emit byte-identically, none with a tail; '
          'per-proto footers %s' % (len(corpus), footers))


def _h4(b, corpus):
    if not corpus:
        return b.add('H4', 'SKIP', 'no HavokScript chunks on this machine')
    bad, nins = [], 0
    for ff, name, blob in corpus:
        try:
            f = H.parse(blob)
            n, probs = D.selfcheck(f, strict=True)
            nins += n
            if probs:
                bad.append('%s: %s' % (name[-40:], probs[0][:90]))
        except Exception as ex:
            bad.append('%s: %s: %s' % (name[-40:], type(ex).__name__, str(ex)[:70]))
    b.add('H4', 'FAIL' if bad else 'PASS',
          '; '.join(bad[:2]) if bad else
          '%d instruction(s) decoded strictly, no unknown opcodes, all structural checks clean'
          % nins)


def _h5(b, corpus):
    if not corpus:
        return b.add('H5', 'SKIP', 'no HavokScript chunks on this machine')
    bad = 0
    first = None
    for ff, name, blob in corpus:
        try:
            other = H.transcode(blob, not H.parse(blob).little)
            back = H.transcode(other, H.parse(blob).little)
            if back != blob:
                bad += 1
                first = first or '%s: round-trip differs (%d vs %d B)' % (
                    name[-40:], len(back), len(blob))
        except Exception as ex:
            bad += 1
            first = first or '%s: %s: %s' % (name[-40:], type(ex).__name__, str(ex)[:60])
    b.add('H5', 'FAIL' if bad else 'PASS',
          first if bad else '%d chunk(s) survive BE<->LE<->BE byte-identically' % len(corpus))


def _pick(corpus, want_strings=True):
    for ff, name, blob in corpus:
        try:
            f = H.parse(blob)
        except Exception:
            continue
        if not want_strings or f.strings():
            return name, blob, f
    return None, None, None


def _h6(b, corpus):
    name, blob, f = _pick(corpus)
    if blob is None:
        return b.add('H6', 'SKIP', 'no chunk with string constants')
    # skip NULL strings (value None): they carry no bytes to lengthen
    hit = next(((pi, ci, c) for pi, ci, c in f.strings() if c.value), None)
    if hit is None:
        return b.add('H6', 'SKIP', 'no non-NULL string constant to grow')
    pi, ci, c = hit
    out = H.parse(blob).patch_constant(pi, ci, (c.value.rstrip(b'\x00') + b'_LONGER'))
    proof = P.prove_product(blob, out, expect_changed={pi})
    b.add('H6', 'PASS' if proof.ok else 'FAIL',
          ('%s: k[%d] of proto #%d grew, %d -> %d B, only proto #%d changed'
           % (name[-34:], ci, pi, len(blob), len(out), pi)) if proof.ok
          else '; '.join(proof.problems[:2]))


def _h7(b, corpus):
    """Insert a no-op-ish word and prove every straddling branch keeps its target."""
    target = None
    for ff, name, blob in corpus:
        try:
            f = H.parse(blob)
        except Exception:
            continue
        for pr in f.protos:
            words = pr.code_words()
            br = P._branch_indices(words)
            # want a proto where at least one branch STRADDLES the insertion point, or the gate
            # proves nothing: an insert with no straddling branch needs no re-basing at all.
            for at in range(1, len(words)):
                if D.TEST.get((words[at - 1] >> D.OP_SHIFT) & D.OP_MASK):
                    continue
                if P.DATA is not None and \
                        ((words[at] >> D.OP_SHIFT) & D.OP_MASK) == P.DATA:
                    continue
                straddle = [i for i, s in br if (i >= at) != (P._target(i, s) >= at)]
                if len(straddle) >= 2:
                    target = (name, blob, pr.index, at, len(straddle))
                    break
            if target:
                break
        if target:
            break
    if not target:
        return b.add('H7', 'SKIP', 'no proto with a branch straddling a legal insertion point')

    name, blob, pi, at, nstr = target
    nop = D.encode(next(o for o, n in D.MNEMONIC.items() if n == 'MOVE'), 0, 0, 0)
    try:
        out, proof = P.insert_instructions(blob, pi, at, [nop])
    except Exception as ex:
        return b.add('H7', 'FAIL', '%s: %s: %s' % (name[-34:], type(ex).__name__, str(ex)[:80]))
    prod = P.prove_product(blob, out, expect_changed={pi})
    ok = proof.ok and prod.ok
    b.add('H7', 'PASS' if ok else 'FAIL',
          ('%s: 1 word into proto #%d at %d, %d straddling branch(es) re-based, every branch '
           'still lands on the same word; +%d B'
           % (name[-30:], pi, at, nstr, len(out) - len(blob))) if ok
          else '; '.join((proof.problems + prod.problems)[:2]))


def _h8(b, corpus):
    """Negative controls. Each of these MUST be rejected; a pass here would void H3-H7."""
    name, blob, f = _pick(corpus, want_strings=False)
    if blob is None:
        return b.add('H8', 'SKIP', 'no chunk to corrupt')
    results, probs = [], []

    built = 0
    for kind in P.CANARY_KINDS:
        try:
            proof, bad = P.canary_is_rejected(blob, kind)
            built += 1
            results.append('%s:%s' % (kind, 'caught' if proof.ok else 'MISSED'))
            if not proof.ok:
                probs.append('canary %r was not rejected' % kind)
        except P.PatchError as ex:
            results.append('%s:n/a' % kind)
            del ex
    if not built:
        # ⚠ otherwise a chunk where NO canary can be constructed reports "all rejected" while
        # having rejected nothing -- the gate would be reporting its own inapplicability as a pass
        probs.append('no canary could be built for this chunk, so nothing was actually rejected')

    # a byte splice: drop 4 bytes from the middle of the first proto's constants
    pr = f.root
    if pr.constants:
        c = pr.constants[-1]
        spliced = blob[:c.off] + blob[c.off + 4:]
        caught = False
        try:
            g = H.parse(spliced)
            caught = not g.check()[0]
            if not caught:
                caught = bool(D.selfcheck(g, strict=False)[1])
        except Exception:
            caught = True
        results.append('splice:%s' % ('caught' if caught else 'MISSED'))
        if not caught:
            probs.append('a 4-byte splice was not rejected')
    else:
        results.append('splice:not-applicable')
        probs.append('the chosen chunk has no constants, so the splice control never ran')

    # a truncated chunk
    caught = False
    try:
        g = H.parse(blob[:len(blob) - 8])
        caught = not g.check()[0]
    except Exception:
        caught = True
    results.append('truncate:%s' % ('caught' if caught else 'MISSED'))
    if not caught:
        probs.append('an 8-byte truncation was not rejected')

    # a forged footer assertion must be refused rather than silently honoured
    caught = False
    try:
        H.HksFile(blob, footer_size=(12 if f.footer_size == 8 else 8))
    except H.HksError:
        caught = True
    results.append('wrong-footer-assert:%s' % ('caught' if caught else 'MISSED'))
    if not caught:
        probs.append('a wrong footer_size assertion was accepted')

    b.add('H8', 'FAIL' if probs else 'PASS',
          '; '.join(probs[:2]) if probs else '%s all rejected [%s]'
          % (name[-30:], ' '.join(results)))


def _h9(b, corpus):
    """Identification oracle: what does each candidate opcode's C operand actually resolve to?

    SELF's C names a method, so it must be a STRING CONSTANT. This is a MEASUREMENT, not a
    validity rule -- C is an RK operand, so a register there is legal and does occur.
    """
    if not corpus:
        return b.add('H9', 'SKIP', 'no HavokScript chunks on this machine')
    import collections
    prof = collections.defaultdict(collections.Counter)
    for ff, name, blob in corpus:
        try:
            f = H.parse(blob)
        except Exception:
            continue
        for pr in f.protos:
            for x in D.instructions(f, pr, strict=False):
                if D.CMODE.get(x.op) != 'RK':
                    continue
                if x.c & D.RK_BIT:
                    k = x.c & 0xFF
                    prof[x.op][pr.constants[k].type_name
                               if k < len(pr.constants) else 'out-of-range'] += 1
                else:
                    prof[x.op]['register'] += 1

    def pct(op, key):
        tot = sum(prof[op].values())
        return (100.0 * prof[op][key] / tot) if tot else 0.0

    self_op = next((o for o, n in D.MNEMONIC.items() if n == 'SELF'), None)
    gts = next((o for o, n in D.MNEMONIC.items() if n == 'GETTABLE_S'), None)
    n_self = sum(prof[self_op].values()) if self_op is not None else 0
    n_gts = sum(prof[gts].values()) if gts is not None else 0
    # ⚠ BOTH populations must be non-empty. "GETTABLE_S is a string 0% of the time" is vacuously
    # true when there are no GETTABLE_S instructions at all, and a contrast against an empty set
    # discriminates nothing -- the gate would pass hardest on a corpus that says nothing.
    if n_self < 100 or n_gts < 20:
        return b.add('H9', 'SKIP', 'too few samples to discriminate (SELF %d, GETTABLE_S %d)'
                     % (n_self, n_gts))
    detail = ('SELF(op %d) C is a string constant in %.2f%% of %d; GETTABLE_S(op %d) in %.2f%% '
              'of %d' % (self_op, pct(self_op, 'string'), n_self,
                         gts, pct(gts, 'string'), n_gts))
    # the identification only holds if the two are far apart; if they were not, the opcode
    # assignment would not be readable off the corpus at all.
    ok = pct(self_op, 'string') > 90.0 and pct(gts, 'string') < 10.0
    b.add('H9', 'PASS' if ok else 'FAIL', detail)


def _h11(b, corpus):
    """Every constant type the loader accepts must survive a round-trip.

    ⚠ THIS GATE EXISTS BECAUSE THE CORPUS CANNOT PROVE IT. Across 761 console chunks only types
    0/1/3/4 ever appear, so lightuserdata (2), ui64 (11) and the NULL string were carried on
    assumption -- and two of those assumptions were wrong: there is no type 13, and a
    zero-length string is NULL with no bytes following rather than an empty string. A fix with
    no coverage is not a fix, so the constants are planted here and read back.
    """
    name, blob, f = _pick(corpus, want_strings=False)
    if blob is None:
        return b.add('H11', 'SKIP', 'no chunk to plant constants in')
    pr = f.root
    planted = [
        H.Constant(H.T_NIL, None, 0, 0),
        H.Constant(H.T_BOOL, True, 0, 0),
        H.Constant(H.T_LIGHTUSERDATA, 0xDEADBEEF, 0, 0),
        H.Constant(H.T_NUMBER, 0.5, 0, 0),
        H.Constant(H.T_STRING, b'planted\x00', 0, 0),
        H.Constant(H.T_STRING, None, 0, 0),            # the NULL string
        H.Constant(H.T_UI64, 0x0123456789ABCDEF, 0, 0),
    ]
    base = len(pr.constants)
    pr.constants.extend(planted)
    try:
        out = f.serialize()
        g = H.parse(out)
    except Exception as ex:
        return b.add('H11', 'FAIL', 'planting failed: %s: %s' % (type(ex).__name__, str(ex)[:90]))

    # ⚠ BREAK THE LOOP. Planting with Constant.encode and reading back with the parser tests
    # only that the two are inverses -- they would agree just as happily on a wrong layout. So
    # the emitted BYTES are compared against a literal expectation written from the loader's
    # field widths, independently of the encoder that produced them.
    e = '<' if f.little else '>'
    expect = (b'\x00'                                                   # nil: tag only
              + b'\x01\x01'                                             # bool: tag + 1 byte
              + b'\x02' + struct.pack(e + 'I', 0xDEADBEEF)              # lightuserdata: 4 B
              + b'\x03' + struct.pack(e + 'f', 0.5)                     # number: 4 B float
              + b'\x04' + struct.pack(e + 'I', 8) + b'planted\x00'      # string: len + bytes
              + b'\x04' + struct.pack(e + 'I', 0)                       # NULL string: len 0only
              + b'\x0b' + struct.pack(e + 'Q', 0x0123456789ABCDEF))     # ui64: 8 B
    first = g.root.constants[base]
    actual = out[first.off:first.off + len(expect)]

    ok, probs = g.check()
    got = g.root.constants[base:]
    bad = list(probs)
    if actual != expect:
        bad.append('planted bytes differ from the layout expectation at +%d'
                   % next((i for i in range(min(len(actual), len(expect)))
                           if actual[i] != expect[i]), min(len(actual), len(expect))))
    if len(got) != len(planted):
        bad.append('read back %d of %d planted constants' % (len(got), len(planted)))
    else:
        for want, have in zip(planted, got):
            if want.type != have.type or want.value != have.value:
                bad.append('%s %r came back as %s %r'
                           % (want.type_name, want.value, have.type_name, have.value))
    # the NULL string in particular must not gain a terminator byte
    nulls = [c for c in got if c.type == H.T_STRING and c.value is None]
    if len(nulls) != 1:
        bad.append('the NULL string did not survive as NULL')
    b.add('H11', 'FAIL' if bad else 'PASS',
          '; '.join(bad[:2]) if bad else
          'types %s round-trip, NULL string stays NULL, product tiles and re-emits'
          % sorted({c.type for c in planted}))

    # And a type the loader rejects must be refused. Both halves are exercised separately,
    # because "it raised" is not evidence about WHICH side raised -- and only HksError counts:
    # a TypeError from a mistake in this gate would otherwise read as a pass.
    bad13 = H.Constant(13, 0, 0, 0)
    enc_refused = parse_refused = False
    try:
        bad13.encode('<')
    except H.HksError:
        enc_refused = True
    # hand the parser a real type-13 constant by writing the tag directly, so the parse side is
    # tested even though encode refuses to produce one
    forged = bytearray(out)
    k0 = g.root.constants[base]                    # the planted nil, 1 byte on the wire
    forged[k0.off] = 13
    try:
        H.parse(bytes(forged))
    except H.HksError:
        parse_refused = True
    if enc_refused and parse_refused:
        b.add('H11r', 'PASS', 'type 13 is refused by encode AND by parse')
    else:
        b.add('H11r', 'FAIL', 'type 13 accepted by %s'
              % (' and '.join(x for x, r in (('encode', enc_refused), ('parse', parse_refused))
                              if not r)))


def _h12(b, corpus):
    """Seen-to-fail for the SIZE AUDIT and for a structural corruption.

    ⛔ These two exist because every other negative control in H8 rewrites one instruction WORD,
    which never changes a size or a count -- so nothing had ever exercised the half of
    `tiling_report()` that can actually fail, nor the proto-count comparison. A checker that has
    not been seen to fail is not a checker.
    """
    name, blob, f = _pick(corpus, want_strings=True)
    if blob is None:
        return b.add('H12', 'SKIP', 'no chunk with string constants')
    probs = []

    # (a) the size audit: lengthen a constant IN THE MODEL without re-emitting, so the walked
    #     region no longer matches what the model says is there
    g = H.parse(blob)
    if g.tiling_report()[0] is not True:
        probs.append('the untouched chunk does not tile, so this control proves nothing')
    hit = next(((pi, ci) for pi, ci, c in g.strings() if c.value), None)
    if hit is None:
        return b.add('H12', 'SKIP', 'no non-NULL string constant')
    pr = g.proto(hit[0])
    pr.constants[hit[1]].value = pr.constants[hit[1]].value.rstrip(b'\x00') + b'_MUCH_LONGER\x00'
    if g.tiling_report()[0]:
        probs.append('the size audit did not notice a constant that no longer fits its region')

    # (b) a structural corruption: drop a proto's children. Caught only by comparing against
    #     the input -- a standalone check on the product cannot tell it from a shorter chunk.
    h = H.parse(blob)
    root = h.root
    if not root.subs:
        return b.add('H12', 'SKIP', 'root proto has no sub-protos to drop')
    dropped = len(root.subs)
    root.subs = []
    shorter = h.serialize()
    standalone = H.parse(shorter).check()[0]
    caught = not P.prove_product(blob, shorter, expect_changed={0}).ok
    if not caught:
        probs.append('dropping %d sub-proto(s) was not caught by prove_product' % dropped)

    b.add('H12', 'FAIL' if probs else 'PASS',
          '; '.join(probs[:2]) if probs else
          'size audit catches a model/region mismatch; dropping %d sub-proto(s) passes a '
          'standalone check (%s) but prove_product catches it' % (dropped, standalone))


def _h10(b):
    try:
        H.refuse_splice()
    except H.SpliceRefused as ex:
        return b.add('H10', 'PASS', 'refuse_splice raises SpliceRefused (%s...)' % str(ex)[:44])
    except Exception as ex:
        return b.add('H10', 'FAIL', 'raised %s, not SpliceRefused' % type(ex).__name__)
    b.add('H10', 'FAIL', 'refuse_splice returned instead of raising')


def run():
    b = Battery()
    _h1(b)
    _h2(b)
    corpus = _corpus()
    _h3(b, corpus)
    _h4(b, corpus)
    _h5(b, corpus)
    _h6(b, corpus)
    _h7(b, corpus)
    _h8(b, corpus)
    _h9(b, corpus)
    _h10(b)
    _h11(b, corpus)
    _h12(b, corpus)
    return b.report()


if __name__ == '__main__':
    print('HavokScript gate battery (container, decoder, patcher)')
    sys.exit(1 if run() else 0)
