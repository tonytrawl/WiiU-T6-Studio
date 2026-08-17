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
# all_events() merges them. Rule (J), baked 2026-07-30: a MISSING BAND IS A BUILD ERROR,
# not a silent fallback — a silently-missing band reads as "exact" while shipping drifted
# aliases (the omission of rt_events_fx was the root of the whole FX-visuals front).
# `allow_missing=True` is the explicit escape hatch for exploratory tooling only.
_BANDS = ('rt_events_mts', 'rt_events_xmodel', 'rt_events_weapon',
          'rt_events_gfxworld', 'rt_events_fx', 'rt_events_material',
          'rt_events_camo', 'rt_events_misc', 'rt_events_snd',
          'rt_events_light')


def all_events(verbose=False, allow_missing=False):
    ev = dict(EXTRA_EVENTS)
    missing = []
    for mod in _BANDS:
        try:
            m = __import__(mod)
        except ImportError as e:
            missing.append((mod, str(e)))
            if verbose:
                print('  rt_exact: band %s NOT PRESENT' % mod)
            continue
        ev.update(getattr(m, 'EXTRA', None) or getattr(m, 'EXTRA_EVENTS', {}))
    if missing and not allow_missing:
        raise ImportError('rule (J): rt band(s) missing — a missing band silently ships '
                          'drifted aliases for its whole asset class. %s. Pass '
                          'allow_missing=True ONLY for exploratory tooling.'
                          % ', '.join('%s (%s)' % m for m in missing))
    return ev


def coverage_report(emitted_types, events=None):
    """Rule (J) second half: which EMITTED asset types have no event band? Returns the list
    of uncovered types — the build path should print it and treat non-empty as a warning
    at minimum (types relocated by the coarse linear model)."""
    ev = events if events is not None else all_events(allow_missing=True)
    return sorted(set(emitted_types) - set(ev))


def policy(verbose=False, **extra):
    """loader_sim policy enabling the exact runtime model (opt-in per map).

    ⭐⭐⭐⭐ (GM) THE GLASSES EVENT MODEL IS ON BY DEFAULT since 2026-08-16.

    It was specced and validated on 2026-08-10 and then left unarmed as
    "rebake-scoped". Leaving a KNOWN-CORRECT model switched off cost boots 87 and
    88: without it loader_sim charges no pixel pads for a Glasses span, so its
    runtime offsets run 24,576 bytes LOW for everything downstream, and every
    alias we mint past that point resolves 24 KB early. b88 crashed with
    WeaponDef+916 landing on the cstring "viewmodel_default_idle".

    PROVEN AGAINST REAL GUEST MEMORY (b88 dump, pointer-free windows compared
    byte-for-byte): before, 96 of 349 decidable probes were +24,576 off and every
    one of them was past the Glasses span; after, 359 of 361 are EXACT and the
    residual 2 sit at +2, the baseline-convention residue the spec already names.

    ⛔ IT IS THE DEFAULT ON PURPOSE. An opt-in correctness flag is exactly the
    (FN) trap that hid this for the whole campaign -- two code paths disagreeing
    about the same coordinate, each validating itself. Pass
    `policy(glasses_events=None)` to restore the old behaviour deliberately, and
    say why in the call site.
    """
    import glasses_events as _GE
    p = dict(extra_events=all_events(verbose),
             glasses_events=_GE.events_for_loader_sim)
    p.update(extra)                       # caller may override, incl. with None
    return p


