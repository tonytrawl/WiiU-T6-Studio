#!/usr/bin/env python3
"""FX alias fix (boot-30/31 front) -> mp_skate_gfxtail26.

Census (boot-31 dump): of 345 FX alias slots, broken = 166 material visuals +
74 sound/runner strings + 36 effect refs (model visuals 62/62 already fine).

Intent: PC FX walked with the same (PC-IDENTICAL layout) walker in LE; assets
paired BY ORDER (79<->79, validated by name+slot-kind pattern); PC alias
payloads (PC-runtime addrs) resolved to a unified PC target universe — every
INLINE-consumed item (material bodies, ref/sound name strings, fx names) in
stream order — by the take-3 monotone-drift DP.

Console fixes (all size-neutral VALUE rewrites, dump-verified):
  material -> earliest b5 holder of the DB Material (take-3 method), closed
              loop: DB name == intent
  string/fxref -> runtime address of the intent name string in b5 (engine
              reads chars and resolves by name)
"""
import bisect
import pickle
import struct
import sys
from collections import Counter

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
from _dumplib import Dump
import fx_probe as FP
import xmodel_probe

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000
SRC = 'mp_skate_gfxtail25.zone'
OUT = 'mp_skate_gfxtail26'
DMP = r'C:\CemuDumps\Cemu.exe.30080.dmp'
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
RENAME_GUEST = 0x3f47a6a2

Z = open(SRC, 'rb').read()
P = open('../mp_skate_pc.zone', 'rb').read()
S6 = pickle.load(open('_skate6_simmap.pkl', 'rb'))


# ---------------- generic FX walker (BE console / LE pc) ----------------
def walk_fx(d, s, le, cap_slot, cap_item):
    """cap_slot(pos, val, kind); cap_item(pos, kind, name) for inline items."""
    fmt = '<' if le else '>'
    u32 = lambda o: struct.unpack_from(fmt + 'I', d, o)[0]
    i16 = lambda o: struct.unpack_from(fmt + 'h', d, o)[0]
    MATB = 112 if le else 104

    class C:
        o = 0

        def skip(self, n):
            self.o += n

        def cstr(self, maxlen=128):
            e = d.index(b'\x00', self.o)
            r = d[self.o:e]
            self.o = e + 1
            return r.decode('latin-1', 'replace')

    c = C()
    c.o = s + 76

    def cstr():
        e = d.index(b'\x00', c.o)
        r = d[c.o:e].decode('latin-1', 'replace')
        c.o = e + 1
        return r

    def consume_mat():
        b = c.o
        if le:
            import material_convert as MCX
            _, nxt = MCX.convert_material(d, b)
            c.o = nxt
        else:
            xmodel_probe.consume_material(d, c)
        nm = None
        try:
            e = d.index(b'\x00', b + MATB)
            nm = d[b + MATB:e].decode('latin-1', 'replace')
        except Exception:
            pass
        return b, nm

    name = cstr() if u32(s) in PTRS else None
    cap_item(s + 76, 'fxname', name)
    n = i16(s + 8) + i16(s + 10) + i16(s + 12)
    if u32(s + 28) not in PTRS:
        return c.o, name
    base = c.o
    c.o += n * FP.ED
    for k in range(n):
        eb = base + k * FP.ED
        etype = d[eb + 184]
        vcount = d[eb + 185]
        vic, vsc = d[eb + 186], d[eb + 187]
        if u32(eb + 188) in PTRS:
            c.o += (vic + 1) * 96
        if u32(eb + 192) in PTRS:
            c.o += (vsc + 1) * 48
        vis = u32(eb + 196)
        vkind = ('string' if etype in (FP.TYPE_SOUND, FP.TYPE_RUNNER)
                 else 'model' if etype == FP.TYPE_MODEL else 'material')

        def one_visual(pos, w):
            cap_slot(pos, w, vkind)
            if w in PTRS:
                if vkind == 'string':
                    p0 = c.o
                    nmx = cstr()
                    cap_item(p0, 'string', nmx)
                elif vkind == 'material':
                    b, nmx = consume_mat()
                    cap_item(b, 'material', nmx)
                else:
                    if le:
                        import xmodel_pc
                        end = xmodel_pc.parse_xmodel_pc(d, c.o)
                        c.o = end
                    else:
                        end, _ = xmodel_probe.parse_xmodel(d, c.o)
                        c.o = end

        if etype == FP.TYPE_DECAL:
            if vis in PTRS:
                mb = c.o
                c.o += vcount * 8
                for i in range(vcount):
                    for kk in (0, 4):
                        w = u32(mb + i * 8 + kk)
                        cap_slot(mb + i * 8 + kk, w, 'material')
                        if w in PTRS:
                            b, nmx = consume_mat()
                            cap_item(b, 'material', nmx)
        elif vcount > 1:
            if vis in PTRS:
                ab = c.o
                c.o += vcount * 4
                for i in range(vcount):
                    one_visual(ab + i * 4, u32(ab + i * 4))
        else:
            one_visual(eb + 196, vis)
        for off in (224, 228, 232, 252):
            w = u32(eb + off)
            cap_slot(eb + off, w, 'fxref')
            if w in PTRS:
                p0 = c.o
                nmx = cstr()
                cap_item(p0, 'fxref', nmx)
        ext = u32(eb + 256)
        if ext in PTRS:
            if etype == FP.TYPE_TRAIL:
                tb = c.o
                c.o += 28
                vc_, ic_ = u32(tb + 12), u32(tb + 20)
                if u32(tb + 16) in PTRS:
                    c.o += vc_ * 20
                if u32(tb + 24) in PTRS:
                    c.o += ic_ * 2
            elif etype == FP.TYPE_SPOT_LIGHT:
                c.o += 12
            else:
                raise FP.Fail('ext type %d' % etype)
        w = u32(eb + 280)
        cap_slot(eb + 280, w, 'string')
        if w in PTRS:
            p0 = c.o
            nmx = cstr()
            cap_item(p0, 'string', nmx)
    return c.o, name


