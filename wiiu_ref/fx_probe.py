#!/usr/bin/env python3
"""
Task #28: console (Wii U) FxEffectDef / FxElemDef layout probe for T6.

FINDINGS:
  Console FxEffectDef body = 76 = PC-IDENTICAL (name FOLLOW @0 -> chars at +76).
  Console FxElemDef = 292 = PC-IDENTICAL at PC offsets (elemType@184,
  visualCount@185, vel/visIntervalCount@186/187, velSamples*@188, visSamples*@192,
  visuals union @196, effect refs @224/228/232/252, extended @256, spawnSound @280).
  Reference FX exist as ",name" markers preceded by... (NOTE: the 136-byte all-zero
  ",effect_*" bodies are reference TECHSETS, not FX — techset names use the same
  8-char hash suffix style.)

Dynamic order per elemDef (array rule: all 292-byte bodies first, then per-elem):
  1. velSamples: (velIntervalCount+1) x 96      [FxElemVelStateSample, PC size]
  2. visSamples: (visStateIntervalCount+1) x 48 [FxElemVisStateSample, PC size]
  3. visuals: DECAL -> markArray visualCount x 8 (material ptr pairs, asset refs);
     visualCount>1 -> array visualCount x 4 (FxElemVisuals ptr entries);
     single visual is inline in the body union @196.
     FxElemVisuals per elemType: SOUND -> soundName string; RUNNER ->
     FxEffectDefRef (assetref string); MODEL/materials -> asset refs (alias/FOLLOW
     -> inline asset! observed aliases only in genuine zones so far).
  4. effectOnImpact/effectOnDeath/effectEmitted (@224/228/232): FxEffectDefRef =
     {name*} assetref -> FOLLOW consumes the fx name string chars.
  5. emitDist/emitDistVariance are plain floats (no dyn).
  6. effectAttached @252: same as 4.
  7. extended @256: TRAIL -> FxTrailDef(28) + verts(20xn) + inds(2xn);
     SPOT_LIGHT -> FxSpotLightDef(12).
  8. spawnSound @280: {spawnSound*} -> FOLLOW consumes string chars.
"""
import struct
import re
import sys
from collections import Counter

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
NAME = set(b"abcdefghijklmnopqrstuvwxyz0123456789_/")
ED = 292

TYPE_TRAIL = 5
TYPE_MODEL = 7
TYPE_SPOT_LIGHT = 9
TYPE_SOUND = 10
TYPE_DECAL = 11
TYPE_RUNNER = 12

STATS = Counter()


class Fail(Exception):
    pass


def u32(d, o):
    return struct.unpack('>I', d[o:o+4])[0]


def i16(d, o):
    return struct.unpack('>h', d[o:o+2])[0]


class Cur:
    def __init__(self, d, o):
        self.d = d
        self.o = o

    def skip(self, n):
        self.o += n

    def cstr(self, maxlen=128):
        e = self.d.index(b'\x00', self.o)
        if e - self.o > maxlen:
            raise Fail('string too long')
        s = self.d[self.o:e]
        self.o = e + 1
        return s.decode('latin-1', 'replace')


def visual_dyn(d, c, vptr, etype):
    """One FxElemVisuals value: consume inline data if any."""
    if vptr not in PTRS:
        return
    if etype == TYPE_SOUND:
        c.cstr()
        STATS['soundName'] += 1
    elif etype == TYPE_RUNNER:
        c.cstr()                       # FxEffectDefRef assetref name
        STATS['runner ref'] += 1
    elif etype <= 6:                   # sprite/tail/line/trail/cloud -> material
        import xmodel_probe
        xmodel_probe.consume_material(d, c)
        STATS['inline material visual'] += 1
    elif etype == TYPE_MODEL:
        import xmodel_probe
        end, _ = xmodel_probe.parse_xmodel(d, c.o - 0)  # body follows inline
        # parse_xmodel expects body at given offset; inline asset = full XModel
        c.o = end
        STATS['inline model visual'] += 1
    else:
        raise Fail('inline visual asset (type %d)' % etype)


