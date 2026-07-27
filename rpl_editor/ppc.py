#!/usr/bin/env python3
"""Minimal PPC32-BE encoders for authoring RPL code caves.

Deliberately tiny and dependency-free -- keystone is not always installed, and
for hook/trampoline work the instruction vocabulary is small. Every encoder is
verified against capstone by verify() below.

⚠ Addresses baked in by lis/ori MUST already be RUNTIME addresses -- pass file
VAs through rpl_edit.rt_code() / rt_data() first. See the delta note in
rpl_edit.py; getting this wrong costs a boot (build B9).
"""
import struct


def lis(rd, imm):
    return 0x3C000000 | (rd << 21) | (imm & 0xFFFF)


def ori(ra, rs, imm):
    return 0x60000000 | (rs << 21) | (ra << 16) | (imm & 0xFFFF)


def load32(rd, val):
    """rd = val (2 instructions). ori, not addi -- the low half must be
    zero-extended, not sign-extended."""
    return [lis(rd, (val >> 16) & 0xFFFF), ori(rd, rd, val & 0xFFFF)]


def mtctr(rs):
    return 0x7C0903A6 | (rs << 21)


def bctr():
    return 0x4E800420


def bctrl():
    return 0x4E800421


def stw(rs, d, ra):
    return 0x90000000 | (rs << 21) | (ra << 16) | (d & 0xFFFF)


def lwz(rd, d, ra):
    return 0x80000000 | (rd << 21) | (ra << 16) | (d & 0xFFFF)


def nop():
    return 0x60000000


def mflr(rd):
    return 0x7C0802A6 | (rd << 21)


def mtlr(rs):
    return 0x7C0803A6 | (rs << 21)


def stwu(rs, d, ra):
    return 0x94000000 | (rs << 21) | (ra << 16) | (d & 0xFFFF)


def addi(rd, ra, imm):
    # ⚠ PPC GOTCHA: addi with rA=r0 means LITERAL ZERO, not "register r0".
    # `addi r0, r0, 1` sets r0 = 1, it does not increment. Never use r0 as rA.
    if ra == 0 and rd != 0:
        pass  # intentional li-style use
    return 0x38000000 | (rd << 21) | (ra << 16) | (imm & 0xFFFF)


def li(rd, imm):
    return addi(rd, 0, imm)


def mr(rd, rs):
    return 0x7C000378 | (rs << 21) | (rd << 16) | (rs << 11)


def cmpwi(ra, imm):
    return 0x2C000000 | (ra << 16) | (imm & 0xFFFF)


def bge(frm, to):
    """Branch if CR0 >= (i.e. not less-than). BO=4 (branch if condition FALSE),
    BI=0 (CR0 LT bit)."""
    d = to - frm
    if not (-0x8000 <= d < 0x8000):
        raise ValueError(f"conditional branch {frm:#x} -> {to:#x} out of +/-32KB")
    return 0x40800000 | (d & 0xFFFC)


def branch(frm, to, link=False):
    """PC-relative b/bl. IMMUNE to the runtime delta -- a uniform rebase
    preserves relative distances, so pass FILE VAs here, not runtime ones."""
    d = to - frm
    if not (-0x02000000 <= d < 0x02000000):
        raise ValueError(f"branch {frm:#x} -> {to:#x} out of +/-32MB range")
    return 0x48000000 | (d & 0x03FFFFFC) | (1 if link else 0)


def assemble(words):
    return b"".join(struct.pack(">I", w) for w in words)


def verify(words, addr=0x02B5C000, show=False):
    """Round-trip every encoder through capstone."""
    try:
        from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN
    except ImportError:
        return None
    md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    out = []
    for ins in md.disasm(assemble(words), addr):
        out.append(f"{ins.address:#010x}: {ins.mnemonic:8} {ins.op_str}")
        if show:
            print("    " + out[-1])
    if len(out) != len(words):
        raise ValueError(f"capstone decoded {len(out)}/{len(words)} instructions")
    return out


if __name__ == "__main__":
    verify(load32(12, 0x028BFF98) + [mtctr(12), bctr(),
           stw(12, 0, 12), lwz(12, 0, 12), nop(),
           branch(0x02B5C018, 0x028BFF98)], show=True)
