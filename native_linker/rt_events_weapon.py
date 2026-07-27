"""EXACT runtime-allocation events for console WeaponVariantDef (2026-07-26).

Band: the 79 measured WeaponVariantDef anchors in the zm_nuked boot realmap
showed a NEARLY CONSTANT +612+-3 delta at every weapon (zero 0/79) under the
old model (loader_sim dispatched weapons through the body_relayout DELIMITER
path: ONE verbatim region + temp_next=716).  Regression of the realmap against
the walk decomposed that into TWO independent facts.

FACT 1 - INLINE SUB-ASSET ROOTS DIVERT TO TEMP (a real allocation miss)
  Every inline sub-ASSET inside a weapon loads its ROOT into the TEMP block,
  exactly like the XModel inline-Material / MapEnts / PhysPreset classes of the
  T6 load db - file bytes that consume NO block-5 space.  The verbatim model
  streamed them 1:1, so the sim over-allocated by sum(inline roots) per weapon.
  Evidence (74 weapon body gaps, realmap-derived, root-convention corrected):
    * weapons with NO inline sub-asset          -> gap  -5 .. -1
    * weapons with 1 inline Material            -> gap +406..+429  (104 mat
      root + 328 GfxImage record = 432; the material's textureTable/stateBits/
      names stay virtual)
    * 2 / 3 inline Materials                    -> +842/+847, +1272/+1284
    * 2 / 4 / 8 inline WeaponAttachmentUnique   -> +841..+843, +1678,
      +3373/+3380  (= n * 424 console root)
  Console root sizes = the loader Load_Stream r5 ground truth (weapon_convert):
  Material 104, GfxImage 328, XModel 244, WeaponAttachment 284,
  WeaponAttachmentUnique 424, WeaponCamo 28, TracerDef 128, FxEffectDef 76,
  techset 136.  The inline WeaponDef struct (2836 = PC 2448 + the 388-B WiiU
  tail) is NOT an asset: it is part of the parent and stays VIRTUAL.

FACT 2 - THE +612 ITSELF IS THE ANCHOR CONVENTION, NOT OVER-ALLOCATION
  measure_band derives each anchor by needle back-projection
      real[start] = found_rt(needle) - (needle - start)
  i.e. it LINEARIZES the TEMP root: a span anchor lands root_size BELOW the
  block-5 cursor V, while loader_sim registers the anchor AT V.  So
  err(anchor) = drift + root_size(type), and the band metric (difference of
  consecutive anchor errors) carries root(T) - root(prev) at every type
  BOUNDARY.  Same-type runs cancel (that is why XAnimParts could be driven to
  0); scattered types expose the difference.  Measured on this realmap:
      XAnimParts(104) -> Weapon = +612 = 716-104   (54 pairs, exact)
      RawFile(12)     -> Weapon = +704 = 716-12
      FxEffectDef(76) -> Weapon = +640 = 716-76
      Weapon -> WeaponCamo(28)  = -688 = 28-716
      FxEffectDef(76) -> XAnimParts(104) = +28     (independent confirmation)
  Fix: register the weapon start at V - ANCHOR_BIAS via a zero-allocation
  skip(-b)/skip(+b) pair around the TEMP root, so weapon anchors carry the SAME
  bias as the dominant predecessor convention (XAnimParts, 104).  Net virtual
  allocation is unchanged and every non-root omap entry is untouched.
  ANCHOR_BIAS = VARIANT_SIZE (716) is the STRICTLY correct linearized
  convention (it is what MeasuredRuntimeMap already does for every span);
  it only pays off once every type registers the same way, so the default is
  the 612 form that matches today's mixed convention.
  2026-07-26 UPDATE: a FLAT 716 is still one align-4 pad short - the realmap
  needle lands at/after the inline weapDef, so the projection also carries the
  loader's AllocStreamPos(3) pad:  real[start] = V(weapDef) - nameLen - 716.
  weapon_walk now emits that pad BEFORE the root (net allocation unchanged,
  every address from weapDef on unchanged), which makes the composed weapDef
  prediction phase-invariant and exact.  See the ANCHOR_BIAS block below.

RESULT on zm_nuked_authored.zone (harness = rt_events_exact + this module):
  WeaponVariantDef band 79 anchors: median +613 -> +1, zero 0 -> 15, |d|<=2
  0 -> 44.  The residual classes are enumerated at the bottom of this file.

Bonus fix: the delimiter path made exactly ONE omap registration per weapon
(at the TEMP root), so every weapon-interior stream offset resolved 716 bytes
too high - any dedup alias / rtmap pointer into a weapon interior baked past
its target.  Per-allocation registration removes that.

Usage: merge into the loader_sim policy seam:
    EX = dict(rt_events_exact.EXTRA_EVENTS); EX.update(rt_events_weapon.EXTRA_EVENTS)
    policy = dict(extra_events=EX, ...)
"""
import struct

