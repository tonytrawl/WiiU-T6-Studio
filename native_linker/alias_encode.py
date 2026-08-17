#!/usr/bin/env python3
"""THE ONE PLACE A BLOCK-5 HANDLE IS MINTED. Rule (FO), baked for the fleet.

⭐⭐⭐⭐ (FO) AN ALIAS MUST BE ENCODED IN THE CONSUMER'S COORDINATE SYSTEM, NOT THE
PRODUCER'S. Our payload `(v-1)&0x1FFFFFFF` was a FILE offset; the loader wants a
RUNTIME offset. On zm_nuked they differ by ~1,179,000 bytes. CONFIRMED AGAINST
REAL GUEST MEMORY: at the file coordinate the array read 77/95 words of garbage
and the engine faulted; at the runtime coordinate it read 95/95 NULL, a valid
empty array.

⛔⛔ WHY THIS IS A MODULE AND NOT A FUNCTION IN ONE TOOL. The defect survived the
entire campaign because THREE tools each minted handles their own way and each
validated its own output by decoding with its own rule. Every gate passed. We
validated our coordinate system against itself (rule FN). A shared contract makes
that class of bug structurally impossible: there is one encoder, it takes a FILE
offset because that is what a producer naturally knows, and it is the only code
that decides what goes in the payload.

⚠ FOR THE FLEET (Die Rise, hydro, downhill, mirage, prison): any new tool that
writes a pointer into a zone MUST come through here. If you find yourself writing
`| 0xA0000000` anywhere else, that is the bug reappearing.

ON INTERPOLATION. omap is sampled at object starts, not dense, so an interior
target (a slot inside a body -- what a DEREF-alias names) has no exact key. The
runtime offset is then interpolated from the nearest anchor at or below AND
VALIDATED against the target's natural alignment. That validation is what makes
interpolation legitimate rather than a guess: an interpolation that crossed
padding lands on an arbitrary residue and is caught 3 times in 4. Measured before
enabling on b85: +8 2/2, +24 86/86, +28 76/76 interpolate to a perfectly aligned
runtime offset (164/164), where random error would align about 1 in 4.

⛔ REFUSES rather than guesses. A target that cannot be resolved, or that fails
its alignment check, comes back as a refusal with a reason -- it is never
approximated. The 6 Material* field targets on b85 failed the check and stayed
refused, which is the check doing real work rather than rubber-stamping.
"""
import bisect, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'wiiu_ref'))

B5_BASE = 64
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
BLOCK_VIRTUAL = 5

# natural alignment per target kind. ⛔ NOT a blanket 4: genuine zm_transit ships
# handles at residue 2 (ScriptString/u16 arrays align 2), and a gate asserting
# blanket 4-alignment flags retail content, which is always the gate being wrong.
ALIGN_PTR_ARRAY = 4
ALIGN_U16_ARRAY = 2
ALIGN_BYTE = 1


class Refused(Exception):
    """Carries the DATUM and the losing value, never just a verdict string."""


