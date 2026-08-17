#!/usr/bin/env python3
"""
DESTRUCTIBLEDEF console (Wii U v148) layout probe.

FINDING: console DestructibleDef is PC-IDENTICAL.
  DestructibleDef body (24 bytes):
    +0 name* (FOLLOW -> chars after body)  +4 XModel* model (alias)
    +8 XModel* pristineModel (alias)  +12 int numPieces
    +16 DestructiblePiece* pieces (FOLLOW)  +20 int clientOnly
  DestructiblePiece (312 bytes), offsets:
    +0   DestructibleStage stages[5] (48 each):
         { showBone u16(scriptstring)+pad, breakHealth f32@4, maxTime f32@8,
           flags u32@12, breakEffect FxEffectDef*@16 (asset ref),
           breakSound char*@20, breakNotify char*@24, loopSound char*@28,
           spawnModel XModel*[3]@32 (asset refs), physPreset PhysPreset*@44 }
    +240 parentPiece u8 (+3 pad), +244 6 x f32 damage scales,
    +268 physConstraints*, +272 health, +276 damageSound*, +280 burnEffect*,
    +284 burnSound*, +288 enableLabel u16 (scriptstring, +2 pad),
    +292 hideBones int[5] -> 312.
  Dynamic order per asset: name chars, then numPieces x 312-byte piece
  bodies, then per piece in order: per stage breakSound/breakNotify/
  loopSound strings (FOLLOW -> cstr), then damageSound/burnSound strings.
  Asset refs (model, effects, physPreset) are aliases in genuine zones
  (their assets load earlier), so they consume nothing.
"""
import struct, re, sys, os

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
NAME_RE = re.compile(rb'[a-z0-9_]{4,64}$')
PIECE = 312
STAGE = 48


def detect_endian(d):
    be = struct.unpack('>I', d[0:4])[0]
    le = struct.unpack('<I', d[0:4])[0]
    return '>' if abs(be - len(d)) < abs(le - len(d)) else '<'


class Fail(Exception):
    pass


def parse_destructible(d, b, e):
    def u32(o):
        return struct.unpack(e + 'I', d[o:o+4])[0]
    name_p, model, pristine, num, pieces_p, client = \
        struct.unpack(e + '6I', d[b:b+24])
    o = b + 24
    name = '<alias:%08x>' % name_p
    if name_p in PTRS:
        nul = d.index(b'\x00', o)
        name = d[o:nul].decode('latin-1')
        o = nul + 1
    strings = 0

    def cstr(o):
        nul = d.index(b'\x00', o)
        if nul - o > 96:
            raise Fail('string too long')
        return nul + 1

    def consume_physconstraints(o):
        # PhysConstraints (write order per OAT codegen): body 2696 (name@0, count@4, data[16]@8) ->
        # name string -> ALL 16 x PhysConstraint(168): target_bone1@+20, target_bone2@+36 strings.
        pc = o
        o += 2696
        if u32(pc + 0) in PTRS:
            o = cstr(o)
        for c in range(16):
            cb = pc + 8 + c * 168
            if u32(cb + 20) in PTRS:
                o = cstr(o)
            if u32(cb + 36) in PTRS:
                o = cstr(o)
        return o

    if pieces_p in PTRS:
        base = o
        o += num * PIECE
        for i in range(num):
            pb = base + i * PIECE
            # per-piece dynamic, in field/write order (codegen destructibledef_t6_write_db.cpp):
            # 5 stages' strings (breakSound/breakNotify/loopSound), then physConstraints@268,
            # damageSound@276, burnEffect@280, burnSound@284.
            for s in range(5):
                sb = pb + s * STAGE
                for ao in (sb+16, sb+32, sb+36, sb+40, sb+44):  # stage asset refs must be aliased
                    if u32(ao) in PTRS:
                        raise Fail('inline stage sub-asset at piece %d +0x%x' % (i, ao - pb))
                for so in (sb+20, sb+24, sb+28):                # breakSound/breakNotify/loopSound
                    if u32(so) in PTRS:
                        o = cstr(o); strings += 1
            if u32(pb + 268) in PTRS:                            # physConstraints (INLINE on PC)
                o = consume_physconstraints(o)
            if u32(pb + 276) in PTRS:                            # damageSound
                o = cstr(o); strings += 1
            if u32(pb + 280) in PTRS:                            # burnEffect (inline FxEffectDef)
                raise Fail('inline burnEffect at piece %d — FX sub-span not wired' % i)
            if u32(pb + 284) in PTRS:                            # burnSound
                o = cstr(o); strings += 1
    return o, name, num, strings


def find_destructibles(d, e):
    out = []
    pos = 0
    ff = b'\xff\xff\xff\xff'
    while True:
        pos = d.find(ff, pos)
        if pos < 0:
            break
        b = pos - 16                  # pieces* FOLLOW sits at +16
        pos += 1
        if b < 0 or b + 24 > len(d):
            continue
        v = struct.unpack(e + '6I', d[b:b+24])
        if v[4] != FOLLOW or not (0 < v[3] <= 32) or v[5] not in (0, 1):
            continue
        if v[0] < 0x80000000 or v[0] == FOLLOW:           # name is an alias
            continue
        if not (v[1] >= 0x80000000 and v[1] != FOLLOW):   # model alias
            continue
        if v[2] != 0 and (v[2] < 0x80000000 or v[2] == FOLLOW):
            continue                                      # pristine null/alias
        out.append(b)
    return out


def main():
    for zp in sys.argv[1:] or ['mp_raid_genuine.zone',
                               'zm_transit_original.zone',
                               '../PC ff/mp_raid.zone']:
        d = open(zp, 'rb').read()
        e = detect_endian(d)
        found = find_destructibles(d, e)
        print('%s [%s]: destructible candidates=%d' %
              (os.path.basename(zp), 'BE' if e == '>' else 'LE', len(found)))
        for b in found:
            try:
                end, name, num, ns = parse_destructible(d, b, e)
            except (Fail, ValueError) as ex:
                print('    0x%08x FAIL %s' % (b, ex))
                continue
            nxt = struct.unpack(e + 'I', d[end:end+4])[0]
            ok = nxt == FOLLOW or nxt >= 0x80000000
            print('    0x%08x %-36s pieces=%-2d strings=%-3d end=0x%08x '
                  'next=%08x %s' % (b, name, num, ns, end, nxt,
                                    'RESYNC' if ok else 'BAD'))


if __name__ == '__main__':
    main()
