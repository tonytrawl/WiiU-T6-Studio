"""core.hks_dis -- HavokScript instruction decoder and disassembler.

THE INSTRUCTION FORMAT
----------------------
HKS reorders both the opcode numbering and the operand fields relative to Lua 5.1, so every
field below was solved against decoded listings and then checked instruction by instruction.

    31        25 24      17 16            8 7        0
    +-----------+----------+---------------+----------+
    |    OP     |    B     |       C       |    A     |
    +-----------+----------+---------------+----------+
       7 bits      8 bits       9 bits        8 bits

    OP  = bits 31-25   (7 bits)   -- NOT 6. See below.
    A   = bits 7-0     (8 bits)   -- the LOW byte, not the high one
    B   = bits 24-17   (8 bits)
    C   = bits 16-8    (9 bits)   -- RK: bit 0x100 set => constant index (C & 0xFF)
    Bx  = bits 24-8    (17 bits)  -- iABx forms (LOADK, GETGLOBAL, ...)
    sBx = Bx - 65535              -- iAsBx forms (JMP, FORPREP, FORLOOP)

⚠ THE OPCODE FIELD IS 7 BITS, NOT 6. A 6-bit read looks plausible (32 distinct values, max 38,
which matches Lua 5.1's 38 opcodes) and it is WRONG. The tell: aligning against decoded listings
produced conflicts on exactly `LT`/`LT_BK`, `FORPREP`/`FORLOOP` and `MOVE`/`GETGLOBAL` --
adjacent opcode numbers that differ only in operand mode. HKS assigns mode variants to
consecutive opcodes, so folding bit 25 into the operand merges each pair. With 7 bits: 0
conflicts.

⚠ THE sBx BIAS IS 65535, NOT Lua 5.1's 131071. Verified both directions:
    FORLOOP 0x7EFFFD04 -> Bx 65533 -> -2
    FORPREP 0x7D000004 -> Bx 65536 -> +1
  A bias of 65535 with target `pc+sBx+1` and a bias of 65536 with target `pc+sBx+2` are THE
  SAME FUNCTION. This file uses the first pair. Do not "fix" one half without the other.

⚠ B IS 8 BITS BUT SOMETIMES NEEDS 9. Opcodes whose B is an RK operand come in even/odd pairs and
the low opcode bit carries B's 9th bit -- so in an odd `_BK` opcode, B is a constant index, and
in its even partner B is a register. `ISBK` names the pairs; `rk_b()` applies the rule.

AN UNKNOWN OPCODE IS A FAILURE, NOT A LABEL
-------------------------------------------
The previous opcode table was assembled from observed instructions and had 22 gaps below 76 and
nothing above it, while the real enum runs to 91. A gap is not inert: rendering it as `OP_57` and
carrying on means a disassembly, a gate, or a codegen silently proceeds on an instruction it
cannot model. `instructions()` therefore RAISES on an unrecognised opcode unless a caller opts
out explicitly with `strict=False` (which the human-facing listing does, so a broken file can
still be looked at, and marks every such word).
"""
import struct

from . import paths  # noqa: F401
from . import hks as H

try:
    from ._opcodes_hks import MNEMONIC, FORMAT, BMODE, CMODE, TEST, USE_RA, ISBK
except Exception:                                    # table not generated yet
    MNEMONIC, FORMAT, BMODE, CMODE, TEST, USE_RA, ISBK = {}, {}, {}, {}, {}, {}, frozenset()

OP_SHIFT, OP_MASK = 25, 0x7F
SBX_BIAS = 65535
RK_BIT = 0x100

#: the only opcode whose Bx indexes the sub-proto array rather than the constant table
CLOSURE = next((op for op, n in MNEMONIC.items() if n == 'CLOSURE'), None)
JMP = next((op for op, n in MNEMONIC.items() if n == 'JMP'), None)
RETURN = next((op for op, n in MNEMONIC.items() if n == 'RETURN'), None)


