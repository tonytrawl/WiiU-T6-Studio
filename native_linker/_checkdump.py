"""Validate a Cemu minidump BEFORE analysing it. Usage: python _checkdump.py <path>
A complete Cemu dump is ~3.5 GB with ~2200 memory ranges. Task Manager backfills the
minidump stream directory LAST, so a dump read too early parses as 'no memory' silently."""
import sys, os
sys.path.insert(0, '.')
from _dumplib import Dump
p = sys.argv[1]
print('file   : %s' % p)
print('size   : %.2f GB' % (os.path.getsize(p) / 1e9))
try:
    d = Dump(p)
except Exception as ex:
    print('STATUS : UNUSABLE\n  %s' % ex)
    sys.exit(1)
print('mapped : %.2f GB in %d ranges' % (d.total() / 1e9, len(d.ranges)))
e = d.exception()
print('excep  : %s' % (('code=0x%08X addr=0x%X' % (e['code'], e['address'])) if e else 'none (hang dump)'))
hits = d.scan(b'maps/mp/mp_skate.d3dbsp\x00', limit=2)
print('anchor : bsp string hits=%s' % [hex(h) for h in hits])
if hits:
    print('BASE   : 0x%X   (guest 0x3EAA84E0)' % (hits[0] - 0x3EAA84E0))
print('STATUS : USABLE')
