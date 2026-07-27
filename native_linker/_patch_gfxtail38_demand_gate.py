#!/usr/bin/env python3
"""gfxtail38: demand-gate the gfxtail37 techset re-binds (boot-45 load crash).

Boot 45 (gfxtail37) crashed ~2.1s after mp_skate.ff opened: the boot-23-class
unbounded constant walk (rbp=0x50000000, r8=0x7793A24B = colorTint2 — the
v-layer demand). At least one of the 689 name-anchored re-binds bound a material
to a techset demanding constants the material does not carry (invariant #2:
demand ⊆ carried). Boot 44 loaded with the OLD (off-by-one) bindings, so every
old value is load-proven.

Fix: for each of the 689 changed slots (diff gfxtail36 vs 37):
  carried = walk_material(zone)['consts']
  demand  = type-6 arg hashes of the NEW техset's RUNTIME body (boot-44 dump —
            authoritative even when the name pool substitutes a patch_mp body)
  if demand ⊆ carried: keep the re-bind
  else: try the v-stripped sibling (runtime entry with the v-stripped name,
        demand-checked the same way); else REVERT to the old value.
Size-neutral, clipMap-gated.
"""
import hashlib
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_zone
from _dumplib import Dump
from _matconst_map import walk_material

Z36 = open('mp_skate_gfxtail36.zone', 'rb').read()
Z37 = bytearray(open('mp_skate_gfxtail37.zone', 'rb').read())
orig37 = bytes(Z37)
DMP44 = r'C:\CemuDumps\Cemu.hang.boot44.dmp'
BB = 84512493
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x50000000
ARG_CONST_HASH = 6

# ---------------- changed slots = the 689 re-binds --------------------------
assert len(Z36) == len(Z37)
import numpy as np
a36 = np.frombuffer(Z36, dtype=np.uint8)
a37 = np.frombuffer(orig37, dtype=np.uint8)
diffb = np.nonzero(a36 != a37)[0]
slots = sorted({int(b) & ~3 for b in diffb})
# each changed word must be a 4-aligned... aliases are not 4-aligned in stream;
# group differing bytes into words by the slot list from values instead:
slots = []
i = 0
db = list(map(int, diffb))
while i < len(db):
    o = db[i]
    # find the word start: try o-3..o such that Z36 word is alias and Z37 word is alias
    found = None
    for st in range(max(0, o - 3), o + 1):
        v36 = struct.unpack_from('>I', Z36, st)[0]
        v37 = struct.unpack_from('>I', orig37, st)[0]
        if AL(v36) and AL(v37) and v36 != v37:
            found = st
            break
    assert found is not None, ('cannot align diff byte', o)
    slots.append(found)
    while i < len(db) and db[i] < found + 4:
        i += 1
print('changed alias slots: %d' % len(slots))
assert len(slots) == 689, len(slots)

rc = wiiu_zone.ZoneReader(bytes(Z37)); rc.read_string_table(); rc.read_asset_list()
our_arr = ((rc.assets_off - 64) + 7) & ~7
REG_LO = our_arr + 4


def dec_k(alias):
    pay = (alias - 1) & 0x1FFFFFFF
    if (pay - REG_LO) % 8:
        return None
    k = (pay - REG_LO) // 8
    return k if 0 <= k < len(rc.assets) else None


def enc(k):
    return 0xA0000000 + (our_arr + 4 + k * 8) + 1


# ---------------- boot-44 dump: runtime entries + demand walker -------------
d = Dump(DMP44)
BASE = d.scan(RENAME, limit=2)[0] - 0x3f47a6a2
assert d.read(BASE + 0x3f47a6a2, 8) == b'wpc_sw4_'


def u32g(g):
    b = d.read(BASE + g, 4)
    return struct.unpack('>I', b)[0] if b else None


def u16g(g):
    b = d.read(BASE + g, 2)
    return struct.unpack('>H', b)[0] if b else None


def cstr(g, n=120):
    b = d.read(BASE + g, n) or b''
    i = b.find(b'\x00')
    return b[:i].decode('latin1', 'replace') if i >= 0 else None


entry_ptr = {}
entry_name = {}
name2k = defaultdict(list)
import measure_band as MB
f, ranges = MB._load_dump_ranges(DMP44)
base_w, G = MB._zone_window(f, ranges, Z36, int(122e6))
for k in range(len(rc.assets)):
    v = struct.unpack_from('>I', G, our_arr + 4 + k * 8)[0]
    entry_ptr[k] = v
    nm = cstr(u32g(v)) if DB(v) else None
    entry_name[k] = nm
    if nm and rc.assets[k][0] == 8:
        name2k[nm].append(k)