from alloc_events import Ev, PTRS

FOLLOW = 0xFFFFFFFF

import weapon_convert as WC

VARIANT_SIZE = WC.VARIANT_SIZE            # 716
WEAPONDEF_CO = WC.WEAPONDEF_CO            # 2836 = 2448 + 388 tail
ATTACH_SIZE = WC.ATTACH_SIZE              # 284
ATTACHU_SIZE = WC.ATTACHU_SIZE            # 424
FLAMETABLE_SIZE = WC.FLAMETABLE_SIZE      # 484
NUM_WEAP_ANIMS = WC.NUM_WEAP_ANIMS        # 88
SURF_TYPE_NUM = WC.SURF_TYPE_NUM          # 32
HITLOC_COUNT = WC.HITLOC_COUNT            # 21
NOTETRACK_COUNT = WC.NOTETRACK_COUNT      # 20

# console sub-asset root sizes (Load_Stream r5 / measured console bodies)
MAT_ROOT = 104
IMG_ROOT = 328          # inline console GfxImage record (xmodel_probe IMG_SIZE)
XMODEL_ROOT = 244       # measured console XModel body (body_relayout NAME_AT)
FX_ROOT = 76
TECHSET_ROOT = 136
TRACER_ROOT = 128
CAMO_ROOT = 28

# anchor-bias alignment: weapon anchors register at V - ANCHOR_BIAS so they
# carry the same bias as the XAnimParts measurement convention (716 - 104).
# 2026-07-26: was VARIANT_SIZE-104 (tuned so the BAND METRIC matched the
# dominant XAnimParts(104) predecessor convention). The band metric is itself
# convention-biased, so it was the wrong judge. Against the CONVENTION-FREE
# test (_rt_acceptance.py: composed measured-anchor + exact-interior map vs 4
# dump-measured absolute addresses) VARIANT_SIZE is right and 612 is not:
#   bias 612 -> weapon sites err -103/-99/-99   (= -(716-612), a uniform shift)
#   bias 716 -> weapon sites err   +1/+5/+5
#   bias 0   -> weapon sites err -715/-711/-711
# This is the strictly-correct linearized form (what MeasuredRuntimeMap already
# does for every span) and it is what makes the composed map usable.
#
# 2026-07-26 (later, this lane): the FLAT 716 is still one align-4 pad short.
# The realmap needle for a weapon lands AT OR AFTER the inline weapDef, so the
# back-projection carries the loader's AllocStreamPos(3) pad in front of it:
#       real[start] = V(weapDef) - len(szInternalName) - 716
# CONVENTION-FREE PROOF (no band medians): for every pair of ADJACENT spans that
# are BOTH measured WeaponVariantDefs, replay weapon A's event list from its own
# true cursor and compare the end against weapon B's measured anchor.  A's body
# model is then judged with NOTHING but two dump-measured absolute addresses.
#       flat 716            : 1 / 10 pairs exact  (six sit at a constant -3)
#       716 - pad(weapDef)  : 8 / 10 pairs exact  (remaining 2 are the deep-needle
#                             class, +814 / +1127, see RESIDUALS)
# and the same correction takes the composed weapDef acceptance point from +1 to
# +0 (0x77D0BD8 exactly).  It is implemented in weapon_walk as a pad emitted
# BEFORE the root instead of between the name and weapDef - net allocation and
# every address from weapDef on are unchanged, so it is a pure anchor move.
ANCHOR_BIAS = VARIANT_SIZE


class _W(Ev):
    """Ev with labeled section recording (mask/measure tooling)."""
    def __init__(self, d, base, e):
        Ev.__init__(self, d, base, e)
        self.sections = []                 # (label, abs_start, abs_end, kind)
        self.ptr_words = []                # abs offsets of pointer WORDS

    def sec(self, label, size, kind='seg', align=1):
        s = self.o
        if size <= 0:
            return
        if kind == 'temp':
            self.temp(size)
        else:
            self.seg(size, align)
        self.sections.append((label, s, s + size, kind))

    def sec_cstr(self, label):
        s = self.o
        self.cstr()
        self.sections.append((label, s, self.o, 'seg'))

    def mark_ptrs(self, base, offs):
        for o in offs:
            self.ptr_words.append(base + o)


def _xstring(c, v, label):
    if v == FOLLOW:
        c.sec_cstr(label)


