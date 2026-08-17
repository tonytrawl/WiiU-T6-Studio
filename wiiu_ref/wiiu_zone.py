#!/usr/bin/env python3
"""
Native big-endian Wii U (T6, v148) zone reader. Purpose-built, not bound to OAT's
PC struct assumptions. Reads the decompressed zone stream directly.

Stream model (all big-endian, 32-bit):
  header:  u32 size, u32 externalSize, u32 blockSize[8]
  content: the serialized asset graph. Pointer fields are markers:
             0xFFFFFFFF = FOLLOW (data written inline, next in stream order)
             0xFFFFFFFE = INSERT
             0x00000000 = null
             else       = offset-encoded alias to already-written data
  Dynamic data follows its owning struct in member order (DFS), which is how we
  advance the cursor.

Milestone 1: header + string table + full XAsset type list (all 889 for raid),
with the console(v148)->PC asset-enum remap so every id resolves to a real type.
"""
import struct, sys

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE

# PC (v147) T6 asset enum — OAT order.
PC_ASSET_TYPES = [
    "XMODELPIECES", "PHYSPRESET", "PHYSCONSTRAINTS", "DESTRUCTIBLEDEF", "XANIMPARTS",
    "XMODEL", "MATERIAL", "TECHNIQUE_SET", "IMAGE", "SOUND", "SOUND_PATCH", "CLIPMAP",
    "CLIPMAP_PVS", "COMWORLD", "GAMEWORLD_SP", "GAMEWORLD_MP", "MAP_ENTS", "GFXWORLD",
    "LIGHT_DEF", "UI_MAP", "FONT", "FONTICON", "MENULIST", "MENU", "LOCALIZE_ENTRY",
    "WEAPON", "WEAPONDEF", "WEAPON_VARIANT", "WEAPON_FULL", "ATTACHMENT",
    "ATTACHMENT_UNIQUE", "WEAPON_CAMO", "SNDDRIVER_GLOBALS", "FX", "IMPACT_FX",
    "AITYPE", "MPTYPE", "MPBODY", "MPHEAD", "CHARACTER", "XMODELALIAS", "RAWFILE",
    "STRINGTABLE", "LEADERBOARD", "XGLOBALS", "DDL", "GLASSES", "EMBLEMSET",
    "SCRIPTPARSETREE", "KEYVALUEPAIRS", "VEHICLEDEF", "MEMORYBLOCK", "ADDON_MAP_ENTS",
    "TRACER", "SKINNEDVERTS", "QDB", "SLUG", "FOOTSTEP_TABLE", "FOOTSTEPFX_TABLE",
    "ZBARRIER",
]


def console_to_pc(t):
    """Console (WiiU v148) asset id -> PC (v147) id.
    WiiU enum vs PC: ONE console-only type at id 7 (SNDDRIVER-ish, no PC equivalent), and MAP_ENTS
    relocated to id 47 (PC 16). So ids 8..46 shift -1, and ids >=48 shift -2 (MAP_ENTS occupies 47).
    This CORRECTS an earlier Xbox360-v146-derived rule that wrongly treated id 44 as console-only and
    used -2 past 44: that mislabeled LEADERBOARD(cid44)->None, XGLOBALS(cid45)->43, DDL(cid46)->44.
    Verified on genuine patch_mp: cid44 = 59 LeaderboardDef bodies, cid46 = 10 ddlRoot_t "*.ddl".
    raid/skate/dock map zones have NO cid 44/45/46 asset (only 43/47/48/50 in this range), so the
    map-zone round-trip gates are unaffected.

    ⚠ KNOWN-WRONG LABELS, kept for behaviour compatibility (rule AN, 2026-07-30): per the RPL's
    Load_XAssetHeader switch the true console ids are 47 = GLASSES (a 16-B no_follow stub; MapEnts
    is cid 17), 48 = TextureList and 7 = PixelShader (both with NO case in the switch — their
    headerPtr word is never read). This function still labels cid 47 'MAP_ENTS' because the
    validated walk configuration (63/63 zones EOF-exact) relabels those entries BY BODY SIGNATURE
    (raid_oracle_control._looks_like_glasses — see zone_walk.py / loader_sim), and loader_sim keys
    its Glasses handling on the 'MAP_ENTS' label. Changing the mapping here would alter every
    consumer at once and was never validated — do not do it casually. Gates that reason about
    headerPtr classes must EXCLUDE cid 7 and 48 (their word is engine-dead, §2b entry 16)."""
    if t == 47:
        return 16            # MAP_ENTS (relocated)
    if t == 7:
        return None          # console-only, no PC equivalent
    if t <= 6:
        return t
    if t <= 46:
        return t - 1
    return t - 2


