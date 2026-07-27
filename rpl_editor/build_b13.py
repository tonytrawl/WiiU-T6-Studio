#!/usr/bin/env python3
"""B13 -- printf debugging from injected code.

The single new variable vs B12: calling an IMPORT (OSReport) PC-relative via its
loader trampoline stub, and passing it a string pointer that lives in our own
added section.

Cemu prints OSReport output as `[OSConsole] ...` lines -- the game already does
this, so success is directly visible in log.txt with zero Cemu config.

Deliberately kept to ONE new variable: a FIXED string, no varargs. Format args
are a separate step; conflating them would repeat the B9 mistake.

Timing safety: the probe is COUNTER-GATED to the first 8 hits via a .bss word.
OSReport is synchronous and slow, and several open bugs in this project are
livelocks/fence stalls -- an ungated probe in a hot path can mask or create
exactly that class. `hksL_loadfile_FastFile` fires for every LUI lua, so gating
is not optional here.

    probe:                              ; r3 = const char* name (preserved)
        mflr  r0
        stwu  r1, -0x20(r1)
        stw   r0, 0x24(r1)
        stw   r3, 0x1c(r1)
        lis   r12, hi(rt_data(counter)) ; .bss gate
        ori   r12, r12, lo(...)
        lwz   r11, 0(r12)
        cmpwi r11, 8
        bge   skip
        addi  r11, r11, 1               ; rA=r11, never r0 -- see ppc.addi note
        stw   r11, 0(r12)
        lis   r3, hi(rt_code(msg))      ; string lives in our exec section
        ori   r3, r3, lo(...)
        bl    <OSReport stub>           ; PC-relative, no reloc, no delta
    skip:
        lwz   r3, 0x1c(r1)              ; restore the real argument
        lwz   r0, 0x24(r1)
        addi  r1, r1, 0x20
        mtlr  r0
        b     LUI_CoD_GetRawFile        ; tail call, LR = original caller

Registers touched: r0, r3, r11, r12 -- all volatile at a call boundary. r3 is
saved/restored because it carries the real argument.
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
MSG_OFF = 0x200
MAX_HITS = 8
MSG = b"[RPLEDIT] probe alive: LUI_CoD_GetRawFile hooked\n\x00"


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")

    osreport = r.import_stub("OSReport")
    counter = r.grow_bss(4)
    msg_va = NEWSEC_VA + MSG_OFF
    print(f"  OSReport stub  {osreport:#010x} (file VA, PC-relative -- no delta)")
    print(f"  .bss counter   {counter:#010x} -> runtime {R.rt_data(counter):#010x}")
    print(f"  message        {msg_va:#010x} -> runtime {R.rt_code(msg_va):#010x}")

    # lay the code out twice: once to size it, once with real branch targets
    def emit(base):
        c = [ppc.mflr(0), ppc.stwu(1, -0x20, 1), ppc.stw(0, 0x24, 1), ppc.stw(3, 0x1C, 1)]
        c += ppc.load32(12, R.rt_data(counter))
        c += [ppc.lwz(11, 0, 12), ppc.cmpwi(11, MAX_HITS)]
        bge_at = base + len(c) * 4
        c += [0]                                   # placeholder for bge
        c += [ppc.addi(11, 11, 1), ppc.stw(11, 0, 12)]
        c += ppc.load32(3, R.rt_code(msg_va))
        c += [ppc.branch(base + len(c) * 4, osreport, link=True)]
        skip_at = base + len(c) * 4
        c[(bge_at - base) // 4] = ppc.bge(bge_at, skip_at)
        c += [ppc.lwz(3, 0x1C, 1), ppc.lwz(0, 0x24, 1),
              ppc.addi(1, 1, 0x20), ppc.mtlr(0)]
        c += [ppc.branch(base + len(c) * 4, GETRAW, link=False)]
        return c

    code = emit(NEWSEC_VA)
    assert len(code) * 4 <= MSG_OFF, "code overruns the string area"
    body = bytearray(NEWSEC_SZ)
    body[:len(code) * 4] = ppc.assemble(code)
    body[MSG_OFF:MSG_OFF + len(MSG)] = MSG
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
    open(os.path.join(OUTDIR, "B13_t6mp_cafef_rpl.rpl"), "wb").write(out)

    v = R.Rpl(out)
    assert v.sections[-1].type == R.SHT_RPL_FILEINFO
    ns = next(x for x in v.sections if x.addr == NEWSEC_VA)
    assert bytes(ns.data[MSG_OFF:MSG_OFF + len(MSG)]) == MSG, "message lost"
    assert bytes(ns.data[:len(code) * 4]) == ppc.assemble(code)
    print(f"\n  B13: {len(out)} bytes  md5={hashlib.md5(out).hexdigest()}")
    print(f"       expect up to {MAX_HITS} '[OSConsole] [RPLEDIT] probe alive' lines in log.txt")


if __name__ == "__main__":
    main()
