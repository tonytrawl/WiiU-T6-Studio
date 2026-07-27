"""Resolve the skate.all bank's zone*/language*/strm pointers AT RUNTIME in crash vs
working dump and read the strings they point to. If the pipeline's baked pointer
resolves to garbage (mount-string region) while the playable's resolves to
'mpl_skate\\0all', the baked zone*/language* (rtm.rt) is the real defect."""
import struct, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

NEW  = r'C:\CemuDumps\Cemu.exe.30528.dmp'
WORK = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
SKALL = 0x1088B428   # runtime mpl_skate.all bank struct

def show(path, tag):
    d = Dump(path); B = d.scan(ANCHOR, limit=1)[0] - ANCHOR_G
    rd = lambda g, n: d.read(B + g, n) or b''
    print('\n=== %s (base 0x%X) skate.all @0x%X ===' % (tag, B, SKALL))
    for off, nm in ((0x20, 'strm.zone*'), (0x24, 'strm.lang*'),
                    (0x1264, 'load.zone*'), (0x1268, 'load.lang*')):
        p = struct.unpack_from('>I', rd(SKALL + off, 4), 0)[0]
        # p may be a resolved guest addr (MEM2/0x1x) or still a 0xA6 alias
        s = rd(p, 40)
        z = s.find(b'\x00'); txt = s[:z if z >= 0 else 40]
        pr = txt.decode('latin1') if txt and all(32 <= c < 127 for c in txt) else s[:24].hex()
        print('  +0x%04X %-11s ptr=0x%08X -> "%s"' % (off, nm, p, pr))
    # the built filename buffer
    fn = rd(SKALL + 0x840, 64)
    z = fn.find(b'\x00')
    print('  +0x0840 strm.filename = "%s"' % fn[:z if z >= 0 else 64].decode('latin1', 'replace'))

show(NEW, 'NEW-crash-30528')
show(WORK, 'WORKING-playable')
