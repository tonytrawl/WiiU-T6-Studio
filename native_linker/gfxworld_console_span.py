#!/usr/bin/env python3
"""
Console (Wii U) GfxWorld SPAN for the zone-level loader simulation.

The generic ZoneCode walk happens to consume raid's GfxWorld correctly but
desyncs on dockside (the old "@758 clipMap" break was collateral): the real
console GfxWorld needs the G2 region walk (incl. the streamInfo 20-byte
prefix quirk) PLUS the console-only SSkinShaders GX2 tail block that sits
between GFXWORLD END and the next asset (raid: 11,321 bytes; noted in
gameworldmp_probe's WP-B docstring).

The SSkinShaders tail has no parsed shape yet — it is bounded by the next
asset's body: in MP/ZM zones the asset after GFXWORLD is GameWorldMp/Sp,
whose PathData body signature (nodeCount == originalNodeCount > 0, nodes* ==
FOLLOW, name* is a b5 alias) is scanned for within 128 KB.
"""
import io
import contextlib
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'wiiu_ref'))
import gfxworld_probe2 as G2

FOLLOW = 0xFFFFFFFF


def g2_console_end(d, off):
    """G2 region-walk end of a console GfxWorld body at `off` (pre-tail)."""
    cfg = dict(G2.CFG['wiiu'])
    cfg['body'] = off
    p = G2.W.__new__(G2.W)
    p.d = d; p.c = cfg; p.e = '>'; p.b = off; p.o = off + cfg['bodysize']
    p.mark = lambda l, *a: None
    with contextlib.redirect_stdout(io.StringIO()):
        G2.walk(p)
    return p.o


def _gameworld_body_at(d, o):
    """PathData body signature at o (GameWorldMp/Sp both open with name* +
    PathData{nodeCount, originalNodeCount, nodes*=FOLLOW, ...})."""
    if o + 44 > len(d):
        return False
    v = struct.unpack_from('>11I', d, o)
    return (0xA0000001 <= v[0] <= 0xBFFFFFFF and v[1] == v[2]
            and 0 < v[1] < 200000 and v[3] == FOLLOW and v[9] < 200000)


def parse_gfxworld_console(d, off, tail_limit=33554432):
    """Full console GfxWorld span: G2 walk + tail, bounded by the following
    GameWorld body signature. The tail is normally just the SSkinShaders GX2
    block (raid: 11,341 B), but dockside carries ~12.45 MB of gfx content the
    G2 probe does not model (36-B placement-like records + image pixel runs +
    the skinshader block at its end) — the signature scan hops it; the zone
    sim treats it as linear GfxWorld interior, which is what it is."""
    o = g2_console_end(d, off)
    for t in range(o, min(o + tail_limit, len(d) - 44)):
        if _gameworld_body_at(d, t):
            return t
    raise RuntimeError('no GameWorld signature within %d after GfxWorld end '
                       '0x%x' % (tail_limit, o))
