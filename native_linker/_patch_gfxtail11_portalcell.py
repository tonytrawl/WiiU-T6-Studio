#!/usr/bin/env python3
"""gfxtail11: fix skate GfxWorld portal->cell (+32) stale aliases (boot-11 crash).

Boot 11 (gfxtail10) reached R_LoadWorld -> R_GenerateShadowMapCasterCells ->
R_VisitPortalsForCellNoFrustum, AV (r3=0xf) walking cell 0's portals. Dump
(crashdump\crash_20260715_1260403.dmp, BASE 0x1da8c6e0000): all 318 GfxPortal
+32 (portal->cell, a b5-alias cross-ref) pointers resolve to intended_cell-12
(216 land on cells[k]+36 == cells[k+1]-12; 102 land on CELLS-12 == cells[0]-12;
mod-48 distribution 100% {36}). Same -12 stale-omap offset as the clipMap planes
family. FOLLOW pointers (cell->portals/aabbTree, portal->vertices) resolve fine.
Fix: +12 to every portal+32 alias. Verified: +12 lands ALL 318 on clean cell
boundaries in [0,38). Size-neutral.
"""
import struct, hashlib, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from gfxworld_regions import walk_cells

B5V = 0x3C5A5FC0
CELLS_GUEST = 0x401625ac          # from dump (deterministic guest layout)
NC = 38
dec = lambda v: (v & 0x1FFFFFFF) - 1 + 64
isalias = lambda v: 0xA0000000 <= v < 0xC0000000

z = bytearray(open('mp_skate_gfxtail10.zone', 'rb').read())

# locate cells region by cell0 bounds + portalCount/aabbCount signature
needle = struct.pack('>3f', -28672.0, -30208.0, -5120.0)
CELLS = None
i = z.find(needle)
while i != -1:
    if struct.unpack_from('>I', z, i + 32)[0] == 102 and struct.unpack_from('>I', z, i + 24)[0] == 1028:
        CELLS = i; break
    i = z.find(needle, i + 1)
assert CELLS is not None, 'cells region not found'
subs, _ = walk_cells(bytes(z), False, CELLS, NC)
portal_spans = [(off, cnt) for k, off, cnt in subs if k == 'portals']

# pre-validate: every +32 alias, +12, lands on a clean cell boundary in range
n = 0
for off, cnt in portal_spans:
    for j in range(cnt):
        o = off + j * 92 + 32
        v = struct.unpack_from('>I', z, o)[0]
        assert isalias(v), '+32 not alias @0x%x: 0x%08x' % (o, v)
        tgt = (B5V + dec(v)) + 12
        rel = tgt - CELLS_GUEST
        assert rel % 48 == 0 and 0 <= rel // 48 < NC, \
            'portal @0x%x +12 -> 0x%08x = cells%+d (not clean/in-range)' % (o, tgt, rel)
        n += 1
print('validated %d portal+32 aliases: +12 lands all on clean cell boundaries [0,%d)' % (n, NC))

# patch
for off, cnt in portal_spans:
    for j in range(cnt):
        o = off + j * 92 + 32
        v = struct.unpack_from('>I', z, o)[0]
        nv = v + 12
        assert nv >> 29 == v >> 29           # block unchanged
        struct.pack_into('>I', z, o, nv)
print('patched %d portal->cell aliases (+12)' % n)

open('mp_skate_gfxtail11.zone', 'wb').write(z)
print('mp_skate_gfxtail11.zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open('mp_skate_gfxtail11.ff', 'wb').write(ff)
print('mp_skate_gfxtail11.ff md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
