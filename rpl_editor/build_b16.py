#!/usr/bin/env python3
"""B16 -- logging by HIJACKING an existing, provably-hot OSReport call site.

B15 settled it: our cave at the LUI_CoD_GetRawFile call site never executes, so
every "it boots" result from B4 onward proved only that the CONTAINER loads.
And the disassembly explains why the OSReport call was silent:

    0x02250094  lis  r3, 0x1002       <- NO relocation
    0x02250098  addi r3, r3, 0x41c4   <- NO relocation   r3 = 0x100241c4
    0x022500a0  bl   0x2b57810        <- RELOCATED (R_PPC_REL24, type 10)

Only the IMPORT BRANCH is relocated. The loader rewrites the game's own `bl`s;
ours was never in `.rela.text`, so `bl 0x2b57810` branched into a region that has
no file bytes and is not populated for us. Meanwhile rodata pointers are baked
ABSOLUTE with no reloc and read back as valid strings -- so data addresses are
correct as written, with no delta.

So don't call OSReport. Steer a call the game already makes.

  patch:  lis/addi that builds "R_InitScanBuffers" (0x100CD340)
    to:   the same pair building 0x100241C4
          ("Attempted to register an endpoint with an unregistered security ID")

Two same-size instruction edits. No new section, no cave, no execution
assumption -- "R_InitScanBuffers" appears in EVERY boot log, so the site is
provably hot.

  log shows the endpoint string  -> we can steer real relocated calls; absolute
                                    rodata addressing works as written. That IS
                                    the logging primitive, at 2 instructions.
  log still shows R_InitScanBuffers -> the edit did not take effect.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R
import ppc

BASE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl.pre_rpleditor.bak")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

LIS_AT, ADDI_AT = 0x0297B4C4, 0x0297B4CC
OLD_STR = 0x100CD340          # "R_InitScanBuffers"
NEW_STR = 0x100241C4          # "Attempted to register an endpoint ..."


def ha_pair(va):
    """(lis_imm, addi_imm) such that (imm<<16) + sign_extend(lo) == va."""
    lo = va & 0xFFFF
    hi = ((va >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF
    assert ((hi << 16) + struct.unpack(">h", struct.pack(">H", lo))[0]) & 0xFFFFFFFF == va
    return hi, lo


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    r = R.Rpl(open(BASE, "rb").read())
    text = r.by_name(".text")
    rod = r.by_name(".rodata")

    def rdstr(va):
        o = va - rod.addr
        return bytes(rod.data[o:rod.data.find(b"\0", o)]).decode("latin1")

    print(f"  from {OLD_STR:#010x} {rdstr(OLD_STR)!r}")
    print(f"  to   {NEW_STR:#010x} {rdstr(NEW_STR)!r}")

    lo_off, ad_off = LIS_AT - text.addr, ADDI_AT - text.addr
    old_lis = struct.unpack(">I", bytes(text.data[lo_off:lo_off + 4]))[0]
    old_addi = struct.unpack(">I", bytes(text.data[ad_off:ad_off + 4]))[0]
    rd_ = (old_lis >> 21) & 31
    assert (old_lis >> 26) == 15 and (old_addi >> 26) == 14, "not a lis/addi pair"
    hi, lo = ha_pair(NEW_STR)
    new_lis = (old_lis & 0xFFFF0000) | hi
    new_addi = (old_addi & 0xFFFF0000) | lo

    text.mark_dirty()
    struct.pack_into(">I", text.data, lo_off, new_lis)
    struct.pack_into(">I", text.data, ad_off, new_addi)

    assert not r.check_headroom()
    out = r.build()
    open(os.path.join(OUTDIR, "B16_t6mp_cafef_rpl.rpl"), "wb").write(out)

    v = R.Rpl(out).by_name(".text")
    for line in ppc.verify([struct.unpack(">I", bytes(v.data[lo_off:lo_off + 4]))[0],
                            struct.unpack(">I", bytes(v.data[lo_off + 4:lo_off + 8]))[0],
                            struct.unpack(">I", bytes(v.data[ad_off:ad_off + 4]))[0]],
                           LIS_AT) or []:
        print("    " + line)
    orig = R.Rpl(open(BASE, "rb").read()).by_name(".text").data
    diffs = [i for i in range(len(orig)) if orig[i] != v.data[i]]
    print(f"\n  .text bytes changed: {len(diffs)} at {[hex(text.addr+i) for i in diffs]}")
    print(f"  B16: {len(out)} bytes  md5={hashlib.md5(out).hexdigest()}")
    print(f"       expect '[OSConsole] Attempted to register an endpoint...' "
          f"where 'R_InitScanBuffers' used to be")


if __name__ == "__main__":
    main()
