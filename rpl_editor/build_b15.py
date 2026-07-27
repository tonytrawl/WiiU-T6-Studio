#!/usr/bin/env python3
"""B15 -- DOES THE CAVE EXECUTE AT ALL? Deliberate crash as the signal.

Three builds (B13 gated, B14 ungated menu, B14 ungated match) produced ZERO
OSReport output and ZERO crashes. That is only consistent with two stories:

  (1) the cave runs, and `bl <OSReport stub>` silently does nothing, or
  (2) the cave NEVER RUNS, and the call site at 0x0280E374 is dead code in
      this build.

I have been asserting (1) purely because B9 crashed -- B9 and B11 differ only in
one `ori` immediate, so the cave "must" execute. That is inference, not
measurement, and it is the same "absence of failure = success" error that has
already bitten twice this session (B12's unverified .bss value, and the
menu-only boot instruction). So: measure it.

This cave's FIRST instruction is a store to address 0. Nothing else can mask it.

    <newsec>  stw r0, 0(r0)     ; rA=0 means LITERAL zero -> EA = 0 -> DSI fault
              ...passthrough, unreachable...

  CRASHES (0xc0000005 / DSI in the Cemu crashlog)
      -> the cave executes. Story (1). The bug is the import stub call, and
         `0x02B57810` is not a usable trampoline at runtime -- it is a LINK-TIME
         placeholder in a region with no file bytes, and Cemu evidently resolves
         imports by rewriting each call site rather than populating it.
         Next: call imports by a different route entirely.

  BOOTS FINE
      -> the cave NEVER executes. Story (2). Then B4/B8/B11/B12 proved only that
         the CONTAINER loads -- not that appended code runs -- and B9's crash had
         some other cause. Everything from B4 onward needs re-deriving against a
         call site proven to be hot.

⚠ THIS BUILD IS EXPECTED TO CRASH IF IT IS WORKING. A clean boot is the
  surprising result here, not the good one.
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


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")

    code = [ppc.stw(0, 0, 0)]                       # <-- the whole experiment
    code += [ppc.branch(NEWSEC_VA + 4, GETRAW, link=False)]
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

    assert not r.check_headroom()
    out = r.build()
    open(os.path.join(OUTDIR, "B15_t6mp_cafef_rpl.rpl"), "wb").write(out)

    v = R.Rpl(out)
    assert v.sections[-1].type == R.SHT_RPL_FILEINFO
    w = struct.unpack(">I", bytes(v.by_name(".text").data[off:off + 4]))[0]
    assert w == ppc.branch(CALLSITE, NEWSEC_VA, True), "call site not retargeted"
    print(f"\n  B15: {len(out)} bytes  md5={hashlib.md5(out).hexdigest()}")
    print("       EXPECTED TO CRASH. A clean boot means the cave is dead code.")


if __name__ == "__main__":
    main()
