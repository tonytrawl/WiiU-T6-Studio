"""weapon_span — a REAL console WeaponVariantDef span parser, built from lane D's structure.

WHY: `body_relayout._weapon_end` has no body model at all. It either resyncs on a follower chain or
falls back to a RAWFILE-cluster heuristic, and both are guesses -- which is why it is wrong in BOTH
directions on genuine zones:

    mp_nuketown_2020  entry 789  UNDER-reads  41 B   (rule AY: a ptr[8] array 32 B + an inline name 9 B)
    zm_transit_patch  entry 598  OVER-reads  ~513 B  (it swallowed three techsets belonging to later entries)

STRUCTURE (lane D `native_linker/weapon_convert.py`, transcribed there from the OAT codegen
`weaponvariantdef_t6_load_db.cpp` Load_WeaponVariantDef @1799; console sizes are Load_Stream r5 ground
truth from the RPL):

    WeaponVariantDef  716        WeaponDef console 2836 (= PC 2448 + a 388-byte WiiU-only tail)
    WeaponAttachment  284        WeaponAttachmentUnique 424

Stream tail, IN LOAD ORDER -- each item consumes inline data only when its (already-filled) pointer
word reads FOLLOW:

     +0   szInternalName            xstring
     +8   weapDef                   inline WeaponDef
     +12  szDisplayName             xstring
     +16  szAltWeaponName           xstring
     +20  szAttachmentUnique        xstring
     +24  attachments[63]           ptr array -> inline WeaponAttachment per FOLLOW
     +28  attachmentUniques[95]     ptr array -> inline WeaponAttachmentUnique per FOLLOW
     +32  szXAnims[88]              xstring array
     +36  hideTags[32]              u16 array
     +40  attachViewModel[8]        ptr array -> inline XModel per FOLLOW
     +44  attachWorldModel[8]       ptr array -> inline XModel per FOLLOW
     +48  attachViewModelTag[8]     xstring array
     +52  attachWorldModelTag[8]    xstring array
     +508 szAmmoDisplayName         xstring
     +512 szAmmoName                xstring
     +520 szClipName                xstring
     +596 overlayMaterial           inline Material (on FOLLOW **or INSERT**)
     +600 overlayMaterialLowRes     inline Material
     +604 dpadIcon                  inline Material

RUNG 3 (landed 2026-07-30): the inline sub-assets. `attachment`/`attachmentUnique` are no longer
"root + name" stubs -- WeaponAttachment carries a SECOND inline string (szDisplayName@+4) and
WeaponAttachmentUnique carries a whole stream (5 XModels, a WeaponCamo, 2 Materials, szXAnims[88],
locationDamage[21], 14 sounds, 2 FX, 2 TracerDefs). TracerDef and WeaponCamo are modelled here;
XModel/FxEffectDef/Material/GfxImage reuse the validated console probes. See `_SUB` below.

DESIGN RULE: every case this parser cannot model RAISES. A span parser that guesses is exactly the
defect being replaced -- silent mis-sizing desyncs the whole zone, and the walk's EOF check is the only
thing that would catch it. Fail loudly instead.
"""
import collections
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

# COVERAGE CENSUS (pure side effect, never read by the parser). A branch that has NEVER fired on any
# genuine zone is TRANSCRIBED BUT UNVALIDATED -- it is a guess with good provenance, not a measured
# fact, and it is exactly where the next map conversion can go wrong. `coverage_census.py` aggregates
# this across the corpus and prints the never-fired list.
STATS = collections.Counter()


def _hit(key):
    STATS[key] += 1

VARIANT_SIZE = 716
WEAPONDEF_CO = 2836
ATTACH_SIZE = 284
ATTACHU_SIZE = 424


class WeaponSpanError(RuntimeError):
    pass


def _u32(d, o):
    return struct.unpack_from('>I', d, o)[0]


def _cstr_end(d, o, cap=1024):
    e = d.find(b'\x00', o)
    if e < 0 or e - o > cap:
        raise WeaponSpanError('unterminated inline string at 0x%x' % o)
    return e + 1


