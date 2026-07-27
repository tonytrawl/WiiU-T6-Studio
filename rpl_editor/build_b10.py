#!/usr/bin/env python3
"""B10 -- split B9's two variables apart.

B9 (crashed at the loading screen) changed TWO things vs B8 (which boots):
  (i)  it clobbered r12 and CTR
  (ii) it JUMPED to a baked-in absolute VA

B10 keeps (i) and drops (ii): identical register usage, but the control transfer
is PC-relative again.

    <newsec>  lis   r12, 0x028B
              ori   r12, r12, 0xDF98      ; r12 = absolute VA, computed...
              mtctr r12                   ; ...and parked in CTR...
              b     LUI_CoD_GetRawFile    ; ...but NEVER USED. Relative branch.

  boots   -> r12/CTR are safe, so B9 died on the absolute JUMP
             => the module IS rebased; baked-in VAs are invalid without a
                matching .rela entry. New code must stay PC-relative, or we
                must learn to append R_PPC_ADDR16_HA/LO relocations.
  crashes -> r12 or CTR is NOT free at this call site, and B9 says nothing at
             all about rebasing. Retest absolutes using a scratch register that
             is provably dead here.

Why this matters more than it looks: `.rela.text` holds ~172k entries
(0x2A3948 decompressed). A fully-linked image that loaded at a fixed address
would not need them. Their existence is real evidence for rebasing -- which is
why (ii) is the leading suspect and why this test is worth one boot.

NOTE: B8 proves nothing either way. Every branch in it is PC-relative, and a
uniform rebase delta preserves relative distances exactly.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

BASE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl.pre_rpleditor.bak")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

CALLSITE = 0x0280E374
GETRAW = 0x028BDF98
NEWSEC_VA = 0x02B5C000
NEWSEC_SZ = 0x400


def br(frm, to, link):
    d = to - frm
    assert -0x02000000 <= d < 0x02000000
    return 0x48000000 | (d & 0x03FFFFFC) | (1 if link else 0)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")

    hi, lo = (GETRAW >> 16) & 0xFFFF, GETRAW & 0xFFFF
    code = [0x3D800000 | hi,                    # lis   r12, hi
            0x618C0000 | lo,                    # ori   r12, r12, lo
            0x7D8903A6,                         # mtctr r12
            br(NEWSEC_VA + 12, GETRAW, False)]  # b     GETRAW   (RELATIVE)
    body = bytearray(NEWSEC_SZ)
    for i, w in enumerate(code):
        struct.pack_into(">I", body, i * 4, w)
    r.add_section(body, NEWSEC_VA)

    off = CALLSITE - text.addr
    assert struct.unpack(">I", bytes(text.data[off:off + 4]))[0] == br(CALLSITE, GETRAW, True)
    text.mark_dirty()
    struct.pack_into(">I", text.data, off, br(CALLSITE, NEWSEC_VA, True))

    assert not r.check_headroom()
    out = r.build()
    open(os.path.join(OUTDIR, "B10_t6mp_cafef_rpl.rpl"), "wb").write(out)

    v = R.Rpl(out)
    assert v.sections[-1].type == R.SHT_RPL_FILEINFO
    ns = next(x for x in v.sections if x.addr == NEWSEC_VA)
    try:
        from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN
        md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
        for ins in md.disasm(bytes(ns.data[:16]), NEWSEC_VA):
            print(f"    {ins.address:#010x}: {ins.mnemonic:8} {ins.op_str}")
    except ImportError:
        pass
    print(f"\n  B10: {len(out)} bytes  md5={hashlib.md5(out).hexdigest()}")


if __name__ == "__main__":
    main()
