#!/usr/bin/env python3
"""
SndBank loadedAssets — console conversion (POLISH session, 2026-07-10).

KEY FINDING (retires the handoff premise, 2-map validated): the "inline loadedAssets blob"
in the zone is NOT audio. On genuine console zones the entry table AND the data blob are
ALL ZEROS — a runtime-filled buffer allocation (verified: mp_raid 654 entries + 11,519,896
bytes all zero; common_mp 810 entries + 23,951,128 bytes all zero). On PC the same region
contains uninitialized linker-heap garbage (not parseable audio). OAT's loader confirms the
semantics: entries/data are Alloc'd (zero) capacity; `runtimeAssetLoad = true`; the engine
fills the buffer at runtime from the on-disc loaded bank `sound/loaded/<bank>.all.sabl`
(console) / `sound/<bank>.all.sabl` (PC).

So the audio conversion happens at the FILE level, which wiiu_ref's sab_convert already
solved (PCM/FLAC -> 2/3-rate DSP-ADPCM, format 9). This module:
  1. converts a map's PC .sabl/.sabs pair to console format (delegates to sab_convert),
  2. computes the ZONE authoring numbers (entryCount, dataSize) for the console SndBank
     from the PC zone's own values (spec below),
  3. provides find_sndbank_body() so the assemble session can locate/patch the fields.

ZONE FIELD SPEC (assemble session):
  entryCount: copy the PC value verbatim. (Genuine console is +2/+3 higher on raid/common —
    console-added aliases we do not ship; runtime fills entries only for aliases present,
    which are the PC set, so the PC count is self-consistent capacity.)
  dataSize:   ceil(PC_dataSize * CONSOLE_RATIO) rounded up to 2048. Calibrated on both
    oracles: genuine console/PC = 0.1972 (mp_raid 11,519,896/58,421,162) and 0.1948
    (common_mp 23,951,128/122,939,726); CONSOLE_RATIO=0.21 gives ~7% headroom.
    Copying the PC dataSize verbatim also WORKS (capacity semantics) but wastes ~4/5 of
    the buffer (e.g. mp_skate: 48.5 MB PC -> ~10.2 MB console-sized).
  entries/data bytes: emit ZEROS (both platforms do).
  The rest of the loadedAssets head (zone/language string aliases etc.) is the field-aware
  swap the assemble side already handles.
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

CONSOLE_RATIO = 0.21          # calibrated: genuine 0.195-0.197 + headroom
FOLLOW = 0xFFFFFFFF


def find_sndbank_body(zone_bytes, endian='<', body=4756):
    """Locate SndBank bodies by the loadedAssets signature at body+0x1270:
    entryCount u32, entries*=FOLLOW, dataSize u32, data*=FOLLOW, with name*@0 and
    alias*@8 = FOLLOW. Returns list of (body_off, name, entryCount, dataSize)."""
    out = []
    for m in re.finditer(rb'\xff\xff\xff\xff(....)\xff\xff\xff\xff', zone_bytes, re.S):
        b = m.start() - 0x1274
        if b < 0:
            continue
        ec = struct.unpack_from(endian + 'I', zone_bytes, b + 0x1270)[0]
        ds = struct.unpack_from(endian + 'I', zone_bytes, m.start() + 4)[0]
        np_ = struct.unpack_from(endian + 'I', zone_bytes, b)[0]
        ap = struct.unpack_from(endian + 'I', zone_bytes, b + 8)[0]
        ac = struct.unpack_from(endian + 'I', zone_bytes, b + 4)[0]
        if np_ == FOLLOW and ap == FOLLOW and 0 < ec < 100000 and \
                0 < ds < 500000000 and 0 < ac < 100000:
            nul = zone_bytes.index(b'\x00', b + body)
            name = zone_bytes[b + body:nul].decode('latin-1', 'replace')
            out.append((b, name, ec, ds))
    return out


def console_zone_fields(pc_entry_count, pc_data_size):
    """(entryCount, dataSize) to author into the console SndBank loadedAssets."""
    ds = int(pc_data_size * CONSOLE_RATIO + 2047) & ~2047
    return pc_entry_count, ds


def convert_map_banks(map_name, pc_sound_dir, out_dir, verbose=True):
    """Convert <map>'s PC .sabl/.sabs to console format via sab_convert.
    Console placement: .sabl -> content/sound/loaded/, .sabs -> content/sound/."""
    import sab_convert as SC
    os.makedirs(out_dir, exist_ok=True)
    results = {}
    for ext in ('sabl', 'sabs'):
        src = os.path.join(pc_sound_dir, 'mpl_%s.all.%s' % (map_name, ext))
        if not os.path.isfile(src):
            src = os.path.join(pc_sound_dir, '%s.all.%s' % (map_name, ext))
        if not os.path.isfile(src):
            if verbose:
                print('  (no %s bank for %s)' % (ext, map_name))
            continue
        dst = os.path.join(out_dir, os.path.basename(src))
        if verbose:
            print('converting %s -> %s' % (src, dst))
        SC.convert_bank(src, dst, verbose=verbose)
        results[ext] = dst
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Convert a map\'s PC sound banks to console '
                                 'and report the zone loadedAssets authoring numbers')
    ap.add_argument('map_name', help='e.g. skate (matches mpl_<name>.all.*)')
    ap.add_argument('--pc-zone', help='PC zone file (to read entryCount/dataSize)')
    ap.add_argument('--pc-sound-dir', default=r'E:\pluto_t6_full_game\sound')
    ap.add_argument('-o', '--out-dir', default='converted_banks')
    args = ap.parse_args()
    if args.pc_zone:
        z = open(args.pc_zone, 'rb').read()
        for b, name, ec, ds in find_sndbank_body(z):
            cec, cds = console_zone_fields(ec, ds)
            print('PC bank %-20s @0x%x: entryCount=%d dataSize=%d -> console '
                  'entryCount=%d dataSize=%d' % (name, b, ec, ds, cec, cds))
    convert_map_banks(args.map_name, args.pc_sound_dir, args.out_dir)


if __name__ == '__main__':
    main()
