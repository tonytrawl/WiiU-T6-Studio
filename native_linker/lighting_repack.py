#!/usr/bin/env python3
"""
LIGHTING REPACK (POLISH session, 2026-07-10) — root cause of the "renders darker" defect.

Two GfxWorld vertex-stream conversions were wrong in gfxworld_dynamics:

1. vd0 TANGENT (@32..36 of the 36B world vertex): NOT a plain swap2. The console re-packs the
   PC 10:10:10 tangent with a ONE-BIT shift that carries ACROSS the two u16 lanes:
       clo = (lo >> 1) | (hi15 << 15)          # PC low u16  -> console 1st u16 (BE)
       chi = (hi << 1) | lo0                   # PC high u16 -> console 2nd u16 (BE)
   (lo/hi = the two LE u16s of the PC tangent dword; hi15/lo0 = their top/bottom bits.)
   Validated byte-exact on ALL verts: mp_raid 162,752/162,752, mp_dockside (see validator).
   Under the old swap2 only 0.73% of tangents were correct -> broken tangent frames on
   every normal-mapped world surface = the lighting-level render defect.

2. vd1 is NOT a uniform stride-4 swap2 stream. It is per-surface-group ELEMENTS of stride
   4/8/12/16 (1..3 f16 lightmap-UV layer words + optionally ONE trailing RGBA8 vertex-color
   word). UV words = swap2; the color word = VERBATIM bytes (no swap). The old flat swap2
   byte-reversed every color word (wrong vertex tint) and mis-swapped nothing else.
   Group table: from the PC surface table (vertexDataOffset1@28) + index buffer
   (vc = max group index + 1); stride = group extent / vc.
   Last-column type (UV vs color): f16-plausibility MAJORITY VOTE over the column
   (validated 111/111 decisive groups on mp_raid; see validator for dockside).

Integration (assemble session):
  * gfxworld_dynamics.conv_world_vertex: replace the (32,34) swap2 with conv_tangent() below —
    or simply route vd0 through conv_world_vertex_grouped with this module's conv_world_vertex36.
  * gfxworld_assemble: route the 2nd 'draw.vd.data' span to conv_vd1(pc_vd1, groups) where
    groups = vd1_groups(...) built alongside the existing vd0_groups (same surface/index spans).
"""
import struct

WORLD_VERT_STRIDE = 36


# ---------------------------------------------------------------- vd0 tangent
def conv_tangent(pc4):
    """PC tangent dword (4 bytes LE) -> console 4 bytes (BE u16 pair), cross-carry 1-bit shift."""
    lo, hi = struct.unpack('<HH', pc4)
    clo = ((lo >> 1) | ((hi >> 15) << 15)) & 0xFFFF
    chi = ((hi << 1) | (lo & 1)) & 0xFFFF
    return struct.pack('>HH', clo, chi)


def conv_world_vertex36(pc_bytes):
    """Full 36B world-vertex conversion (drop-in for gfxworld_dynamics.conv_world_vertex):
    pos/w f32 swap4, color verbatim, normal 2xu16 swap2, uv 2xf32 swap4, tangent cross-carry."""
    n = len(pc_bytes)
    out = bytearray(pc_bytes)
    for b in range(0, n - WORLD_VERT_STRIDE + 1, WORLD_VERT_STRIDE):
        for o in (0, 4, 8, 12, 24, 28):
            out[b + o:b + o + 4] = pc_bytes[b + o:b + o + 4][::-1]
        for o in (20, 22):
            out[b + o:b + o + 2] = pc_bytes[b + o:b + o + 2][::-1]
        out[b + 32:b + 36] = conv_tangent(pc_bytes[b + 32:b + 34 + 2])
    return bytes(out)


