#!/usr/bin/env python3
"""Texdef image-handle alias fix (boots 30-34 root cause) -> mp_skate_gfxtail28.

Census: 3,167 texdef.image aliases (texdefs+i*16+12), 3,153 broken (deref class:
the loader writes slot := *(target); broken targets feed garbage 'GfxImage*'
into the image-stream list insert -> the deterministic crash at draw #11).

Scheme (all dump-anchored, boot-34 dump):
  * first occurrences are CONSOLE FOLLOW slots; their inline image NAME is at
    image body+328 (readable offline); their RUNTIME slot addr is found by the
    material-name string in b5 + the texdef hash literal (the loader keeps
    [.. mat name][texdef table] adjacent and writes DB image ptrs in place —
    probe-proven on mc/ch_factory_silo).
  * intents for alias slots: console alias <-> PC alias (materials paired by
    name, texdefs 1:1, PC stride 16 image@+12); PC payload = PC-runtime addr of
    the first occurrence; resolve by PC-drift interpolation (take-3 calibration
    + bootstrap) snapped to PC first-occurrence slot positions.
  * fix: alias := 0xA0000000 + slot_rt(first occurrence of intent) + 1,
    verified G[slot_rt] is a DB pointer.
  * fallback (unresolvable intent): a verified neutral first-occurrence slot —
    wrong texture, cannot crash.
"""
import bisect
import pickle
import struct
import sys
import time
from collections import Counter

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
from _nullct_oracle import scan
from _matconst_map import be32
import xmodel_probe as XP
import shader_probe as SP

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000
SRC = 'mp_skate_gfxtail27.zone'
OUT = 'mp_skate_gfxtail28'
DMP = r'C:\CemuDumps\Cemu.exe.23224.dmp'

t0 = time.time()
Z = open(SRC, 'rb').read()
P = open('../mp_skate_pc.zone', 'rb').read()
_, mats, _, _, _, _ = scan(SRC)
print('materials: %d  [%.0fs]' % (len(mats), time.time() - t0))

# ---------------- phase 1: console census ----------------
follow_first = {}      # image name -> (mat_name, k, slot_file, table_file, hash8)
alias_slots = []       # (slot_file, value, mat_name, k, table_file, hash8)
mat_meta = {}          # mat_name -> (texc, table_file, [slot kinds])
walk_err = 0
for m in mats:
    b = m['_off']
    mname = m.get('name') or ''
    texc = Z[b + 72]
    tsp, ttp = be32(Z, b + 80), be32(Z, b + 84)
    c = XP.Cur(Z, b + 104)
    try:
        if be32(Z, b) in PTRS:
            c.cstr()
        if tsp in PTRS:
            c.o, _ = SP.parse_techset(Z, c.o)
        if ttp not in PTRS:
            continue
        defs = c.o
        c.skip(texc * 16)
        kinds = []
        for i in range(texc):
            sp = defs + i * 16 + 12
            w = be32(Z, sp)
            kinds.append('F' if w in PTRS else ('A' if AL(w) else '0'))
            if w in PTRS:
                body = c.o
                XP.consume_image(Z, c)
                e = Z.index(b'\x00', body + 328)
                iname = Z[body + 328:e].decode('latin-1', 'replace')
                if iname and iname not in follow_first:
                    follow_first[iname] = (mname, i, sp, defs, Z[defs + i * 16:defs + i * 16 + 8])
            elif AL(w):
                alias_slots.append((sp, w, mname, i, defs, Z[defs + i * 16:defs + i * 16 + 8]))
        mat_meta[mname] = (texc, defs, kinds)
    except Exception:
        walk_err += 1
print('census: %d first-occurrence images, %d alias slots, %d walk errors  [%.0fs]'
      % (len(follow_first), len(alias_slots), walk_err, time.time() - t0))

# ---------------- phase 2: PC pairing ----------------
def pc_mat(nm):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b2 = i - 112
        if b2 >= 0 and struct.unpack_from('<I', P, b2)[0] in PTRS:
            return b2


pc_slot_of = {}        # (mat_name, k) -> (pc_slot_pos, pc_value)
pat_fail = 0
for mname, (texc, defs, kinds) in mat_meta.items():
    pb = pc_mat(mname)
    if pb is None:
        continue
    nm_end = P.index(b'\x00', pb + 112)
    ptab = nm_end + 1
    pk = []
    for i in range(texc):
        w = struct.unpack_from('<I', P, ptab + i * 16 + 12)[0]
        pk.append('F' if w in PTRS else ('A' if AL(w) else '0'))
    if pk != kinds:
        pat_fail += 1
        continue
    for i in range(texc):
        w = struct.unpack_from('<I', P, ptab + i * 16 + 12)[0]
        pc_slot_of[(mname, i)] = (ptab + i * 16 + 12, w)
print('PC pairing: %d slots paired, %d pattern mismatches  [%.0fs]'
      % (len(pc_slot_of), pat_fail, time.time() - t0))

# ---------------- phase 3: intents via PC drift interpolation ----------------
# calibration: take-3 material pairs
src3 = open('_fix_xmodel_handles3.py').read()
_g = dict(globals())
exec(src3.split('# ---------------- dump machinery')[0], _g)
pc_calib = {}
for (sp3, p3) in _g['ali']:
    nm3 = _g['intents'].get(sp3)
    if not nm3:
        continue
    pb3 = _g['pc_body_by_name'](nm3)
    if pb3 is not None:
        pc_calib[p3] = pb3
