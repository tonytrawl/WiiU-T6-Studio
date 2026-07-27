"""STEP 0 GO/NO-GO measurement (throwaway).

ONE question with a number: for the misaligned FX 'visuals' aliasers in
mp_skate_final.zone (OURS), what fraction does the KEY-FREE snap-then-map
derivation resolve to the SAME console target that the answer key
(mp_skate_gfxtail46.zone) assigns?

Derivation under test = the never-shipped snap() idea (_r1x_clean.py:96):
  PC sim -> pc_inv = InverseMap(em.omap); for an aliaser's PC visuals payload
  p = (v & 0x1FFFFFFF) - 1:  streamoff = pc_inv.stream(p);  snap streamoff to
  the NEAREST PC inline-Material record start (the owner materials) within a
  window -> owner identity; map owner -> the CONSOLE owner material whose
  RT.rt() address is the derived alias value. Score vs the key.

Scoring uses the KEY only for the target value. Reuses fx_backref_fix (console
walker), fx_pc + material_convert (PC walker), measured_rtmap. No zone written.
"""
import sys, struct, bisect, time, statistics
from collections import Counter
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import fx_backref_fix as FB
import fx_pc
import material_convert as MC
import wiiu_zone
from measured_rtmap import MeasuredRuntimeMap

OURS = 'mp_skate_final.zone'
KEY = 'mp_skate_gfxtail46.zone'
PCPATH = '../mp_skate_pc.zone'
PTRS = (0xFFFFFFFF, 0xFFFFFFFE)
ED, VIS, HDR = 292, 196, 76
STR_T = (10, 12)
be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
le32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
be16s = lambda d, o: struct.unpack_from('>h', d, o)[0]
le16s = lambda d, o: struct.unpack_from('<h', d, o)[0]
is_al = lambda v: 0xA0000000 <= v < 0xC0000000
pay = lambda v: (v - 1) & 0x1FFFFFFF
T0 = time.time()
log = lambda *a: print('[%5.1fs]' % (time.time() - T0), *a)


def con_elembase(Z, s, e):
    c = s + HDR
    if be32(Z, s) in PTRS:
        z = Z.find(b'\x00', c, e); c = z + 1 if z >= 0 else c
    n = be16s(Z, s + 8) + be16s(Z, s + 10) + be16s(Z, s + 12)
    return c, n


def pc_elembase(P, s):
    c = s + HDR
    if le32(P, s) in PTRS:
        z = P.find(b'\x00', c); c = z + 1 if z >= 0 else c
    n = le16s(P, s + 8) + le16s(P, s + 10) + le16s(P, s + 12)
    return c, n


def pc_owner_mats(P, s):
    """PC inline-material record starts (owner materials), reusing fx_pc logic."""
    base, n = pc_elembase(P, s); c = fx_pc.Cur(P, base + n * ED); out = []
    for j in range(n):
        eb = base + j * ED; et = P[eb + 184]; vc = P[eb + 185]
        vic, vsc = P[eb + 186], P[eb + 187]
        if le32(P, eb + 188) in PTRS: c.skip((vic + 1) * 96)
        if le32(P, eb + 192) in PTRS: c.skip((vsc + 1) * 48)
        vis = le32(P, eb + 196)
        if et == 11:
            if vis in PTRS:
                mb = c.o; c.skip(vc * 8)
                for i in range(vc):
                    for k in (0, 4):
                        if le32(P, mb + i * 8 + k) in PTRS:
                            out.append((j, c.o)); _, nx = MC.convert_material(P, c.o); c.o = nx
        elif vc > 1:
            if vis in PTRS:
                ab = c.o; c.skip(vc * 4)
                for i in range(vc):
                    vp = le32(P, ab + i * 4)
                    if vp in PTRS:
                        if et in STR_T: c.cstr()
                        elif et <= 6:
                            out.append((j, c.o)); _, nx = MC.convert_material(P, c.o); c.o = nx
        else:
            if vis in PTRS:
                if et in STR_T: c.cstr()
                elif et <= 6:
                    out.append((j, c.o)); _, nx = MC.convert_material(P, c.o); c.o = nx
        for off in (224, 228, 232):
            if le32(P, eb + off) in PTRS: c.cstr()
        if le32(P, eb + 252) in PTRS: c.cstr()
        if le32(P, eb + 256) in PTRS:
            if et == 5:
                tb = c.o; c.skip(28)
                if le32(P, tb + 16) in PTRS: c.skip(le32(P, tb + 12) * 20)
                if le32(P, tb + 24) in PTRS: c.skip(le32(P, tb + 20) * 2)
            elif et == 9: c.skip(12)
            else: c.skip(1)
        if le32(P, eb + 280) in PTRS: c.cstr()
    return out


