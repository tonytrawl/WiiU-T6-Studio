#!/usr/bin/env python3
"""
Task #28: console (Wii U) XModel / XSurface layout for T6 — SOLVE probe.

Method (as gfximage_probe/shader_probe): find genuine XModel bodies by
structural validation, parse the FULL dynamic stream, and require byte-exact
resync onto the next XModel body.

== Console XModel = 244 bytes (PC-32 248) ==
PC's `bool bad` @212 (+3 pad) is DROPPED; tail shifts -4:
  +0 name*  +4 numBones u8  +5 numRootBones u8  +6 numsurfs u8  +7 lodRampType
  +8 boneNames*  +12 parentList*  +16 quats*  +20 trans*
  +24 partClassification*  +28 baseMat*  +32 surfs*  +36 materialHandles*
  +40 lodInfo[4] x28  +152 collSurfs*  +156 numCollSurfs  +160 contents
  +164 boneInfo*  +168 radius f32  +172 mins  +184 maxs  +196 numLods u16
  +198 collLod i16  +200 himipInvSqRadii*  +204 memUsage  +208 flags
  +212 physPreset*  +216 numCollmaps u8(+3)  +220 collmaps*
  +224 physConstraints*  +228 lightingOriginOffset vec3  +240 lightingOriginRange
Everything up to +208 is PC-identical.

== Console XSurface = 128 bytes (PC 80) — a GX2 struct, not shifted PC ==
  +0   tileMode u8, +1 vertListCount u8, +2 flags u16 (1=quantized, 2=skinned,
       0x80=deformed), +4 vertCount u16, +6 triCount u16, +8 baseVertIndex u16
  +12  triIndices*  (FOLLOW -> triCount x 3 x BE u16, tightly packed, LAST)
  +16  vertInfo.vertCount[4] i16 (PC-identical)
  +24  vertsBlend* (skinned only)  +28 u32 skinned scalar (unknown)
  +32  ptr (skinned only, tension?)  +36 ptr (skinned only)  +40 u32 scalar
  +44  ptr (skinned only)  +48 u32
  +52  verts0*  (FOLLOW -> vertCount x 24 B: xyz BE float32 + 12 B packed)
  +56..71 zeros
  +72  verts1*  (NEW on console: FOLLOW -> vertCount x 8 B second vertex stream)
  +76..95 zeros
  +96  vertList* (FOLLOW -> vertListCount x XRigidVertList(12) each w/ optional
       XSurfaceCollisionTree(40)+nodes(16*n)+leafs(2*n))
  +100..107 zeros
  +108 partBits[5] -> 128
Static per-surface dynamic order (byte-exact):
  verts0 (24*vc), verts1 (8*vc), vertList (+trees), triIndices (6*tc).
Skinned surfaces have an extra pre-verts0 blob (vertsBlend/tension/GX2 skin
streams) not yet sized — the probe skips those models (rare in map zones).

materialHandles entries are asset refs: FOLLOW/INSERT -> full inline console
Material asset (104 + name + techset/textures/images per solved layouts).
physPreset FOLLOW -> inline PhysPreset(84)+strings. collmaps FOLLOW -> Collmap
chain (PhysGeomList 12, PhysGeomInfo 68, BrushWrapper 96, sides/verts/planes).
"""
import struct
import sys
from collections import Counter

import shader_probe

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
BODY = 244
SURF = 128
VTX = 24
MAT_SIZE = 104
IMG_SIZE = 328

# Default-off texdef-table trace (2026-08-08, RT-instrument successor).
# When a consumer sets TEXTABLE_TRACE to a list, consume_material appends
# (defs_abs_off, table_count) for every inline material's texdef table it
# delimits. Covers hosts with no event walker (e.g. the Glasses asset via
# raid_oracle_control._console_glasses_end). Delimiter behavior unchanged.
TEXTABLE_TRACE = None

STATS = Counter()


class Fail(Exception):
    pass


def u32(d, o):
    return struct.unpack('>I', d[o:o+4])[0]


def u16(d, o):
    return struct.unpack('>H', d[o:o+2])[0]


def i16(d, o):
    return struct.unpack('>h', d[o:o+2])[0]


def ptr_ok(v):
    return v in (0, FOLLOW, INSERT) or ((v - 1) >> 29) < 8


def is_body(d, o, strict=True):
    """Structural validator for a console XModel body at o."""
    if o + BODY > len(d) or u32(d, o) != FOLLOW:
        return False
    ns = d[o+6]
    if not (1 <= ns <= 64):
        return False
    if not all(ptr_ok(u32(d, o+k)) for k in (8, 12, 16, 20, 24, 28, 32, 36,
                                             152, 164, 200, 212, 220, 224)):
        return False
    if u32(d, o+32) == 0 or u32(d, o+36) == 0:
        return False
    tot = 0
    si = 0
    nlods = 0
    for i in range(4):
        lo = o + 40 + i*28
        lns = u16(d, lo+4)
        if lns:
            if u16(d, lo+6) != si:
                return False
            si += lns
            tot += lns
            nlods += 1
    if not (tot == ns and nlods >= 1):
        return False
    return not strict or u16(d, o+196) == nlods


