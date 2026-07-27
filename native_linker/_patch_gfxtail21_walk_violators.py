#!/usr/bin/env python3
"""gfxtail21: close the WALK class (`demand ⊄ carried`, constc>0) — all 6 violators at once.

WHY THERE WERE 6 AND WE ONLY SAW 1: `_matconst_map.parse_material` ignored inline GfxImages
(texdef.image* == FOLLOW => 328+name+pixels FOLLOW IN THE STREAM) and inline techsets, so for any
material carrying one it computed ct_off SHORT and read garbage "constants" -- and the audit's
name-sanity check then DROPPED the material silently. A techset with zero *found* users is never
audited, which is exactly how boot 24's `wpc/skt_video_screen_lrg` (3 of 4 texdefs FOLLOW =>
ct_off 1034 bytes short) passed a "0 unsatisfied" gate. Fixed by `_matconst_map.walk_material`,
which mirrors the VALIDATED `xmodel_probe.consume_material`.

With the correct walker the oracle is far stronger, and the skate picture is complete:
    raid  926 materials (was 578), 273 bound to a demanding techset (was 65) -> 0 unsatisfied
    skate 1211 materials (was 893), 260 bound -> 0 null-ct (gfxtail20 held) + 6 walk violators

TWO TOOLS, chosen per violator (both strictly size-neutral):
 A) REPOINT `Material.techSet*` (+80) -- a block-5 alias = a VALUE, consumes NO stream bytes.
    Target must satisfy demand ⊆ carried, same family (raid 351/351), and be the minimal name
    edit -- in practice the v-layer-stripped sibling (rule: a v-layer <=> demands
    alphaRevealP/colorTint2; no v-layer <=> demands NOTHING).
 B) REMAP the unsatisfiable type-6 arg VALUE inside the techset (gfxtail14's method) where a
    repoint would destroy the material's semantics (a 4-layer video screen, an ocean shader).
    ONLY safe because each of these techsets has exactly ONE user, so no other material is
    affected -- and only if the techset is NOT shadowed (family 9: a shadowed remap is a no-op).
    NEVER touch arg COUNTS: stream size = perPrim+perObj+stable (c.skip(nargs*8 + lits*16)).
"""
import hashlib
import re
import struct
import sys
from collections import defaultdict

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
import wiiu_ff
import wiiu_zone
from _matconst_map import ARG_CONST_HASH, be16, be32, walk_techset
from _nullct_oracle import scan

SRC = 'mp_skate_gfxtail20.zone'
DST = 'mp_skate_gfxtail21.zone'
FF = 'mp_skate_gfxtail21.ff'
BB = 84512493
GATE_END = 89584099

# material FILE OFFSET -> (current ts, target ts, target name)  (A: REPOINT)
# keyed by offset, not name: these are blend-COMBO materials whose synthetic names are long and
# easy to mistype. Every entry is re-verified below against enc(current ts) at body+80.
REPOINT = {
    78109411: (780, 676, 'wpc_unlit_multiply_20236462'),        # *127n_294n_236n(skt_wood_board_multi..)
    78181015: (724, 704, 'lit_sm_r0c0n0x0_b1c1_b2c2n2'),        # *15_17(metal_whitewall_karma..)  v2-strip
    78182464: (724, 704, 'lit_sm_r0c0n0x0_b1c1_b2c2n2'),        # *15_216(metal_whitewall_karma..) v2-strip
    78387137: (714, 701, 'lit_sm_r0c0n0x0_b1c1n1'),             # wpc/dub_decal_sea_foam_scroll    v1-strip
}
# techset asset -> expected name (B: REMAP its unsatisfiable arg; must have exactly 1 user)
REMAP = {729: 'wpc_unlit_add_8ez3wzw3', 717: 'wpc_cod7watershore_69ww38j2'}

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    print('  GATE[%s] clipmap end=%d delta=%+d' % (tag, end, m.start() - end))
    assert end == GATE_END, 'clipMap gate moved — zone desynced'
    return m.start() - end


assert gate(Z, 'in') == 0

Zb, mats, demand, ts_spans, ts_name, ts_idx = scan(SRC)
rc = wiiu_zone.ZoneReader(bytes(Z))
rc.read_string_table()
rc.read_asset_list()
our_arr = ((rc.assets_off - 64) + 7) & ~7
enc = lambda k: (0xA0000000 | (our_arr + 4 + k * 8)) + 1
assert not [m for m in mats if ts_idx(m['ts']) is not None and enc(ts_idx(m['ts'])) != m['ts']], \
    'alias round-trip failed'

bound = defaultdict(list)
for m in mats:
    k = ts_idx(m['ts'])
    if k is not None:
        bound[k].append(m)

viol = []
for m in mats:
    k = ts_idx(m['ts'])
    if k is None or not demand.get(k):
        continue
    if demand[k] - set(m['consts']):
        viol.append((m, k))