# =====================================================================
# load, sim, RT
# =====================================================================
log('loading zones + measured RT ...')
CO = open(KEY, 'rb').read(); Zo = open(OURS, 'rb').read()
RT = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
CO_ARR0 = ((rc.assets_off - 64 + 7) & ~7)          # 8-aligned runtime asset-array start
NARR = len(rc.assets)
_, cspans, _ = LS.simulate(KEY, verbose=False)
cfx = [(s, e) for (i, n, r, s, e) in cspans if r == 'FxEffectDef']
log('console FX assets=%d' % len(cfx))

# console owner-material registry + elem-visuals-slot registry (both via RT)
con_owner_mat = {}            # (q, elem_j, sub) -> (console_off, exp_alias)
con_slot_val = {}             # (q, elem_j) -> console elem visuals-slot alias (RT)
slot_val_to_owner = {}        # exp elem-slot alias -> (q, elem_j)
for q, (s, e) in enumerate(cfx):
    _, base, n, mats_list, _ = FB.fx_layout(CO, s, e)
    b2, n2 = con_elembase(CO, s, e)
    for j in range(n2):
        eb = b2 + j * ED
        a = 0xA0000000 + RT.rt((eb + VIS) - RT.ae) + 1
        con_slot_val[(q, j)] = a
        slot_val_to_owner.setdefault(a, (q, j))

# rebuild owner-material list tagged with elem (for the snap-map derivation)
con_ownermat_by_elem = {}     # (q, j, sub) -> (console_off, exp_alias)
for q, (s, e) in enumerate(cfx):
    _, base, n, mats_list, _ = FB.fx_layout(CO, s, e)
    # map material starts to owning elem by re-walking with elem tags
    from fx_backref_fix import _mat_span, _be32
    b2, n2 = con_elembase(CO, s, e); c = b2 + n2 * ED
    for j in range(n2):
        eb = b2 + j * ED; et = CO[eb + 184]; vc = CO[eb + 185]
        vic, vsc = CO[eb + 186], CO[eb + 187]
        if _be32(CO, eb + 188) in PTRS: c += (vic + 1) * 96
        if _be32(CO, eb + 192) in PTRS: c += (vsc + 1) * 48
        vis = _be32(CO, eb + VIS); sub = 0
        if et == 11:
            if vis in PTRS:
                mb = c; c = mb + vc * 8
                for i in range(vc):
                    for k in (0, 4):
                        if _be32(CO, mb + i * 8 + k) in PTRS:
                            con_ownermat_by_elem[(q, j, sub)] = (c, 0xA0000000 + RT.rt(c - RT.ae) + 1); sub += 1
                            c = _mat_span(CO, c, e)
        elif vc > 1:
            if vis in PTRS:
                ab = c; c = ab + vc * 4
                for i in range(vc):
                    vp = _be32(CO, ab + i * 4)
                    if vp in PTRS:
                        if et in STR_T:
                            z = CO.find(b'\x00', c, e); c = z + 1
                        elif et <= 6:
                            con_ownermat_by_elem[(q, j, sub)] = (c, 0xA0000000 + RT.rt(c - RT.ae) + 1); sub += 1
                            c = _mat_span(CO, c, e)
        else:
            if vis in PTRS:
                if et in STR_T:
                    z = CO.find(b'\x00', c, e); c = z + 1
                elif et <= 6:
                    con_ownermat_by_elem[(q, j, sub)] = (c, 0xA0000000 + RT.rt(c - RT.ae) + 1); sub += 1
                    c = _mat_span(CO, c, e)
        for off in (224, 228, 232, 252):
            if _be32(CO, eb + off) in PTRS:
                z = CO.find(b'\x00', c, e); c = z + 1
        if _be32(CO, eb + 256) in PTRS:
            if et == 5:
                tb = c; c = tb + 28
                if _be32(CO, tb + 16) in PTRS: c += _be32(CO, tb + 12) * 20
                if _be32(CO, tb + 24) in PTRS: c += _be32(CO, tb + 20) * 2
            elif et == 9: c += 12
            else: c += 1
        if _be32(CO, eb + 280) in PTRS:
            z = CO.find(b'\x00', c, e); c = z + 1