def anchor_rt(simmap_pkl, realmap_pkl, needle_model=None):
    """Build policy['anchor_rt'] = {asset_index: measured runtime offset} from a
    dump-measured realmap, so each measured asset's interior is walked in the
    console's own alignment PHASE (see loader_sim's ANCHOR RE-PHASE note).
    Keyed by asset INDEX deliberately: index is domain-free, whereas offsets
    differ between the final-zone walk (file-64) and the build's body stream.
    Measurement outliers are excluded — the realmap carries known bad needles
    (single points off by tens of MB); re-phasing to one of those would corrupt
    an entire asset's interior. A span is accepted only if its measured start
    is within TOL of its own span-relative expectation.

    ⚠ THIS FUNCTION IS THE SINGLE SOURCE OF ANCHOR *ACCEPTANCE*. Reuse the set of
    indices it accepts (`measured_rtmap.MeasuredRuntimeMap` does); do NOT assume its
    VALUES are right for your consumer — see below.

    ⛔⛔ THE `rv += root_size` BELOW IS PER-CLASS CORRECT, NOT UNIVERSALLY CORRECT.
    Measured 2026-08-08 against the plain allocation-exact model (itself validated
    4/4 on the dump-measured golden points and 18/18 on genuine transit techsets),
    at ACCEPTED anchors, `model_rt(span start)` vs the stored value:
        WeaponVariantDef   == RAW              50/58 exact   (phased 0/58)
        XAnimParts         == RAW + 104       299/1336       (raw 3/1336)
        FxEffectDef        == RAW + 76        105/177        (raw 0/177)
    i.e. the correction is RIGHT for XAnim/Fx/Destructible and DOUBLE-COUNTED for
    WeaponVariantDef, where `rt_events_weapon.ANCHOR_BIAS` (= VARIANT_SIZE 716)
    already applies it inside the band model. That double-count — NOT any
    "frame" difference — is why feeding these values to MeasuredRuntimeMap breaks
    the golden points by exactly +716 (4/4 EXACT -> 1/4). An earlier note here
    called it a two-frames law; that explanation was WRONG and is retracted.
    ⚠ THE CONVERSE IS ALSO A LIVE DEFECT, currently UNFIXED: MeasuredRuntimeMap
    consumes RAW, so it is one root-size LOW at every non-WVD anchor (XAnimParts
    -104, XModel -244, FxEffectDef -76, MaterialTechniqueSet -136, Destructible
    -24) and exactly right only for WeaponVariantDef. **The 4 golden points CANNOT
    see this: all four live inside a WeaponVariantDef, and 2,217 of 2,275 accepted
    anchors (97%) are outside that class.** Fixing it needs a measured oracle in a
    non-WVD band — do not "correct" it against the existing gates, which are
    structurally blind to it (ST-dedup is self-consistency with aliases this same
    map baked, so it cannot detect a systematic offset either)."""
    return {i: rv for (i, root, s, rv, ok, why)
            in _anchor_scan(simmap_pkl, realmap_pkl, needle_model)
            if ok}


#: reject reason codes emitted by `_anchor_scan` / `anchor_rt_report`
ANCHOR_REJECT_BACKWARD = 'backward'    # dr < 0: block-5 cursor cannot run backwards
ANCHOR_REJECT_TOLERANCE = 'tolerance'  # dr > ds + TOL: runtime outran file distance
ANCHOR_REJECT_NEEDLE = 'needle'        # back-projection crosses non-linear consumption
ANCHOR_TOL = 1 << 20


