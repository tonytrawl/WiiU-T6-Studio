#!/usr/bin/env python3
"""B12 -- .bss growth for new mutable data, exercised for real.

`.data` is a dead end: 0x1C bytes before .module_id. `.bss` is SHT_NOBITS, sits
last in the data region, and dataSize == (.bss end - 0x10000000) exactly, so
growing it costs zero file bytes and one FILEINFO field.

This build SUPERSETS B11 -- it also carries the delta-corrected absolute jump.
If it boots, both are proven at once. If it crashes, deploy B11 (already built,
md5 877aa8c0512b9ea6ca965890eeae6edf) to tell the two apart.

The cave doesn't just reserve .bss, it USES it, so an unmapped page faults:

    lis   r12, hi(rt_data(scratch))
    ori   r12, r12, lo(...)
    stw   r12, 0(r12)          ; WRITE to newly added .bss
    lwz   r12, 0(r12)          ; READ it back
    lis   r12, hi(rt_code(GETRAW))
    ori   r12, r12, lo(...)
    mtctr r12
    bctr                       ; passthrough, LR untouched

Only r12 is touched -- proven free at this call site by B10.

⚠ ASSUMPTION UNDER TEST: .bss takes the DATA delta (+0x5000). The project rule
was measured on .rodata; .bss is in the same region so it should follow, but a
crash here with B11 booting would indict exactly this.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R
import ppc

BASE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl.pre_rpleditor.bak")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

CALLSITE = 0x0280E374
GETRAW = 0x028BDF98
NEWSEC_VA = 0x02B5C000
NEWSEC_SZ = 0x400
BSS_GROW = 0x1000


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")
    bss = r.by_name(".bss")

    before_bss, before_ds = bss.size, r.fi_get(R.FI_DATASIZE)
    scratch = r.grow_bss(BSS_GROW)
    print(f"  .bss  size {before_bss:#x} -> {bss.size:#x}")
    print(f"  dataSize {before_ds:#x} -> {r.fi_get(R.FI_DATASIZE):#x}")
    print(f"  scratch file VA {scratch:#010x}  runtime {R.rt_data(scratch):#010x}")

    code = (ppc.load32(12, R.rt_data(scratch))
            + [ppc.stw(12, 0, 12), ppc.lwz(12, 0, 12)]
            + ppc.load32(12, R.rt_code(GETRAW))
            + [ppc.mtctr(12), ppc.bctr()])
    body = bytearray(NEWSEC_SZ)
    body[:len(code) * 4] = ppc.assemble(code)
    r.add_section(body, NEWSEC_VA)
    print("  cave:")
    for line in ppc.verify(code, NEWSEC_VA) or []:
        print("    " + line)

    off = CALLSITE - text.addr
    assert struct.unpack(">I", bytes(text.data[off:off + 4]))[0] == \
        ppc.branch(CALLSITE, GETRAW, True), "call site is not bl GetRawFile"
    text.mark_dirty()
    struct.pack_into(">I", text.data, off, ppc.branch(CALLSITE, NEWSEC_VA, True))

    probs = r.check_headroom()
    assert not probs, probs
    out = r.build()
    open(os.path.join(OUTDIR, "B12_t6mp_cafef_rpl.rpl"), "wb").write(out)

    v = R.Rpl(out)
    assert v.sections[-1].type == R.SHT_RPL_FILEINFO, "FILEINFO must stay last"
    assert v.by_name(".bss").size == before_bss + (scratch - (0x1016EF00 + before_bss)) + BSS_GROW
    assert v.fi_get(R.FI_DATASIZE) >= v._rebuild_fileinfo()["data"]
    ns = next(x for x in v.sections if x.addr == NEWSEC_VA)
    assert bytes(ns.data[:len(code) * 4]) == ppc.assemble(code)
    print(f"\n  B12: {len(out)} bytes  md5={hashlib.md5(out).hexdigest()}"
          f"  (file size vs B11: {len(out) - 8014464:+d})")


if __name__ == "__main__":
    main()
