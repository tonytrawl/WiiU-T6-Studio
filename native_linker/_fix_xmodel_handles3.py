#!/usr/bin/env python3
"""XModel materialHandles fix, take 3 — COMPLETE coverage (boot-29 front, gfxtail25).

Boot 29 (gfxtail24) crashed on a handle slot holding 0x80000000 in BOTH boot-28
and boot-29 dumps: an alias my take-2 census never saw. Take 2 anchored on
inline material BODIES, so handle arrays with NO FOLLOW entry (models whose
every material is shared — no adjacent body) were invisible: 809 aliases was an
UNDERCOUNT (§6.6: a silently-dropped item is an unaudited item).

Take 3 enumerates EVERY materialHandles array by walking all 466 XModel bodies
with the validated walkers (wiiu_ref/xmodel_probe on console, xmodel_pc-style
on PC), paired per-model by NAME. Intent resolution and target machinery are
take 2's (cluster DP over PC-runtime payloads + first-occurrence slot targets
measured from the dump), extended with a generic fallback target: the
EARLIEST-rt block-5 holder of the intent's DB Material* in the boot dump
(valid because zone order == rt order and the first occurrence is earliest by
construction; covers FX-inline first occurrences generically).

Closed loop as before: every fix target's DB Material name == the intent name.
--apply writes mp_skate_gfxtail25.zone/.ff after gates.
"""
import bisect
import pickle
import struct
import sys
from collections import Counter

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
from _dumplib import Dump
from _nullct_oracle import scan
from _matconst_map import walk_material
import xmodel_probe as XP
import xmodel_convert as XC
import xmodel_pc as XPC
import material_convert as MC

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000
SRC = 'mp_skate_gfxtail24.zone'
OUT = 'mp_skate_gfxtail25'
DMP = r'C:\CemuDumps\Cemu.exe.32492.dmp'      # boot-29 (same layout as 28)
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
RENAME_GUEST = 0x3f47a6a2

Z = open(SRC, 'rb').read()
P = open('../mp_skate_pc.zone', 'rb').read()
S6 = pickle.load(open('_skate6_simmap.pkl', 'rb'))

# ---------------- console: every XModel's handle array ----------------
def co_handles(o):
    """console XModel body at o -> (name, [slot file offsets], [values]) or None."""
    d = Z
    nb, nrb, ns = d[o + 4], d[o + 5], d[o + 6]
    ptr = {k: XP.u32(d, o + k) for k in (0, 8, 12, 16, 20, 24, 28, 32, 36)}
    c = XP.Cur(d, o + XP.BODY)
    name = c.cstr() if ptr[0] in PTRS else None
    if ptr[8] in PTRS:
        c.skip(2 * nb)
    if ptr[12] in PTRS:
        c.skip(nb - nrb)
    if ptr[16] in PTRS:
        c.skip(8 * (nb - nrb))
    if ptr[20] in PTRS:
        c.skip(16 * (nb - nrb))
    if ptr[24] in PTRS:
        c.skip(nb)
    if ptr[28] in PTRS:
        c.skip(32 * nb)
    if ptr[32] in PTRS:
        sb = c.o
        c.skip(ns * XP.SURF)
        for i in range(ns):
            XP.parse_surface_dyn(d, sb + i * XP.SURF, c)
    if ptr[36] not in PTRS:
        return name, [], []
    base = c.o
    return name, [base + 4 * i for i in range(ns)], [XP.u32(d, base + 4 * i) for i in range(ns)]


co_models = []
fails = 0
for (i, nm, root, s, e) in sorted(S6['spans'], key=lambda t: t[3]):
    if root != 'XModel':
        continue
    try:
        name, slots, vals = co_handles(s)
        co_models.append((name, slots, vals, s))
    except Exception:
        fails += 1
