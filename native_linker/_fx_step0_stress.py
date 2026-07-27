"""STEP 0 stress test (throwaway) -- audits _fx_step0_derive.py.

Reuses the SAME setup as _fx_step0_derive.py, then adds:
  (A) TARGET-KIND structural check: are ANY of the 126 key values reachable as a
      console owner-material alias value? (derivation output space membership)
  (B) REMOVE THE SNAP: window -> infinity (always nearest PC owner-material). If
      value/owner-correct stay 0, the window is filtering nothing useful.
  (C) window sweep incl. large windows -> overfitting smell.
  (D) cross-asset specific: is owner-correct EVER nonzero for cross-asset?
  (E) elemType-0 confirmation.
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

OURS = 'mp_skate_final.zone'; KEY = 'mp_skate_gfxtail46.zone'; PCPATH = '../mp_skate_pc.zone'
PTRS = (0xFFFFFFFF, 0xFFFFFFFE); ED, VIS, HDR = 292, 196, 76; STR_T = (10, 12)
be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
le32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
be16s = lambda d, o: struct.unpack_from('>h', d, o)[0]
le16s = lambda d, o: struct.unpack_from('<h', d, o)[0]
is_al = lambda v: 0xA0000000 <= v < 0xC0000000
pay = lambda v: (v - 1) & 0x1FFFFFFF
T0 = time.time(); log = lambda *a: print('[%5.1fs]' % (time.time() - T0), *a)


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


log('loading zones + RT ...')
CO = open(KEY, 'rb').read(); Zo = open(OURS, 'rb').read()
RT = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
CO_ARR0 = ((rc.assets_off - 64 + 7) & ~7); NARR = len(rc.assets)
_, cspans, _ = LS.simulate(KEY, verbose=False)
cfx = [(s, e) for (i, n, r, s, e) in cspans if r == 'FxEffectDef']

# console elem-visuals-slot registry
con_slot_val = {}; slot_val_to_owner = {}
for q, (s, e) in enumerate(cfx):
    b2, n2 = con_elembase(CO, s, e)
    for j in range(n2):
        eb = b2 + j * ED
        a = 0xA0000000 + RT.rt((eb + VIS) - RT.ae) + 1
        con_slot_val[(q, j)] = a; slot_val_to_owner.setdefault(a, (q, j))

# console owner-material registry (derivation output space)
from fx_backref_fix import _mat_span, _be32
con_ownermat_by_elem = {}
for q, (s, e) in enumerate(cfx):
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

# aliaser population
aliasers = []
for q, (s, e) in enumerate(cfx):
    b2, n2 = con_elembase(CO, s, e)
    for j in range(n2):
        eb = b2 + j * ED; vo = be32(Zo, eb + VIS); vk = be32(CO, eb + VIS); et = CO[eb + 184]
        if not is_al(vo) or et in STR_T: continue
        if pay(vo) % 4 == 0: continue
        aliasers.append((q, j, et, vo, vk))
NAL = len(aliasers)
log('aliasers = %d' % NAL)

# ground-truth elem-slot owner map
gt_slot_owner = {}
for (q, j, et, vo, vk) in aliasers:
    owner = slot_val_to_owner.get(vk)
    if owner is not None: gt_slot_owner[(q, j)] = owner

# ============ (A) TARGET-KIND structural membership check ============
out_space = set(a for (_, a) in con_ownermat_by_elem.values())
key_vals = set(vk for (_, _, _, _, vk) in aliasers)
in_space = sum(1 for (_, _, _, _, vk) in aliasers if vk in out_space)
print('\n===== (A) OUTPUT-SPACE MEMBERSHIP =====')
print('distinct console owner-material alias values (derivation can emit): %d' % len(out_space))
print('of 126 KEY target values, how many equal ANY console owner-mat alias: %d' % in_space)
print(' -> if 0, value-correct is STRUCTURALLY impossible (target-kind mismatch)')
# also: how many key vals equal an elem-visuals-SLOT alias?
slot_space = set(con_slot_val.values())
in_slot = sum(1 for (_, _, _, _, vk) in aliasers if vk in slot_space)
print('of 126 KEY target values, how many equal an elem-visuals-SLOT alias: %d' % in_slot)

# ============ PC side ============
log('PC sim ...')
pol = LS.derive_pc_policy(PCPATH, verbose=False); P = open(PCPATH, 'rb').read()
em, pspans, _ = LS.simulate_pc(P, verbose=False, policy=pol)
pc_inv = LS.InverseMap(em.omap)
pfx = [(s, e) for (i, nm, root, s, e) in pspans if root == 'FxEffectDef']
pc_mat = {}
for q, (s, e) in enumerate(pfx):
    seen = {}
    for (j, mo) in pc_owner_mats(P, s):
        sub = seen.get(j, 0); seen[j] = sub + 1
        pc_mat[(q, j, sub)] = mo
ipos = sorted((off, k) for k, off in pc_mat.items())
ipos_off = [t[0] for t in ipos]; ipos_own = [t[1] for t in ipos]
pc_vis = {}
for q, (s, e) in enumerate(pfx):
    pb, pn = pc_elembase(P, s)
    for j in range(pn):
        pc_vis[(q, j)] = le32(P, pb + j * ED + 196)
DOM = 64


def snap(streamoff, win):
    i = bisect.bisect_left(ipos_off, streamoff); best = None
    for k in range(max(0, i - 8), min(len(ipos_off), i + 8)):
        d = abs(ipos_off[k] - streamoff)
        if d <= win and (best is None or d < best[0]):
            best = (d, ipos_own[k])
    return best


def snap_unbounded(streamoff):
    """Nearest PC owner-material start, NO window (remove-the-snap-filter)."""
    i = bisect.bisect_left(ipos_off, streamoff); best = None
    for k in range(max(0, i - 2), min(len(ipos_off), i + 2)):
        d = abs(ipos_off[k] - streamoff)
        if best is None or d < best[0]: best = (d, ipos_own[k])
    return best


def score(win, unbounded=False):
    res = ow = vv = ow_cross = ow_same = 0
    for (q, j, et, vo, vk) in aliasers:
        pv = pc_vis.get((q, j))
        if pv is None or not is_al(pv): continue
        so = pc_inv.stream(pay(pv)) + DOM
        b = snap_unbounded(so) if unbounded else snap(so, win)
        if not b: continue
        res += 1; owner = b[1]; da = con_ownermat_by_elem.get(owner)
        gt = gt_slot_owner.get((q, j))
        if gt is not None and owner[:2] == gt:
            ow += 1
            if gt[0] == q: ow_same += 1
            else: ow_cross += 1
        if da is not None and da[1] == vk: vv += 1
    return res, ow, vv, ow_cross, ow_same


print('\n===== (B/C) WINDOW SWEEP incl. UNBOUNDED (remove-the-snap) =====')
print('window     resolved  owner-correct  value-correct  owc-cross  owc-same')
for win in (64, 256, 1024, 4096, 16384, 65536, 1 << 20):
    r, ow, vv, oc, os_ = score(win)
    print('%9d  %8d  %13d  %13d  %9d  %8d' % (win, r, ow, vv, oc, os_))
r, ow, vv, oc, os_ = score(0, unbounded=True)
print('UNBOUNDED  %8d  %13d  %13d  %9d  %8d' % (r, ow, vv, oc, os_))

# ============ (D) cross-asset: could owner even be right? ============
# For each aliaser with a gt elem-slot owner, is that gt owner elem ALSO a PC
# owner-material we enumerate? (necessary for owner-correct to be reachable)
gt_reachable = 0; gt_cross = 0; gt_same = 0
pc_owner_elems = set((q, j) for (q, j, sub) in pc_mat)
for (q, j, et, vo, vk) in aliasers:
    gt = gt_slot_owner.get((q, j))
    if gt is None: continue
    if gt[0] == q: gt_same += 1
    else: gt_cross += 1
    if gt in pc_owner_elems: gt_reachable += 1
print('\n===== (D) GROUND-TRUTH OWNER REACHABILITY =====')
print('aliasers whose key target IS an elem-visuals-slot: %d (same=%d cross=%d)'
      % (len(gt_slot_owner), gt_same, gt_cross))
print('...of those, gt owner elem is ALSO an enumerable PC owner-material: %d' % gt_reachable)
print(' -> elem-slot targets are dedup-CHAIN pointer slots, not owner mats; a')
print('    snap-to-owner-material cannot name them even with a perfect PC map')

# ============ (E) elemType-0 confirmation across windows ============
print('\n===== (E) elemType-0 (sprite/crash path) value-correct across windows =====')
et0 = [(q, j, vo, vk) for (q, j, et, vo, vk) in aliasers if et == 0]
print('type-0 aliasers = %d' % len(et0))
for win in (4096, 65536, 1 << 20):
    v0 = 0
    for (q, j, vo, vk) in et0:
        pv = pc_vis.get((q, j))
        if pv is None or not is_al(pv): continue
        so = pc_inv.stream(pay(pv)) + DOM
        b = snap(so, win)
        if not b: continue
        da = con_ownermat_by_elem.get(b[1])
        if da is not None and da[1] == vk: v0 += 1
    print('   win %8d: type-0 value-correct = %d / %d' % (win, v0, len(et0)))
