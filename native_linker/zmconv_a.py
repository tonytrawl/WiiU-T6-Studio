#!/usr/bin/env python3
"""
LANE A -- PC(LE) -> console(BE) converters for four ZM small asset roots that
the zm_transit oracle proved are SAME-SIZE, pure field-swap types:

    ZBarrierDef (560)  XGlobals (564)  FootstepTableDef (900)  FootstepFXTableDef (132)

Layouts are taken from the OAT load_db codegen + T6_Assets.h structs:
  tools/ref_oat/build/src/ZoneCode/Game/T6/XAssets/<type>/..._load_db.cpp
  tools/ref_oat/src/Common/Game/T6/T6_Assets.h

Each convert_* returns (body_bytes, pc_end), mirroring native_linker/smalls_convert.py.
The `Sw` LE->BE cursor, FOLLOW/INSERT sentinels and reloc contract are reused from
smalls_convert (read-only import -- that file is a shared/contended file we never edit).

Pointer handling: every pointer word goes through `reloc` (asset refs + XStrings).
For these four roots the oracle proved bodies are byte-length-identical console<->PC,
i.e. all asset-ref pointers (XModel/FxEffectDef) are ALIASES or NULL (never inline-
FOLLOWING), so the only inline data that FOLLOWs is XString name/anim strings. If an
asset-ref pointer is ever seen FOLLOWing we raise (would need an inline sub-convert;
not expected for zm_transit's 13 instances).
"""
import struct
from smalls_convert import Sw, FOLLOW, INSERT, PTRS, _default_reloc


def _emit_cstr(s):
    """Emit the NUL-terminated string at the cursor, verbatim (incl. NUL)."""
    s.cstr()


# ---------------------------------------------------------------- ZBarrierDef
def convert_zbarrier(pc, off, reloc=_default_reloc):
    """ZBarrierDef: 560-B body ( name ptr + scalars + boards[6] x ZBarrierBoard(80) )
    followed by the inline name string (FOLLOW) and per-board FOLLOW anim strings.

    ZBarrierBoard(80): 7 ptrs @0..24 (3 XModel refs, pTearAnim/pBoardAnim XStrings,
    2 FxEffectDef refs), repairEffect1/2Offset vec3 @28/@40, 3 u32 @52/56/60,
    2 float @64/68, zombieBoardTear{State,SubState}Name u16 @72/@74, u32 @76."""
    s = Sw(pc, off, reloc)
    p = lambda o: s.peek32(off + o)
    # --- 560-B body ---
    s.ptr()                                    # name @0
    s.u32(16)                                  # generalRepairSound1..reachThroughAttacks @4..64
    s.u16(2)                                   # zombieTaunt/ReachThroughAnimState @68/@70
    s.u32(2)                                   # numAttackSlots @72, attackSpotHorzOffset @76
    for b in range(6):                         # boards[6] @80..559
        bb = off + 80 + b * 80
        s.ptr(7)                               # pBoardModel..repairEffect2 @0..24
        s.u32(6)                               # repairEffect1Offset+2Offset vec3s @28..51
        s.u32(3)                               # boardRepairSound/Hover/pauseAndRepeat @52..63
        s.u32(2)                               # minPause/maxPause @64/68
        s.u16(2)                               # zombieBoardTear{State,SubState}Name @72/74
        s.u32()                                # numRepsToPullProBoard @76
    assert s.o == off + 560, (s.o, off + 560)
    # --- dynamic (Load order) ---
    if p(0) in PTRS:
        _emit_cstr(s)                          # name string
    for b in range(6):
        bb = 80 + b * 80
        for ao in (0, 4, 8, 20, 24):           # XModel/FxEffectDef refs must be aliases
            if p(bb + ao) in PTRS:
                raise NotImplementedError(
                    'ZBarrierDef board %d asset-ref @+%d is FOLLOWING (inline) -- '
                    'unexpected for a same-size zm_transit instance' % (b, ao))
        if p(bb + 12) in PTRS:
            _emit_cstr(s)                      # pTearAnim
        if p(bb + 16) in PTRS:
            _emit_cstr(s)                      # pBoardAnim
    return bytes(s.b), s.o


# ---------------------------------------------------------------- XGlobals
def convert_xglobals(pc, off, reloc=_default_reloc):
    """XGlobals: 564-B body -- name ptr, scalars, gumps[32]{char* name,int size}
    @44, overlays[32]{char* name,int size} @308 -- then FOLLOW name strings for the
    first gumpsCount / overlayCount entries."""
    s = Sw(pc, off, reloc)
    p = lambda o: s.peek32(off + o)
    gumpsCount = p(40)
    overlayCount = p(304)
    # --- 564-B body ---
    s.ptr()                                    # name @0
    s.u32(10)                                  # xanimStreamBufferSize..gumpsCount @4..40 (incl vec4 screenClearColor)
    for i in range(32):                        # gumps[32] @44..299
        s.ptr()                                # gump name
        s.u32()                                # gump size
    s.u32(2)                                   # bigestOverlaySize @300, overlayCount @304
    for i in range(32):                        # overlays[32] @308..563
        s.ptr()                                # overlay name
        s.u32()                                # overlay size
    assert s.o == off + 564, (s.o, off + 564)
    # --- dynamic ---
    if p(0) in PTRS:
        _emit_cstr(s)                          # name string
    for i in range(gumpsCount):
        if p(44 + i * 8) in PTRS:
            _emit_cstr(s)
    for i in range(overlayCount):
        if p(308 + i * 8) in PTRS:
            _emit_cstr(s)
    return bytes(s.b), s.o


# ---------------------------------------------------------------- FootstepTableDef
def convert_footsteptable(pc, off, reloc=_default_reloc):
    """FootstepTableDef: 900-B body -- name ptr + sndAliasTable[32][7] unsigned int
    (all 4-byte words) -- then the FOLLOW name string."""
    s = Sw(pc, off, reloc)
    s.ptr()                                    # name @0
    s.u32(224)                                 # sndAliasTable[32][7] @4..899
    assert s.o == off + 900, (s.o, off + 900)
    if s.peek32(off) in PTRS:
        _emit_cstr(s)                          # name string
    return bytes(s.b), s.o


# ---------------------------------------------------------------- FootstepFXTableDef
def convert_footstepfxtable(pc, off, reloc=_default_reloc):
    """FootstepFXTableDef: 132-B body -- name ptr + footstepFX[32] FxEffectDef*
    (aliases) -- then the FOLLOW name string. The 32 FX pointers are asset aliases
    (reloc'd); an inline-FOLLOWING FX here would grow the body and is not expected."""
    s = Sw(pc, off, reloc)
    p = lambda o: s.peek32(off + o)
    s.ptr()                                    # name @0
    s.ptr(32)                                  # footstepFX[32] @4..128
    assert s.o == off + 132, (s.o, off + 132)
    if p(0) in PTRS:
        _emit_cstr(s)                          # name string
    for i in range(32):
        if p(4 + i * 4) in PTRS:
            raise NotImplementedError(
                'FootstepFXTableDef footstepFX[%d] is FOLLOWING (inline FxEffectDef) '
                '-- unexpected for a same-size zm_transit instance' % i)
    return bytes(s.b), s.o
