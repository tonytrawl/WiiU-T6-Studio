#!/usr/bin/env python3
"""gfxtail34: strip the FX occlusion-query gate bit (boot-41 FX-fence livelock).

Disasm-proven (FX_SpawnElem +0x4a0, callers of RB_AllocOcclusionQuery @0x029E505C):
FX occlusion queries are allocated ONLY when `FxElemDef.flags & 0x8000` is set
(rlwinm. r6, r9, 0, 16, 16 on *(elemDef+0) → beq skips the alloc). The other 4
RB_AllocOcclusionQuery callers are RB_AllocSunSpriteQueries (sun flare, not FX).

Enumeration of ALL 329 elemDefs across 79 FxEffectDef assets: EXACTLY ONE carries
the bit — FX ordinal 0 (asset row 5), elem 0, zone offset 135890, flags 0x00018082,
elemType 0. (The seagull FX flags are 0x11000066 — bit clear; the handoff's
"gate = effectDef header flag 0x840C" was WRONG.) The whole 51 MB boot-41 log has
exactly 2 GX2QueryBegin (round-start, intermittent) = this one FX, not the sun.

Fix: clear bit 0x8000 → 0x00018082 becomes 0x00010082. FxElemDef.flags does NOT
drive the loader stream walk (only counts + elem tail fields do), so this is
size-neutral and stream-safe. Result: FX_SpawnElem never allocs an occlusion
query for any map FX → no GX2QueryBegin → Cemu's query-emulation stall is never hit.

Visual effect: the sprite no longer fades by occlusion visibility (draws full).
"""
import hashlib
import re
import struct
import sys

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC

SRC = 'mp_skate_gfxtail33.zone'
DST = 'mp_skate_gfxtail34.zone'
FF = 'mp_skate_gfxtail34.ff'
BB = 84512493
FLAGS_OFF = 135890
GATE = 0x00008000

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

cur = struct.unpack_from('>I', Z, FLAGS_OFF)[0]
assert cur == 0x00018082, 'flags word changed: 0x%08x (expected 0x00018082)' % cur
new = cur & ~GATE
struct.pack_into('>I', Z, FLAGS_OFF, new)
print('elemDef flags @%d: 0x%08x -> 0x%08x (cleared 0x%04x)' % (FLAGS_OFF, cur, new, GATE))

# re-verify: zero elemDefs left with the gate bit
import loader_sim as LS
open(DST + '.tmp', 'wb').write(bytes(Z))
em, spans, CO = LS.simulate(DST + '.tmp', verbose=False)
be16 = lambda o: struct.unpack_from('>h', Z, o)[0]
be32 = lambda o: struct.unpack_from('>I', Z, o)[0]
left = 0
for (i, nm, root, s, e) in spans:
    if root != 'FxEffectDef' or e <= s:
        continue
    o = s + 76
    if be32(s + 0) in (0xFFFFFFFF, 0xFFFFFFFE):
        o = Z.index(b'\x00', o) + 1
    cnt = be16(s + 8) + be16(s + 10) + be16(s + 12)
    for k in range(cnt):
        if be32(o + k * 292) & GATE:
            left += 1
import os
os.remove(DST + '.tmp')
print('elemDefs with GATE bit after edit: %d (must be 0)' % left)
assert left == 0

assert len(Z) == len(orig)
assert gate(Z, 'out') == 0
changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d (size-neutral)' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
print('\nSTAGED — NOT deployed. Recommend `fx_enable 0` boot first to confirm the FX/query trigger.')