class ZoneReader:
    def __init__(self, zone: bytes):
        self.d = zone
        self.n = len(zone)
        (self.size, self.external_size) = struct.unpack('>II', zone[0:8])
        self.block_sizes = list(struct.unpack('>8I', zone[8:40]))
        self.cur = 40  # content starts after header
        self.strings = []
        self.assets = []  # list of (console_id, pc_id, pc_name)

    def u32(self, o): return struct.unpack('>I', self.d[o:o+4])[0]

    def read_string_table(self):
        """XAssetList: stringCount, strings*, dependCount, depends*, assetCount, assets*."""
        o = self.cur
        self.string_count, strings_p, self.depend_count, depends_p, \
            self.asset_count, assets_p = struct.unpack('>6I', self.d[o:o+24])
        o += 24
        # strings pointer array: stringCount entries (FOLLOW or null)
        ptrs = struct.unpack('>%dI' % self.string_count, self.d[o:o + self.string_count*4])
        o += self.string_count * 4
        # inline string bytes for each FOLLOW pointer, in order
        self.strings = [""]  # index 0 conventionally empty
        for p in ptrs:
            if p == FOLLOW:
                end = self.d.index(b'\x00', o)
                self.strings.append(self.d[o:end].decode('latin-1'))
                o = end + 1
            # null pointers contribute no inline data
        # depends (dependCount) — rule (AM): actually consume the region. This line used to be a
        # comment ("skip if present"), so on any zone with dependCount > 0 assets_off was short by
        # the whole depends region (measured 8 B on a DC=1 zone: 4-B FOLLOW + name + NUL) and every
        # downstream number — asset types, container refs, spans — was garbage. NOT hypothetical:
        # the LIVE patch_mp is PROBE A with dependCount=1. Layout mirrors the strings region and is
        # validated in relink_validation/zone_facts.py: batched (DC ptr words, then DC inline names)
        # preferred; interleaved ([ptr][name\0] x DC) as fallback. For DC <= 1 the two are
        # byte-identical. DC=0 zones (raid, all retail maps) are untouched by this change.
        self.depends = []
        if self.depend_count:
            # engine parity (rule W): the loader's guard is `if (depends*)` — dependCount > 0 with
            # depends* == NULL means NO inline depends region. FOLLOW => parse; anything else is a
            # malformed container and raises (rule AB: fail loudly, never answer wrong).
            depends_p = struct.unpack_from('>I', self.d, 0x34)[0]
            if depends_p == FOLLOW:
                o = self._read_depends(o)
            elif depends_p != 0:
                raise ValueError('dependCount=%d but depends* = 0x%08X (expected FOLLOW or NULL)'
                                 % (self.depend_count, depends_p))
        # The asset array immediately follows the inline strings/depends — it is NOT 4-aligned.
        # (mp_raid happened to land aligned; common_mp starts at an unaligned offset.)
        self.assets_off = o
        return o

    def _read_depends(self, o0):
        """Consume the depends region at o0, filling self.depends. Returns the end offset.
        Tries the batched layout first (matches the strings region shape), then interleaved.
        Raises if neither parses — a mis-modelled depends region silently garbles everything
        after it, and rule (AB) says fail loudly rather than answer wrong."""
        DC = self.depend_count
        d, n = self.d, self.n

        def try_batched():
            o = o0 + 4 * DC
            if o > n:
                return None
            ptrs = struct.unpack_from('>%dI' % DC, d, o0)
            if any(p != FOLLOW for p in ptrs):
                return None
            names = []
            for _ in range(DC):
                e = d.find(b'\x00', o)
                if e < 0 or e - o > 128:
                    return None
                names.append(d[o:e].decode('latin-1')); o = e + 1
            return names, o

        def try_interleaved():
            o, names = o0, []
            for _ in range(DC):
                if o + 4 > n or struct.unpack_from('>I', d, o)[0] != FOLLOW:
                    return None
                o += 4
                e = d.find(b'\x00', o)
                if e < 0 or e - o > 128:
                    return None
                names.append(d[o:e].decode('latin-1')); o = e + 1
            return names, o

        pick = try_batched() or try_interleaved()
        if pick is None:
            raise ValueError('dependCount=%d but neither depends layout parses at 0x%x' % (DC, o0))
        self.depends, o = pick
        return o

    def read_asset_list(self):
        """Asset array: assetCount * XAsset{ u32 type; u32 headerPtr } = 8 bytes each."""
        o = self.assets_off
        for i in range(self.asset_count):
            ctype = self.u32(o + i*8)
            pc = console_to_pc(ctype)
            name = PC_ASSET_TYPES[pc] if (pc is not None and 0 <= pc < len(PC_ASSET_TYPES)) else None
            self.assets.append((ctype, pc, name))
        self.assets_end = o + self.asset_count * 8
        return self.assets


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'mp_raid_genuine.zone'
    z = ZoneReader(open(path, 'rb').read())
    print("zone size=0x%x externalSize=0x%x" % (z.size, z.external_size))
    print("blocks:", [hex(b) for b in z.block_sizes])
    z.read_string_table()
    print("stringCount=%d  (parsed %d strings)  dependCount=%d  assetCount=%d" %
          (z.string_count, len(z.strings)-1, z.depend_count, z.asset_count))
    print("sample strings:", [s for s in z.strings[1:9]])
    z.read_asset_list()
    # validate: every console id must remap to a real type
    from collections import Counter
    bad = [(i, c) for i, (c, pc, nm) in enumerate(z.assets) if nm is None]
    dist = Counter(nm for _, _, nm in z.assets)
    print("\nasset-type histogram (%d assets, %d distinct):" % (len(z.assets), len(dist)))
    for nm, cnt in dist.most_common():
        print("  %5d  %s" % (cnt, nm))
    print("\nunresolved/console-only ids: %d" % len(bad))
    if bad:
        print("  first few:", bad[:10])
    print("\nfirst 12 assets (console_id -> pc_name):")
    for i, (c, pc, nm) in enumerate(z.assets[:12]):
        print("  [%d] console=%d -> %s" % (i, c, nm))


if __name__ == '__main__':
    main()
