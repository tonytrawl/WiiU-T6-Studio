"""Bank names are VALID at runtime; the garbage .sabs path is built from a different
bad pointer. (1) un-escape the log path to REAL bytes, (2) scan the crash dump for
the garbage bankname -> find its source struct, (3) read skate banks' runtime
filename fields (+0x840 streamed, +0x1162 loaded) in crash vs working."""
import struct, sys, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

NEW  = r'C:\CemuDumps\Cemu.exe.30528.dmp'
WORK = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
LOG = r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\log.txt"

# ---- (1) un-escape the logged path to real bytes ----
data = open(LOG, 'rb').read()
i = data.rfind(b'/vol/content/english/sound/')
j = data.find(b'.sabs"', i)
esc = data[i:j+5].decode('latin1')
# Cemu escapes: \xNN, \t \n \r \\ etc. Reverse it.
out = bytearray(); k = 0
while k < len(esc):
    c = esc[k]
    if c == '\\' and k+1 < len(esc):
        n = esc[k+1]
        if n == 'x':
            out.append(int(esc[k+2:k+4], 16)); k += 4; continue
        m = {'t':9,'n':10,'r':13,'\\':92,'0':0,'a':7,'b':8,'f':12,'v':11}.get(n)
        if m is not None:
            out.append(m); k += 2; continue
    out.append(ord(c)); k += 1
real = bytes(out)
print('[1] real path bytes (%d): %s' % (len(real), real.hex()))
prefix = b'/vol/content/english/sound/'
gname = real[len(prefix):-5]   # strip prefix and ".sabs"
print('    garbage bankname real bytes (%d): %s' % (len(gname), gname.hex()))
print('    ascii:', ''.join(chr(c) if 32 <= c < 127 else '.' for c in gname))

dN = Dump(NEW); BN = dN.scan(ANCHOR, limit=1)[0] - ANCHOR_G
rn = lambda g, n: dN.read(BN + g, n) or b''

# ---- (2) scan crash dump for the garbage name -> source struct ----
print('\n[2] garbage name in crash dump:')
for probe in (gname[:24], gname[:16], gname[:12]):
    hits = dN.scan(probe, limit=8)
    print('    probe len %d -> hosts %s' % (len(probe), [hex(h - BN) for h in hits]))
    if hits:
        break

# ---- (3) runtime filename fields of skate banks in both dumps ----
def show(path, tag):
    d = Dump(path); B = d.scan(ANCHOR, limit=1)[0] - ANCHOR_G
    rd = lambda g, n: d.read(B + g, n) or b''
    print('\n[3][%s] base=0x%X' % (tag, B))
    for slot, bp in (('skate.english', 0x10887C6C), ('skate.all', 0x1088B428)):
        for off, fn in ((0x840, 'strm.filename'), (0x1162, 'load.filename'),
                        (0x28, 'strm.handle'), (0x94a, 'load.handle'),
                        (0x1288, 'state'), (0x1290, 'busy'), (0x1291, 'err')):
            raw = rd(bp + off, 32 if 'filename' in fn else 4)
            if 'filename' in fn:
                z = raw.find(b'\x00'); s = raw[:z if z >= 0 else 32]
                pr = s.decode('latin1') if all(32 <= c < 127 for c in s) and s else raw[:16].hex()
                print('    %-13s +0x%04X %-14s = %s' % (slot, off, fn, pr))
            else:
                print('    %-13s +0x%04X %-14s = 0x%08X' % (slot, off, fn, struct.unpack_from('>I', raw, 0)[0] if len(raw) >= 4 else -1))

show(NEW, 'NEW-crash')
show(WORK, 'WORKING')