# console walk
co_fx = []
for (i, nm, root, s, e) in sorted(S6['spans'], key=lambda t: t[3]):
    if root != 'FxEffectDef':
        continue
    slots, items = [], []
    end, name = walk_fx(Z, s, False,
                        lambda p, v, k: slots.append((p, v, k)),
                        lambda p, k, n2: items.append((p, k, n2)))
    assert end == e, (s, end, e)
    co_fx.append((name, s, slots, items))
print('console FX: %d walked clean' % len(co_fx))

# PC FX: locate bodies by ORDER using the console names where available.
# PC FX bodies found by scanning for the same name strings at +76.
def pc_fx_by_name(nm):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b = i - 76
        if b >= 0 and struct.unpack_from('<I', P, b)[0] == FOLLOW:
            l, o_, e_ = struct.unpack_from('<3h', P, b + 8)
            if 0 <= l < 100 and 0 <= o_ < 100 and 0 <= e_ < 100:
                return b
    return None


pc_fx = []
anchor = []
for idx, (name, s, slots, items) in enumerate(co_fx):
    b = pc_fx_by_name(name) if name else None
    anchor.append(b)
# order-window fill for unnamed/missing
known = [(i, b) for i, b in enumerate(anchor) if b is not None]
print('PC FX by name: %d/%d; order-filling the rest' % (len(known), len(co_fx)))
for idx in range(len(co_fx)):
    if anchor[idx] is not None:
        continue
    lo = max((b for i, b in known if i < idx), default=0)
    hi = min((b for i, b in known if i > idx), default=len(P) - 80)
    # find PC FX bodies in (lo, hi): elem counts must match console's
    name, s, slots, items = co_fx[idx]
    cn = struct.unpack_from('>3h', Z, s + 8)
    cands = []
    p = lo
    while True:
        p = P.find(struct.pack('<3h', *cn), p + 1, hi)
        if p < 0:
            break
        b = p - 8
        if struct.unpack_from('<I', P, b)[0] == FOLLOW and \
           struct.unpack_from('<I', P, b + 28)[0] in (FOLLOW, INSERT, 0) or True:
            # validate by walking
            try:
                sl2, it2 = [], []
                walk_fx(P, b, True, lambda p2, v2, k2: sl2.append((p2, v2, k2)),
                        lambda p2, k2, n2: it2.append((p2, k2, n2)))
                if [k for _, _, k in sl2] == [k for _, _, k in slots]:
                    cands.append(b)
            except Exception:
                pass
    if len(cands) == 1:
        anchor[idx] = cands[0]
