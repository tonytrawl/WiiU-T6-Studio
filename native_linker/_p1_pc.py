#!/usr/bin/env python3
"""PHASE 1 step 3: what does the PC SOURCE say for the 13? (intent, by construction)"""
import sys, re, struct, os
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import pc_zone
from _p1_recover import H
from _p1_dump13 import nm

FOLLOW = 0xFFFFFFFF
AL = lambda v: 0xA0000000 <= v < 0xC0000000
le32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
_cstr = lambda d, o, n=200: (lambda e: d[o:e].decode('latin1', 'replace') if e >= 0 else None)(d.find(b'\x00', o, o + n))

NAMES = ['*127n_236n_238(', '*127n_294n_236n(', '*127n_557n_238(', '*145n_5_192n(',
         '*1n_67n_175_5(', '*222_236n_65(', '*23n_73_71_65(', '*4n_5_192n(',
         '*53n_661_5_238(', '*67n_135(', '*75n_5_192n(', '*93n_192n(', 'wpc/shadowcaster']

pc = open('../mp_skate_pc.zone', 'rb').read()
print('simulating PC zone ...')
empc, spanspc, _ = LS.simulate_pc(pc, verbose=False)
pc_ts_name = {sp[0]: _cstr(pc, sp[3] + 152) for sp in spanspc if sp[2] == 'MaterialTechniqueSet'}
prc = pc_zone.PCZoneReader(pc); prc.read_string_table(); prc.read_asset_list()
pc_our_arr = ((prc.assets_off - 64) + 7) & ~7
NPC = len(prc.assets)


def pc_dec_k(alias):
    p = (alias - 1) & 0x1FFFFFFF
    lo = pc_our_arr + 4
    return (p - lo) // 8 if (lo <= p < lo + NPC * 8 and (p - lo) % 8 == 0) else None


print('PC techsets: %d' % len(pc_ts_name))
for pat in NAMES:
    i = pc.find(pat.encode() if pat.endswith('(') else pat.encode() + b'\x00')
    b = i - 112
    assert le32(pc, b) == FOLLOW, pat
    full = _cstr(pc, i)
    ts = le32(pc, b + 92)
    k = pc_dec_k(ts)
    tsn = (pc_ts_name.get(k) or '?').lstrip(',')
    counts = tuple(pc[b + 84:b + 88])
    print('\n### %s' % full)
    print('    PC body@%d  bytes[84..88]=%s  ts=0x%08x k=%s' % (b, counts, ts, k))
    print('    PC techset : %s' % tsn)
