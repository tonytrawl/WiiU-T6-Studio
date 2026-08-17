#!/usr/bin/env python3
"""
PC-side SndBank (SOUND) span (HANDOFF Track E dispatch). SPAN ONLY — not a
byte-perfect sound converter (a stub SndBank is expected to suffice for first
boot; sound is non-fatal to load).

The console probe `sndbank_probe.parse_sndbank(..., '<')` walks the PC SndBank
body byte-IDENTICALLY (matched-pair oracle on mp_raid: head + loadedAssets at
0x1264 are structurally identical, dataSize/entries/data all read correctly) and
lands exactly at data_start + dataSize. The ONE PC divergence vs Wii U (which
lands byte-exact) is a block of ZERO alignment padding emitted AFTER the huge
inline `loadedAssets.data` block, before the next asset:
  mp_raid: probe end 0x94cbbd3, next asset (XAnimParts) @0x94d8266 -> 50835
  bytes of pure zeros in between.
The pad is opaque stream-alignment for the big data block (the data goes to a
differently-aligned stream on PC); its size isn't derivable from the linear
cursor, but it is ALWAYS zero and the next asset body begins with a non-zero
name dword (FOLLOW / block-alias / null-with-body). So: run the probe, then skip
the trailing zero pad to the next asset boundary.
"""
import struct
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import sndbank_probe as _S


def parse_sndbank_pc(d, off, body=None):
    end, name, ac, stats = _S.parse_sndbank(d, off, '<', body=body)
    # Skip zero alignment padding after the inline data block to the next asset.
    # Guard: only advance over genuine zero pad (never across real content).
    o = end
    n = len(d)
    while o < n and d[o] == 0:      # pad is byte-granular (not 4-aligned)
        o += 1
    return o
