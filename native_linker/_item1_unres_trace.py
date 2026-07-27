"""Queue item 1: authoritative final-pass unresolved-tag trace.

Robust vs the earlier attempt:
  - reset detection by stats-OBJECT IDENTITY (each pass reset replaces
    omap.stats with a fresh dict) → the log holds ONLY the final pass's tags,
    in assignment order, so n matches the baked zone's 0xBF000001+n tags.
  - branch attribution by snapshotting WHICH unres:* counter increments (not
    a cumulative-counter guess).
Captures per tag: (n, root, name, pc_off_of_asset, branch, input_value).
The boot crashed on 0xBF000002 = n=2.
"""
import sys, pickle
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import produce_container as PC
import produce_nobackbone as PN
import material_convert as MC
import smalls_convert as SM
from measured_rtmap import MeasuredRuntimeMap
import map_config

_log = []
_state = {'sid': None}
_orig = PN.Omap.reloc
_UNRES_KEYS = ('unres:GfxWorld', 'unres:techset-interior', 'unres:<outside>')

def _traced(self, v):
    # new pass? (stats dict replaced) -> drop prior passes' records
    sid = id(self.stats)
    if sid != _state['sid']:
        _state['sid'] = sid
        _log.clear()
    before_u = self.stats.get('unresolved', 0)
    snap = {k: self.stats.get(k, 0) for k in _UNRES_KEYS}
    r = _orig(self, v)
    if self.stats.get('unresolved', 0) > before_u:
        n = self.stats['unresolved']
        branch = 'other'
        for k in _UNRES_KEYS:
            if self.stats.get(k, 0) > snap[k]:
                branch = k
                break
        ctx = self.ctx or (None, None, None, None)
        _log.append((n, ctx[2], ctx[1], ctx[3], branch, v))
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

from collections import Counter
print('final-pass unresolved tags: %d' % len(_log))
print('by (root, branch):')
for (rb), c in Counter((r[1], r[4]) for r in _log).most_common():
    print('   %-18s %-24s %d' % (rb[0], rb[1], c))
print('\nFIRST 14 tags (n=2 = the 0x5B5A6001 boot crash):')
for r in _log[:14]:
    print('   n=%-5d root=%-16s name=%-26s pc_off=%s branch=%-22s v=0x%08x'
          % (r[0], r[1], str(r[2])[:26], r[3], r[4], r[5]))
pickle.dump(_log, open('_item1_unres.pkl', 'wb'))
print('\nsaved _item1_unres.pkl  (zone md5-check: authored %d bytes)' % len(zone))
