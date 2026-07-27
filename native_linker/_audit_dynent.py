"""Audit the GfxWorld 'models'/dynEnt array in mp_skate_gfxtail11.zone against the
boot-12 dump: bound the 84-byte-record array, and for every record resolve its
pointer-position fields via the dump (per-record K from the pos-vector anchor) to
classify each as valid guest ptr / null / WILD. Counts family-8 scope + detects
whether >1 field per record is bad and whether the bug extends past the models array."""
import sys, struct, re
sys.path.insert(0, '.')
from _dumplib import Dump

Z = open('mp_skate_gfxtail11.zone', 'rb').read()
d = Dump(r'C:\CemuDumps\Cemu.exe.29840.dmp')
BASE = 0x1b89ece0000
def rg(g, n): return d.read(BASE + g, n)
u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
isalias = lambda v: 0xA0000000 <= v < 0xC0000000

# 1) bound the record array: 84-byte stride, each ends with 01ff01ff 01ff01ff at +0x38
STRIDE = 84
start = 0x5560843
# walk backward while prev record also has the double sentinel at +0x38
def has_sentinel(rec):
    return Z[rec+0x3c:rec+0x44] == b'\x01\xff\x01\xff\x01\xff\x01\xff'
lo = start
while lo-STRIDE >= 0 and has_sentinel(lo-STRIDE):
    lo -= STRIDE
hi = start
while has_sentinel(hi) and hi+STRIDE < len(Z):
    hi += STRIDE
n = (hi-lo)//STRIDE
print('models/dynEnt array: [0x%x,0x%x) stride=%d records=%d' % (lo, hi, STRIDE, n))

def valid_guest(v):
    if v == 0: return 'null'
    if rg(v, 4) is not None and 0x10000000 <= v < 0x50000000: return 'ok'
    return 'WILD'

# 2) classify every 4-aligned field position across the record: which positions hold aliases
alias_pos = {}
for k in range(n):
    rec = lo + k*STRIDE
    for o in range(0, STRIDE, 4):
        if isalias(u32(rec+o)):
            alias_pos[o] = alias_pos.get(o, 0) + 1
print('alias-bearing field offsets (offset: #records):', {hex(k): v for k, v in sorted(alias_pos.items())})

# 3) for a sample of records, resolve each alias field via per-record K (pos anchor @+0x14)
def rec_K(rec):
    pos = Z[rec+0x14:rec+0x20]
    hits = d.scan(pos, limit=4)
    for h in hits:                       # entry start guest = pos_host - BASE - 0x14
        g = h - BASE - 0x14
        # sanity: model field should match
        if rg(g+0x20, 4) is not None:
            return g - rec               # K
    return None

from collections import Counter
tally = Counter()
per_field = {}
checked = 0
for k in range(n):
    rec = lo + k*STRIDE
    K = rec_K(rec)
    if K is None:
        tally['no_anchor'] += 1; continue
    checked += 1
    for o in sorted(alias_pos):
        v = u32(rec+o)
        if not isalias(v):
            continue
        resolved = struct.unpack_from('>I', rg(rec+o+K, 4) or b'\x00\x00\x00\x00', 0)[0]
        cls = valid_guest(resolved)
        per_field.setdefault(o, Counter())[cls] += 1
        tally[cls] += 1
    if checked >= 60:
        break

print('records anchored/checked:', checked, '(no_anchor: %d)' % tally['no_anchor'])
print('overall field classes:', dict(tally))
for o in sorted(per_field):
    print('  field +0x%02x: %s' % (o, dict(per_field[o])))
