#!/usr/bin/env python3
"""B5 / B6 -- add a WHOLE NEW SECTION, the scalable route to arbitrary code.

Why this and not the relocation rewriter: append() is capped by the pinned
.syscall at 0x02B5B940, giving only 0x56D4 (22,228 B) of .text cave and a
laughable 0x1C bytes in .data. A new section takes the LAST section index, so no
existing index moves, no `.rela` sh_link/sh_info breaks, and nothing needs
relocating -- yet it can be as large as the text allocation allows. That is a far
cheaper unlock than rewriting 172k relocations.

Space available above .syscall, inside the existing FILEINFO textSize:
    .syscall ends   0x02B5B948
    textSize limit  0x02B74290
    => 0x18948 (100,680 B) without even raising textSize, which is itself just a
       FILEINFO field with nothing above it until .rodata at 0x10000000.

  B5  add a 0x400 executable section, DO NOT branch into it   (inert)
  B6  B5 + repoint the call site through it                   (executed)

Deploy B6 first: if it boots, new sections are loaded, mapped executable and
usable in one shot. If it hangs, fall back to B5 to tell "section rejected" from
"section not executable".
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

BASE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl.pre_rpleditor.bak")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

CALLSITE = 0x0280E374
GETRAW = 0x028BDF98
NEWSEC_VA = 0x02B5C000      # 0x6b8 clear of .syscall's end, 0x20-aligned
NEWSEC_SZ = 0x400


def br(frm, to, link):
    d = to - frm
    assert -0x02000000 <= d < 0x02000000, f"branch {frm:#x}->{to:#x} out of range"
    return 0x48000000 | (d & 0x03FFFFFC) | (1 if link else 0)


def build(tag, wire):
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")

    body = bytearray(NEWSEC_SZ)
    struct.pack_into(">I", body, 0, br(NEWSEC_VA, GETRAW, False))   # b GETRAW
    sec = r.add_section(body, NEWSEC_VA)

    if wire:
        off = CALLSITE - text.addr
        old = struct.unpack(">I", bytes(text.data[off:off + 4]))[0]
        assert old == br(CALLSITE, GETRAW, True), f"call site is {old:#010x}"
        text.mark_dirty()
        struct.pack_into(">I", text.data, off, br(CALLSITE, NEWSEC_VA, True))

    probs = r.check_headroom()
    assert not probs, probs
    out = r.build()
    path = os.path.join(OUTDIR, f"{tag}_t6mp_cafef_rpl.rpl")
    open(path, "wb").write(out)

    # reparse the WRITTEN file and check the new section survived intact
    v = R.Rpl(out)
    assert v.e_shnum == r.e_shnum, "shnum lost"
    assert v.sections[-1].type == R.SHT_RPL_FILEINFO, "FILEINFO must stay last"
    ns = next(x for x in v.sections if x.addr == NEWSEC_VA)
    assert ns.addr == NEWSEC_VA and ns.size == NEWSEC_SZ, "new section header wrong"
    assert bytes(ns.data[:4]) == struct.pack(">I", br(NEWSEC_VA, GETRAW, False))
    assert len(v.by_type(R.SHT_RPL_CRCS)[0].data) == 4 * v.e_shnum, "crcs not resized"
    vc = bytes(v.by_type(R.SHT_RPL_CRCS)[0].data)
    v._rebuild_crcs()
    assert bytes(v.by_type(R.SHT_RPL_CRCS)[0].data) == vc, "crc self-check"
    assert not v.check_headroom()
    # nothing but the call site may differ in .text
    orig = R.Rpl(open(BASE, "rb").read()).by_name(".text").data
    vt = v.by_name(".text").data
    diffs = [i for i in range(len(orig)) if orig[i] != vt[i]]
    assert len(orig) == len(vt), ".text size changed -- it should not have"

    md5 = hashlib.md5(out).hexdigest()
    print(f"  {tag}: {len(out)} bytes  md5={md5}")
    print(f"      new section idx={ns.idx} va={ns.addr:#010x}..{ns.addr+ns.size:#010x} "
          f"flags={ns.flags:#x} shnum={v.e_shnum}")
    print(f"      .text edits: {len(diffs)} bytes {[hex(text.addr+i) for i in diffs]}")
    return md5


if __name__ == "__main__":
    os.makedirs(OUTDIR, exist_ok=True)
    r0 = R.Rpl(open(BASE, "rb").read())
    sc = r0.by_name(".syscall")
    print(f"  .syscall {sc.addr:#010x}..{sc.addr+len(sc.data):#010x}   "
          f"textSize limit {r0.by_name('.text').addr + r0.fi_get(R.FI_TEXTSIZE):#010x}")
    print(f"  free above .syscall: {r0.by_name('.text').addr + r0.fi_get(R.FI_TEXTSIZE) - (sc.addr+len(sc.data)):#x}\n")
    build("B7", wire=False)
    build("B8", wire=True)
