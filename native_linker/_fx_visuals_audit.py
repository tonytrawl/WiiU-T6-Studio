"""Structured FX material-handle audit: walk FxEffectDef -> FxElemDef on the
CONSOLE side and compare each elem's `visuals` union ours-vs-key.

Motivated by the dump-7 crash in FX_GenSpriteVerts -> R_AddCodeMeshDrawSurf+0x28,
which dereferences a Material*. Word-scanning the FX bodies proved useless three
different ways (see skate-fx-spritevert-material-crash), so this walks the real
layout instead:

  FxEffectDef  76 B : elemDefCount{Looping,OneShot,Emission} i16 @8/@10/@12,
                      elemDefs ptr @28
  FxElemDef   292 B : elemType u8 @184, visualCount u8 @185, visuals union @196

  python _fx_visuals_audit.py [ours.zone] [key.zone]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS

OURS = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_final.zone'
KEY = sys.argv[2] if len(sys.argv) > 2 else 'mp_skate_gfxtail46.zone'
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
ED, FX_HDR = 292, 76
be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
be16s = lambda d, o: struct.unpack_from('>h', d, o)[0]
TYPE_SOUND, TYPE_RUNNER, TYPE_DECAL = 8, 9, 7


def cls(v):
    if v in PTRS:
        return 'FOLLOW/INSERT'
    if v == 0:
        return 'NULL'
    if 0xA0000000 <= v < 0xC0000000:
        return 'b5-alias'
    if 0x80000000 <= v < 0xA0000000:
        return 'PC-block'
    return 'OTHER(0x%08X)' % v


def walk(path):
    Z = open(path, 'rb').read()
    _, spans, _ = LS.simulate(path, verbose=False)
    fx = [(i, s, e) for (i, n, r, s, e) in spans if r == 'FxEffectDef']
    out = []
    for (idx, s, e) in fx:
        n = be16s(Z, s + 8) + be16s(Z, s + 10) + be16s(Z, s + 12)
        if not (0 < n < 4096):
            out.append((idx, None, 'bad elem count %d' % n))
            continue
        c = s + FX_HDR
        if be32(Z, s) in PTRS:                       # inline name string
            z = Z.find(b'\x00', c)
            c = z + 1 if z >= 0 else c
        if be32(Z, s + 28) not in PTRS:
            out.append((idx, None, 'elemDefs not inline'))
            continue
        elems = []
        for i in range(n):
            eb = c + i * ED
            if eb + ED > len(Z):
                break
            elems.append((eb, Z[eb + 184], Z[eb + 185], be32(Z, eb + 196)))
        out.append((idx, elems, None))
    return Z, out


Za, fa = walk(OURS)
Zb, fb = walk(KEY)
print('FX assets: ours=%d key=%d' % (len(fa), len(fb)))

bad = [(i, m) for (i, el, m) in fa if m]
if bad:
    print('!! %d FX assets could not be walked in ours: %s' % (len(bad), bad[:4]))

tot = mism = 0
byclass = {}
examples = []
for (ia, ea, ma), (ib, eb_, mb) in zip(fa, fb):
    if ma or mb or ea is None or eb_ is None:
        continue
    if len(ea) != len(eb_):
        print('   asset %d: elem count differs %d vs %d' % (ia, len(ea), len(eb_)))
        continue
    for (oa, ta, va, ha), (ob, tb, vb, hb) in zip(ea, eb_):
        tot += 1
        if ha == hb and ta == tb and va == vb:
            continue
        mism += 1
        k = (ta, cls(ha), cls(hb))
        byclass[k] = byclass.get(k, 0) + 1
        if len(examples) < 12:
            examples.append((ia, oa, ta, tb, va, vb, ha, hb))

print('\nelems compared: %d   MISMATCHED: %d' % (tot, mism))
if byclass:
    print('\n(elemType, ours-visuals-class, key-visuals-class) -> count')
    for k, v in sorted(byclass.items(), key=lambda x: -x[1]):
        print('   type=%-3s %-16s -> %-16s  x%d' % (k[0], k[1], k[2], v))
    print('\nexamples:')
    for (ia, oa, ta, tb, va, vb, ha, hb) in examples:
        print('   fx#%-3d elem@%-9d type %s/%s vcount %s/%s  ours=0x%08X key=0x%08X'
              % (ia, oa, ta, tb, va, vb, ha, hb))
else:
    print('\nEvery FX elem visuals handle MATCHES the key '
          '(type, visualCount and union word).')
