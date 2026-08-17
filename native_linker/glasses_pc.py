#!/usr/bin/env python3
"""
PC-side Glasses span parser (HANDOFF Track E dispatch). Order from OAT codegen
glasses_t6_write_db.cpp:
  Glasses: body 56 (name@0 str, numGlasses@4, glasses@8) -> name string -> glasses[numGlasses]×140 ->
    per glass (Write_Glass): glassDef@16 (if FOLLOW -> inline GlassDef), then outline@80 (if FOLLOW ->
    numOutlineVerts@77 × vec2_t(8)).
  GlassDef: body 60 -> name@0, crackSound@40, shatterShound@44, autoShatterShound@48 strings (in that
    order). pristine/cracked/shardMaterial + crack/shatterEffect are asset refs (aliased; consume 0).
"""
import struct
import material_convert as MC
import fx_pc

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


class Fail(Exception):
    pass


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]

def _cstr(d, o):
    return d.index(b'\x00', o) + 1


def parse_glasses_pc(d, off):
    o = off + 56
    if _u32(d, off + 0) in PTRS:                    # Glasses name
        o = _cstr(d, o)
    num = _u32(d, off + 4)
    if _u32(d, off + 8) in PTRS:                    # glasses array
        gbase = o
        o += num * 140
        for i in range(num):
            gb = gbase + i * 140
            if _u32(d, gb + 16) in PTRS:            # inline GlassDef (reusable)
                gd = o
                o += 60
                # GlassDef write order (codegen): name, pristine/cracked/shardMaterial (inline
                # Material), crackSound/shatterShound/autoShatterShound, crack/shatterEffect (inline FX).
                if _u32(d, gd + 0) in PTRS:
                    o = _cstr(d, o)                # name
                for mo in (28, 32, 36):            # pristine/cracked/shard Material (inline)
                    if _u32(d, gd + mo) in PTRS:
                        _, o = MC.convert_material(d, o)
                for so in (40, 44, 48):            # crackSound/shatterShound/autoShatterShound
                    if _u32(d, gd + so) in PTRS:
                        o = _cstr(d, o)
                for fo in (52, 56):                # crack/shatterEffect (inline FxEffectDef)
                    if _u32(d, gd + fo) in PTRS:
                        o, _ = fx_pc.parse_fx_pc(d, o)
            if _u32(d, gb + 80) in PTRS:            # outline: numOutlineVerts × vec2_t(8)
                o += d[gb + 77] * 8
    return o