# ------------------------------------------------------------ inline sub-assets
PIXEL_ALIGN = 8192      # Load_GfxImage +0x5c: DB_AllocStreamPos(0x1fff)


def _sub_image(c):
    """Inline console GfxImage.  Load_GfxImage@0x021B124C, verified against the
    RPL disassembly (and _rpl_align_table.json site 0x5c raw=8191):

        Load_Stream(atStart, img, 0x148)        -> 328-byte record, CALLER's
                                                   block = TEMP (Load_GfxImagePtr
                                                   @0x021B1504 pushes block 0)
        DB_PushStreamPos(5)                     -> every follower is VIRTUAL
        Load_XString(img+0x140 = 320)           -> name
        if u32(img+0xb0 = 176) != 0:            -> pixel pointer word
            DB_AllocStreamPos(0x1fff)           -> ALIGN 8192
            Load_Stream(1, pixels, u32(img+0xa0 = 160))
        Load_Stream(0, img+0, 0x9c) + Load_WiiuTexture   (no allocation)
        DB_PopStreamPos

    DEFECT D1 (fixed 2026-07-26): the pixel blob used to be modelled as
    `temp(size)` gated on the streaming byte at +171.  BOTH halves were wrong.
      * BLOCK: the loader never leaves block 5 for the pixels, so they are a
        VIRTUAL seg that DOES consume runtime space (a temp() consumed none).
      * ALIGN: the AllocStreamPos(0x1fff) pad (up to 8191 bytes!) was missing
        entirely, so every allocation after an inline pixel blob was modelled
        low by an unbounded amount.
      * GATE: there is no streaming test and no FOLLOW/-1 test in the code —
        it is a plain `!= 0` on the pointer word (`cmpwi r0,0; beq`), so an
        image marked "streamed" whose pixel word is non-zero still streams.
    zm_nuked exercise: all 51 inline GfxImages reached through weapon subtrees
    carry pixel word 0 (streamed) -> this fix is a no-op on THIS zone (identical
    spans, identical omap, identical acceptance) and a correctness fix for any
    zone that inlines resident pixels."""
    b = c.o
    c.sec('img.rec', IMG_ROOT, 'temp')
    if c.u32(b + 320) in PTRS:
        c.sec_cstr('img.name')
    if c.u32(b + 176) != 0:
        c.sec('img.pixels', c.u32(b + 160), 'seg', PIXEL_ALIGN)


def _sub_material(c):
    """Inline console Material: root 104 -> TEMP; name/tables virtual with the
    loader's table aligns (const 16 / statebits 8); per-texture inline image."""
    b = c.o
    tc, cc, sbc = c.d[b + 72], c.d[b + 73], c.d[b + 74]
    tsp, ttp, ctp, sbp, th = (c.u32(b + 80), c.u32(b + 84), c.u32(b + 88),
                              c.u32(b + 92), c.u32(b + 96))
    c.sec('mat.root', MAT_ROOT, 'temp')
    if c.u32(b) in PTRS:
        c.sec_cstr('mat.name')
    if tsp in PTRS:                        # inline techset asset
        import shader_probe
        ts = c.o
        end, _ = shader_probe.parse_techset(c.d, ts)
        c.sec('mat.techset.root', TECHSET_ROOT, 'temp')
        c.sec('mat.techset.rest', end - c.o, 'seg', 4)
    if ttp in PTRS:
        defs = c.o
        c.sec('mat.textable', tc * 16, 'seg', 4)
        for i in range(tc):
            if c.u32(defs + i * 16 + 12) in PTRS:
                _sub_image(c)
    if ctp in PTRS:
        c.sec('mat.const', cc * 32, 'seg', 16)
    if sbp in PTRS:
        c.sec('mat.statebits', sbc * 8, 'seg', 8)
    if th in PTRS:
        _sub_material(c)


# Delegate a weapon-inline XModel's INTERIOR to rt_events_xmodel (the band that
# owns XModel) instead of one packed remainder seg?  MEASURED: today it makes
# the attachment-heavy weapons WORSE (sum|body-gap| over 73 weapons: coarse
# 118,676 vs delegated 274,718; the difference is dominated by that walker's
# img_pix -> TEMP rule diverting whole inline pixel blobs).  Flip to True once
# the XModel band itself lands at median 0 - the delegation is the composable
# model, it is just not the accurate one yet.
XMODEL_INTERIOR = False


