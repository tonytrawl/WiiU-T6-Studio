#!/usr/bin/env python3
"""FX alias fix, final — PC-drift interpolation (boot-30/31 front) -> gfxtail26.

Console-side payload calibration is unusable (the broken reloc is NOT injective
— exact-match test produced a cross-kind collision). The PC payloads are
genuine PC-linker output: pc_payload = pc_runtime(target). take 3 yields ~1,400
clean (pc_payload -> pc_stream_pos(target)) pairs; PC drift is smooth
(~1.3 B/KB), so interpolation predicts a target's PC stream position to within
a few hundred bytes; snap to the nearest kind-compatible inline item.

Fixes (size-neutral, dump-verified): material -> earliest b5 holder of the DB
Material (closed loop: DB name == intent); string/fxref -> runtime address of
the intent name string.
"""
import bisect
import pickle
import struct
import sys
from collections import Counter

sys.argv = ['x'] + [a for a in sys.argv[1:]]

# ---- phase 1: take-3 head -> PC calibration -------------------------------
src3 = open('_fix_xmodel_handles3.py').read()
exec(src3.split('# ---------------- dump machinery')[0])
pc_calib = {}
for (sp, p) in ali:                       # take-3: (console slot, pc payload)
    nm = intents.get(sp)
    if not nm:
        continue
    pb = pc_body_by_name(nm)
    if pb is None:
        continue
    if p in pc_calib and pc_calib[p] != pb:
        continue                          # inconsistent — drop
    pc_calib[p] = pb
print('PC calibration pairs: %d' % len(pc_calib))
cal_k = sorted(pc_calib)
cal_d = [k - pc_calib[k] for k in cal_k]  # drift = payload - stream pos


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


# ---- phase 2: FX walk + pairing (from visuals2) ----------------------------
src2 = open('_fix_fx_visuals2.py').read()
exec(src2.split('# ---------------- unified PC target universe')[0])

# unified item universe: FX inline items + every inline material body's PC pos
from _nullct_oracle import scan

_, mats, _, _, _, _ = scan(SRC)
for nm in sorted({(m.get('name') or '') for m in mats}):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            break
        b = i - 112
        if b >= 0 and struct.unpack_from('<I', P, b)[0] in PTRS:
            pc_items.append((b, 'material', nm))
            break
items = sorted({(p, k, n) for (p, k, n) in pc_items if n})
ipos = [t[0] for t in items]
print('item universe: %d' % len(items))

STRK = {'string', 'fxref', 'fxname'}


def snap(pos_hat, kind, win=1500):
    j = bisect.bisect_left(ipos, pos_hat)
    best = None
    for k in range(max(0, j - 6), min(len(items), j + 6)):
        pos, ik, nm = items[k]
        ok = (ik == 'material') if kind == 'material' else (ik in STRK)
        if not ok:
            continue
        d = abs(pos - pos_hat)
        if d <= win and (best is None or d < best[0]):
            best = (d, nm, pos)
    return best


ali_fx = []
for idx, (name, s, slots, itms) in enumerate(co_fx):
    ps = pc_slots_all.get(idx)
    if ps is None:
        continue
    for (cp, cv, ck), (pp, pv, pk) in zip(slots, ps):
        if AL(cv) and ck in ('material', 'string', 'fxref') and AL(pv):
            ali_fx.append((cp, ck, (pv & 0x1FFFFFFF) - 1))
print('FX alias slots with PC payloads: %d' % len(ali_fx))

resolved = {}
snapd = Counter()
extra_anchor = {}
for rnd, win in ((0, 1500), (1, 3000), (2, 6000)):
    if extra_anchor:
        for p2, pos2 in extra_anchor.items():
            pc_calib[p2] = pos2
        cal_k = sorted(pc_calib)
        cal_d = [k - pc_calib[k] for k in cal_k]
        extra_anchor = {}
    for (cp, ck, p) in ali_fx:
        if cp in resolved:
            continue
        got = snap(interp_pos(p), ck, win)
        if got:
            resolved[cp] = (ck, got[1])
            snapd['r%d' % rnd] += 1
            extra_anchor[p] = got[2]
print('resolved %d/%d across rounds: %s' % (len(resolved), len(ali_fx), dict(snapd)))

# ---- phase 3: dump-verified fixes ------------------------------------------
import measure_band as MB
from _dumplib import Dump

