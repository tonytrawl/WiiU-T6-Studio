#!/usr/bin/env python3
"""gfxtail22: fix boot 25 — 3 materials whose techSet* points at a GfxLightDef (OFF-BY-ONE).

BOOT 25 (gfxtail21) — the walk class IS closed (fault is no longer 0x50000000). New fault:
guest 0x77, R8 = 0x73, in `R_DrawSurfStandardPrepassSortKey` (0x02988b04), reached from
`Material_Compare+0x40` (which calls the pixel-consts comparator FIRST and this one SECOND ⇒
we are advancing WITHIN the same sort, not regressing):

    0x02988b14  lwz  r11, 0x50(r3)   ; r11 = material->techniqueSet
    0x02988b18  lwz  r12, 8(r11)     ; r12 = techset->slots[0]
    0x02988b1c  cmpwi r12, 0
    0x02988b20  bne  0x2988b34
    0x02988b34  lhz  r31, 4(r12)     ; <== FAULT: r12 = 0x73 (garbage, non-NULL) -> read 0x77

ROOT CAUSE — a THIRD oracle-proven invariant, which no audit checked:
    *** every material's techSet* must point at an asset of TYPE 8 (MaterialTechniqueSet) ***
    raid 926/926 OK.  skate: 3 materials point at asset 665 = TYPE 19 = **GfxLightDef**.
The engine reads [GfxLightDef+8] as a technique slot -> 0x73 -> derefs it -> AV.

It is an OFF-BY-ONE of exactly one asset entry (8 bytes):
    asset 664 type=19 GfxLightDef
    asset 665 type=19 GfxLightDef          <- the 3 materials point HERE (alias 0xa0005535)
    asset 666 type=8  wpc_lit_sm_t0c0_2w3887e6   <- the intended target (enc = 0xa000553d)

ORACLE CONFIRMS THE TARGET: raid binds `wpc/core_fence_chain_link` (texc=2, constc=0) to
`wpc_lit_sm_t0c0_2w3887e6` — the very name of skate's asset 666, which demands NOTHING and whose
existing user `wpc/vista_city_block_housing` is also texc=2/constc=0, matching all 3 violators.

Size-neutral: techSet* (+80) is a block-5 alias = a VALUE, consumes NO stream bytes.
"""
import hashlib
import re
import struct
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
import wiiu_ff
import wiiu_zone
from _matconst_map import be32
from _nullct_oracle import scan

SRC = 'mp_skate_gfxtail21.zone'
DST = 'mp_skate_gfxtail22.zone'
FF = 'mp_skate_gfxtail22.ff'
BB = 84512493
GATE_END = 89584099
BAD_ASSET = 665                     # GfxLightDef
GOOD_ASSET = 666                    # wpc_lit_sm_t0c0_2w3887e6
GOOD_NAME = 'wpc_lit_sm_t0c0_2w3887e6'
EXPECT = {'wpc/core_fence_chain_link', 'wpc/crossbeam_detail_b', 'wpc/vista_buildings_set'}

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    print('  GATE[%s] clipmap end=%d delta=%+d' % (tag, end, m.start() - end))
    assert end == GATE_END, 'clipMap gate moved'
    return m.start() - end


assert gate(Z, 'in') == 0

Zb, mats, dem, spans, ts_name, ts_idx = scan(SRC)
rc = wiiu_zone.ZoneReader(bytes(Z))
rc.read_string_table()
rc.read_asset_list()
our_arr = ((rc.assets_off - 64) + 7) & ~7
enc = lambda k: (0xA0000000 | (our_arr + 4 + k * 8)) + 1

assert rc.assets[BAD_ASSET][0] == 19, 'asset %d is not type 19' % BAD_ASSET
assert rc.assets[GOOD_ASSET][0] == 8, 'asset %d is not type 8' % GOOD_ASSET
assert ts_name(spans[GOOD_ASSET][0]) == GOOD_NAME, 'asset %d is not %s' % (GOOD_ASSET, GOOD_NAME)
assert not dem.get(GOOD_ASSET), 'target demands constants'
assert enc(GOOD_ASSET) - enc(BAD_ASSET) == 8, 'not an off-by-one'
print('bad  asset %d type=19 GfxLightDef  alias 0x%08x' % (BAD_ASSET, enc(BAD_ASSET)))
print('good asset %d type=8  %-26s alias 0x%08x  demands NOTHING'
      % (GOOD_ASSET, GOOD_NAME, enc(GOOD_ASSET)))

# --- every material whose techSet* points at a NON-type-8 asset ---
viol = []
for m in mats:
    k = ts_idx(m['ts'])
    if k is not None and rc.assets[k][0] != 8:
        viol.append((m, k))
print('\nmaterials with a non-techset techSet*: %d' % len(viol))
assert len(viol) == 3, 'expected 3, got %d' % len(viol)
assert {m['name'] for m, k in viol} == EXPECT, 'unexpected violator set: %s' % {m['name'] for m, k in viol}
assert all(k == BAD_ASSET for m, k in viol), 'not all pointing at asset %d' % BAD_ASSET
assert all(m['texc'] == 2 and m['constc'] == 0 for m, k in viol), \
    'profile differs from the oracle (raid: texc=2, constc=0)'

# --- apply ---
for m, k in viol:
    off = m['_off'] + 80
    assert be32(Z, off) == enc(BAD_ASSET)
    struct.pack_into('>I', Z, off, enc(GOOD_ASSET))
    print('  REPOINT @%-9d %-34s asset %d (GfxLightDef) -> %d (%s)'
          % (m['_off'], m['name'], BAD_ASSET, GOOD_ASSET, GOOD_NAME))

assert len(Z) == len(orig), 'ZONE SIZE CHANGED'
changed = [i for i in range(len(Z)) if Z[i] != orig[i]]
allowed = set()
for m, k in viol:
    allowed.update(range(m['_off'] + 80, m['_off'] + 84))
assert set(changed) <= allowed, 'edit touched bytes outside techSet* fields'
print('size-neutral OK; bytes changed: %d (all at body+80)' % len(changed))
assert gate(Z, 'out') == 0
open(DST, 'wb').write(bytes(Z))

# --- post: all three invariants (the permanent gate set) ---
Z2, mats2, dem2, sp2, tsn2, tsi2 = scan(DST)
rc2 = wiiu_zone.ZoneReader(bytes(Z))
rc2.read_string_table()
rc2.read_asset_list()
bad_type = [m for m in mats2 if tsi2(m['ts']) is not None and rc2.assets[tsi2(m['ts'])][0] != 8]
unsat = []
for m in mats2:
    k = tsi2(m['ts'])
    if k is None or not dem2.get(k):
        continue
    if dem2[k] - set(m['consts']):
        unsat.append(m)
print('\nPOST-CHECK  (raid oracle: 0 / 0)')
print('  techSet* -> non-type-8 asset : %d' % len(bad_type))
print('  demand NOT subset carried    : %d' % len(unsat))
assert not bad_type and not unsat
assert len(mats2) == len(mats), 'material count changed'
print('  materials walked             : %d (unchanged)' % len(mats2))

print('\n%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
