#!/usr/bin/env python3
"""gfxtail13: refine the dynEnt light fix (boot-13). gfxtail12 nulled the +0x38
light-list ptr and advanced skate all the way into GPU rendering (~22 frames @720p),
but the render iterates a per-entity SHADOW-light count (+0x4e == 4 on every skate
record) over the now-null list -> null AV @guest 0x43. Genuine "no lights" records
zero the light-info counts too (+0x48=0x200, +0x4c=0x0001). This replicates that full
state on all dynEnt records. Built from the clean gfxtail11 base.
"""
import struct, hashlib, sys
from gfxworld_dynent_fix import find_dynent_arrays, null_dynent_lightlists, STRIDE, NULLSTATE

SRC = 'mp_skate_gfxtail11.zone'
DST = 'mp_skate_gfxtail13.zone'
FF = 'mp_skate_gfxtail13.ff'

z = bytearray(open(SRC, 'rb').read())
orig = bytes(z)
arrays = find_dynent_arrays(z)
print('dynEnt arrays:', [(hex(s), c) for s, c in arrays])
assert arrays and arrays[0][0] == 0x555e8c3

# pre-assert every touched record is a real dynEnt (unit quat + model handle)
import math
for s, c in arrays:
    for k in range(c):
        r = s + k * STRIDE
        q = struct.unpack_from('>4f', z, r + 4)
        m = struct.unpack_from('>I', z, r + 0x20)[0]
        assert 0.8 < math.sqrt(sum(x * x for x in q)) < 1.3 and 0xA0000000 <= m < 0xB0000000

null_dynent_lightlists(z)

# verify: only the NULLSTATE words (+0x38,+0x48,+0x4c) changed, and they hold the target values
expected = []
for s, c in arrays:
    for k in range(c):
        for off in NULLSTATE:
            expected.append((s + k * STRIDE + off, s + k * STRIDE + off + 4))
def in_expected(i):
    return any(lo <= i < hi for lo, hi in expected)
changed = [i for i in range(len(z)) if z[i] != orig[i]]
assert all(in_expected(i) for i in changed), 'changed bytes outside light-info fields!'
for s, c in arrays:
    for k in range(c):
        for off, val in NULLSTATE.items():
            assert struct.unpack_from('>I', z, s + k * STRIDE + off)[0] == val
assert len(z) == len(orig)
print('changed %d bytes, all within +0x38/+0x48/+0x4c of %d dynEnt records'
      % (len(changed), sum(c for _, c in arrays)))

open(DST, 'wb').write(bytes(z))
print('%s md5 %s' % (DST, hashlib.md5(z).hexdigest()))
sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
