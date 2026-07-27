#!/usr/bin/env python3
"""PHASE 1 step 2/3/4: for each of the 13 G4-unsatisfied materials, dump
   CARRIED texdef names + constant names   vs   techset DEMANDED names,
with every hash resolved to its real string via the CONFIRMED hash fn.
"""
import sys, struct, re
from collections import OrderedDict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
from _matconst_map import be32, be16, walk_material, walk_techset, PTRS, FOLLOW
import shader_probe as SP
import xmodel_probe as XP
from _p1_recover import H

CONST_TYPES = (0, 6)
SAMPLER_TYPE = 2

# ---- name vocabulary: bases x index suffixes, plus harvested constdef names ----
BASES = ['colorMap', 'normalMap', 'specularMap', 'detailMap', 'occlusionMap',
         'glossMap', 'specColorMap', 'blendMap', 'revealMap', 'alphaMap',
         'lightmapPrimary', 'lightmapSecondary', 'attenMap', 'cinematicY',
         'cinematicU', 'cinematicV', 'cinematicA', 'reflectionProbe',
         'modelLighting', 'shadowmapSamplerSun', 'shadowmapSamplerSpot',
         'floatZ', 'outdoorMap', 'dustMap', 'noiseMap', 'lookupMap',
         'envMap', 'cubeMap', 'grainMap', 'flowMap', 'rippleMap',
         'colorTint', 'scaleRGB', 'detailScale', 'alphaRevealParms',
         'uvAnimParms', 'featherParms', 'envMapParms', 'eyeOffsetParms',
         'falloffParms', 'distortionScale', 'flagParams', 'hdrAmount',
         'controlVar', 'waterNormalMap', 'heightMap', 'thicknessMap',
         'lightmapSamplerPrimary', 'lightmapSamplerSecondary']
SUFF = [''] + [str(i) for i in range(0, 10)] + ['%02d' % i for i in range(0, 8)]
VOCAB = {}
for b in BASES:
    for s in SUFF:
        VOCAB.setdefault(H(b + s), b + s)


def nm(h):
    return VOCAB.get(h, '?')


def fmt(hs):
    return ' '.join('%s[0x%08x]' % (nm(h), h) for h in sorted(hs))


def material_at_name(Z, name_off):
    """material body offset given the inline name offset (name FOLLOWs at body+104)."""
    b = name_off - 104
    if be32(Z, b) in PTRS:
        return b
    return None


def carried(Z, off):
    """(texdef hashes, const hashes) carried by the material body at off."""
    b = off
    texc, constc = Z[b + 72], Z[b + 73]
    tsp, ttp, ctp = be32(Z, b + 80), be32(Z, b + 84), be32(Z, b + 88)
    c = XP.Cur(Z, b + 104)
    if be32(Z, b) in PTRS:
        c.cstr()
    if tsp in PTRS:
        c.o, _ = SP.parse_techset(Z, c.o)
    tex, kind = [], 'inline'
    if ttp in PTRS:
        defs = c.o
        tex = [be32(Z, defs + i * 16) for i in range(texc)]
        texchars = [(chr(Z[defs + i * 16 + 4]), chr(Z[defs + i * 16 + 5])) for i in range(texc)]
        c.skip(texc * 16)
        for i in range(texc):
            if be32(Z, defs + i * 16 + 12) in PTRS:
                XP.consume_image(Z, c)
    else:
        kind = 'ALIAS'
        texchars = []
    consts = []
    if ctp in PTRS:
        consts = [be32(Z, c.o + i * 32) for i in range(constc)]
    return tex, texchars, consts, kind, texc, constc
