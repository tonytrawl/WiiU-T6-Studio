#!/usr/bin/env python3
"""gfxtail15: close the dynEnt locator GAP (boot-15 AV in DynEntCl_InitEntities+0x2c8).

The engine null-checks the field (disasm @0x02265750):
    lwz r4,0x38(r16) ; cmpwi r4,0 ; beq skip ; lwz r12,4(r4)   <== fault
so NULL is handled cleanly. gfxtail12/13's locator required an 8-byte
`01ff01ff01ff01ff` sentinel at +0x3c, but those four u16s are real fields that can
carry a live index (0x0029) -> it found 157 of 267 records and left 109 live +0x38
aliases, one of which resolved to 0x3f -> lwz 4(0x3f) -> AV @0x43.
Robust structural locator (u32(+0)==1, unit quat, sane pos, XModel handle @+0x20,
BYTE-aligned scan) finds 2 arrays / 267 records. Apply the genuine no-lights state to ALL.
"""
import struct, hashlib, sys, math
from gfxworld_dynent_fix import find_dynent_arrays, null_dynent_lightlists, STRIDE, NULLSTATE

SRC = 'mp_skate_gfxtail14.zone'; DST = 'mp_skate_gfxtail15.zone'; FF = 'mp_skate_gfxtail15.ff'
z = bytearray(open(SRC, 'rb').read()); orig = bytes(z)
arrays = find_dynent_arrays(z)
print('dynEnt arrays:', [(hex(s), c) for s, c in arrays], 'total', sum(c for _, c in arrays))
for s, c in arrays:
    for k in range(c):
        r = s + k * STRIDE
        q = struct.unpack_from('>4f', z, r + 4)
        assert 0.85 < math.sqrt(sum(x * x for x in q)) < 1.2
        assert 0xA0000000 <= struct.unpack_from('>I', z, r + 0x20)[0] < 0xB0000000
live = sum(1 for s, c in arrays for k in range(c)
           if 0xA0000000 <= struct.unpack_from('>I', z, s + k * STRIDE + 0x38)[0] < 0xC0000000)
print('live +0x38 block-5 aliases BEFORE: %d' % live)
n, ch = null_dynent_lightlists(z)
expected = []
for s, c in arrays:
    for k in range(c):
        for off in NULLSTATE:
            expected.append((s + k * STRIDE + off, s + k * STRIDE + off + 4))
changed = [i for i in range(len(z)) if z[i] != orig[i]]
assert all(any(lo <= i < hi for lo, hi in expected) for i in changed), 'wrote outside light-info fields'
for s, c in arrays:
    for k in range(c):
        for off, val in NULLSTATE.items():
            assert struct.unpack_from('>I', z, s + k * STRIDE + off)[0] == val
assert len(z) == len(orig)
live2 = sum(1 for s, c in arrays for k in range(c)
            if struct.unpack_from('>I', z, s + k * STRIDE + 0x38)[0] != 0)
print('live +0x38 AFTER: %d ; records=%d ; bytes changed=%d ; size-neutral OK' % (live2, n, len(changed)))
open(DST, 'wb').write(bytes(z)); print('%s md5 %s' % (DST, hashlib.md5(bytes(z)).hexdigest()))
sys.path.insert(0, '../WiiU_FF_Studio'); import wiiu_ff
ff = wiiu_ff.pack(bytes(z), 'mp_skate'); open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
