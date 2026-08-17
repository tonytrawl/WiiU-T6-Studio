#!/usr/bin/env python3
"""
PC-side GfxWorld span (HANDOFF Track E dispatch). Extent ONLY. Delegates to the geometry session's
`wiiu_ref/gfxworld_probe2.py` (READ-ONLY — that session owns GfxWorld conversion / region logic).
Mirrors the console `body_relayout._gfxworld_end` call pattern with the probe's 'pc' config.
"""
import io
import contextlib
import struct
import gfxworld_probe2 as G2

FOLLOW = 0xFFFFFFFF


def _gwmp_signature(d, o):
    """Is `o` a GameWorldMp body? (its own read-only signature: alias name, nodeCount==
    originalNodeCount>0, nodes/pathVis/... FOLLOW). Mirrors gameworldmp_probe.main's scan."""
    if o + 44 > len(d):
        return False
    v = struct.unpack('<11I', d[o:o + 44])
    return (v[0] >= 0x80000000 and v[1] == v[2] and 0 < v[1] < 100000
            and v[3] == FOLLOW and v[9] < 100000)


def parse_gfxworld_pc(d, off, next_is_gwmp=True):
    """GfxWorld extent via the geometry session's gfxworld_probe2 (READ-ONLY). Its PC walk currently
    ends a few hundred bytes short of the real GfxWorld tail; since GameWorldMp always immediately
    follows GfxWorld in a map zone, bridge to GameWorldMp's own self-validating signature (read-only
    use of gameworldmp knowledge — edits neither shared probe). Falls back to the probe end if the
    signature isn't found in the small window."""
    cfg = dict(G2.CFG['pc'])
    cfg['body'] = off
    p = G2.W.__new__(G2.W)
    p.c = cfg; p.d = d; p.e = cfg['endian']; p.b = off; p.o = off + cfg['bodysize']
    with contextlib.redirect_stdout(io.StringIO()):
        G2.walk(p)
    end = p.o
    if next_is_gwmp:
        for cand in range(end, min(end + 0x2000, len(d) - 44), 4):
            if _gwmp_signature(d, cand):
                return cand
    return end