class _Cur(object):
    def __init__(self, d, o):
        self.d = d
        self.o = o

    def cstr(self):
        self.o = _cstr_end(self.d, self.o)

    def xstring(self, ptr):
        if ptr == FOLLOW:
            self.cstr()

    def u16_array(self, n):
        self.o += 2 * n

    def xstring_array(self, n):
        base = self.o
        ptrs = [_u32(self.d, base + 4 * i) for i in range(n)]
        self.o = base + 4 * n
        for v in ptrs:
            if v == FOLLOW:
                self.cstr()

    def ptr_array(self, n, kind):
        base = self.o
        ptrs = [_u32(self.d, base + 4 * i) for i in range(n)]
        self.o = base + 4 * n
        for v in ptrs:
            if v in PTRS:
                self.sub(kind)

    def sub(self, kind):
        """Consume one inline sub-asset (lane D Rung 3). Unmodelled kinds RAISE."""
        _hit('sub:' + kind)
        fn = _SUB.get(kind)
        if fn is None:
            raise WeaponSpanError('inline weapon sub-asset %r not modelled' % kind)
        at = self.o
        try:
            fn(self)
        except WeaponSpanError:
            raise
        except Exception as e:                     # keep the CULPRIT in the message: triage by
            raise WeaponSpanError(                 # first divergence, never by the death site
                'inline %s at 0x%x: %s: %s' % (kind, at, type(e).__name__, e))
        if not (at < self.o <= len(self.d)):
            raise WeaponSpanError('inline %s at 0x%x produced end 0x%x (zone 0x%x)'
                                  % (kind, at, self.o, len(self.d)))

    def material(self):
        self.sub('material')


# ------------------------------------------------------- inline sub-assets (lane D Rung 3)
# The four asset classes reached from a weapon (XModel / FxEffectDef / Material / GfxImage) are
# spanned by the SAME validated console probes the map walk already runs byte-exact -- nothing is
# re-reversed here. The four weapon-private roots (WeaponAttachment, WeaponAttachmentUnique,
# TracerDef, WeaponCamo) are transcribed from lane D `weapon_convert._convert_attachment*` /
# `zmconv_b`, and every size and pointer offset below is confirmed against `struct_layout`
# (console=True) + the zone-code count directives:
#     TracerDef 128  name*@0  material*@8
#     WeaponCamo 28  name*@0  solid/patternBaseImage*@4/@8
#                    camoSets*@12 count numCamoSets@16 · camoMaterials*@20 count @24
#     WeaponCamoSet 20 (2 image ptrs) · WeaponCamoMaterialSet 8 {numMaterials@0, materials*@4}
#     WeaponCamoMaterial 44 {numBaseMaterials u16@2, baseMaterials**@4, camoMaterials**@8}
#     WeaponAttachment 284 (ptrs {0,4}) · WeaponAttachmentUnique 424 (34 ptrs, list below)
_XP = None
_FP = None


def _probes():
    global _XP, _FP
    if _XP is None:
        import xmodel_probe
        import fx_probe
        _XP, _FP = xmodel_probe, fx_probe
    return _XP, _FP


def _sub_xmodel(c):
    XP, _ = _probes()
    c.o = XP.parse_xmodel(c.d, c.o)[0]


def _sub_fx(c):
    _, FP = _probes()
    c.o = FP.parse_fx(c.d, c.o)[0]


def _sub_material(c):
    XP, _ = _probes()
    k = XP.Cur(c.d, c.o)
    XP.consume_material(c.d, k)
    c.o = k.o


def _sub_image(c):
    XP, _ = _probes()
    k = XP.Cur(c.d, c.o)
    XP.consume_image(c.d, k)
    c.o = k.o


def _sub_attachment(c):
    """WeaponAttachment: 284 root + szInternalName + szDisplayName.

    ⚠ THE RUNG-2 STUB CONSUMED ONLY szInternalName. szDisplayName@+4 is a second inline string, so
    every FOLLOW attachment was short by its display name."""
    b = c.o
    c.o = b + ATTACH_SIZE
    for k in (0, 4):
        if _u32(c.d, b + k) == FOLLOW:
            _hit('att:%d' % k)
            c.cstr()


ATTACHU_SOUND_OFFS = tuple(range(256, 312, 4))          # 14 fire-sound XStrings