class UnknownOpcode(H.HksError):
    def __init__(self, op, where=''):
        self.op = op
        H.HksError.__init__(
            self, 'opcode %d is not in the table (0..%d)%s'
                  % (op, max(MNEMONIC) if MNEMONIC else -1, (' at %s' % where) if where else ''))


def decode(w):
    """Split one instruction word into (op, A, B, C, Bx, sBx)."""
    return (
        (w >> OP_SHIFT) & OP_MASK,
        w & 0xFF,                    # A
        (w >> 17) & 0xFF,            # B
        (w >> 8) & 0x1FF,            # C
        (w >> 8) & 0x1FFFF,          # Bx
        ((w >> 8) & 0x1FFFF) - SBX_BIAS,   # sBx
    )


class OperandOutOfRange(H.HksError):
    """An operand does not fit its field. Masking it would encode a DIFFERENT instruction."""


def _fit(name, value, hi):
    # ⛔ Do not mask. `c & 0x1FF` on constant index 300 yields 44 -- a perfectly valid-looking
    # instruction that reads the wrong constant, in a chunk that passes every structural check
    # because 44 is in range. A proto with more than 256 constants and an RK operand is ordinary,
    # so this is reachable from real source, not a theoretical concern.
    if not (0 <= value <= hi):
        raise OperandOutOfRange('%s = %d does not fit 0..%d' % (name, value, hi))
    return value


def encode(op, a=0, b=0, c=0):
    return ((_fit('op', op, OP_MASK) << OP_SHIFT) | (_fit('B', b, 0xFF) << 17)
            | (_fit('C', c, 0x1FF) << 8) | _fit('A', a, 0xFF))


def encode_bx(op, a, bx):
    return ((_fit('op', op, OP_MASK) << OP_SHIFT) | (_fit('Bx', bx, 0x1FFFF) << 8)
            | _fit('A', a, 0xFF))


def encode_sbx(op, a, sbx):
    if not (-SBX_BIAS <= sbx <= 0x1FFFF - SBX_BIAS):
        raise OperandOutOfRange('sBx = %d does not fit %d..%d'
                                % (sbx, -SBX_BIAS, 0x1FFFF - SBX_BIAS))
    return encode_bx(op, a, sbx + SBX_BIAS)


def rk(c):
    """Decode an RK C operand: a constant index comes back as -(k+1), matching hksc's listing."""
    return -((c & 0xFF) + 1) if (c & RK_BIT) else c


def rk_b(op, b):
    """Decode a B operand, honouring the opcode's low bit as B's 9th (RK) bit."""
    if op in ISBK and (op & 1):
        return -(b + 1)
    return b


def const_index(operand):
    """The constant index behind a negative operand from `rk`/`rk_b`, or None."""
    return (-operand - 1) if isinstance(operand, int) and operand < 0 else None


def jump_target(ins):
    """0-based index of the instruction a branch transfers to.

    Pinned with a structural oracle rather than by reasoning about `pc`: FORPREP must land
    exactly on its matching FORLOOP. Across patch_ui_zm + patch_mp, `i + sBx + 1` hits a FORLOOP
    293 times out of 293; `i + sBx` hits 6 (2%, coincidence). `ins.idx` is 1-based, so
    `idx + sBx` already IS `i + sBx + 1`.

    ⚠ Bias and target are ONE convention. SBX_BIAS 65535 with `+1` here and SBX_BIAS 65536 with
    `+2` are the same function; changing either alone breaks both.
    """
    return ins.idx + ins.sbx


