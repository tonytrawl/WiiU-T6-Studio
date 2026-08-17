#!/usr/bin/env python3
"""
PC-side FxEffectDef span parser (HANDOFF Track E) — the missing PC probe that lets a PC map zone
be walked past FX. FxEffectDef (76 B) and FxElemDef (292 B) are byte-IDENTICAL in layout on PC and
console, so this mirrors wiiu_ref/fx_probe.parse_fx exactly but reads little-endian and dispatches
inline sub-assets to the PC converters. Returns (end_offset, name).

Inline visuals in a *map* zone are almost always ALIASED (shared from common), so the inline-asset
paths are rarely taken; when a material IS inline it is measured via material_convert (which consumes
exactly one PC material). Inline model/other-asset visuals raise Fail (need the XModel surface span,
tracked with Track C surfaces).
"""
import struct
import material_convert as MC

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
ED = 292                      # FxElemDef size (both platforms)
HDR = 76                      # FxEffectDef header (name string follows)

TYPE_MODEL = 7
TYPE_SPOT_LIGHT = 9
TYPE_SOUND = 10
TYPE_DECAL = 11
TYPE_RUNNER = 12
TYPE_TRAIL = 5


class Fail(Exception):
    pass


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]

def _i16(d, o):
    return struct.unpack_from('<h', d, o)[0]


class Cur:
    def __init__(self, d, o):
        self.d = d; self.o = o
    def skip(self, n):
        self.o += n
    def cstr(self, maxlen=128):
        e = self.d.index(b'\x00', self.o)
        if e - self.o > maxlen:
            raise Fail('string too long')
        s = self.d[self.o:e]; self.o = e + 1
        return s.decode('latin-1', 'replace')


def _material_span(d, c):
    """Consume one inline PC material, advancing c.o (uses the Track A converter's cursor)."""
    _, nxt = MC.convert_material(d, c.o)
    c.o = nxt


def _visual_dyn(d, c, vptr, etype):
    if vptr not in PTRS:
        return
    if etype in (TYPE_SOUND, TYPE_RUNNER):
        c.cstr()
    elif etype <= 6:                       # sprite/tail/line/trail/cloud -> inline material
        _material_span(d, c)
    elif etype == TYPE_MODEL:
        raise Fail('inline model visual (needs PC XModel surface span — Track C)')
    else:
        raise Fail('inline visual asset (type %d)' % etype)


def _parse_elem_dyn(d, eb, c):
    etype = d[eb + 184]
    vcount = d[eb + 185]
    vic = d[eb + 186]
    vsc = d[eb + 187]
    if _u32(d, eb + 188) in PTRS:
        c.skip((vic + 1) * 96)             # velSamples
    if _u32(d, eb + 192) in PTRS:
        c.skip((vsc + 1) * 48)             # visSamples
    vis = _u32(d, eb + 196)
    if etype == TYPE_DECAL:
        if vis in PTRS:
            mb = c.o
            c.skip(vcount * 8)             # FxElemMarkVisuals: 2 material ptrs
            for i in range(vcount):
                for k in (0, 4):
                    if _u32(d, mb + i * 8 + k) in PTRS:
                        _material_span(d, c)
    elif vcount > 1:
        if vis in PTRS:
            ab = c.o
            c.skip(vcount * 4)             # FxElemVisuals array
            for i in range(vcount):
                _visual_dyn(d, c, _u32(d, ab + i * 4), etype)
    else:
        _visual_dyn(d, c, vis, etype)
    for off in (224, 228, 232):            # onImpact/onDeath/emitted refs
        if _u32(d, eb + off) in PTRS:
            c.cstr()
    if _u32(d, eb + 252) in PTRS:          # effectAttached
        c.cstr()
    ext = _u32(d, eb + 256)
    if ext in PTRS:
        if etype == TYPE_TRAIL:
            tb = c.o
            c.skip(28)                     # FxTrailDef: vertCount@12 verts@16 indCount@20 inds@24
            vc_, ic_ = _u32(d, tb + 12), _u32(d, tb + 20)
            if _u32(d, tb + 16) in PTRS:
                c.skip(vc_ * 20)           # FxTrailVertex = 20
            if _u32(d, tb + 24) in PTRS:
                c.skip(ic_ * 2)
        elif etype == TYPE_SPOT_LIGHT:
            c.skip(12)
        else:
            c.skip(1)                      # unknownDef: single char (OAT Load<char>, sizeof=1)
    if _u32(d, eb + 280) in PTRS:          # spawnSound
        c.cstr()


def parse_fx_pc(d, b):
    """Full PC FxEffectDef span from body offset b. Returns (end, name)."""
    c = Cur(d, b + HDR)
    name = c.cstr() if _u32(d, b) in PTRS else '<alias>'
    n = _i16(d, b + 8) + _i16(d, b + 10) + _i16(d, b + 12)
    if _u32(d, b + 28) in PTRS:
        base = c.o
        c.skip(n * ED)
        for i in range(n):
            _parse_elem_dyn(d, base + i * ED, c)
    return c.o, name