class Cur:
    def __init__(self, d, o):
        self.d = d
        self.o = o

    def skip(self, x):
        self.o += x

    def cstr(self, maxlen=160):
        e = self.d.index(b'\x00', self.o)
        if e - self.o > maxlen:
            raise Fail('string too long')
        s = self.d[self.o:e]
        self.o = e + 1
        return s.decode('latin-1', 'replace')


def consume_image(d, c):
    """Inline console GfxImage (solved, section 0f): 328 + name + pixels."""
    b = c.o
    c.skip(IMG_SIZE)
    if u32(d, b+320) in PTRS:          # name (reorder: loads first)
        c.cstr()
    streaming = d[b+171]
    if u32(d, b+176) in PTRS and streaming == 0:
        c.skip(u32(d, b+160))          # pixels: baseSize bytes
    STATS['inline_image'] += 1


def consume_material(d, c):
    """Inline console Material asset (solved, section 0c/0f)."""
    b = c.o
    c.skip(MAT_SIZE)
    tc, cc, sbc = d[b+72], d[b+73], d[b+74]
    tsp, ttp, ctp, sbp = (u32(d, b+80), u32(d, b+84), u32(d, b+88), u32(d, b+92))
    thermal = u32(d, b+96)
    if u32(d, b) in PTRS:
        c.cstr()                       # info.name
    if tsp in PTRS:                    # inline techset asset
        c.o, _ = shader_probe.parse_techset(d, c.o)
        STATS['inline_techset'] += 1
    if ttp in PTRS:
        defs = c.o
        if TEXTABLE_TRACE is not None and tc:
            TEXTABLE_TRACE.append((defs, tc))
        c.skip(tc * 16)
        for i in range(tc):
            if u32(d, defs + i*16 + 12) in PTRS:
                consume_image(d, c)
    if ctp in PTRS:
        c.skip(cc * 32)
    if sbp in PTRS:
        c.skip(sbc * 8)
    if thermal in PTRS:
        consume_material(d, c)
    STATS['inline_material'] += 1


def consume_collmaps(d, c, ncm):
    """collmaps: ncm x Collmap(4) bodies, then each geomList chain."""
    base = c.o
    c.skip(4 * ncm)
    for i in range(ncm):
        if u32(d, base + i*4) not in PTRS:
            continue
        gl = c.o
        c.skip(12)                     # PhysGeomList {count, geoms*, contents}
        cnt = u32(d, gl)
        if u32(d, gl+4) in PTRS:
            gbase = c.o
            c.skip(68 * cnt)           # PhysGeomInfo bodies
            for g in range(cnt):
                if u32(d, gbase + g*68) not in PTRS:
                    continue
                bw = c.o
                c.skip(96)             # BrushWrapper
                nsides = u32(d, bw+28)
                nverts = u32(d, bw+84)
                if u32(d, bw+32) in PTRS:      # sides
                    sbase = c.o
                    c.skip(12 * nsides)
                    for s in range(nsides):
                        if u32(d, sbase + s*12) in PTRS:
                            c.skip(20)         # cplane_s
                if u32(d, bw+88) in PTRS:      # verts
                    c.skip(12 * nverts)
                if u32(d, bw+92) in PTRS:      # planes
                    c.skip(20 * nsides)
    STATS['inline_collmaps'] += 1


def parse_surface_dyn(d, b, c):
    """One console XSurface's dynamic data. Static path only (skinned raises)."""
    vc, tc = u16(d, b+4), u16(d, b+6)
    if u32(d, b+24) in PTRS or u32(d, b+32) in PTRS or \
       u32(d, b+36) in PTRS or u32(d, b+44) in PTRS:
        # skinned: consume the console pre-verts0 blob (solved in skinned_probe.py).
        # vertsBlend = (vc0 + 3vc1 + 5vc2 + 7vc3)*2 (vertCount[4] = s16 @ b+16..+22),
        # + Latte skin-stream gap = 2*lo16(s28) + 2*hi16(s28) + 2*s40.
        vi = [struct.unpack('>h', d[b+16+j*2:b+18+j*2])[0] for j in range(4)]
        s28, s40 = u32(d, b+28), u32(d, b+40)
        vb = (vi[0] + 3*vi[1] + 5*vi[2] + 7*vi[3]) * 2
        c.skip(vb + 2*(s28 & 0xffff) + 2*(s28 >> 16) + 2*s40)
    if u32(d, b+52) in PTRS:
        c.skip(vc * VTX)               # verts0
        STATS['verts_inline'] += 1
    if u32(d, b+72) in PTRS:
        c.skip(vc * 8)                 # verts1 (console second stream)
    if u32(d, b+96) in PTRS:           # vertList
        vlc = d[b+1]
        base = c.o
        c.skip(vlc * 12)
        for k in range(vlc):
            if u32(d, base + k*12 + 8) in PTRS:
                tb = c.o
                c.skip(40)             # XSurfaceCollisionTree
                nc_, lc_ = u32(d, tb+24), u32(d, tb+32)
                if u32(d, tb+28) in PTRS:
                    c.skip(nc_ * 16)   # nodes
                if u32(d, tb+36) in PTRS:
                    c.skip(lc_ * 2)    # leafs
    if u32(d, b+12) in PTRS:
        c.skip(tc * 6)                 # triIndices
        STATS['tris_inline'] += 1