def _sub_xmodel(c):
    """Inline console XModel.  Root 244 -> TEMP; the INTERIOR is delegated to
    the dump-calibrated rt_events_xmodel walker (verts0 align 256, verts1 64,
    tris/collnodes 16, inline Material/GfxImage roots -> TEMP), spliced into
    this weapon's event list.  The coarse `packed remainder` model that used to
    stand here under-allocated the attachment-heavy weapons by 8-30 KB each
    (measured d_out on the 6 big zm_nuked weapons)."""
    if not XMODEL_INTERIOR:
        import xmodel_probe as XP
        s = c.o
        end = XP.parse_xmodel(c.d, s)[0]
        c.sec('xm.root', XMODEL_ROOT, 'temp')
        c.sec('xm.rest', end - c.o, 'seg', 4)
        return
    import rt_events_xmodel as RX
    base = c.o
    end, evs = RX.xmodel_events(c.d, base, c.e)
    delta = base - c.base
    for k, ev in enumerate(evs):
        if ev[0] == 'seg':
            _, rel, size, align = ev
            if k == 0:                                # root -> TEMP (nested)
                c.events.append(('temp', rel + delta, size))
            else:
                c.events.append(('seg', rel + delta, size, align))
        elif ev[0] == 'temp':
            c.events.append(('temp', ev[1] + delta, ev[2]))
        else:
            c.events.append(ev)
    c.sections.append(('xm.inline', base, end, 'mixed'))
    c.o = end


def _sub_fx(c):
    import fx_probe as FP
    s = c.o
    end = FP.parse_fx(c.d, s)[0]
    c.sec('fx.root', FX_ROOT, 'temp')
    c.sec('fx.rest', end - c.o, 'seg', 4)


_GENERIC = [None]


def _generic_end(z, root, o):
    """Span of an inline sub-asset via the generic console grammar walk
    (body_relayout.ReEmitter on a throwaway writer)."""
    if _GENERIC[0] is None:
        import body_relayout as BR
        import struct_layout
        import walker as W
        import zone_stream as zs
        Lc = struct_layout.Layout(W.HDR, console=True)
        zc = W.ZoneCode(W.ZC_DIR)
        _GENERIC[0] = (BR, Lc, zc, zs)
    BR, Lc, zc, zs = _GENERIC[0]
    em = BR.ReEmitter(z, Lc, zc, zs.ZoneWriter())
    return em.emit_asset(root, o)


def _sub_tracer(c):
    s = c.o
    end = _generic_end(c.d, 'TracerDef', s)
    c.sec('tracer.root', TRACER_ROOT, 'temp')
    c.sec('tracer.rest', end - c.o, 'seg', 4)


def _sub_camo(c):
    s = c.o
    end = _generic_end(c.d, 'WeaponCamo', s)
    c.sec('camo.root', CAMO_ROOT, 'temp')
    c.sec('camo.rest', end - c.o, 'seg', 4)


_SUB = {'material': _sub_material, 'xmodel': _sub_xmodel, 'fx': _sub_fx,
        'tracer': _sub_tracer, 'camo': _sub_camo}


def _sub(c, kind):
    _SUB[kind](c)


# ------------------------------------------------------------ weapon stream
def _xstring_array(c, count, label):
    base = c.o
    c.mark_ptrs(base, range(0, 4 * count, 4))
    ptrs = struct.unpack_from(c.e + '%dI' % count, c.d, base)
    c.sec(label + '.ptrs', 4 * count, 'seg', 4)
    for v in ptrs:
        if v == FOLLOW:
            c.sec_cstr(label + '.str')


def _attachment(c):
    a = c.o
    A = lambda o: c.u32(a + o)
    c.mark_ptrs(a, WC.ATTACH_PTR_OFFS)
    c.sec('attachment', ATTACH_SIZE, 'temp')   # sub-asset root -> TEMP
    if A(0) == FOLLOW:
        c.sec_cstr('attachment.name')
    if A(4) == FOLLOW:
        c.sec_cstr('attachment.disp')


