#!/usr/bin/env python3
"""XModel materialHandles fix, take 2 — PC-runtime cluster resolution.

PC handle aliases encode PC-RUNTIME slot addresses (the PC loader consumes
inline material bodies, so file-offset decoding fails). Resolution:
  * distinct PC payloads cluster into per-run windows (a run's array is 4n
    bytes; between-run gaps are KB+);
  * within a cluster, payload-minus-base = 4*(slot index) and every referenced
    slot must be a FOLLOW slot of the run -> a fingerprint;
  * cluster order is monotone in run stream order; PC drift (cluster base -
    pc array stream pos) must be monotonically non-increasing (consumption
    only removes bytes).
Assignment: anchored greedy — clusters with a unique fingerprint+order match
pin the drift curve; the rest resolve by nearest drift among order-valid
candidates.

CLOSED-LOOP VERIFY per fix (boot-28 dump): console target slot's runtime word
is a DB Material*; dereference it in the FULL dump (host BASE via the rename
anchor, §6.3 control) and compare the Material's NAME with the intent name.

--apply writes mp_skate_gfxtail24.zone/.ff after all gates.
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

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000
SRC = 'mp_skate_gfxtail23.zone'
OUT = 'mp_skate_gfxtail24'
DMP = r'C:\CemuDumps\Cemu.exe.11160.dmp'
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
RENAME_GUEST = 0x3f47a6a2

Z = open(SRC, 'rb').read()
P = open('../mp_skate_pc.zone', 'rb').read()
PC_MAT = 112

# ---------------- console runs ----------------
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


def back_run(zone, b0, is_pc=False):
    fmt = '<I' if is_pc else '>I'
    run = []
    p = b0 - 4
    while p > 0:
        w = struct.unpack_from(fmt, zone, p)[0]
        if w in (FOLLOW, INSERT) or AL(w):
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
    s = back_run(Z, b0)
    if not s:
        continue
    nf = sum(1 for _, w in s if not AL(w))
    ch = [b0]
    while len(ch) < nf and ch[-1] in nxt:
        ch.append(nxt[ch[-1]])
    runs.append((b0, s, ch))
print('console: %d runs' % len(runs))

# ---------------- PC runs (paired by body0 name; validated pattern) ----------------
def pc_body_by_name(nm):
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b = i - PC_MAT
        if b >= 0 and struct.unpack_from('<I', P, b)[0] in (FOLLOW, INSERT):
            return b


pc_arr = []                       # per run: (pc_array_start, pc_slots)
for (b0, s, ch) in runs:
    pb = pc_body_by_name(name_of[b0])
    ps = back_run(P, pb, is_pc=True) if pb is not None else None
    if ps is None or [AL(w) for _, w in ps] != [AL(w) for _, w in s]:
        pc_arr.append(None)
        continue
    pc_arr.append((ps[0][0], ps))
paired = sum(1 for x in pc_arr if x)
print('PC pairing: %d/%d' % (paired, len(runs)))

# ---------------- clusters of PC payloads ----------------
ali = []                          # (console slot pos, run idx, slot j, pc payload)
for ri, (b0, s, ch) in enumerate(runs):
    if pc_arr[ri] is None:
        continue
    ps = pc_arr[ri][1]
    for j, (sp, sw) in enumerate(s):
        if AL(sw):
            pw = ps[j][1]
            ali.append((sp, ri, j, (pw & 0x1FFFFFFF) - 1))
pays = sorted({p for _, _, _, p in ali})
clusters = []                     # (base, [offsets])
for p in pays:
    if clusters and p - clusters[-1][0] <= 4 * 64:
        clusters[-1][1].append(p - clusters[-1][0])
    else:
        clusters.append((p, [0]))
print('%d aliases, %d distinct payloads, %d clusters' % (len(ali), len(pays), len(clusters)))

# fingerprint helpers
fol_idx = []                      # per run: sorted follow-slot indices
for (b0, s, ch) in runs:
    fol_idx.append([j for j, (_, w) in enumerate(s) if not AL(w)])


def fits(ci, ri):
    """cluster ci fits run ri if every offset maps to a follow-slot index
    under SOME alignment shift (cluster base may be any slot, not slot 0)."""
    base, offs = clusters[ci]
    if pc_arr[ri] is None:
        return []
    n = len(runs[ri][1])
    F = set(fol_idx[ri])
    shifts = []
    for s0 in range(n):
        idx = [s0 + o // 4 for o in offs]
        if all(o % 4 == 0 for o in offs) and all(0 <= k < n and k in F for k in idx):
            shifts.append(s0)
    return shifts


# ---------------- assignment: order-monotone + drift-monotone ----------------
# candidates per cluster
cand = []
for ci in range(len(clusters)):
    cs = []
    for ri in range(len(runs)):
        for s0 in fits(ci, ri):
            if pc_arr[ri] is None:
                continue
            drift = clusters[ci][0] - (pc_arr[ri][0] + 4 * s0 - 64)
            cs.append((ri, s0, drift))
    cand.append(cs)
print('clusters with 1/2-5/many candidates: %s'
      % Counter(('1' if len(c) == 1 else ('2-5' if len(c) <= 5 else 'many')) for c in cand))

# dynamic program: choose per-cluster candidate s.t. run order strictly increases
# and drift is non-increasing (allow +8 jitter); maximize assigned clusters
INF = float('inf')
NC = len(clusters)
best = {}


def solve():
    # state: (cluster_i) -> list of (ri, drift, score, parent) pareto  — greedy DP
    layers = []
    for ci in range(NC):
        layer = []
        for (ri, s0, dr) in cand[ci]:
            bestprev = None
            for pi, pl in enumerate(reversed(layers)):
                real_pi = len(layers) - 1 - pi
                for k, (ri2, dr2, sc2, par2, s02) in enumerate(pl):
                    if ri2 < ri and dr2 >= dr - 8:
                        if bestprev is None or sc2 > bestprev[0]:
                            bestprev = (sc2, real_pi, k)
                if bestprev is not None and bestprev[0] == real_pi + 1:
                    break                      # cannot do better
            sc = 1 + (bestprev[0] if bestprev else 0)
            layer.append((ri, dr, sc, bestprev, s0))
        layers.append(layer)
    # backtrack best chain
    endci, endk, endsc = -1, -1, -1
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
print('DP assigned %d/%d clusters on a monotone chain' % (score, NC))

# resolve each alias -> intent material name
def owner_of(ri, j):
    b0, s, ch = runs[ri]
    k = sum(1 for (_, w) in s[:j + 1] if not AL(w)) - 1
    return name_of.get(ch[k]) if 0 <= k < len(ch) else None


pay2cluster = {}
for ci, (base, offs) in enumerate(clusters):
    for o in offs:
        pay2cluster[base + o] = (ci, o)

# stragglers (4 single-payload clusters the DP left out; resolved 2026-07-16 by
# PC slot arithmetic + confirmed-cluster exclusion + the unique-DB-holder scan;
# see session notes): payload -> intent name, or -> ('RT', slot_rt) direct.
STRAGGLER = {
    20768912: 'mlv/mtl_skt_barrier',
    21296868: 'mc/mtl_skt_speaker_monitor',   # mixing_board already claimed by a confirmed cluster
    25572672: 'mc/mtl_skt_amp_speaker',
    32486128: ('RT', 30184272),               # gfx_fxt_smk_gen_z120: FX visual ptr word,
                                              # unique b5 holder of DB Material 0x1047d218
}

intents = {}
direct_rt = {}
unres = 0
for (sp, ri, j, p) in ali:
    st = STRAGGLER.get(p)
    if st is not None:
        if isinstance(st, tuple):
            direct_rt[sp] = st[1]
        else:
            intents[sp] = st
        continue
    ci, o = pay2cluster[p]
    a = assign.get(ci)
    if a is None:
        unres += 1
        continue
    tri, s0, dr = a
    nm = owner_of(tri, s0 + o // 4)
    if nm:
        intents[sp] = nm
    else:
        unres += 1
print('intents resolved: %d / %d aliases (%d unresolved)' % (len(intents), len(ali), unres))
self_ref = sum(1 for (sp, ri, j, p) in ali
               if sp in intents and intents[sp] == name_of.get(runs[ri][0]))

# ---------------- console slot rt measurement (from take 1, re-derived) ----------------
f, ranges = MB._load_dump_ranges(DMP)
base_w, G = MB._zone_window(f, ranges, Z, int(122e6))

own = {}
for ri, (b0, s, ch) in enumerate(runs):
    fi = -1
    for j, (sp, sw) in enumerate(s):
        if not AL(sw):
            fi += 1
            if fi < len(ch):
                own.setdefault(name_of.get(ch[fi]), (ri, j))

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


# full-dump reader for the DB-name closed loop
d = Dump(DMP)
hits = d.scan(RENAME, limit=2)
BASE = hits[0] - RENAME_GUEST
assert d.read(BASE + RENAME_GUEST, 8) == b'wpc_sw4_', 'dump BASE control failed'


def guest_cstr(g, maxlen=96):
    b = d.read(BASE + g, maxlen) or b''
    i = b.find(b'\x00')
    return b[:i].decode('latin-1', 'replace') if i >= 0 else None


fixes = {}
verified = name_ok = name_bad = 0
fails = Counter()
bad_examples = []
for sp, nm in sorted(intents.items()):
    o = own.get(nm)
    if o is None:
        fails['no-owner'] += 1
        continue
    ri, j = o
    h = measure_run(ri)
    if h in (None, 'AMBIG'):
        fails['anchor-%s' % h] += 1
        continue
    n = len(runs[ri][1])
    slot_rt = h - 4 * n + 4 * j
    rv = struct.unpack_from('>I', G, slot_rt)[0]
    if not DB(rv):
        fails['target-not-DB'] += 1
        continue
    # closed loop: the DB Material's name must equal the intent
    nptr = struct.unpack_from('>I', d.read(BASE + rv, 4) or b'\x00' * 4, 0)[0]
    mname = guest_cstr(nptr) if 0x1000000 < nptr < 0x50000000 else None
    if mname == nm:
        name_ok += 1
    else:
        name_bad += 1
        if len(bad_examples) < 8:
            bad_examples.append((sp, nm, rv, mname))
    fixes[sp] = 0xA0000000 + slot_rt + 1
for sp, rt in direct_rt.items():
    rv = struct.unpack_from('>I', G, rt)[0]
    assert DB(rv), 'direct-rt straggler target not a DB ptr: 0x%08x' % rv
    fixes[sp] = 0xA0000000 + rt + 1
print('fixes: %d (incl %d direct-rt);  closed-loop DB-name check: %d OK, %d MISMATCH;  fails: %s'
      % (len(fixes), len(direct_rt), name_ok, name_bad, dict(fails)))
for sp, nm, rv, mn in bad_examples:
    print('   slot@%d intent %-36s DB 0x%08x name %r' % (sp, nm[:36], rv, mn))

if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write %s)' % OUT)
    sys.exit(0)
assert name_bad == 0, 'closed-loop name mismatches — do not apply'

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
