"""Fast full audit of the models/dynEnt array: anchor K once (verify constant),
then classify the real pointer fields (+0x20 xmodel, +0x38) across all 101 records
and inspect what VALID +0x38 targets point to (to derive the correct emission)."""
import sys, struct
sys.path.insert(0, '.')
from _dumplib import Dump

Z = open('mp_skate_gfxtail11.zone', 'rb').read()
d = Dump(r'C:\CemuDumps\Cemu.exe.29840.dmp')
BASE = 0x1b89ece0000
def rg(g, n): return d.read(BASE + g, n)
u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
isalias = lambda v: 0xA0000000 <= v < 0xC0000000
STRIDE = 84; lo = 0x555e8c3; n = 101

def anchor(rec):
    hits = d.scan(Z[rec+0x14:rec+0x20], limit=6)
    for h in hits:
        g = h - BASE - 0x14
        mv = rg(g+0x20, 4)
        if mv and 0x10000000 <= struct.unpack('>I', mv)[0] < 0x50000000:
            return g - rec
    return None

K0 = anchor(lo); Klast = anchor(lo+(n-1)*STRIDE)
print('K first=0x%x last=0x%x  constant=%s' % (K0 or 0, Klast or 0, K0 == Klast))
K = K0
def resolved(off):
    b = rg(off+K, 4)
    return struct.unpack('>I', b)[0] if b else None
def cls(v):
    if v is None: return 'unmapped_field'
    if v == 0: return 'null'
    if 0x10000000 <= v < 0x50000000 and rg(v, 4) is not None: return 'ok'
    return 'WILD'

from collections import Counter
c20, c38 = Counter(), Counter()
ok38_targets = []; wild38 = []
extra = Counter()
for k in range(n):
    rec = lo + k*STRIDE
    r20 = resolved(rec+0x20); c20[cls(r20)] += 1
    r38 = resolved(rec+0x38); c38[cls(r38)] += 1
    if cls(r38) == 'ok':
        ok38_targets.append((k, r38))
    elif cls(r38) == 'WILD':
        wild38.append((k, u32(rec+0x38), r38))
    # extra alias fields at +0x24, +0x28, +0x2c, +0x30 (non-float positions)
    for o in (0x24, 0x28, 0x2c, 0x30):
        if isalias(u32(rec+o)):
            extra[o] += 1
print('+0x20 xmodel:', dict(c20))
print('+0x38 field :', dict(c38))
print('extra alias fields (+0x24/0x28/0x2c/0x30):', {hex(k): v for k, v in extra.items()})
print('\nsample WILD +0x38 (record, built_alias, resolved):')
for k, a, r in wild38[:6]:
    print('  rec%3d built=0x%08x -> resolved 0x%08x' % (k, a, r))
print('\nsample OK +0x38 targets (record, resolved, first 24 bytes @target):')
for k, r in ok38_targets[:6]:
    t = rg(r, 24)
    print('  rec%3d -> 0x%08x : %s' % (k, r, t.hex() if t else None))
# what built alias do the OK ones carry, vs wild?
print('\nOK +0x38 built alias values:', sorted(set('0x%08x' % u32(lo+k*STRIDE+0x38) for k, _ in ok38_targets))[:12])
