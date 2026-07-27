#!/usr/bin/env python3
"""gfxtail37: fix the boot-44 techset-binding OFF-BY-ONE class (spawn fatal +
"slightly off" visuals).

Boot 44 (gfxtail36): map loads, spectate works; class-select spawn hits guest
Com_Error "Vertex type 6 doesn't have the information used by shader
pimp_shader_layer_lmap_d74f58.hlsl in material wpc/ao_decal_ramp" -> OSFatal
(Cemu unimplemented -> hang). Dump ground truth: the material's techSet* alias
(0xA00058C5, carried from PC where it encodes asset 779 = wpc_unlit_blend)
binds CONSOLE runtime asset entry 779 = wpc_shadowcaster_wj6w5j60 -- the console
asset array (842 entries vs PC 840) is shifted +1 here by console-only asset
insertions. The shadowcaster's slot-3 technique is layer_lmap -> vertex type 6
lacks lmap streams -> fatal. Neighbors are all lit_sm_* variants, so most
off-by-one bindings render subtly wrong instead of crashing = the reported
malformed visuals.

Fix: for EVERY console material with an ALIAS techSet* into the asset array:
  intent  = PC techset NAME bound by the same-named PC material
  actual  = runtime DB techset name at G[payload] (boot-44 dump = oracle)
  if actual != intent: repoint alias to enc(k') where runtime entry k' has the
  intent name (enc(k) = 0xA0000000 + (our_arr + 4 + k*8) + 1).
Size-neutral value writes, clipMap-gated. Also censuses ALL other aliases
landing in the asset-array region and reports how many are off (other families).
"""
import hashlib
import pickle
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import measure_band as MB
import wiiu_zone
from _dumplib import Dump
from _matconst_map import FOLLOW, PTRS, be32, walk_material

SRC = 'mp_skate_gfxtail36.zone'
DST = 'mp_skate_gfxtail37.zone'
FF = 'mp_skate_gfxtail37.ff'
PC = '../mp_skate_pc.zone'
DMP = r'C:\CemuDumps\Cemu.hang.boot44.dmp'
BB = 84512493
RENAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9\x00'
AL = lambda v: 0xA0000000 <= v < 0xC0000000
DB = lambda v: 0x10000000 <= v < 0x50000000

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)
P = open(PC, 'rb').read()

rc = wiiu_zone.ZoneReader(bytes(Z)); rc.read_string_table(); rc.read_asset_list()
arr = rc.assets_off - 64
our_arr = (arr + 7) & ~7
NASSETS = len(rc.assets)
REG_LO, REG_HI = our_arr + 4, our_arr + 4 + NASSETS * 8
print('asset array: our_arr=%d entries=%d region=[%d,%d)' % (our_arr, NASSETS, REG_LO, REG_HI))

# ---------------- dump: runtime asset-entry names ---------------------------
d = Dump(DMP)
BASE = d.scan(RENAME, limit=2)[0] - 0x3f47a6a2
assert d.read(BASE + 0x3f47a6a2, 8) == b'wpc_sw4_'
f, ranges = MB._load_dump_ranges(DMP)
base_w, G = MB._zone_window(f, ranges, orig, int(122e6))


def u32g(g):
    b = d.read(BASE + g, 4)
    return struct.unpack('>I', b)[0] if b else None


def cstr(g, n=120):
    b = d.read(BASE + g, n) or b''
    i = b.find(b'\x00')
    return b[:i].decode('latin1', 'replace') if i >= 0 else None


entry_ptr = {}    # k -> runtime DB ptr
entry_name = {}   # k -> runtime asset name
name2k = defaultdict(list)   # name -> [k] (type-8 only, for repoint targets)
for k in range(NASSETS):
    off = our_arr + 4 + k * 8
    v = struct.unpack_from('>I', G, off)[0]
    entry_ptr[k] = v
    nm = None
    if DB(v):
        nm = cstr(u32g(v))
    entry_name[k] = nm
    if nm and rc.assets[k][0] == 8:
        name2k[nm].append(k)
n_named = sum(1 for k in entry_name if entry_name[k])
print('runtime entries with readable names: %d/%d ; distinct type-8 names: %d'
      % (n_named, NASSETS, len(name2k)))
dups = {n: ks for n, ks in name2k.items() if len(ks) > 1}
if dups:
    print('duplicate type-8 names: %d (first few: %s)' % (len(dups), list(dups)[:4]))


def enc(k):
    return 0xA0000000 + (our_arr + 4 + k * 8) + 1


