#!/usr/bin/env python3
"""PHASE 1 FINAL: for each of the 13, compare
     ACTUAL console techset (what the handle points at)   vs
     PC INTENT techset (what the PC source binds, by name) and ask whether the
     intent-named techset EXISTS on console and whether its demand is satisfied.
"""
import sys, re, struct, os
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
from collections import defaultdict
import loader_sim as LS
import pc_zone, wiiu_zone
from _matconst_map import be32, be16, walk_techset, PTRS, FOLLOW
from _p1_dump13 import carried, nm, material_at_name
from _p1_recover import H

AL = lambda v: 0xA0000000 <= v < 0xC0000000
le32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
_cstr = lambda d, o, n=200: (lambda e: d[o:e].decode('latin1', 'replace') if e >= 0 else None)(d.find(b'\x00', o, o + n))
CONST_TYPES = (0, 6)
SAMPLER_TYPE = 2

NAMES = ['*127n_236n_238(', '*127n_294n_236n(', '*127n_557n_238(', '*145n_5_192n(',
         '*1n_67n_175_5(', '*222_236n_65(', '*23n_73_71_65(', '*4n_5_192n(',
         '*53n_661_5_238(', '*67n_135(', '*75n_5_192n(', '*93n_192n(', 'wpc/shadowcaster']


def ts_demands(Z, s):
    passes, _ = walk_techset(Z, s)
    c, sm = set(), set()
    for p in passes:
        for j in range(p['nargs']):
            a = p['args_off'] + j * 8
            t, v = be16(Z, a), be32(Z, a + 4)
            if v in PTRS:
                continue
            if t in CONST_TYPES:
                c.add(v)
            elif t == SAMPLER_TYPE:
                sm.add(v)
    return c, sm


Z = open('mp_skate_final.zone', 'rb').read()
pc = open('../mp_skate_pc.zone', 'rb').read()

print('simulating console + PC ...')
emc, spansc, _ = LS.simulate('mp_skate_final.zone', verbose=False)
rc = wiiu_zone.ZoneReader(Z); rc.read_string_table(); rc.read_asset_list()
our_arr = ((rc.assets_off - 64) + 7) & ~7
NC = len(rc.assets)
def dec_k(a):
    p = (a - 1) & 0x1FFFFFFF; lo = our_arr + 4
    return (p - lo) // 8 if (lo <= p < lo + NC * 8 and (p - lo) % 8 == 0) else None

co_ts_span, co_name, name2k = {}, {}, defaultdict(list)
for (i, en, root, s, e) in spansc:
    if root != 'MaterialTechniqueSet':
        continue
    n_ = _cstr(Z, s + 136) if be32(Z, s) == FOLLOW else None
    co_ts_span[i] = s
    co_name[i] = (n_ or '').lstrip(',')
    if n_:
        name2k[(n_ or '').lstrip(',')].append(i)

empc, spanspc, _ = LS.simulate_pc(pc, verbose=False)
pc_ts_name = {sp[0]: (_cstr(pc, sp[3] + 152) or '').lstrip(',') for sp in spanspc
              if sp[2] == 'MaterialTechniqueSet'}
pc_ts_span = {sp[0]: sp[3] for sp in spanspc if sp[2] == 'MaterialTechniqueSet'}
prc = pc_zone.PCZoneReader(pc); prc.read_string_table(); prc.read_asset_list()
pc_arr = ((prc.assets_off - 64) + 7) & ~7
NPC = len(prc.assets)
def pc_dec_k(a):
    p = (a - 1) & 0x1FFFFFFF; lo = pc_arr + 4
    return (p - lo) // 8 if (lo <= p < lo + NPC * 8 and (p - lo) % 8 == 0) else None

print('console techsets %d (%d distinct names) | PC techsets %d\n'
      % (len(co_ts_span), len(name2k), len(pc_ts_name)))

ok_intent = 0
rows = []
for pat in NAMES:
    o = Z.find(pat.encode() if pat.endswith('(') else pat.encode() + b'\x00')
    b = material_at_name(Z, o)
    full = _cstr(Z, o)
    tex, tc, consts, kind, texc, constc = carried(Z, b)
    ctex, ccon = set(tex), set(consts)
    ka = dec_k(be32(Z, b + 80))
    actual = co_name.get(ka, '?')
    dca, dsa = ts_demands(Z, co_ts_span[ka]) if ka in co_ts_span else (set(), set())

    ipc = pc.find(pat.encode() if pat.endswith('(') else pat.encode() + b'\x00')
    pb = ipc - 112
    kpc = pc_dec_k(le32(pc, pb + 92))
    intent = pc_ts_name.get(kpc, '?')

    ks = name2k.get(intent) or []
    sib = re.sub(r'v\d?(?=(_|$))', '', intent)
    if not ks and sib != intent:
        ks = name2k.get(sib) or []
    if ks:
        ki = ks[0]
        dci, dsi = ts_demands(Z, co_ts_span[ki])
        miss_i = (dsi - ctex) | (dci - ccon)
        status = 'SATISFIED' if not miss_i else 'still misses ' + ' '.join(sorted(nm(h) for h in miss_i))
        if not miss_i:
            ok_intent += 1
    else:
        ki, status = None, 'INTENT NAME ABSENT FROM CONSOLE ZONE'
    miss_a = (dsa - ctex) | (dca - ccon)
    rows.append((full, ka, actual, kpc, intent, ki, status, miss_a))
    print('### %s' % full[:88])
    print('    carried      : tex[%d] %s | const[%d] %s'
          % (texc, ' '.join(nm(h) for h in tex), constc, ' '.join(nm(h) for h in consts)))
    print('    ACTUAL  k=%-4s %s' % (ka, actual))
    print('       misses    : %s' % (' '.join(sorted(nm(h) for h in miss_a)) or '-'))
    print('    PC INTENT k=%-4s %s' % (kpc, intent))
    print('       console slot for intent name: %s  -> %s' % (ki, status))
    print()

print('=' * 90)
print('rebinding to the PC-intent techset would SATISFY %d / 13' % ok_intent)
print('handles still decoding to the PC index (unrebound): %d / 13'
      % sum(1 for r in rows if r[1] == r[3]))