# gap-zip: within each between-anchors gap, if unpaired console FX count ==
# matching PC candidates count, pair them in order
gaps = {}
for idx in range(len(co_fx)):
    if anchor[idx] is not None:
        continue
    lo = max(((i, b) for i, b in enumerate(anchor) if b is not None and i < idx),
             key=lambda t: t[0], default=(None, 0))
    gaps.setdefault(lo[0], []).append(idx)
for lo_i, idxs in gaps.items():
    lo = anchor[lo_i] if lo_i is not None else 0
    his = [b for i, b in enumerate(anchor) if b is not None and i > max(idxs)]
    hi = min(his) if his else len(P) - 80
    cands = []
    p = lo
    while True:
        # any FX-shaped body: name FOLLOW @0 + plausible counts
        p = P.find(b'\xff\xff\xff\xff', p + 1, hi)
        if p < 0:
            break
        l, o_, e_ = struct.unpack_from('<3h', P, p + 8)
        if not (0 <= l < 100 and 0 <= o_ < 100 and 0 <= e_ < 100 and l + o_ + e_ > 0):
            continue
        try:
            sl2 = []
            walk_fx(P, p, True, lambda p2, v2, k2: sl2.append(k2),
                    lambda p2, k2, n2: None)
        except Exception:
            continue
        cands.append((p, sl2))
    for idx in idxs:
        name, s, slots, items = co_fx[idx]
        want = [k for _, _, k in slots]
        fit = [p for (p, sl2) in cands if sl2 == want and
               (not any(a == p for a in anchor))]
        if len(fit) >= 1:
            anchor[idx] = fit[0]
print('PC FX paired: %d/%d' % (sum(1 for b in anchor if b is not None), len(co_fx)))

# PC walk: slots + unified target items
pc_slots_all = {}
pc_items = []
for idx, b in enumerate(anchor):
    if b is None:
        continue
    sl2, it2 = [], []
    walk_fx(P, b, True, lambda p2, v2, k2: sl2.append((p2, v2, k2)),
            lambda p2, k2, n2: it2.append((p2, k2, n2)))
    name, s, slots, items = co_fx[idx]
    if [k for _, _, k in sl2] != [k for _, _, k in slots]:
        print('   pattern mismatch on FX %s — skipped' % name)
        continue
    pc_slots_all[idx] = sl2
    pc_items.extend(it2)
print('PC items captured from FX: %d' % len(pc_items))

# ---------------- unified PC target universe ----------------
# + all inline material bodies in XModels (cross-asset dedup), by name search
from _nullct_oracle import scan

_, mats, _, _, _, _ = scan(SRC)
mat_names = sorted({(m.get('name') or '') for m in mats})


def pc_mat_by_name(nm):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b = i - 112
        if b >= 0 and struct.unpack_from('<I', P, b)[0] in PTRS:
            return b


for nm in mat_names:
    b = pc_mat_by_name(nm)
    if b is not None:
        pc_items.append((b, 'material', nm))
items = sorted({(p, k, n) for (p, k, n) in pc_items if n})
print('unified PC target universe: %d items' % len(items))

# ---------------- payload clusters -> DP over the universe ----------------
STRKIND = {'string', 'fxref', 'fxname'}


def compat(pk, ik):
    if pk == 'material':
        return ik == 'material'
    return ik in STRKIND


