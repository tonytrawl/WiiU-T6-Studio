"""Final control: does the PASS-2 GUARD ever fire on GENUINE console output?
Uses the shipped predicate _actionable_violations with alias-resolved demands."""
import sys, os
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
import _p2_inv as INV, _p2_perslot as PS
import techset_rebind as TR
tot_m = tot_fire = 0
for p in ['../wiiu_ref/mp_raid_genuine.zone','../wiiu_ref/mp_dockside_wiiu.zone',
          '../wiiu_ref/zm_transit_original.zone','../wiiu_ref/Original FF/mp_hijacked.zone',
          '../wiiu_ref/Original FF/mp_slums.zone']:
    inv = INV.build(p, verbose=False)
    sd = {}
    for k,t in inv['TS'].items():
        ps = PS.corpus_slots(t['name'])
        if ps is None:
            try: ps = PS.per_slot_demands(inv['Z'], t['span'])
            except Exception: ps = {}
        sd[k] = ps
    fire = sum(1 for M in inv['MATS']
               if TR._actionable_violations(sd, M['k'], M['tex'], M['consts']))
    tot_m += len(inv['MATS']); tot_fire += fire
    print('%-40s materials=%-6d guard fires=%d' % (os.path.basename(p), len(inv['MATS']), fire))
print('TOTAL genuine materials=%d  guard fires=%d' % (tot_m, tot_fire))
