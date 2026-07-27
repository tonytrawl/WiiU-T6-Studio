import sys, os
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
from collections import Counter
import _p2_inv as INV, _p2_perslot as PS
for path in ['../wiiu_ref/mp_raid_genuine.zone','../wiiu_ref/mp_dockside_wiiu.zone',
             '../wiiu_ref/zm_transit_original.zone','../wiiu_ref/Original FF/mp_hijacked.zone',
             '../wiiu_ref/Original FF/mp_slums.zone']:
    if not os.path.exists(path):
        print('MISSING', path); continue
    try:
        inv = INV.build(path, verbose=False)
    except Exception as e:
        print('FAIL', path, e); continue
    sd = {}
    ncorp = 0
    for k,t in inv['TS'].items():
        ps = PS.corpus_slots(t['name'])
        if ps is not None: ncorp += 1
        else:
            try: ps = PS.per_slot_demands(inv['Z'], t['span'])
            except Exception: ps = {}
        sd[k] = ps
    c = Counter(); mats = 0
    for M in inv['MATS']:
        v = [i for i,(cc,ss) in (sd.get(M['k']) or {}).items()
             if (cc - M['consts']) or (ss - M['tex'])]
        if v: mats += 1; c.update(v)
    print('%-42s mats=%-6d ts=%-4d corpus-resolved=%-4d violators=%-4d slots=%s'
          % (os.path.basename(path), len(inv['MATS']), len(inv['TS']), ncorp, mats, sorted(c.items())))