def parse_elem_dyn(d, eb, c):
    etype = d[eb+184]
    vcount = d[eb+185]
    vic = d[eb+186]
    vsc = d[eb+187]
    if u32(d, eb+188) in PTRS:
        c.skip((vic + 1) * 96)         # velSamples
    if u32(d, eb+192) in PTRS:
        c.skip((vsc + 1) * 48)         # visSamples
    vis = u32(d, eb+196)
    if etype == TYPE_DECAL:
        if vis in PTRS:
            mb = c.o
            c.skip(vcount * 8)         # FxElemMarkVisuals: 2 material ptrs
            for i in range(vcount):
                for k in (0, 4):
                    if u32(d, mb + i*8 + k) in PTRS:
                        import xmodel_probe
                        xmodel_probe.consume_material(d, c)
                        STATS['inline material in markArray'] += 1
    elif vcount > 1:
        if vis in PTRS:
            ab = c.o
            c.skip(vcount * 4)         # FxElemVisuals array
            for i in range(vcount):
                visual_dyn(d, c, u32(d, ab + i*4), etype)
    else:
        visual_dyn(d, c, vis, etype)
    for off in (224, 228, 232):        # onImpact/onDeath/emitted refs
        if u32(d, eb+off) in PTRS:
            c.cstr()
            STATS['fx ref str'] += 1
    if u32(d, eb+252) in PTRS:         # effectAttached
        c.cstr()
        STATS['fx ref str'] += 1
    ext = u32(d, eb+256)
    if ext in PTRS:
        if etype == TYPE_TRAIL:
            tb = c.o
            c.skip(28)                 # FxTrailDef
            # FxTrailDef: vertCount@12, verts*@16, indCount@20, inds*@24 —
            # same offsets as PC (proven exact on zm_transit fx_trail_rpg;
            # the old @8/@12/@16/@20 guess under-consumed trails by 390 B)
            vc_, ic_ = u32(d, tb+12), u32(d, tb+20)
            if u32(d, tb+16) in PTRS:
                c.skip(vc_ * 20)
            if u32(d, tb+24) in PTRS:
                c.skip(ic_ * 2)
            STATS['trail'] += 1
        elif etype == TYPE_SPOT_LIGHT:
            c.skip(12)
            STATS['spotlight'] += 1
        else:
            raise Fail('extended FOLLOW on type %d' % etype)
    if u32(d, eb+280) in PTRS:         # spawnSound
        c.cstr()
        STATS['spawnSound'] += 1


def parse_fx(d, b):
    """Full console FxEffectDef parse from body b. Returns (end, name)."""
    c = Cur(d, b + 76)
    name = c.cstr() if u32(d, b) in PTRS else '<alias>'
    n = i16(d, b+8) + i16(d, b+10) + i16(d, b+12)
    if u32(d, b+28) in PTRS:
        base = c.o
        c.skip(n * ED)
        for i in range(n):
            parse_elem_dyn(d, base + i*ED, c)
    return c.o, name


def find_fx(d):
    """Real FX bodies via fx-path-name ruler (name chars at +76)."""
    out = []
    for m in re.finditer(rb'[a-z][a-z0-9_]*(?:/[a-z0-9_]+)+\x00', d):
        p = m.start()
        if d[p-1] in NAME or d[p-1] == 0x2c:
            continue
        b = p - 76
        if b < 0 or u32(d, b) != FOLLOW:
            continue
        l, o_, e_ = i16(d, b+8), i16(d, b+10), i16(d, b+12)
        if not (0 <= l < 100 and 0 <= o_ < 100 and 0 <= e_ < 100 and l+o_+e_ > 0):
            continue
        out.append(b)
    return out


def main():
    for path in (sys.argv[1:] or ['mp_raid_genuine.zone', 'zm_transit_original.zone']):
        STATS.clear()
        d = open(path, 'rb').read()
        bodies = find_fx(d)
        ok = bad = chained = 0
        fails = Counter()
        bodyset = set(bodies)
        for b in bodies:
            try:
                end, name = parse_fx(d, b)
            except (Fail, ValueError, IndexError) as e:
                bad += 1
                fails[str(e)[:44]] += 1
                continue
            ok += 1
            # resync checks: next asset = another FX body, a ',name' ref,
            # or any FOLLOW/alias-headed body
            nx = u32(d, end)
            if end in bodyset:
                chained += 1
            elif d[end:end+1] == b',':
                chained += 1
            elif nx in PTRS or (0xa0000000 <= nx < 0xc0000000):
                pass  # plausible but unproven
            else:
                fails['end lands on garbage'] += 1
        print('%s: FX=%d ok=%d hard-chained=%d bad=%d' %
              (path, len(bodies), ok, chained, bad))
        print('  fails:', dict(fails.most_common(6)))
        print('  stats:', dict(STATS))


if __name__ == '__main__':
    main()
