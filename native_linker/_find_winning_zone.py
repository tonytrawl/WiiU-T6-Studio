#!/usr/bin/env python3
"""
_find_winning_zone.py — which GENUINE zone supplies the techset that beats skate's copy?

boot-21 proof: exactly 2 copies of 'wpc_sw4_3d_unlit_4layer_570jw7w9' exist in guest memory:
  0x31d44008  <- another zone, loaded EARLIER (lower addr). Its args still demand hdrAmount.
  0x3f47a6a2  <- OUR skate copy (patched, args remapped to 0x7793a248). NEVER USED.
The unpatched arg quartet appears 0 times in mp_skate_gfxtail18.zone, so 0x31d4xxxx is not ours.
T6 pools assets by name and the first registration wins => skate materials bind to the OTHER
zone's techset, which demands constants our converted materials do not carry.

Find the owner among the zones the boot-21 log shows being loaded before the map.
"""
import os
import re
import struct
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff

NAME = b'wpc_sw4_3d_unlit_4layer_570jw7w9\x00'
QUART = struct.pack('>HHI', 6, 248, 0x00e262b2) + struct.pack('>HHI', 6, 254, 0xc6ea3186)
D = r'E:\Wii U Black ops 2\content\english'
ZONES = ['code_pre_gfx.ff', 'code_pre_gfx_mp.ff', 'code_post_gfx.ff', 'code_post_gfx_mp.ff',
         'code_post_gfx_720.ff', 'code_post_gfx_720_mp.ff', 'common.ff', 'common_mp.ff',
         'common_patch_mp.ff', 'patch.ff', 'patch_mp.ff', 'ui_mp.ff', 'patch_ui_mp.ff',
         'faction_pmc_mp.ff', 'faction_fbi_mp.ff']


def get_zone(path):
    r = wiiu_ff.decrypt(open(path, 'rb').read())
    if isinstance(r, dict):
        for k in ('zone', 'data', 'decompressed', 'out'):
            if k in r and isinstance(r[k], (bytes, bytearray)):
                return bytes(r[k])
        for v in r.values():
            if isinstance(v, (bytes, bytearray)) and len(v) > 4096:
                return bytes(v)
        raise ValueError('dict keys=%s' % list(r))
    if isinstance(r, tuple):
        for v in r:
            if isinstance(v, (bytes, bytearray)) and len(v) > 4096:
                return bytes(v)
    return bytes(r)


for f in ZONES:
    p = os.path.join(D, f)
    if not os.path.exists(p):
        print('%-26s MISSING' % f)
        continue
    try:
        Z = get_zone(p)
    except Exception as e:
        print('%-26s decrypt failed: %s' % (f, str(e)[:60]))
        continue
    n = [m.start() for m in re.finditer(re.escape(NAME), Z)]
    q = [m.start() for m in re.finditer(re.escape(QUART), Z)]
    tag = '   <=== OWNS THE WINNING TECHSET' if n else ''
    print('%-26s zone=%9d  nameHits=%-3d quartetHits=%-3d%s' % (f, len(Z), len(n), len(q), tag))
