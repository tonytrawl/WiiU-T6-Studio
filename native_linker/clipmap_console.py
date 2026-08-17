#!/usr/bin/env python3
"""
Console-side clipMap_t span (chase findings §3: PC/console layouts IDENTICAL).
Mirror of clipmap_pc.py with endian '>' and the console inline-Material consumer
(constraint rope materials in zm zones). Extent only — replaces the truncating
ZoneCode/struct_layout walk in loader_sim's genuine-zone simulation, which
under-consumed ~2.17 MB and desynced every span after the clipMap asset.
"""
import io
import contextlib
import clipmap_probe as CP
import xmodel_probe as XP

_SIZES = dict(cLeafBrushNode_s=20, cbrush_t=96, cStaticModel_s=84,
              cLeaf_s=44, CollisionPartition=16, CollisionAabbTree=32, cmodel_t=76)

# true console/PC root size: 332 (struct_layout reports 328 — drops triIndices)
CLIPMAP_ROOT = 332


def _mat_span(d, o):
    """Span of one inline CONSOLE Material (rope-constraint materials)."""
    c = XP.Cur(d, o)
    XP.consume_material(d, c)
    return c.o


def parse_clipmap_console(d, off):
    with contextlib.redirect_stdout(io.StringIO()):
        c = CP.walk(d, off, _SIZES, '>', mat_span=_mat_span)
    return c.o