f2, ranges2 = MB._load_dump_ranges(r'C:\CemuDumps\Cemu.exe.30080.dmp')
Z25 = open('mp_skate_gfxtail25.zone', 'rb').read()
base_w, G = MB._zone_window(f2, ranges2, Z25, int(122e6))
d = Dump(r'C:\CemuDumps\Cemu.exe.30080.dmp')
hits2 = d.scan(b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00', limit=2)
BASE = hits2[0] - 0x3f47a6a2
assert d.read(BASE + 0x3f47a6a2, 8) == b'wpc_sw4_'


def guest_cstr(g, maxlen=96):
    b = d.read(BASE + g, maxlen) or b''
    i = b.find(b'\x00')
    return b[:i].decode('latin-1', 'replace') if i >= 0 else None


_dbc = {}


def db_material(nm):
    if nm in _dbc:
        return _dbc[nm]
    key = nm.encode('latin-1') + b'\x00'
    gaddrs = [x - BASE for x in d.scan(key, limit=8)]
    targets = {struct.pack('>I', g) for g in gaddrs if 0 < g < 0x50000000}
    M = None
    for gb in range(0x10000000, 0x13000000, 1 << 20):
        blk = d.read(BASE + gb, 1 << 20)
        if not blk:
            continue
        for t in targets:
            j = -1
            while True:
                j = blk.find(t, j + 1)
                if j < 0:
                    break
                M = gb + j if M is None else ('AMBIG' if gb + j != M else M)
        if M == 'AMBIG':
            break
    _dbc[nm] = M
    return M


fixes = {}
st = Counter()
for cp, (ck, nm) in sorted(resolved.items()):
    if ck == 'material':
        M = db_material(nm)
        if M in (None, 'AMBIG'):
            st['dbmat-%s' % M] += 1
            continue
        t = G.find(struct.pack('>I', M))
        if t < 0:
            st['no-holder'] += 1
            continue
        mn = guest_cstr(struct.unpack_from('>I', d.read(BASE + M, 4) or b'\x00' * 4, 0)[0])
        if mn != nm:
            st['loop-mismatch'] += 1
            continue
        fixes[cp] = 0xA0000000 + t + 1
        st['material'] += 1
    else:
        t = G.find(b'\x00' + nm.encode('latin-1') + b'\x00')
        if t < 0:
            st['no-string'] += 1
            continue
        fixes[cp] = 0xA0000000 + (t + 1) + 1
        st[ck] += 1
print('intent fixes: %d  %s' % (len(fixes), dict(st)))

# ---- fallbacks: EVERY remaining broken alias must point at a VALID target of
# its kind (wrong-but-valid = cosmetic; garbage = the crash). ----
FB_MAT = 'gfx_fxt_smk_gen_z120'          # proven present (DB 0x1047d218)
M = db_material(FB_MAT)
t = G.find(struct.pack('>I', M))
assert t >= 0
FB_MAT_ALIAS = 0xA0000000 + t + 1
# fallback fx name: first console FX's own name string at runtime
fbname = next(n for (n, s, sl, it) in co_fx if n)
tfx = G.find(b'\x00' + fbname.encode('latin-1') + b'\x00')
assert tfx >= 0
FB_FX_ALIAS = 0xA0000000 + (tfx + 1) + 1
# fallback sound string: 'null' if present, else any short ascii alias name
tsnd = G.find(b'\x00null\x00')
FB_SND_ALIAS = 0xA0000000 + (tsnd + 1) + 1 if tsnd >= 0 else FB_FX_ALIAS

# all broken slots from the census walk (includes unpaired-FX slots)
DBv = DB
fb = Counter()
for idx, (name, s, slots, itms) in enumerate(co_fx):
    for (cp, cv, ck) in slots:
        if not AL(cv) or ck not in ('material', 'string', 'fxref'):
            continue
        if cp in fixes:
            continue
        pay = (cv - 1) & 0x1FFFFFFF
        w = struct.unpack_from('>I', G, pay)[0] if pay + 4 <= len(G) else 0
        if ck == 'material' and DBv(w):
            continue                      # healthy
        if ck in ('string', 'fxref'):
            b2 = G[pay:pay + 24].split(b'\x00')[0]
            if 3 < len(b2) < 24 and all(32 <= c < 127 for c in b2):
                continue                  # healthy ascii
        fixes[cp] = (FB_MAT_ALIAS if ck == 'material' else
                     FB_FX_ALIAS if ck == 'fxref' else FB_SND_ALIAS)
        fb['fallback-' + ck] += 1
print('fallbacks: %s;  TOTAL fixes: %d' % (dict(fb), len(fixes)))

if '--apply' not in sys.argv:
    print('DRY RUN')
    sys.exit(0)

Zn = bytearray(Z25)
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
open('mp_skate_gfxtail26.zone', 'wb').write(bytes(Zn))
ff = wiiu_ff.pack(bytes(Zn), 'mp_skate')
open('mp_skate_gfxtail26.ff', 'wb').write(ff)
print('wrote mp_skate_gfxtail26.zone (md5 %s)' % hashlib.md5(bytes(Zn)).hexdigest())
print('wrote mp_skate_gfxtail26.ff   (md5 %s, %d B)' % (hashlib.md5(ff).hexdigest(), len(ff)))
