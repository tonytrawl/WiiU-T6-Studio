"""Coverage gap in _fx_visuals_audit.py: it only ever reads the FxElemDef +196
word. When visualCount > 1 that word points to an ARRAY of FxElemVisuals handles
living in the per-elem dynamic tail, and those entries were NEVER compared.

This reads the array entries too, so the defect is sized against all handles
rather than only the fixed-body one.

Tail layout per elem (from fx_convert._conv_elem_dyn), in order:
  velSamples : (velIntervalCount+1) * 96          if +188 is FOLLOW/INSERT
  visSamples : (visStateIntervalCount+1) * 2 * 24 if +192 is FOLLOW/INSERT
  visuals    : elemType 7 (DECAL) -> visualCount * 8 (FxElemMarkVisuals)
               visualCount > 1    -> visualCount * 4 (FxElemVisuals ptr array)
               else               -> inline record, no array

  python _fx_visarray_audit.py [ours.zone] [key.zone]
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS

OURS = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_final.zone'
KEY = sys.argv[2] if len(sys.argv) > 2 else 'mp_skate_gfxtail46.zone'
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
ED, FX_HDR, TYPE_DECAL = 292, 76, 7
be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
be16s = lambda d, o: struct.unpack_from('>h', d, o)[0]
AL = lambda v: 0xA0000000 <= v < 0xC0000000


def collect(path):
    """-> list of (fx_idx, elem_i, slot, value) for EVERY visuals handle:
    slot 'fixed' = the +196 word, slot k = array entry k."""
    Z = open(path, 'rb').read()
    _, spans, _ = LS.simulate(path, verbose=False)
    out, skipped = [], 0
    for (idx, _n, r, s, e) in spans:
        if r != 'FxEffectDef':
            continue
        n = be16s(Z, s + 8) + be16s(Z, s + 10) + be16s(Z, s + 12)
        if not (0 < n < 4096):
            skipped += 1
            continue
        c = s + FX_HDR
        if be32(Z, s) in PTRS:
            z = Z.find(b'\x00', c)
            c = z + 1 if z >= 0 else c
        if be32(Z, s + 28) not in PTRS:
            skipped += 1
            continue
        base = c
        tail = base + n * ED
        for i in range(n):
            eb = base + i * ED
            if eb + ED > len(Z):
                break
            etype, vcount = Z[eb + 184], Z[eb + 185]
            vic, vsc = Z[eb + 186], Z[eb + 187]
            out.append((idx, i, 'fixed', be32(Z, eb + 196)))
            # advance the tail cursor exactly as the converter does
            if be32(Z, eb + 188) in PTRS:
                tail += (vic + 1) * 96
            if be32(Z, eb + 192) in PTRS:
                tail += (vsc + 1) * 2 * 24
            vis = be32(Z, eb + 196)
            if etype == TYPE_DECAL:
                if vis in PTRS:
                    for k in range(vcount):
                        out.append((idx, i, 'mark%d' % k, be32(Z, tail + k * 8)))
                        out.append((idx, i, 'mark%db' % k, be32(Z, tail + k * 8 + 4)))
                # inline material bodies follow; cursor unknown without a walker
                return out, skipped, True
            if vcount > 1 and vis in PTRS:
                for k in range(vcount):
                    out.append((idx, i, 'arr%d' % k, be32(Z, tail + k * 4)))
            # NOTE: cannot advance past inline material bodies without a full
            # material walker, so we only trust arrays on elems reached before
            # the first inline record. Handled by the caller via 'trusted'.
    return out, skipped, False


a, ska, trunca = collect(OURS)
b, skb, truncb = collect(KEY)
print('handles collected: ours=%d key=%d   (fx assets skipped: %d/%d)'
      % (len(a), len(b), ska, skb))

# ---------------------------------------------------------------------------
# ⛔ THIS AUDIT IS INCOMPLETE BY CONSTRUCTION — it must REFUSE, not report zeros.
#
# Advancing the per-elem tail cursor requires stepping over each INLINE MATERIAL
# record, which is variable-length (name + texdefs + constants) and needs a full
# console material walker this script does not have. Without it, every array
# offset computed after the first inline record is WRONG, and comparing garbage
# to garbage yields a confident "0 mismatches".
#
# Measured on skate: it collected 8 handles (2 fixed + 6 array) before going
# invalid, out of several hundred, and printed "MISMATCH: 0". Reading that as
# "the array entries are clean" would be exactly backwards.
#
# To finish this: use the inline-material walker from the FIX 6 work
# (fx_backref_fix / the recon recipe) to advance the cursor, then re-enable.
# ---------------------------------------------------------------------------
_EXPECT_MIN = 300          # skate has 329 elems; a valid run must see >= this
if trunca or truncb or len(a) < _EXPECT_MIN:
    raise SystemExit(
        'REFUSING: collected only %d handles (expected >= %d)%s.\n'
        'The tail cursor cannot step over variable-length inline Material\n'
        'records without a material walker, so any counts printed past that\n'
        'point are meaningless. This script is NOT a clean bill of health for\n'
        'the FxElemVisuals array entries — that measurement is still OPEN.'
        % (len(a), _EXPECT_MIN,
           '; stopped at a DECAL elem' if (trunca or truncb) else ''))

fixed_a = [x for x in a if x[2] == 'fixed']
arr_a = [x for x in a if x[2] != 'fixed']
print('  fixed(+196) handles: %d     ARRAY-entry handles: %d' % (len(fixed_a), len(arr_a)))

if len(a) != len(b):
    print('!! handle counts differ — structures diverge, cannot pair')
    sys.exit(1)

mism_fixed = mism_arr = 0
for (ia, ei, sl, va), (ib, ej, sm, vb) in zip(a, b):
    if (ia, ei, sl) != (ib, ej, sm):
        print('!! pairing desync at fx#%d elem%d %s' % (ia, ei, sl))
        break
    if va == vb:
        continue
    if sl == 'fixed':
        mism_fixed += 1
    else:
        mism_arr += 1

print('\nMISMATCH vs key:  fixed(+196)=%d   ARRAY entries=%d   TOTAL=%d'
      % (mism_fixed, mism_arr, mism_fixed + mism_arr))
print('distinct alias targets  ours=%d  key=%d'
      % (len(set(v for _, _, _, v in a if AL(v))),
         len(set(v for _, _, _, v in b if AL(v)))))
