"""PASS/FAIL oracle for the sound-stream fix. Run against a dump of the fix boot:
  python _cascade_check.py "<dump path>"
- Task-Mgr full dump if it got INTO the map (working state), OR
- C:\CemuDumps\Cemu.exe.<pid>.dmp if it crashed.
PASS  = registry count 16, all 16 record ptrs non-zero, record[0]/[11] structured,
        the 0x103DD000..0x109D5000 region NOT high-entropy garbage.
FAIL  = count 12 / garbage records / big high-entropy blob (stomp still present)."""
import struct, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

path = sys.argv[1] if len(sys.argv) > 1 else r'C:\CemuDumps\Cemu.exe.15276.dmp'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
TBL_G = 0x119CF210; REC0_G = 0x1087E7CC; REC11_G = 0x1088B428
d = Dump(path)
hits = d.scan(ANCHOR, limit=1)
if not hits:
    print('anchor not in dump; if this is a CRASH dump use r13 base via _r2boot_triage.py');
BASE = hits[0] - ANCHOR_G if hits else None
print('dump: %s' % path)
if BASE is None:
    # crash-dump fallback: read r13 from context stream
    try:
        s, l = d.streams[6]; d.f.seek(l); b = d.f.read(s)
        cz, cr = struct.unpack_from('<II', b, 160); d.f.seek(cr); C = d.f.read(cz)
        BASE = struct.unpack_from('<Q', C, 0xE0)[0]   # r13
        print('using crash r13 base 0x%X' % BASE)
    except Exception as e:
        print('cannot determine base:', e); sys.exit(1)
else:
    print('base via anchor 0x%X' % BASE)
rd = lambda g, n: d.read(BASE + g, n) or b''
u32 = lambda b, o=0: struct.unpack_from('>I', b, o)[0]

tbl = rd(TBL_G, 8 + 16 * 4)
cnt = u32(tbl, 0) if len(tbl) >= 4 else -1
nonzero = sum(1 for i in range(16) if len(tbl) >= 12 + i * 4 and u32(tbl, 8 + i * 4))
print('\nregistry count = %d  (PASS=16, stomped=12)' % cnt)
print('non-zero bank ptrs = %d / 16' % nonzero)
for tag, g in (('record[0]', REC0_G), ('record[11]', REC11_G)):
    r = rd(g, 64)
    db = len(set(r))
    print('%s @0x%X distinct-bytes=%d %s' % (tag, g, db,
          '(structured, GOOD)' if db < 24 else '(HIGH ENTROPY = still stomped)'))

# blob check: sample the region that was 6MB garbage in the crash
sample = rd(0x105A0000, 0x400)
db = len(set(sample)) if sample else 0
print('\n0x105A0000 sample distinct-bytes=%d %s' % (db,
      '(garbage blob region clean -> GOOD)' if db < 40 else '(still garbage -> stomp present)'))

verdict = 'PASS (stomp fixed)' if (cnt == 16 and nonzero == 16) else 'FAIL (stomp present or crashed earlier)'
print('\n=== VERDICT: %s ===' % verdict)
