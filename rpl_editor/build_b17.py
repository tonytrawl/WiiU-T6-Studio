#!/usr/bin/env python3
"""B17 -- B16's string hijack applied to BOTH modules.

B16 patched t6mp only and the message was unchanged. The scan was not at fault:
even base-register-aware, `R_InitScanBuffers` has exactly ONE reference in t6mp
and B16 patched it. So that log line does not originate in t6mp.

This is already established project knowledge that I failed to apply --
`wiiu_ref/rpl_sigpatch.py` says it outright: "Patch the SHARED engine RPL, not
just MP. Auth runs from t6_cafef_rpl.rpl (loaded first), which has its own
statically-compiled db_auth. t6mp has a second copy. Patch BOTH." Both modules
stay resident with duplicate statically-linked engine code, and the ZM copy is
what prints.

  t6mp : lis/addi @ 0x0297B4C4/0x0297B4CC   0x100CD340 -> 0x100241C4
  t6zm : lis/addi @ 0x027FA228/0x027FA230   0x100AC220 -> 0x100278C4

Both replacement strings are "Attempted to register an endpoint with an
unregistered security ID", resolved per-module (the rodata layouts differ).
3 bytes per module, same size, no new sections.

  log shows the endpoint string -> hijack works; steering a hot call site is a
                                   2-instruction logging primitive.
  log unchanged                 -> the message comes from neither patched site.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

CODE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

JOBS = [
    # (source, output tag, lis VA, addi VA, old string VA)
    (r"t6mp_cafef_rpl.rpl.pre_rpleditor.bak", "t6mp_cafef_rpl.rpl",
     0x0297B4C4, 0x0297B4CC, 0x100CD340),
    (r"t6_cafef_rpl.rpl", "t6_cafef_rpl.rpl",
     0x027FA228, 0x027FA230, 0x100AC220),
]
NEW_TEXT = b"Attempted to register an endpoint"


def ha_pair(va):
    lo = va & 0xFFFF
    hi = ((va >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF
    assert ((hi << 16) + struct.unpack(">h", struct.pack(">H", lo))[0]) & 0xFFFFFFFF == va
    return hi, lo


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for src, outname, lis_at, addi_at, old_str in JOBS:
        r = R.Rpl(open(os.path.join(CODE, src), "rb").read())
        text, rod = r.by_name(".text"), r.by_name(".rodata")
        rb = bytes(rod.data)
        new_str = rod.addr + rb.find(NEW_TEXT)
        assert rb.find(NEW_TEXT) >= 0, f"{src}: replacement string not found"

        lo_off, ad_off = lis_at - text.addr, addi_at - text.addr
        old_lis = struct.unpack(">I", bytes(text.data[lo_off:lo_off + 4]))[0]
        old_addi = struct.unpack(">I", bytes(text.data[ad_off:ad_off + 4]))[0]
        assert (old_lis >> 26) == 15 and (old_addi >> 26) == 14, f"{src}: not lis/addi"
        built = ((old_lis & 0xFFFF) << 16) + struct.unpack(
            ">h", struct.pack(">H", old_addi & 0xFFFF))[0]
        assert built & 0xFFFFFFFF == old_str, f"{src}: pair builds {built:#x} not {old_str:#x}"

        hi, lo = ha_pair(new_str)
        text.mark_dirty()
        struct.pack_into(">I", text.data, lo_off, (old_lis & 0xFFFF0000) | hi)
        struct.pack_into(">I", text.data, ad_off, (old_addi & 0xFFFF0000) | lo)

        assert not r.check_headroom()
        out = r.build()
        path = os.path.join(OUTDIR, "B17_" + outname)
        open(path, "wb").write(out)
        print(f"  {src}")
        print(f"    {old_str:#010x} -> {new_str:#010x}   md5={hashlib.md5(out).hexdigest()}")


if __name__ == "__main__":
    main()