def _sub_attachment_unique(c):
    """WeaponAttachmentUnique: 424 root + Load_WeaponAttachmentUnique stream.

    ⚠ THE RUNG-2 STUB CONSUMED ONLY szInternalName -- it modelled NONE of the 5 XModels, the camo,
    the 2 overlay Materials, szXAnims[88], the 14 sounds, the 2 FX or the 2 TracerDefs."""
    b = c.o
    c.o = b + ATTACHU_SIZE

    def A(k, ptrs=False):
        v = _u32(c.d, b + k)
        on = (v in PTRS) if ptrs else (v == FOLLOW)
        if on:
            _hit('attu:%d' % k)
        return on

    for k in (0, 20, 28):              # szInternalName / szAltWeaponName / szDualWieldWeaponName
        if A(k):
            c.cstr()
    if A(36):
        c.u16_array(32)                # hideTags
    for k in (40, 44, 48, 52, 56):     # viewModel{,Additional,ADS} / worldModel{,Additional}
        if A(k, True):
            c.sub('xmodel')
    for k in (60, 64):                 # viewModelTag / worldModelTag
        if A(k):
            c.cstr()
    if A(164, True):
        c.sub('camo')
    for k in (196, 200):               # overlayMaterial / overlayMaterialLowRes
        if A(k, True):
            c.sub('material')
    if A(232):
        c.xstring_array(88)            # szXAnims
    if A(248):
        c.o += 4 * HITLOC_COUNT        # locationDamageMultipliers[21]
    for k in ATTACHU_SOUND_OFFS:
        if A(k):
            c.cstr()
    for k in (316, 320):               # viewFlashEffect / worldFlashEffect
        if A(k, True):
            c.sub('fx')
    for k in (324, 328):               # tracerType / enemyTracerType
        if A(k, True):
            c.sub('tracer')


TRACER_SIZE = 128


def _sub_tracer(c):
    b = c.o
    c.o = b + TRACER_SIZE
    if _u32(c.d, b) == FOLLOW:
        _hit('tracer:0')
        c.cstr()                       # name
    if _u32(c.d, b + 8) in PTRS:
        _hit('tracer:8')
        c.sub('material')


CAMO_SIZE = 28
CAMOSET_SIZE = 20
CAMOMATSET_SIZE = 8
CAMOMAT_SIZE = 44


def _sub_camo(c):
    """Load_WeaponCamo. Array rule throughout: ALL element bodies first, then each element's own
    dynamics -- the same DFS order every other console walk in this project obeys."""
    b = c.o
    U = lambda k: _u32(c.d, b + k)
    c.o = b + CAMO_SIZE
    _hit('camo')
    if U(0) == FOLLOW:
        _hit('camo:0')
        c.cstr()                                        # name
    for k in (4, 8):                                    # solidBaseImage / patternBaseImage
        if U(k) in PTRS:
            _hit('camo:%d' % k)
            c.sub('image')
    if U(12) in PTRS:                                   # camoSets[numCamoSets]
        _hit('camo:12')
        n = U(16)
        base = c.o
        c.o = base + CAMOSET_SIZE * n
        for i in range(n):
            for k in (0, 4):                            # solidCamoImage / patternCamoImage
                if _u32(c.d, base + CAMOSET_SIZE * i + k) in PTRS:
                    _hit('camoset:%d' % k)
                    c.sub('image')
    if U(20) in PTRS:                                   # camoMaterials[numCamoMaterials]
        _hit('camo:20')
        n = U(24)
        base = c.o
        c.o = base + CAMOMATSET_SIZE * n
        for i in range(n):
            sb = base + CAMOMATSET_SIZE * i
            if _u32(c.d, sb + 4) not in PTRS:
                continue
            cnt = _u32(c.d, sb)                         # numMaterials
            mb = c.o
            c.o = mb + CAMOMAT_SIZE * cnt
            for j in range(cnt):
                m = mb + CAMOMAT_SIZE * j
                nbm = struct.unpack_from('>H', c.d, m + 2)[0]      # numBaseMaterials
                for k in (4, 8):                        # baseMaterials** / camoMaterials**
                    if _u32(c.d, m + k) in PTRS:
                        _hit('camomat:%d' % k)
                        ab = c.o
                        c.o = ab + 4 * nbm
                        for q in range(nbm):
                            if _u32(c.d, ab + 4 * q) in PTRS:
                                c.sub('material')


_SUB = {
    'attachment': _sub_attachment,
    'attachmentUnique': _sub_attachment_unique,
    'tracer': _sub_tracer,
    'camo': _sub_camo,
    'material': _sub_material,
    'image': _sub_image,
    'fx': _sub_fx,
    'xmodel': _sub_xmodel,
}


