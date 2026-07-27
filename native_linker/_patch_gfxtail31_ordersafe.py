#!/usr/bin/env python3
"""gfxtail31: ORDER-SAFE re-derivation of ALL demand remaps (types 0/2/6).

Boot-37 discovery (dump 28036, water techset args): the engine's per-arg hash
lookups are a SORTED MERGE-JOIN — arg hashes ascend within a pass's same-type
run, the material tables (constantTable stride 32, texdefs stride 16) are
sorted, and ONE forward cursor serves the whole run (a match leaves the cursor
ON the entry; no restart, no wrap). Oracle-proven: raid = 0 order violations in
10,969 adjacent pairs; skate gfxtail13 (pre-remap) = 0; gfxtail30 = 1,294 t2 +
36 t0 violations — ALL introduced by gfxtail29/30 remap targets. Boot 37 fault
= water techset arg run 19cc0727, [0b198186 <- remapped], ... : cursor already
past table[0] can never find 0b198186 again.

Fix rule, per pass, per contiguous same-type run, args in stream order:
    orig  = the arg's gfxtail13 (pristine) value
    keep orig if orig ∈ carried_all(name-group binders)
    else  -> previous EFFECTIVE demand of the run (duplicate = instant match)
             or min(carried_all) when the run has no previous demand
Applied to EVERY member body of each bound name-group (pool-winner agnostic).
Unbound groups: args restored to pristine. Types {0,6} use constants,
{2} uses texdef hashes. Size-neutral value writes only.

Usage: python _patch_gfxtail31_ordersafe.py [--apply]
"""
import hashlib
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
from _matconst_map import be16, be32, walk_techset, FOLLOW, PTRS
from _nullct_oracle import scan
from _sampler_oracle import mat_texhashes

SRC = 'mp_skate_gfxtail30.zone'
OLD = 'mp_skate_gfxtail13.zone'          # pristine arg values (order-check: 0 violations)
DST = 'mp_skate_gfxtail31.zone'
FF = 'mp_skate_gfxtail31.ff'
BB = 84512493
TAIL_TS = {804: 84317304, 815: 89654859, 817: 89678237, 819: 89678868}
AL = lambda v: 0xA0000000 <= v < 0xC0000000

Z = bytearray(open(SRC, 'rb').read())
orig_zone = bytes(Z)
OZ = open(OLD, 'rb').read()
assert len(OZ) == len(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, d))
    return d


assert gate(Z, 'in') == 0

_, mats, _d6, ts_spans, ts_name, ts_idx = scan(SRC)
ts_spans = dict(ts_spans)
for k, s in TAIL_TS.items():
    ts_spans[k] = (s, None)

# carried sets per material
carried_c = {mm['_off']: set(mm['consts']) for mm in mats}
carried_t = {}
for mm in mats:
    hs, kind = mat_texhashes(orig_zone, mm['_off'])
    carried_t[mm['_off']] = set(hs or [])

# name groups + binders (zone binding == runtime binding, ','-strip aside: b37 census)
name_of = {i: ts_name(s) for i, (s, e) in ts_spans.items()}
groups = defaultdict(list)
for i, nm in name_of.items():
    if nm:
        groups[nm].append(i)
binders = defaultdict(list)
unbound_mats = 0
for mm in mats:
    if not AL(mm['ts']):
        unbound_mats += 1
        continue
    k = ts_idx(mm['ts'])
    if k is None or k not in ts_spans:
        unbound_mats += 1
        continue
    binders[name_of.get(k) or ('#idx%d' % k)].append(mm['_off'])
print('materials: %d (%d with no walked binding)  groups with binders: %d'
      % (len(mats), unbound_mats, len(binders)))

# group carried intersections
gc, gt = {}, {}
for nm, offs in binders.items():
    gc[nm] = set.intersection(*[carried_c[o] for o in offs])
    gt[nm] = set.intersection(*[carried_t[o] for o in offs])

# ---- assignment pass ----
stats = Counter()
edits = {}
order_fail = []
for i, (s, e) in ts_spans.items():
    nm = name_of.get(i)
    bound = nm in binders
    try:
        passes, _ = walk_techset(orig_zone, s)
    except Exception:
        stats['walkfail'] += 1
        continue
    for p in passes:
        base = p['args_off']
        prev_t = None
        prev_eff = None
        for j in range(p['nargs']):
            a = base + j * 8
            t = be16(orig_zone, a)
            cur = be32(orig_zone, a + 4)
            old = be32(OZ, a + 4)
            if cur in PTRS or old in PTRS:
                prev_t = None
                continue
            if t not in (0, 2, 6):
                prev_t = None
                continue
            if prev_t != t:
                prev_t, prev_eff = t, None
            if not bound:
                new = old                     # pristine for unbound groups
                stats['unbound-restore' if new != cur else 'unbound-same'] += 1
            else:
                car = gt[nm] if t == 2 else gc[nm]
                if old in car:
                    new = old
                    stats['keep-orig' if new == cur else 'restore-orig'] += 1
                elif prev_eff is not None:
                    new = prev_eff
                    stats['dup-prev'] += 1
                elif car:
                    new = min(car)
                    stats['min-carried'] += 1
                else:
                    new = old                  # no carried at all: leave pristine
                    stats['NO-CARRIED'] += 1
            if prev_eff is not None and new < prev_eff:
                order_fail.append((i, j, t, prev_eff, new))
            prev_eff = new
            if new != cur:
                edits[a + 4] = new

print('assignment: %s' % dict(stats))
print('edits: %d arg slots' % len(edits))
print('order failures in assignment: %d %s' % (len(order_fail), order_fail[:5]))
assert not order_fail
assert stats.get('NO-CARRIED', 0) == 0, 'a bound group carries nothing of a demanded kind'

if '--apply' not in sys.argv:
    print('\nDRY RUN — no bytes written')
    sys.exit(0)

for a, v in edits.items():
    struct.pack_into('>I', Z, a, v)
assert len(Z) == len(orig_zone)
assert gate(Z, 'out') == 0

# ---- verify: order invariant + satisfaction, on the PATCHED zone ----
runs = Counter()
viol = Counter()
unsat = 0
for i, (s, e) in ts_spans.items():
    nm = name_of.get(i)
    try:
        passes, _ = walk_techset(bytes(Z), s)
    except Exception:
        continue
    for p in passes:
        base = p['args_off']
        prev_t = None
        prev_v = None
        for j in range(p['nargs']):
            a = base + j * 8
            t, v = be16(Z, a), be32(Z, a + 4)
            if v in PTRS or t not in (0, 2, 6):
                prev_t = None
                continue
            if prev_t == t:
                runs[t] += 1
                if v < prev_v:
                    viol[t] += 1
            prev_t, prev_v = t, v
            if nm in binders:
                car = gt[nm] if t == 2 else gc[nm]
                if v not in car:
                    unsat += 1
print('verify: adjacent pairs %s  ORDER VIOLATIONS %s  unsatisfied-arg-values %d'
      % (dict(runs), dict(viol) or 'NONE', unsat))
assert not viol and unsat == 0

changed = sum(1 for i in range(len(Z)) if Z[i] != orig_zone[i])
print('bytes changed vs gfxtail30: %d' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
