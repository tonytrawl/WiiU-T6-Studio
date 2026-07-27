#!/usr/bin/env python3
"""B18 -- DOES ANY .text EDIT REACH EXECUTION? Delete the log line.

B17 patched the string pointer in BOTH modules, at a site verified by
disassembly to be inside R_InitScanBuffers__FPC14GfxWindowParms two instructions
before `bl OSReport`, with no relocation on either instruction, and both modules
demonstrably loaded (Cemu checksums changed). The message printed unchanged.

Everything offline checks out, so the remaining suspect is the one assumption I
have never tested: that a `.text` edit made through rpl_edit's rebuild actually
reaches executed code. If it does not, that single fact explains EVERY result
tonight -- B13/B14 silent (call site never redirected, cave never entered),
B15 not crashing (same), B16/B17 unchanged -- and would mean B9's crash was
unrelated, as already suspected.

The test: NOP the `bl OSReport` itself. Nothing subtle, no pointer, no cave.

    t6mp  0x0297B4D8   bl 0x2b57810  -> nop
    t6zm  0x027FA23C   bl 0x29d72f0  -> nop

The string edit from B16/B17 is REVERTED so this is one variable only.

  "R_InitScanBuffers" DISAPPEARS from the log
      -> .text edits DO reach execution. Then B17's failure is specific to the
         string pointer and I chase that instead.
  "R_InitScanBuffers" STILL PRINTS (twice, as always)
      -> .text edits do NOT reach execution. Everything built on "it boots"
         since B2 is void, and the real question becomes what the loader
         actually executes -- not how to author code for it.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R
import ppc

CODE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

JOBS = [
    ("t6mp_cafef_rpl.rpl.pre_rpleditor.bak", "t6mp_cafef_rpl.rpl", 0x0297B4D8),
    ("t6_cafef_rpl.rpl.pre_rpleditor.bak",   "t6_cafef_rpl.rpl",   0x027FA23C),
]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for src, outname, bl_at in JOBS:
        r = R.Rpl(open(os.path.join(CODE, src), "rb").read())
        text = r.by_name(".text")
        off = bl_at - text.addr
        old = struct.unpack(">I", bytes(text.data[off:off + 4]))[0]
        assert (old >> 26) == 18 and (old & 1) == 1, f"{src}: {old:#x} is not a bl"
        text.mark_dirty()
        struct.pack_into(">I", text.data, off, ppc.nop())

        assert not r.check_headroom()
        out = r.build()
        open(os.path.join(OUTDIR, "B18_" + outname), "wb").write(out)

        v = R.Rpl(out).by_name(".text")
        assert struct.unpack(">I", bytes(v.data[off:off + 4]))[0] == ppc.nop()
        orig = R.Rpl(open(os.path.join(CODE, src), "rb").read()).by_name(".text").data
        diffs = [i for i in range(len(orig)) if orig[i] != v.data[i]]
        print(f"  {outname}")
        print(f"    {bl_at:#010x}  {old:#010x} -> nop   "
              f".text bytes changed: {len(diffs)} at "
              f"{[hex(text.addr + i) for i in diffs]}")
        print(f"    md5={hashlib.md5(out).hexdigest()}")


if __name__ == "__main__":
    main()
