#!/usr/bin/env python3
"""
_hunt_stale_source.py — find the REAL source of the pre-gfxtail14 techset bytes.

Boot 22 killed the "stale base-content mp_skate.ff" theory: that file was renamed aside and the
stale bytes are STILL resident at the SAME guest addresses (0x31d45a34...). So they come from a
zone that IS loaded.

Correction to the earlier hunt: the title is mounted with an UPDATE partition that overlays
/vol/content/, so the genuine zones actually loaded are (update first, then base):
  mlc01\\usr\\title\\0005000e\\1010cf00\\content\\english\\   <- update (v128) — NEVER SEARCHED
  E:\\Wii U Black ops 2\\content\\english\\                    <- base
Prime suspects: dlc0..5_load_mp.ff / seasonpass_load_mp.ff — LOAD ZONES THIS PROJECT BUILT
(batch_loadzones.py). They load BEFORE the map, so their techsets would win the name-keyed pool.

Search every zone the boot-22 log opens for the unpatched quartet + the techset name.
"""
import os
import re
import struct
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff

ORIG = (struct.pack('>HHI', 6, 248, 0x00e262b2) + struct.pack('>HHI', 6, 254, 0xc6ea3186)
        + struct.pack('>HHI', 6, 253, 0xc93e49a5) + struct.pack('>HHI', 6, 252, 0xcb4e41c4))
NAME = b'wpc_sw4_3d_unlit_4layer_570jw7w9\x00'

DIRS = [
    r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\content\english',
    r'E:\Wii U Black ops 2\content\english',
    r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000c\1010cf00\content\0010\english',
]
# every .ff the boot-22 log opens
LOADED = ['code_pre_gfx.ff', 'code_pre_gfx_mp.ff', 'code_post_gfx.ff', 'code_post_gfx_mp.ff',
          'code_post_gfx_720.ff', 'code_post_gfx_720_mp.ff', 'common_mp.ff', 'common_patch_mp.ff',
          'patch.ff', 'patch_mp.ff', 'ui_mp.ff', 'patch_ui_mp.ff',
          'faction_pmc_mp.ff', 'faction_fbi_mp.ff',
          'dlc0_load_mp.ff', 'dlc1_load_mp.ff', 'dlc2_load_mp.ff', 'dlc3_load_mp.ff',
          'dlc4_load_mp.ff', 'dlc5_load_mp.ff', 'dlczm0_load_mp.ff', 'seasonpass_load_mp.ff',
          'en_common_loc_mp.ff', 'en_mp_skate_loc.ff', 'mp_skate.ff']


def get_zone(path):
    r = wiiu_ff.decrypt(open(path, 'rb').read())
    if isinstance(r, dict):
        for v in r.values():
            if isinstance(v, (bytes, bytearray)) and len(v) > 1024:
                return bytes(v)
        raise ValueError('dict')
    if isinstance(r, tuple):
        for v in r:
            if isinstance(v, (bytes, bytearray)) and len(v) > 1024:
                return bytes(v)
    return bytes(r)


seen = set()
print('%-24s %-10s %10s %6s %6s' % ('file', 'partition', 'zone', 'QUART', 'NAME'))
print('-' * 66)
for D in DIRS:
    part = ('UPDATE' if '0005000e' in D else 'DLC/aoc' if '0005000c' in D else 'base')
    if not os.path.isdir(D):
        print('  [dir missing] %s' % D)
        continue
    for f in LOADED:
        p = os.path.join(D, f)
        if not os.path.exists(p) or (f, part) in seen:
            continue
        seen.add((f, part))
        try:
            Z = get_zone(p)
        except Exception as e:
            print('%-24s %-10s  decrypt failed: %s' % (f, part, str(e)[:24]))
            continue
        nq = len(re.findall(re.escape(ORIG), Z))
        nn = len(re.findall(re.escape(NAME), Z))
        flag = '   <=== THE STALE SOURCE' if nq else ''
        if nq or nn:
            print('%-24s %-10s %10d %6d %6d%s' % (f, part, len(Z), nq, nn, flag))
        else:
            print('%-24s %-10s %10d %6d %6d' % (f, part, len(Z), nq, nn))
