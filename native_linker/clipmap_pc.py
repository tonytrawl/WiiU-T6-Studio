#!/usr/bin/env python3
"""
PC-side clipMap_t span (HANDOFF Track E dispatch). clipMap_t is PC-identical (332-B body), so this
reuses wiiu_ref/clipmap_probe.walk with endian '<' (the probe was parameterised for endian, default
'>'). Extent only. struct sizes are the console/PC-shared values (clipMap sub-structs don't diverge).
"""
import io
import contextlib
import clipmap_probe as CP
import material_convert as MC

_SIZES = dict(cLeafBrushNode_s=20, cbrush_t=96, cStaticModel_s=84,
              cLeaf_s=44, CollisionPartition=16, CollisionAabbTree=32, cmodel_t=76)


def _mat_span(d, o):
    """Span of one inline PC Material (rope-constraint materials in zm zones)."""
    return MC.convert_material(d, o)[1]


def parse_clipmap_pc(d, off):
    with contextlib.redirect_stdout(io.StringIO()):
        c = CP.walk(d, off, _SIZES, '<', mat_span=_mat_span)
    return c.o
