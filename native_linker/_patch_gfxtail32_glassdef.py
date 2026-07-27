#!/usr/bin/env python3
"""gfxtail32: fix the 35 stale GlassDef offset-pointers (boot-39 front).

Boot 39 (dump 34912): crash in R_AddBModelSurfacesCamera via the scene-glass
path — surfaces[7080..7129].material (48 surfaces) held 0x00000042 at runtime,
stamped there by GlassClient::SetBrushMaterial reading def->material from a
WRONG GlassDef address.

Root cause (dump-proven): the GLASSES asset ('glasses(n=36)' @84504106) has 36
Glass records (0x8C each, array @84504120); glass 0 carries the single inline
GlassDef (file 84509160), glasses 1..35 reference it via an offset-pointer at
Glass+0x10 (loader path: DB_ConvertOffsetToPointer — DIRECT conversion, no
deref). All 35 hold 0xa51b8dea -> guest 0x4175EDE9, which is 19 bytes SHORT of
the true runtime def body 0x4175EDFC (name-adjacency proven: name ptr at
def+0 = 0x4175EE38 = body+0x3C = the 'glass_dec_clear_mp' string hit).
*(0x4175EDE9+0x1C) = 0x00000042 — exactly the poison on the 48 surfaces; the
true def's material handles are the three DB glass materials.

Fix: 35 x rewrite 0xa51b8dea -> 0xa51b8dfd (payload 85691881 -> 85691900).
The def's own handles (3 inline materials, FX 0x10968248, XStrings incl. the
+0x30 alias 0xa51b9226 -> 0x4175F225) are all runtime-verified CORRECT.
Region alias census: this is the ONLY real defect (everything else = §6.7
false positives).
"""
import hashlib
import re
import struct
import sys

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC

SRC = 'mp_skate_gfxtail31.zone'
DST = 'mp_skate_gfxtail32.zone'
FF = 'mp_skate_gfxtail32.ff'
BB = 84512493
ARR = 84504120
OLD, NEW = 0xa51b8dea, 0xa51b8dfd

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, d))
    return d


assert gate(Z, 'in') == 0

n = 0
for g in range(36):
    o = ARR + g * 0x8C + 0x10
    w = struct.unpack_from('>I', Z, o)[0]
    if g == 0:
        assert w == 0xFFFFFFFF, 'glass 0 def must be FOLLOW, got 0x%08x' % w
        continue
    assert w == OLD, 'glass %d def word 0x%08x != expected 0x%08x' % (g, w, OLD)
    struct.pack_into('>I', Z, o, NEW)
    n += 1
print('rewrote %d def offset-pointers 0x%08x -> 0x%08x' % (n, OLD, NEW))
assert n == 35
assert len(Z) == len(orig)
assert gate(Z, 'out') == 0

changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
