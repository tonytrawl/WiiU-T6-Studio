#!/usr/bin/env python3
"""
_alias_decode.py — the CORRECT block-5 alias -> file-offset decoder, validated on the raid oracle.

WHY: the decoder used earlier, `fileoff(a) = (a & 0x1FFFFFFF) - 1 + 64`, is WRONG. It assumes the
alias payload is a FILE offset. It is not. From loader_sim (the model that reproduces genuine
aliases byte-exactly):

    want = zs.encode_ptr(zs.BLOCK_VIRTUAL, rtmap.rt(byhash[h] - B5_BASE))
    d    = ((p - 1) & 0x1FFFFFFF) - rtmap.rt(byhash[h] - B5_BASE)
    B5_BASE = 64

    => payload = ((alias - 1) & 0x1FFFFFFF) == rt(file_offset - 64)

so the payload is a **RUNTIME** offset. Decoding needs the INVERSE of RuntimeMap.rt, which is
piecewise-linear over emitter.omap:

    rt(src) = vals[i] + (src - keys[i])         for src in [keys[i], keys[i+1])

The old decoder is exactly this with rt == identity, which is why it failed the oracle (it claimed
raid's surf[0].material pointed inside raid's own surfaces array).

GATE (CORRECTED 2026-08-08 -- the previous wording was FALSE AS WRITTEN, see below):
on raid (byte-exact, BOOTS) every surfaces[k].material must decode onto the **materialMemory SLOT
TABLE** -- i.e. onto `&matmem[i].material`, NOT onto a Material body. Measured on raid: all 5,281
targets land in a **2,808-byte window = 352 distinct addresses, 8-strided, contiguous**
(`MaterialMemory` = {Material*; int} = 8 B). Reaching the Material body takes ONE MORE DEREF, which
is the loader's own law: `*dest = *(&matmem[i].material)`.

  OLD CLAIM, RETRACTED: "EVERY surfaces[k].material must decode to a real Material body -- i.e. to
  an offset in the set found by _nullct_oracle.scan(). Anything less and the decoder is wrong."
  That scored **0/5281** and was read as a decoder regression. It was not: the DECODER IS SOUND and
  was resolving exactly per the deref law above. The self-test's TARGET-DOMAIN assumption was wrong
  from the start -- it asserted the wrong destination, one deref too deep.
  Ruled out cheaply before triage: running the self-test under BOTH the plain and the anchored
  policies gave 0/5281 IDENTICALLY, which eliminates map displacement as the cause.

⚠ TWO LAWS EARNED HERE, worth applying to any future self-test in this module:
  1. A SELF-TEST BUILT ON AN INVERSE VALIDATES THREE THINGS AT ONCE -- the rt MAP, the DECODER, and
     the TARGET-DOMAIN ASSUMPTION. When it fails you cannot tell which broke, so it is a poor test
     of the decoder specifically. Prefer a target-domain assertion that is structural and
     model-free (here: "352 distinct, 8-strided, contiguous" is checkable without resolving
     anything).
  2. **WHEN A RESOLUTION SCORES ~0%, SUSPECT ONE-DEREF-TOO-SHALLOW BEFORE A BROKEN INSTRUMENT.**
     A near-zero hit rate against a plausible domain usually means the right answer is one
     indirection away, not that the decoder is wrong. (A ~663 KB constant miss looked like map
     displacement; it was a holder table sitting one deref short of the bodies.)
"""
import bisect
import struct
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS

B5_BASE = 64


def be32(d, o):
    return struct.unpack_from('>I', d, o)[0]


class AliasDecoder:
    """Invert loader_sim.RuntimeMap.rt: runtime b5 offset -> stream b5 offset."""

    def __init__(self, omap):
        self.keys = sorted(omap)                       # stream b5 starts
        self.vals = [omap[k] for k in self.keys]       # runtime b5 starts
        # segment i spans stream [keys[i], keys[i+1]) -> runtime [vals[i], vals[i]+len_i)
        segs = []
        for i, k in enumerate(self.keys):
            end = self.keys[i + 1] if i + 1 < len(self.keys) else None
            ln = (end - k) if end is not None else None
            segs.append((self.vals[i], ln, k))
        segs.sort(key=lambda t: t[0])                  # sort by RUNTIME start
        self.rv = [s[0] for s in segs]
        self.segs = segs

    def rt(self, src_b5):
        i = bisect.bisect_right(self.keys, src_b5) - 1
        if i < 0:
            return src_b5
        return self.vals[i] + (src_b5 - self.keys[i])

    def inv(self, rt_off):
        """runtime b5 offset -> stream b5 offset (None if it lands outside every region)."""
        j = bisect.bisect_right(self.rv, rt_off) - 1
        if j < 0:
            return None
        rstart, ln, kstart = self.segs[j]
        if ln is not None and rt_off >= rstart + ln:
            return None                                # falls in a gap between regions
        return kstart + (rt_off - rstart)

    def target(self, alias):
        """block-5 alias -> file offset (None if undecodable)."""
        if not (0xA0000000 <= alias < 0xC0000000):
            return None
        payload = (alias - 1) & 0x1FFFFFFF
        s = self.inv(payload)
        return None if s is None else s + B5_BASE