tot_slots = sum(len(s) for _, s, _, _ in co_models)
tot_alias = sum(1 for _, s, v, _ in co_models for x in v if AL(x))
print('console: %d XModels walked (%d failed), %d handle slots, %d ALIAS slots'
      % (len(co_models), fails, tot_slots, tot_alias))

# ---------------- PC: same, paired by model name ----------------
def pc_xmodel_by_name(nm):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b = i - XPC.BODY
        if b >= 0 and struct.unpack_from('<I', P, b)[0] in PTRS:
            return b


def pc_handles(o, ns_want):
    d = P
    u = lambda x: struct.unpack_from('<I', d, x)[0]
    nb, nrb, ns = d[o + 4], d[o + 5], d[o + 6]
    if ns != ns_want:
        return None
    if u(o + 36) not in PTRS:
        return []
    _, cur = XC.convert_xmodel_bonedata(d, o)
    c = [cur]
    if u(o + 32) in PTRS:
        sb = c[0]
        c[0] += ns * XPC.SURF
        for i in range(ns):
            XPC._surface_dyn(d, sb + i * XPC.SURF, c)
    base = c[0]
    return [u(base + 4 * i) for i in range(ns)]


ali = []                       # (console slot file pos, pc payload)
pair_fail = pat_fail = 0
pc_off_of = {}                 # console model idx -> pc body offset (for order pairing)
unpaired = []
for mi, (name, slots, vals, s) in enumerate(co_models):
    if not slots or not any(AL(v) for v in vals):
        if name:
            pb = pc_xmodel_by_name(name)
            if pb is not None:
                pc_off_of[mi] = pb
        continue
    pb = pc_xmodel_by_name(name or '')
    pv = None
    if pb is not None:
        try:
            pv = pc_handles(pb, len(slots))
        except Exception:
            pv = None
    if pv is None:
        unpaired.append(mi)
        pair_fail += 1
        continue
    pc_off_of[mi] = pb
    if [AL(v) for v in vals] != [AL(v) for v in pv]:
        pat_fail += 1
        continue
    for sp, cv, pw in zip(slots, vals, pv):
        if AL(cv):
            ali.append((sp, (pw & 0x1FFFFFFF) - 1))
print('PC pairing: %d models unpaired, %d pattern mismatches; %d alias slots with PC intent'
      % (pair_fail, pat_fail, len(ali)))

# ---- order-window pairing for name-aliased models (name ptr not FOLLOW) ----
# console/PC preserve asset order; bracket the unpaired model between its
# nearest paired neighbors' PC offsets and fingerprint by nb/nrb/ns + the
# handle FOLLOW/alias pattern.
def pc_handle_pattern(o, ns):
    try:
        pv = pc_handles(o, ns)
    except Exception:
        return None
    return pv


recovered = 0
for mi in unpaired:
    name, slots, vals, s = co_models[mi]
    lo_pc = max((pc_off_of[k] for k in pc_off_of if k < mi), default=0)
    hi_pc = min((pc_off_of[k] for k in pc_off_of if k > mi), default=len(P) - 8)
    nb, nrb, ns = Z[s + 4], Z[s + 5], Z[s + 6]
    want = [AL(v) for v in vals]
    cands = []
    p = lo_pc
    while True:
        # PC XModel bodies: scan for +4..+6 byte triple inside the window
        p = P.find(bytes((nb, nrb, ns)), p + 1, hi_pc)
        if p < 0:
            break
        b = p - 4
        w0 = struct.unpack_from('<I', P, b)[0]
        if not (w0 in PTRS or AL(w0)):
            continue
        pv = pc_handle_pattern(b, len(slots))
        if pv is None or [AL(v) for v in pv] != want:
            continue
        cands.append((b, pv))
    if len(cands) == 1:
        b, pv = cands[0]
        for sp, cv, pw in zip(slots, vals, pv):
            if AL(cv):
                ali.append((sp, (pw & 0x1FFFFFFF) - 1))
        recovered += 1
    else:
        print('   order-pair FAILED for body@%d (candidates=%d, aliases=%d)'
              % (s, len(cands), sum(want)))
