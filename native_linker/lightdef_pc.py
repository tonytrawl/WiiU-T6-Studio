#!/usr/bin/env python3
"""
PC-side GfxLightDef span parser (HANDOFF Track E dispatch). GfxLightDef = 16 B body
(name@0 str, attenuation{image@4, samplerState@8}, lmapLookupStart@12). Dynamic: name string
(if name@0 FOLLOW) then, if attenuation.image@4 is FOLLOW/INSERT, an INLINE GfxImage (the light
cookie) — which the generic walker misses (it stops at the 16-B body). The inline image span reuses
material_convert.pc_image_span (self-locating body + loadDef tail).
"""
import struct
import material_convert as MC

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def parse_lightdef_pc(d, off):
    o = off + 16
    if struct.unpack_from('<I', d, off + 0)[0] in PTRS:      # name string
        o = d.index(b'\x00', o) + 1
    if struct.unpack_from('<I', d, off + 4)[0] in PTRS:      # attenuation.image -> inline GfxImage
        o = MC.pc_image_span(d, o)
    return o