class Instr(object):
    __slots__ = ('idx', 'word', 'op', 'a', 'b', 'c', 'bx', 'sbx')

    def __init__(self, idx, word):
        self.idx = idx
        self.word = word
        self.op, self.a, self.b, self.c, self.bx, self.sbx = decode(word)

    @property
    def known(self):
        return self.op in MNEMONIC

    @property
    def name(self):
        return MNEMONIC.get(self.op, 'OP_%d' % self.op)

    def operands(self):
        """Operand tuple, following the mode table rather than the format alone.

        A constant index comes back NEGATIVE, as -(k+1), so `const_index()` can recover it and
        the listing can resolve it. Three different things put a constant there and they must all
        be handled or the operand renders as a register:
          * mode 'K'  -- a plain constant index (GETFIELD's C, SETFIELD's B, LOADK's Bx)
          * mode 'RK' -- constant only when the RK bit is set (C's 0x100; B's comes from the
                         opcode's low bit, see `rk_b`)
        """
        fmt = FORMAT.get(self.op)
        if fmt == 'iABx':
            return (self.a, -(self.bx + 1)) if BMODE.get(self.op) == 'K' else (self.a, self.bx)
        if fmt == 'iAsBx':
            return (self.a, self.sbx)
        if fmt == 'sBx':
            return (self.sbx,)
        bm, cm = BMODE.get(self.op), CMODE.get(self.op)
        b = -(self.b + 1) if bm == 'K' else rk_b(self.op, self.b)
        c = -((self.c & 0xFF) + 1) if cm == 'K' else (rk(self.c) if cm == 'RK' else self.c)
        return (self.a, b, c)

    def __repr__(self):
        return '%4d  %-22s %s' % (self.idx, self.name,
                                  ' '.join(str(o) for o in self.operands()))


def instructions(f, proto, strict=True):
    """Decode one proto's instruction list.

    `strict` (the default) raises `UnknownOpcode` rather than handing back a word nothing in the
    toolchain can reason about.
    """
    out = []
    for k, w in enumerate(proto.code_words()):
        ins = Instr(k + 1, w)
        if strict and MNEMONIC and not ins.known:
            raise UnknownOpcode(ins.op, 'proto #%d instruction %d' % (proto.index, k + 1))
        out.append(ins)
    return out


def require_table():
    """Refuse to answer opcode questions when the table failed to load.

    ⚠ The import above degrades to empty dicts so the GUI can still open a file and hex-dump it.
    But an EMPTY table makes `x.op not in MNEMONIC` false for nothing and `strict` unable to
    fire, so every opcode check would silently pass. Anything whose job is to FAIL on a bad
    opcode calls this first.
    """
    if not MNEMONIC:
        raise H.HksError('the opcode table is empty -- core/_opcodes_hks.py did not import, so '
                         'no opcode check can fail and none of them mean anything')


def assert_known(blob_or_file):
    """Raise on the first unrecognised opcode anywhere in a chunk. Returns the instruction count.

    This is the entry point for gates and for the compiler gauntlet: they must FAIL on an
    opcode outside the table instead of passing it through.
    """
    require_table()
    f = blob_or_file if isinstance(blob_or_file, H.HksFile) else H.parse(blob_or_file)
    n = 0
    for pr in f.protos:
        n += len(instructions(f, pr, strict=True))
    return n


def listing(blob, max_protos=40):
    """Human-readable disassembly of a whole HKS chunk.

    Deliberately non-strict: an unknown opcode is MARKED, not raised, because the reason to open
    a broken file is to look at it. Every automated path uses `strict=True`.
    """
    f = blob if isinstance(blob, H.HksFile) else H.parse(blob)
    lines = ['; HavokScript %s-endian, %d proto(s)'
             % ('little' if f.e == '<' else 'big', len(f.protos))]
    for i, pr in enumerate(f.protos):
        if i >= max_protos:
            lines.append('; ... %d more protos' % (len(f.protos) - i))
            break
        lines.append('')
        lines.append('proto %d: %d params, %d registers, %d instructions, %d constants'
                     % (i, pr.params, pr.registers, pr.n_instr, len(pr.constants)))
        for ins in instructions(f, pr, strict=False):
            txt = repr(ins)
            if not ins.known:
                txt += '   ; !! UNKNOWN OPCODE'
            else:
                for o in ins.operands():
                    k = const_index(o)
                    if k is not None and k < len(pr.constants):
                        txt += '   ; %r' % (pr.constants[k].value,)
                        break
            lines.append('    ' + txt)
    return '\n'.join(lines)


