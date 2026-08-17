#!/usr/bin/env python3
"""
Task #19 / WP-E: GX2/Latte encoding of the console GfxPackedVertex (Wii U T6).

The 32-byte PC GfxPackedVertex is split on console into two GX2 vertex streams:

verts0, 24 bytes per vertex (positions + frame):
  +0  position   3 x BE float32               (= PC xyz, 4-byte-swapped)
  +12 normal     3 x BE snorm16               (unit normal, n = s16 / 32768)
  +18 binormal   1 x BE snorm16               (PC binormalSign: +1 -> 0x7fff,
                                               -1 -> 0x8000; only these occur)
  +20 tangent    3 x snorm8                   (unit tangent, t = s8 / 128)
  +23 pad        1 byte, always 0x00
  GX2 fetch view: R32G32B32_FLOAT + R16G16B16A16_SNORM + R8G8B8A8_SNORM.

verts1, 8 bytes per vertex (material stream):
  +0  texcoord   2 x BE float16, order (u, v) (= the two halves of PC
                                               PackedTexCoords: u = low 16,
                                               v = high 16 of the PC u32)
  +4  color      4 x u8, byte order r,g,b,a   (byte-identical to the PC
                                               GfxColor u32 as stored LE,
                                               i.e. a straight 4-byte copy)
  GX2 fetch view: R16G16_FLOAT + R8G8B8A8_UNORM.

Quantizer (matches genuine data best; original linker truncated):
  s16 = clamp(trunc(x * 32768), -32768, 32767)
  s8  = clamp(trunc(x * 128),   -128,   127)
Console normals/tangents were quantized from the ORIGINAL full-precision
floats, so re-encoding from PC's lossy 10-bit ThirdBased values reproduces
the genuine bytes exactly only where the 10-bit value is exact (axis-aligned
and simple directions); elsewhere it is within 1-2 quantizer steps.
Position, texcoord, color and binormal sign are exactly derivable from PC.

Verification (this file's main):
  mode 'pair'   : align PC ff/mp_raid.zone to wiiu_ref/mp_raid_genuine.zone
                  per shared surface and score PC->console re-encoding.
  mode 'decode' : parse every inline verts0/verts1 in a genuine console zone,
                  decode -> floats -> re-encode, require byte-exact identity
                  plus sanity (unit normal, pad 0, bs in {0x7fff,0x8000}).
"""
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VERT0_SIZE = 24
VERT1_SIZE = 8


def _f32(x):
    """Round a python float to float32 precision."""
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _enc16(x):
    return _clamp(math.trunc(x * 32768.0), -32768, 32767)


def _enc8(x):
    return _clamp(math.trunc(x * 128.0), -128, 127)


def _dec16(s):
    return s / 32768.0


def _dec8(s):
    return s / 128.0


# ---------------------------------------------------------------- console side

def decode_vert0(b, off=0):
    """24-byte console verts0 record -> dict of floats."""
    x, y, z, nx, ny, nz, bs = struct.unpack_from('>fffhhhh', b, off)
    tx, ty, tz, pad = struct.unpack_from('>bbbB', b, off + 20)
    return {
        'pos': (x, y, z),
        'normal': (_dec16(nx), _dec16(ny), _dec16(nz)),
        'binormal_sign': 1.0 if bs >= 0 else -1.0,
        'tangent': (_dec8(tx), _dec8(ty), _dec8(tz)),
        'pad': pad,
    }


def encode_vert0(pos, normal, binormal_sign, tangent):
    """Floats -> 24-byte console verts0 record."""
    return struct.pack(
        '>fffhhhhbbbB',
        pos[0], pos[1], pos[2],
        _enc16(normal[0]), _enc16(normal[1]), _enc16(normal[2]),
        32767 if binormal_sign >= 0 else -32768,
        _enc8(tangent[0]), _enc8(tangent[1]), _enc8(tangent[2]),
        0)


