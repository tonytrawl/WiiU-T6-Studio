#!/usr/bin/env python3
"""
_nullct_oracle.py — HANDOFF §4 upstream question, asked against the RAID ORACLE.

Boot 23 dies because 10 skate materials have constantTable == NULL (constc == 0) while their
(substituted) techset still demands a type-6 constant-by-nameHash. Before papering over it
(alias the ct, §4.1), ask the upstream question:

    Do GENUINE console decal materials carry non-zero constc?

If genuine materials never have constc==0-bound-to-a-demanding-techset, then the invariant is
"a material always carries every constant its techset demands", and our 10 are an upstream
converter/substitution bug (a §4.4-shaped defect), not a thing to alias around.

Runs the SAME name-anchored scan as _find_nullct.py (anchor on name*==FOLLOW, allow constc==0,
BYTE granularity) on any zone. Raid is the control: it never uses MeasuredRuntimeMap and never
uses the techset-substitution path.

Usage:  python _nullct_oracle.py <zone> [<zone> ...]
"""
import re
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_zone
from _matconst_map import (CONSTDEF, FOLLOW, PTRS, be32, walk_material,
                           techset_const_hashes)

isalias = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or isalias(v)


def scan(path):
    Z = open(path, 'rb').read()
    rc = wiiu_zone.ZoneReader(Z)
    rc.read_string_table()
    rc.read_asset_list()
    em, spans, CO = LS.simulate(path, verbose=False)
    ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans
                if root == 'MaterialTechniqueSet' and e > s}
    demand = {}
    for i, (s, e) in ts_spans.items():
        try:
            demand[i] = techset_const_hashes(Z, s)[0]
        except Exception:
            demand[i] = set()

    def ts_name(s):
        if be32(Z, s) != FOLLOW:
            return None
        e = Z.find(b'\x00', s + 136)
        return bytes(Z[s + 136:e]).decode('latin1', 'replace')

    arr = rc.assets_off - 64
    our_arr = (arr + 7) & ~7

    def ts_idx(a):
        v = (a - 1) & 0x1FFFFFFF
        if (v - our_arr - 4) % 8:
            return None
        k = (v - our_arr - 4) // 8
        return k if 0 <= k < len(rc.assets) else None

    mats = []
    dropped = []
    last = -1
    for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), Z):
        o = m.start()
        if o < last or o + 104 > len(Z):
            continue
        if be32(Z, o + 0) != FOLLOW:
            continue
        texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
        ct = be32(Z, o + 88)
        if not (texc <= 64 and constc <= 64 and sbc <= 64 and ptrish(ct)
                and all(ptrish(be32(Z, o + x)) for x in (80, 84, 92, 96))):
            continue
        if not isalias(be32(Z, o + 80)):
            continue
        try:
            # walk_material (NOT parse_material) -- consumes inline images + inline techsets.
            # parse_material's short ct_off produced garbage consts, and the name-sanity check
            # below then DROPPED the material silently. That is exactly how boot 24's
            # wpc/skt_video_screen_lrg escaped every audit.
            info, nxt = walk_material(Z, o)
        except Exception:
            continue
        nm = info['name']
        if not nm or not (1 <= len(nm) <= 200) or not all(
                32 <= c < 127 for c in nm.encode('latin1', 'replace')):
            continue
        if constc and info['ct_off'] is not None:
            names = [Z[info['ct_off'] + k * CONSTDEF + 4:info['ct_off'] + k * CONSTDEF + 16]
                     for k in range(constc)]
            if not all(n[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in n)
                       for n in names):
                # NEVER drop silently again -- a dropped material is an unaudited material.
                dropped.append((o, nm, constc))
                continue
        info['_off'] = o
        info['_ct'] = ct
        mats.append(info)
        last = o + 104
    if dropped:
        print('  !! %d material(s) DROPPED (constant names unreadable => walker still short):'
              % len(dropped))
        for o, nm, cc in dropped[:8]:
            print('       @%-9d constc=%-3d %s' % (o, cc, nm[:56]))
    return Z, mats, demand, ts_spans, ts_name, ts_idx


def report(path):
    print('=' * 78)
    print(path)
    print('=' * 78)
    Z, mats, demand, ts_spans, ts_name, ts_idx = scan(path)
    zero = [m for m in mats if m['constc'] == 0]
    print('techsets parsed        : %d' % len(demand))
    print('materials (name-anchor): %d' % len(mats))
    print('  ZERO-constant (ct*=0): %d' % len(zero))

    # the invariant under test: does every material carry what its techset demands?
    unsat_zero, unsat_nonzero = [], []
    bound = 0
    for mm in mats:
        k = ts_idx(mm['ts'])
        if k is None or k not in demand or not demand[k]:
            continue
        bound += 1
        miss = demand[k] - set(mm['consts'])
        if miss:
            (unsat_zero if mm['constc'] == 0 else unsat_nonzero).append((mm, k, miss))

    print('materials bound to a DEMANDING techset: %d' % bound)
    print('  UNSATISFIED, constc == 0 (NULL table -> lwz r29,0(r28) AV): %d' % len(unsat_zero))
    print('  UNSATISFIED, constc >  0 (unbounded walk -> 0x50000010 AV): %d' % len(unsat_nonzero))

    # decal-specific view: is "decal" a class that genuinely ships without constants?
    decals = [m for m in mats if 'decal' in m['name'].lower()]
    dz = [m for m in decals if m['constc'] == 0]
    print("\nmaterials with 'decal' in the name: %d   of which constc == 0: %d"
          % (len(decals), len(dz)))
    if decals:
        cc = sorted(set(m['constc'] for m in decals))
        print('  decal constc values observed: %s' % cc)
        for m in decals[:8]:
            print('    constc=%-3d %r' % (m['constc'], m['name'][:64]))

    if unsat_zero:
        print('\n  --- zero-constant + demanding (the boot-23 set) ---')
        for mm, k, miss in unsat_zero:
            print('    @%-9d ts %-4d %-40s misses %s' %
                  (mm['_off'], k, ts_name(ts_spans[k][0]),
                   ['0x%08x' % h for h in sorted(miss)][:3]))
    return len(mats), len(zero), len(unsat_zero), len(unsat_nonzero)


if __name__ == '__main__':
    paths = sys.argv[1:] or ['../wiiu_ref/mp_raid_genuine.zone']
    for p in paths:
        report(p)
        print()
