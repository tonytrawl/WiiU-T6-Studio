#!/usr/bin/env python3
"""
PC (T6 v147) little-endian zone reader — container only. Mirrors wiiu_zone.ZoneReader
but LE and with the PC asset enum used directly (no console remap). Used by the
PC->console pipeline to locate each asset body in the PC stream.

PC header is identical in shape to console: u32 size, u32 externalSize, u32 blockSize[8],
then XAssetList{stringCount, stringsPtr, dependCount, dependsPtr, assetCount, assetsPtr},
the script-string table (FOLLOW/null ptr array + inline strings), then the asset array.
"""
import struct, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import wiiu_zone as WZ

FOLLOW = 0xFFFFFFFF


class PCZoneReader:
    def __init__(self, zone: bytes):
        self.d = zone
        self.n = len(zone)
        self.size, self.external_size = struct.unpack_from('<II', zone, 0)
        self.block_sizes = list(struct.unpack_from('<8I', zone, 8))
        self.cur = 40
        self.strings = [""]
        self.assets = []            # (pc_id, pc_name, body_hint) -- body located later

    def u32(self, o): return struct.unpack_from('<I', self.d, o)[0]

    def read_string_table(self):
        o = self.cur
        self.string_count, sp, self.depend_count, dp, self.asset_count, ap = \
            struct.unpack_from('<6I', self.d, o)
        o += 24
        ptrs = struct.unpack_from('<%dI' % self.string_count, self.d, o)
        o += self.string_count * 4
        for p in ptrs:
            if p == FOLLOW:
                end = self.d.index(b'\x00', o)
                self.strings.append(self.d[o:end].decode('latin-1'))
                o = end + 1
        # Asset array immediately follows the inline strings (NOT 4-aligned; mp_raid
        # merely happened to land aligned, common_mp starts unaligned).
        self.assets_off = o
        return o

    def read_asset_list(self):
        o = self.assets_off
        for i in range(self.asset_count):
            t, hp = struct.unpack_from('<II', self.d, o + i*8)
            name = WZ.PC_ASSET_TYPES[t] if 0 <= t < len(WZ.PC_ASSET_TYPES) else None
            self.assets.append((t, name, hp))
        self.assets_end = o + self.asset_count * 8
        return self.assets


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '../PC ff/mp_raid.zone'
    z = PCZoneReader(open(path, 'rb').read())
    z.read_string_table(); z.read_asset_list()
    print("PC zone size=0x%x ext=0x%x blocks=%s" %
          (z.size, z.external_size, [hex(b) for b in z.block_sizes]))
    print("stringCount=%d assetCount=%d bodies start at stream 0x%x" %
          (z.string_count, z.asset_count, z.assets_end))
    from collections import Counter
    dist = Counter(nm for _, nm, _ in z.assets)
    print("first 12:", [(t, nm) for t, nm, _ in z.assets[:12]])
    print("histogram top:", dist.most_common(12))


if __name__ == '__main__':
    main()
