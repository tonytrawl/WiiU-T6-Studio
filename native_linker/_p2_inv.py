#!/usr/bin/env python3
"""PHASE 2 shared inventory: for any console zone, walk EVERY material and the
techset it is bound to, and report carried-vs-demanded by hash.

Reusable by the rule-derivation and the genuine-console control.
"""
import sys, os, re, struct
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'WiiU_FF_Studio'))

import loader_sim as LS
import wiiu_zone
import shader_probe as SP
import xmodel_probe as XP
from _matconst_map import be32, be16, walk_material, walk_techset, PTRS, FOLLOW

AL = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or AL(v)
CONST_TYPES = (0, 6)
SAMPLER_TYPE = 2
_cstr = lambda d, o, n=220: (lambda e: d[o:e].decode('latin1', 'replace') if e >= 0 else None)(
    d.find(b'\x00', o, o + n))


def ts_demands(Z, s):
    """(const hash set, sampler hash set) demanded by the techset body at s."""
    passes, _ = walk_techset(Z, s)
    c, sm = set(), set()
    for p in passes:
        for j in range(p['nargs']):
            a = p['args_off'] + j * 8
            t, v = be16(Z, a), be32(Z, a + 4)
            if v in PTRS:
                continue
            if t in CONST_TYPES:
                c.add(v)
            elif t == SAMPLER_TYPE:
                sm.add(v)
    return c, sm


def carried(Z, b):
    """(tex hashes, const hashes, texc, constc, kind) carried by material body at b."""
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
        c.skip(texc * 16)
        for i in range(texc):
            if be32(Z, defs + i * 16 + 12) in PTRS:
                XP.consume_image(Z, c)
    else:
        kind = 'ALIAS'
    consts = []
    if ctp in PTRS:
        consts = [be32(Z, c.o + i * 32) for i in range(constc)]
    return tex, consts, texc, constc, kind


def build(path, verbose=True):
    Z = open(path, 'rb').read()
    rc = wiiu_zone.ZoneReader(Z); rc.read_string_table(); rc.read_asset_list()
    our_arr = ((rc.assets_off - 64) + 7) & ~7
    NC = len(rc.assets)

    def dec_k(a):
        p = (a - 1) & 0x1FFFFFFF
        lo = our_arr + 4
        return (p - lo) // 8 if (lo <= p < lo + NC * 8 and (p - lo) % 8 == 0) else None

    def enc_k(k):
        return 0xA0000000 + (our_arr + 4 + k * 8) + 1

    em, spans, _ = LS.simulate(path, verbose=False)

    TS = {}   # k -> dict(name, span, dc, ds)
    name2k = defaultdict(list)
    for (i, en, root, s, e) in spans:
        if root != 'MaterialTechniqueSet':
            continue
        nm = (_cstr(Z, s + 136) or '') if be32(Z, s) == FOLLOW else ''
        nm = nm.lstrip(',')
        try:
            dc, ds = ts_demands(Z, s)
        except Exception:
            dc, ds = None, None
        TS[i] = dict(name=nm, span=s, dc=dc, ds=ds)
        if nm:
            name2k[nm].append(i)

    MATS = []
    last = -1
    for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), Z):
        o = m.start()
        if o < last or o + 104 > len(Z) or be32(Z, o) != FOLLOW:
            continue
        texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
        if not (texc <= 64 and constc <= 64 and sbc <= 64
                and all(ptrish(be32(Z, o + x)) for x in (80, 84, 88, 92, 96))):
            continue
        try:
            info, nxt = walk_material(Z, o)
        except Exception:
            continue
        nm = info['name']
        if not nm or not (1 <= len(nm) <= 200) or not all(
                32 <= c < 127 for c in nm.encode('latin1', 'replace')):
            continue
        last = o + 104
        ts = be32(Z, o + 80)
        k = dec_k(ts) if (ts not in PTRS and AL(ts)) else None
        try:
            tex, consts, tc2, cc2, kind = carried(Z, o)
        except Exception:
            continue
        MATS.append(dict(off=o, name=nm, tex=set(tex), consts=set(consts),
                         texc=texc, constc=constc, kind=kind, ts_handle=ts, k=k))

    if verbose:
        print('%s: %d materials, %d techsets (%d distinct names)'
              % (os.path.basename(path), len(MATS), len(TS), len(name2k)))
    return dict(Z=Z, MATS=MATS, TS=TS, name2k=name2k, dec_k=dec_k, enc_k=enc_k, NC=NC)


def unsat(M, ts):
    """(missing const hashes, missing sampler hashes) for material M under techset ts."""
    if ts is None or ts['dc'] is None:
        return None, None
    return ts['dc'] - M['consts'], ts['ds'] - M['tex']


if __name__ == '__main__':
    for p in sys.argv[1:]:
        inv = build(p)
        bad = 0
        for M in inv['MATS']:
            ts = inv['TS'].get(M['k'])
            mc, ms = unsat(M, ts)
            if mc is None:
                continue
            if mc or ms:
                bad += 1
        print('   materials with unsatisfied demand: %d / %d' % (bad, len(inv['MATS'])))
