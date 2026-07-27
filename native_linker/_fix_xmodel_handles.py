#!/usr/bin/env python3
"""Fix the XModel materialHandles dedup-alias family (boot-28 front, gfxtail24).

MECHANISM (dump-proven): the loader DEREFERENCES a handle alias and stores the
fetched word into the slot (boot-28: slot held 0x00c1a6ee == the word AT the
alias target). A working alias must therefore point at a location that holds a
real Material* at link time. Genuine convention: the FIRST-OCCURRENCE handle
slot (a FOLLOW slot the loader rewrote to the registered Material* when that
earlier XModel linked). 807/809 of our aliases point into bulk data instead.
No Material asset-table rows exist (all 1,214 materials are inline), so the
enc(k) row route is unavailable.

FIX: for each alias slot, recover the INTENDED material from the PC zone
(same run structure, PC aliases decode by fileoff=((v&0x1FFFFFFF)-1)+64),
then point the console alias at the first-occurrence slot's RUNTIME address,
measured from the boot-28 dump via the run's leading name-string anchor
(runtime layout: [h0..hn-1][body0 name string] — bodies are consumed to DB
allocations, the array and the first name string stay adjacent in b5).

VERIFY (offline, pre-boot): the dump word at every NEW payload must be a DB
Material* (0x10-0x12 region) — the exact value the loader will copy.
"""
import pickle
import struct
import sys

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
from _nullct_oracle import scan
from _matconst_map import walk_material

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x13000000
SRC = 'mp_skate_gfxtail23.zone'
OUT = 'mp_skate_gfxtail24'
DMP = r'C:\CemuDumps\Cemu.exe.11160.dmp'

Z = open(SRC, 'rb').read()
P = open('../mp_skate_pc.zone', 'rb').read()

# ---------------- A. console bodies + runs ----------------
_, mats, _, _, _, _ = scan(SRC)
bodies = sorted(m['_off'] for m in mats)
bset = set(bodies)
name_of = {}
for m in mats:
    name_of[m['_off']] = m.get('name') or ''

# body chain: walk end -> next body
nxt = {}
for b in bodies:
    try:
        info, end = walk_material(Z, b)
    except Exception:
        end = None
    if end in bset:
        nxt[b] = end

is_next = set(nxt.values())


def back_run(zone, b0, is_pc=False):
    """handle words immediately before b0, in ARRAY ORDER (h0 first)."""
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


runs = []                        # (body0, slots[(pos,val)], bodies[list])
for b0 in bodies:
    if b0 in is_next:
        continue
    slots = back_run(Z, b0)
    if not slots:
        continue
    nf = sum(1 for _, w in slots if w in (FOLLOW, INSERT))
    chain = [b0]
    while len(chain) < nf and chain[-1] in nxt:
        chain.append(nxt[chain[-1]])
    runs.append((b0, slots, chain))
n_alias = sum(1 for _, s, _ in runs for _, w in s if AL(w))
n_follow = sum(1 for _, s, _ in runs for _, w in s if not AL(w))
bad_runs = [(b0, len(s), len(ch)) for b0, s, ch in runs
            if sum(1 for _, w in s if not AL(w)) != len(ch)]
print('console: %d runs, %d follow slots, %d alias slots; %d runs with follow/body mismatch'
      % (len(runs), n_follow, n_alias, len(bad_runs)))

# ---------------- B. PC bodies + runs, paired by body0 name ----------------
PC_MAT = 112


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


pairs = []                       # (console run idx, pc_slots)
pat_mismatch = 0
pc_slot_map = {}                 # pc slot pos -> (run_idx, j)
for ri, (b0, slots, chain) in enumerate(runs):
    pb = pc_body_by_name(name_of.get(b0, ''))
    if pb is None:
        pairs.append(None)
        continue
    ps = back_run(P, pb, is_pc=True)
    pat_c = [AL(w) for _, w in slots]
    pat_p = [AL(w) for _, w in ps]
    if pat_c != pat_p:
        pat_mismatch += 1
        pairs.append(None)
        continue
    pairs.append(ps)
    for j, (pp, pw) in enumerate(ps):
        pc_slot_map[pp] = (ri, j)
print('PC pairing: %d/%d runs paired, %d pattern mismatches'
      % (sum(1 for x in pairs if x), len(runs), pat_mismatch))

# ---------------- C. PC convention check ----------------
pc_bodyset = set()
for ri, ps in enumerate(pairs):
    if ps:
        pc_bodyset.add(pc_body_by_name(name_of[runs[ri][0]]))
hit_slot = hit_other = 0
for ps in pairs:
    if not ps:
        continue
    for pp, pw in ps:
        if AL(pw):
            tf = ((pw & 0x1FFFFFFF) - 1) + 64
            if tf in pc_slot_map:
                hit_slot += 1
            else:
                hit_other += 1
