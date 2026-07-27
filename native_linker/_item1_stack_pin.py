"""Queue item 1: pin the poison-tag SOURCE FIELD by capturing the Python call
stack at every GfxWorld-span reloc miss (the branch that emits 0xBF00000n).
Dedups by (calling frame, target GfxWorld region) so the distinct dangle
sources + their fields are named directly — no value-scanning."""
import sys, traceback
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import produce_container as PC
import produce_nobackbone as PN
import material_convert as MC
import smalls_convert as SM
from measured_rtmap import MeasuredRuntimeMap
import map_config
import gfxworld_emit as GEM

# PC GfxWorld region map (target classification)
PCz = open('../mp_skate_pc.zone', 'rb').read()
_bodies, _ = PN.walk_pc_bodies(PCz)
_gw = [(i, nm, root, s, e) for (i, nm, root, s, e, hp) in _bodies if root == 'GfxWorld'][0]
GWO = _gw[3]; GFX_LO = GWO - 64
_marks = GEM._pc_marks(PCz, GWO)   # (key, pc_a, pc_b) file offsets
def target_region(b5s):
    f = b5s + 64
    for (k, a, b) in _marks:
        if a <= f < b:
            return k
    return '(gfx-body/pre-marks)'

from collections import Counter
_seen = Counter()
_samples = {}
_orig = PN.Omap.reloc
_UK = ('unres:GfxWorld', 'unres:techset-interior')

def _traced(self, v):
    snap = {k: self.stats.get(k, 0) for k in _UK}
    r = _orig(self, v)
    if self.stats.get('unres:GfxWorld', 0) > snap['unres:GfxWorld']:
        # a GfxWorld-span miss just tagged. Where does v point + who called?
        try:
            b5 = (v - 1) & 0x1FFFFFFF
            b5s = self.pc_inv.stream(b5) if self.pc_inv else b5
            tr = target_region(b5s)
        except Exception:
            tr = '?'
        st = traceback.extract_stack(limit=8)[:-1]
        # top non-produce_nobackbone frame = the converter/field site
        site = None
        for fr in reversed(st):
            fn = fr.filename.replace('\\', '/').split('/')[-1]
            if fn not in ('produce_nobackbone.py',):
                site = '%s:%d %s' % (fn, fr.lineno, (fr.line or '')[:60])
                break
        key = (site, tr)
        _seen[key] += 1
        if key not in _samples:
            _samples[key] = (v, self.ctx)
    return r
PN.Omap.reloc = _traced

map_config.apply('mp_skate')
_cfg = map_config.get('mp_skate')
pcp = LS.derive_pc_policy('../mp_skate_pc.zone', verbose=False)
rtm = MeasuredRuntimeMap('_skate2_simmap.pkl', '_skate2_realmap.pkl')
PN.INLINE_ASSET_NAMES = False
zone, info = PC.author_zone('../mp_skate_pc.zone', 'mp_skate', verbose=False,
                            pc_policy=pcp, our_policy=None, override_rtmap=rtm,
                            image_ipak=_cfg['image_ipaks'])

print('distinct (call-site, target-region) GfxWorld-miss classes: %d' % len(_seen))
for (site, tr), n in _seen.most_common():
    v, ctx = _samples[(site, tr)]
    print('  x%-5d target=%-24s v=0x%08x' % (n, tr, v))
    print('        site: %s' % site)
    print('        ctx : %s' % (ctx,))