def selfcheck(blob, strict=False):
    """Structural invariants that hold for any well-formed chunk.

    Cheap, oracle-free validation, usable on the console corpus where the reference compiler
    cannot be run because the chunks are big-endian. Returns (n_instructions, [problems]).

    Every check below is keyed off the operand MODE, not the format:
      * `iABx` alone does not mean "Bx is a constant index" -- CLOSURE's Bx is a proto index and
        DATA's is opaque VM data. Only `BMODE == 'K'` means constant, and checking the other two
        against the constant table is how a correct file gets reported as broken.
    """
    if strict:
        require_table()
    f = blob if isinstance(blob, H.HksFile) else H.parse(blob)
    problems = []
    n_ins = 0
    for i, pr in enumerate(f.protos):
        ins = instructions(f, pr, strict=strict)
        n_ins += len(ins)
        if not ins:
            continue
        if RETURN is not None and ins[-1].op != RETURN:
            problems.append('proto %d does not end in RETURN (ends %s)' % (i, ins[-1].name))
        nk, nsub = len(pr.constants), len(pr.subs)
        for x in ins:
            if MNEMONIC and not x.known:
                problems.append('proto %d insn %d: unknown opcode %d' % (i, x.idx, x.op))
                continue
            if TEST.get(x.op) and JMP is not None:
                # "All `skips' (pc++) assume that next instruction is a jump" -- and it holds
                # without exception: 7,795 of 7,795 test-mode opcodes in the shipped LUI corpus.
                if x.idx >= len(ins) or ins[x.idx].op != JMP:
                    problems.append('proto %d insn %d %s: not followed by JMP (%s)'
                                    % (i, x.idx, x.name,
                                       ins[x.idx].name if x.idx < len(ins) else 'end of code'))
            fmt, bm, cm = FORMAT.get(x.op), BMODE.get(x.op), CMODE.get(x.op)
            if fmt in ('iAsBx', 'sBx'):
                tgt = jump_target(x)
                if not (0 <= tgt < len(ins)):
                    problems.append('proto %d insn %d: jump to %d is outside 0..%d'
                                    % (i, x.idx, tgt, len(ins) - 1))
            elif fmt == 'iABx':
                if bm == 'K' and x.bx >= nk:
                    problems.append('proto %d insn %d %s: constant %d >= %d'
                                    % (i, x.idx, x.name, x.bx, nk))
                elif x.op == CLOSURE and x.bx >= nsub:
                    problems.append('proto %d insn %d CLOSURE: sub-proto %d >= %d'
                                    % (i, x.idx, x.bx, nsub))
            else:
                if cm == 'K' and (x.c & 0xFF) >= nk:
                    problems.append('proto %d insn %d %s: C constant %d >= %d'
                                    % (i, x.idx, x.name, x.c & 0xFF, nk))
                elif cm == 'RK' and (x.c & RK_BIT) and (x.c & 0xFF) >= nk:
                    problems.append('proto %d insn %d %s: C constant %d >= %d'
                                    % (i, x.idx, x.name, x.c & 0xFF, nk))
                if bm == 'K' and x.b >= nk:
                    problems.append('proto %d insn %d %s: B constant %d >= %d'
                                    % (i, x.idx, x.name, x.b, nk))
                elif bm == 'RK' and x.op in ISBK and (x.op & 1) and x.b >= nk:
                    problems.append('proto %d insn %d %s: B constant %d >= %d'
                                    % (i, x.idx, x.name, x.b, nk))
    return n_ins, problems