def _attachment_unique(c):
    a = c.o
    A = lambda o: c.u32(a + o)
    c.mark_ptrs(a, WC.ATTACHU_PTR_OFFS)
    c.sec('attachU', ATTACHU_SIZE, 'temp')     # sub-asset root -> TEMP
    if A(0) == FOLLOW:
        c.sec_cstr('attachU.name')
    if A(20) == FOLLOW:
        c.sec_cstr('attachU.alt')
    if A(28) == FOLLOW:
        c.sec_cstr('attachU.dw')
    if A(36) == FOLLOW:
        c.sec('attachU.hideTags', 64, 'seg', 2)
    for mo in (40, 44, 48, 52, 56):
        if A(mo) in PTRS:
            _sub(c, 'xmodel')
    if A(60) == FOLLOW:
        c.sec_cstr('attachU.vmTag')
    if A(64) == FOLLOW:
        c.sec_cstr('attachU.wmTag')
    if A(164) in PTRS:
        _sub(c, 'camo')
    if A(196) in PTRS:
        _sub(c, 'material')
    if A(200) in PTRS:
        _sub(c, 'material')
    if A(232) == FOLLOW:
        _xstring_array(c, NUM_WEAP_ANIMS, 'attachU.xanims')
    if A(248) == FOLLOW:
        c.sec('attachU.locDmg', 21 * 4, 'seg', 4)
    for so in range(256, 312, 4):
        if A(so) == FOLLOW:
            c.sec_cstr('attachU.snd')
    for so in (316, 320):
        if A(so) in PTRS:
            _sub(c, 'fx')
    for so in (324, 328):
        if A(so) in PTRS:
            _sub(c, 'tracer')


def _ptr_array_assets(c, count, label, kind):
    base = c.o
    c.mark_ptrs(base, range(0, 4 * count, 4))
    ptrs = struct.unpack_from(c.e + '%dI' % count, c.d, base)
    c.sec(label + '.ptrs', 4 * count, 'seg', 4)
    for v in ptrs:
        if v in PTRS:
            if kind == 'attachment':
                _attachment(c)
            elif kind == 'attachmentUnique':
                _attachment_unique(c)
            else:
                _sub(c, kind)


def _flametable(c):
    ft = c.o
    F = lambda o: c.u32(ft + o)
    c.mark_ptrs(ft, WC.FLAMETABLE_PTR_OFFS)
    c.sec('flame', FLAMETABLE_SIZE, 'seg', 4)  # struct, NOT an asset: virtual
    if F(432) == FOLLOW:
        c.sec_cstr('flame.name')
    for mo in (436, 440, 444, 448, 452, 456, 460, 464):
        if F(mo) in PTRS:
            _sub(c, 'material')
    for so in (468, 472, 476, 480):
        if F(so) == FOLLOW:
            c.sec_cstr('flame.snd')


def _weapondef(c):
    wd = c.o
    W = lambda o: c.u32(wd + o)
    c.mark_ptrs(wd, WC.WEAPONDEF_PTR_OFFS)
    # console WeaponDef struct (2448 + 388 tail): VIRTUAL, align 4 (measured:
    # contiguous adj 0 across its whole extent; it is not an asset root)
    c.sec('weapDef', WEAPONDEF_CO, 'seg', 4)
    for op in WC._WD_STREAM:
        k, off = op[0], op[1]
        v = W(off)
        if k == 'xs':
            _xstring(c, v, 'wd.xs%d' % off)
        elif k == 'u16':
            if v == FOLLOW:
                c.sec('wd.u16_%d' % off, NOTETRACK_COUNT * 2, 'seg', 2)
        elif k == 'f32':
            if v == FOLLOW:
                c.sec('wd.f32_%d' % off, op[2] * 4, 'seg', 4)
        elif k == 'vec2':
            if v == FOLLOW:
                cnt = W(op[2])
                c.sec('wd.vec2_%d' % off, cnt * 8, 'seg', 4)
        elif k == 'xsarr':
            if v == FOLLOW:
                _xstring_array(c, op[2], 'wd.bounce')
        elif k == 'xmod':
            if v == FOLLOW:
                _ptr_array_assets(c, 16, 'wd.xmod%d' % off, 'xmodel')
        elif k == 'asset':
            if v in PTRS:
                _sub(c, op[2])
        elif k == 'flame':
            if v == FOLLOW:
                _flametable(c)