# measured asset starts (for classifying interior targets)
import pickle
S = pickle.load(open('_skate6_simmap.pkl', 'rb')); R = pickle.load(open('_skate6_realmap.pkl', 'rb'))
astarts = sorted((rs, i, nm, root) for (i, nm, root, s, e) in S['spans']
                 for rs in [R['real'].get(s - 64)] if rs is not None)
ast_rt = [t[0] for t in astarts]

# =====================================================================
# aliaser population = OURS misaligned pointer-valued fixed@196
# =====================================================================
aliasers = []
for q, (s, e) in enumerate(cfx):
    b2, n2 = con_elembase(CO, s, e)
    for j in range(n2):
        eb = b2 + j * ED; vo = be32(Zo, eb + VIS); vk = be32(CO, eb + VIS); et = CO[eb + 184]
        if not is_al(vo) or et in STR_T: continue
        if pay(vo) % 4 == 0: continue
        aliasers.append((q, j, et, vo, vk))
NAL = len(aliasers)
log('misaligned pointer-valued fixed@196 aliasers = %d' % NAL)

# ---- ground-truth TARGET taxonomy (what the KEY value actually points at) ----
tax = Counter(); gt_slot_owner = {}
for (q, j, et, vo, vk) in aliasers:
    kp = pay(vk)
    if CO_ARR0 <= kp < CO_ARR0 + NARR * 8 and (kp - CO_ARR0) % 8 == 4:
        tax['asset-array-slot'] += 1; continue
    owner = slot_val_to_owner.get(vk)             # exact elem visuals-slot (RT)
    if owner is not None:
        gt_slot_owner[(q, j)] = owner
        tax['elem-visuals-slot(same-asset)' if owner[0] == q
            else 'elem-visuals-slot(cross-asset)'] += 1
        continue
    i = bisect.bisect_right(ast_rt, kp) - 1
    root = astarts[i][3] if i >= 0 else '<below-first-asset>'
    tax['interior:%s' % root] += 1
log('ground-truth target taxonomy: %s' % dict(tax.most_common()))

# =====================================================================
# PC side
# =====================================================================
log('deriving PC policy + simulating PC (163 MB) ...')
pol = LS.derive_pc_policy(PCPATH, verbose=False)
P = open(PCPATH, 'rb').read()
em, pspans, _ = LS.simulate_pc(P, verbose=False, policy=pol)
pc_inv = LS.InverseMap(em.omap)
pfx = [(s, e) for (i, nm, root, s, e) in pspans if root == 'FxEffectDef']
assert len(pfx) == len(cfx)
log('PC FX assets=%d' % len(pfx))

# PC owner-material registry -> ipos (sorted file offsets) + identity (q,j,sub)
pc_mat = {}                    # (q, j, sub) -> pc_file_off
for q, (s, e) in enumerate(pfx):
    seen = {}
    for (j, mo) in pc_owner_mats(P, s):
        sub = seen.get(j, 0); seen[j] = sub + 1
        pc_mat[(q, j, sub)] = mo
ipos = sorted((off, k) for k, off in pc_mat.items())
ipos_off = [t[0] for t in ipos]; ipos_own = [t[1] for t in ipos]

# PC visuals value per aliaser elem (same (q,j))
pc_vis = {}
for q, (s, e) in enumerate(pfx):
    pb, pn = pc_elembase(P, s)
    for j in range(pn):
        pc_vis[(q, j)] = le32(P, pb + j * ED + 196)


def snap(streamoff, win):
    i = bisect.bisect_left(ipos_off, streamoff); best = None
    for k in range(max(0, i - 8), min(len(ipos_off), i + 8)):
        d = abs(ipos_off[k] - streamoff)
        if d <= win and (best is None or d < best[0]):
            best = (d, ipos_own[k])
    return best


DOM = 64          # pc_inv.stream returns file-64; PC material offsets are file offsets


def derive(win):
    out = {}
    for (q, j, et, vo, vk) in aliasers:
        pv = pc_vis.get((q, j))
        if pv is None or not is_al(pv): out[(q, j)] = None; continue
        so = pc_inv.stream(pay(pv)) + DOM
        b = snap(so, win)
        if not b: out[(q, j)] = None; continue
        owner = b[1]
        da = con_ownermat_by_elem.get(owner)
        out[(q, j)] = (owner, da[1] if da else None, b[0])
    return out


