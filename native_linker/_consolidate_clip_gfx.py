#!/usr/bin/env python3
"""Diff GENUINE pointer fields of clipMap_t + GfxWorld between the pipeline
build (mp_skate_pipecheck) and the playable answer key (mp_skate_gfxtail46).

Both zones are the SAME map at the SAME layout (101,404,242 B) -> same offset =
same field.  Region bases come from the authoritative emit region_pairs (GfxWorld)
and the validated console clipmap_probe walk (clipMap).  We compare ONLY known
genuine pointer-field words and classify crash-causing mismatches."""
import struct
from collections import Counter, defaultdict

PIPE = 'mp_skate_pipecheck.zone'
KEY  = 'mp_skate_gfxtail46.zone'
P = open(PIPE, 'rb').read()
K = open(KEY, 'rb').read()

GFX = 61250249            # GfxWorld body (span 61250249..84138442)
CLIP = 84512493          # clipMap_t body (span 84512493..89584099)

# emit region_pairs co_base (relative to GfxWorld body) — authoritative
CO = {'models': 16808172, 'materialMemory': 16819628,
      'cells': 192200,
      'dpvs.surfaces': 17618874, 'dpvs.smodelDrawInsts': 18189354}

# clipMap sub-array ENDs (from console clipmap_probe walk, identical both zones)
SM_END = 0x52a6681       # staticModelList end; 1901 * 84
CM_END = 0x5534fb7       # cmodels end; 179 * 76
ND_END = 0x52ae7e9       # nodes end; 4141 * 8


def u32(d, o):
    return struct.unpack_from('>I', d, o)[0]


def cls(v):
    if v == 0xFFFFFFFF:
        return 'FOLLOW'
    if v == 0xFFFFFFFE:
        return 'INSERT'
    if v == 0:
        return 'null'
    if 0xBF000000 <= v <= 0xBF00FFFF:
        return 'poison'          # 0xBF00_00xx assembler unresolved marker
    if 0xA0000000 <= v < 0xC0000000:
        return 'alias'           # in-zone b5 alias
    if 0xE0000000 <= v <= 0xFFFFFFFD:
        return 'block7'          # common/external handle
    return 'other'               # wild / float-looking / small int


GOOD = {'alias', 'FOLLOW', 'INSERT', 'null'}   # non-crash pointer classes
CRASH = {'block7', 'poison', 'other'}          # potentially crash-causing


def diff_field(name, base, count, stride, poff, results):
    for i in range(count):
        o = base + i * stride + poff
        pv, kv = u32(P, o), u32(K, o)
        if pv == kv:
            continue
        pc, kc = cls(pv), cls(kv)
        results.append((name, i, o, pv, kv, pc, kc))


R = []
# --- clipMap_t ---
diff_field('clip.staticModelList.xmodel@0', SM_END - 1901 * 84, 1901, 84, 0, R)
# cmodels: cmodel_t 76B. Probe/PC layout: no external asset ptr (bounds+brush idx).
# Include a scan for any pointer-class word that flips GOOD->CRASH across the row.
CM_BASE = CM_END - 179 * 76
ND_BASE = ND_END - 4141 * 8
diff_field('clip.nodes.plane@0', ND_BASE, 4141, 8, 0, R)

# --- GfxWorld ---
diff_field('gfx.dpvs.surfaces.material@48', GFX + CO['dpvs.surfaces'], 7131, 80, 48, R)
diff_field('gfx.dpvs.smodelDrawInsts.model@32', GFX + CO['dpvs.smodelDrawInsts'], 3641, 208, 32, R)
# smodelDrawInsts lmapVertexInfo[4] ptr @80 + e*32
for e in range(4):
    diff_field('gfx.dpvs.smodelDrawInsts.lmap%d@%d' % (e, 80 + e * 32),
               GFX + CO['dpvs.smodelDrawInsts'], 3641, 208, 80 + e * 32, R)
diff_field('gfx.materialMemory.material@0', GFX + CO['materialMemory'], 733, 8, 0, R)

# --- report ---
print('Total pointer-field diffs (any):', len(R))
byfield = Counter(r[0] for r in R)
print('\nDiffs per field:')
for k, v in byfield.most_common():
    print('  %-40s %d' % (k, v))

print('\nClass-transition matrix (pipeline_class -> answerkey_class : count):')
trans = Counter((r[5], r[6]) for r in R)
for (pc, kc), n in trans.most_common():
    flag = '  <== CRASH' if (pc in CRASH and kc in GOOD) else ''
    print('  %-8s -> %-8s : %5d%s' % (pc, kc, n, flag))

print('\n=== CRASH-CAUSING mismatches (pipeline crash-class, answerkey good) ===')
crashes = [r for r in R if r[5] in CRASH and r[6] in GOOD]
print('count:', len(crashes))
cf = Counter((r[0], r[5], r[6]) for r in crashes)
for (fld, pc, kc), n in cf.most_common():
    print('  %-40s %s->%s  x%d' % (fld, pc, kc, n))
print('\nExamples (field idx off pipe->key):')
for r in crashes[:40]:
    print('  %-40s i=%-6d off=%d  0x%08x(%s) -> 0x%08x(%s)'
          % (r[0], r[1], r[2], r[3], r[5], r[4], r[6]))

# also: cases where BOTH are pointer-class but differ (alias->alias etc = benign
# reloc differences), summarize
print('\nNon-crash pointer diffs (both good class):',
      len([r for r in R if r[5] in GOOD and r[6] in GOOD]))
