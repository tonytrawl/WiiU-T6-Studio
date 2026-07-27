#!/usr/bin/env python3
"""gfxtail12: fix skate GfxWorld dynEnt-client +0x38 primary-light-list pointer (boot-12).

Family 8 (see FINDINGS_rtmap_systemic_rootcause.md ALIAS AUDIT): the dpvsDynamic
dynEnt-client records carry a per-entity primary-light index-list ptr at +0x38 that
R_LinkDynEntToPrimaryLights dereferences. skate relocates it to WILD targets (boot-12
read AV 0x004044f8). Genuine console ships +0x38 == 0 for many entities and null-checks
it, so NULL is boot-safe and genuine-valid. This nulls +0x38 on every dynEnt record,
located STRUCTURALLY (gfxworld_dynent_fix). Size-neutral -> zone layout unchanged.
"""
import struct, hashlib, sys
from gfxworld_dynent_fix import find_dynent_arrays, null_dynent_lightlists, STRIDE

SRC = 'mp_skate_gfxtail11.zone'
DST = 'mp_skate_gfxtail12.zone'
FF = 'mp_skate_gfxtail12.ff'

z = bytearray(open(SRC, 'rb').read())
orig = bytes(z)

arrays = find_dynent_arrays(z)
print('dynEnt arrays:', [(hex(s), c) for s, c in arrays])
assert arrays and arrays[0][:1] == (0x555e8c3,), arrays   # sanity: known primary array
total = sum(c for _, c in arrays)

# pre-assert: every record we touch is a genuine dynEnt (unit quat + model handle)
import math
for s, c in arrays:
    for k in range(c):
        r = s + k * STRIDE
        q = struct.unpack_from('>4f', z, r + 4)
        m = struct.unpack_from('>I', z, r + 0x20)[0]
        assert 0.8 < math.sqrt(sum(x * x for x in q)) < 1.3 and 0xA0000000 <= m < 0xB0000000, \
            'record @0x%x not a dynEnt' % r

n, nz = null_dynent_lightlists(z)
assert n == total

# verify ONLY the +0x38 words changed, and all are now zero
expected = []                       # (lo, hi) byte ranges of every +0x38 word
for s, c in arrays:
    for k in range(c):
        w = s + k * STRIDE + 0x38
        expected.append((w, w + 4))
def in_expected(i):
    return any(lo <= i < hi for lo, hi in expected)
changed = [i for i in range(len(z)) if z[i] != orig[i]]
assert all(in_expected(i) for i in changed), 'changed bytes outside +0x38 fields!'
for lo, hi in expected:
    assert struct.unpack_from('>I', z, lo)[0] == 0
print('touched %d dynEnt +0x38 words (%d were non-zero); no other bytes changed' % (n, nz))
assert len(z) == len(orig)   # size-neutral

open(DST, 'wb').write(bytes(z))
print('%s md5 %s' % (DST, hashlib.md5(z).hexdigest()))

sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