def parse_xmodel(d, o):
    """Full console XModel parse. Returns (end, name)."""
    nb, nrb, ns = d[o+4], d[o+5], d[o+6]
    ptr = {k: u32(d, o+k) for k in (0, 8, 12, 16, 20, 24, 28, 32, 36,
                                    152, 164, 200, 212, 220, 224)}
    ncoll = u32(d, o+156)
    ncollmaps = d[o+216]
    c = Cur(d, o + BODY)
    name = c.cstr() if ptr[0] in PTRS else '<alias>'
    if ptr[8] in PTRS:
        c.skip(2 * nb)                 # boneNames
    if ptr[12] in PTRS:
        c.skip(nb - nrb)               # parentList
    if ptr[16] in PTRS:
        c.skip(8 * (nb - nrb))         # quats
    if ptr[20] in PTRS:
        c.skip(16 * (nb - nrb))        # trans
    if ptr[24] in PTRS:
        c.skip(nb)                     # partClassification
    if ptr[28] in PTRS:
        c.skip(32 * nb)                # baseMat
    if ptr[32] in PTRS:
        sb = c.o
        c.skip(ns * SURF)
        for i in range(ns):
            parse_surface_dyn(d, sb + i*SURF, c)
    if ptr[36] in PTRS:                # materialHandles
        base = c.o
        c.skip(4 * ns)
        for i in range(ns):
            if u32(d, base + i*4) in PTRS:
                consume_material(d, c)
    if ptr[152] in PTRS:               # collSurfs: console XModelCollSurf_s = 36
        c.skip(36 * ncoll)             # (collTris*/numCollTris dropped, no dyn)
    if ptr[164] in PTRS:
        c.skip(44 * nb)                # boneInfo
    if ptr[200] in PTRS:
        c.skip(4 * ns)                 # himipInvSqRadii
    if ptr[212] in PTRS:               # physPreset inline
        pb = c.o
        c.skip(84)
        if u32(d, pb) in PTRS:
            c.cstr()
        if u32(d, pb+28) in PTRS:
            c.cstr()
        STATS['inline_physpreset'] += 1
    if ptr[220] in PTRS:
        consume_collmaps(d, c, ncollmaps)
    if ptr[224] in PTRS:
        raise Fail('inline physConstraints')
    return c.o, name


def main():
    paths = sys.argv[1:] or ['mp_raid_genuine.zone', 'zm_transit_original.zone']
    for path in paths:
        STATS.clear()
        d = open(path, 'rb').read()
        n = len(d)
        cands = []
        o = 0
        while o + BODY <= n:
            if is_body(d, o):
                cands.append(o)
            o += 4
        ok = bad = chained = skinned = 0
        fails = Counter()
        seen = set()
        # parse every candidate, then CHAIN: if the byte right after a model
        # is another valid body, keep walking (models are consecutive assets)
        queue = list(cands)
        qi = 0
        while qi < len(queue):
            o = queue[qi]
            qi += 1
            if o in seen:
                continue
            seen.add(o)
            try:
                end, name = parse_xmodel(d, o)
            except Fail as e:
                if 'skinned' in str(e):
                    skinned += 1
                else:
                    bad += 1
                    fails[str(e)[:48]] += 1
                continue
            except (ValueError, IndexError, struct.error) as e:
                bad += 1
                fails[str(e)[:48]] += 1
                continue
            ok += 1
            if is_body(d, end, strict=False):
                chained += 1
                if end not in seen:
                    queue.append(end)
        print('%s: candidates=%d parsed_ok=%d chained_next_model=%d '
              'skinned_skipped=%d bad=%d' %
              (path, len(cands), ok, chained, skinned, bad))
        print('  fails:', dict(fails.most_common(6)))
        print('  stats:', dict(STATS))


if __name__ == '__main__':
    main()