def decode_vert1(b, off=0):
    """8-byte console verts1 record -> dict (uv as half-float bit patterns
    left to the caller to interpret; color as (r,g,b,a) bytes)."""
    uh, vh = struct.unpack_from('>HH', b, off)
    r, g, b_, a = struct.unpack_from('BBBB', b, off + 4)
    return {'uv_half': (uh, vh), 'color': (r, g, b_, a)}


def encode_vert1(uv_half, color):
    """(u_half16, v_half16), (r,g,b,a) -> 8-byte console verts1 record."""
    return struct.pack('>HHBBBB', uv_half[0], uv_half[1], *color)


# --------------------------------------------------------------------- PC side

def pc_unpack_unitvec(u):
    """T6 PC ThirdBased 10-10-10 unit vec -> 3 float32 (unnormalized,
    length is about 2; float32-exact replica of the game decode)."""
    out = []
    for sh in (0, 10, 20):
        f = (u >> sh) & 0x3ff
        raw = (f - 2 * (f & 0x200) + 0x40400000) & 0xffffffff
        fv = struct.unpack('<f', struct.pack('<I', raw))[0]
        out.append(_f32(_f32(fv - 3.0) * _f32(8208.0312)))
    return out


def pc_pack_unitvec(v):
    """3 floats -> T6 PC ThirdBased 10-10-10 (game encode, scale about x2:
    a unit input packs to fields of about +-255; genuine PC zones store
    length-2 vectors, i.e. fields up to +-511)."""
    out = 0
    for i, sh in enumerate((0, 10, 20)):
        raw = struct.unpack(
            '<I', struct.pack('<f', _f32(_f32(v[i] - -24624.0939334638)
                                         * _f32(0.0001218318939208984))))[0]
        out |= (raw & 0x3ff) << sh
    return out


