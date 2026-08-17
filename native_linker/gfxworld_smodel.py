#!/usr/bin/env python3
"""
dpvs.smodelDrawInsts converter (Track F, Bucket B): PC 152B -> console 208B.

Layout derived from the raid matched pair (2026-07-10, supersedes the handoff's
PC tail offsets which were off):
  PC GfxStaticModelDrawInst (152B, LE):
    @0 cullDist f32; @4 GfxPlacement {origin vec3, axis[3][3] row-major, scale
    f32} (52B); @56 model*; @60 flags u32; @64 invScaleSq f32; @68 lightingHandle
    u16; @70 colorsIndex u16; @72 lightingSH[24]; @96 {primaryLightIndex u8,
    visibility u8, reflectionProbeIndex u8, pad}; @100 smid u32; @104
    lmapVertexInfo[4] x 12B {ptr, u32, numColors u32}.
  Console (208B, BE):
    @0 cullDist; @4 origin vec3; @16 axis[3] PACKED rows — each row is a
    10:10:10 packed unit vector, field = round(c*511) two's-complement (SOLVED:
    identity row0 = 0x1ff, 0.2672/0.9636 -> 137/492 exact); @28 scale f32;
    @32 model*; @36 flags; @40 invScaleSq; @44 lightingHandle u16; @46
    colorsIndex u16; @48 lightingSH[24]; @72 {pl, vis, probeIdx, pad} VERBATIM;
    @76 smid 4 bytes VERBATIM (kept LE!); @80 lmapVertexInfo[4] x 32B
    {ptr u32, zeros[20], numColors u16 @+24, pad[6]}.
  Trailing lmapVertexColors (count*4 per FOLLOW entry): VERBATIM (byte-equal
  regions on raid).
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def _is_alias(v):
    return 0xA0000000 <= v < 0xC0000000


def pack_row(x, y, z):
    def q(c):
        v = int(round(c * 511.0))
        v = max(-512, min(511, v))
        return v & 0x3FF
    return q(x) | (q(y) << 10) | (q(z) << 20)


def conv_smodel_draw_insts(pc, pstart, count, fixups=None, out_base=0):
    """Convert the full smodelDrawInsts region (array + trailing colors).
    Returns (console_bytes, pc_end)."""
    if fixups is None:
        fixups = []
    out = bytearray(count * 208)
    for i in range(count):
        pb = pstart + i * 152
        cb = i * 208
        out[cb:cb + 4] = pc[pb:pb + 4][::-1]                      # cullDist
        for k in range(3):                                        # origin
            out[cb + 4 + k * 4:cb + 8 + k * 4] = pc[pb + 4 + k * 4:pb + 8 + k * 4][::-1]
        ax = struct.unpack_from('<9f', pc, pb + 16)
        for r in range(3):
            struct.pack_into('>I', out, cb + 16 + r * 4,
                             pack_row(ax[r * 3], ax[r * 3 + 1], ax[r * 3 + 2]))
        out[cb + 28:cb + 32] = pc[pb + 52:pb + 56][::-1]          # scale
        model = struct.unpack_from('<I', pc, pb + 56)[0]
        struct.pack_into('>I', out, cb + 32, model)
        if _is_alias(model):
            fixups.append(out_base + cb + 32)
        out[cb + 36:cb + 40] = pc[pb + 60:pb + 64][::-1]          # flags
        out[cb + 40:cb + 44] = pc[pb + 64:pb + 68][::-1]          # invScaleSq
        out[cb + 44:cb + 46] = pc[pb + 68:pb + 70][::-1]          # lightingHandle
        out[cb + 46:cb + 48] = pc[pb + 70:pb + 72][::-1]          # colorsIndex
        for k in range(0, 24, 4):                                 # lightingSH
            out[cb + 48 + k:cb + 52 + k] = pc[pb + 72 + k:pb + 76 + k][::-1]
        out[cb + 72:cb + 76] = pc[pb + 96:pb + 100]               # pl/vis/probe/pad VERBATIM
        out[cb + 76:cb + 80] = pc[pb + 100:pb + 104]              # smid VERBATIM
        for e in range(4):
            po = pb + 104 + e * 12
            co = cb + 80 + e * 32
            ptr = struct.unpack_from('<I', pc, po)[0]
            struct.pack_into('>I', out, co, ptr)
            if _is_alias(ptr):
                fixups.append(out_base + co)
            n = struct.unpack_from('<H', pc, po + 8)[0]
            struct.pack_into('>H', out, co + 24, n)
    # trailing lmapVertexColors: verbatim
    o = pstart + count * 152
    tail_len = 0
    for i in range(count):
        pb = pstart + i * 152
        for e in range(4):
            po = pb + 104 + e * 12
            if struct.unpack_from('<I', pc, po)[0] in PTRS:
                tail_len += struct.unpack_from('<H', pc, po + 8)[0] * 4
    out += pc[o:o + tail_len]
    return bytes(out), o + tail_len
