#!/usr/bin/env python3
"""gfxtail8: fix skate clipMap collision aliases (dump-31144-proven mis-relocations).

Dump ground truth (HANDOFF_skate_clipmap_collision, resolved = B5V + dec(alias),
B5V=0x3C5A5FC0 verified on name/planes/leafbrushes):
  - planes family (body+12, 8278 brushside plane ptrs, 4141 cNode plane ptrs):
    resolved 0x4013e434 vs true plane array 0x4013e440  -> +12
  - leafbrushes (body+44) + 3132 cbrush sides ptrs: resolved base short of the
    lbn trailing leafBrush region / brushsides section by 4241 -> +4241
    (leafbrushes true = brushsides + 0x59458 = lbn_end 0x417be808, PC-delta exact)
  - 6827 cbrush verts ptrs: short of brushVerts section by 4243 -> +4243
Alias encoding is linear (blk<<29 | off+1), so patch = alias + correction.
Size-neutral: zone layout / measured map stay valid.
"""
import struct, hashlib, sys

SRC = 'mp_skate_gfxtail7.zone'
DST = 'mp_skate_gfxtail8.zone'
FF = 'mp_skate_gfxtail8.ff'

BB = 84512493                 # clipMap body file offset
F_SIDES = 0x509ede9           # brushsides section (8278 x 12)
F_BRUSHES = 0x51df69d         # brushes section (6827 x 96)
F_BRUSHES_END = 0x527f6bd
B5V = 0x3C5A5FC0
TRUE_PLANES = 0x4013e440
TRUE_BRUSHSIDES = 0x417653b0
TRUE_LEAFBRUSHES = 0x417be808
TRUE_BRUSHVERTS = 0x417cae4c
PLANECOUNT = 6265

z = bytearray(open(SRC, 'rb').read())
u32 = lambda o: struct.unpack_from('>I', z, o)[0]
u16 = lambda o: struct.unpack_from('>H', z, o)[0]

NSIDES = u32(BB + 24)
NBRUSH = u16(BB + 64)
NSM = u32(BB + 84)
NNODES = u32(BB + 92)
assert (NSIDES, NBRUSH, NNODES) == (8278, 6827, 4141), (NSIDES, NBRUSH, NNODES)
F_NODES = F_BRUSHES_END + NSM * 84
# structural check: node entries = {plane alias blk5, children i16[2]}
for j in (0, 1, NNODES - 1):
    v = u32(F_NODES + j * 8)
    assert v >> 29 == 5, 'node %d not blk5: 0x%08x' % (j, v)
    off = (v & 0x1FFFFFFF) - 1 + 64
    r = B5V + off
    assert (r - (TRUE_PLANES - 12)) % 20 == 0 and 0 <= (r - (TRUE_PLANES - 12)) < PLANECOUNT * 20, \
        'node %d plane resolve 0x%08x not on bad-base grid' % (j, r)

dec = lambda v: (v & 0x1FFFFFFF) - 1 + 64
res = lambda v: B5V + dec(v)

def bump(o, add, expect_lo=None, expect_hi=None):
    v = u32(o)
    assert v >> 29 == 5, '0x%x: not blk5 alias: 0x%08x' % (o, v)
    nv = v + add
    assert nv >> 29 == 5
    struct.pack_into('>I', z, o, nv)
    return nv

n = 0
# body planes / leafbrushes
assert res(u32(BB + 12)) == TRUE_PLANES - 12
bump(BB + 12, 12); n += 1
assert res(u32(BB + 44)) == TRUE_LEAFBRUSHES - 4241
bump(BB + 44, 4241); n += 1
# brushside plane ptrs +12
for j in range(NSIDES):
    o = F_SIDES + j * 12
    r = res(u32(o))
    assert (r - (TRUE_PLANES - 12)) % 20 == 0 and 0 <= r - (TRUE_PLANES - 12) < PLANECOUNT * 20
    bump(o, 12); n += 1
# cNode plane ptrs +12
for j in range(NNODES):
    bump(F_NODES + j * 8, 12); n += 1
# brush sides +4241 / verts +4243
ns = nv2 = 0
for j in range(NBRUSH):
    o = F_BRUSHES + j * 96
    sv = u32(o + 32)
    if sv:
        r = res(sv) + 4241
        assert TRUE_BRUSHSIDES <= r < TRUE_BRUSHSIDES + NSIDES * 12 and (r - TRUE_BRUSHSIDES) % 12 == 0, \
            'brush %d sides -> 0x%08x' % (j, r)
        bump(o + 32, 4241); n += 1; ns += 1
    vv = u32(o + 88)
    if vv:
        r = res(vv) + 4243
        assert TRUE_BRUSHVERTS <= r and (r - TRUE_BRUSHVERTS) % 12 == 0, \
            'brush %d verts -> 0x%08x' % (j, r)
        bump(o + 88, 4243); n += 1; nv2 += 1

assert ns == 3132 and nv2 == 6827, (ns, nv2)
# final spot checks
assert res(u32(BB + 12)) == TRUE_PLANES
assert res(u32(BB + 44)) == TRUE_LEAFBRUSHES
assert res(u32(F_SIDES)) == TRUE_PLANES + 5925 * 20         # side0 -> plane 5925 (PC-verified)
assert res(u32(F_BRUSHES + 32)) == TRUE_BRUSHSIDES          # brush0.sides -> section start
assert res(u32(F_BRUSHES + 88)) == TRUE_BRUSHVERTS          # brush0.verts -> section start
print('patched %d aliases (planes-family %d, sides-family %d, verts %d)'
      % (n, 2 + NSIDES + NNODES - 1, 1 + ns, nv2))

open(DST, 'wb').write(z)
print('%s md5 %s' % (DST, hashlib.md5(z).hexdigest()))

sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
