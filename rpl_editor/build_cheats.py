#!/usr/bin/env python3
"""C1 -- force CheatsOk() to always pass, unlocking god/noclip/give/etc.

WHERE THE CHEATS ACTUALLY LIVE (measured, both modules):
    t6mp_cafef_rpl  CheatsOk has  0 callers  -> cheat commands COMPILED OUT of MP
    t6_cafef_rpl    CheatsOk has  9 callers  -> ClientCommand, Cmd_Give_f, Cmd_Kill_f,
                                                Cmd_SetViewpos_f, Cmd_Take_f, Cmd_DropWeapon_f
So this only helps Zombies/campaign. Multiplayer would need the commands ADDED, which
needs working code injection -- currently UNPROVEN (see the B4-B15 corrections).

THE GATE, in t6zm __CheatsOkInternal @0x022D2E20:

    lis   r3, 0x1111
    lwz   r3, 0x480c(r3)      ; sv_cheats dvar_t*
    bl    0x24c127c           ; Dvar_GetBool
    cmpwi r3, 0
    bne   0x22d2eb0           ; <-- taken when cheats ARE enabled
    ... "GAME_CHEATSNOTENABLED", return 0

ONE instruction: make the conditional branch unconditional. `bne target` -> `b target`.
Same size, no new sections, no relocation involved (it is a local branch with no .rela
entry). This is exactly the edit class proven to reach execution by B18.

sv_cheats itself is ALREADY writable in this baseline -- both registration sites read
default=1 with flags 0x0 and 0x40 (the DVAR_ROM 0x8 bit was cleared by the earlier
patch_svcheats_rom.py work). So this is the remaining lever, not a duplicate of it.
"""
import sys, os, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R
import ppc

CODE = (r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title"
        r"\0005000e\1010cf00\code")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# (source, output name, address of the `bne`, its branch target)
JOBS = [
    ("t6_cafef_rpl.rpl.pre_rpleditor.bak", "t6_cafef_rpl.rpl", 0x022D2E50, 0x022D2EB0),
]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    for src, outname, bne_at, target in JOBS:
        r = R.Rpl(open(os.path.join(CODE, src), "rb").read())
        text = r.by_name(".text")
        off = bne_at - text.addr
        old = struct.unpack(">I", bytes(text.data[off:off + 4]))[0]

        # verify it really is `bne <target>` (opcode 16 = bc, BO=4 branch-if-false, BI=2 EQ)
        assert (old >> 26) == 16, f"{bne_at:#x}: {old:#010x} is not a conditional branch"
        d = old & 0xFFFC
        if d & 0x8000:
            d -= 0x10000
        assert bne_at + d == target, f"bne target {bne_at + d:#x} != expected {target:#x}"

        new = ppc.branch(bne_at, target, link=False)
        text.mark_dirty()
        struct.pack_into(">I", text.data, off, new)

        assert not r.check_headroom()
        out = r.build()
        open(os.path.join(OUTDIR, "C1_" + outname), "wb").write(out)

        v = R.Rpl(out).by_name(".text")
        for line in ppc.verify([struct.unpack(">I", bytes(v.data[off:off + 4]))[0]], bne_at) or []:
            print(f"    {bne_at:#010x}: {old:#010x} -> {new:#010x}   {line.split(': ')[1]}")
        orig = R.Rpl(open(os.path.join(CODE, src), "rb").read()).by_name(".text").data
        diffs = [i for i in range(len(orig)) if orig[i] != v.data[i]]
        print(f"  {outname}: .text bytes changed = {len(diffs)}  "
              f"md5={hashlib.md5(out).hexdigest()}")


if __name__ == "__main__":
    main()
