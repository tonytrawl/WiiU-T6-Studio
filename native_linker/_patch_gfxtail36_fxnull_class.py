#!/usr/bin/env python3
"""gfxtail36: eliminate the whole boot-43 NULL-runtime-material FX class.

Boot 43 (gfxtail35) crashed at R_AddCodeMeshDrawSurf(NULL) via FX_DrawElem_Line:
a sprite/line batch buffer's material was set NULL by an earlier-drawn FX elem
whose runtime material was 0. DB-wide runtime enumeration (_b43_null_material_enum)
found 115 FX visual slots carrying the SAME degenerate alias 0xA1CC9351 (+ a few
siblings) — a converter default-stamp pointing at ONE forward target (b5 30184272)
that resolves to NULL at runtime (all 115 are forward-refs). The seagull elem2
(gfxtail35) was 1 of them; it just drew first.

Fix (generalizes the proven gfxtail35 method): for every ALIAS-valued sprite/line
visual slot (etype 0..6, vcount>0) whose runtime-resolved material is NOT a valid
loaded material, repoint it to the nearest-BACKWARD b5 holder of a valid FX
sprite/line material (drawn from the healthy sibling slots, so correct-kind,
guaranteed-loaded). Backward-ref enforced (deref fires at stream time). FOLLOW /
inline / vcount==0 slots are left untouched (repointing them would desync the
stream). Size-neutral value writes, clipMap-gated, closed-loop dump-verified.
"""
import bisect
import hashlib
import pickle
import struct
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
import fx_probe as FP
import xmodel_probe
from _dumplib import Dump
from measured_rtmap import MeasuredRuntimeMap

SRC = 'mp_skate_gfxtail35.zone'
DST = 'mp_skate_gfxtail36.zone'
FF = 'mp_skate_gfxtail36.ff'
DMP = r'C:\CemuDumps\Cemu.exe.19568.dmp'
BB = 84512493
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)
u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
i16 = lambda o: struct.unpack_from('>h', Z, o)[0]


# ---------------- console FX material-slot walker ---------------------------
def walk_slots(b, out):
    c = FP.Cur(bytes(Z), b + 76)

    def cstr():
        e = Z.index(b'\x00', c.o); c.o = e + 1

    name = None
    if u32(b) in PTRS:
        e = Z.index(b'\x00', c.o); name = bytes(Z[c.o:e]).decode('latin-1', 'replace'); c.o = e + 1
    n = i16(b + 8) + i16(b + 10) + i16(b + 12)
    if u32(b + 28) not in PTRS:
        return c.o, name
    base = c.o; c.o = base + n * FP.ED
    for k in range(n):
        eb = base + k * FP.ED
        et = Z[eb + 184]; vc = Z[eb + 185]; vic = Z[eb + 186]; vsc = Z[eb + 187]
        if u32(eb + 188) in PTRS:
            c.o += (vic + 1) * 96
        if u32(eb + 192) in PTRS:
            c.o += (vsc + 1) * 48
        vis = u32(eb + 196)

        def consume(w, et=et):
            if w not in PTRS:
                return
            if et in (FP.TYPE_SOUND, FP.TYPE_RUNNER):
                cstr()
            elif et <= 6:
                xmodel_probe.consume_material(bytes(Z), c)
            elif et == FP.TYPE_MODEL:
                end, _ = xmodel_probe.parse_xmodel(bytes(Z), c.o); c.o = end
            else:
                raise RuntimeError('vis %d' % et)

        ismat = et <= 6
        if et == FP.TYPE_DECAL:
            if vis in PTRS:
                mb = c.o; c.o += vc * 8
                for i in range(vc):
                    for kk in (0, 4):
                        w = u32(mb + i * 8 + kk)
                        out.append((name, k, et, vc, mb + i * 8 + kk, w, 'decal'))
                        if w in PTRS:
                            xmodel_probe.consume_material(bytes(Z), c)
        elif vc > 1:
            if vis in PTRS:
                ab = c.o; c.o += vc * 4
                for i in range(vc):
                    w = u32(ab + i * 4)
                    if ismat:
                        out.append((name, k, et, vc, ab + i * 4, w, 'array'))
                    consume(w)
        else:
            if ismat:
                out.append((name, k, et, vc, eb + 196, vis, 'single'))
            consume(vis)
        for off in (224, 228, 232, 252):
            if u32(eb + off) in PTRS:
                cstr()
        ext = u32(eb + 256)
        if ext in PTRS:
            if et == FP.TYPE_TRAIL:
                tb = c.o; c.o += 28
                vc_, ic_ = u32(tb + 12), u32(tb + 20)
                if u32(tb + 16) in PTRS:
                    c.o += vc_ * 20
                if u32(tb + 24) in PTRS:
                    c.o += ic_ * 2
            elif et == FP.TYPE_SPOT_LIGHT:
                c.o += 12
            else:
                raise RuntimeError('ext %d' % et)
        if u32(eb + 280) in PTRS:
            cstr()
    return c.o, name


