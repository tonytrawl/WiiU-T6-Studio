"""CASCADE forensic round 2 (offline): the 16 static SndBank structs @0x1087E7CC
(4756B each, RPL BSS) got overwritten by ~76KB random data during skate load.
Pin: (a) EXACT garbage extent+alignment in the crash dump, (b) whether skate's own
mpl_skate.all bank header differs pipe-vs-playable zone, (c) identify the blob by
comparing to the real .sabs/.sabl files, (d) where skate data lands in WORK."""
import struct, sys, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

CRASH = r'C:\CemuDumps\Cemu.exe.15276.dmp'
WORK  = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
REC0_G = 0x1087E7CC
BODY = 4756  # 0x1294 SndBank struct stride
SND = r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000c\1010cf00\content\0010\sound"

dc = Dump(CRASH); dw = Dump(WORK)
BW = dw.scan(ANCHOR, limit=1)[0] - ANCHOR_G
hc = dc.scan(ANCHOR, limit=1); BC = (hc[0] - ANCHOR_G) if hc else 0x2565BB40000
print('BW=0x%X BC=0x%X' % (BW, BC))
rc = lambda g, n: dc.read(BC + g, n) or b''
rw = lambda g, n: dw.read(BW + g, n) or b''

# ---- (a) exact garbage extent: widen window well before/after ----
lo_g, hi_g = REC0_G - 0x2000, REC0_G + 16 * BODY + 0x2000
span = hi_g - lo_g
bc = rc(lo_g, span); bw = rw(lo_g, span)
m = min(len(bc), len(bw))
diffs = [i for i in range(m) if bc[i] != bw[i]]
if diffs:
    s, e = diffs[0], diffs[-1]
    print('\n[a] garbage extent (crash!=work): guest 0x%X .. 0x%X  size=%d (0x%X)'
          % (lo_g + s, lo_g + e, e - s + 1, e - s + 1))
    print('    start rel to REC0(0x%X): %+d   0x800-aligned start? %s   0x2000? %s'
          % (REC0_G, (lo_g + s) - REC0_G, ((lo_g + s) & 0x7ff) == 0, ((lo_g + s) & 0x1fff) == 0))
    print('    end   rel to REC0+16*BODY(0x%X): %+d' % (REC0_G + 16 * BODY, (lo_g + e + 1) - (REC0_G + 16 * BODY)))
    # is the region BEFORE the garbage identical (so the blob starts cleanly)?
    pre = [i for i in diffs if i < s + 8]
    print('    bytes just before start (work): ', bw[max(0, s - 16):s].hex())
    print('    bytes just before start (crash):', bc[max(0, s - 16):s].hex())

# ---- (b) diff mpl_skate.all bank header between the two zones ----
print('\n[b] mpl_skate.all bank header diff (pipecheck vs gfxtail46 zone) ...')
def find_bank(zbytes):
    for m in re.finditer(re.escape(b'mpl_skate.all\x00'), zbytes):
        c = m.start() - BODY
        if c >= 0 and struct.unpack_from('>I', zbytes, c)[0] == 0xFFFFFFFF:
            return c
    return None
ZP = open('mp_skate_pipecheck.zone', 'rb').read()
ZK = open('mp_skate_gfxtail46.zone', 'rb').read()
bp, bk = find_bank(ZP), find_bank(ZK)
print('    bank body off: pipe=0x%X key=0x%X' % (bp, bk))
hlen = 0x1280
hd = [i for i in range(hlen) if ZP[bp + i] != ZK[bk + i]]
print('    header diffs in first 0x%X bytes: %d' % (hlen, len(hd)))
FIELDS = {0x830: 'streamed.checksum[16]', 0x942: 'zp@0x942', 0x946: 'lp@0x946',
          0x1152: 'loaded.checksum[16]', 0x1264: 'zone*', 0x1268: 'language*',
          0x126c: 'loadedCount', 0x1270: 'entryCount', 0x1274: 'entries*',
          0x1278: 'dataSize', 0x127c: 'data*'}
for off, nm in sorted(FIELDS.items()):
    vp = struct.unpack_from('>I', ZP, bp + off)[0]
    vk = struct.unpack_from('>I', ZK, bk + off)[0]
    mark = '' if vp == vk else '  <<< DIFF'
    print('    +0x%04X %-22s pipe=0x%08X key=0x%08X%s' % (off, nm, vp, vk, mark))
# any header diffs outside the known fields?
unknown = [i for i in hd if not any(o <= i < o + (16 if 'checksum' in FIELDS[o] else 4) for o in FIELDS)]
if unknown:
    print('    UNCLASSIFIED header diff offsets:', ['0x%X' % (u) for u in unknown[:40]])

# ---- (c) identify the garbage blob: compare to skate .sabs/.sabl ----
print('\n[c] identify blob vs real .sab files ...')
if diffs:
    blob = rc(lo_g + s, min(e - s + 1, 0x200))   # first 512B of the garbage
    for fn in ('mpl_skate.all.sabs', r'loaded\mpl_skate.all.sabl'):
        try:
            fb = open(SND + '\\' + fn, 'rb').read()
        except Exception as ex:
            print('    (no %s: %s)' % (fn, ex)); continue
        # does the blob prefix appear in this .sab?
        idx = fb.find(blob[:32])
        print('    %-30s len=%d  blob[:32] found at file off %s' % (fn, len(fb), idx))
    # also: scan the WHOLE crash dump for the blob prefix (multiple copies = a buffer)
    hits = dc.scan(blob[:32], limit=6)
    print('    blob[:32] in CRASH dump @guest:', [hex(h - BC) for h in hits])

# ---- (d) where does skate's bank data live in WORK (correct placement)? ----
print('\n[d] skate resident data in WORK dump (scan for .sabs data signature) ...')
try:
    sabs = open(SND + r"\mpl_skate.all.sabs", 'rb').read()
    sig = sabs[0x40:0x60]
    hits = dw.scan(sig, limit=6)
    print('    .sabs[0x40:0x60] in WORK @guest:', [hex(h - BW) for h in hits])
    hits2 = dc.scan(sig, limit=6)
    print('    .sabs[0x40:0x60] in CRASH @guest:', [hex(h - BC) for h in hits2])
except Exception as ex:
    print('    (skate .sabs unreadable: %s)' % ex)
