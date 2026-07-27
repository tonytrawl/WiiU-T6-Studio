"""Family 9 audit: locate console inline Materials by a STRICT signature, map each to
its techset (via the ts alias -> XAsset array slot -> asset index), and report materials
whose techset demands a type-6 constant their constantTable lacks."""
import sys, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_zone
from _matconst_map import (be32, parse_material, techset_const_hashes,
                           FOLLOW, INSERT, PTRS, CONSTDEF)

ZONE = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_gfxtail13.zone'
Z = open(ZONE, 'rb').read()
isalias = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or isalias(v)

rc = wiiu_zone.ZoneReader(Z); rc.read_string_table(); rc.read_asset_list()
print('assets=%d assets_off=0x%x assets_end=0x%x' % (len(rc.assets), rc.assets_off, rc.assets_end))
em, spans, CO = LS.simulate(ZONE, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}

# techset asset index -> demanded type-6 hashes
demand = {}
for i, (s, e) in ts_spans.items():
    try:
        hs, _ = techset_const_hashes(Z, s)
        demand[i] = hs
    except Exception:
        demand[i] = None
print('techsets parsed: %d/%d' % (sum(1 for v in demand.values() if v is not None), len(ts_spans)))

# our_arr: runtime b5 base of the XAsset array (slot aliases = our_arr + idx*8 + 4)
arr = rc.assets_off - 64
our_arr = (arr + 7) & ~7
def ts_idx(alias):
    v = (alias - 1) & 0x1FFFFFFF
    if (v - our_arr - 4) % 8:
        return None
    k = (v - our_arr - 4) // 8
    return k if 0 <= k < len(rc.assets) else None

# STRICT material scan
mats = []
N = len(Z)
o = 0
while o < N - 104:
    if Z[o + 0:o + 4] == b'\xff\xff\xff\xff':           # name* == FOLLOW
        texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
        ct = be32(Z, o + 88)
        ts = be32(Z, o + 80)
        if ct == FOLLOW and 1 <= constc <= 64 and texc <= 64 and sbc <= 64 \
           and ptrish(ts) and ptrish(be32(Z, o + 84)) and ptrish(be32(Z, o + 92)) \
           and ptrish(be32(Z, o + 96)):
            try:
                info, nxt = parse_material(Z, o)
                names = [Z[info['ct_off'] + k * CONSTDEF + 4:
                           info['ct_off'] + k * CONSTDEF + 16] for k in range(constc)]
                if all(n[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in n)
                       for n in names) and info['name']:
                    mats.append(info)
                    o = nxt
                    continue
            except Exception:
                pass
    o += 4
print('materials located (strict): %d' % len(mats))

HDR = 0x00e262b2
bad = 0; nots = 0; checked = 0
from collections import Counter
missing_hash = Counter()
for m in mats:
    k = ts_idx(m['ts']) if isalias(m['ts']) else None
    if k is None or k not in demand or demand[k] is None:
        nots += 1
        continue
    checked += 1
    miss = demand[k] - set(m['consts'])
    if miss:
        bad += 1
        for h in miss:
            missing_hash[h] += 1
print('materials with resolvable techset: %d (unresolved/inline-ts: %d)' % (checked, nots))
print('materials MISSING >=1 demanded constant: %d / %d' % (bad, checked))
print('top missing hashes:', [(hex(h), c) for h, c in missing_hash.most_common(8)])
print('hdrAmount missing in %d materials' % missing_hash.get(HDR, 0))
