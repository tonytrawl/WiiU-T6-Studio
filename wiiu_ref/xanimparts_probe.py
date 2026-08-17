#!/usr/bin/env python3
"""
XANIMPARTS console (Wii U v148) layout probe.

FINDING: console XAnimParts is PC-IDENTICAL in layout (same 104-byte body,
same stream order, same delta encodings); only the endianness of u16/u32/f32
fields differs. Verified by hard-chaining consecutive XANIMPARTS runs.

Body (104 bytes):
  +0 name* +4..15 u16 dataByteCount,dataShortCount,dataIntCount,
  randomDataByteCount,randomDataIntCount,numframes
  +16 bLoop,bDelta,bDelta3D,bLeftHandGripIK (u8)
  +20 streamedFileSize +24 boneCount[10] +34 notifyCount +35 assetType
  +36 isDefault (+3 pad) +40 randomDataShortCount +44 indexCount
  +48 framerate +52 frequency +56 primedLength +60 loopEntryTime
  +64 names* +68 dataByte* +72 dataShort* +76 dataInt* +80 randomDataShort*
  +84 randomDataByte* +88 randomDataInt* +92 indices +96 notify*
  +100 deltaPart* -> 104
Stream order (ZoneCode reorder): name, names (boneCount[9] x u16
scriptstrings), notify (notifyCount x {u16 name, pad2, f32 time} = 8),
deltaPart (if bDelta), dataByte, dataShort (2B, 2-aligned relative to
nothing: tightly packed), dataInt, randomDataShort, randomDataByte,
randomDataInt, indices (indexCount x (u8 if numframes<256 else u16)).
XAnimDeltaPart = {trans*, quat2*, quat*} (12B), each FOLLOW:
  trans: u16 size, u8 smallTrans; size==0 -> vec3 frame0 (16 total);
    else XAnimPartTransFrames: mins vec3, size vec3, frames*(4),
    inline indices (size+1) x idxw, then if frames FOLLOW:
    (size+1) x (ByteVec 3B if smallTrans else UShortVec 6B/align4).
  quat2: u16 size; size==0 -> XQuat2 frame0 (2x i16) inline (8 total);
    else {frames*(4) @4, inline indices (size+1) x idxw}, frames FOLLOW ->
    (size+1) x XQuat2(4B).
  quat: u16 size; size==0 -> XQuat frame0 (4x i16) (12 total);
    else same with XQuat(8B).
  (alignment inside delta records observed on genuine data; see code)
"""
import struct, re, sys, os
from collections import Counter

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def detect_endian(d):
    be = struct.unpack('>I', d[0:4])[0]
    le = struct.unpack('<I', d[0:4])[0]
    return '>' if abs(be - len(d)) < abs(le - len(d)) else '<'


class Fail(Exception):
    pass


def _align(o, n):
    return (o + n - 1) & ~(n - 1)


def parse_xanim(d, b, e):
    def u32(o): return struct.unpack(e + 'I', d[o:o+4])[0]
    def u16(o): return struct.unpack(e + 'H', d[o:o+2])[0]
    (dataByteCount, dataShortCount, dataIntCount, randomDataByteCount,
     randomDataIntCount, numframes) = struct.unpack(e + '6H', d[b+4:b+16])
    boneCount = d[b+24:b+34]
    notifyCount = d[b+34]
    randomDataShortCount = u32(b+40)
    indexCount = u32(b+44)
    idxw = 1 if numframes < 256 else 2
    o = b + 104
    name = None
    if u32(b) == FOLLOW:
        nul = d.index(b'\x00', o)
        name = d[o:nul].decode('latin-1')
        o = nul + 1
    if u32(b+64) in PTRS:                       # names
        o += boneCount[9] * 2
    if u32(b+96) in PTRS:                       # notify
        o += notifyCount * 8
    if u32(b+100) in PTRS:                      # deltaPart
        o = parse_delta(d, o, e, numframes, idxw)
    if u32(b+68) in PTRS:
        o += dataByteCount
    if u32(b+72) in PTRS:
        o += dataShortCount * 2
    if u32(b+76) in PTRS:
        o += dataIntCount * 4
    if u32(b+80) in PTRS:
        o += randomDataShortCount * 2
    if u32(b+84) in PTRS:
        o += randomDataByteCount
    if u32(b+88) in PTRS:
        o += randomDataIntCount * 4
    if u32(b+92) in PTRS:                       # indices
        o += indexCount * idxw
    return o, name