def dec_k(alias):
    pay = (alias - 1) & 0x1FFFFFFF
    if not (REG_LO <= pay < REG_HI) or (pay - REG_LO) % 8:
        return None
    return (pay - REG_LO) // 8


# ---------------- console materials: name -> techSet alias ------------------
mats = []          # (body_off, name, ts_alias)
last = -1
for m in re.finditer(re.escape(b'\xff\xff\xff\xff'), bytes(Z)):
    o = m.start()
    if o < last or o + 104 > len(Z):
        continue
    if be32(Z, o) != FOLLOW:
        continue
    texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
    ts = be32(Z, o + 80)
    ptrish = lambda v: v == 0 or v in PTRS or AL(v)
    if not (texc <= 64 and constc <= 64 and sbc <= 64
            and all(ptrish(be32(Z, o + x)) for x in (80, 84, 88, 92, 96))):
        continue
    try:
        info, nxt = walk_material(bytes(Z), o)
    except Exception:
        continue
    nm = info['name']
    if not nm or not (1 <= len(nm) <= 200) or not all(
            32 <= c < 127 for c in nm.encode('latin1', 'replace')):
        continue
    mats.append((o, nm, ts))
    last = o + 104
print('console materials walked: %d' % len(mats))

# ---------------- PC: material name -> intent techset name ------------------
pc_spans = pickle.load(open('_b44_pc_spans.pkl', 'rb'))
pc_ts_name = {}    # pc asset k -> techset name (type TECHNIQUE_SET)
for (i, nm, s, e) in pc_spans:
    if nm == 'TECHNIQUE_SET':
        en = P.find(b'\x00', s + 152)
        pc_ts_name[i] = P[s + 152:en].decode('latin1', 'replace')
print('PC techsets named: %d' % len(pc_ts_name))

import pc_zone
prc = pc_zone.PCZoneReader(P); prc.read_string_table(); prc.read_asset_list()
pc_arr = prc.assets_off - 64
pc_our_arr = (pc_arr + 7) & ~7


def pc_dec_k(alias):
    pay = (alias - 1) & 0x1FFFFFFF
    lo = pc_our_arr + 4
    hi = lo + len(prc.assets) * 8
    if not (lo <= pay < hi) or (pay - lo) % 8:
        return None
    return (pay - lo) // 8


def pc_mat_ts(nm):
    """PC material (112B) with this name -> its techSet* value @+92."""
    key = nm.encode('latin-1') + b'\x00'
    i = -1
    while True:
        i = P.find(key, i + 1)
        if i < 0:
            return None
        b = i - 112
        if b >= 0 and struct.unpack_from('<I', P, b)[0] == FOLLOW:
            return struct.unpack_from('<I', P, b + 92)[0]


# ---------------- audit + fixes ---------------------------------------------
st = Counter()
fixes = {}
rows = []
for (o, nm, ts) in mats:
    if ts == FOLLOW or ts in PTRS:
        st['inline-techset'] += 1
        continue
    if not AL(ts):
        st['ts-not-alias'] += 1
        continue
    k = dec_k(ts)
    if k is None:
        st['alias-not-array'] += 1
        continue
    actual = entry_name.get(k)
    pv = pc_mat_ts(nm)
    if pv is None:
        st['no-pc-material'] += 1
        continue
    if not AL(pv):
        st['pc-ts-not-alias'] += 1
        continue
    pk = pc_dec_k(pv)
    intent = pc_ts_name.get(pk) if pk is not None else None
    if intent is None:
        st['no-pc-intent'] += 1
        continue
    intent = intent.lstrip(',')          # DB strips the force-local ',' prefix
    if actual == intent:
        st['correct'] += 1
        continue
    ks = name2k.get(intent)
    if not ks:
        # documented fallback: the v-layer-stripped sibling (v layers were
        # deliberately not ported; a v layer only adds alphaRevealP/colorTint2)
        sib = re.sub(r'v\d?(?=(_|$))', '', intent)
        ks = name2k.get(sib) if sib != intent else None
        if ks:
            st['fixed-via-vstrip'] += 1
        else:
            st['intent-not-in-console'] += 1
            rows.append((nm, k, actual, intent, None))
            continue
    k2 = ks[0]
    if len(ks) > 1:
        # name-dup: safe only if all same-named entries resolved to ONE DB body
        ptrs = {entry_ptr[x] for x in ks}
        if len(ptrs) > 1:
            st['dup-name-divergent-DB'] += 1
            rows.append((nm, k, actual, intent, ('DUP!', ks)))
            continue
    fixes[o + 80] = enc(k2)
    st['fixed'] += 1
    rows.append((nm, k, actual, intent, k2))

