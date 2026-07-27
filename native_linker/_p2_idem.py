import sys, os
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
import techset_rebind as TR, _p2_inv as INV, _p2_perslot as PS
PC = open('../mp_skate_pc.zone','rb').read()
Z1 = open('_p2_skate_pass2.zone','rb').read()
Z2 = TR.rebind_matmem_techsets(Z1, PC, 'mp_skate', verbose=True)
print('IDEMPOTENT:', Z2 == Z1, ' delta bytes:', sum(1 for a,b in zip(Z1,Z2) if a!=b))
print()
# does the guard fire on OUR raid pipeline build (green control lane)?
for p in ['mp_raid_authored.zone','mp_raid_native.zone']:
    if not os.path.exists(p): print('missing', p); continue
    inv = INV.build(p, verbose=False)
    sd = {}
    for k,t in inv['TS'].items():
        try: sd[k] = PS.per_slot_demands(inv['Z'], t['span'])
        except Exception: sd[k] = {}
    fire = [M['name'] for M in inv['MATS']
            if TR._actionable_violations(sd, M['k'], M['tex'], M['consts'])]
    print('%-28s materials=%-6d guard fires=%d %s' % (p, len(inv['MATS']), len(fire), fire[:3]))
