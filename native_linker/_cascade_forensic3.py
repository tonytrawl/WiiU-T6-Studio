"""CASCADE forensic 3: (1) TRUE extent of the garbage blob (walk outward until
crash==work again), (2) the +0x20 rtBank_STREAMED head diff, (3) where the baked
zp/lp resolve in each dump and what's there, (4) what the blob start/end align to.
Goal: decide data-buffer misplacement (~11.4MB) vs struct-array corruption."""
import struct, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

CRASH = r'C:\CemuDumps\Cemu.exe.15276.dmp'
WORK  = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
REC0_G = 0x1087E7CC
dc = Dump(CRASH); dw = Dump(WORK)
BW = dw.scan(ANCHOR, limit=1)[0] - ANCHOR_G
BC = dc.scan(ANCHOR, limit=1)[0] - ANCHOR_G
rc = lambda g, n: dc.read(BC + g, n) or b''
rw = lambda g, n: dw.read(BW + g, n) or b''

# ---- (1) true extent: chunked outward walk from REC0 ----
CH = 0x1000
def same(g):
    a, b = rc(g, CH), rw(g, CH)
    if not a or not b or len(a) != len(b):
        return None      # unmapped edge
    return a == b
# walk down
g = REC0_G & ~0xFFF
while True:
    r = same(g - CH)
    if r is None or r:
        lo = g
        break
    g -= CH
# walk up
g = REC0_G & ~0xFFF
while True:
    r = same(g)
    if r is None or r:
        hi = g
        break
    g += CH
print('[1] garbage blob (chunk-resolution): guest 0x%X .. 0x%X  size~%d (0x%X)'
      % (lo, hi, hi - lo, hi - lo))
print('    dataSize for reference = 0x00AE7DC3 (%d)  16*4756=0x12940' % 0x00AE7DC3)
print('    blob start 0x800-aligned? %s  0x2000? %s' % ((lo & 0x7ff) == 0, (lo & 0x1fff) == 0))
# fine start within [lo-CH, lo+CH]
w = 0x2000
bc = rc(lo - CH, w + CH); bw2 = rw(lo - CH, w + CH)
mm = min(len(bc), len(bw2))
fd = next((i for i in range(mm) if bc[i] != bw2[i]), None)
if fd is not None:
    print('    fine start @guest 0x%X' % (lo - CH + fd))

# ---- (2) +0x20 head diff (both zones + both dumps at record[11]) ----
import re
BODY = 4756
def find_bank(z):
    for m in re.finditer(re.escape(b'mpl_skate.all\x00'), z):
        c = m.start() - BODY
        if c >= 0 and struct.unpack_from('>I', z, c)[0] == 0xFFFFFFFF:
            return c
    return None
ZP = open('mp_skate_pipecheck.zone', 'rb').read()
ZK = open('mp_skate_gfxtail46.zone', 'rb').read()
bp, bk = find_bank(ZP), find_bank(ZK)
print('\n[2] +0x00..0x30 of mpl_skate.all bank body (zone):')
print('    pipe:', ZP[bp:bp+0x30].hex())
print('    key :', ZK[bk:bk+0x30].hex())
REC11_G = 0x1088B428
print('    dump record[11] +0x00..0x30 WORK :', rw(REC11_G, 0x30).hex())
print('    dump record[11] +0x00..0x30 CRASH:', rc(REC11_G, 0x30).hex())

# ---- (3) resolve baked zp/lp in each dump ----
def alias_payload(v):
    return (v - 1) & 0x1FFFFFFF
print('\n[3] baked pointers:')
for nm, vp, vk in (('zp/zone*', 0xA61941EF, 0xA6192DB4),):
    print('    %s pipe=0x%08X (blk%d pay=0x%X) key=0x%08X (blk%d pay=0x%X) delta=%d'
          % (nm, vp, vp >> 29, alias_payload(vp), vk, vk >> 29, alias_payload(vk),
             alias_payload(vp) - alias_payload(vk)))
# these are block-5 aliases; can't resolve to absolute without the b5 base map.
# but check: is 0xA61941EF's payload anywhere near the BSS blob? (block mismatch => no)

# ---- (4) what sits right before/after the blob (identify neighbors) ----
print('\n[4] context around blob edges:')
print('    @0x%X (blob start-32): %s' % (lo - 32, rc(lo - 32, 48).hex()))
print('    work same region      : %s' % rw(lo - 32, 48).hex())
print('    @0x%X (blob end-16)   : %s' % (hi - 16, rc(hi - 16, 48).hex()))
print('    work same region      : %s' % rw(hi - 16, 48).hex())
