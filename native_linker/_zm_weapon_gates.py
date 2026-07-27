"""SHAPE-BLIND consumer gates for BG_SetupWeaponVariantDef (bake candidate).

These are CONSUMER-side structural contracts the engine asserts at
G_SetupWeaponDef time. No loader walk or zone gate sees them, so they must be
asserted explicitly after any weapon-family patch. Each cost at least one boot.

G-W1  uniques[k] != 0 for k < 63  =>  attachments[k] must be a real record.
      The loop is FULLY UNROLLED over 63 slots and derefs attachments[k] with
      NO null check (it only skips when the record's byte@+0x32 == 0).
      Surplus uniques are parked at index >= 63 (the PC source does this
      itself: xm8_upgraded_zm ships uniques at 63/64). Marker placement inside
      a pointer array is STREAM-NEUTRAL, so re-indexing is free.
G-W2  attachments[0] is read UNCONDITIONALLY and compared (+0xEC, float +0xE8)
      against every non-zero entry => any non-zero entry needs a real record
      at index 0.
G-W3  every char* NAME the engine looks up must be EMPTY or resolve to real
      ASCII. An empty name is engine-blessed (`lbz r12,0(p); extsb.; beq skip`);
      a garbage one is a fatal Com_Error. Boot 33 died here on weapDef+0, the
      "standing WeapDef" name: "could not find standingWeapdef '<raw bytes>'".

⚠ ENUMERATE BY FIELD SHAPE, NEVER BY WALKER SECTION. The three live shapes are
(attachments, uniques) = (FOLLOW,FOLLOW) / (ZEROREC,alias) / (ZEROREC,FOLLOW);
a section-keyed check sees only the FOLLOW ones and silently passes the rest
(that was the boot-32 defect).
"""
import struct, sys, os, bisect
sys.path.insert(0, '.')
import loader_sim as LS, rt_events_weapon as RW, rt_events_exact as RTX
from measured_rtmap import MeasuredRuntimeMap

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
EMPTY, ZEROREC = 0xA001BD32, 0xA77D18F5
# defaultweapon_mp ships BOTH shared all-zero arrays back to back (dump-measured
# acceptance anchors): attachments[63] @rt 0x77D18F4 and uniques[95] @rt
# 0x77D19F0, exactly 63*4 apart. 76 of 98 weapons alias the uniques one, so a
# resolver that does not know it reads whatever the interpolated rt->file
# inverse lands on and reports thousands of phantom G-W1 violations.
ZEROUNIQ = 0xA77D19F1
N_ATTACH, N_UNIQ, UNROLL = 63, 95, 63
ZONE = sys.argv[1] if len(sys.argv) > 1 else 'zm_nuked_authored.zone'

Z = open(ZONE, 'rb').read()
_, spans, _ = LS.simulate(ZONE, verbose=False,
                          policy=dict(extra_events=RTX.all_events()))
ov = MeasuredRuntimeMap('_zmnuked_simmap.pkl', '_zmnuked_realmap.pkl')
inv = []
for (lo, hi, rs) in ov.spans:
    if rs is not None:
        inv.append((rs, rs + (hi - lo), lo))
for (lo, rt) in ov.interior:
    sp = next(((l, h) for (l, h, r) in ov.spans if l <= lo < h), None)
    if sp:
        inv.append((rt, rt + (sp[1] - lo), lo))
inv.sort()
_keys = [t[0] for t in inv]

def rt_to_file(rt):
    j = bisect.bisect_right(_keys, rt) - 1
    if j < 0:
        return None
    r0, r1, lo = inv[j]
    if rt >= r1 + 0x40000:
        return None
    return lo + (rt - r0) + ov.ae

def array_at(v, n, sect):
    """Entries of a pointer array field, shape-blind. -> (list|None, shape)."""
    if v == 0:
        return [0] * n, 'ZERO'
    if v in (ZEROREC, ZEROUNIQ):
        return [0] * n, 'ZEROARRAY'
    if v == FOLLOW:
        if sect is None:
            return None, 'FOLLOW(no section)'
        return list(struct.unpack_from('>%dI' % n, Z, sect)), 'FOLLOW'
    if (v >> 28) == 0xA:
        fo = rt_to_file((v - 1) & 0x1FFFFFFF)
        if fo is None or fo + 4 * n > len(Z):
            return None, 'alias(unresolved)'
        ent = list(struct.unpack_from('>%dI' % n, Z, fo))
        # the measured rt->file inverse INTERPOLATES; never trust a target that
        # does not read as a pointer array (report UNRESOLVED, do not accuse).
        if not all(x in (0, FOLLOW, INSERT) or (x >> 28) == 0xA for x in ent):
            return None, 'alias(target not a ptr array)'
        return ent, 'alias'
    return None, 'raw(%08x)' % v

def is_ascii_at(v):
    if v == EMPTY:
        return True
    if v in (0, FOLLOW, INSERT):
        return True
    if (v >> 28) != 0xA:
        return False
    fo = rt_to_file((v - 1) & 0x1FFFFFFF)
    if fo is None or not (64 <= fo < len(Z)):
        return False
    e = Z.find(b'\x00', fo, fo + 64)
    return e > fo and all(32 <= c < 127 for c in Z[fo:e])

v1 = v2 = v3 = 0
unresolved = []
nw = 0
for (i, nm, root, s, e) in spans:
    if root != 'WeaponVariantDef' or e <= s:
        continue
    nw += 1
    c = RW.weapon_walk(Z, s, '>')
    sec = {l: a for (l, a, b, k) in c.sections}
    wname = 'idx%d' % i
    if 'name' in sec:
        wname = Z[sec['name']:Z.find(b'\x00', sec['name'])].decode('latin1')
    av = struct.unpack_from('>I', Z, s + 0x18)[0]
    uv = struct.unpack_from('>I', Z, s + 0x1C)[0]
    at, ash = array_at(av, N_ATTACH, sec.get('attachments.ptrs'))
    uq, ush = array_at(uv, N_UNIQ, sec.get('attachmentUniques.ptrs'))
    if at is None or uq is None:
        unresolved.append((wname, ash, ush))
        continue
    for k in range(UNROLL):
        if uq[k] != 0 and at[k] == 0:
            v1 += 1
            print('  G-W1 %-28s uniques[%d]=%08x but attachments[%d]=0 (%s/%s)'
                  % (wname, k, uq[k], k, ash, ush))
    if any(x != 0 for x in at) and at[0] == 0:
        v2 += 1
        print('  G-W2 %-28s non-zero attachment entries but attachments[0]=0' % wname)
    if 'weapDef' in sec:
        wd = sec['weapDef']
        nv = struct.unpack_from('>I', Z, wd)[0]
        if not is_ascii_at(nv):
            v3 += 1
            print('  G-W3 %-28s weapDef+0 (standing WeapDef name) = %08x -> not a string'
                  % (wname, nv))
print('weapons=%d  G-W1=%d  G-W2=%d  G-W3=%d  unresolved=%d'
      % (nw, v1, v2, v3, len(unresolved)))
for u in unresolved:
    print('  UNRESOLVED', u)
print('GATE:', 'PASS' if (v1 == v2 == v3 == 0 and not unresolved) else 'FAIL')
