#!/usr/bin/env python3
"""gfxtail39: FULL demand gate over the techset re-binds (boot-46 load crash).

Boot 46 (gfxtail38) crashed in the same load window with a hash-search walk
(r9=0x50000000, cmp esi=0x34ECCCB3): gfxtail38's gate only checked TYPE-6 (pixel
const) demands. Types 0 (vertex const) and 2 (sampler) hash-search the material's
constant table / texdef table the same way (boot-35/36 crash classes).

This pass re-decides every one of the 689 re-bound slots (diff gfxtail36 vs 37)
from scratch. Candidates in order:
    1. gfxtail37 value  (name-intent bind)
    2. v-stripped sibling of the intent name
    3. gfxtail36 value  (boot-44-proven old binding — always accepted)
A candidate passes only if its techset's RUNTIME demands (boot-44 dump; walk must
be COMPLETE, partial = fail) satisfy:
    const demands (arg types 0 and 6)  ⊆  material consts (walk_material)
    sampler demands (arg type 2)       ⊆  material texdef hashes
      (texdef table read zone-side when inline; via the b5 window when aliased)
Output = gfxtail39 (built from gfxtail37 base + the chosen values).
"""
import hashlib
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import numpy as np
import measure_band as MB
import wiiu_zone
import shader_probe as SP
import xmodel_probe as XP
from _dumplib import Dump
from _matconst_map import walk_material, be32, PTRS

Z36 = open('mp_skate_gfxtail36.zone', 'rb').read()
Z37 = open('mp_skate_gfxtail37.zone', 'rb').read()
OUT = bytearray(Z37)
DMP44 = r'C:\CemuDumps\Cemu.hang.boot44.dmp'
DMP46 = r'C:\CemuDumps\Cemu.exe.9332.dmp'
BB = 84512493
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x50000000
CONST_TYPES = (0, 6)
SAMPLER_TYPE = 2

# ---------------- the 689 slots (diff 36 vs 37) -----------------------------
a36 = np.frombuffer(Z36, dtype=np.uint8)
a37 = np.frombuffer(Z37, dtype=np.uint8)
db = list(map(int, np.nonzero(a36 != a37)[0]))
slots = []
i = 0
while i < len(db):
    o = db[i]
    found = None
    for st in range(max(0, o - 3), o + 1):
        if AL(struct.unpack_from('>I', Z36, st)[0]) and \
           AL(struct.unpack_from('>I', Z37, st)[0]):
            found = st
            break
    assert found is not None, o
    slots.append(found)
    while i < len(db) and db[i] < found + 4:
        i += 1
print('re-bound slots: %d' % len(slots))
assert len(slots) == 689

rc = wiiu_zone.ZoneReader(Z37); rc.read_string_table(); rc.read_asset_list()
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


# ---------------- boot-44 dump machinery ------------------------------------
d = Dump(DMP44)
BASE = d.scan(RENAME, limit=2)[0] - 0x3f47a6a2
assert d.read(BASE + 0x3f47a6a2, 8) == b'wpc_sw4_'
f, ranges = MB._load_dump_ranges(DMP44)
base_w, G = MB._zone_window(f, ranges, Z36, int(122e6))


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
for k in range(len(rc.assets)):
    v = struct.unpack_from('>I', G, our_arr + 4 + k * 8)[0]
    entry_ptr[k] = v
    nm = cstr(u32g(v)) if DB(v) else None
    entry_name[k] = nm
    if nm and rc.assets[k][0] == 8:
        name2k[nm].append(k)

_dem = {}


def runtime_demand(k):
    """(const_hashes, sampler_hashes, complete) from runtime techset at entry k."""
    if k in _dem:
        return _dem[k]
    ts = entry_ptr.get(k)
    ch, sh = set(), set()
    complete = True
    if ts is None or not DB(ts):
        _dem[k] = (None, None, False)
        return _dem[k]
    for j in range(32):
        t = u32g(ts + 8 + j * 4)
        if not t:
            continue
        if not (0x10000000 <= t < 0x50000000):
            complete = False
            continue
        pc = u16g(t + 6) or 0
        if not (1 <= pc <= 8):
            complete = False
            continue
        for i in range(pc):
            po = t + 8 + i * 24
            cb = d.read(BASE + po + 12, 3)
            if not cb:
                complete = False
                continue
            nargs = cb[0] + cb[1] + cb[2]
            if not nargs:
                continue
            ap = u32g(po + 20)
            if not ap or not (0x10000000 <= ap < 0x50000000):
                complete = False
                continue
            blob = d.read(BASE + ap, nargs * 8)
            if not blob or len(blob) < nargs * 8:
                complete = False
                continue
            for x in range(nargs):
                at = struct.unpack_from('>H', blob, x * 8)[0]
                v = struct.unpack_from('>I', blob, x * 8 + 4)[0]
                if at in CONST_TYPES:
                    ch.add(v)
                elif at == SAMPLER_TYPE:
                    sh.add(v)
    _dem[k] = (ch, sh, complete)
    return _dem[k]


