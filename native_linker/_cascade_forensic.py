"""CASCADE (offline, no boot): identify WHAT overwrote the global sound-bank
registry during mp_skate.ff load. The DB heap is deterministic across builds
(same GUEST addresses) so we read the same guest addrs in the CRASH (pipeline,
stomped) and WORKING (playable, valid) dumps and diff.

Table  @guest 0x119CF210 : +0 count(u32 BE), +4 flag, +8 record-ptr array
Records @guest 0x1087E7CC.. (16 bank records ~4756B apart, last 0x1088FE78)
Goal: (a) confirm the stomp extent, (b) characterize the garbage, (c) find the
SOURCE buffer that got copied over the table (scan working dump for the garbage)."""
import struct, sys, collections
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

CRASH = r'C:\CemuDumps\Cemu.exe.15276.dmp'
WORK  = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
TBL_G = 0x119CF210
REC0_G = 0x1087E7CC
RECLAST_G = 0x1088FE78

dc = Dump(CRASH); dw = Dump(WORK)


def base_via_anchor(d):
    h = d.scan(ANCHOR, limit=1)
    return (h[0] - ANCHOR_G) if h else None


BW = base_via_anchor(dw)
BC = base_via_anchor(dc)
print('WORK base via anchor: %s' % (hex(BW) if BW else None))
print('CRASH base via anchor: %s' % (hex(BC) if BC else '(not in dump; using r13)'))
if BC is None:
    BC = 0x2565BB40000   # handoff: crash BASE = r13
print('CRASH base used: 0x%X' % BC)

rc = lambda g, n: dc.read(BC + g, n) or b''
rw = lambda g, n: dw.read(BW + g, n) or b''
u32 = lambda b, o=0: struct.unpack_from('>I', b, o)[0]

# ---- 1. the registry table @0x119CF210 ----
print('\n=== registry table @guest 0x%X ===' % TBL_G)
tc = rc(TBL_G, 8 + 16 * 4); tw = rw(TBL_G, 8 + 16 * 4)
print('CRASH count=%d flag=%d' % (u32(tc, 0), u32(tc, 4)) if len(tc) >= 8 else ('CRASH table unmapped',))
print('WORK  count=%d flag=%d' % (u32(tw, 0), u32(tw, 4)) if len(tw) >= 8 else ('WORK table unmapped',))
print('record-ptr array (idx: CRASH  WORK):')
for i in range(16):
    vc = u32(tc, 8 + i * 4) if len(tc) >= 12 + i * 4 else 0
    vw = u32(tw, 8 + i * 4) if len(tw) >= 12 + i * 4 else 0
    mark = '' if vc == vw else '  <-- DIFF'
    print('  [%2d] 0x%08X   0x%08X%s' % (i, vc, vw, mark))

# ---- 2. the records region: where does CRASH diverge from WORK? ----
print('\n=== records region 0x%X..0x%X (diff crash vs work) ===' % (REC0_G, RECLAST_G + 0x100))
span = (RECLAST_G + 0x200) - REC0_G
bc = rc(REC0_G, span); bw = rw(REC0_G, span)
print('read crash=%d work=%d bytes' % (len(bc), len(bw)))
m = min(len(bc), len(bw))
diffs = [i for i in range(m) if bc[i] != bw[i]]
if diffs:
    lo, hi = diffs[0], diffs[-1]
    print('first diff @guest 0x%X  last diff @guest 0x%X  (%d bytes span, %d differing)'
          % (REC0_G + lo, REC0_G + hi, hi - lo + 1, len(diffs)))
    # entropy-ish: distinct byte count in the crash divergent window
    win = bc[lo:hi + 1]
    print('crash divergent-window distinct bytes = %d (structured<~16, garbage>~50)' % len(set(win)))
    # show the boundary
    b0 = max(0, lo - 16)
    print('  work  @0x%X: %s' % (REC0_G + b0, bw[b0:lo + 16].hex()))
    print('  crash @0x%X: %s' % (REC0_G + b0, bc[b0:lo + 16].hex()))
else:
    print('records region IDENTICAL in both dumps (stomp is elsewhere)')

# ---- 3. identify the SOURCE of the garbage: scan WORK for the crash garbage ----
# take a 64-byte fingerprint from the middle of the crash divergent window
if diffs:
    fp_off = REC0_G + diffs[len(diffs) // 2]
    fp = rc(fp_off & ~0xF, 64)
    print('\n=== garbage fingerprint @guest 0x%X ===' % (fp_off & ~0xF))
    print('  ', fp.hex())
    ascii_fp = ''.join(chr(c) if 32 <= c < 127 else '.' for c in fp)
    print('   ascii:', ascii_fp)
    # scan the WORKING dump for this exact fingerprint -> tells us what buffer it is
    if len(set(fp)) > 8:   # only worth scanning if it's distinctive
        hits = dw.scan(fp[:32], limit=5)
        print('   fingerprint[:32] found in WORK dump at host:', [hex(h) for h in hits],
              '-> guest:', [hex(h - BW) for h in hits])
        hits2 = dc.scan(fp[:32], limit=5)
        print('   fingerprint[:32] found in CRASH dump at host:', [hex(h) for h in hits2],
              '-> guest:', [hex(h - BC) for h in hits2])

# ---- 4. record[0] content in both (sanity: work should be 'mpl_patch.english') ----
print('\n=== record[0] @0x%X first 64B ===' % REC0_G)
print('WORK :', rw(REC0_G, 64).hex())
print('       ', ''.join(chr(c) if 32 <= c < 127 else '.' for c in rw(REC0_G, 64)))
print('CRASH:', rc(REC0_G, 64).hex())
print('       ', ''.join(chr(c) if 32 <= c < 127 else '.' for c in rc(REC0_G, 64)))
