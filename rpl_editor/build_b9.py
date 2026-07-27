#!/usr/bin/env python3
"""B9 -- can NEW code use ABSOLUTE addresses without adding .rela entries?

This is the last unknown blocking real function authoring. B4/B8 only used
PC-relative b/bl, which are immune to rebasing. But any useful new function
references globals in .data/.rodata at 0x10000000+, which is 220 MB away -- far
outside the +/-32 MB branch range. That needs lis/ori, i.e. an absolute VA baked
into the instruction stream.

If Cemu rebases the module at load, a baked-in VA is wrong unless a matching
R_PPC_ADDR16_HA/LO relocation exists in .rela.text -- and growing .rela.text is
expensive (it is a load-range section, so loadSize/tempSize move).

Evidence it is NOT rebased: the 2026-07-10 Cemu graphic-pack hook used absolute
lis/ori for GetRawFile (0x028BDF98), FS_ReadFile (0x024FBA24) and rodata strings
(0x1009B904) and worked at runtime. That implies load-address == link-address.
But that hook was injected into already-mapped memory, where absolute VAs are
trivially correct; it says nothing about a FILE-patched image. So: measure.

Test: same pure passthrough as B8, but reached via an ABSOLUTE indirect jump
instead of a relative branch.

    <newsec>  lis   r12, 0x028B
              ori   r12, r12, 0xDF98      ; r12 = LUI_CoD_GetRawFile
              mtctr r12
              bctr                        ; LK=0 -> LR untouched, tail-call

r12 and CTR are volatile at a call boundary, so this clobbers nothing. Behaviour
is identical to stock IF the absolute VA is valid.

  boots + LUI works -> absolute VAs are correct as-written; NO .rela growth is
                       ever needed for new code. Function authoring is unblocked.
  crashes           -> the image IS rebased; new code must either stay
                       PC-relative or we must learn to append .rela.text entries.
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
    # ori (not addi) so the low half is zero-extended, not sign-extended
    code = [0x3D800000 | hi,          # lis   r12, hi
            0x618C0000 | lo,          # ori   r12, r12, lo
            0x7D8903A6,               # mtctr r12
            0x4E800420]               # bctr
    body = bytearray(NEWSEC_SZ)
    for i, w in enumerate(code):
        struct.pack_into(">I", body, i * 4, w)
    r.add_section(body, NEWSEC_VA)

    off = CALLSITE - text.addr
    old = struct.unpack(">I", bytes(text.data[off:off + 4]))[0]
    assert old == br(CALLSITE, GETRAW, True), f"call site is {old:#010x}"
    text.mark_dirty()
    struct.pack_into(">I", text.data, off, br(CALLSITE, NEWSEC_VA, True))

    assert not r.check_headroom()
    out = r.build()
    path = os.path.join(OUTDIR, "B9_t6mp_cafef_rpl.rpl")
    open(path, "wb").write(out)

    v = R.Rpl(out)
    assert v.sections[-1].type == R.SHT_RPL_FILEINFO, "FILEINFO must stay last"
    ns = next(x for x in v.sections if x.addr == NEWSEC_VA)
    try:
        from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN
        md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
        print("  new section disasm:")
        for ins in md.disasm(bytes(ns.data[:16]), NEWSEC_VA):
            print(f"    {ins.address:#010x}: {ins.mnemonic:8} {ins.op_str}")
    except ImportError:
        print("  (capstone absent)", [hex(w) for w in code])
    # reconstruct the VA the CPU will compute, exactly as the hardware would
    built = ((code[0] & 0xFFFF) << 16) | (code[1] & 0xFFFF)
    assert built == GETRAW, f"lis/ori builds {built:#x}, wanted {GETRAW:#x}"
    print(f"    -> r12 = {built:#010x} == LUI_CoD_GetRawFile")

    md5 = hashlib.md5(out).hexdigest()
    print(f"\n  B9: {len(out)} bytes  md5={md5}  newsec idx={ns.idx} shnum={v.e_shnum}")


if __name__ == "__main__":
    main()
