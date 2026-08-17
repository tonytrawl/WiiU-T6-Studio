#!/usr/bin/env python3
"""
PC GfxWorld allocation-event model (Track G part B: the PC-side interior
virtual model — HANDOFF item "unresolved -> 0").

The PC sim used to register the whole GfxWorld as ONE linear region plus an
empirical end-skip (gfx_skip_pc = -10.4 MB raid / -15.4 MB dock), which makes
pc_inv garbage INSIDE GfxWorld — every ref into the interior stayed tagged
(24.7K raid / 32.4K skate unres:GfxWorld).

This module derives the region list from the geometry session's
gfxworld_probe2 walk (READ-ONLY use: we hook `mark`, we do not edit it) and
classifies each region per the T6 load db (gfxworld_t6_load_db.cpp):
  seg   VIRTUAL: consumes block-5 1:1 (alloc-aligned)
  temp  file bytes, NO virtual: the image-pixel class (reflection-probe
        pixels, lightmap pixels, outdoorImage) — PC loads these outside the
        virtual block (measured: the three regions sum to 10,234,048 on raid
        = the bulk of the -10,402,376 PC deficit), plus inline-asset roots.
RUNTIME_VIRTUAL allocations carry no file bytes and consume no block-5 —
they do not appear in the stream walk at all.

Region->class table (label prefixes from gfxworld_probe2.mark):
  temp : draw.reflectionProbes / draw.lightmaps / outdoorImage
  seg  : everything else (aligns: sunLight 16 per AllocOutOfBlock<GfxLight>(16);
         nodes 2 (u16); the rest 4; strings/byte runs 1 — kept coarse at the
         marked-region granularity, which anchor-validates, see below)

The residual (raid: 10,402,376 - 105,045 pre-GfxWorld drift - 10,234,048
image class = 63,283) is fit empirically per zone against absolute anchors
(clipMap-header planes alias = rt(dpvsPlanes.planes), blind-derivable) as
`interior_residual` — where it sits is refined by the anchor E-profile.
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

# marked-region label prefix -> class
TEMP_PREFIX = ('draw.reflectionProbes', 'draw.lightmaps', 'outdoorImage')
ALIGN = {
    'sunLight': 16,                    # AllocOutOfBlock<GfxLight>(16)
    'dpvsPlanes.nodes': 2,             # Alloc<uint16>(2)
}


def pc_regions(d, off):
    """Walk the PC GfxWorld at `off` with gfxworld_probe2 (hooked mark) and
    return (end_abs, [(label, rel_start, rel_end)]) incl. the leading body."""
    cfg = dict(G2.CFG['pc'])
    cfg['body'] = off
    p = G2.W.__new__(G2.W)
    p.c = cfg
    p.d = d
    p.e = cfg['endian']
    p.b = off
    p.o = off + cfg['bodysize']
    marks = []
    p.mark = lambda label, note='': marks.append((label, p.o))
    with contextlib.redirect_stdout(io.StringIO()):
        G2.walk(p)
    regions = [('body', 0, cfg['bodysize'])]
    prev = off + cfg['bodysize']
    for (label, cur) in marks:
        if cur > prev:
            regions.append((label, prev - off, cur - off))
        prev = cur
    return p.o, regions


def gfxworld_pc_events(d, off, interior_residual=0, matmem_residual=0):
    """(end_abs, events) for the PC GfxWorld: 'seg'/'temp' events per marked
    region + residual 'skip's (the per-zone unexplained non-virtual remainder,
    fit from anchors; NEGATIVE = extra non-virtual).

    `matmem_residual` (E@matmem, part B session 2) localizes the residual at
    the materialMemory ARRAY: the dpvs.surfaces material aliases (x5,281 raid)
    are AddPointerLookup FIELD dedups targeting materialMemory[k].material —
    all zones show every entry referenced at exact stride 8, so a single skip
    of E@matmem just before the region makes the whole family exact. E is
    blind-derivable per zone (min distinct surface-alias minus model rt of
    the array start: raid 3,144 / dock 3,628 / skate 2,312). The trailing
    skip is interior_residual - matmem_residual so the gfx-END total (the
    GWMP-plateau-anchored gfx_residual_pc) is unchanged."""
    end, regions = pc_regions(d, off)
    events = []
    for (label, lo, hi) in regions:
        if matmem_residual and label.startswith('materialMemory'):
            events.append(('skip', matmem_residual, 1))
        if label == 'body' or any(label.startswith(t) for t in TEMP_PREFIX):
            # body = the GfxWorld asset root (TEMP); image regions = TEMP
            events.append(('temp', lo, hi - lo))
        else:
            align = next((a for k, a in ALIGN.items() if label.startswith(k)), 4)
            events.append(('seg', lo, hi - lo, align))
    if interior_residual or matmem_residual:
        events.append(('skip', interior_residual - matmem_residual, 1))
    return end, events


def co_regions(d, off):
    """Console (Wii U, BE) GfxWorld region walk at `off` (G2 CFG['wiiu'],
    hooked mark; same shape as pc_regions). The walk end can precede the
    asset span end (SSkinShaders GX2 tail; dockside adds ~12.45 MB of
    unmodeled gfx content) — the caller covers the remainder linearly."""
    cfg = dict(G2.CFG['wiiu'])
    cfg['body'] = off
    p = G2.W.__new__(G2.W)
    p.c = cfg
    p.d = d
    p.e = '>'
    p.b = off
    p.o = off + cfg['bodysize']
    marks = []
    p.mark = lambda label, note='': marks.append((label, p.o))
    with contextlib.redirect_stdout(io.StringIO()):
        G2.walk(p)
    regions = [('body', 0, cfg['bodysize'])]
    prev = off + cfg['bodysize']
    for (label, cur) in marks:
        if cur > prev:
            regions.append((label, prev - off, cur - off))
        prev = cur
    return p.o, regions


def gfxworld_console_events(d, off, span_end, planes_skip=0, matmem_skip=0,
                            end_residual=0, regions=None):
    """(end_abs, events) for a CONSOLE GfxWorld occupying [off, span_end):
    linear 'seg' regions from the G2 walk plus runtime-virtual knob 'skip's
    (part B session 2, HANDOFF item 5 — the console-side interior model):
      planes_skip  : runtime-virtual allocated BEFORE dpvsPlanes.planes
                     (raid 749,115 — the known constant; dock 471,012);
                     GX2/DPVS runtime allocs, no file bytes.
      matmem_skip  : additional runtime-virtual between planes and the
                     materialMemory array (raid +234,650; dock has NO inline
                     matmem materials — no band needed).
      end_residual : remainder so the gfx-END total equals the (independently
                     anchored) gfx_skip (raid -63,989 / dock +93,972).
    File bytes all consume virtual 1:1 (unlike the PC side, the console
    keeps image pixels in block 5 as far as every measured anchor shows —
    the E profile is monotone-increasing runtime extras, not temps).
    Any walk shortfall vs span_end (SSkinShaders/dock tail) is one linear
    trailing seg."""
    if regions is None:
        _, regions = co_regions(d, off)
    events = []
    cur = 0
    span = span_end - off
    for (label, lo, hi) in regions:
        if planes_skip and label.startswith('dpvsPlanes.planes'):
            events.append(('skip', planes_skip, 1))
            planes_skip = 0
        if matmem_skip and label.startswith('materialMemory'):
            events.append(('skip', matmem_skip, 1))
            matmem_skip = 0
        events.append(('seg', lo, hi - lo, 1))
        cur = hi
    if cur < span:
        events.append(('seg', cur, span - cur, 1))
    if end_residual or planes_skip or matmem_skip:
        # un-placed knobs (region missing on this zone) collapse into the end
        events.append(('skip', end_residual + planes_skip + matmem_skip, 1))
    return span_end, events


def _selfcheck():
    import loader_sim as LS
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import raid_oracle_control as RC
    for label, path, pol in (('raid', '../PC ff/mp_raid.zone', RC.PC_POLICY),
                             ('dock', RC.DOCK_PC, RC.DOCK_PC_POLICY),
                             ('skate', '../mp_skate_pc.zone', {})):
        em, spans, PC = LS.simulate_pc(path, policy=pol)
        gw = [(s, e) for (i, nm, root, s, e) in spans
              if root == 'GfxWorld' and e > s][0]
        end, ev = gfxworld_pc_events(PC, gw[0])
        segs = sum(e2[2] for e2 in ev if e2[0] == 'seg')
        temps = sum(e2[2] for e2 in ev if e2[0] == 'temp')
        span = gw[1] - gw[0]
        print('gfx[%s]: end %s span=%d seg=%d temp=%d (%d events)' %
              (label, 'OK' if end == gw[1] else
               'MISMATCH ev=0x%x ref=0x%x' % (end, gw[1]), span, segs, temps,
               len(ev)))


if __name__ == '__main__':
    _selfcheck()
