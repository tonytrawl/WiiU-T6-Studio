"""Confirm root_name_fix landed at RUNTIME, and census pointers to the bsp string."""
import sys, struct
sys.path.insert(0,'.')
from _dumplib import Dump
d = Dump(sys.argv[1])
print('dump total mapped: %.2f GB' % (d.total()/1e9))
needle = b'maps/mp/mp_skate.d3dbsp\x00'
hits = d.scan(needle, limit=8)
print('bsp string guest hits:', [hex(h) for h in hits])
TARGET = 0x3EAA84E0
got = d.read(TARGET, 32)
print('read @0x3EAA84E0 =', got[:24] if got else None)
if got and got.startswith(b'maps/mp/mp_skate.d3dbsp'):
    print('  ==> string IS at the address our name* encodes  [root_name_fix CONFIRMED in guest]')
else:
    print('  ==> MISMATCH: our name* target does not hold the string')
# who points at it?
ptr = struct.pack('>I', TARGET)
refs = d.scan(ptr, limit=40)
print('pointers to 0x3EAA84E0: %d' % len(refs), [hex(r) for r in refs[:20]])
