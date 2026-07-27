#!/usr/bin/env python3
"""FX pointer-slot census (boot-30/31 front). Mirrors fx_probe.parse_elem_dyn
EXACTLY, capturing every pointer slot; classifies each ALIAS slot's target
content in the boot-31 dump:
    material/model visual -> should hold a DB pointer (deref class)
    string ref (sound/runner/effect refs/spawnSound) -> should be ASCII name
Census only (read-only); the fix strategy is decided from the histogram.
"""
import pickle
import struct
import sys
from collections import Counter

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
import fx_probe as FP
import xmodel_probe

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000
SRC = 'mp_skate_gfxtail25.zone'
DMP = r'C:\CemuDumps\Cemu.exe.30080.dmp'

Z = open(SRC, 'rb').read()
S6 = pickle.load(open('_skate6_simmap.pkl', 'rb'))
SLOTS = []          # (slot_file, value, kind, fxname, elem)


def cap(off, v, kind, fxn, ei):
    SLOTS.append((off, v, kind, fxn, ei))


def parse_elem_dyn_cap(d, eb, c, fxn, ei):
    u32, i16 = FP.u32, FP.i16
    etype = d[eb + 184]
    vcount = d[eb + 185]
    vic, vsc = d[eb + 186], d[eb + 187]
    if u32(d, eb + 188) in PTRS:
        c.skip((vic + 1) * 96)
    if u32(d, eb + 192) in PTRS:
        c.skip((vsc + 1) * 48)
    vis = u32(d, eb + 196)
    vkind = ('string' if etype in (FP.TYPE_SOUND, FP.TYPE_RUNNER)
             else 'model' if etype == FP.TYPE_MODEL else 'material')
    if etype == FP.TYPE_DECAL:
        if vis in PTRS:
            mb = c.o
            c.skip(vcount * 8)
            for i in range(vcount):
                for k in (0, 4):
                    w = u32(d, mb + i * 8 + k)
                    cap(mb + i * 8 + k, w, 'material', fxn, ei)
                    if w in PTRS:
                        xmodel_probe.consume_material(d, c)
        else:
            cap(eb + 196, vis, 'markarray*', fxn, ei)
    elif vcount > 1:
        if vis in PTRS:
            ab = c.o
            c.skip(vcount * 4)
            for i in range(vcount):
                w = u32(d, ab + i * 4)
                cap(ab + i * 4, w, vkind, fxn, ei)
                if w in PTRS:
                    FP.visual_dyn(d, c, w, etype)
        else:
            cap(eb + 196, vis, 'visarray*', fxn, ei)
    else:
        cap(eb + 196, vis, vkind, fxn, ei)
        if vis in PTRS:
            FP.visual_dyn(d, c, vis, etype)
    for off in (224, 228, 232):
        w = u32(d, eb + off)
        cap(eb + off, w, 'fxref', fxn, ei)
        if w in PTRS:
            c.cstr()
    w = u32(d, eb + 252)
    cap(eb + 252, w, 'fxref', fxn, ei)
    if w in PTRS:
        c.cstr()
    ext = u32(d, eb + 256)
    if ext in PTRS:
        if etype == FP.TYPE_TRAIL:
            tb = c.o
            c.skip(28)
            vc_, ic_ = u32(d, tb + 12), u32(d, tb + 20)
            if u32(d, tb + 16) in PTRS:
                c.skip(vc_ * 20)
            if u32(d, tb + 24) in PTRS:
                c.skip(ic_ * 2)
        elif etype == FP.TYPE_SPOT_LIGHT:
            c.skip(12)
        else:
            raise FP.Fail('extended FOLLOW on type %d' % etype)
    elif ext != 0:
        cap(eb + 256, ext, 'extended*', fxn, ei)
    w = u32(d, eb + 280)
    cap(eb + 280, w, 'string', fxn, ei)
    if w in PTRS:
        c.cstr()


fails = 0
for (i, nm, root, s, e) in sorted(S6['spans'], key=lambda t: t[3]):
    if root != 'FxEffectDef':
        continue
    try:
        c = FP.Cur(Z, s + 76)
        name = c.cstr() if FP.u32(Z, s) in PTRS else '<alias>'
        n = FP.i16(Z, s + 8) + FP.i16(Z, s + 10) + FP.i16(Z, s + 12)
        if FP.u32(Z, s + 28) in PTRS:
            base = c.o
            c.skip(n * FP.ED)
            for k in range(n):
                parse_elem_dyn_cap(Z, base + k * FP.ED, c, name, k)
        if c.o != e:
            fails += 1
    except Exception:
        fails += 1
print('FX walked; %d mis-walks; %d slots captured' % (fails, len(SLOTS)))

al = [(o, v, k, f, e) for (o, v, k, f, e) in SLOTS if AL(v)]
print('alias slots: %d  by kind: %s' % (len(al), Counter(k for _, _, k, _, _ in al)))

f, ranges = MB._load_dump_ranges(DMP)
base, G = MB._zone_window(f, ranges, Z, int(122e6))


def classify(pay):
    if pay + 4 > len(G):
        return 'OOB'
    w = struct.unpack_from('>I', G, pay)[0]
    if DB(w):
        return 'DB-ptr'
    b = G[pay:pay + 24]
    if all(32 <= c < 127 for c in b.split(b'\x00')[0][:6]) and b.find(b'\x00') > 3:
        return 'ascii'
    return 'garbage'


cls = Counter()
bad = []
for (o, v, k, fxn, ei) in al:
    pay = (v - 1) & 0x1FFFFFFF
    c = classify(pay)
    cls[(k, c)] += 1
    if (k in ('material', 'model') and c != 'DB-ptr') or \
       (k in ('string', 'fxref') and c != 'ascii'):
        if len(bad) < 14:
            bad.append((o, v, k, c, fxn, ei))
print('target classes:')
for (k, c), n in cls.most_common(20):
    print('   %-12s -> %-8s x%d' % (k, c, n))
print('examples of broken:')
for (o, v, k, c, fxn, ei) in bad:
    print('   slot@%9d 0x%08x %-9s->%-8s %s elem%d' % (o, v, k, c, fxn[:34], ei))
