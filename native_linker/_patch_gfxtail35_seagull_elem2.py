#!/usr/bin/env python3
"""gfxtail35: repoint the seagull FX elem-2 broken material handle (boot-42 crash).

Boot 42 (gfxtail34) confirmed the fence-livelock fix (0 GX2QueryBegin) and exposed
the next crash: R_AddCodeMeshDrawSurf(NULL) from FX_GenSpriteVerts, drawing the
seagull tail. Root cause: seagull FX (asset 22) elem 2 (type-3 tail, body @zone
405207) has visuals material-handle @+196 (zone offset 405403) = broken dedup
alias 0xa1cc9351 whose deref target (file 30184337) holds 0x00e06f7d (not a DB
ptr) -> NULL at runtime. Runtime DB: elem0->seagull_side, elem1->seagull_under,
elem2->NULL. The gfxtail26 FX-visual census MISSED this slot (census gap).

Fix (proven _fix_fx_visuals3.py material pattern): repoint elem2.visuals to the
runtime b5 holder of gfx_fxt_bio_seagull_under (elem1's material, DB 0x104A9DE8) —
the same-type-tail choice; worst case slightly wrong texture on one seagull layer,
never a crash. BACKWARD-REF ENFORCED (boot-40 rule: deref fires at stream time):
pick the holder nearest-but-before elem2's slot rt. Closed loop: *(target) == the
DB material whose name == seagull_under. Size-neutral value write, clipMap gated.
"""
import hashlib
import re
import struct
import sys

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
import measure_band as MB
from _dumplib import Dump
from measured_rtmap import MeasuredRuntimeMap

SRC = 'mp_skate_gfxtail34.zone'
DST = 'mp_skate_gfxtail35.zone'
FF = 'mp_skate_gfxtail35.ff'
BB = 84512493
DMP = r'C:\CemuDumps\Cemu.exe.33324.dmp'
SLOT = 405403                 # seagull FX elem2 visuals @+196
OLD = 0xA1CC9351
MATNAME = b'gfx_fxt_bio_seagull_under'
DB = lambda v: 0x10000000 <= v < 0x13000000

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)
assert struct.unpack_from('>I', Z, SLOT)[0] == OLD, 'slot value changed'


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, d))
    return d


assert gate(Z, 'in') == 0

# ---- runtime window + dump ----
d = Dump(DMP)
hits = d.scan(b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00', limit=2)
BASE = hits[0] - 0x3f47a6a2
assert d.read(BASE + 0x3f47a6a2, 8) == b'wpc_sw4_'
f, ranges = MB._load_dump_ranges(DMP)
base_w, G = MB._zone_window(f, ranges, orig, int(122e6))


def guest_cstr(g, n=64):
    b = d.read(BASE + g, n) or b''
    i = b.find(b'\x00')
    return b[:i] if i >= 0 else b


# ---- confirm seagull_under DB material address (elem1's, from the crash r23) ----
M = 0x104A9DE8
nmp = struct.unpack('>I', d.read(BASE + M, 4))[0]
assert guest_cstr(nmp) == MATNAME, 'M name = %r' % guest_cstr(nmp)
print('seagull_under DB material @0x%08X name=%r' % (M, guest_cstr(nmp).decode()))

# ---- elem2 slot runtime b5 offset (for backward-ref enforcement) ----
rtm = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
import pickle
ae = pickle.load(open('_skate6_realmap.pkl', 'rb'))['ae']
slot_rt = int(rtm.rt(SLOT - ae))
print('elem2 slot file=%d -> b5 rt offset=%d' % (SLOT, slot_rt))

# ---- find b5 holders of M; pick nearest BACKWARD of the slot ----
needle = struct.pack('>I', M)
holders = []
i = -1
while True:
    i = G.find(needle, i + 1)
    if i < 0:
        break
    holders.append(i)
print('runtime b5 holders of 0x%08X: %d (first few %s)' % (M, len(holders), holders[:5]))
backward = [t for t in holders if t < slot_rt]
assert backward, 'no backward holder of the material — would be a forward-ref'
t = max(backward)               # nearest backward holder
assert struct.unpack_from('>I', G, t)[0] == M
print('chosen holder b5 rt=%d (backward, %d before slot); *(holder)=0x%08X == M OK'
      % (t, slot_rt - t, struct.unpack_from('>I', G, t)[0]))

new = 0xA0000000 + t + 1
struct.pack_into('>I', Z, SLOT, new)
print('elem2.visuals @%d: 0x%08X -> 0x%08X (b5 payload %d)' % (SLOT, OLD, new, t))

assert len(Z) == len(orig)
assert gate(Z, 'out') == 0
changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d' % changed)

if '--apply' not in sys.argv:
    print('DRY RUN')
    sys.exit(0)

open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