def mat_texhashes_any(Zb, off):
    """carried texdef hashes; resolves ALIASED tables via the b5 window."""
    b = off
    texc = Zb[b + 72]
    if texc == 0:
        return []
    tsp, ttp = be32(Zb, b + 80), be32(Zb, b + 84)
    if ttp in PTRS:
        c = XP.Cur(Zb, b + 104)
        if be32(Zb, b) in PTRS:
            c.cstr()
        if tsp in PTRS:
            c.o, _ = SP.parse_techset(Zb, c.o)
        defs = c.o
        return [be32(Zb, defs + i * 16) for i in range(texc)]
    if AL(ttp):
        pay = (ttp - 1) & 0x1FFFFFFF
        if pay + texc * 16 <= len(G):
            return [struct.unpack_from('>I', G, pay + i * 16)[0] for i in range(texc)]
    return None    # unreadable


# ---------------- decide every slot -----------------------------------------
st = Counter()
downgrades = []
for so in slots:
    old = struct.unpack_from('>I', Z36, so)[0]
    new = struct.unpack_from('>I', Z37, so)[0]
    body = so - 80
    try:
        info, _ = walk_material(Z37, body)
        carried_c = set(info['consts'])
        nm = info['name']
    except Exception:
        OUT[so:so + 4] = struct.pack('>I', old)
        st['walk-fail-revert'] += 1
        downgrades.append(('?', so, 'WALK-FAIL', 'revert'))
        continue
    tex = mat_texhashes_any(Z37, body)
    carried_t = set(tex) if tex is not None else None

    def passes(k):
        ch, sh, complete = runtime_demand(k)
        if ch is None or not complete:
            return False, 'unreadable'
        if ch - carried_c:
            return False, 'const-miss %s' % sorted('0x%08x' % h for h in (ch - carried_c))[:3]
        if sh:
            if carried_t is None:
                return False, 'tex-unreadable'
            if sh - carried_t:
                return False, 'tex-miss %s' % sorted('0x%08x' % h for h in (sh - carried_t))[:3]
        return True, 'ok'

    k_new = dec_k(new)
    ok, why = passes(k_new)
    if ok:
        st['keep-intent'] += 1
        continue
    intent = entry_name.get(k_new) or ''
    sib = re.sub(r'v\d?(?=(_|$))', '', intent)
    if sib != intent and name2k.get(sib):
        k3 = name2k[sib][0]
        ok3, why3 = passes(k3)
        if ok3:
            OUT[so:so + 4] = struct.pack('>I', enc(k3))
            st['vstrip'] += 1
            downgrades.append((nm, so, '%s [%s]' % (intent, why), 'sib k=%d' % k3))
            continue
    OUT[so:so + 4] = struct.pack('>I', old)
    st['revert'] += 1
    downgrades.append((nm, so, '%s [%s]' % (intent, why), 'revert k=%s' % dec_k(old)))

print('\nfull-gate results: %s' % dict(st))
print('\ndowngrades:')
for (nm, so, why, action) in downgrades:
    print('  %-44s slot=%-9d %-70s -> %s' % ((nm or '?')[:44], so, why[:70], action))

# ---------------- cross-check the boot-46 victim ----------------------------
try:
    d46 = Dump(DMP46)
    B46 = d46.scan(RENAME, limit=2)[0] - 0x3f47a6a2
    vb = d46.read(B46 + 0x1049E8B8, 4)
    if vb:
        np_ = struct.unpack('>I', vb)[0]
        nb = d46.read(B46 + np_, 96) or b''
        i = nb.find(b'\x00')
        print('\nboot-46 victim (rdx=0x1049E8B8): %r' % (nb[:i].decode('latin1', 'replace') if i >= 0 else None))
except Exception as ex:
    print('victim read failed: %s' % ex)

# ---------------- gate + write ----------------------------------------------
import alloc_events as AE
import clipmap_console as CC
m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                        + b'maps/mp/mp_skate.gsc'), bytes(OUT))
end, _ = AE.clipmap_events(bytes(OUT), BB, '>', mat_span=CC._mat_span)
print('\nGATE: delta=%+d end=%d (want 89584099)' % (m.start() - end, end))
assert m.start() - end == 0 and end == 89584099
diffs = sum(1 for so in slots if OUT[so:so + 4] != Z37[so:so + 4])
print('slots changed vs gfxtail37: %d' % diffs)

if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write)')
    sys.exit(0)

open('mp_skate_gfxtail39.zone', 'wb').write(bytes(OUT))
print('mp_skate_gfxtail39.zone md5 %s' % hashlib.md5(bytes(OUT)).hexdigest())
import wiiu_ff
ff = wiiu_ff.pack(bytes(OUT), 'mp_skate')
open('mp_skate_gfxtail39.ff', 'wb').write(ff)
print('mp_skate_gfxtail39.ff md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
