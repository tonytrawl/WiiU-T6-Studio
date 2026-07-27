"""Locate the CLIPMAP_PVS(805) body in pipeline vs answer key, read the name field
(body[0]) — the BSP-not-found suspect — and classify the 25KB clipMap diff (is it
the name/pointer fields, or the collision geometry data?)."""
import struct, sys, pickle, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
ZP = open('mp_skate_mountfix.zone', 'rb').read()
ZK = open('mp_skate_gfxtail46.zone', 'rb').read()
sim = pickle.load(open('_skate2_simmap.pkl', 'rb'))
ae = sim['assets_end']
# simmap is pre-GfxWorld-growth (-11089 stale); the actual is +11089.
DRIFT = 11089
cm = next((i, tn, root, s, e) for (i, tn, root, s, e) in sim['spans'] if tn == 'CLIPMAP_PVS')
i, tn, root, s, e = cm
fs, fe = s + ae + DRIFT, e + ae + DRIFT
print('CLIPMAP_PVS(%d) span (drift-corrected): file 0x%X..0x%X (%d bytes)' % (i, fs, fe, fe-fs))

def dec(v):
    if 0xA0000000 <= v < 0xC0000000: return 'b5 0x%X' % ((v-1)&0x1FFFFFFF)
    if 0x80000000 <= v < 0xA0000000: return '*b4* 0x%X' % ((v-1)&0x1FFFFFFF)
    if v == 0xFFFFFFFF: return 'FOLLOW'
    return '0x%X' % v

# clipMap_t body[0] = name*. Print first 0x40 bytes of the body in both.
print('\nclipMap body head (pipe vs key), first 16 words:')
for w in range(0, 0x40, 4):
    vp = struct.unpack_from('>I', ZP, fs+w)[0]
    vk = struct.unpack_from('>I', ZK, fs+w)[0]
    mk = '' if vp == vk else '  <<< DIFF'
    print('  +0x%02X pipe=0x%08X [%s]  key=0x%08X [%s]%s' % (w, vp, dec(vp), vk, dec(vk), mk))

# bsp string location + which alias payload points there
bsp = b'maps/mp/mp_skate.d3dbsp\x00'
bpos = ZK.find(bsp)
print('\nbsp string @file 0x%X' % bpos)

# classify the diff across the clipMap body
diffs = [x for x in range(fs, min(fe, len(ZP), len(ZK))) if ZP[x] != ZK[x]]
print('\nclipMap total diff bytes: %d' % len(diffs))
# bucket into 0x1000 chunks to see WHERE (head=pointers, body=geometry)
from collections import Counter
c = Counter((x - fs) // 0x1000 for x in diffs)
print('diff distribution (offset-from-body-start // 0x1000 : count), first 20:')
for k in sorted(c)[:20]:
    print('  +0x%05X00 : %d' % (k*0x10, c[k]))
print('...' if len(c) > 20 else '', 'total diff chunks:', len(c))
# show a few sample diff words to see if they are pointers (alias) or data
print('\nsample diff words:')
shown = 0
for x in diffs:
    if x % 4 == 0 and shown < 14:
        vp = struct.unpack_from('>I', ZP, x)[0]; vk = struct.unpack_from('>I', ZK, x)[0]
        print('  @0x%X (body+0x%X) pipe=0x%08X [%s] key=0x%08X [%s]'
              % (x, x-fs, vp, dec(vp), vk, dec(vk)))
        shown += 1