print('violators found: %d' % len(viol))
assert len(viol) == 6, 'expected 6 walk-class violators, got %d' % len(viol)

# --- family-9 trap: the REMAP targets must NOT be shadowed, or the remap is a no-op ---
import os
UPD = r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\content\english'
BASEDIR = r'E:\Wii U Black ops 2\content\english'


def gz(p):
    r = wiiu_ff.decrypt(open(p, 'rb').read())
    if isinstance(r, dict):
        return [v for v in r.values() if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    if isinstance(r, tuple):
        return [v for v in r if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    return bytes(r)


earlier = []
for f in ('patch_mp.ff', 'common_mp.ff', 'ui_mp.ff', 'code_post_gfx_mp.ff', 'faction_pmc_mp.ff'):
    for D in (UPD, BASEDIR):
        p = os.path.join(D, f)
        if os.path.exists(p):
            try:
                earlier.append((f, gz(p)))
            except Exception:
                pass
            break


def shadowed(nm):
    return [f for f, B in earlier if B.find(nm.encode() + b'\x00') != -1]


for k, want in REMAP.items():
    got = ts_name(ts_spans[k][0])
    assert got == want, 'remap target %d name %r != %r' % (k, got, want)
    sh = shadowed(want)
    assert not sh, 'ts %d %s is SHADOWED by %s — a remap would be a NO-OP (family 9)' % (k, want, sh)
    assert len(bound[k]) == 1, 'ts %d has %d users — remap would hit others' % (k, len(bound[k]))
    print('remap ts %-4d %-30s unshadowed, users=1  OK' % (k, want))

for _o, (src_k, dst_k, want) in REPOINT.items():
    got = ts_name(ts_spans[dst_k][0])
    assert got == want, 'repoint target %d name %r != %r' % (dst_k, got, want)
    assert rc.assets[dst_k][0] == 8, 'target %d not a MaterialTechniqueSet' % dst_k
    assert not demand.get(dst_k), 'target %d demands something' % dst_k
    sh = shadowed(want)
    print('repoint target %-4d %-42s demands=NOTHING %s'
          % (dst_k, want, 'SHADOWED by %s (genuine copy wins — verify it too)' % sh if sh
             else 'unshadowed'))

# ---------------------------------------------------------------- apply
n_rep = n_arg = 0
for m, k in viol:
    nm = m['name']
    if m['_off'] in REPOINT:
        src_k, dst_k, want = REPOINT[m['_off']]
        assert k == src_k, '%s is on ts %d, expected %d' % (nm[:40], k, src_k)
        off = m['_off'] + 80
        assert be32(Z, off) == enc(k)
        struct.pack_into('>I', Z, off, enc(dst_k))
        print('  REPOINT @%-9d %-42s ts %d -> %d' % (m['_off'], nm[:42], k, dst_k))
        n_rep += 1
    else:
        assert k in REMAP, 'violator %s on unplanned ts %d' % (nm[:40], k)
        carried = set(m['consts'])
        assert carried, 'remap needs a non-empty constantTable'
        target = min(carried)                       # deterministic
        bad = demand[k] - carried
        s, e = ts_spans[k]
        passes, _ = walk_techset(bytes(Z), s)
        for p in passes:
            assert p['lits'] == 0, 'inline literal args — layout assumption broken'
            for j in range(p['nargs']):
                a = p['args_off'] + j * 8
                if be16(Z, a) == ARG_CONST_HASH and be32(Z, a + 4) in bad:
                    struct.pack_into('>I', Z, a + 4, target)
                    n_arg += 1
        print('  REMAP   ts %-4d %-30s %s -> 0x%08x  (sole user %s)'
              % (k, ts_name(s), ['0x%08x' % h for h in sorted(bad)], target, nm[:30]))
print('repointed %d materials; remapped %d args' % (n_rep, n_arg))

# ---------------------------------------------------------------- gates
assert len(Z) == len(orig), 'ZONE SIZE CHANGED — forbidden'
assert gate(Z, 'out') == 0
open(DST, 'wb').write(bytes(Z))

Z2, mats2, dem2, sp2, tsn2, tsi2 = scan(DST)
left = []
for m in mats2:
    k = tsi2(m['ts'])
    if k is None or not dem2.get(k):
        continue
    if dem2[k] - set(m['consts']):
        left.append((m, k))
print('\nPOST-CHECK unsatisfied: %d   (raid oracle = 0)' % len(left))
for m, k in left[:6]:
    print('   STILL BAD constc=%d %s -> ts %d' % (m['constc'], m['name'][:44], k))
assert not left, 'invariant still violated'
assert len(mats2) == len(mats), 'material count changed — walk desynced'
print('materials walked: %d (unchanged)' % len(mats2))

print('\n%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