def parse_weapon_console(d, off):
    """Span of one console WeaponVariantDef body at `off`. Returns the end offset."""
    if off + VARIANT_SIZE > len(d):
        raise WeaponSpanError('variant root runs past EOF at 0x%x' % off)
    P = lambda k: _u32(d, off + k)
    c = _Cur(d, off + VARIANT_SIZE)
    _hit('variant')

    def V(k, cond=None):
        """Take variant branch k, recording whether it fired."""
        v = P(k)
        on = (v == FOLLOW) if cond is None else (v in PTRS)
        if on:
            _hit('var:%d' % k)
        return on

    if V(0):
        c.cstr()                                      # szInternalName
    if V(8):                                          # inline WeaponDef (lane D Rung 2)
        _weapondef(c)
    for k in (12, 16, 20):                            # szDisplayName/AltWeaponName/AttachmentUnique
        if V(k):
            c.cstr()
    if V(24):
        c.ptr_array(63, 'attachment')
    if V(28):
        c.ptr_array(95, 'attachmentUnique')
    if V(32):
        c.xstring_array(88)                           # szXAnims[NUM_WEAP_ANIMS]
    if V(36):
        c.u16_array(32)                               # hideTags
    if V(40):
        c.ptr_array(8, 'xmodel')
    if V(44):
        c.ptr_array(8, 'xmodel')
    if V(48):
        c.xstring_array(8)                            # attachViewModelTag
    if V(52):
        c.xstring_array(8)                            # attachWorldModelTag
    for k in (508, 512, 520):                         # szAmmoDisplayName/AmmoName/ClipName
        if V(k):
            c.cstr()
    for k in (596, 600, 604):                         # overlay/overlayLowRes/dpadIcon materials
        if V(k, 'ptrs'):
            c.material()

    if not (off < c.o <= len(d)):
        raise WeaponSpanError('span 0x%x..0x%x out of range' % (off, c.o))
    return c.o


# ---------------------------------------------------------------- WeaponDef (lane D Rung 2)
# Console body = 2836 (PC 2448 + a 388-byte WiiU-only tail T6_Assets.h does not model), then the
# inline sub-load stream. Op table transcribed from lane D's _WD_STREAM, which came from the OAT
# codegen Load_WeaponDef @988. Pointer fields are read at wd+off for off < 2448 (that region is
# PC-layout-identical); the 388 tail carries no pointers the stream consumes.
NOTETRACK_COUNT = 20
SURF_TYPE_NUM = 32
HITLOC_COUNT = 21

_WD_STREAM = (
    ('xs', 0), ('xmod', 4), ('asset', 8, 'xmodel'), ('xs', 12),
    ('u16', 16), ('u16', 20), ('xs', 64),
    ('asset', 108, 'fx'), ('asset', 112, 'fx'), ('asset', 116, 'fx'),
) + tuple(('xs', o) for o in range(148, 436, 4)) + (
    ('xsarr', 436, SURF_TYPE_NUM),
    ('xs', 440), ('xs', 444), ('xs', 448),
    ('asset', 464, 'fx'), ('asset', 468, 'fx'), ('asset', 472, 'fx'), ('asset', 476, 'fx'),
    ('asset', 528, 'material'), ('asset', 532, 'material'),
    ('xmod', 916),
    ('asset', 920, 'xmodel'), ('asset', 924, 'xmodel'),
    ('asset', 928, 'xmodel'), ('asset', 932, 'xmodel'),
    ('asset', 936, 'material'), ('asset', 940, 'material'), ('asset', 956, 'material'),
    ('xs', 980), ('xs', 1100), ('xs', 1104), ('xs', 1108),
    ('xs', 1112), ('xs', 1116), ('xs', 1120), ('xs', 1388),
    ('asset', 1632, 'material'), ('asset', 948, 'material'),
    ('xs', 1652), ('xs', 1656),
    ('asset', 1768, 'xmodel'),
    ('asset', 1776, 'fx'), ('asset', 1784, 'fx'), ('asset', 1792, 'fx'),
    ('asset', 1800, 'fx'), ('asset', 1808, 'fx'), ('asset', 1816, 'fx'),
    ('xs', 1820), ('xs', 1824), ('xs', 1828), ('xs', 1832),
    ('f32', 1880, SURF_TYPE_NUM), ('f32', 1884, SURF_TYPE_NUM),
    ('asset', 1888, 'fx'), ('asset', 1916, 'fx'),
    ('xs', 1920), ('xs', 2124),
    # both aiVsAi arrays size from the count at +2148, both aiVsPlayer from +2152
    # (lane D FIX D2, raw-RPL verified). Stream order: 2132, 2140, xstring@2128, 2136, 2144.
    ('vec2', 2132, 2148), ('vec2', 2140, 2148),
    ('xs', 2128),
    ('vec2', 2136, 2152), ('vec2', 2144, 2152),
    ('xs', 2236), ('xs', 2240), ('xs', 2284),
    ('f32', 2300, HITLOC_COUNT),
    ('xs', 2304), ('xs', 2308), ('xs', 2312), ('xs', 2316),
    ('asset', 2320, 'tracer'), ('asset', 2324, 'tracer'),
    ('xs', 2356), ('xs', 2360),
    ('flame', 2364), ('flame', 2368),
    ('asset', 2372, 'fx'), ('asset', 2376, 'fx'),
    ('asset', 2404, 'fx'), ('asset', 2408, 'fx'), ('asset', 2412, 'fx'),
    ('xs', 2416), ('asset', 2420, 'camo'),
)

