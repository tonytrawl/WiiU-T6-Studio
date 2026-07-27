"""Queue item 1: definitively pin the poison-tag source by scanning each
emitted asset BODY (from author_zone's info['out_assets']) for low tags."""
import sys, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import produce_container as PC
import produce_nobackbone as PN
import material_convert as MC
import smalls_convert as SM
from measured_rtmap import MeasuredRuntimeMap
import map_config
import gfxworld_emit as GEM

map_config.apply('mp_skate')
_cfg = map_config.get('mp_skate')
pcp = LS.derive_pc_policy('../mp_skate_pc.zone', verbose=False)
rtm = MeasuredRuntimeMap('_skate2_simmap.pkl', '_skate2_realmap.pkl')
PN.INLINE_ASSET_NAMES = False
zone, info = PC.author_zone('../mp_skate_pc.zone', 'mp_skate', verbose=False,
                            pc_policy=pcp, our_policy=None, override_rtmap=rtm,
                            image_ipak=_cfg['image_ipaks'])

def low_tag(w):
    return 0xBF000001 <= w <= 0xBF0000FF

# GfxWorld emit-region map (to label a GfxWorld-internal tag's field)
PCz = open('../mp_skate_pc.zone', 'rb').read()
bodies, _ = PN.walk_pc_bodies(PCz)
gw = [(i, nm, root, s, e) for (i, nm, root, s, e, hp) in bodies if root == 'GfxWorld'][0]
_data, _fx, _log = GEM.emit_gfxworld(PCz, gw[3], ctx={'image_source': None,
                                     'sampler_lookup': None, 'defer_tail_rebase': True})
_rb = []; _co = 0
for (k, m, ln, nt) in _log:
    _rb.append((_co, _co + ln, k)); _co += ln
def gfx_region(off):
    for a, b, k in _rb:
        if a <= off < b:
            return '%s+%d' % (k, off - a)
    return '(end)'

# data regions in GfxWorld that legitimately carry -0.5f-ish tag-value floats
_DATA = {'draw.reflectionProbes', 'draw.lightmaps', 'draw.vd0', 'draw.vd1',
         'draw.indices', 'lightGrid.coeffs', 'lightGrid.rawRowData'}

print('scanning %d emitted asset bodies for low tags...' % len(info['out_assets']))
found = 0
for (i, nm, root, body, why) in info['out_assets']:
    if body is None:
        continue
    for o in range(0, len(body) - 3, 4):
        w = struct.unpack_from('>I', body, o)[0]
        if low_tag(w):
            if root == 'GfxWorld':
                reg = gfx_region(o)
                if any(reg.startswith(d) for d in _DATA):
                    continue      # -0.5f float in pixel/geom data, not a ptr
                print('  TAG asset[%s] %-14s %-10s off+%d val=0x%08x region=%s'
                      % (i, root, str(nm)[:10], o, w, reg))
            else:
                print('  TAG asset[%s] %-14s %-16s off+%d val=0x%08x'
                      % (i, root, str(nm)[:16], o, w))
            found += 1
            if found > 60:
                print('  ... (capping output)')
                break
    if found > 60:
        break
print('total pointer-position low tags: %d' % found)