S6 = pickle.load(open('_skate6_simmap.pkl', 'rb'))
fx_spans = sorted([(s, e, nm) for (i, nm, root, s, e) in S6['spans']
                   if root == 'FxEffectDef'], key=lambda t: t[0])
slots = []
for (s, e, _n) in fx_spans:
    end, _ = walk_slots(s, slots)
    assert end == e, (s, end, e)
print('material-visual slots: %d' % len(slots))

# ---------------- dump window + runtime map ---------------------------------
f, ranges = MB._load_dump_ranges(DMP)
base_w, G = MB._zone_window(f, ranges, orig, int(122e6))
Gnp = np.frombuffer(bytes(G), dtype='>u4')
d = Dump(DMP)
BASE = d.scan(RENAME, limit=2)[0] - 0x3f47a6a2
assert d.read(BASE + 0x3f47a6a2, 8) == b'wpc_sw4_'
rtm = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
ae = pickle.load(open('_skate6_realmap.pkl', 'rb'))['ae']


def rt(fo):
    return int(rtm.rt(fo - ae))


def Gval_off(rtoff):
    return struct.unpack_from('>I', G, rtoff)[0] if 0 <= rtoff + 4 <= len(G) else None


def guest_cstr(g, n=96):
    b = d.read(BASE + g, n) or b''
    i = b.find(b'\x00')
    return b[:i].decode('latin-1', 'replace') if i >= 0 else None


_mn = {}


def mat_name(v):
    if v in _mn:
        return _mn[v]
    r = None
    if DB(v):
        p = d.read(BASE + v, 4)
        if p:
            np_ = struct.unpack('>I', p)[0]
            s = guest_cstr(np_)
            if s and all(32 <= ord(c) < 127 for c in s) and ('/' in s or '_' in s or s.isalnum()):
                r = s
    _mn[v] = r
    return r


# ---------------- classify every material slot at runtime -------------------
# UNIFIED classifier (correct for single AND array entries): an alias slot is
# healthy iff its deref target is BACKWARD of the slot's own stream position AND
# G[target] holds a valid loaded material. (In-place G[slot_rt] is only written
# for SINGLE visuals; ARRAY entries resolve into a runtime array elsewhere, so
# reading in-place would false-flag every array entry.)
def Gval_at(b5off):
    return struct.unpack_from('>I', G, b5off)[0] if 0 <= b5off + 4 <= len(G) else None


valid_sprite_mats = Counter()   # DB ptr -> count, from healthy etype 0..6 slots
targets = []                    # broken alias-valued sprite/line slots
skip = Counter()
for (name, k, et, vc, foff, zval, how) in slots:
    if vc == 0:
        skip['vcount0'] += 1
        continue
    if not AL(zval):             # FOLLOW/inline/0 -> cannot safely repoint
        skip['non-alias(%s)' % ('FOLLOW' if zval in PTRS else '0' if zval == 0 else '?')] += 1
        continue
    slot_rt = rt(foff)           # slot's own stream position (b5 runtime offset)
    target = (zval - 1) & 0x1FFFFFFF
    gt = Gval_at(target)
    healthy = (target < slot_rt) and (gt is not None) and DB(gt) and bool(mat_name(gt))
    if healthy:
        if 0 <= et <= 6:
            valid_sprite_mats[gt] += 1
        skip['healthy'] += 1
        continue
    # broken: forward-ref (target >= slot_rt) or target not a valid material
    reason = 'fwd-ref' if (gt is not None and DB(gt) and mat_name(gt) and target >= slot_rt) \
        else 'bad-target'
    if 0 <= et <= 6:
        targets.append((name, k, et, vc, foff, zval, how, slot_rt))
    else:
        skip['broken-nonsprite(t%d,%s)' % (et, reason)] += 1
print('healthy sprite/line materials (distinct): %d' % len(valid_sprite_mats))
print('skip breakdown:', dict(skip))
print('TARGET broken sprite/line alias slots: %d' % len(targets))