_dem = {}


def runtime_demand(k):
    """type-6 const hashes demanded by runtime techset at entry k (boot-44 dump)."""
    if k in _dem:
        return _dem[k]
    ts = entry_ptr[k]
    hs = set()
    ok = True
    if not DB(ts):
        _dem[k] = (None, 'entry-not-DB')
        return _dem[k]
    for j in range(32):
        t = u32g(ts + 8 + j * 4)
        if not t:
            continue
        if not (DB(t) or 0x30000000 <= t < 0x50000000):
            ok = False
            continue
        pc = u16g(t + 6) or 0
        if not (1 <= pc <= 8):
            ok = False
            continue
        for i in range(pc):
            po = t + 8 + i * 24
            nargs = sum(d.read(BASE + po + 12, 3) or b'\x00\x00\x00')
            ap = u32g(po + 20)
            if not nargs:
                continue
            if not ap or not (0x10000000 <= ap < 0x50000000):
                ok = False
                continue
            blob = d.read(BASE + ap, nargs * 8)
            if not blob or len(blob) < nargs * 8:
                ok = False
                continue
            for x in range(nargs):
                at = struct.unpack_from('>H', blob, x * 8)[0]
                if at == ARG_CONST_HASH:
                    hs.add(struct.unpack_from('>I', blob, x * 8 + 4)[0])
    _dem[k] = (hs, 'ok' if ok else 'partial')
    return _dem[k]


# ---------------- per-slot gate ---------------------------------------------
import re
st = Counter()
changes = {}     # slot -> final value (only where != current Z37 value)
report = []
for so in slots:
    old = struct.unpack_from('>I', Z36, so)[0]
    new = struct.unpack_from('>I', orig37, so)[0]
    body = so - 80
    try:
        info, _ = walk_material(bytes(Z37), body)
        carried = set(info['consts'])
        nm = info['name']
    except Exception as ex:
        st['walk-fail-revert'] += 1
        changes[so] = old
        report.append(('?', so, 'WALK-FAIL', None, 'revert'))
        continue
    k_new = dec_k(new)
    dem, q = runtime_demand(k_new)
    if dem is None:
        st['demand-unreadable-revert'] += 1
        changes[so] = old
        report.append((nm, so, entry_name.get(k_new), None, 'revert(unreadable)'))
        continue
    miss = dem - carried
    if not miss:
        st['keep'] += 1
        continue
    # violation: try v-stripped sibling of the intent name
    intent = entry_name.get(k_new) or ''
    sib = re.sub(r'v\d?(?=(_|$))', '', intent)
    fixed = False
    if sib != intent and name2k.get(sib):
        k3 = name2k[sib][0]
        dem3, q3 = runtime_demand(k3)
        if dem3 is not None and not (dem3 - carried):
            changes[so] = enc(k3)
            st['vstrip-rebind'] += 1
            report.append((nm, so, intent, sorted('0x%08x' % h for h in miss),
                           'sib=%s k=%d' % (sib, k3)))
            fixed = True
    if not fixed:
        changes[so] = old
        st['revert'] += 1
        report.append((nm, so, intent, sorted('0x%08x' % h for h in miss),
                       'revert(old k=%s)' % dec_k(old)))

print('\ngate results: %s' % dict(st))
print('\nviolations detail:')
for (nm, so, intent, miss, action) in report:
    print('  %-44s slot=%-9d bound=%-40s miss=%s -> %s'
          % ((nm or '?')[:44], so, (intent or '?')[:40], miss, action))

# ---------------- apply to gfxtail37 -> gfxtail38 ---------------------------
for so, v in changes.items():
    struct.pack_into('>I', Z37, so, v)

import alloc_events as AE
import clipmap_console as CC


def gate(buf):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    return m.start() - end, end


d1, e1 = gate(Z37)
print('\nGATE: delta=%+d end=%d (want 89584099)' % (d1, e1))
assert d1 == 0 and e1 == 89584099
words = sum(1 for so in changes
            if struct.unpack_from('>I', Z37, so)[0] != struct.unpack_from('>I', orig37, so)[0])
print('words changed vs gfxtail37: %d (== %d gated)' % (words, len(changes)))

if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write)')
    sys.exit(0)

open('mp_skate_gfxtail38.zone', 'wb').write(bytes(Z37))
print('mp_skate_gfxtail38.zone md5 %s' % hashlib.md5(bytes(Z37)).hexdigest())
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z37), 'mp_skate')
open('mp_skate_gfxtail38.ff', 'wb').write(ff)
print('mp_skate_gfxtail38.ff md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