print('order-window pairing recovered %d/%d name-aliased models; %d alias slots total'
      % (recovered, len(unpaired), len(ali)))

# ---------------- material runs (take-2 machinery, for targets) ----------------
_, mats, _, _, _, _ = scan(SRC)
bodies = sorted(m['_off'] for m in mats)
bset = set(bodies)
name_of = {m['_off']: (m.get('name') or '') for m in mats}
nxt = {}
for b in bodies:
    try:
        _, e = walk_material(Z, b)
    except Exception:
        e = None
    if e in bset:
        nxt[b] = e
isn = set(nxt.values())


def back_run(b0):
    run = []
    p = b0 - 4
    while p > 0:
        w = struct.unpack_from('>I', Z, p)[0]
        if w in PTRS or AL(w):
            run.append((p, w))
            p -= 4
        else:
            break
    run.reverse()
    return run


runs = []
for b0 in bodies:
    if b0 in isn:
        continue
    s = back_run(b0)
    if not s:
        continue
    nf = sum(1 for _, w in s if not AL(w))
    ch = [b0]
    while len(ch) < nf and ch[-1] in nxt:
        ch.append(nxt[ch[-1]])
    runs.append((b0, s, ch))

own = {}
for ri, (b0, s, ch) in enumerate(runs):
    fi = -1
    for j, (sp, sw) in enumerate(s):
        if not AL(sw):
            fi += 1
            if fi < len(ch):
                own.setdefault(name_of.get(ch[fi]), (ri, j))

# PC arrays for the RUN universe (cluster fingerprint machinery)
def pc_body_by_name(nm):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b = i - 112
        if b >= 0 and struct.unpack_from('<I', P, b)[0] in PTRS:
            return b


pc_arr = []
for (b0, s, ch) in runs:
    pb = pc_body_by_name(name_of[b0])
    if pb is None:
        pc_arr.append(None)
        continue
    run = []
    p = pb - 4
    while p > 0:
        w = struct.unpack_from('<I', P, p)[0]
        if w in PTRS or AL(w):
            run.append((p, w))
            p -= 4
        else:
            break
    run.reverse()
    if [AL(w) for _, w in run] != [AL(w) for _, w in s]:
        pc_arr.append(None)
        continue
    pc_arr.append((run[0][0] if run else pb, run))

fol_idx = [[j for j, (_, w) in enumerate(s) if not AL(w)] for (b0, s, ch) in runs]

# ---------------- clusters + DP over the FULL payload set ----------------
pays = sorted({p for _, p in ali})
clusters = []
for p in pays:
    if clusters and p - clusters[-1][0] <= 4 * 64:
        clusters[-1][1].append(p - clusters[-1][0])
    else:
        clusters.append((p, [0]))
print('%d aliases, %d distinct payloads, %d clusters' % (len(ali), len(pays), len(clusters)))


