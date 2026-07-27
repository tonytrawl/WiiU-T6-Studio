"""Walk the runtime SndBank registry in the NEW crash dump (30528) and the WORKING
dump, resolve each bank's name* pointer, and find the one whose name is garbage
(= mpl_skate.english). Compare its name* between dumps to pin the bad resolution."""
import struct, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

NEW  = r'C:\CemuDumps\Cemu.exe.30528.dmp'
WORK = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
TBL_G = 0x119CF210

def walk(path, tag):
    d = Dump(path)
    h = d.scan(ANCHOR, limit=1)
    if not h:
        print('[%s] anchor not found' % tag); return
    BASE = h[0] - ANCHOR_G
    rd = lambda g, n: d.read(BASE + g, n) or b''
    tbl = rd(TBL_G, 8 + 16 * 4)
    cnt = struct.unpack_from('>I', tbl, 0)[0]
    print('\n[%s] base=0x%X  registry count=%d' % (tag, BASE, cnt))
    for i in range(16):
        bp = struct.unpack_from('>I', tbl, 8 + i * 4)[0]
        if not bp:
            continue
        # bank struct @ guest bp; name* @ +0
        body = rd(bp, 0x30)
        if len(body) < 4:
            print('  [%2d] bank@0x%08X UNMAPPED' % (i, bp)); continue
        namep = struct.unpack_from('>I', body, 0)[0]
        # resolve name* -> string
        ns = rd(namep, 48)
        # trim at nul
        z = ns.find(b'\x00')
        name = ns[:z if z >= 0 else 48]
        printable = all(32 <= c < 127 for c in name) and len(name) > 0
        show = name.decode('latin1') if printable else ('GARBAGE ' + ns[:24].hex())
        # also show state @+0x1288, filename @+0x840
        st = rd(bp + 0x1288, 4)
        state = struct.unpack_from('>I', st, 0)[0] if len(st) >= 4 else -1
        print('  [%2d] bank@0x%08X name*=0x%08X state=%d  name=%s%s'
              % (i, bp, namep, state, show, '' if printable else '  <<< CORRUPT'))
    return d, BASE, rd

walk(NEW, 'NEW-crash-30528')
walk(WORK, 'WORKING-playable')
