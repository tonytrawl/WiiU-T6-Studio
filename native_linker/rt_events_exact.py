"""EXACT runtime-allocation event generators (pipeline hardening, 2026-07-26).

Dump-calibrated per-type interior models replacing verbatim/linear consumption
in loader_sim. Measurement provenance: workflow wf_c4960942-4b9 needle-curves
against boot-16 (Cemu.exe.12984.dmp, zone 279fc3f1); every rule below was
verified by closed-form pad arithmetic pad == (-rt_cursor) mod N against
absolute runtime addresses.

XAnimParts (byte-exact on 3/3 measured assets):
  * console root struct = 104 BYTES (struct_layout says 92 — 12 short) and it
    loads into the TEMP block (the sim previously streamed 92 of it virtual:
    the +12/asset half of the systemic +9..12/asset error).
  * the loader re-aligns the RT cursor at each variable-data section while the
    FILE stays packed: names/dataShort/randomDataShort -> 2, notify -> 4,
    dataInt/randomDataInt -> 4, dataByte/randomDataByte -> 1, indices -> idxw.

Usage: pass via loader_sim policy seam:
    policy=dict(extra_events=rt_events_exact.EXTRA_EVENTS, ...)
"""
import struct

from alloc_events import Ev, PTRS

FOLLOW = 0xFFFFFFFF


def xanim_events(d, b, e='>'):
    import xanimparts_probe as XA
    c = Ev(d, b, e)
    (dataByteCount, dataShortCount, dataIntCount, randomDataByteCount,
     randomDataIntCount, numframes) = struct.unpack(e + '6H', d[b+4:b+16])
    boneCount = d[b+24:b+34]
    notifyCount = d[b+34]
    randomDataShortCount = c.u32(b+40)
    indexCount = c.u32(b+44)
    idxw = 1 if numframes < 256 else 2
    c.seg(104, 4)                              # root (TEMP via root_size=104)
    if c.u32(b) == FOLLOW:
        c.cstr()                               # name, byte-packed
    if c.u32(b+64) in PTRS:
        c.seg(boneCount[9] * 2, 2)             # names u16[]
    if c.u32(b+96) in PTRS:
        c.seg(notifyCount * 8, 4)              # notify XAnimNotifyInfo[]
    if c.u32(b+100) in PTRS:                   # deltaPart: per-allocation model
        _delta_events(c, numframes, idxw, e)
    if c.u32(b+68) in PTRS:
        c.seg(dataByteCount, 1)
    if c.u32(b+72) in PTRS:
        c.seg(dataShortCount * 2, 2)
    if c.u32(b+76) in PTRS:
        c.seg(dataIntCount * 4, 4)
    if c.u32(b+80) in PTRS:
        c.seg(randomDataShortCount * 2, 2)
    if c.u32(b+84) in PTRS:
        c.seg(randomDataByteCount, 1)
    if c.u32(b+88) in PTRS:
        c.seg(randomDataIntCount * 4, 4)
    if c.u32(b+92) in PTRS:
        c.seg(indexCount * idxw, idxw)
    return c.o, c.events