def fits(ci, ri):
    base, offs = clusters[ci]
    if pc_arr[ri] is None:
        return []
    n = len(runs[ri][1])
    F = set(fol_idx[ri])
    out = []
    for s0 in range(n):
        idx = [s0 + o // 4 for o in offs]
        if all(o % 4 == 0 for o in offs) and all(0 <= k < n and k in F for k in idx):
            out.append(s0)
    return out


cand = []
for ci in range(len(clusters)):
    cs = []
    for ri in range(len(runs)):
        for s0 in fits(ci, ri):
            drift = clusters[ci][0] - (pc_arr[ri][0] + 4 * s0 - 64)
            cs.append((ri, s0, drift))
    cand.append(cs)

NC = len(clusters)


def solve():
    layers = []
    for ci in range(NC):
        layer = []
        for (ri, s0, dr) in cand[ci]:
            bestprev = None
            for pi in range(len(layers) - 1, -1, -1):
                for k, (ri2, dr2, sc2, par2, s02) in enumerate(layers[pi]):
                    if ri2 < ri and dr2 >= dr - 8:
                        if bestprev is None or sc2 > bestprev[0]:
                            bestprev = (sc2, pi, k)
                if bestprev is not None and bestprev[0] == pi + 1:
                    break
            sc = 1 + (bestprev[0] if bestprev else 0)
            layer.append((ri, dr, sc, bestprev, s0))
        layers.append(layer)
    endci = endk = -1
    endsc = -1
    for ci in range(NC):
        for k, (ri, dr, sc, par, s0) in enumerate(layers[ci]):
            if sc > endsc:
                endci, endk, endsc = ci, k, sc
    assign = {}
    ci, k = endci, endk
    while ci >= 0 and k >= 0:
        ri, dr, sc, par, s0 = layers[ci][k]
        assign[ci] = (ri, s0, dr)
        if par is None:
            break
        _, ci, k = par
    return assign, endsc


assign, score = solve()
print('DP assigned %d/%d clusters' % (score, NC))


def owner_of(ri, j):
    b0, s, ch = runs[ri]
    k = sum(1 for (_, w) in s[:j + 1] if not AL(w)) - 1
    return name_of.get(ch[k]) if 0 <= k < len(ch) else None


pay2cluster = {}
for ci, (base, offs) in enumerate(clusters):
    for o in offs:
        pay2cluster[base + o] = (ci, o)

# stragglers proven in take 2 (PC slot arithmetic + claimed-cluster exclusion)
STRAGGLER_PAYLOAD = {
    20768912: 'mlv/mtl_skt_barrier',
    21296868: 'mc/mtl_skt_speaker_monitor',
    25572672: 'mc/mtl_skt_amp_speaker',
}
# SELF-REFERENCE stragglers (2026-07-16 boot-29 session): the aliasing model's
# alias points at its OWN earlier FOLLOW slot (two surfaces sharing a material).
# Proven by PC window content + payload-offset/follow-index arithmetic:
#   german_shepherd [F,A,F]: alias -> own slot0 ('mc/mtl_german_shepherd')
#   briefcase model [5xF,5xA]: aliases -> own follows 0,2,3,4,0
# value = ordinal of the owning FOLLOW in the run CONTAINING the slot.
# NOTE: their materials use the ','-prefixed force-local name convention
# (',mc/mtl_german_shepherd') which the name-sanity scan drops -> not in the
# run universe; they are fixed by a DIRECT string-anchored pass below.
SELF_REF_SLOT = {35695261: 0,
                 36229225: 0, 36229229: 2, 36229233: 3, 36229237: 4, 36229241: 0}

intents = {}
unresolved = []
for (sp, p) in ali:
    if sp in SELF_REF_SLOT:
        continue                       # handled by the direct pass
    st = STRAGGLER_PAYLOAD.get(p)
    if st:
        intents[sp] = st
        continue
    ci, o = pay2cluster[p]
    a = assign.get(ci)
    if a is None:
        unresolved.append((sp, p))
        continue
    tri, s0, dr = a
    nm = owner_of(tri, s0 + o // 4)
    if nm:
        intents[sp] = nm
    else:
        unresolved.append((sp, p))
print('intents: %d resolved, %d unresolved (fall back to DB-holder by payload cluster)'
      % (len(intents), len(unresolved)))

# ---------------- dump machinery ----------------
f, ranges = MB._load_dump_ranges(DMP)
base_w, G = MB._zone_window(f, ranges, Z, int(122e6))
d = Dump(DMP)
hits = d.scan(RENAME, limit=2)
BASE = hits[0] - RENAME_GUEST
assert d.read(BASE + RENAME_GUEST, 8) == b'wpc_sw4_', 'dump BASE control failed'


def guest_cstr(g, maxlen=96):
    b = d.read(BASE + g, maxlen) or b''
    i = b.find(b'\x00')
    return b[:i].decode('latin-1', 'replace') if i >= 0 else None


run_rt = {}


def measure_run(ri):
    if ri in run_rt:
        return run_rt[ri]
    b0, s, ch = runs[ri]
    key = name_of[b0].encode('latin-1') + b'\x00'
    n = len(s)
    got = None
    i = -1
    while True:
        i = G.find(key, i + 1)
        if i < 0:
            break
        ok = bad = 0
        for j, (sp, sw) in enumerate(s):
            rv = struct.unpack_from('>I', G, i - 4 * n + 4 * j)[0]
            if not AL(sw):
                if DB(rv):
                    ok += 1
                else:
                    bad += 1
        if bad == 0 and ok > 0:
            got = 'AMBIG' if got is not None else i
            if got == 'AMBIG':
                break
    run_rt[ri] = got
    return got


# DB Material index: name -> M (guest ptr of the registered material struct)
_db_cache = {}


def db_material(nm):
    if nm in _db_cache:
        return _db_cache[nm]
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
                # material name ptr must be at +0 of a Material struct; accept
                # candidates and disambiguate by b5 holders below
                if M is None:
                    M = gb + j
                elif gb + j != M:
                    M = 'AMBIG'
        if M == 'AMBIG':
            break
    _db_cache[nm] = M
    return M


def earliest_holder(M):
    key = struct.pack('>I', M)
    j = G.find(key)
    return j if j >= 0 else None


fixes = {}
name_ok = name_bad = 0
fcls = Counter()
bad_ex = []
for sp, nm in sorted(intents.items()):
    o = own.get(nm)
    slot_rt = None
    if o is not None:
        ri, j = o
        h = measure_run(ri)
        if h not in (None, 'AMBIG'):
            n = len(runs[ri][1])
            slot_rt = h - 4 * n + 4 * j
    if slot_rt is None:
        M = db_material(nm)
        if M is None or M == 'AMBIG':
            fcls['dbmat-%s' % M] += 1
            continue
        slot_rt = earliest_holder(M)
        if slot_rt is None:
            fcls['no-holder'] += 1
            continue
    rv = struct.unpack_from('>I', G, slot_rt)[0]
    if not DB(rv):
        fcls['target-not-DB'] += 1
        continue
    nptr = struct.unpack_from('>I', d.read(BASE + rv, 4) or b'\x00' * 4, 0)[0]
    mname = guest_cstr(nptr) if 0x1000000 < nptr < 0x50000000 else None
    if mname == nm:
        name_ok += 1
    else:
        name_bad += 1
        if len(bad_ex) < 8:
            bad_ex.append((sp, nm, rv, mname))
        continue
    fixes[sp] = 0xA0000000 + slot_rt + 1

# ---- direct pass for the SELF-REF slots (comma-prefixed local materials) ----
def cstr_at(z, o, maxlen=96):
    e = z.index(b'\x00', o, o + maxlen)
    return z[o:e]


sr_ok = 0
for sp, ordinal in sorted(SELF_REF_SLOT.items()):
    mdl = next(((nm, sl, vv) for (nm, sl, vv, s) in co_models
                if sl and sl[0] <= sp <= sl[-1]), None)
    assert mdl is not None, 'self-ref slot %d not in any model' % sp
    _, sl, vv = mdl
    n = len(sl)
    fidx = [j for j, v in enumerate(vv) if not AL(v)]
    j = fidx[ordinal]
    b0 = sl[-1] + 4
    name0 = cstr_at(Z, b0 + 104)                   # includes the ',' prefix
    # intent = the ordinal-th FOLLOW body's name (walk the chain of inline bodies)
    bb = b0
    for _ in range(ordinal):
        _, bb = walk_material(Z, bb)
    intent_name = cstr_at(Z, bb + 104)
    key = name0 + b'\x00'
    h = None
    i = -1
    while True:
        i = G.find(key, i + 1)
        if i < 0:
            break
        okc = badc = 0
        for k in range(n):
            rv = struct.unpack_from('>I', G, i - 4 * n + 4 * k)[0]
            if not AL(vv[k]):
                if DB(rv):
                    okc += 1
                else:
                    badc += 1
        if badc == 0 and okc > 0:
            assert h is None, 'self-ref anchor AMBIG for %r' % name0
            h = i
    assert h is not None, 'self-ref anchor not found for %r' % name0
    slot_rt = h - 4 * n + 4 * j
    rv = struct.unpack_from('>I', G, slot_rt)[0]
    assert DB(rv), 'self-ref target not DB: 0x%08x' % rv
    nptr = struct.unpack_from('>I', d.read(BASE + rv, 4) or b'\x00' * 4, 0)[0]
    mname = (guest_cstr(nptr) or '').encode('latin-1')
    assert mname in (intent_name, intent_name.lstrip(b',')), \
        'self-ref DB name %r != intent %r' % (mname, intent_name)
    fixes[sp] = 0xA0000000 + slot_rt + 1
    sr_ok += 1
print('self-ref direct pass: %d/%d fixed (DB-name verified)' % (sr_ok, len(SELF_REF_SLOT)))

# unresolved clusters -> DB-holder route needs a NAME; recover it by reading the
# earliest holder's DB material name directly from ANY confirmed neighbor? Not
# possible without intent — report them instead.
print('fixes: %d;  closed-loop name check: %d OK, %d MISMATCH;  fail classes: %s'
      % (len(fixes), name_ok, name_bad, dict(fcls)))
for sp, nm, rv, mn in bad_ex:
    print('   slot@%d intent %-36s DB 0x%08x name %r' % (sp, nm[:36], rv, mn))
print('unresolved aliases (no cluster assignment): %d' % len(unresolved))
for sp, p in unresolved[:10]:
    print('   slot@%d payload %d' % (sp, p))

# how many previously-fixed (take-2) slots does take 3 confirm identically?
Z23 = open('mp_skate_gfxtail23.zone', 'rb').read()
same = diff_t2 = new_fix = 0
t2_mismatch = []
for sp, na in fixes.items():
    old = struct.unpack_from('>I', Z, sp)[0]
    was_t2 = Z23[sp:sp + 4] != Z[sp:sp + 4]        # take 2 rewrote this slot
    if old == na:
        same += 1
    elif was_t2:
        diff_t2 += 1
        if len(t2_mismatch) < 8:
            t2_mismatch.append((sp, old, na, intents.get(sp)))
    else:
        new_fix += 1
print('vs gfxtail24: %d identical, %d NEW fixes (untouched by take 2), %d take-2 DISAGREEMENTS'
      % (same, new_fix, diff_t2))
for sp, old, na, nm in t2_mismatch:
    print('   t2-disagree slot@%d old=0x%08x new=0x%08x intent=%s' % (sp, old, na, nm))

if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write %s)' % OUT)
    sys.exit(0)
assert name_bad == 0

Zn = bytearray(Z)
for sp, na in fixes.items():
    struct.pack_into('>I', Zn, sp, na)
import alloc_events
import clipmap_console
end, _ = alloc_events.clipmap_events(bytes(Zn), 84512493, '>',
                                     mat_span=clipmap_console._mat_span)
print('clipMap gate: end=%d %s' % (end, 'OK' if end == 89584099 else '*** FAIL ***'))
assert end == 89584099
import hashlib
import wiiu_ff
open(OUT + '.zone', 'wb').write(bytes(Zn))
ff = wiiu_ff.pack(bytes(Zn), 'mp_skate')
open(OUT + '.ff', 'wb').write(ff)
print('wrote %s.zone (md5 %s)' % (OUT, hashlib.md5(bytes(Zn)).hexdigest()))
print('wrote %s.ff   (md5 %s, %d bytes)' % (OUT, hashlib.md5(ff).hexdigest(), len(ff)))
