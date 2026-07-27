"""A/B: prove the FIX 6 wire-in changes NOTHING in a real build (AUDIT never
writes). Same parameters as _fx6_wirecheck.py but with fx_backref_fix.
fix_fx_backrefs monkeypatched to a no-op, so any md5 difference is attributable
to FIX 6 alone.

Compare the md5 printed here with the one from _fx6_wirecheck.py.
"""
import hashlib
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')

import gwmp_tree_fix
gwmp_tree_fix.fix_gwmp_nodetree = (
    lambda *a, **k: dict(status='bypassed', corrected=0))

import fx_backref_fix
fx_backref_fix.fix_fx_backrefs = (
    lambda *a, **k: dict(status='NOOP-AB-CONTROL', corrected=0))

from measured_rtmap import MeasuredRuntimeMap
from _skate6_stepA_reproduce import author

rtm = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
zone, info = author(rtm)
print('A/B CONTROL (FIX 6 disabled): %d bytes md5=%s'
      % (len(zone), hashlib.md5(zone).hexdigest()))
print('compare with _fx6_wirecheck.py md5 227165cfe94c3b2fe344a5eda166070b')