def weapon_walk(d, b, e='>'):
    """Console WeaponVariantDef at b -> _W with events/sections/ptr mask.
    Mirrors weapon_convert.convert_weapon (Load_WeaponVariantDef order)."""
    c = _W(d, b, e)
    P = lambda o: c.u32(b + o)
    c.mark_ptrs(b, WC.VARIANT_PTR_OFFS)
    # ---- anchor registration (see ANCHOR_BIAS notes above) -------------
    # The measured realmap back-projects a weapon's start as
    #     real[start] = rt(needle) - (needle - start)
    # and the needle lands AT OR AFTER the inline weapDef, so the projection
    # carries BOTH the 716-byte TEMP root and the align-4 pad the loader
    # inserts in front of weapDef:
    #     real[start] = V(weapDef) - len(szInternalName) - 716
    # (evidence: the ANCHOR_BIAS block above; regression: _pair_check()).
    # Registering the root at the flat V_root - 716 mispredicts by that pad.
    #
    # Emitting the pad BEFORE the root instead of between name and weapDef
    # makes the composed prediction PHASE-INVARIANT:
    #     skip(nameLen)        V_root + nameLen
    #     skip(0, align 4)     A = align4(V_root + nameLen)   <- true V(weapDef)
    #     skip(-(nameLen+716)) A - nameLen - 716              <- anchor registers here
    #     [root 716 -> TEMP]
    #     skip(+716)           A - nameLen
    #     name seg(nameLen)    A                (weapDef's own align-4 is a no-op)
    # Net virtual allocation and EVERY address from weapDef onward are byte-
    # identical to the old form; only the anchor (and the name string, by the
    # same pad) move.  composed(weapDef) - anchor is now nameLen + 716 whatever
    # the incoming cursor phase is - which is exactly GOLD - mrs (733 = 17+716
    # for defaultweapon_mp).
    name_len = 0
    if P(0) == FOLLOW:
        name_len = d.index(b'\x00', b + VARIANT_SIZE) + 1 - (b + VARIANT_SIZE)
    if P(8) == FOLLOW:                         # inline weapDef -> needle lands there
        c.events.append(('skip', name_len, 1))
        c.events.append(('skip', 0, 4))        # align-only: pin A = align4(...)
        c.events.append(('skip', -(name_len + ANCHOR_BIAS), 1))
    else:                                      # no weapDef: flat 716 convention
        c.events.append(('skip', -ANCHOR_BIAS, 1))
    c.sec('variant', VARIANT_SIZE, 'seg', 4)   # root: TEMP via root_size seam
    c.events.append(('skip', ANCHOR_BIAS, 1))
    _xstring(c, P(0), 'name')
    if P(8) == FOLLOW:
        _weapondef(c)
    _xstring(c, P(12), 'dispName')
    _xstring(c, P(16), 'altName')
    _xstring(c, P(20), 'attachUniqueName')
    if P(24) == FOLLOW:
        _ptr_array_assets(c, 63, 'attachments', 'attachment')
    if P(28) == FOLLOW:
        _ptr_array_assets(c, 95, 'attachmentUniques', 'attachmentUnique')
    if P(32) == FOLLOW:
        _xstring_array(c, NUM_WEAP_ANIMS, 'szXAnims')
    if P(36) == FOLLOW:
        c.sec('hideTags', 64, 'seg', 2)
    if P(40) == FOLLOW:
        _ptr_array_assets(c, 8, 'attachViewModel', 'xmodel')
    if P(44) == FOLLOW:
        _ptr_array_assets(c, 8, 'attachWorldModel', 'xmodel')
    if P(48) == FOLLOW:
        _xstring_array(c, 8, 'attachViewModelTag')
    if P(52) == FOLLOW:
        _xstring_array(c, 8, 'attachWorldModelTag')
    _xstring(c, P(508), 'ammoDisp')
    _xstring(c, P(512), 'ammoName')
    _xstring(c, P(520), 'clipName')
    for o2 in (596, 600, 604):
        if P(o2) in PTRS:
            _sub(c, 'material')
    return c


def weapon_events(d, b, e='>'):
    c = weapon_walk(d, b, e)
    return c.o, c.events


EXTRA_EVENTS = {
    'WeaponVariantDef': (lambda z, o: weapon_events(z, o, '>'), VARIANT_SIZE),
}


