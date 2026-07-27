#!/usr/bin/env python3
"""Bisect the RPL-growth failure into its three confounded variables.

The 2026-07-10 v1/v2 repacks changed THREE things at once and hung at LUI-init:
  (a) .text was re-deflated at zlib level 9 instead of the SDK's stream
  (b) every section after .data was relaid out to a new file offset
  (c) .text (and .data) grew

Because rpl_edit.py now reproduces the stock file BYTE-IDENTICALLY with verbatim
payloads (gate G1), (b) is already exonerated on its own: a no-edit rebuild IS
the stock file. So these builds isolate (a) then (c).

Every build here is FUNCTIONALLY INERT -- no call site is repointed, no appended
byte is ever executed. A hang therefore indicts the container, not a hook.

  B1  recompress .text only            -> isolates (a)
  B2  B1 + append 0x40 zeros to .text  -> isolates (c), text only
  B3  B2 + append 0x40 zeros to .data  -> (c) on both grown sections

Boot in order. First hang names the culprit. All three boot => growth is viable
and the "RPL repack is a DEAD END" finding is retired.
"""
import sys, os, hashlib, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpl_edit as R

SRC = r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\code\t6mp_cafef_rpl.rpl"
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def md5(b):
    return hashlib.md5(b).hexdigest()


def fixup_fileinfo(r, stock_fi):
    """Raise (never lower) the FILEINFO allocation fields if our edits grew a
    region. tempSize is not derivable from stock (it sits ~0x5600 BELOW the
    load-region total on both RPLs), so we carry the stock value forward and
    add whatever the load region grew by -- which for append-to-.text is 0."""
    cur = r._rebuild_fileinfo()
    for key, off in (("text", R.FI_TEXTSIZE), ("data", R.FI_DATASIZE),
                     ("load", R.FI_LOADSIZE)):
        if cur[key] > r.fi_get(off):
            r.fi_set(off, R._align(cur[key], 0x100))
            print(f"      FILEINFO {key}Size raised -> {r.fi_get(off):#x}")
    grew = cur["load"] - stock_fi["load"]
    if grew > 0:
        r.fi_set(R.FI_TEMPSIZE, r.fi_get(R.FI_TEMPSIZE) + R._align(grew, 0x100))
        print(f"      FILEINFO tempSize raised -> {r.fi_get(R.FI_TEMPSIZE):#x}")


def build(tag, textpad, datapad):
    stock = open(SRC, "rb").read()
    r = R.Rpl(stock)
    base_fi = r._rebuild_fileinfo()

    text = r.by_name(".text")
    text.mark_dirty()                      # forces re-deflate even with no edit
    before_t = len(text.data)
    if textpad:
        r.append(".text", b"\x4e\x80\x00\x20" * (textpad // 4), align=4)
    if datapad:
        r.append(".data", b"\x00" * datapad, align=8)

    probs = r.check_headroom()
    fixup_fileinfo(r, base_fi)
    probs = r.check_headroom()
    if probs:
        print(f"  {tag}: REFUSED --")
        for p in probs:
            print("      " + p)
        return None

    out = r.build()
    path = os.path.join(OUTDIR, f"{tag}_t6mp_cafef_rpl.rpl")
    open(path, "wb").write(out)

    # verify by reparsing the written file
    v = R.Rpl(out)
    vt = v.by_name(".text")
    assert len(vt.data) == before_t + textpad, "text size mismatch on reparse"
    assert bytes(vt.data[:before_t]) == bytes(text.data[:before_t]), \
        "original .text bytes changed!"
    vc = v.by_type(R.SHT_RPL_CRCS)[0]
    v._rebuild_crcs()
    assert bytes(v.by_type(R.SHT_RPL_CRCS)[0].data) == bytes(vc.data), "CRC self-check"
    assert not v.check_headroom(), "headroom on reparse"

    print(f"  {tag}: {len(out)} bytes  md5={md5(out)}")
    print(f"      .text {before_t:#x} -> {len(vt.data):#x}   "
          f"cave_va={text.addr + before_t:#010x}")
    print(f"      file {len(stock)} -> {len(out)}  ({len(out)-len(stock):+d})")
    return path


if __name__ == "__main__":
    os.makedirs(OUTDIR, exist_ok=True)
    print(f"SRC {SRC}\n    md5={md5(open(SRC,'rb').read())}\n")
    # .data has only 0x1c bytes of VA headroom before the pinned .module_id
    # (0x1016ee24 -> 0x1016ee40), so B3's data append must stay <= 0x18 after
    # 8-alignment. The 2026-07-10 build's 16B RawFile scratch fit only just.
    for tag, tp, dp in (("B1", 0, 0), ("B2", 0x40, 0), ("B3", 0x40, 0x10)):
        build(tag, tp, dp)
    print("\nBackup + deploy one at a time, cold-start Cemu, boot, copy log.txt.")