ali = []
for idx, (name, s, slots, itms) in enumerate(co_fx):
    ps = pc_slots_all.get(idx)
    if ps is None:
        continue
    for (cp, cv, ck), (pp, pv, pk) in zip(slots, ps):
        if AL(cv) and ck in ('material', 'string', 'fxref'):
            if AL(pv):
                ali.append((cp, ck, (pv & 0x1FFFFFFF) - 1))
print('alias slots with PC intent: %d (of 345 census aliases)' % len(ali))

pays = sorted({(p, k) for (_, k, p) in ali})
# DP: items in pos order; payloads in order; drift monotone (slack 512)
ipos = [t[0] for t in items]
cand = []
for (p, k) in pays:
    cs = []
    for j, (pos, ik, nm) in enumerate(items):
        if compat(k, ik):
            cs.append((j, p - (pos - 64)))
    cand.append(cs)
N = len(pays)
layers = []
for ci in range(N):
    layer = []
    for (j, dr) in cand[ci]:
        best = None
        for pi in range(len(layers) - 1, -1, -1):
            for kk, (j2, dr2, sc2, par2) in enumerate(layers[pi]):
                if j2 < j and dr2 >= dr - 512:
                    if best is None or sc2 > best[0]:
                        best = (sc2, pi, kk)
            if best is not None and best[0] == pi + 1:
                break
        sc = 1 + (best[0] if best else 0)
        layer.append((j, dr, sc, best))
    layers.append(layer)
endci = endk = -1
endsc = -1
for ci in range(N):
    for kk, (j, dr, sc, par) in enumerate(layers[ci]):
        if sc > endsc:
            endci, endk, endsc = ci, kk, sc
assign = {}
ci, kk = endci, endk
while ci >= 0 and kk >= 0:
    j, dr, sc, par = layers[ci][kk]
    assign[ci] = j
    if par is None:
        break
    _, ci, kk = par
print('DP assigned %d/%d distinct (payload,kind)' % (endsc, N))

pay_intent = {}
for ci, (p, k) in enumerate(pays):
    j = assign.get(ci)
    if j is not None:
        pay_intent[(p, k)] = items[j][2]

# ---------------- dump machinery + fixes ----------------
f, ranges = MB._load_dump_ranges(DMP)
base_w, G = MB._zone_window(f, ranges, Z, int(122e6))
d = Dump(DMP)
hits = d.scan(RENAME, limit=2)
BASE = hits[0] - RENAME_GUEST
assert d.read(BASE + RENAME_GUEST, 8) == b'wpc_sw4_', 'dump BASE control'


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
    CH = 1 << 20
    for gb in range(0x10000000, 0x13000000, CH):
        blk = d.read(BASE + gb, CH)
        if not blk:
            continue
        for t in targets:
            j = -1
            while True:
                j = blk.find(t, j + 1)
                if j < 0:
                    break
                if M is None:
                    M = gb + j
                elif gb + j != M:
                    M = 'AMBIG'
        if M == 'AMBIG':
            break
    _dbc[nm] = M
    return M


fixes = {}
stats = Counter()
for (sp, k, p) in ali:
    nm = pay_intent.get((p, k))
    if not nm:
        stats['no-intent'] += 1
        continue
    if k == 'material':
        M = db_material(nm)
        if M in (None, 'AMBIG'):
            stats['dbmat-%s' % M] += 1
            continue
        t = G.find(struct.pack('>I', M))
        if t < 0:
            stats['no-holder'] += 1
            continue
        mn = guest_cstr(struct.unpack_from(
            '>I', d.read(BASE + M, 4) or b'\x00' * 4, 0)[0])
        if mn != nm:
            stats['name-mismatch'] += 1
            continue
        fixes[sp] = 0xA0000000 + t + 1
        stats['material-fixed'] += 1
    else:
        key = b'\x00' + nm.encode('latin-1') + b'\x00'
        t = G.find(key)
        if t < 0:
            stats['no-string'] += 1
            continue
        fixes[sp] = 0xA0000000 + (t + 1) + 1
        stats['string-fixed'] += 1
print('fixes: %d   %s' % (len(fixes), dict(stats)))

if '--apply' not in sys.argv:
    print('\nDRY RUN')
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