# =====================================================================
# THE weapDef -> attachments[63] SPAN: 3360 vs 3356  (2026-07-26, this lane)
# =====================================================================
# The composed map used to read +5 at attachments[63]/attachmentUniques[95]:
# +1 inherited from the anchor (fixed above) and +4 from the interior.  The +4
# is NOT an extra allocation in this module.  Full byte-by-byte audit of
# defaultweapon_mp's event list against Load_WeaponVariantDef / Load_WeaponDef /
# Load_Material in the RPL - EVERY size, order and align matches:
#
#   V+0     weapDef            2836  al 4   (Load_WeaponDef, AllocStreamPos(3))
#   +2836   gunXModel[16]        64  al 4
#   +2900   notetrackKeys[20]    40  al 2
#   +2940   szParentWeaponName   12  al 1
#           Material root  -> TEMP (104)
#   +2952   mat.name             19  al 1
#   +2972   mat.textureTable     16  al 4   (pad 1;  Load_Material +0x9c)
#           GfxImage root  -> TEMP (328)
#   +2988   img.name             11  al 1
#   +3000   mat.stateBits         8  al 8   (pad 1;  Load_Material +0x33c raw=7)
#   +3008   worldModel[16]       64  al 4
#           Material root  -> TEMP (104)
#   +3072   mat.name             22  al 1
#   +3096   mat.textureTable     16  al 4   (pad 2)
#           GfxImage root  -> TEMP (328)
#   +3112   mat.stateBits         8  al 8   (pad 0)
#   +3120   parallelBounce[32]  128  al 4
#   +3248   locDmgMultipliers    84  al 4
#   +3332   szDisplayName        21  al 1
#   +3356   attachments[63]     252  al 4   (pad 3)      <== 3356 EXACTLY
#           attachmentUniques[95] 380 al 4  (pad 0)      <== +252 EXACTLY
# Replaying that chain from the dump-measured V(weapDef)=0x77D0BD8 lands
# attachments[63] on 0x77D18F4 and attachmentUniques[95] on 0x77D19F0 - both
# golden points, to the byte.  So the model is exact.
#
# The +4 is a PHASE leak: mat.stateBits is align 8, so its pad depends on the
# ABSOLUTE virtual address.  The dump-free sim carries -748,212 of global drift
# into this weapon, which puts V(weapDef) at 4 mod 8 where the console has it at
# 0 mod 8, and that one align-8 site then pads 5 instead of 1.  Nothing this
# module allocates can change a residue class it does not own.
#   * NOT fixable by dropping stateBits to align 4.  That WOULD make this zone
#     read 3356 (it cancels the same 4 bytes) and it is WRONG: Load_Material
#     +0x33c passes r3=7 (_rpl_align_table.json raw=7 -> align 8), and the true
#     chain above is align-8-consistent.  Fitting it would hide the drift and
#     break any zone whose phase differs.  Explicitly rejected.
#   * The true V(weapDef) residues mod 8 over the 82 measured weapons are 27x 0
#     / 39x 4 / 16x unaligned-needle - i.e. no structure to pin to.  The residue
#     is a genuine function of the incoming cursor.
#   * It closes when (a) the global drift is gone, or (b) MeasuredRuntimeMap
#     replays a band's events from the MEASURED anchor instead of sampling the
#     drifted sim (see the note at the bottom of this file).
#
# =====================================================================
# RESIDUALS (zm_nuked, 79 band samples) - what is left and why
#   * +-1..3 on ~44 samples: NOT a weapon term.  A band delta is
#     err(anchor) - err(previous anchor), so it measures the drift of the
#     PRECEDING asset; 57 of the 79 weapons are preceded by an XAnimParts and
#     the +-1..3 spread is exactly the XAnimParts band's own residual (that
#     band: 426/1528 exact, 1184/1528 within +-2).  It moves when the anim
#     model moves, not when this one does.
#   * +24..+28 (6) and +79..+92 (5): weapons preceded by FxEffectDef(76) /
#     RawFile(12).  ANCHOR_BIAS can only match ONE predecessor convention
#     (104); these are 104-76=28 and 104-12=92.  They vanish only when every
#     type registers its anchor linearized (ANCHOR_BIAS = root_size everywhere).
#   * +813..+2154 (7): the huge attachment-bearing weapons (65 KB .. 734 KB
#     bodies).  Their realmap anchor's needle landed DEEP in the body, past
#     extra inline-asset roots, so real[start] carries that extra TEMP depth
#     (err(anchor) = temp_bytes_before_needle - ANCHOR_BIAS + drift).  This is
#     a property of the measurement, not of the model; a re-measure with a
#     needle pinned just past the 716 root would remove it.
#   * body gaps (forward drift, not this band): the same big weapons still
#     under-allocate 7-30 KB each - inline XModel interior aligns, i.e. the
#     XModel band.  One weapon (55,512-B body, 3 attachmentUniques) shows a
#     -1.35 MB body gap: a runtime block or a bad needle match, isolated.
# =====================================================================


def _selfcheck(zone='zm_nuked_authored.zone'):
    """Standing regression: the walk must land byte-exact on EVERY weapon span
    (loader_sim raises `span drift` otherwise and falls back to verbatim)."""
    import pickle
    import loader_sim as LS
    import rt_events_exact as RTX
    Z = open(zone, 'rb').read()
    EX = dict(RTX.EXTRA_EVENTS)
    _, spans, _ = LS.simulate(Z, verbose=False, policy=dict(gfx_skip=0,
                                                           extra_events=EX))
    ok = bad = 0
    for (i, nm, root, s, e) in spans:
        if root != 'WeaponVariantDef' or e <= s:
            continue
        try:
            end, _ev = weapon_events(Z, s, '>')
        except Exception as ex:
            bad += 1
            print('  ERR   @%d %s' % (s, str(ex)[:70]))
            continue
        if end == e:
            ok += 1
        else:
            bad += 1
            print('  DRIFT @%d len=%d walk=%d (%+d)' % (s, e - s, end - s, end - e))
    print('weapon span-exact %d/%d' % (ok, ok + bad))
    return bad == 0