def _anchor_scan(simmap_pkl, realmap_pkl, needle_model=None):
    """Yield (index, root, file_start, phased_rv, accepted, reason) per measured
    span, in file order. The ONE implementation of the acceptance test; both
    `anchor_rt` and `anchor_rt_report` are thin readers of it, so a reject can
    never be reported under a different rule than the one that rejected it.

    NEEDLE REFUSAL (2026-08-09, mechanism-backed, rule BZ — no fitted constants):
    `measure_band` stores real[start] = rt(needle) - (needle - start), a LINEAR
    back-projection. The measured E(F) curve is STEP-SHAPED (zm_nuked GfxWorld:
    -1,081 at rel 0 .. +225,379 at matmem), so an anchor is exactly as good as
    the linearity of block-5 consumption between the span start and its needle.
    With `needle_model` supplied (a callable file_offset -> plain-model runtime
    b5, built from an exact-policy simulate of the SAME zone the realmap was
    measured against), an anchor whose needle offset noff satisfies
        model(s + noff) - model(s + root) != noff - root
    is REFUSED — the back-projection provably crossed a pad/TEMP divergence and
    the stored value is displaced by exactly that amount. Needles at/inside the
    root pass by construction (nothing consumed yet). A span with NO recorded
    needle offset is UNKNOWN and keeps today's acceptance behavior — refusing
    on absence would reject every pre-needle realmap wholesale. Default
    needle_model=None is byte-identical to the previous behavior."""
    import pickle
    S = pickle.load(open(simmap_pkl, 'rb'))
    R = pickle.load(open(realmap_pkl, 'rb'))
    real = R['real']
    needles = R.get('needle') or {}          # absence == UNKNOWN, never rel 0
    ev = all_events()
    spans = sorted(S['spans'], key=lambda t: t[3])
    prev = None
    for (i, nm, root, s, e) in spans:
        rv = real.get(s - 64)
        if rv is None:
            continue
        # measure_band back-projects real[start] = rt(needle) - (needle-start),
        # which LINEARIZES the TEMP root: the stored anchor sits root_size below
        # the loader's actual block-5 cursor at the asset's virtual start. Add it
        # back, or the re-phase installs a cursor a root-size off and every
        # interior align inside the asset lands in the wrong phase.
        root_sz = (ev.get(root) or (None, 0))[1]
        rv += root_sz
        if needle_model is not None:
            noff = needles.get(s - 64)
            if noff is not None and noff > root_sz:
                nonlin = ((needle_model(s + noff) - needle_model(s + root_sz))
                          - (noff - root_sz))
                if nonlin != 0:
                    yield (i, root, s, rv, False,
                           (ANCHOR_REJECT_NEEDLE, nonlin, noff))
                    continue
        if prev is not None:
            # monotonic + plausible: runtime must advance, and by no more than
            # the file distance plus a generous interior-pad allowance
            ds, dr = s - prev[0], rv - prev[1]
            if dr < 0:
                yield (i, root, s, rv, False, (ANCHOR_REJECT_BACKWARD, dr, ds))
                continue
            if dr > ds + ANCHOR_TOL:
                yield (i, root, s, rv, False, (ANCHOR_REJECT_TOLERANCE, dr, ds))
                continue
        yield (i, root, s, rv, True, None)
        prev = (s, rv)


def anchor_rt_report(simmap_pkl, realmap_pkl, verbose=True, needle_model=None):
    """Per-reject REASON CODES, so a wrongly-rejected genuine anchor is
    discoverable instead of silent (rule (CG): a filter that reports only a count
    cannot be audited).

    ⭐ MEASURED 2026-08-08 — IS `ANCHOR_TOL` LOAD-BEARING? On all three realmaps the
    accepted and tolerance-rejected populations are BIMODAL with an EMPTY BAND
    between them, so the constant is not fitted to a continuum:
        realmap      max excess ACCEPTED     min excess REJECTED
        _zmnuked          785,626                1,079,246
        _skate6            61,487                1,145,802
        _skate2           914,466                1,208,306
    Every threshold in [914,467 .. 1,079,246] yields the IDENTICAL partition on all
    three; `ANCHOR_TOL` = 1,048,576 sits inside that window. ⚠ FINISH THE SENTENCE
    (rule CG): this filter would still reject a GENUINE anchor whose runtime start
    outruns its file distance by 914 KB - 1.03 MB. None does in any measured map,
    but that is evidence, not proof — the documented worst legitimate runtime-only
    growth is GfxWorld's 246,063 B of 8 KB pre-image alignment pads, ~4x under the
    bound. If a map ever lands an anchor in that band, DERIVE the ceiling from the
    asset class rather than widening the constant."""
    rej = [(i, root, s, why) for (i, root, s, rv, ok, why)
           in _anchor_scan(simmap_pkl, realmap_pkl, needle_model) if not ok]
    by = {}
    for (i, root, s, (code, dr, ds)) in rej:
        by.setdefault(code, []).append((i, root, dr, ds, dr - ds))
    if verbose:
        for code in sorted(by):
            rows = by[code]
            print('  %-10s %4d rejects  worst %+d' % (code, len(rows),
                  max((r[2] for r in rows), key=abs) if code == ANCHOR_REJECT_BACKWARD
                  else max(r[4] for r in rows)))
            for (i, root, dr, ds, ex) in sorted(rows, key=lambda r: r[4])[:5]:
                print('     span %-6d %-22s dr %+13d ds %12d excess %+12d'
                      % (i, root[:22], dr, ds, ex))
    return by
