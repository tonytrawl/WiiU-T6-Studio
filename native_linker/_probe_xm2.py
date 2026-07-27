import sys
sys.path.insert(0,'.')
from _dumplib import Dump
d = Dump(sys.argv[1])
print('mapped %.2f GB' % (d.total()/1e9))
e = d.exception()
print('exception record:', e)