FLAMETABLE_SIZE = 484


def _flametable(c):
    ft = c.o
    F = lambda k: _u32(c.d, ft + k)
    c.o = ft + FLAMETABLE_SIZE
    _hit('flametable')
    if F(432) == FOLLOW:
        _hit('ft:432')
        c.cstr()
    for mo in (436, 440, 444, 448, 452, 456, 460, 464):
        if F(mo) in PTRS:
            _hit('ft:%d' % mo)
            c.material()
    for so in (468, 472, 476, 480):
        if F(so) == FOLLOW:
            _hit('ft:%d' % so)
            c.cstr()


def _weapondef(c):
    wd = c.o
    if wd + WEAPONDEF_CO > len(c.d):
        raise WeaponSpanError('WeaponDef root runs past EOF at 0x%x' % wd)
    W = lambda k: _u32(c.d, wd + k)
    c.o = wd + WEAPONDEF_CO
    _hit('weapondef')
    for op in _WD_STREAM:
        kind, off = op[0], op[1]
        v = W(off)
        if v in PTRS:
            _hit('wd:%d:%s' % (off, kind))
        if kind == 'xs':
            if v == FOLLOW:
                c.cstr()
        elif kind == 'u16':
            if v == FOLLOW:
                c.u16_array(NOTETRACK_COUNT)
        elif kind == 'f32':
            if v == FOLLOW:
                c.o += 4 * op[2]
        elif kind == 'vec2':
            if v == FOLLOW:
                c.o += W(op[2]) * 8
        elif kind == 'xsarr':
            if v == FOLLOW:
                c.xstring_array(op[2])
        elif kind == 'xmod':
            if v == FOLLOW:
                c.ptr_array(16, 'xmodel')
        elif kind == 'asset':
            if v in PTRS:
                c.sub(op[2])
        elif kind == 'flame':
            if v == FOLLOW:
                _flametable(c)


if __name__ == '__main__':
    import os, sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, HERE)
    for _r in ('wiiu_ref', 'native_linker', 'WiiU_FF_Studio'):
        sys.path.insert(0, os.path.join(HERE, '..', _r))
    import zone_facts as ZF
    # known answers from the localiser probes
    # REGRESSION ANCHOR: mp_nuketown_2020 entry 789. Body start and length are the values the
    # rule-(AZ) absorber-bisect proved and walk2 reproduces end-to-end (832/832, EOF exact).
    CASES = [(r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e'
              r'\1010cf00\content\english\mp_nuketown_2020.ff', 0x0524a889, 5218),
             ]
    for p, start, want in CASES:
        d = ZF.load_ff(p)
        try:
            e = parse_weapon_console(d, start)
            print('%s @0x%x -> %d B (want %d) %s'
                  % (os.path.basename(p), start, e - start, want,
                     'MATCH' if e - start == want else 'MISMATCH'))
        except Exception as ex:
            print('%s @0x%x -> %s: %s' % (os.path.basename(p), start, type(ex).__name__, ex))