# ---------------------------------------------------------------- vd1
def vd1_groups(pc, surf_off, nsurf, idx, nidx, vd1_size):
    """Group table for vd1 from the PC surface span. Returns sorted list of
    (offset, stride, element_count). Groups whose extent is not an integral 4/8/12/16
    stride (offset-sharing subranges; a handful per map) are skipped — their bytes are
    covered by the enclosing group or are inter-group padding handled by the caller."""
    g = {}
    for i in range(nsurf):
        po = surf_off + i * 80
        o1 = struct.unpack_from('<I', pc, po + 28)[0]
        tri = struct.unpack_from('<H', pc, po + 42)[0]
        bi = struct.unpack_from('<I', pc, po + 44)[0]
        if tri and bi + tri * 3 <= nidx:
            vc = max(idx[bi:bi + tri * 3]) + 1
            if vc > g.get(o1, 0):
                g[o1] = vc
    offs = sorted(g)
    out = []
    for i, off in enumerate(offs):
        end = offs[i + 1] if i + 1 < len(offs) else vd1_size
        ext = end - off
        vc = g[off]
        if vc and ext % vc == 0 and ext // vc in (4, 8, 12, 16):
            out.append((off, ext // vc, vc))
    return out


def _word_uv_plausible(p4):
    """A 4-byte word looks like an f16 UV pair: each half exact +-0 or a normal f16 with
    exponent field in [5, 17] (|x| roughly in [2^-10, 8)); calibrated on raid+dockside:
    5 admits the smallest genuine lightmap UVs (exp 6) while 17 rejects color-byte patterns
    whose halves alias to large-magnitude f16s (gray RGBA aliases to |x| in the tens+)."""
    for h in struct.unpack('<HH', p4):
        if h in (0x0000, 0x8000):
            continue
        e = (h >> 10) & 0x1F
        if not (5 <= e <= 17):
            return False
    return True


def col_is_color(pc_vd1, off, stride, count, col):
    """Majority vote over one column of a group: color (verbatim) iff <=50% of the words are
    f16-plausible. Column 0 is always a UV layer (never voted). Observed layouts (raid+dockside):
    s4=[UV]; s8=[UV,UV]|[UV,color]; s12=[UV,UV,UV]|[UV,UV,color]; s16=[UV,UV,color,color]."""
    passn = 0
    for v in range(count):
        a = off + v * stride + col * 4
        if _word_uv_plausible(pc_vd1[a:a + 4]):
            passn += 1
    return passn * 2 <= count


def conv_vd1(pc_vd1, groups, col_override=None):
    """PC vd1 stream -> console. groups from vd1_groups().
    Per group: column 0 = UV (swap2); each later column voted UV vs color independently.
    Bytes not covered by any group (inter-group padding / skipped subrange groups): per-WORD
    vote — swap2 if the word is f16-plausible, verbatim otherwise.
    col_override: optional {(offset, col): is_color_bool} to force a column class per group
    (e.g. from an oracle or from material/techset knowledge)."""
    n = len(pc_vd1)
    out = bytearray(pc_vd1)
    covered = bytearray(n)
    for off, stride, count in groups:
        ncol = stride // 4
        colmap = []
        for c in range(ncol):
            if c == 0:
                colmap.append(False)
            elif col_override and (off, c) in col_override:
                colmap.append(col_override[(off, c)])
            else:
                colmap.append(col_is_color(pc_vd1, off, stride, count, c))
        for v in range(count):
            for c in range(ncol):
                a = off + v * stride + c * 4
                if a + 4 > n:
                    break
                covered[a:a + 4] = b'\x01\x01\x01\x01'
                if colmap[c]:
                    out[a:a + 4] = pc_vd1[a:a + 4]
                else:
                    out[a:a + 2] = pc_vd1[a:a + 2][::-1]
                    out[a + 2:a + 4] = pc_vd1[a + 2:a + 4][::-1]
    # uncovered words: per-word vote
    i = 0
    while i + 4 <= n:
        if not covered[i]:
            w = pc_vd1[i:i + 4]
            if _word_uv_plausible(w):
                out[i:i + 2] = w[0:2][::-1]
                out[i + 2:i + 4] = w[2:4][::-1]
            else:
                out[i:i + 4] = w
        i += 4
    return bytes(out)
