#!/usr/bin/env python3
"""gfxtail20: fix the boot-23 NULL-constantTable crash by REPOINTING 10 mis-bound materials.

ROOT CAUSE (oracle-proven, _nullct_oracle.py / _nullct_oracle_pair.py / _nullct_target.py):
Boot 23 faults at `lwz r29,0(r28)` in R_GetPixelLiteralConsts with r28 == material->constantTable
== NULL: 10 materials carry ZERO constants (ct*==0) yet their techset demands a type-6
constant-by-nameHash.

The RAID ORACLE settles what "correct" means -- and it is NOT "the converter dropped constants":
  * raid ships 283/578 materials with constc==0  -> zero-constant materials are NORMAL.
  * raid: 0 of 65 materials bound to a demanding techset are unsatisfied.
  => the genuine INVARIANT is  demand(techset) SUBSET carried(material),
     upheld by ZERO-CONSTANT MATERIALS BINDING TO ZERO-DEMAND TECHSETS.
Our 10 violate it because they are bound to the WRONG TECHSET. Two distinct defects:

(A) 6 decals on asset 678 `wpc_unlit_replace_4688792e` (demands scaleRGB 0xe27483cf).
    ORACLE-EXACT: raid ships the very same material `wpc/decal_damage_wall_fillet`
    (texc=1, constc=0) bound to `wpc_unlitdecalblend_multiply_35079164` (demands NOTHING).
    EVERY genuine `*_unlitdecalblend_multiply_*` user in raid is texc=1/constc=0 -- exactly our
    6 decals' profile. Meanwhile genuine `wpc_unlit_replace_4688792e` has ONE user,
    `wpc/light_white_02_unlit_no_offset` (texc=1, constc=1, carries scaleRGB): it is a LIGHT
    techset, not a decal one. `wpc_unlit` prefixes BOTH names -> techset_translate.prefix_fallback
    mis-picked. Repoint 678 -> 679 (same name as raid's, present in our own zone).

(B) 4 decals on asset 699 `lit_sm_r0c0n0x0_b1c1n1s1v1` (demands alphaRevealP 0x88befc31).
    NOT a fallback mis-pick: the PC zone itself contains the bare name `lit_sm_r0c0n0x0_b1c1n1s1v1`
    (x4) and NO `wpc_lit_sm_..._b1c1n1s1v1`, so the substitution matched the PC name EXACTLY. The
    console techset of that name demands a constant our material cannot feed. Across ~40 sibling
    techsets the rule is perfect: a `v1` layer <=> demands alphaRevealP/colorTint2; no `v1` <=>
    demands NOTHING. Repoint to the v1-stripped sibling `lit_sm_r0c0n0x0_b1c1n1s1` (asset 695):
    same family, same layer grammar, minus the reveal layer they cannot feed.

WHY REPOINTING IS SAFE HERE (the family-9 trap, checked -- do not skip this):
  * Material.techSet* (+80) is a block-5 ALIAS into the asset array: a 4-byte VALUE that consumes
    NO stream bytes. Rewriting it is strictly size-neutral -> rtmap + the whole gfxtail stack stay
    valid. (Contrast ct*: FOLLOW there would make the loader consume constc*32 bytes.)
  * Alias math verified by round-trip on all 893 materials: enc(ts_idx(a)) == a.
        alias = (0xA0000000 | (our_arr + 4 + k*8)) + 1,  our_arr = align8(assets_off - 64)
  * POOL WINNER checked: neither target name occurs in ANY earlier-loading zone (patch_mp,
    common_mp, ui_mp, code_*_gfx*, faction_*), so OUR copy wins the name-keyed pool -- and the
    genuine corpus copies of both names demand NOTHING anyway. Both paths are safe.

Cost: a repointed material samples a slightly different shader (visual only) -- already the
accepted trade for the whole substitution layer. Raid is untouched by construction.
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
from _matconst_map import FOLLOW, be32
from _nullct_oracle import scan

SRC = 'mp_skate_gfxtail19.zone'
DST = 'mp_skate_gfxtail20.zone'
FF = 'mp_skate_gfxtail20.ff'
BB = 84512493
GATE_END = 89584099

# (wrong techset asset) -> (correct techset asset, expected name)
REPOINT = {
    678: (679, 'wpc_unlitdecalblend_multiply_35079164'),
    699: (695, 'lit_sm_r0c0n0x0_b1c1n1s1'),
}

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap end=%d (want %d) delta=%+d' % (tag, end, GATE_END, d))
    assert end == GATE_END, 'clipMap gate moved — zone desynced'
    return d


assert gate(Z, 'in') == 0

# ---------------------------------------------------------------- survey (pre)
Zb, mats, demand, ts_spans, ts_name, ts_idx = scan(SRC)
rc = wiiu_zone.ZoneReader(bytes(Z))
rc.read_string_table()
rc.read_asset_list()
our_arr = ((rc.assets_off - 64) + 7) & ~7


def enc(k):
    return (0xA0000000 | (our_arr + 4 + k * 8)) + 1


# alias math must round-trip on EVERY material before we rewrite any of them
rt_bad = [m for m in mats if ts_idx(m['ts']) is not None and enc(ts_idx(m['ts'])) != m['ts']]
assert not rt_bad, 'alias round-trip failed on %d materials' % len(rt_bad)
print('alias round-trip verified on %d materials' % len(mats))

# targets must be MaterialTechniqueSet (type 8) and demand NOTHING
for src_k, (dst_k, want_name) in REPOINT.items():
    assert rc.assets[dst_k][0] == 8, 'target %d is not a MaterialTechniqueSet' % dst_k
    got = ts_name(ts_spans[dst_k][0])
    assert got == want_name, 'target %d name %r != %r' % (dst_k, got, want_name)
    assert not demand.get(dst_k), 'target %d DEMANDS %s — not a safe repoint' % (
        dst_k, ['0x%08x' % h for h in sorted(demand[dst_k])])
    print('target asset %-4d %-42s type=8 demands=NOTHING  OK' % (dst_k, want_name))

# the violators: zero-constant materials bound to a demanding techset
viol = []
for m in mats:
    k = ts_idx(m['ts'])
    if k is None or not demand.get(k):
        continue
    if demand[k] - set(m['consts']):
        viol.append((m, k))
print('\nunsatisfied materials found: %d' % len(viol))
assert len(viol) == 10, 'expected the 10 boot-23 materials, got %d' % len(viol)
assert all(m['constc'] == 0 for m, k in viol), 'a violator carries constants — different bug'
assert all(k in REPOINT for m, k in viol), 'a violator sits on an unplanned techset'

# ---------------------------------------------------------------- apply
n = 0
for m, k in viol:
    dst_k, want_name = REPOINT[k]
    off = m['_off'] + 80
    assert be32(Z, off) == m['ts'] == enc(k), 'ts field moved under us'
    struct.pack_into('>I', Z, off, enc(dst_k))
    print('  @%-9d %-46s  ts %d -> %d (%s)' % (m['_off'], m['name'][:46], k, dst_k, want_name))
    n += 1
print('repointed %d materials' % n)

# ---------------------------------------------------------------- gates (post)
assert len(Z) == len(orig), 'ZONE SIZE CHANGED — forbidden'
changed = [i for i in range(len(Z)) if Z[i] != orig[i]]
allowed = set()
for m, k in viol:
    allowed.update(range(m['_off'] + 80, m['_off'] + 84))
assert set(changed) <= allowed, 'edit touched bytes outside the techSet* fields'
print('size-neutral OK; bytes changed: %d (== %d materials x 4, all at body+80)'
      % (len(changed), n))
assert gate(Z, 'out') == 0

open(DST, 'wb').write(bytes(Z))

# ---------------------------------------------------------------- survey (post)
Z2, mats2, demand2, ts_spans2, ts_name2, ts_idx2 = scan(DST)
left = []
for m in mats2:
    k = ts_idx2(m['ts'])
    if k is None or not demand2.get(k):
        continue
    if demand2[k] - set(m['consts']):
        left.append((m, k))
print('\nPOST-CHECK unsatisfied materials: %d  (raid oracle = 0)' % len(left))
for m, k in left[:8]:
    print('   STILL BAD constc=%d %s -> ts %d' % (m['constc'], m['name'][:44], k))
assert not left, 'invariant demand SUBSET carried still violated'
assert len(mats2) == len(mats), 'material count changed — walk desynced'

print('\n%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
