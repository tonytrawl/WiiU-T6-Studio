"""core.hks_dis -- HavokScript instruction decoder and disassembler (roadmap P6.3).

THE INSTRUCTION FORMAT, DERIVED AND VERIFIED
--------------------------------------------
Nothing here is inherited from Lua 5.1 documentation -- HKS reorders both the opcode numbering
and the operand fields, so every field below was solved against hksc's own `-l -l` listing
(which prints decoded operands) and then checked instruction by instruction.

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
which matches Lua 5.1's 38 opcodes) and it is WRONG. The tell: aligning against hksc produced
conflicts on exactly `LT`/`LT_BK`, `FORPREP`/`FORLOOP` and `MOVE`/`GETGLOBAL` -- adjacent
opcode numbers that differ only in operand mode. HKS assigns mode variants to consecutive
opcodes, so folding bit 25 into the operand merges each pair. With 7 bits: 0 conflicts.
(Statistically the 6-bit max of 38 and the 7-bit max of 76 differ by exactly 2x, which invites
the wrong conclusion that bit 25 is an operand bit. It is not.)

⚠ THE sBx BIAS IS 65535, NOT Lua 5.1's 131071. Verified both directions:
    FORLOOP 0x7EFFFD04 -> Bx 65533 -> -2   (hksc prints -2)
    FORPREP 0x7D000004 -> Bx 65536 -> +1   (hksc prints +1)

Opcode names come from `core/_opcodes_hks.py`, mined by `core/_mine_hks.py`.
"""
import struct

from . import paths  # noqa: F401
from . import hks as H

try:
    from ._opcodes_hks import MNEMONIC, FORMAT
except Exception:                                    # not mined yet
    MNEMONIC, FORMAT = {}, {}

OP_SHIFT, OP_MASK = 25, 0x7F
SBX_BIAS = 65535
RK_BIT = 0x100


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


def encode(op, a=0, b=0, c=0):
    return ((op & OP_MASK) << OP_SHIFT) | ((b & 0xFF) << 17) | ((c & 0x1FF) << 8) | (a & 0xFF)


def encode_bx(op, a, bx):
    return ((op & OP_MASK) << OP_SHIFT) | ((bx & 0x1FFFF) << 8) | (a & 0xFF)


def encode_sbx(op, a, sbx):
    return encode_bx(op, a, (sbx + SBX_BIAS) & 0x1FFFF)


def rk(c):
    """Decode an RK operand: a constant index is returned as -(k+1), matching hksc's listing."""
    return -((c & 0xFF) + 1) if (c & RK_BIT) else c


class Instr(object):
    __slots__ = ('idx', 'word', 'op', 'a', 'b', 'c', 'bx', 'sbx')

    def __init__(self, idx, word):
        self.idx = idx
        self.word = word
        self.op, self.a, self.b, self.c, self.bx, self.sbx = decode(word)

    @property
    def name(self):
        return MNEMONIC.get(self.op, 'OP_%d' % self.op)

    def operands(self):
        """Best-effort operand tuple, following the format table when it is known."""
        fmt = FORMAT.get(self.op)
        if fmt == 'iABx':
            return (self.a, -(self.bx + 1))
        if fmt == 'iAsBx':
            return (self.a, self.sbx)
        if fmt == 'sBx':
            return (self.sbx,)
        return (self.a, self.b, rk(self.c))

    def __repr__(self):
        return '%4d  %-22s %s' % (self.idx, self.name,
                                  ' '.join(str(o) for o in self.operands()))


def instructions(f, proto):
    """Decode one proto's instruction list."""
    out = []
    for k in range(proto.n_instr):
        (w,) = struct.unpack_from(f.e + 'I', f.b, proto.instr_off + 4 * k)
        out.append(Instr(k + 1, w))
    return out


def listing(blob, max_protos=40):
    """Human-readable disassembly of a whole HKS chunk."""
    f = H.parse(blob)
    lines = ['; HavokScript %s-endian, %d proto(s)'
             % ('little' if f.e == '<' else 'big', len(f.protos))]
    for i, pr in enumerate(f.protos):
        if i >= max_protos:
            lines.append('; ... %d more protos' % (len(f.protos) - i))
            break
        lines.append('')
        lines.append('proto %d: %d params, %d registers, %d instructions, %d constants'
                     % (i, pr.params, pr.registers, pr.n_instr, len(pr.constants)))
        for ins in instructions(f, pr):
            txt = repr(ins)
            ops = ins.operands()
            k = next((o for o in ops if isinstance(o, int) and o < 0), None)
            if k is not None:
                idx = -k - 1
                if idx < len(pr.constants):
                    txt += '   ; %r' % (pr.constants[idx].value,)
            lines.append('    ' + txt)
    return '\n'.join(lines)


def selfcheck(blob):
    """Structural invariants that hold for any well-formed chunk.

    Cheap, oracle-free validation: usable on the game corpus where hksc cannot be run because
    the chunks are big-endian.
    """
    f = H.parse(blob)
    problems = []
    n_ins = 0
    for i, pr in enumerate(f.protos):
        ins = instructions(f, pr)
        n_ins += len(ins)
        if not ins:
            continue
        if ins[-1].name != 'RETURN' and 'RETURN' in MNEMONIC.values():
            problems.append('proto %d does not end in RETURN (ends %s)' % (i, ins[-1].name))
        for x in ins:
            if MNEMONIC and x.op not in MNEMONIC:
                problems.append('proto %d insn %d: unknown opcode %d' % (i, x.idx, x.op))
            if FORMAT.get(x.op) in ('iAsBx', 'sBx'):
                tgt = x.idx + x.sbx
                if not (0 <= tgt <= len(ins) + 1):
                    problems.append('proto %d insn %d: jump to %d is outside 1..%d'
                                    % (i, x.idx, tgt, len(ins)))
            if FORMAT.get(x.op) == 'iABx' and x.bx >= len(pr.constants):
                problems.append('proto %d insn %d: constant %d >= %d'
                                % (i, x.idx, x.bx, len(pr.constants)))
    return n_ins, problems
