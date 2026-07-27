#!/usr/bin/env python3
"""B19 -- change a data reference the way the loader actually resolves it.

THE FINDING (B16/B17 failed for this reason):

    off=0x0297B4C6  type=6  R_PPC_ADDR16_HA  $DATA(0x10000000) + 0xCD340
    off=0x0297B4CE  type=4  R_PPC_ADDR16_LO  $DATA(0x10000000) + 0xCD340
    off=0x0297B4D8  type=10 R_PPC_REL24      OSReport

ADDR16_HA/LO relocations point at the 16-bit IMMEDIATE FIELD -- instruction
address + 2 -- not the instruction start. I probed 0x0297B4C4/0x0297B4CC, saw
"no reloc", and wrongly concluded data refs were unrelocated. The loader
rewrites those immediates at load, so editing the instruction is pointless: our
value is overwritten with symval+addend.

REL24 sits at +0 and the loader masks the displacement into whatever opcode is
present -- which is why B18's `nop` worked (it stayed an `ori`, never became a
branch) while B16/B17's immediate edits were silently reverted.

So: edit the ADDEND. symval is $DATA = 0x10000000, so addend == offset into the
data region.

    t6mp  addend 0xCD340 -> 0x241C4   ("Attempted to register an endpoint ...")
    t6zm  addend 0xAC220 -> 0x278C4   (same text, different rodata layout)

The instruction immediates are updated to match as well, so the file is
self-consistent whether or not the loader relocates.

Corollary worth keeping: symval+addend == the address already baked in the
instruction, i.e. the relocation resolves to the SAME value the file holds.
$DATA is 0x10000000 and .rodata is at 0x10000000 => the data delta is ZERO.
The rt_data()/+0x5000 assumption in rpl_edit.py is not supported by this
evidence and must not be trusted until re-measured.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

CODE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
NEW_TEXT = b"Attempted to register an endpoint"

JOBS = [
    ("t6mp_cafef_rpl.rpl.pre_rpleditor.bak", "t6mp_cafef_rpl.rpl", 0x0297B4C4, 0x0297B4CC),
    ("t6_cafef_rpl.rpl.pre_rpleditor.bak",   "t6_cafef_rpl.rpl",   0x027FA228, 0x027FA230),
]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for src, outname, lis_at, addi_at in JOBS:
        r = R.Rpl(open(os.path.join(CODE, src), "rb").read())
        text, rod = r.by_name(".text"), r.by_name(".rodata")
        relasec = r.by_name(".rela.text")
        rela = relasec.data
        rb = bytes(rod.data)
        k = rb.find(NEW_TEXT)
        assert k >= 0, f"{src}: replacement string missing"
        new_va = rod.addr + k

        # find the HA/LO relocs at instruction+2
        want = {lis_at + 2: 6, addi_at + 2: 4}
        found = {}
        for o in range(0, len(rela), 12):
            off, info, add = struct.unpack(">III", bytes(rela[o:o + 12]))
            if off in want:
                assert (info & 0xFF) == want[off], f"type {info & 0xFF} at {off:#x}"
                found[off] = (o, info, add)
        assert len(found) == 2, f"{src}: found {len(found)}/2 relocs"

        symval = None
        for off, (o, info, add) in sorted(found.items()):
            sym = bytes(r.by_name(".symtab").data)
            symval = struct.unpack(">I", sym[(info >> 8) * 16 + 4:(info >> 8) * 16 + 8])[0]
            new_add = new_va - symval
            assert 0 <= new_add < (1 << 32)
            relasec.mark_dirty()
            struct.pack_into(">I", relasec.data, o + 8, new_add)
            print(f"    reloc @{off:#010x} type={info & 0xFF}  addend {add:#x} -> {new_add:#x}")

        # keep the instruction immediates consistent with the new target
        lo = new_va & 0xFFFF
        hi = ((new_va >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF
        text.mark_dirty()
        for at, imm in ((lis_at, hi), (addi_at, lo)):
            off = at - text.addr
            w = struct.unpack(">I", bytes(text.data[off:off + 4]))[0]
            struct.pack_into(">I", text.data, off, (w & 0xFFFF0000) | imm)

        assert not r.check_headroom()
        out = r.build()
        open(os.path.join(OUTDIR, "B19_" + outname), "wb").write(out)
        print(f"  {outname}: target {new_va:#010x}  md5={hashlib.md5(out).hexdigest()}\n")


if __name__ == "__main__":
    main()
