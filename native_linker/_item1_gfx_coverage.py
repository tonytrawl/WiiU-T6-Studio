"""Queue item 1 (offline, fast): GfxWorld-interior fixup COVERAGE analysis.

For skate's GfxWorld: emit it, build region_pairs, then for every alias fixup
classify resolved-by-fine vs would-TAG, bucketed by the emit region the fixup
lives in AND (for tagged ones) the PC target region it points at. Names the
region_pairs coverage gaps the boot crash comes from — independent of the
(slow) full-build trace.
"""
import sys, struct, bisect
from collections import Counter
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import produce_nobackbone as PN
import material_convert as MC
import gfxworld_emit as GEM

PC = open('../mp_skate_pc.zone', 'rb').read()
bodies, _ = PN.walk_pc_bodies(PC)
gw = [(i, nm, root, s, e) for (i, nm, root, s, e, hp) in bodies if root == 'GfxWorld'][0]
GWO = gw[3]
print('PC GfxWorld @ %d..%d' % (gw[3], gw[4]))

# PC loader sim -> inverse (PC runtime alias -> PC stream)
em_pc, _, _ = LS.simulate_pc(PC, verbose=False)
pc_inv = LS.InverseMap(em_pc.omap)
gfx_lo, gfx_hi = GWO - 64, gw[4] - 64      # PC b5 span of GfxWorld

data, fx, log = GEM.emit_gfxworld(
    PC, GWO, ctx={'image_source': None, 'sampler_lookup': None,
                  'defer_tail_rebase': True})
pairs = GEM.region_pairs(PC, GWO, log)
print('emit: %d bytes, %d fixups, %d regions, %d pairs' % (len(data), len(fx), len(log), len(pairs)))

# emit-region boundaries (console body offsets) from log
reg_bounds = []      # (start, end, key)
co = 0
for (key, method, ln, note) in log:
    reg_bounds.append((co, co + ln, key))
    co += ln
def emit_region(off):
    for (a, b, k) in reg_bounds:
        if a <= off < b:
            return k
    return '(end)'

# PC-side coverage from pairs: [pa-64, pa-64+cl_eff)
cover = []           # (pc_b5_lo, pc_b5_hi, key)
for (pa, pb, co_, cl, meth, key) in pairs:
    if pb - pa == cl:
        cover.append((pa - 64, pa - 64 + cl, key))
    elif key == 'materialMemory':
        nmm = struct.unpack_from('<I', PC, GWO + 572)[0]
        cover.append((pa - 64, pa - 64 + nmm * 8, key))
    else:
        cover.append((pa - 64, pa - 64 + 4, key))
cover.sort()
clo = [c[0] for c in cover]
def covered(b5):
    i = bisect.bisect_right(clo, b5) - 1
    if i >= 0 and cover[i][0] <= b5 < cover[i][1]:
        return cover[i][2]
    return None

AL = lambda v: 0xA0000000 <= v < 0xC0000000
res = Counter(); tag_by_emit = Counter(); tag_targets = []
for f in fx:
    v = struct.unpack_from('>I', data, f)[0]
    if not AL(v):
        res['non-alias-fixup'] += 1
        continue
    b5 = (v - 1) & 0x1FFFFFFF
    b5s = pc_inv.stream(b5)
    if not (gfx_lo <= b5s < gfx_hi):
        res['not-gfx-interior'] += 1     # resolved by another reloc branch
        continue
    ck = covered(b5s)
    if ck is not None:
        res['resolved-by-fine'] += 1
    else:
        res['WOULD-TAG'] += 1
        er = emit_region(f)
        tag_by_emit[er] += 1
        tag_targets.append((f, er, b5s, v))
print('\nfixup classification:', dict(res))
print('\nWOULD-TAG by emit region (the coverage gaps):')
for k, c in tag_by_emit.most_common():
    print('   %-24s %d' % (k, c))
# where do the tagged targets POINT (which PC region)? bucket by nearest cover gap
print('\nsample tagged fixups (emit_region -> pc_target_b5, offset into GfxWorld):')
for (f, er, b5s, v) in tag_targets[:16]:
    print('   fixup@%-7d region=%-22s -> pc_b5=%d (gfx+%d) v=0x%08x'
          % (f, er, b5s, b5s - gfx_lo, v))