# ---------------- holder index for valid sprite/line materials --------------
# GOOD = the healthy FX sprite/line materials (all proven FX-drawable). Prefer
# pure gfx_fxt_* particle materials; fall back to any GOOD value.
val2name = {}
for v in valid_sprite_mats:
    val2name[int(v)] = mat_name(int(v))
GOOD = set(val2name)
GFX = set(v for v, n in val2name.items() if n and n.startswith('gfx'))
print('GOOD materials:', {val2name[v]: int(valid_sprite_mats[v]) for v in GOOD})


def build_holders(vals):
    if not vals:
        return np.array([], dtype=np.int64), np.array([], dtype=np.uint32)
    mask = np.isin(Gnp, np.array(sorted(vals), dtype='>u4'))
    idx = np.nonzero(mask)[0]
    offs = (idx * 4).astype(np.int64)
    vv = Gnp[idx].astype(np.uint32)
    o = np.argsort(offs)
    return offs[o], vv[o]


all_offs, all_vals = build_holders(GOOD)
gfx_offs, gfx_vals = build_holders(GFX)
print('holder occurrences: all=%d (span %d..%d)  gfx=%d (span %d..%d)'
      % (len(all_offs), all_offs[0], all_offs[-1],
         len(gfx_offs), gfx_offs[0] if len(gfx_offs) else -1,
         gfx_offs[-1] if len(gfx_offs) else -1))


def nearest_backward(offs, vals, slot_rt):
    j = bisect.bisect_left(offs, slot_rt) - 1
    if j < 0:
        return None, None
    return int(offs[j]), int(vals[j])


# ---------------- compute fixes --------------------------------------------
fixes = {}
st = Counter()
examples = []
noop = []
for (name, k, et, vc, foff, zval, how, slot_rt) in targets:
    # prefer a backward gfx particle material; else any GOOD material
    t, v = nearest_backward(gfx_offs, gfx_vals, slot_rt)
    if t is None:
        t, v = nearest_backward(all_offs, all_vals, slot_rt)
    if t is None:
        st['no-backward-holder'] += 1
        continue
    assert t < slot_rt
    nm = val2name.get(int(v))
    if not nm:
        st['holder-not-material'] += 1
        continue
    new = 0xA0000000 + t + 1
    if new == zval:
        noop.append((name, k, et, foff, zval, t, nm))
        st['noop(new==orig)'] += 1
        continue
    fixes[foff] = new
    st['fixed'] += 1
    if len(examples) < 14:
        examples.append((name, k, et, foff, zval, new, t, slot_rt - t, nm))

print('\nfixes: %d  %s' % (len(fixes), dict(st)))
print('examples (fx, elem, type, slot_file, old, new_alias, holder_rt, dist, mat):')
for (name, k, et, foff, zval, new, t, dist, nm) in examples:
    print('  %-40s e%-2d t%-2d f=%-9d 0x%08X->0x%08X hb5=%-9d d=%-6d %s'
          % ((name or '?')[:40], k, et, foff, zval, new, t, dist, nm[:34]))
if noop:
    print('\nNO-OP targets (already point at a valid backward holder -> not broken?): %d' % len(noop))
    for (name, k, et, foff, zval, t, nm) in noop[:20]:
        print('  %-40s e%-2d t%-2d f=%-9d zval=0x%08X t=%d %s'
              % ((name or '?')[:40], k, et, foff, zval, t, nm))

# ---------------- apply, gate, pack ----------------------------------------
import re
import alloc_events as AE
import clipmap_console as CC


def gate(buf):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    return m.start() - end, end


d0, e0 = gate(Z)
print('\nGATE in: delta=%+d end=%d' % (d0, e0))
assert d0 == 0

for foff, new in fixes.items():
    struct.pack_into('>I', Z, foff, new)

d1, e1 = gate(Z)
print('GATE out: delta=%+d end=%d (want end==89584099)' % (d1, e1))
assert d1 == 0 and e1 == 89584099
assert len(Z) == len(orig)
words_changed = sum(1 for foff in fixes
                    if struct.unpack_from('>I', Z, foff)[0] != struct.unpack_from('>I', orig, foff)[0])
bytes_changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('words changed: %d (== %d fixes) ; bytes changed: %d'
      % (words_changed, len(fixes), bytes_changed))
assert words_changed == len(fixes), 'a fix did not change its word'

if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write)')
    sys.exit(0)

open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