def _pair_check(zone='zm_nuked_authored.zone',
                simmap='_zmnuked_simmap.pkl', realmap='_zmnuked_realmap.pkl'):
    """CONVENTION-FREE body check (the judge for this module - NOT band medians).

    For every pair of ADJACENT spans that are both MEASURED WeaponVariantDefs,
    replay weapon A's event list from its own true cursor and check that the end
    lands on weapon B's measured anchor.  Both endpoints are dump-measured
    absolute addresses, so nothing about the anchor convention can bias it: a
    per-weapon body-model error of N bytes shows up as exactly N.

    2026-07-26 result: 8/10 exact.  The 2 misses (+814, +1127) are the deep-needle
    class in RESIDUALS - their realmap anchor was back-projected across extra
    inline sub-asset TEMP roots, so real[start] itself carries that depth."""
    import pickle
    import loader_sim as LS
    import rt_events_exact as RTX
    Z = open(zone, 'rb').read()
    real = pickle.load(open(realmap, 'rb'))['real']
    _em, spans, _ = LS.simulate(zone, verbose=False,
                                policy=RTX.policy(gfx_skip=0))

    def replay(events, V, root_size):           # mirrors loader_sim.replay_events
        tn = root_size
        for ev in events:
            if ev[0] == 'seg':
                _, _rel, size, align = ev
                if align > 1 and tn == 0:
                    V = (V + align - 1) & ~(align - 1)
                take = min(tn, size); tn -= take; V += size - take
            elif ev[0] == 'skip':
                _, size, align = ev
                if align > 1:
                    V = (V + align - 1) & ~(align - 1)
                V += size
        return V

    ok = tot = 0
    for k in range(len(spans) - 1):
        (_i, _n, r1, s1, e1) = spans[k]
        (_j, _m, r2, s2, e2) = spans[k + 1]
        if r1 != 'WeaponVariantDef' or r2 != 'WeaponVariantDef':
            continue
        if e1 <= s1 or e2 <= s2:
            continue
        a, b2 = real.get(s1 - 64), real.get(s2 - 64)
        if a is None or b2 is None:
            continue
        tot += 1
        nl = Z.index(b'\x00', s1 + VARIANT_SIZE) + 1 - (s1 + VARIANT_SIZE)
        # true cursor at A's root: the value for which A's anchor == real[A]
        V0 = a + VARIANT_SIZE + (-(a + VARIANT_SIZE + nl)) % 4
        nl2 = Z.index(b'\x00', s2 + VARIANT_SIZE) + 1 - (s2 + VARIANT_SIZE)
        end = replay(weapon_events(Z, s1, '>')[1], V0, VARIANT_SIZE)
        pred = end + (-(end + nl2)) % 4 - VARIANT_SIZE     # B's anchor
        if pred == b2:
            ok += 1
        else:
            print('  PAIR @%d len=%d  %+d' % (s1, e1 - s1, pred - b2))
    print('weapon adjacent-pair exact %d/%d' % (ok, tot))
    return ok, tot


# =====================================================================
# WHAT I WOULD WANT FROM loader_sim (NOT made here - shared file)
# =====================================================================
# The residual +4 at attachments[63]/attachmentUniques[95] is an align-8 pad
# resolved against a virtual cursor that carries -748 KB of GLOBAL drift.  A
# band generator cannot fix a residue class it does not own.  The minimal seam:
#
#   loader_sim.replay_events(): accept an optional `base_rt` and, when given,
#   run the replay with the cursor re-based to it, i.e. one line at the top
#       if base_rt is not None: w.block_size[w.cur_block] = base_rt
#   (plus restoring the drift delta after the loop so the global walk is
#   unaffected).
#
# MeasuredRuntimeMap.rt() could then hand the measured anchor down when it
# composes, and every interior align inside a measured asset would be resolved
# at its REAL address instead of the drifted one.  That is a per-asset re-phase,
# not a fit: it uses only the anchor the composed map already trusts, and it
# would close this +4 and the same class in every other band that has an align
# larger than 4 (Material stateBits 8, constantTable 16, GfxImage pixels 8192,
# clipMap cbrush 128).  Until then the honest number is +4, and it goes to 0 on
# its own the moment the dump-free absolute drift is closed.


if __name__ == '__main__':
    _selfcheck()
    _pair_check()