print('PC calibration pairs (take-3): %d  [%.0fs]' % (len(pc_calib), time.time() - t0))

# PC item universe: first-occurrence PC slot positions
items = sorted((pc_slot_of[(mn, k)][0], iname)
               for iname, (mn, k, sf, df, h8) in follow_first.items()
               if (mn, k) in pc_slot_of)
ipos = [t[0] for t in items]
print('PC first-occurrence items: %d' % len(items))

cal_k = sorted(pc_calib)
cal_d = [k - pc_calib[k] for k in cal_k]


def interp_pos(p):
    i = bisect.bisect_right(cal_k, p) - 1
    if i < 0:
        d = cal_d[0]
    elif i >= len(cal_k) - 1:
        d = cal_d[-1]
    else:
        x0, x1 = cal_k[i], cal_k[i + 1]
        y0, y1 = cal_d[i], cal_d[i + 1]
        d = y0 + (y1 - y0) * (p - x0) / (x1 - x0) if x1 > x0 else y0
    return p - d


def snap(ph, win):
    j = bisect.bisect_left(ipos, ph)
    best = None
    for k2 in range(max(0, j - 4), min(len(items), j + 4)):
        dd = abs(items[k2][0] - ph)
        if dd <= win and (best is None or dd < best[0]):
            best = (dd, items[k2][1], items[k2][0])
    return best


intent = {}
for rnd, win in ((0, 1500), (1, 4000), (2, 8000), (3, 16000), (4, 32000)):
    newly = {}
    for (sp, w, mname, k, defs, h8) in alias_slots:
        if sp in intent:
            continue
        ps = pc_slot_of.get((mname, k))
        if ps is None or not AL(ps[1]):
            continue
        p = (ps[1] & 0x1FFFFFFF) - 1
        got = snap(interp_pos(p), win)
        if got:
            intent[sp] = got[1]
            newly[p] = got[2]
    for p2, pos2 in newly.items():
        pc_calib[p2] = pos2
    cal_k = sorted(pc_calib)
    cal_d = [k2 - pc_calib[k2] for k2 in cal_k]
print('intents resolved: %d / %d  [%.0fs]' % (len(intent), len(alias_slots), time.time() - t0))

# ---------------- phase 4: runtime targets + fixes ----------------
f, ranges = MB._load_dump_ranges(DMP)
base, G = MB._zone_window(f, ranges, Z, int(122e6))

slot_rt_cache = {}


def slot_rt_of(iname):
    if iname in slot_rt_cache:
        return slot_rt_cache[iname]
    got = None
    ent = follow_first.get(iname)
    if ent:
        mn, k, sf, df, h8 = ent
        key = mn.encode('latin-1') + b'\x00'
        i = -1
        while True:
            i = G.find(key, i + 1)
            if i < 0:
                break
            j = G.find(h8, i, i + 160)     # texdef k's hash+flags literal
            if j < 0:
                continue
            table_rt = j - k * 16
            srt = table_rt + k * 16 + 12
            rv = struct.unpack_from('>I', G, srt)[0]
            if DB(rv):
                got = srt
                break
    slot_rt_cache[iname] = got
    return got


fixes = {}
st = Counter()
for (sp, w, mname, k, defs, h8) in alias_slots:
    nm = intent.get(sp)
    srt = slot_rt_of(nm) if nm else None
    if srt is None:
        st['fallback'] += 1
        continue
    fixes[sp] = 0xA0000000 + srt + 1
    st['fixed'] += 1
# fallback: a verified neutral slot (the silo colorMap slot — DB-verified)
fb = slot_rt_of('$identitynormalmap')
assert fb is not None
FB_ALIAS = 0xA0000000 + fb + 1
for (sp, w, mname, k, defs, h8) in alias_slots:
    if sp not in fixes:
        pay = (w - 1) & 0x1FFFFFFF
        rv = struct.unpack_from('>I', G, pay)[0] if pay + 4 <= len(G) else 0
        if DB(rv):
            st['left-healthy'] += 1
            continue
        fixes[sp] = FB_ALIAS
        st['fallback-applied'] += 1
print('fixes: %d  %s  [%.0fs]' % (len(fixes), dict(st), time.time() - t0))

if '--apply' not in sys.argv:
    print('DRY RUN')
    sys.exit(0)

Zn = bytearray(Z)
for sp, na in fixes.items():
    struct.pack_into('>I', Zn, sp, na)
import alloc_events
import clipmap_console
end, _ = alloc_events.clipmap_events(bytes(Zn), 84512493, '>',
                                     mat_span=clipmap_console._mat_span)
print('clipMap gate: end=%d %s' % (end, 'OK' if end == 89584099 else 'FAIL'))
assert end == 89584099
import hashlib
import wiiu_ff
open(OUT + '.zone', 'wb').write(bytes(Zn))
ff = wiiu_ff.pack(bytes(Zn), 'mp_skate')
open(OUT + '.ff', 'wb').write(ff)
print('wrote %s.zone (md5 %s)' % (OUT, hashlib.md5(bytes(Zn)).hexdigest()))
print('wrote %s.ff   (md5 %s, %d B)' % (OUT, hashlib.md5(ff).hexdigest(), len(ff)))
