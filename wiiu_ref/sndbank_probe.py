#!/usr/bin/env python3
"""
SOUND / SndBank console (Wii U v148) layout probe.

FINDING: console SndBank serializes PC-IDENTICALLY (byte-swap only).
Verified byte-exact on Wii U mp_raid: both banks walked from the SOUND[0]
body (located by chaining the preceding FX assets) across 12.9 MB
(12,467 aliases + an 11.5 MB inline loadedAssets data blob) landing
exactly on the XANIMPARTS body at 0x0521e1e5. Also verified on Wii U
common_mp (common.all bank, 23.9 MB data, resyncs onto the next asset).

  SndBank body = 4756 bytes in mp_raid on BOTH platforms (the pack(1)
  SndRuntimeAssetBank x2 with their 2048-byte SndAssetBankHeader dominate;
  gcc_align32(8) int64 members do NOT change the console size).
  OPEN: genuine Wii U common_mp uses a 4760-byte body (one extra u32 in
  the tail after scriptIdLookups); the loadedAssets offsets 0x1264..0x127c
  are identical in both variants.
  PC RESIDUAL: the PC walk of mpl_raid.all ends 46 KB short of the PC
  XANIMPARTS body (PC-only detail, likely subtitle/alignment related; the
  PC read path is already solved inside OAT, so not chased here).

  Head: +0 name* +4 aliasCount +8 alias* +12 aliasIndex* +16 radverbCount
  +20 radverbs* +24 duckCount +28 ducks* +32 streamAssetBank (pack1,
  zone*@32 language*@36) ... loadAssetBank ... loadedAssets
  {zone*@0x1264, language*@0x1268, loadedCount@0x126c, entryCount@0x1270,
  entries*@0x1274, dataSize@0x1278, data*@0x127c},
  scriptIdLookupCount@0x1280, scriptIdLookups*@0x1284, state/tail bytes.
  Dynamics, in member order:
    name string
    alias: aliasCount x SndAliasList(20) {name*,id,head*,count,sequence},
      then per list: name string, head -> count x SndAlias(100), then per
      alias: subtitle/secondaryName/assetFileName strings (the alias name*
      aliases its list's name string, so it consumes nothing).
    aliasIndex: aliasCount x SndIndexEntry(4)
    radverbs: radverbCount x 100
    ducks: duckCount x SndDuck(76), per duck attenuation/filter FOLLOW ->
      32 x f32 (128 B each)
    zone/language strings for every FOLLOW pointer in body[32..0x126c)
    entries: entryCount x SndAssetBankEntry(20)
    data: dataSize bytes of inline sound sample data
    scriptIdLookups: count x 8
  SndAliasFlags is 8 bytes on both platforms; only its BIT layout is the
  known console bitfield question (section 0d) - it does not affect stream
  consumption. The flags dword pair is byte-swapped per-u32 like all words.
"""
import struct, sys

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

BODY = 4756
ALIASLIST = 20
ALIAS = 100
RADVERB = 100
DUCK = 76


def parse_sndbank(d, b, e, body=None):
    body = body or BODY

    def u32(o):
        return struct.unpack(e + 'I', d[o:o+4])[0]

    name_p, aliasCount, alias_p, aliasIndex_p, radverbCount, radverbs_p, \
        duckCount, ducks_p = struct.unpack(e + '8I', d[b:b+32])
    o = b + body
    name = None
    if name_p in PTRS:
        nul = d.index(b'\x00', o)
        name = d[o:nul].decode('latin-1')
        o = nul + 1
    stats = dict(aliases=0, strings=0)
    if alias_p in PTRS:
        base = o
        o += aliasCount * ALIASLIST
        for i in range(aliasCount):
            lb = base + i * ALIASLIST
            lname_p, lid, head_p, cnt, seq = struct.unpack(
                e + '5I', d[lb:lb+ALIASLIST])
            if lname_p in PTRS:
                nul = d.index(b'\x00', o)
                o = nul + 1
                stats['strings'] += 1
            if head_p in PTRS:
                ab = o
                o += cnt * ALIAS
                stats['aliases'] += cnt
                for k in range(cnt):
                    a = ab + k * ALIAS
                    for po in (a+0, a+8, a+12, a+20):   # name/sub/sec/file
                        if u32(po) in PTRS:
                            nul = d.index(b'\x00', o)
                            o = nul + 1
                            stats['strings'] += 1
    if aliasIndex_p in PTRS:
        o += aliasCount * 4
    if radverbs_p in PTRS:
        o += radverbCount * RADVERB
    if ducks_p in PTRS:
        base = o
        o += duckCount * DUCK
        for i in range(duckCount):
            db = base + i * DUCK
            for po in (db+64, db+68):                   # attenuation/filter
                if u32(po) in PTRS:
                    o += 32 * 4
    # zone/language strings of the embedded runtime banks / loadedAssets
    for po in range(32, 0x126c, 4):
        if u32(b + po) == FOLLOW:
            nul = d.index(b'\x00', o)
            o = nul + 1
            stats['strings'] += 1
    entryCount = u32(b + 0x1270)
    dataSize = u32(b + 0x1278)
    if u32(b + 0x1274) == FOLLOW:
        o += entryCount * 20                            # SndAssetBankEntry
    if u32(b + 0x127c) == FOLLOW:
        o += dataSize
        stats['dataSize'] = dataSize
    cnt = u32(b + 0x1280)
    if u32(b + 0x1284) == FOLLOW:
        o += cnt * 8                                    # scriptIdLookups
    return o, name, aliasCount, stats


def main():
    d = open('mp_raid_genuine.zone', 'rb').read()
    b = 0x45bea9e
    for i in (0, 1):
        end, name, ac, st = parse_sndbank(d, b, '>')
        print('WU bank[%d] @0x%08x %-22s aliasLists=%-5d %s end=0x%08x' %
              (i, b, name, ac, st, end))
        b = end
    print('   -> expected next asset (XANIMPARTS fxanim_gp_umbrella) '
          '@0x0521e1e5 : %s' % ('BYTE-EXACT' if b == 0x0521e1e5 else
                                'MISMATCH (0x%x)' % b))
    dp = open('../PC ff/mp_raid.zone', 'rb').read()
    end, name, ac, st = parse_sndbank(dp, 0x5bcc5a6, '<')
    print('PC bank[0] @0x05bcc5a6 %-22s aliasLists=%-5d %s end=0x%08x '
          '(PC residual: short of the PC xanim body, see docstring)' %
          (name, ac, st, end))
    # second genuine Wii U zone: common_mp (4760-byte body variant)
    dc = open('../common_mp.zone', 'rb').read()
    i = dc.find(b'common.all' + bytes(1))
    b = i - 4760
    end, name, ac, st = parse_sndbank(dc, b, '>', body=4760)
    print('WU common_mp bank %s @0x%08x lists=%d %s end=0x%08x '
          'next-bytes=%s' % (name, b, ac, st, end, dc[end:end+16].hex()))


if __name__ == '__main__':
    main()