print('PC aliases decode to: known slot=%d  other=%d' % (hit_slot, hit_other))

# ---------------- D. intent per console alias ----------------
def owner_of(ri, j):
    """material name owned by FOLLOW slot j of run ri (k-th follow -> k-th body)."""
    b0, slots, chain = runs[ri]
    k = sum(1 for (_, w) in slots[:j + 1] if not AL(w)) - 1
    if k < 0 or k >= len(chain):
        return None
    return name_of.get(chain[k])


def resolve_pc(ri, j, depth=0):
    """intent name for console alias slot (ri, j) via the PC chain."""
    ps = pairs[ri]
    if not ps or depth > 4:
        return None
    pp, pw = ps[j]
    if not AL(pw):
        return owner_of(ri, j)
    tf = ((pw & 0x1FFFFFFF) - 1) + 64
    tgt = pc_slot_map.get(tf)
    if tgt is None:
        return None
    ri2, j2 = tgt
    ps2 = pairs[ri2]
    if AL(ps2[j2][1]):
        return resolve_pc(ri2, j2, depth + 1)
    return owner_of(ri2, j2)


intents = {}                     # console slot pos -> intended material name
no_intent = []
for ri, (b0, slots, chain) in enumerate(runs):
    for j, (sp, sw) in enumerate(slots):
        if AL(sw):
            nm = resolve_pc(ri, j)
            if nm:
                intents[sp] = nm
            else:
                no_intent.append((ri, j, sp))
print('intent recovered for %d/%d alias slots (%d unresolved)'
      % (len(intents), n_alias, len(no_intent)))

# ---------------- E. dump: measure first-occurrence slot runtimes ----------------
f, ranges = MB._load_dump_ranges(DMP)
base, G = MB._zone_window(f, ranges, Z, int(122e6))

# where is each material's OWNING slot? (its run + follow index)
own = {}                         # name -> (ri, j)
for ri, (b0, slots, chain) in enumerate(runs):
    fidx = -1
    for j, (sp, sw) in enumerate(slots):
        if not AL(sw):
            fidx += 1
            if fidx < len(chain):
                own.setdefault(name_of.get(chain[fidx]), (ri, j))

run_string_rt = {}               # ri -> rt of body0 name string


def measure_run(ri):
    if ri in run_string_rt:
        return run_string_rt[ri]
    b0, slots, chain = runs[ri]
    key = name_of[b0].encode('latin-1') + b'\x00'
    n = len(slots)
    best = None
    i = -1
    while True:
        i = G.find(key, i + 1)
        if i < 0:
            break
        # validate: every zone-FOLLOW slot of this run must hold a DB ptr at runtime
        okc = badc = 0
        for j, (sp, sw) in enumerate(slots):
            rv = struct.unpack_from('>I', G, i - 4 * n + 4 * j)[0]
            if not AL(sw):
                if DB(rv):
                    okc += 1
                else:
                    badc += 1
        if badc == 0 and okc > 0:
            if best is not None:
                best = 'AMBIG'
                break
            best = i
    run_string_rt[ri] = best
    return best


fixes = {}                       # slot file pos -> new alias
meas_fail = []
for sp, nm in sorted(intents.items()):
    o = own.get(nm)
    if o is None:
        meas_fail.append((sp, nm, 'no-owner'))
        continue
    ri, j = o
    h = measure_run(ri)
    if h is None or h == 'AMBIG':
        meas_fail.append((sp, nm, 'anchor-%s' % h))
        continue
    n = len(runs[ri][1])
    slot_rt = h - 4 * n + 4 * j
    rv = struct.unpack_from('>I', G, slot_rt)[0]
    if not DB(rv):
        meas_fail.append((sp, nm, 'target-not-DB 0x%08x' % rv))
        continue
    fixes[sp] = 0xA0000000 + slot_rt + 1
print('fixes computed: %d;  failures: %d' % (len(fixes), len(meas_fail)))
from collections import Counter
print('failure classes:', Counter(k for _, _, k in meas_fail).most_common(8))
for sp, nm, why in meas_fail[:10]:
    print('   slot@%d intent %-40s %s' % (sp, nm[:40], why))

# ---------------- F. rewrite + verify + pack ----------------
if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write %s)' % OUT)
    sys.exit(0)

Zn = bytearray(Z)
for sp, na in fixes.items():
    struct.pack_into('>I', Zn, sp, na)

ver_ok = ver_bad = 0
for sp, na in fixes.items():
    rv = struct.unpack_from('>I', G, (na - 1) & 0x1FFFFFFF)[0]
    if DB(rv):
        ver_ok += 1
    else:
        ver_bad += 1
print('VERIFY vs boot-28 dump: %d/%d new payloads hold a DB Material*' % (ver_ok, len(fixes)))
assert ver_bad == 0

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
