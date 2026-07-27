#!/usr/bin/env python3
"""Locate the 4 TECHNIQUE_SET asset bodies beyond the sim break and audit the 4
unaudited material binds (glass shatter / skybox / compass x2) against them.

Techset body signature: name*@0 == FOLLOW, 32 remap slots @+8 each in
{0, FOLLOW, alias}, printable name at +136. Byte-granular (§2).
"""
import re
import struct
import sys
from collections import Counter

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_zone
from _matconst_map import be32, walk_techset, FOLLOW, PTRS
from _nullct_oracle import scan
from _sampler_oracle import mat_texhashes, techset_args

SRC = 'mp_skate_gfxtail28.zone'
AL = lambda v: 0xA0000000 <= v < 0xC0000000
ok_slot = lambda v: v == 0 or v == FOLLOW or AL(v)

Z = open(SRC, 'rb').read()
_, mats, _, ts_spans, ts_name, ts_idx = scan(SRC)
rc = wiiu_zone.ZoneReader(Z)
rc.read_string_table()
rc.read_asset_list()

ts_rows = [i for i, (cid, pc, nm) in enumerate(rc.assets) if nm == 'TECHNIQUE_SET']
missing = sorted(set(ts_rows) - set(ts_spans))
print('TECHNIQUE_SET rows: %d   walked: %d   missing: %s' % (len(ts_rows), len(ts_spans), missing))

unaud = []
for mm in mats:
    if not AL(mm['ts']):
        continue
    k = ts_idx(mm['ts'])
    if k is not None and k not in ts_spans:
        unaud.append((mm, k))
for mm, k in unaud:
    print('unaudited bind: @%-9d %-44s -> asset %d' % (mm['_off'], (mm['name'] or '?')[:44], k))

# ---- signature scan beyond the last walked span ----
last_end = max(e for (s, e) in ts_spans.values())
print('last walked techset span ends at %d; scanning tail...' % last_end)
found = []
for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), Z[last_end:]):
    o = last_end + m.start()
    if o + 200 > len(Z):
        continue
    if be32(Z, o) != FOLLOW:
        continue
    if not all(ok_slot(be32(Z, o + 8 + i * 4)) for i in range(32)):
        continue
    e = Z.find(b'\x00', o + 136, o + 300)
    if e < 0:
        continue
    nm = Z[o + 136:e].decode('latin-1', 'replace')
    if not (3 <= len(nm) <= 120 and all(32 < c < 127 for c in nm.encode('latin-1', 'replace'))):
        continue
    # must actually walk as a techset
    try:
        passes, endo = walk_techset(Z, o)
    except Exception:
        continue
    if not passes:
        continue
    found.append((o, nm, len(passes)))
print('tail techset bodies found: %d' % len(found))
for o, nm, np_ in found:
    print('   @%-9d passes=%-3d %s' % (o, np_, nm))

# ---- audit each unaudited bind against each found body (order-matched) ----
# asset order == stream order for inline assets: sorted missing rows <-> sorted offsets
bodies = sorted(found)
if len(bodies) == len(missing):
    pair = dict(zip(missing, [b[0] for b in bodies]))
else:
    pair = {}
    print('!! count mismatch — no order pairing')

for mm, k in unaud:
    s = pair.get(k)
    if s is None:
        print('@%d %s: NO BODY for asset %d' % (mm['_off'], mm['name'], k))
        continue
    dem = set(v for (t, d, v) in techset_args(Z, s) if t == 2 and v not in PTRS)
    ch, kind = mat_texhashes(Z, mm['_off'])
    ch = set(ch or [])
    miss = dem - ch
    e = Z.find(b'\x00', s + 136)
    tnm = Z[s + 136:e].decode('latin-1')
    print('@%-9d %-40s asset %-3d ts %-36s demand=%d carried=%d(%s) miss=%s'
          % (mm['_off'], (mm['name'] or '?')[:40], k, tnm[:36], len(dem), len(ch), kind,
             ['0x%08x' % h for h in sorted(miss)] or 'NONE'))
