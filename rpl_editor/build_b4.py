#!/usr/bin/env python3
"""B4 -- the first build whose APPENDED CODE ACTUALLY EXECUTES.

B1/B2/B3 proved the container accepts a grown .text/.data. They were all inert:
nothing branched into the appended bytes. B4 closes the last question -- does the
loader relocate and execute code that lives past the original end of .text?

Design: a PURE PASSTHROUGH trampoline at the exact call site that hung on
2026-07-10, with none of the logic that actually caused that hang.

    stock:  0x0280E374  bl  LUI_CoD_GetRawFile        (in hksL_loadfile_FastFile)
    B4:     0x0280E374  bl  <cave>
            <cave>      b   LUI_CoD_GetRawFile

`bl` sets LR to the instruction after the call site; the cave tail-branches with
`b` (LK=0) so LR is untouched and GetRawFile returns straight to the original
caller. No registers are read or written -- r12/CTR are not used, unlike the
lis/ori/mtctr/bctrl form. Behaviour is bit-for-bit identical to stock; the ONLY
difference is that every LUI raw-file load now detours through appended memory.

That makes the result unambiguous:
  boots + LUI works  -> appended code executes correctly; growth is fully usable
  hangs at LUI init  -> the loader mismaps appended .text even though it loads it

Critically this does NOT reintroduce the 07-10 design flaw. That build called
FS_ReadFile on EVERY lua before the DB lookup, firing hundreds of synchronous
disk opens through the FS thread during zone streaming. B4 touches no filesystem.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

BASE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl.pre_rpleditor.bak")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

CALLSITE = 0x0280E374          # bl LUI_CoD_GetRawFile in hksL_loadfile_FastFile
GETRAW = 0x028BDF98            # LUI_CoD_GetRawFile


def br(frm, to, link):
    d = to - frm
    assert -0x02000000 <= d < 0x02000000, f"branch {frm:#x}->{to:#x} out of range"
    return 0x48000000 | (d & 0x03FFFFFC) | (1 if link else 0)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")
    base = text.addr

    # confirm the call site is what we think before touching anything
    off = CALLSITE - base
    old = struct.unpack(">I", bytes(text.data[off:off + 4]))[0]
    assert old == br(CALLSITE, GETRAW, True), \
        f"call site {old:#010x} is not 'bl LUI_CoD_GetRawFile'"

    _, cave_va = r.append(".text", struct.pack(">I", 0), align=4)  # placeholder
    tramp = br(cave_va, GETRAW, False)                            # b GETRAW
    struct.pack_into(">I", text.data, cave_va - base, tramp)
    struct.pack_into(">I", text.data, off, br(CALLSITE, cave_va, True))

    probs = r.check_headroom()
    assert not probs, probs
    out = r.build()
    path = os.path.join(OUTDIR, "B4_t6mp_cafef_rpl.rpl")
    open(path, "wb").write(out)

    # verify by reparsing the WRITTEN file and disassembling both sites
    v = R.Rpl(out).by_name(".text")
    got_call = struct.unpack(">I", bytes(v.data[off:off + 4]))[0]
    got_cave = struct.unpack(">I", bytes(v.data[cave_va - base:cave_va - base + 4]))[0]
    try:
        from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN
        md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
        dis = lambda w, a: next(md.disasm(struct.pack(">I", w), a))
        i1, i2 = dis(got_call, CALLSITE), dis(got_cave, cave_va)
        print(f"  {CALLSITE:#010x}: {old:#010x} -> {got_call:#010x}   {i1.mnemonic} {i1.op_str}")
        print(f"  {cave_va:#010x}: (new)        {got_cave:#010x}   {i2.mnemonic} {i2.op_str}")
    except ImportError:
        print(f"  callsite {got_call:#010x}   cave {got_cave:#010x}  (capstone absent)")

    assert got_call == br(CALLSITE, cave_va, True)
    assert got_cave == br(cave_va, GETRAW, False)
    # every other byte of .text must be untouched
    orig = R.Rpl(open(BASE, "rb").read()).by_name(".text").data
    diffs = [i for i in range(len(orig)) if orig[i] != v.data[i]]
    print(f"  .text differing bytes vs baseline: {len(diffs)} "
          f"at {[hex(base+i) for i in diffs]}")
    assert all(CALLSITE - base <= i < CALLSITE - base + 4 for i in diffs), \
        "unexpected .text edits outside the call site"

    md5 = hashlib.md5(out).hexdigest()
    print(f"\n  B4: {len(out)} bytes  md5={md5}")
    print(f"      cave_va={cave_va:#010x}  .text {len(orig):#x} -> {len(v.data):#x}")


if __name__ == "__main__":
    main()