# =====================================================================
# SCORE
# =====================================================================
print('\n===== TASK-SPECIFIED DERIVATION: snap PC aliaser -> nearest PC owner-material -> console =====')
print('window  resolved  owner-correct  value-correct   (of %d)' % NAL)
for win in (64, 256, 1024, 4096):
    r = derive(win); res = ow = vv = 0
    for (q, j, et, vo, vk) in aliasers:
        v = r[(q, j)]
        if not v: continue
        res += 1; owner, da, dist = v
        # owner-correct = derived owner elem == ground-truth elem-slot owner elem
        gt = gt_slot_owner.get((q, j))
        if gt is not None and owner[:2] == gt: ow += 1
        if da is not None and da == vk: vv += 1
    print('%6d  %8d  %13d  %13d' % (win, res, ow, vv))

# breakdown @4096
WIN = 4096; r = derive(WIN)
by_et = Counter(); by_et_ok = Counter()
same = {'n': 0, 'v': 0}; cross = {'n': 0, 'v': 0}
resid = Counter(); res = ow = vv = 0
for (q, j, et, vo, vk) in aliasers:
    by_et[et] += 1
    gt = gt_slot_owner.get((q, j))
    grp = same if (gt and gt[0] == q) else cross
    grp['n'] += 1
    v = r[(q, j)]
    if not v:
        pv = pc_vis.get((q, j))
        if pv is None or not is_al(pv): resid['pc-visuals-not-alias'] += 1
        else:
            so = pc_inv.stream(pay(pv)) + DOM
            if not ipos_off or so < ipos_off[0] - 5e5 or so > ipos_off[-1] + 5e5:
                resid['streamoff-outside-FX-material-range'] += 1
            else:
                resid['snap-window-miss(nearest owner-mat too far)'] += 1
        continue
    res += 1; owner, da, dist = v
    if da is not None and da == vk: vv += 1; by_et_ok[et] += 1; grp['v'] += 1
    if gt is not None and owner[:2] == gt: ow += 1

print('\n===== BREAKDOWN @ window=%d =====' % WIN)
print('resolved=%d/%d  owner-correct=%d  value-correct=%d' % (res, NAL, ow, vv))
print('by elemType (n / value-correct):')
for et in sorted(by_et):
    print('   type %-3d: %3d / %-3d %s' % (et, by_et[et], by_et_ok[et], '<-- sprite/crash path' if et == 0 else ''))
print('same-asset target: n=%d value-correct=%d' % (same['n'], same['v']))
print('cross-asset target: n=%d value-correct=%d   (HARD case)' % (cross['n'], cross['v']))
print('residue reasons: %s' % dict(resid))

# ---- PC-side localisation quality: how far does pc_inv put the alias from ANY
#      enumerable PC owner-material start? (the real blocker) ----
dists = []
for (q, j, et, vo, vk) in aliasers:
    pv = pc_vis.get((q, j))
    if pv is None or not is_al(pv): continue
    so = pc_inv.stream(pay(pv)) + DOM
    i = bisect.bisect_left(ipos_off, so); best = None
    for k in (i - 1, i, i + 1):
        if 0 <= k < len(ipos_off):
            d = abs(ipos_off[k] - so)
            if best is None or d < best: best = d
    if best is not None: dists.append(best)
print('\nPC-side localisation: dist(pc_inv landing -> nearest PC owner-material start):')
print('   min=%d median=%d p90=%d max=%d   (<=64B: %d of %d)'
      % (min(dists), statistics.median(dists), sorted(dists)[int(len(dists) * .9)],
         max(dists), sum(1 for d in dists if d <= 64), len(dists)))

# =====================================================================
# VERDICT
# =====================================================================
frac = vv / max(NAL, 1)
print('\n===== GO / NO-GO =====')
print('value-correct = %d / %d = %.1f%%' % (vv, NAL, 100 * frac))
print('owner-correct = %d / %d = %.1f%%' % (ow, NAL, 100 * ow / max(NAL, 1)))
print('VERDICT: %s' % ('GO' if frac >= 0.90 else ('PARTIAL' if frac >= 0.50 else 'NO-GO')))
