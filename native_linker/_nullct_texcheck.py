#!/usr/bin/env python3
"""
_nullct_texcheck.py — discriminate the repoint target for the 4 lit_sm decals by TEXTURE COUNT.

The techset name grammar encodes the map set per layer:
    lit_sm_r0c0n0x0_b1c1n1s1    -> layer0 {r,c,n,x} + layer1 {b,c,n,s} = 8 maps
    lit_sm_r0c0n0x0_b1c1n1s1v1  -> ... + v1                            = 9 maps
If our 4 materials carry 8 textures, they are `...b1c1n1s1` materials and the substitution
appended a bogus `v1` layer (whose alphaRevealP they cannot feed). If they carry 9, the story
is different and repointing would be wrong.

Control: measure texc of the materials GENUINELY bound to each techset (skate + raid), so the
name->texc rule is validated rather than assumed.
"""
import sys
from collections import defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
from _nullct_oracle import scan

RAID = '../wiiu_ref/mp_raid_genuine.zone'
SKATE = 'mp_skate_gfxtail19.zone'
TEN_TS = (678, 699)


def name_maps(nm):
    """count map letters in a techset name: tokens after the family prefix, digits mark layers."""
    core = nm.rsplit('_', 1)[0] if nm.count('_') >= 1 else nm
    n = 0
    for tok in core.split('_'):
        if tok and tok[0] in 'rcnxbstamv' and any(ch.isdigit() for ch in tok):
            n += sum(1 for ch in tok if ch.isalpha())
    return n


def survey(path, tag):
    Z, mats, dem, spans, tsname, tsidx = scan(path)
    bound = defaultdict(list)
    for m in mats:
        k = tsidx(m['ts'])
        if k is not None:
            bound[k].append(m)
    print('=' * 78); print(tag); print('=' * 78)
    rows = []
    for k, (s, e) in sorted(spans.items()):
        nm = tsname(s) or ''
        if not nm.startswith('lit_sm_r0c0n0x0_b1c1n1s1'):
            continue
        lst = bound.get(k, [])
        if not lst:
            continue
        texs = sorted({m['texc'] for m in lst})
        rows.append((k, nm, name_maps(nm), texs, len(lst)))
    for k, nm, nmaps, texs, cnt in rows:
        print('  ts %-4d %-44s name_maps=%-2d  mats=%-3d texc seen=%s'
              % (k, nm, nmaps, cnt, texs))
    return Z, mats, dem, spans, tsname, tsidx, bound


print('name->maps rule check (does texc track the name grammar?)\n')
survey(RAID, 'RAID (genuine control)')
print()
Z, mats, dem, spans, tsname, tsidx, bound = survey(SKATE, 'SKATE')

print('\n%s\nTHE 10: texc vs candidate targets\n%s' % ('=' * 78, '=' * 78))
for k in TEN_TS:
    nm = tsname(spans[k][0])
    print('\n  currently ts %d %s  (name_maps=%d, demands %s)'
          % (k, nm, name_maps(nm), ['0x%08x' % h for h in sorted(dem[k])]))
    for m in bound[k]:
        print('     texc=%-3d constc=%-3d %s' % (m['texc'], m['constc'], m['name'][:56]))

print('\n%s\nZERO-DEMAND candidates and their texc footprint\n%s' % ('=' * 78, '=' * 78))
for k, (s, e) in sorted(spans.items()):
    nm = tsname(s) or ''
    if nm in ('lit_sm_r0c0n0x0_b1c1n1s1', 'wpc_unlitdecalblend_multiply_35079164',
              'wpc_unlit_multiply_20236462'):
        lst = bound.get(k, [])
        print('  ts %-4d %-44s demands=%-8s name_maps=%-2d mats=%-3d texc=%s'
              % (k, nm, 'NOTHING' if not dem[k] else 'SOME', name_maps(nm), len(lst),
                 sorted({m['texc'] for m in lst})))