print('\naudit: %s' % dict(st))
print('\nmismatches (material, boundk, actual, intent, newk):')
for (nm, k, actual, intent, k2) in rows[:40]:
    print('  %-44s k=%-4d %-38s -> %-38s k2=%s'
          % (nm[:44], k, (actual or '?')[:38], (intent or '?')[:38], k2))
if len(rows) > 40:
    print('  ... %d more' % (len(rows) - 40))

# sanity: the crash material must be in the fix set
crash_fixed = any(nm == 'wpc/ao_decal_ramp' for (nm, k, a, i2, k2) in rows
                  if isinstance(k2, int))
print('\ncrash material wpc/ao_decal_ramp fixed: %s' % crash_fixed)
assert crash_fixed, 'the audit must catch the crash material'

# delta histogram: pure carried-alias drift shows as small uniform +N shifts
deltas = Counter(k2 - k for (nm, k, a, i2, k2) in rows if isinstance(k2, int))
print('fix delta histogram (k2-k):', dict(deltas.most_common(12)))

# ---------------- shift structure: console runtime vs PC entry names --------
pc_entry_name = {}   # PC k -> asset name (techsets only is enough for structure)
for i, n in pc_ts_name.items():
    pc_entry_name[i] = n.lstrip(',')
shiftpts = []
for k in sorted(pc_entry_name):
    n = pc_entry_name[k]
    # find console runtime ks with this name
    cks = name2k.get(n)
    if not cks:
        continue
    dmin = min(ck - k for ck in cks)
    shiftpts.append((k, dmin))
from itertools import groupby
runs = []
for dd, g in groupby(shiftpts, key=lambda t: t[1]):
    gl = list(g)
    runs.append((dd, gl[0][0], gl[-1][0]))
print('\nPC->console techset index shift runs (shift, pc_k_lo, pc_k_hi):')
for r in runs[:20]:
    print('   shift %+d over pc_k %d..%d' % r)

# ---------------- census: OTHER aliases landing in the asset array ----------
mat_slots = {o + 80 for (o, nm, ts) in mats}
Zb = bytes(Z)
n_arr_alias = 0
other_by_k = Counter()
other_slots = []
for m in re.finditer(re.escape(b'\xa0'), Zb):
    o = m.start()
    if o + 4 > len(Zb):
        continue
    v = struct.unpack_from('>I', Zb, o)[0]
    if not AL(v):
        continue
    k = dec_k(v)
    if k is None:
        continue
    n_arr_alias += 1
    if o in mat_slots:
        continue
    other_by_k[k] += 1
    other_slots.append((o, k))
print('\nasset-array aliases total: %d ; outside material techSet slots: %d'
      % (n_arr_alias, sum(other_by_k.values())))
print('non-material alias hits by entry (top 12):')
for k, c in other_by_k.most_common(12):
    print('   k=%-4d x%-5d type=%-2d %s' % (k, c, rc.assets[k][0], (entry_name.get(k) or '?')[:44]))
# how many hit entries at/beyond the first shifted techset index?
first_shift_k = min((k for (k, dd) in shiftpts if dd != 0), default=None)
if first_shift_k is not None:
    n_sus = sum(c for k, c in other_by_k.items() if k >= first_shift_k)
    print('first shifted pc_k=%s ; non-material aliases at k>=that: %d' % (first_shift_k, n_sus))

# ---------------- apply, gate, pack ----------------------------------------
import alloc_events as AE
import clipmap_console as CC


def gate(buf):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    return m.start() - end, end


d0, e0 = gate(Z)
print('\nGATE in: delta=%+d end=%d' % (d0, e0))
assert d0 == 0
for so, nv in fixes.items():
    struct.pack_into('>I', Z, so, nv)
d1, e1 = gate(Z)
print('GATE out: delta=%+d end=%d (want 89584099)' % (d1, e1))
assert d1 == 0 and e1 == 89584099
assert len(Z) == len(orig)
words = sum(1 for so in fixes
            if struct.unpack_from('>I', Z, so)[0] != struct.unpack_from('>I', orig, so)[0])
print('words changed: %d (== %d fixes)' % (words, len(fixes)))
assert words == len(fixes)

if '--apply' not in sys.argv:
    print('\nDRY RUN (pass --apply to write)')
    sys.exit(0)

open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