# ---------------------------------------------------------------------------
# dpvs.surfaces LOCATOR  (retires the hardcoded CASES bases below)
#
# The CASES table at the bottom of this file hardcodes raid's and skate's
# surfaces bases because they were found BY HAND. That hardcoding was debt: the
# array is locatable from its own signature, with no dump and no layout guess.
#
# SIGNATURE (validated on the raid oracle): GfxSurface is 80 bytes and its
# `material` field is at +48 (gfxworld_probe2.CFG['wiiu']['surf_mat']). In a
# correct zone EVERY element's material word is a block-5 alias, so the array is
# the longest run of consecutive stride-80 records whose +48 word is in the
# block-5 band. The signature is razor-sharp: on raid the true base scores
# 5281/5281 and shifting +-1/+-2/+-4 collapses it to 0-13%.
#
# ⚠ Do NOT take the base from the gfxworld_probe2 / gfxworld_console_span region
# walk. That walk is exact on raid (delta 0) but DESYNCS on zm_nuked: it places
# dpvs.surfaces at 0x056B732D when the true base is 0x06626E1A -- 15.8 MB adrift,
# landing in 16-byte-periodic data. Its cursor is derived from header counts, so
# any earlier region mis-model shifts everything after it. The signature scan is
# content-derived and does not inherit that drift.
SURF_STRIDE, SURF_MAT = 80, 48


def find_surfaces(Z, lo, hi, min_run=200):
    """Locate dpvs.surfaces by signature. Returns (base, count) or (None, 0).

    `Z` = zone bytes, [lo, hi) = the GfxWorld span. Content-derived; no layout
    assumption beyond the 80/48 GfxSurface shape."""
    import numpy as np
    d = np.frombuffer(Z, dtype=np.uint8)
    best = (0, None)
    for phase in range(SURF_STRIDE):
        start = lo + phase
        n = (hi - start) // SURF_STRIDE
        if n < min_run:
            continue
        idx = start + SURF_MAT + np.arange(n) * SURF_STRIDE
        v = ((d[idx].astype(np.uint32) << 24) | (d[idx + 1].astype(np.uint32) << 16)
             | (d[idx + 2].astype(np.uint32) << 8) | d[idx + 3].astype(np.uint32))
        ok = (v >= 0xA0000000) & (v < 0xC0000000)
        if not ok.any():
            continue
        w = np.concatenate(([0], ok.view(np.int8), [0]))
        dd = np.diff(w)
        st, en = np.where(dd == 1)[0], np.where(dd == -1)[0]
        if not len(st):
            continue
        j = int(np.argmax(en - st))
        run = int((en - st)[j])
        if run > best[0]:
            best = (run, int(start + st[j] * SURF_STRIDE))
    return (best[1], best[0]) if best[0] >= min_run else (None, 0)


def surface_material_audit(Z, base, count):
    """Per-element health of GfxSurface.material. Genuine scores misaligned=0.

    Returns dict(total, alias, zero, other, misaligned, distinct, payload_lo,
    payload_hi, gap_set). The 4-align handle law -- ((h-1) & 0x1FFFFFFF) %% 4 == 0
    -- is the discriminator: raid 0/5281 violations, zm_nuked 9867/9867."""
    import collections
    out = collections.Counter()
    pays = []
    for k in range(count):
        v = struct.unpack_from('>I', Z, base + k * SURF_STRIDE + SURF_MAT)[0]
        if v == 0:
            out['zero'] += 1
            continue
        if not (0xA0000000 <= v < 0xC0000000):
            out['other'] += 1
            continue
        out['alias'] += 1
        p = (v - 1) & 0x1FFFFFFF
        pays.append(p)
        if p % 4:
            out['misaligned'] += 1
    u = sorted(set(pays))
    return dict(total=count, alias=out['alias'], zero=out['zero'], other=out['other'],
                misaligned=out['misaligned'], distinct=len(u),
                payload_lo=(u[0] if u else None), payload_hi=(u[-1] if u else None),
                gap_set=sorted({u[i + 1] - u[i] for i in range(len(u) - 1)})[:5])


def build(path):
    em, spans, CO = LS.simulate(path, verbose=False)
    return AliasDecoder(em.omap), CO, spans


if __name__ == '__main__':
    from _nullct_oracle import scan

    # (zone, surfaces file offset, count) -- raid's start is the walker cursor AFTER smodelInsts
    # (0x03c7a774); VERIFY: 63416180 + 5281*80 == 63838660 == 0x03ce19c4 == the cursor after
    # surfaces. skate's was located by byte-matching its pointer-free bounds (handoff 1.2).
    CASES = [('../wiiu_ref/mp_raid_genuine.zone', 'RAID (oracle, BOOTS)', 63416180, 5281),
             ('mp_skate_gfxtail22.zone', 'SKATE gfxtail22', 78869123, 7131)]

    for path, tag, S, N in CASES:
        print('=' * 78)
        print(tag)
        print('=' * 78)
        assert S + N * 80 == (63838660 if 'raid' in path else S + N * 80), 'arith'
        dec, Z, spans = build(path)
        print('  omap regions: %d' % len(dec.keys))
        # ground truth: every real Material body offset in this zone
        _, mats, _, _, _, _ = scan(path)
        mat_offs = {m['_off'] for m in mats}
        print('  material bodies found by the oracle scan: %d' % len(mat_offs))

        ok = bad = undec = 0
        misses = []
        for k in range(N):
            a = be32(Z, S + k * 80 + 48)
            t = dec.target(a)
            if t is None:
                undec += 1
                continue
            if t in mat_offs:
                ok += 1
            else:
                bad += 1
                if len(misses) < 5:
                    misses.append((k, a, t))
        print('  surfaces[].material  -> a REAL Material body : %d / %d' % (ok, N))
        print('                       -> some other offset    : %d' % bad)
        print('                       -> undecodable          : %d' % undec)
        for k, a, t in misses:
            near = min((abs(t - m), m) for m in mat_offs)[1] if mat_offs else 0
            print('     surf[%-5d] alias 0x%08x -> file %-9d  nearest material %d (delta %+d)'
                  % (k, a, t, near, t - near))
        print()