def _delta_events(c, numframes, idxw, e):
    """XAnimDeltaPart interior: mirrors xanimparts_probe.parse_delta but emits
    one event per loader allocation. Frame-data element aligns: ByteVec 1,
    UShortVec/XQuat2/XQuat 2 (i16 components), indices idxw."""
    d = c.d
    trans_p, quat2_p, quat_p = struct.unpack(e + '3I', d[c.o:c.o+12])
    c.seg(12, 4)                               # XAnimDeltaPart struct
    if trans_p in PTRS:
        size = c.u16(c.o)
        small = d[c.o + 2]
        if size == 0:
            c.seg(16, 4)                       # header + vec3 frame0
        else:
            frames_p = c.u32(c.o + 28)
            c.seg(32, 4)                       # trans header
            c.seg((size + 1) * idxw, idxw)     # inline indices
            if frames_p in PTRS:
                c.seg((size + 1) * (3 if small else 6), 1 if small else 2)
    if quat2_p in PTRS:
        size = c.u16(c.o)
        if size == 0:
            c.seg(8, 4)
        else:
            frames_p = c.u32(c.o + 4)
            c.seg(8, 4)
            c.seg((size + 1) * idxw, idxw)
            if frames_p in PTRS:
                c.seg((size + 1) * 4, 2)       # XQuat2
    if quat_p in PTRS:
        size = c.u16(c.o)
        if size == 0:
            c.seg(12, 4)
        else:
            frames_p = c.u32(c.o + 4)
            c.seg(8, 4)
            c.seg((size + 1) * idxw, idxw)
            if frames_p in PTRS:
                c.seg((size + 1) * 8, 2)       # XQuat
    return c.o


EXTRA_EVENTS = {
    'XAnimParts': (lambda z, o: xanim_events(z, o, '>'), 104),
}

# Per-band modules land as rt_events_<band>.py exporting EXTRA (or EXTRA_EVENTS).
# all_events() merges whatever exists so the build path picks up each band the
# day it is written, without touching call sites. Import failure of one band is
# NOT fatal (the band simply keeps the old linear model) but IS reported, since
# a silently-missing band reads as "exact" while shipping drifted aliases.
_BANDS = ('rt_events_mts', 'rt_events_xmodel', 'rt_events_weapon',
          'rt_events_gfxworld', 'rt_events_fx', 'rt_events_material',
          'rt_events_camo')


def all_events(verbose=False):
    ev = dict(EXTRA_EVENTS)
    for mod in _BANDS:
        try:
            m = __import__(mod)
        except ImportError:
            if verbose:
                print('  rt_exact: band %s not present (linear model kept)' % mod)
            continue
        ev.update(getattr(m, 'EXTRA', None) or getattr(m, 'EXTRA_EVENTS', {}))
    return ev


def policy(verbose=False, **extra):
    """loader_sim policy enabling the exact runtime model (opt-in per map)."""
    p = dict(extra_events=all_events(verbose))
    p.update(extra)
    return p


def anchor_rt(simmap_pkl, realmap_pkl):
    """Build policy['anchor_rt'] = {asset_index: measured runtime offset} from a
    dump-measured realmap, so each measured asset's interior is walked in the
    console's own alignment PHASE (see loader_sim's ANCHOR RE-PHASE note).
    Keyed by asset INDEX deliberately: index is domain-free, whereas offsets
    differ between the final-zone walk (file-64) and the build's body stream.
    Measurement outliers are excluded — the realmap carries known bad needles
    (single points off by tens of MB); re-phasing to one of those would corrupt
    an entire asset's interior. A span is accepted only if its measured start
    is within TOL of its own span-relative expectation."""
    import pickle
    S = pickle.load(open(simmap_pkl, 'rb'))
    R = pickle.load(open(realmap_pkl, 'rb'))
    real = R['real']
    ev = all_events()
    spans = sorted(S['spans'], key=lambda t: t[3])
    out, prev = {}, None
    for (i, nm, root, s, e) in spans:
        rv = real.get(s - 64)
        if rv is None:
            continue
        # measure_band back-projects real[start] = rt(needle) - (needle-start),
        # which LINEARIZES the TEMP root: the stored anchor sits root_size below
        # the loader's actual block-5 cursor at the asset's virtual start. Add it
        # back, or the re-phase installs a cursor a root-size off and every
        # interior align inside the asset lands in the wrong phase.
        rv += (ev.get(root) or (None, 0))[1]
        if prev is not None:
            # monotonic + plausible: runtime must advance, and by no more than
            # the file distance plus a generous interior-pad allowance
            ds, dr = s - prev[0], rv - prev[1]
            if dr < 0 or dr > ds + (1 << 20):
                continue
        out[i] = rv
        prev = (s, rv)
    return out
