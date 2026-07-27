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

GATE: on raid (byte-exact, BOOTS) EVERY surfaces[k].material must decode to a real Material body —
i.e. to an offset in the set found by _nullct_oracle.scan(). Anything less and the decoder is wrong.
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
