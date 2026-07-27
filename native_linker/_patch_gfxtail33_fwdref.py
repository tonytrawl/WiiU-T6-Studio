#!/usr/bin/env python3
"""gfxtail33: fix the 5 FORWARD-REFERENCE texdef image aliases (boot-40 front).

Boot 40 (dump 33192, 1,857 draws): R_SetSampler crashed writing image->0x98
with image == 0xFFFFFFFF. Runtime census of ALL 1,214 materials' texdef tables
(name+hash8 adjacency in the dump) found exactly 5 slots holding -1:
  mc/mtl_p_pent_security_rope[0], mc/mtl_veh_t6_dlc_news_van_poptop_dead[1],
  mc/mtl_veh_t6_sportscar_rubber_d[0], mc/mtl_skt_ferris_wheel_carriage_purple[0],
  mc/mtl_skt_banner_waving02[0].

Root cause — NEW COROLLARY to the loader-deref rule: slot := *(target) fires
WHEN THE SLOT STREAMS IN. All 5 aliases point FORWARD (target file offset >
slot offset), at first-occurrence image slots still holding the FOLLOW
placeholder (-1) at deref time. The targets read DB in the final dump — a
load-ORDER bug invisible to end-state target checks. DEREF TARGETS MUST BE
BACKWARD REFERENCES.

Fix: repoint each slot to an EARLIER material's first-occurrence (FOLLOW)
image slot with the SAME texdef nameHash (same map kind — wrong texture at
worst, cannot crash), runtime slot located by the dump adjacency method and
verified DB + backward.
"""
import hashlib
import re
import struct
import sys

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
import measure_band as MB
import xmodel_probe as XP
import shader_probe as SP
from _matconst_map import be32, FOLLOW, PTRS
from _nullct_oracle import scan

SRC = 'mp_skate_gfxtail32.zone'
DST = 'mp_skate_gfxtail33.zone'
FF = 'mp_skate_gfxtail33.ff'
BB = 84512493
DMP = r'C:\CemuDumps\Cemu.exe.33192.dmp'
BAD = {('mc/mtl_p_pent_security_rope', 0),
       ('mc/mtl_veh_t6_dlc_news_van_poptop_dead', 1),
       ('mc/mtl_veh_t6_sportscar_rubber_d', 0),
       ('mc/mtl_skt_ferris_wheel_carriage_purple', 0),
       ('mc/mtl_skt_banner_waving02', 0)}
DB = lambda v: 0x10000000 <= v < 0x13000000

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    delta = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, delta))
    return delta


assert gate(Z, 'in') == 0

f, ranges = MB._load_dump_ranges(DMP)
base, G = MB._zone_window(f, ranges, orig, int(122e6))
_, mats, _, _, _, _ = scan(SRC)
mats.sort(key=lambda m: m['_off'])


def tables(m):
    """(defs_file, name) or None — texdef table file offset of a material."""
    b = m['_off']
    texc = orig[b + 72]
    if texc == 0:
        return None
    tsp, ttp = be32(orig, b + 80), be32(orig, b + 84)
    c = XP.Cur(orig, b + 104)
    if be32(orig, b) in PTRS:
        c.cstr()
    if tsp in PTRS:
        c.o, _ = SP.parse_techset(orig, c.o)
    if ttp not in PTRS:
        return None
    return c.o, texc


def slot_rt(mname, defs, k):
    """runtime address of texdef slot k via name + hash8 adjacency."""
    h8 = orig[defs:defs + 8]
    key = mname.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = G.find(key, i + 1)
        if i < 0:
            return None
        j = G.find(h8, i, i + 160)
        if j < 0:
            continue
        return j + k * 16 + 12


fixes = {}
for m in mats:
    nm = m.get('name') or ''
    for (bn, bk) in BAD:
        if nm != bn:
            continue
        t = tables(m)
        assert t, nm
        defs, texc = t
        sp = defs + bk * 16 + 12
        want_hash = be32(orig, defs + bk * 16)
        # find the LATEST earlier material with a FOLLOW slot of the same hash
        cand = None
        for m2 in mats:
            if m2['_off'] >= m['_off']:
                break
            t2 = tables(m2)
            if not t2:
                continue
            d2, c2 = t2
            for k2 in range(c2):
                if be32(orig, d2 + k2 * 16) != want_hash:
                    continue
                if be32(orig, d2 + k2 * 16 + 12) != FOLLOW:
                    continue
                cand = (m2, d2, k2)
        assert cand, 'no earlier same-hash inline holder for %s[%d] hash 0x%08x' % (nm, bk, want_hash)
        m2, d2, k2 = cand
        srt = slot_rt(m2.get('name') or '', d2, k2)
        assert srt is not None, 'runtime slot not located for %s' % m2['name']
        rv = struct.unpack_from('>I', G, srt)[0]
        assert DB(rv), 'target not DB: 0x%08x' % rv
        na = 0xA0000000 + srt + 1
        fixes[sp] = na
        print('%-46s [%d] hash 0x%08x -> holder %-46s [%d] slot_rt=%d *=0x%08x alias 0x%08x'
              % (nm[:46], bk, want_hash, (m2.get('name') or '')[:46], k2, srt, rv, na))

assert len(fixes) == 5, 'expected exactly 5 fixes, got %d' % len(fixes)
for sp, na in fixes.items():
    struct.pack_into('>I', Z, sp, na)
assert len(Z) == len(orig)
assert gate(Z, 'out') == 0

changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
