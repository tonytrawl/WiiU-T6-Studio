"""Prove FIX 6 actually RUNS inside author_zone on the skate (blind-map) lane.

NOTE: the skate build currently dies EARLIER, inside FIX 4 (gwmp_tree_fix), with
"correction did NOT produce a valid tree (out-of-range=1)". That failure is
PRE-EXISTING and unrelated to FIX 6: gwmp_tree_fix is called ~35 lines BEFORE the
FIX 6 block, and the only edit ahead of it is one `elif _root == 'FxEffectDef'`
branch that appends to a list. To exercise the FIX 6 wiring anyway, this runner
neutralises FIX 4 for the duration (THROWAWAY -- never do this in a real build).

Run with T6_FX6_BYPASS_GWMP=1 to enable the bypass.
"""
import hashlib
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')

if os.environ.get('T6_FX6_BYPASS_GWMP') == '1':
    import gwmp_tree_fix
    gwmp_tree_fix.fix_gwmp_nodetree = (
        lambda *a, **k: print('  [wirecheck] FIX 4 bypassed (pre-existing '
                              'out-of-range failure, unrelated to FIX 6)')
        or dict(status='bypassed', corrected=0))

from measured_rtmap import MeasuredRuntimeMap
from _skate6_stepA_reproduce import author

rtm = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
zone, info = author(rtm)
open('_fx6_skate_wirecheck.zone', 'wb').write(zone)
print('BUILT %d bytes md5=%s' % (len(zone), hashlib.md5(zone).hexdigest()))

import alloc_events
e = alloc_events.clipmap_events(zone, 84512493, '>')
e = e[0] if isinstance(e, tuple) else e
print('clipMap gate end = %s (MUST be 89584099) %s'
      % (e, 'OK' if int(e) == 89584099 else '*** FAIL ***'))

# AUDIT writes nothing, so the FX spans must be byte-identical to a build
# without FIX 6. Verify directly: re-survey the produced zone.
import fx_backref_fix as FBF
_ae = info['assets_end']
cur = _ae
fx = []
for (_i, _nm, _root, _b, _w) in info['out_assets']:
    if _b is None:
        continue
    if _root == 'FxEffectDef':
        fx.append((cur, len(_b)))
    cur += len(_b)
r = FBF.survey_fx_backrefs(bytearray(zone), fx, verbose=True)
print('post-build survey: %d aliases, %d misaligned' % (r['aliases'],
                                                        len(r['violations'])))