class Coord(object):
    """The loader's coordinate system for one zone, derived from the CONSUMER.

    `loader_sim` reproduces the loader's own stream walk, so its omap is the
    loader's placement for every object -- not our record of where we put things.
    That distinction is the whole fix."""

    def __init__(self, zone_bytes, omap=None, label='', policy=None):
        self.label = label
        if omap is None:
            import loader_sim as LS, rt_events_exact as RTE
            em, _spans, _ = LS.simulate(bytes(zone_bytes), verbose=False,
                                        policy=policy or RTE.policy())
            omap = dict(em.omap)
        self.omap = omap                       # {file_b5_off: runtime_b5_off}
        self.anchors = sorted(omap)
        self.vals = [omap[k] for k in self.anchors]
        self.rt_set = set(omap.values())
        self._rt_sorted = sorted(self.rt_set)
        # ⭐⭐⭐⭐ (GL) THE REVERSE MAP MUST PREFER THE **LAST** REGISTRANT.
        # 5,827 runtime offsets (1.8%) have MORE THAN ONE file offset, because a
        # root routed to the TEMP block registers without advancing the virtual
        # cursor -- so the next object registers at the same runtime offset and is
        # the one that actually occupies that space. Keeping the FIRST (setdefault,
        # over sorted keys = the lowest file offset) returns the TEMP root instead
        # of the real occupant.
        # PROVEN AGAINST GUEST MEMORY: rt 126271496 maps to file 125103413 (a
        # struct root) and 125103517 (the cstring "viewmodel_default_idle"). The
        # b88 dump shows the loader placed the STRING there. The first-wins map
        # answered 125103413, so the census judged the wrong object and PASSED the
        # cell that then crashed the boot.
        self._rt_to_fo = {}
        for k in self.anchors:
            self._rt_to_fo[omap[k]] = k          # last wins, deliberately

    # ---- file -> runtime ---------------------------------------------------
    def rt_of_file(self, file_off, align=ALIGN_PTR_ARRAY):
        """Returns (runtime_offset, how). Raises Refused with the datum."""
        fo = file_off - B5_BASE
        rt = self.omap.get(fo)
        if rt is not None:
            return rt, 'exact'
        j = bisect.bisect_right(self.anchors, fo) - 1
        if j < 0:
            raise Refused('file offset %d (b5 %d) has NO anchor at or below it '
                          'in a %d-entry omap -- cannot place it in the loader\'s '
                          'coordinates' % (file_off, fo, len(self.omap)))
        delta = fo - self.anchors[j]
        rt = self.vals[j] + delta
        if align > 1 and rt % align:
            raise Refused('file offset %d interpolates to runtime %d (anchor %d '
                          '+%d), which is %d mod %d -- an interpolation that '
                          'crossed padding. REFUSED, not approximated.'
                          % (file_off, rt, self.anchors[j], delta, rt % align,
                             align))
        return rt, 'interp+%d' % delta

    # ---- the mint ----------------------------------------------------------
    def encode_file(self, file_off, align=ALIGN_PTR_ARRAY):
        """FILE offset in -> a handle the LOADER will resolve correctly."""
        rt, how = self.rt_of_file(file_off, align)
        return self.encode_rt(rt), how

    @staticmethod
    def encode_rt(rt_off, block=BLOCK_VIRTUAL):
        return (((block << 29) | (rt_off & 0x1FFFFFFF)) + 1) & 0xFFFFFFFF

    # ---- inspection --------------------------------------------------------
    @staticmethod
    def decode(v):
        w = (v - 1) & 0xFFFFFFFF
        return (w >> 29), (w & 0x1FFFFFFF)

    def is_runtime_coord(self, v):
        """Does this handle already speak the loader's language? A handle whose
        payload is a registered RUNTIME start is correct; one whose payload is a
        registered FILE offset is the (FO) defect."""
        blk, off = self.decode(v)
        if blk != BLOCK_VIRTUAL:
            return None
        if off in self.rt_set:
            return True
        if off in self.omap:
            return False
        return None                            # interior or unknown

    def file_of_rt(self, rt):
        """Inverse, for reading a correctly-encoded handle back to a file
        offset (needed when re-pointing something already fixed)."""
        fo = self._rt_to_fo.get(rt)
        if fo is not None:
            return fo + B5_BASE
        j = bisect.bisect_right(self._rt_sorted, rt) - 1
        if j < 0:
            return None
        r0 = self._rt_sorted[j]
        return self._rt_to_fo[r0] + (rt - r0) + B5_BASE


def for_zone(zone_path_or_bytes, label=''):
    Z = (zone_path_or_bytes if isinstance(zone_path_or_bytes, (bytes, bytearray))
         else open(zone_path_or_bytes, 'rb').read())
    return Coord(Z, label=label or str(zone_path_or_bytes))


def selftest():
    """Both arms in one run: a handle that must round-trip, and one that must be
    REFUSED. A gate that cannot fail is not a gate."""
    omap = {0: 0, 100: 128, 200: 256, 301: 360}
    C = Coord(b'', omap=omap, label='selftest')
    ok = fail = 0

    h, how = C.encode_file(100 + B5_BASE)
    exp = ((5 << 29) | 128) + 1
    if h == exp and how == 'exact' and C.decode(h) == (5, 128):
        ok += 1
        print('   PASS exact: file 100 -> rt 128 -> 0x%08X' % h)
    else:
        fail += 1
        print('   FAIL exact: got 0x%08X (%s), wanted 0x%08X' % (h, how, exp))

    h, how = C.encode_file(104 + B5_BASE)
    if C.decode(h)[1] == 132 and how == 'interp+4':
        ok += 1
        print('   PASS interp: file 104 -> rt 132 (%s)' % how)
    else:
        fail += 1
        print('   FAIL interp: got %s (%s)' % (C.decode(h), how))

    # KNOWN-FAIL ARM: 301 -> 360 is fine, but 302 -> 361 is odd, so a 4-aligned
    # target must be REFUSED rather than silently written one byte out.
    try:
        C.encode_file(302 + B5_BASE, align=4)
        fail += 1
        print('   FAIL known-fail arm: a misaligned interpolation was ACCEPTED')
    except Refused as e:
        ok += 1
        print('   PASS known-fail arm REFUSED: %s' % str(e)[:96])

    # and the same target IS legal at byte alignment -- the check is type-aware,
    # not a blanket 4 (genuine ships residue-2 handles; see ALIGN_U16_ARRAY)
    try:
        h, _ = C.encode_file(302 + B5_BASE, align=1)
        ok += 1
        print('   PASS align-aware: same target accepted at align 1 -> 0x%08X' % h)
    except Refused:
        fail += 1
        print('   FAIL align-aware: refused a legal byte-aligned target')

    if C.is_runtime_coord(Coord.encode_rt(128)) is not True:
        fail += 1
        print('   FAIL is_runtime_coord did not recognise a runtime payload')
    else:
        ok += 1
        print('   PASS is_runtime_coord: runtime payload recognised')
    if C.is_runtime_coord(Coord.encode_rt(200)) is not False:
        fail += 1
        print('   FAIL is_runtime_coord did not flag a FILE payload as the defect')
    else:
        ok += 1
        print('   PASS is_runtime_coord: file payload flagged as the (FO) defect')

    print('   selftest: %d pass, %d FAIL' % (ok, fail))
    return fail == 0


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    sys.exit(__doc__)
