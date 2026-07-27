#!/usr/bin/env python3
"""
_find_nullct.py — boot 23: find the material with a NULL constantTable whose techset still
demands a type-6 constant.

boot-23 fault: guest 0x0 (NULL read), R8=6, Rdx=0x3ec5a58c -> arg{type=6,dest=255,
value=0xe27483cf} followed by 'pimp_technique_unlit_c70...'. That fits R_GetPixelLiteralConsts:
    lwz r28, 0x58(r3)   ; material->constantTable == NULL
    lwz r29, 0(r28)     ; -> reads address 0 -> AV
i.e. NOT the unbounded walk (0x50000010) any more -- family 9's walk is closed.

BLIND SPOT this closes: every material scan since gfxtail14 anchored on `be32(Z,o+88)==FOLLOW`
(inline constantTable) and filtered `1 <= constc`. A material with NO constants has ct* == 0,
not FOLLOW -> such materials were NEVER scanned or audited. Re-anchor on name* == FOLLOW and
allow constc == 0.
"""
import re
import struct
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_zone
from _matconst_map import (CONSTDEF, FOLLOW, PTRS, be32, parse_material,
                           techset_const_hashes)

SRC = 'mp_skate_gfxtail19.zone'
WANT = 0xe27483cf
Z = open(SRC, 'rb').read()
isalias = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or isalias(v)

rc = wiiu_zone.ZoneReader(Z)
rc.read_string_table()
rc.read_asset_list()
em, spans, CO = LS.simulate(SRC, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}
demand = {}
for i, (s, e) in ts_spans.items():
    try:
        demand[i] = techset_const_hashes(Z, s)[0]
    except Exception:
        demand[i] = set()


def ts_name(s):
    if be32(Z, s) != FOLLOW:
        return None
    e = Z.find(b'\x00', s + 136)
    return bytes(Z[s + 136:e]).decode('latin1', 'replace')


print('techsets demanding 0x%08x: %s' % (WANT, [i for i, h in demand.items() if WANT in h]))
for i, h in demand.items():
    if WANT in h:
        print('   asset %-4d %-44s demands %d consts' % (i, ts_name(ts_spans[i][0]), len(h)))

arr = rc.assets_off - 64
our_arr = (arr + 7) & ~7


def ts_idx(a):
    v = (a - 1) & 0x1FFFFFFF
    if (v - our_arr - 4) % 8:
        return None
    k = (v - our_arr - 4) // 8
    return k if 0 <= k < len(rc.assets) else None


# --- re-anchored scan: name* == FOLLOW, ct* may be 0 (NULL) OR FOLLOW; constc may be 0 ---
mats = []
last = -1
for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), Z):
    o = m.start()
    if o < last or o + 104 > len(Z):
        continue
    if be32(Z, o + 0) != FOLLOW:              # name must be inline
        continue
    texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
    ct = be32(Z, o + 88)
    if not (texc <= 64 and constc <= 64 and sbc <= 64 and ptrish(ct)
            and all(ptrish(be32(Z, o + x)) for x in (80, 84, 92, 96))):
        continue
    if not isalias(be32(Z, o + 80)):          # needs a real techset alias
        continue
    try:
        info, nxt = parse_material(Z, o)
    except Exception:
        continue
    nm = info['name']
    if not nm or not (1 <= len(nm) <= 200) or not all(32 <= c < 127 for c in nm.encode('latin1', 'replace')):
        continue
    if constc and info['ct_off'] is not None:
        names = [Z[info['ct_off'] + k * CONSTDEF + 4:info['ct_off'] + k * CONSTDEF + 16]
                 for k in range(constc)]
        if not all(n[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in n) for n in names):
            continue
    info['_off'] = o
    info['_ct'] = ct
    mats.append(info)
    last = o + 104

zero = [m for m in mats if m['constc'] == 0]
print('\nmaterials found (name-anchored, constc>=0): %d   of which ZERO-constant: %d'
      % (len(mats), len(zero)))

print('\n*** ZERO-constant materials whose TECHSET still demands type-6 constants ***')
hits = []
for mm in zero:
    k = ts_idx(mm['ts'])
    if k is None or k not in demand or not demand[k]:
        continue
    hits.append((mm, k))
    print('  @file %-9d ct*=0x%08x  ts->asset %-4d %-40s demands %s%s'
          % (mm['_off'], mm['_ct'], k, ts_name(ts_spans[k][0]),
             ['0x%08x' % h for h in sorted(demand[k])][:4],
             '   <== DEMANDS 0x%08x' % WANT if WANT in demand[k] else ''))
    print('       material: %r' % mm['name'][:78])
print('\ntotal zero-constant materials bound to a demanding techset: %d' % len(hits))