def parse_delta(d, o, e, numframes, idxw):
    def u32(x): return struct.unpack(e + 'I', d[x:x+4])[0]
    def u16(x): return struct.unpack(e + 'H', d[x:x+2])[0]
    db = o
    trans_p, quat2_p, quat_p = struct.unpack(e + '3I', d[o:o+12])
    o += 12
    if trans_p in PTRS:
        size = u16(o)
        small = d[o+2]
        if size == 0:
            o += 16                              # u16+u8+pad + vec3 frame0
        else:
            frames_p = u32(o + 28)               # 4 + mins12 + size12
            o += 32                              # header + frames ptr
            o += (size + 1) * idxw               # inline indices
            if frames_p in PTRS:
                if small:
                    o += (size + 1) * 3          # ByteVec
                else:
                    o += (size + 1) * 6          # UShortVec (tightly packed, no pad)
    if quat2_p in PTRS:
        size = u16(o)
        if size == 0:
            o += 8                               # u16+pad + XQuat2
        else:
            frames_p = u32(o + 4)
            o += 8
            o += (size + 1) * idxw
            if frames_p in PTRS:
                o += (size + 1) * 4              # XQuat2 (tightly packed, no pad)
    if quat_p in PTRS:
        size = u16(o)
        if size == 0:
            o += 12                              # u16+pad + XQuat(8)
        else:
            frames_p = u32(o + 4)
            o += 8
            o += (size + 1) * idxw
            if frames_p in PTRS:
                o += (size + 1) * 8              # XQuat (tightly packed, no pad)
    return o


def find_xanims(d, e):
    """Anchor: FOLLOW name ptr + plausible framerate float + name chars."""
    out = []
    pos = 0
    ff = b'\xff\xff\xff\xff'
    NAME_RE = re.compile(rb'[\w\-]{4,96}$')
    while True:
        pos = d.find(ff, pos)
        if pos < 0:
            break
        b = pos
        pos += 1
        fr = struct.unpack(e + 'f', d[b+48:b+52])[0] if b+52 <= len(d) else 0
        if not (1.0 <= fr <= 120.0):
            continue
        numframes = struct.unpack(e + 'H', d[b+14:b+16])[0]
        if numframes == 0 or numframes > 20000:
            continue
        nul = d.find(b'\x00', b + 104, b + 104 + 100)
        if nul <= b + 104 or not NAME_RE.fullmatch(d[b+104:nul]):
            continue
        out.append(b)
    return out


def main():
    for zp in sys.argv[1:] or ['zm_transit_original.zone',
                               'mp_raid_genuine.zone',
                               '../PC ff/zm_nuked.zone']:
        d = open(zp, 'rb').read()
        e = detect_endian(d)
        bodies = find_xanims(d, e)
        starts = set(bodies)
        ok = chained = bad = 0
        fails = Counter()
        for b in bodies:
            try:
                end, name = parse_xanim(d, b, e)
            except (Fail, ValueError, IndexError, struct.error) as ex:
                bad += 1
                fails[str(ex)[:40]] += 1
                continue
            ok += 1
            if end in starts:
                chained += 1
        print('%s [%s]: xanim=%d parsed=%d hard-chained=%d bad=%d' %
              (os.path.basename(zp), 'BE' if e == '>' else 'LE',
               len(bodies), ok, chained, bad))
        if fails:
            print('   fails:', dict(fails.most_common(4)))
        # show a few chains for eyeballing
        for b in bodies[:4]:
            try:
                end, name = parse_xanim(d, b, e)
                print('    0x%08x %-52s end=0x%08x %s' %
                      (b, name, end, 'CHAIN' if end in starts else ''))
            except Exception:
                pass


if __name__ == '__main__':
    main()
