#!/usr/bin/env python3
"""Offline gate for rpl_edit.py. Nothing gets deployed until all of these pass.

  G1  round-trip: parse + rebuild with no edits  ==  byte-identical to stock
  G2  .rplcrcs semantics: our recomputed table == the stock table
  G3  FILEINFO semantics: our derived region sizes vs the stock fields
  G4  headroom: stock file reports zero problems
"""
import sys, os, hashlib, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

RPLS = [
    # the BASELINE backup, not the live file -- live is whatever test build is
    # currently deployed, and gating against that is meaningless.
    r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl.pre_rpleditor.bak",
    r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\code\t6_cafef_rpl.rpl",
]

def md5(b): return hashlib.md5(b).hexdigest()

def run(path):
    print("=" * 78)
    print(path)
    stock = open(path, "rb").read()
    print(f"  size={len(stock)}  md5={md5(stock)}")
    r = R.Rpl(stock)
    ok = True

    # G1 -- round trip, no metadata rebuild (pure layout reproduction)
    rt = r.build(rebuild_meta=False)
    g1 = (rt == stock)
    print(f"  G1 round-trip byte-identical .......... {'PASS' if g1 else 'FAIL'}")
    if not g1:
        print(f"       stock {len(stock)} vs rebuilt {len(rt)}")
        for i in range(min(len(stock), len(rt))):
            if stock[i] != rt[i]:
                print(f"       first diff at {i:#x}"); break
    ok &= g1

    # G2 -- CRC table semantics
    crcs = r.by_type(R.SHT_RPL_CRCS)
    if crcs:
        stock_tab = bytes(crcs[0].data)
        r._rebuild_crcs()
        ours = bytes(r.by_type(R.SHT_RPL_CRCS)[0].data)
        g2 = (ours == stock_tab)
        print(f"  G2 .rplcrcs recompute matches stock ... {'PASS' if g2 else 'FAIL'}")
        if not g2:
            for i in range(0, len(stock_tab), 4):
                a = struct.unpack(">I", stock_tab[i:i+4])[0]
                b = struct.unpack(">I", ours[i:i+4])[0]
                if a != b:
                    n = r.sections[i//4].sname or f"<{i//4}>"
                    print(f"       sec {i//4:>2} {n:<22} stock {a:#010x} ours {b:#010x}")
        ok &= g2
        # undo the dirty flag so G1 stays meaningful if rerun
        crcs[0]._dirty = False
        crcs[0]._data = bytearray(stock_tab)

    # G3 -- FILEINFO semantics
    cur = r._rebuild_fileinfo()
    for label, off in (("textSize", R.FI_TEXTSIZE), ("dataSize", R.FI_DATASIZE),
                       ("loadSize", R.FI_LOADSIZE), ("tempSize", R.FI_TEMPSIZE)):
        stockv = r.fi_get(off)
        key = {"textSize": "text", "dataSize": "data", "loadSize": "load"}.get(label)
        ourv = cur.get(key) if key else None
        if ourv is None:
            print(f"  G3 {label:<9} stock {stockv:#010x}  (not derived)")
        else:
            d = stockv - ourv
            print(f"  G3 {label:<9} stock {stockv:#010x}  derived {ourv:#010x}  "
                  f"headroom {d:+#x} {'OK' if d >= 0 else 'OVERRUN'}")

    # G5 -- SHT_RPL_FILEINFO must be the LAST section header.
    # Violating this makes Cemu fail with "RPLLoader: Invalid FILEINFO magic"
    # and crash (measured: build B6, 2026-07-26).
    last = r.sections[-1]
    g5 = last.type == R.SHT_RPL_FILEINFO
    print(f"  G5 FILEINFO is the last section ....... {'PASS' if g5 else 'FAIL'}")
    if not g5:
        print(f"       last section is idx {last.idx} type {last.type:#x}")
    ok &= g5

    # G6 -- an added section must not disturb any section-index reference
    r2 = R.Rpl(stock)
    r2.add_section(b"\x60\x00\x00\x00" * 4, r2.by_name(".text").addr + 0x10)
    g6 = (r2.sections[-1].type == R.SHT_RPL_FILEINFO
          and r2.e_shstrndx == r.e_shstrndx
          and r2.by_name(".shstrtab").idx == r.by_name(".shstrtab").idx
          and all(a.link == b.link for a, b in zip(r.sections[:34], r2.sections[:34])))
    print(f"  G6 add_section preserves indices ...... {'PASS' if g6 else 'FAIL'}")
    ok &= g6

    # G4 -- headroom / VA overlap on the stock file
    probs = r.check_headroom()
    g4 = not probs
    print(f"  G4 stock headroom+VA check ............ {'PASS' if g4 else 'FAIL'}")
    for p in probs:
        print("       " + p)
    ok &= g4
    return ok

if __name__ == "__main__":
    allok = True
    for p in RPLS:
        if os.path.exists(p):
            allok &= run(p)
        else:
            print("MISSING:", p); allok = False
    print("=" * 78)
    print("GATE:", "PASS" if allok else "FAIL")
    sys.exit(0 if allok else 1)
