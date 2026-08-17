#!/usr/bin/env python3
"""
PC-side MaterialTechniqueSet span parser (HANDOFF Track E dispatch). Extents ONLY — no GX2/shader
conversion (that's Track B, which reuses this to LOCATE the inline DXBC shaders).

Emission order taken from the OAT codegen (ground truth, not byte-reversed):
tools/ref_oat/build/src/ZoneCode/Game/T6/XAssets/materialtechniqueset/materialtechniqueset_t6_write_db.cpp

  MaterialTechniqueSet: body 152 (name@0 str, worldVertFormat@4, techniques[36]@8) -> name string ->
    for each of 36 slots (FOLLOW): Align(4) then MaterialTechnique.
  MaterialTechnique: body = 8 (name@0,flags@4,passCount@6) + passArray[passCount]×24 (contiguous) ->
    per pass, IN THIS ORDER: vertexShader, vertexDecl(116), pixelShader, args -> technique name string LAST.
    (Align(4) before each shader/decl/args; NOT before name strings.)
  Material{Vertex,Pixel}Shader: body 16 (name@0 str, prog@4 -> loadDef program@8 ptr, programSize@12)
    -> name string -> program bytecode (programSize B, Align(1)=byte-packed).
  args: MaterialShaderArgument×argc (12 B each; type@0, u@8). For type LITERAL_VERTEX/PIXEL_CONST
    (1 / 7) with u.literalConst@8 FOLLOW: Align(4) + float[4] (16 B).
PC has 36 technique slots (console 32). Aliased ptrs (≠FOLLOW) consume 0 bytes.
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

TS_BODY = 152
TECH_HDR = 8
PASS = 24
ARG = 12
VDECL = 116
MTL_ARG_LITERAL = (1, 7)     # MTL_ARG_LITERAL_VERTEX_CONST / _PIXEL_CONST


class Fail(Exception):
    pass


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]

def _u16(d, o):
    return struct.unpack_from('<H', d, o)[0]

def _a4(o):
    return o  # no alignment: technique-tree elements are stream-packed (Align is per-block)


def _shader_span(d, o):
    """Material{Vertex,Pixel}Shader at o (already aligned) -> end."""
    name_ptr = _u32(d, o + 0)
    prog_ptr = _u32(d, o + 8)
    prog_size = _u32(d, o + 12)
    e = o + 16
    if name_ptr in PTRS:                            # shader name string
        e = d.index(b'\x00', e) + 1
    if prog_ptr in PTRS:                            # DXBC program bytecode (Align(1) = byte-packed)
        e += prog_size
    return e


def _technique_span(d, tb):
    """MaterialTechnique at tb (already aligned) -> end."""
    pass_count = _u16(d, tb + 6)
    o = tb + TECH_HDR + pass_count * PASS
    for p in range(pass_count):
        pb = tb + TECH_HDR + p * PASS
        if _u32(d, pb + 4) in PTRS:                 # vertexShader
            o = _shader_span(d, _a4(o))
        if _u32(d, pb + 0) in PTRS:                 # vertexDecl
            o = _a4(o) + VDECL
        if _u32(d, pb + 8) in PTRS:                 # pixelShader
            o = _shader_span(d, _a4(o))
        if _u32(d, pb + 20) in PTRS:                # args
            o = _a4(o)
            argc = d[pb + 12] + d[pb + 13] + d[pb + 14]
            abase = o
            o += argc * ARG
            for a in range(argc):
                ab = abase + a * ARG
                if _u16(d, ab + 0) in MTL_ARG_LITERAL and _u32(d, ab + 8) in PTRS:
                    o = _a4(o) + 16                  # literalConst float[4]
    if _u32(d, tb + 0) in PTRS:                     # technique name string (LAST, no align)
        o = d.index(b'\x00', o) + 1
    return o


def parse_techset_pc(d, off):
    """MaterialTechniqueSet at off -> end offset."""
    o = off + TS_BODY
    if _u32(d, off + 0) in PTRS:                     # techset name string
        o = d.index(b'\x00', o) + 1
    for i in range(36):                              # 36 technique slots
        if _u32(d, off + 8 + i * 4) in PTRS:
            o = _technique_span(d, _a4(o))
    return o
