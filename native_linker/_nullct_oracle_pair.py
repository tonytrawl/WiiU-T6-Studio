#!/usr/bin/env python3
"""
_nullct_oracle_pair.py — the boot-23 upstream question, answered by NAME-PAIRING skate's 10
zero-constant decals against the genuine raid oracle.

_nullct_oracle.py established:
  raid : 283/578 materials have constc==0  -> zero-constant materials are NORMAL genuine content
  raid : 0 materials bound to a demanding techset are unsatisfied (65/65 satisfied)
  skate: 10 unsatisfied, all constc==0

So the genuine INVARIANT is `demand(techset) SUBSET carried(material)`, and it is NOT maintained
by "every material carries constants" -- it is maintained by ZERO-CONSTANT MATERIALS BINDING TO
ZERO-DEMAND TECHSETS. Our converter did NOT drop constants (raid ships the same decals with
constc==0). The defect is the BINDING: techset_translate.py substituted a DEMANDING techset for
a material that carries nothing.

This script pairs by material basename (prefix-stripped) to show, for each of the 10:
   skate: <prefix>/<name> constc=0 -> ts <k> <name> demands {h}
   raid : <prefix>/<name> constc=? -> ts <k> <name> demands {...}
"""
import re
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
from _nullct_oracle import scan

RAID = '../wiiu_ref/mp_raid_genuine.zone'
SKATE = 'mp_skate_gfxtail19.zone'


def base(n):
    return n.split('/', 1)[1] if '/' in n else n


def index(path):
    Z, mats, demand, ts_spans, ts_name, ts_idx = scan(path)
    out = {}
    for m in mats:
        k = ts_idx(m['ts'])
        d = demand.get(k, set()) if k is not None else set()
        nm = ts_name(ts_spans[k][0]) if (k is not None and k in ts_spans) else None
        out.setdefault(base(m['name']), []).append(
            dict(full=m['name'], constc=m['constc'], ts=k, tsname=nm, demand=d, off=m['_off']))
    return out, demand, ts_spans, ts_name


print('indexing skate ...')
sk, sk_dem, sk_spans, sk_tsname = index(SKATE)
print('indexing raid  ...')
rd, rd_dem, rd_spans, rd_tsname = index(RAID)

TEN = ['decal_damage_concrete_clean_line', 'decal_damage_wall_fillet',
       'decal_grunge_runway_tire_tracks', 'skt_decal_mural_02', 'skt_decal_mural_03',
       'skt_decal_mural_04', 'decal_damage_asphalt_crack02', 'dust_leaves_cigarettes_dec',
       'intro_wall_edgeworn01_decal', 'skt_bleacher_01_tarp_alpha']

print('\n%s' % ('=' * 78))
print('THE 10 vs THE ORACLE (paired by basename)')
print('=' * 78)
for b in TEN:
    print('\n--- %s' % b)
    for e in sk.get(b, []):
        if e['constc'] == 0 and e['demand']:
            print('  SKATE  %-46s constc=%d ts=%-4s %-34s demands %s'
                  % (e['full'], e['constc'], e['ts'], e['tsname'],
                     ['0x%08x' % h for h in sorted(e['demand'])]))
    # any sibling in skate itself with the same basename (different prefix)?
    for e in sk.get(b, []):
        if not (e['constc'] == 0 and e['demand']):
            print('   sib.  %-46s constc=%d ts=%-4s %-34s demands %s'
                  % (e['full'], e['constc'], e['ts'], e['tsname'],
                     ['0x%08x' % h for h in sorted(e['demand'])] or 'NOTHING'))
    got = rd.get(b, [])
    if not got:
        print('  RAID   (basename absent from oracle)')
    for e in got:
        print('  RAID   %-46s constc=%d ts=%-4s %-34s demands %s'
              % (e['full'], e['constc'], e['ts'], e['tsname'],
                 ['0x%08x' % h for h in sorted(e['demand'])] or 'NOTHING'))

# --- how do GENUINE zero-constant materials relate to their techsets? ---
print('\n%s' % ('=' * 78))
print('ORACLE RULE CHECK: what do raid ZERO-constant materials bind to?')
print('=' * 78)
z_nodem = z_dem = 0
for b, lst in rd.items():
    for e in lst:
        if e['constc'] == 0:
            if e['demand']:
                z_dem += 1
            else:
                z_nodem += 1
print('raid materials constc==0 bound to ZERO-demand techset : %d' % z_nodem)
print('raid materials constc==0 bound to a DEMANDING techset  : %d' % z_dem)

# --- do the two demanded hashes appear ANYWHERE in raid, and on what? ---
print('\n%s' % ('=' * 78))
print('WHO CARRIES 0xe27483cf / 0x88befc31 ?')
print('=' * 78)
for want in (0xe27483cf, 0x88befc31):
    for tag, idx in (('skate', sk), ('raid', rd)):
        carr = [e for lst in idx.values() for e in lst if want in set()]
        dem = sorted({(e['ts'], e['tsname']) for lst in idx.values() for e in lst
                      if want in e['demand']})
        print('  0x%08x  %-5s: demanded by %d techset(s) %s'
              % (want, tag, len(dem), [d[1] for d in dem][:4]))