def _normalize(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if l == 0.0:
        return (0.0, 0.0, 0.0)
    return (v[0] / l, v[1] / l, v[2] / l)


def pc_vertex_to_console(pcb, off=0):
    """One 32-byte PC GfxPackedVertex -> (verts0 24B, verts1 8B).
    Positions, uv, color, binormal sign are exact; normal/tangent are
    re-quantized from PC's 10-bit values (exact where those are exact)."""
    x, y, z, bs = struct.unpack_from('<ffff', pcb, off)
    col, uv, nrm, tan = struct.unpack_from('<IIII', pcb, off + 16)
    n = _normalize(pc_unpack_unitvec(nrm))
    t = _normalize(pc_unpack_unitvec(tan))
    v0 = encode_vert0((x, y, z), n, bs, t)
    v1 = encode_vert1((uv & 0xffff, uv >> 16),
                      (col & 0xff, (col >> 8) & 0xff,
                       (col >> 16) & 0xff, col >> 24))
    return v0, v1


def console_vertex_to_pc(v0b, v1b, off0=0, off1=0):
    """(verts0 24B, verts1 8B) -> one 32-byte PC GfxPackedVertex."""
    d0 = decode_vert0(v0b, off0)
    d1 = decode_vert1(v1b, off1)
    uv = d1['uv_half'][0] | (d1['uv_half'][1] << 16)
    col = (d1['color'][0] | (d1['color'][1] << 8)
           | (d1['color'][2] << 16) | (d1['color'][3] << 24))
    # PC stores length-2 packed vectors; scale unit frame back up by 2
    n = [c * 2.0 for c in d0['normal']]
    t = [c * 2.0 for c in d0['tangent']]
    return struct.pack('<ffffIIII',
                       d0['pos'][0], d0['pos'][1], d0['pos'][2],
                       d0['binormal_sign'], col,
                       uv, pc_pack_unitvec(n), pc_pack_unitvec(t))


# ------------------------------------------------------------- verification

def _collect_console_surfaces(path):
    """All inline (verts0, verts1) blocks in a genuine console zone via the
    solved XModel/XSurface walk in xmodel_probe."""
    import xmodel_probe as XP
    surfs = []
    orig = XP.parse_surface_dyn

    def hook(d, b, c):
        vc, tc = XP.u16(d, b + 4), XP.u16(d, b + 6)
        if any(XP.u32(d, b + k) in XP.PTRS for k in (24, 32, 36, 44)):
            raise XP.Fail('skinned surface (pre-verts blob unsized)')
        v0 = c.o if XP.u32(d, b + 52) in XP.PTRS else None
        if v0 is not None:
            c.skip(vc * VERT0_SIZE)
        v1 = c.o if XP.u32(d, b + 72) in XP.PTRS else None
        if v1 is not None:
            c.skip(vc * VERT1_SIZE)
        if XP.u32(d, b + 96) in XP.PTRS:
            vlc = d[b + 1]
            base = c.o
            c.skip(vlc * 12)
            for k in range(vlc):
                if XP.u32(d, base + k * 12 + 8) in XP.PTRS:
                    tb = c.o
                    c.skip(40)
                    nc_, lc_ = XP.u32(d, tb + 24), XP.u32(d, tb + 32)
                    if XP.u32(d, tb + 28) in XP.PTRS:
                        c.skip(nc_ * 16)
                    if XP.u32(d, tb + 36) in XP.PTRS:
                        c.skip(lc_ * 2)
        if XP.u32(d, b + 12) in XP.PTRS:
            c.skip(tc * 6)
        if v0 is not None:
            surfs.append((vc, v0, v1))

    XP.parse_surface_dyn = hook
    try:
        d = open(path, 'rb').read()
        n = len(d)
        cands = [o for o in range(0, n - XP.BODY + 1, 4) if XP.is_body(d, o)]
        seen = set()
        queue = list(cands)
        qi = 0
        out = []
        while qi < len(queue):
            o = queue[qi]
            qi += 1
            if o in seen:
                continue
            seen.add(o)
            before = len(surfs)
            try:
                end, name = XP.parse_xmodel(d, o)
            except Exception:
                del surfs[before:]
                continue
            for i, s in enumerate(surfs[before:]):
                out.append((name, i) + s)
            if XP.is_body(d, end, strict=False) and end not in seen:
                queue.append(end)
    finally:
        XP.parse_surface_dyn = orig
    return d, out


def _swap4(b):
    return b''.join(b[i:i + 4][::-1] for i in range(0, len(b), 4))


def verify_decode(path):
    """Decode/re-encode identity plus sanity on every inline console vertex."""
    d, surfs = _collect_console_surfaces(path)
    nv = ns = bad_rt = bad_pad = bad_bs = bad_len = 0
    for (name, si, vc, v0, v1) in surfs:
        ns += 1
        for i in range(vc):
            nv += 1
            o = v0 + i * VERT0_SIZE
            rec = d[o:o + VERT0_SIZE]
            dec = decode_vert0(rec)
            if dec['pad'] != 0:
                bad_pad += 1
            bs = struct.unpack_from('>h', rec, 18)[0]
            if bs not in (32767, -32768):
                bad_bs += 1
            nl = math.sqrt(sum(c * c for c in dec['normal']))
            if abs(nl - 1.0) > 0.01 and nl != 0.0:
                bad_len += 1
            if encode_vert0(dec['pos'], dec['normal'], dec['binormal_sign'],
                            dec['tangent']) != rec:
                bad_rt += 1
            if v1 is not None:
                r1 = d[v1 + i * VERT1_SIZE:v1 + (i + 1) * VERT1_SIZE]
                d1 = decode_vert1(r1)
                if encode_vert1(d1['uv_half'], d1['color']) != r1:
                    bad_rt += 1
    print('%s: surfaces=%d verts=%d roundtrip_bad=%d pad_bad=%d bs_bad=%d '
          'normlen_bad=%d' % (os.path.basename(path), ns, nv, bad_rt,
                              bad_pad, bad_bs, bad_len))
    return bad_rt == 0 and bad_pad == 0 and bad_bs == 0


def verify_pair(console_zone, pc_zone):
    """Align shared surfaces by position content and score PC->console."""
    wd, surfs = _collect_console_surfaces(console_zone)
    pc = open(pc_zone, 'rb').read()
    tot_s = full_s = 0
    nv = pos_ok = v1_ok = bs_ok = n_ok = t_ok = all_ok = 0
    full_names = []
    for (name, si, vc, v0, v1) in surfs:
        if vc < 4:
            continue
        key = _swap4(wd[v0:v0 + 12])
        h = pc.find(key)
        hit = None
        while h != -1:
            if all(_swap4(wd[v0 + i * VERT0_SIZE:v0 + i * VERT0_SIZE + 12])
                   == pc[h + i * 32:h + i * 32 + 12]
                   for i in range(min(vc, 8))):
                hit = h
                break
            h = pc.find(key, h + 1)
        if hit is None:
            continue
        if not all(_swap4(wd[v0 + i * VERT0_SIZE:v0 + i * VERT0_SIZE + 12])
                   == pc[hit + i * 32:hit + i * 32 + 12] for i in range(vc)):
            continue
        tot_s += 1
        surf_full = True
        for i in range(vc):
            nv += 1
            ev0, ev1 = pc_vertex_to_console(pc, hit + i * 32)
            g0 = wd[v0 + i * VERT0_SIZE:v0 + (i + 1) * VERT0_SIZE]
            g1 = (wd[v1 + i * VERT1_SIZE:v1 + (i + 1) * VERT1_SIZE]
                  if v1 is not None else None)
            if ev0[:12] == g0[:12]:
                pos_ok += 1
            if g1 is None or ev1 == g1:
                v1_ok += 1
            if ev0[18:20] == g0[18:20]:
                bs_ok += 1
            if ev0[12:18] == g0[12:18]:
                n_ok += 1
            if ev0[20:24] == g0[20:24]:
                t_ok += 1
            if ev0 == g0 and (g1 is None or ev1 == g1):
                all_ok += 1
            else:
                surf_full = False
        if surf_full:
            full_s += 1
            full_names.append('%s#%d' % (name, si))
    print('%s vs %s:' % (os.path.basename(console_zone),
                         os.path.basename(pc_zone)))
    print('  aligned surfaces=%d verts=%d' % (tot_s, nv))
    if nv:
        print('  byte-exact: pos %.2f%%  verts1(uv+color) %.2f%%  bs %.2f%%  '
              'normal16 %.2f%%  tangent8 %.2f%%  whole-vertex %.2f%%'
              % tuple(100.0 * k / nv for k in
                      (pos_ok, v1_ok, bs_ok, n_ok, t_ok, all_ok)))
    print('  fully byte-exact surfaces=%d/%d' % (full_s, tot_s))
    if full_names:
        print('  e.g.:', ', '.join(full_names[:8]))
    return tot_s, full_s


def main():
    wd_dir = os.path.dirname(HERE)
    ok = True
    # 1. decode/re-encode identity on two genuine console zones
    ok &= verify_decode(os.path.join(HERE, 'mp_raid_genuine.zone'))
    ok &= verify_decode(os.path.join(HERE, 'zm_transit_original.zone'))
    # 2. PC -> console re-encode against genuine bytes (Rosetta pairs)
    verify_pair(os.path.join(HERE, 'mp_raid_genuine.zone'),
                os.path.join(wd_dir, 'PC ff', 'mp_raid.zone'))
    verify_pair(os.path.join(HERE, 'zm_transit_original.zone'),
                os.path.join(wd_dir, 'PC ff', 'zm_nuked.zone'))
    print('decode identity:', 'PASS' if ok else 'FAIL')


if __name__ == '__main__':
    main()
