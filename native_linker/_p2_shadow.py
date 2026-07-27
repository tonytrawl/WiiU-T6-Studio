#!/usr/bin/env python3
"""PHASE 2: why does OUR wpc_shadowcaster_wj6w5j60 body demand 4 samplers when the
GENUINE raid body of the same name demands 1?  Parse the corpus blob directly."""
import sys, os, json, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import _p2_inv as INV
import techset_translate as TT
from _p1_dump13 import nm

ROOT = TT.ROOT
idx = json.load(open(os.path.join(TT.CORPUS_DIR, 'index.json')))
NAME = 'wpc_shadowcaster_wj6w5j60'
meta = idx[NAME]
p = meta['path']
if not os.path.isabs(p):
    p = os.path.join(ROOT, p)
blob = open(p, 'rb').read()
print('corpus blob %s: %d bytes (from %s)' % (NAME, len(blob), meta['zone']))
dc, ds = INV.ts_demands(blob, 0)
print('  corpus blob demand: const=%s samp=%s'
      % (' '.join(sorted(nm(h) for h in dc)) or '-',
         ' '.join(sorted(nm(h) for h in ds)) or '-'))

# our zone's body bytes for the same name
inv = INV.build('mp_skate_final.zone', verbose=False)
Z, TS, name2k = inv['Z'], inv['TS'], inv['name2k']
for k in name2k[NAME]:
    s = TS[k]['span']
    print('  ours k=%d span@%d  first 32B: %s' % (k, s, Z[s:s + 32].hex()))
    print('  corpus       first 32B: %s' % blob[:32].hex())
    # how far do they agree?
    n = min(len(blob), 40000)
    d = next((i for i in range(n) if Z[s + i] != blob[i]), None)
    print('  first differing byte offset: %s' % d)

# same-name body inside genuine raid
gz = open('../wiiu_ref/mp_raid_genuine.zone', 'rb').read()
gi = INV.build('../wiiu_ref/mp_raid_genuine.zone', verbose=False)
for k in gi['name2k'][NAME]:
    s = gi['TS'][k]['span']
    n = min(len(blob), 40000)
    d = next((i for i in range(n) if gz[s + i] != blob[i]), None)
    print('  GENUINE k=%d span@%d  blob-vs-genuine first diff: %s' % (k, s, d))
