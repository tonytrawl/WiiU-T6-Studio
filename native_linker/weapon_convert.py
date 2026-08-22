#!/usr/bin/env python3
"""LANE D — PC(LE) -> console(BE) WeaponVariantDef / WeaponDef converter.

Stream order is transcribed verbatim from the authoritative OAT codegen
`weaponvariantdef_t6_load_db.cpp` (Load_WeaponVariantDef @1799, Load_WeaponDef
@988). Console struct sizes are the loader `Load_Stream` r5 ground truth (RPL
dissection, HANDOFF_transit_gate_close.md §RPL): WeaponVariantDef header = 716
(== PC), WeaponDef = 2836 (PC/SL 2448 + a 388-byte WiiU-only tail block
[2448..2836) that T6_Assets.h does not model).

RUNGS (see FINDINGS_conv_d.md):
  R1  variant-only weapons (weapDef + attachments all block-5 ALIASES; only
      FOLLOW szInternalName / szXAnims inline). Body = field-aware BE swap of the
      716 struct (chars/pad verbatim) + inline FOLLOW strings. DONE.
  R2  the 49 "+388" weapons: variant + an INLINE WeaponDef (FOLLOW weapDef); all
      of WeaponDef's own sub-assets still aliased. Needs Load_WeaponDef + the 388
      tail policy.
  R3  content-heavy weapons: inline attachments / attachmentUniques / camo / hud
      materials+techsets (recurse the existing converters).

Each convert_* returns (body_bytes_BE, pc_end_offset), matching smalls_convert.
Pointer WORDS carry reloc(pc_value); a genuine byte-exact match on the pointer
slots is a job for the assembler's reloc (validate_weapon_convert classes a
pointer-only diff as PASS, exactly like material/xmodel).
"""
import struct
from collections import Counter
from smalls_convert import Sw, FOLLOW, INSERT, PTRS

# ---- rule (EY): stale-heap weapon strings are MINTED from content ----
# Installed by weapon_string_mint.install() (see its docstring for the four
# refutations that bar the pointer graph). MINT = {pc_body_off: {'strs':
# {slot_off: text|None}, 'xanims': [88]|None, 'xanims_elems': [88]|None}}.
# When None (every non-zm_nuked caller, incl. validate_weapon_convert's
# transit run and loader_sim's span delimiter), behaviour is byte-identical
# to before. Minted content NEVER advances s.o, so PC span ends are
# unchanged by construction.
MINT = None
# (EY) v3 -- the populations b78 did not reach, keyed by PC offset:
#   MINT2['variant'][variant_off] = {'strs': {508/512/520: txt},
#                                    'tag': {48/52: [8]}, 'tag_elems': {...}}
#   MINT2['attu'][attu_body_off]  = {'strs': {off: txt}, 'xanims': [88]|None,
#                                    'elems': [88]|None}
#   MINT2['bounce'][weapondef_off] = [32 strings]
MINT2 = None
MINT_STATS = Counter()
MINT_NULLED = []               # (weapon_name, elem_index) for every NULL'd elem
# MINT[_MINT_WD_KEY] = {pc_weapondef_off: {field_off: text}} -- the interior arm
# (boot-77 wall). Keyed by the WeaponDef's own PC offset, NOT the variant's, so a
# weapon with no inline WeaponDef simply has no entry.
_MINT_WD_KEY = '__weapondef__'

# ---- b104: DECLARED-NULL worldModel arrays (WeaponDef +0x394) ----------------
# Installed by `b104_worldmodel_null.install()`; a set of weapon names. While it
# is None the branch in _convert_weapondef is UNREACHABLE and this module behaves
# exactly as b103 did -- which is not an assertion, it is the b104 arm-1 control:
# with the hook unset the bake reproduces b103 raw BYTE-EXACT (3856a119...).
#
# WHAT IT DOES. A weapon whose +0x394 holds a block-5 ALIAS that resolves into a
# FOREIGN asset body is the boot-103 defect class: G_RegisterWeapon does
#     lwz r10, 0x394(r30) ; lwz r3, 0(r10) ; cmpwi r3,0 ; beq <skip>
# -- the array POINTER is never null-checked, but every ELEMENT access through it
# is. (Disassembled from t6mp_cafef_rpl f350a7a2...; G_SpawnItem @0x022e2b3c has
# the identical shape.) So the array must EXIST, and an all-NULL array is safe by
# the engine's own guards: the weapon registers with no world model.
#
# ⛔ THIS IS A DECLARED ABSENCE, NOT A GUESS. Where the donor is underivable we
# assert NOTHING rather than a plausible pointer -- the distinction the no-guessed
# -values rule is actually about (law 74). The weapons in this set are INVISIBLE
# in-world; that is a real, declared gameplay defect and it is written down in
# BOOT_REQUEST_zm_nuked_b104.md, not hidden behind a green gate.
# ⚠ The landing-class bar is satisfied here BY CONSTRUCTION -- an inline FOLLOW
# array cannot land in a foreign asset because it does not point anywhere. The
# bar being green at +0x394 is NECESSARY, NOT SUFFICIENT; the evidence this boots
# is the disassembly above, never the bar.
WORLDMODEL_NULL = None
_CUR_VARIANT = None                    # PC offset of the variant being converted


def _emit_null_worldmodel(s, wd_b, off):
    """Patch the +0x394 head word to FOLLOW and emit an inline all-NULL
    XModel*[16]. Consumes NO PC bytes (the PC source carries no inline array
    here), so s.o is untouched by construction -- same discipline as _mint_cstr."""
    struct.pack_into('>I', s.b, wd_b + off, FOLLOW)
    for _ in range(16):
        _mark_ptr(s)
        s.b += b'\x00\x00\x00\x00'
    MINT_STATS['b104-worldmodel-null'] += 1


def _mint_cstr(s, text):
    s.b += text.encode('ascii') + b'\x00'


def _mint_array(s, base_b, head_off, values, stat):
    """Stale ARRAY head -> emit the whole array inline: patch the head word to
    FOLLOW, write `len(values)` element words, then the inline strings. A None
    element emits a NULL word and no string (used only where a NULL is the
    measured convention -- see weapon_string_mint's szXAnims[0] note)."""
    struct.pack_into('>I', s.b, base_b + head_off, FOLLOW)
    for v in values:
        _mark_ptr(s)
        s.b += struct.pack('>I', FOLLOW if v is not None else 0)
    for v in values:
        if v is not None:
            _mint_cstr(s, v)
    MINT_STATS[stat] += 1

NUM_WEAP_ANIMS = 88            # T6_Assets.h NUM_WEAP_ANIMS = 0x58
SURF_TYPE_NUM = 31            # bounceSound[] count (T6 surfaceType enum size); Rung2
VARIANT_SIZE = 716
WEAPONDEF_PC = 2448
WEAPONDEF_CO = 2836           # console struct (RPL Load_Stream r5) = PC + 388

# pointer-word offsets inside the 716 variant struct (FillPtr in FillStruct_*)
VARIANT_PTR_OFFS = frozenset(
    (0, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52,
     508, 512, 520, 596, 600, 604))
# char/pad words copied VERBATIM (a 4-byte swap would reorder the sub-byte
# bools). Learned from all 107 genuine variant bodies (the only words ever seen
# console==PC-verbatim rather than swapped): bIgnoreAttachments / bSilenced..
# bTVGuided / noAmmoOnDpadIcon..mmsInScope+pad / bUsingLeftHandProneIK+pad.
VARIANT_BYTE_RUNS = ((472, 476), (580, 584), (612, 616), (664, 668))


def _in_runs(o, runs):
    return any(a <= o < b for (a, b) in runs)


def _mark_ptr(s):
    """Record the byte span of the pointer word about to be appended to s.b, so
    the validator can accept reloc-resolved pointer diffs at the exact (possibly
    unaligned) positions weapons place their alias arrays."""
    if hasattr(s, 'pspans'):
        s.pspans.append(len(s.b))


def _emit_struct(s, base, size, ptr_offs, byte_runs, reloc, ss_offs=()):
    """Emit a [base, base+size) fixed struct as BE: 4-byte swap everywhere except
    `ptr_offs` (reloc'd pointer words), `byte_runs` (verbatim char/pad), and
    `ss_offs` (words holding 16-bit ScriptString / uint16 fields -> swap each
    HALFWORD, never the whole 32-bit word)."""
    pc = s.pc
    assert size % 4 == 0
    for o in range(0, size, 4):
        ao = base + o
        if _in_runs(o, byte_runs):
            s.b += pc[ao:ao + 4]
        elif o in ptr_offs:
            v = struct.unpack_from('<I', pc, ao)[0]
            _mark_ptr(s)
            s.b += struct.pack('>I', reloc(v) if v not in PTRS else v)
        elif o in ss_offs:
            s.b += struct.pack('>HH', *struct.unpack_from('<HH', pc, ao))
        else:
            s.b += struct.pack('>I', struct.unpack_from('<I', pc, ao)[0])
    s.o = base + size


def _xstring(s, ptr):
    """LoadXString(false): consume inline string iff the (already-filled) ptr is
    FOLLOW; alias/null consume 0 bytes."""
    if ptr == FOLLOW:
        s.cstr()


def _xstring_array(s, count, reloc, elems=None, wname=None):
    """LoadXStringArray(true, count): count filled ptr words (BE, reloc'd) then an
    inline string for each FOLLOW element.

    `elems` (rule (EY), zm_nuked szXAnims only): repaired element strings. A
    STALE element word (not 0/FOLLOW/INSERT -- a dump heap pointer, refuted as
    a source 4x) becomes FOLLOW + minted elems[i], or NULL when elems[i] is
    None (rule (EX): never guess; logged in MINT_NULLED). FOLLOW/0 elements
    keep today's byte-exact path."""
    base = s.o
    ptrs = struct.unpack_from('<%dI' % count, s.pc, base)
    if elems is None:
        for v in ptrs:
            _mark_ptr(s)
            s.b += struct.pack('>I', reloc(v) if v not in PTRS else v)
    else:
        for i, v in enumerate(ptrs):
            _mark_ptr(s)
            if v in PTRS or v == 0:
                s.b += struct.pack('>I', v)
            elif elems[i] is not None:
                s.b += struct.pack('>I', FOLLOW)
                MINT_STATS['elem-repaired'] += 1
            else:
                s.b += struct.pack('>I', 0)
                MINT_STATS['elem-nulled'] += 1
                MINT_NULLED.append((wname, i))
    s.o = base + 4 * count
    for i, v in enumerate(ptrs):
        if v == FOLLOW:
            if elems is not None:
                MINT_STATS['elem-follow-kept'] += 1
            s.cstr()
        elif (elems is not None and v != 0 and v != INSERT
              and elems[i] is not None):
            _mint_cstr(s, elems[i])


def _u16_array(s, count):
    """Native uint16[count] (hideTags / notetrack keys|values)."""
    s.u16(count)


def _recurse(s, kind, reloc):
    """Consume one inline sub-asset at s.o via its dedicated converter, append the
    converted (BE) body, and advance the PC cursor. Body-byte fidelity is each
    converter's own contract (xmodel verts / image pixels etc. are documented
    lossy) — the weapon layer only guarantees correct SPAN traversal."""
    import material_convert as MC
    if kind == 'material':
        body, nxt = MC.convert_material(s.pc, s.o, reloc)
    elif kind == 'xmodel':
        import xmodel_convert as XC
        # `len(s.b)` is THIS model's offset within the WEAPON body -- the body the
        # I1 binder was handed via begin_asset(co_cursor). Without it the binder
        # stages every materialHandles cell of a nested model short by exactly this
        # amount (probe PG3: 45 holders + 94 aliases on zm_nuked landing on
        # float/index data). `s.b` is appended to only AFTER the converter returns,
        # so it is the correct origin at call time, and it stays correct for models
        # nested one level deeper (attachmentUnique) because those share this `s`.
        body, nxt = XC.convert_xmodel(s.pc, s.o, reloc, co_base=len(s.b))
    elif kind == 'fx':
        import fx_convert as FX
        body, nxt, _ = FX.convert_fx(s.pc, s.o, reloc)
    elif kind == 'tracer':
        import zmconv_b as ZB
        body, nxt = ZB.convert_tracerdef(s.pc, s.o, reloc)
    elif kind == 'camo':
        import zmconv_b as ZB
        body, nxt = ZB.convert_weaponcamo(s.pc, s.o, reloc)
    elif kind == 'attachment':
        _convert_attachment(s, reloc)
        return
    elif kind == 'attachmentUnique':
        _convert_attachment_unique(s, reloc)
        return
    else:
        raise NotImplementedError('inline weapon sub-asset %r (Rung 3)' % kind)
    s.b += body
    s.o = nxt


# --------------------------------------------------- WeaponAttachment[Unique]
# Console struct sizes = Load_Stream r5 (RPL @0x21d225c / @0x21d2324): 284 / 424.
ATTACH_SIZE = 284
ATTACH_PTR_OFFS = frozenset((0, 4))
ATTACH_VERB_RUNS = ((32, 36), (44, 52), (216, 220), (272, 284))

ATTACHU_SIZE = 424
ATTACHU_PTR_OFFS = frozenset((
    0, 20, 28, 36, 40, 44, 48, 52, 56, 60, 64, 164, 196, 200, 232, 248,
    256, 260, 264, 268, 272, 276, 280, 284, 288, 292, 296, 300, 304, 308,
    316, 320, 324, 328))
ATTACHU_VERB_RUNS = ((168, 172), (228, 232), (348, 352), (412, 424))


def _convert_attachment(s, reloc):
    """WeaponAttachment: 284-byte struct + szInternalName + szDisplayName."""
    a = s.o
    A = lambda o: struct.unpack_from('<I', s.pc, a + o)[0]
    _emit_struct(s, a, ATTACH_SIZE, ATTACH_PTR_OFFS, ATTACH_VERB_RUNS, reloc)
    if A(0) == FOLLOW:
        s.cstr()
    if A(4) == FOLLOW:
        s.cstr()


def _convert_attachment_unique(s, reloc):
    """WeaponAttachmentUnique: 424-byte struct + Load_WeaponAttachmentUnique
    stream (3 XStrings, hideTags[32]u16, 5 XModels, 2 XStrings, camo, 2 Materials,
    szXAnims[88], locationDamage[21], 14 XStrings, 2 FX, 2 TracerDefs)."""
    a = s.o
    A = lambda o: struct.unpack_from('<I', s.pc, a + o)[0]
    # (EY) v3: this body is the population boot 78 faulted in
    # (BG_SetupAttachmentAlts). Identity is READ from the body's own inline
    # szInternalName (77/77 FOLLOW), never inferred from position.
    au = MINT2['attu'].get(a) if MINT2 is not None else None
    au_b = len(s.b)
    _emit_struct(s, a, ATTACHU_SIZE, ATTACHU_PTR_OFFS, ATTACHU_VERB_RUNS, reloc)
    if au:
        for o in au['strs']:
            struct.pack_into('>I', s.b, au_b + o, FOLLOW)
            MINT_STATS['attu-struct-minted'] += 1

    def _au(o):
        """Emit the ATTU string slot at struct offset `o`, minted or verbatim."""
        if au and o in au['strs']:
            _mint_cstr(s, au['strs'][o])
        elif A(o) == FOLLOW:
            s.cstr()

    if A(0) == FOLLOW:                                   # szInternalName
        s.cstr()
    _au(20)                                              # szAltWeaponName
    _au(28)                                              # szDualWieldWeaponName
    if A(36) == FOLLOW:                                  # hideTags[32] u16
        s.u16(32)
    for mo in (40, 44, 48, 52, 56):                      # 5 XModels
        if A(mo) in PTRS:
            _recurse(s, 'xmodel', reloc)
    _au(60)                                              # viewModelTag
    _au(64)                                              # worldModelTag
    if A(164) in PTRS:                                   # weaponCamo
        _recurse(s, 'camo', reloc)
    if A(196) in PTRS:                                   # overlayMaterial
        _recurse(s, 'material', reloc)
    if A(200) in PTRS:                                   # overlayMaterialLowRes
        _recurse(s, 'material', reloc)
    if au and au.get('xanims'):                          # stale head -> mint all
        _mint_array(s, au_b, 232, au['xanims'], 'attu-anim-array-minted')
    elif A(232) == FOLLOW:                               # szXAnims[88]
        _xstring_array(s, 88, reloc, elems=(au or {}).get('elems'),
                       wname=(au or {}).get('name'))
    if A(248) == FOLLOW:                                 # locationDamage[21]
        s.u32(21)
    for so in range(256, 312, 4):                        # 14 fire sound XStrings
        _au(so)
    if A(316) in PTRS:                                   # viewFlashEffect
        _recurse(s, 'fx', reloc)
    if A(320) in PTRS:                                   # worldFlashEffect
        _recurse(s, 'fx', reloc)
    if A(324) in PTRS:                                   # tracerType
        _recurse(s, 'tracer', reloc)
    if A(328) in PTRS:                                   # enemyTracerType
        _recurse(s, 'tracer', reloc)


def _ptr_array_assets(s, count, reloc, kind):
    """LoadPtrArray_* : count filled ptr words then recurse into each FOLLOW/INSERT
    element via `kind` (None => raise: an unhandled inline sub-asset class)."""
    base = s.o
    ptrs = struct.unpack_from('<%dI' % count, s.pc, base)
    for v in ptrs:
        _mark_ptr(s)
        s.b += struct.pack('>I', reloc(v) if v not in PTRS else v)
    s.o = base + 4 * count
    for v in ptrs:
        if v in PTRS:
            if kind is None:
                raise NotImplementedError('inline weapon sub-asset array (Rung 3)')
            _recurse(s, kind, reloc)


def convert_weapon(pc, off, reloc=lambda v: v, record_ptrs=False):
    """WeaponVariantDef PC(LE) -> console(BE). Implements Load_WeaponVariantDef.

    Rung 1 (variant-only) is complete. Inline weapDef (Rung 2) and inline
    attachment/model/material sub-assets (Rung 3) raise NotImplementedError with
    a clear message so the validator classes them, never silently mis-sizes.

    Returns (body, pc_end); with record_ptrs also the list of body-relative
    pointer-word offsets (validator uses it to accept reloc-resolved diffs)."""
    global _CUR_VARIANT
    _CUR_VARIANT = off                 # b104: identity for WORLDMODEL_NULL (see below)
    s = Sw(pc, off, reloc)
    s.pspans = []
    s.tailspan = None
    P = lambda o: struct.unpack_from('<I', pc, off + o)[0]
    m = MINT.get(off) if MINT is not None else None
    m2 = MINT2['variant'].get(off) if MINT2 is not None else None

    # ---- 716-byte variant struct (field-aware BE) ----
    _emit_struct(s, off, VARIANT_SIZE, VARIANT_PTR_OFFS, VARIANT_BYTE_RUNS, reloc)
    if m is not None:
        # rule (EY): minted slots' head words become FOLLOW (or NULL) BEFORE
        # anything is appended after the struct -- the loader then consumes the
        # inline string we mint below, in Load order.
        for so, text in m['strs'].items():
            struct.pack_into('>I', s.b, so, FOLLOW if text is not None else 0)
            MINT_STATS['head-minted' if text is not None else 'head-nulled'] += 1
        if m['xanims'] is not None:
            struct.pack_into('>I', s.b, 32, FOLLOW)
            MINT_STATS['xanims-array-minted'] += 1
    if m2 is not None:
        # ⚠ EVERY minted string needs BOTH halves: the inline bytes AND a FOLLOW
        # head word. Emitting only the bytes leaves the loader looking at the old
        # ALIAS word, so it never consumes them -- the body is then longer than
        # the walk, which is exactly the 28-byte span drift that failed the first
        # b79 bake (-3 on a weapon with three empty ammo strings).
        for so in m2['strs']:
            struct.pack_into('>I', s.b, so, FOLLOW)

    def _mstr(k):
        """szInternalName/szDisplayName/szAltWeaponName/szAttachmentUnique:
        minted slots emit their recovered text; everything else is untouched."""
        if m is not None and k in m['strs']:
            if m['strs'][k] is not None:
                _mint_cstr(s, m['strs'][k])
        else:
            _xstring(s, P(k))

    # ---- stream tail, in Load_WeaponVariantDef order ----
    _mstr(0)                                            # szInternalName (inline @716)

    if P(8) == FOLLOW:                                  # weapDef inline (Rung 2)
        _convert_weapondef(s, reloc)
    _mstr(12)                                           # szDisplayName
    _mstr(16)                                           # szAltWeaponName
    _mstr(20)                                           # szAttachmentUnique

    if P(24) == FOLLOW:                                 # attachments[63]
        _ptr_array_assets(s, 63, reloc, 'attachment')
    if P(28) == FOLLOW:                                 # attachmentUniques[95]
        _ptr_array_assets(s, 95, reloc, 'attachmentUnique')
    if m is not None and m['xanims'] is not None:       # szXAnims[88] MINTED
        # stale head slot: emit the full recovered array (head word already
        # patched to FOLLOW above). Nothing is consumed from the PC stream --
        # the dump carries no array here, only the stale pointer.
        for x in m['xanims']:
            _mark_ptr(s)
            s.b += struct.pack('>I', FOLLOW if x is not None else 0)
        for x in m['xanims']:
            if x is not None:
                _mint_cstr(s, x)
    elif P(32) == FOLLOW:                               # szXAnims[88]
        _xstring_array(s, NUM_WEAP_ANIMS, reloc,
                       elems=m['xanims_elems'] if m is not None else None,
                       wname=m['name'] if m is not None else None)
    if P(36) == FOLLOW:                                 # hideTags[32] u16
        _u16_array(s, 32)
    if P(40) == FOLLOW:                                 # attachViewModel[8] XModel*
        _ptr_array_assets(s, 8, reloc, 'xmodel')
    if P(44) == FOLLOW:                                 # attachWorldModel[8]
        _ptr_array_assets(s, 8, reloc, 'xmodel')
    for tag_off in (48, 52):                            # attach*ModelTag[8]
        if m2 and tag_off in m2['tag']:                 # stale head -> mint all 8
            _mint_array(s, 0, tag_off, m2['tag'][tag_off], 'tag-array-minted')
        elif P(tag_off) == FOLLOW:
            _xstring_array(s, 8, reloc,
                           elems=(m2 or {}).get('tag_elems', {}).get(tag_off),
                           wname=(m2 or {}).get('name'))

    for ammo_off in (508, 512, 520):                    # ammoDisplayName/ammo/clip
        if m2 and ammo_off in m2['strs']:
            _mint_cstr(s, m2['strs'][ammo_off])
            MINT_STATS['ammo-minted'] += 1
        else:
            _xstring(s, P(ammo_off))

    if P(596) in PTRS:                                  # overlayMaterial
        _inline_material(s, reloc)
    if P(600) in PTRS:                                  # overlayMaterialLowRes
        _inline_material(s, reloc)
    if P(604) in PTRS:                                  # dpadIcon
        _inline_material(s, reloc)

    if record_ptrs:
        return bytes(s.b), s.o, s.pspans, s.tailspan
    return bytes(s.b), s.o


# =============================================================== WeaponDef (R2)
import os
SURF_TYPE_NUM = 32           # T6 surfaceType enum size (parallelBounce[32])
HITLOC_COUNT = 21            # HITLOC_SHIELD=0x14 -> COUNT=21
NOTETRACK_COUNT = 20         # notetrackSoundMap{Keys,Values}[20]

# WeaponDef pointer-word offsets = every FillPtr in FillStruct_WeaponDef (codegen,
# authoritative — the oracle "conflict" words were exactly these, tagged by the
# loader's own block scheme not just 0xA0).
WEAPONDEF_PTR_OFFS = frozenset((
    0, 4, 8, 12, 16, 20, 64, 108, 112, 116, 148, 152, 156, 160, 164, 168, 172,
    176, 180, 184, 188, 192, 196, 200, 204, 208, 212, 216, 220, 224, 228, 232,
    236, 240, 244, 248, 252, 256, 260, 264, 268, 272, 276, 280, 284, 288, 292,
    296, 300, 304, 308, 312, 316, 320, 324, 328, 332, 336, 340, 344, 348, 352,
    356, 360, 364, 368, 372, 376, 380, 384, 388, 392, 396, 400, 404, 408, 412,
    416, 420, 424, 428, 432, 436, 440, 444, 448, 464, 468, 472, 476, 528, 532,
    916, 920, 924, 928, 932, 936, 940, 948, 956, 980, 1100, 1104, 1108, 1112,
    1116, 1120, 1388, 1632, 1652, 1656, 1768, 1776, 1784, 1792, 1800, 1808,
    1816, 1820, 1824, 1828, 1832, 1880, 1884, 1888, 1916, 1920, 2124, 2128,
    2132, 2136, 2140, 2144, 2236, 2240, 2284, 2300, 2304, 2308, 2312, 2316,
    2320, 2324, 2356, 2360, 2364, 2368, 2372, 2376, 2404, 2408, 2412, 2416,
    2420))
# char/bool + pad word-runs (console == PC verbatim, not swapped). UNION of two
# sources so no bool is missed: (a) the codegen's contiguous char-field runs
# (catches bools that were always 0 in the sample, e.g. 88/1372/1628), and
# (b) words learned from the 49 genuine +388 bodies (catches lone bool+pad fields
# the gap heuristic misses, e.g. 1124/1412/2380).
WEAPONDEF_VERB_RUNS = (
    (88, 92), (992, 996), (1124, 1128), (1372, 1376), (1412, 1416),
    (1584, 1596), (1604, 1632), (1640, 1644), (1684, 1688), (1704, 1708),
    (1836, 1840), (1848, 1856), (1872, 1876), (2380, 2384))

# FIX D3 — WeaponDef+1068 (0x42C) is a 16-bit ScriptString, NOT a 32-bit field.
# RPL evidence (Load_WeaponDef @0x02202634, +0xF10..+0xF44):
#     +0xf10  Load_XString(&wd->0x3d4)          <- the +980 XString
#     0x02203554 addi r4, r4, 0x42c   ; 0x02203558 li r5, 2
#     +0xf2c  Load_Stream(0, &wd->0x42c, 2)     <- TWO bytes
#     +0xf40  Load_ScriptStringCustom(varScriptString)
#     +0xf54  Load_XString(&wd->0x44c)          <- the +1100 XString
# It is the ONLY in-struct ScriptString in WeaponDef (the other 20
# Load_ScriptStringCustom sites in the function are the notetrackSoundMap
# keys/values arrays, already covered by the ('u16', 16)/('u16', 20) stream ops;
# Load_WeaponVariantDef's 8 sites are hideTags[32], covered by _u16_array).
# A whole-word swap32 here moved the index into the wrong halfword: PC LE
# `fa 02 00 00` (0x02FA = 'tag_explosive') became `00 00 02 fa` instead of
# `02 fa 00 00`, i.e. the loader read ScriptString 0 and lost the tag.
# REMAP: the zone-local script-string table is carried PC->console
# index-identical (verified: PC vs genuine console mp_raid string lists are
# equal, and mp_raid_authored / zm_nuked_authored reproduce the PC list
# exactly), so — exactly like every other ScriptString in this codebase
# (xmodel boneNames, xanim names, GameWorldMp pathnode tags, hideTags,
# notetrackSoundMap) — the remap is the identity on the INDEX plus a 16-bit
# endian swap. Nothing else is required; there is no index renumbering to undo.
WEAPONDEF_SS_OFFS = frozenset((1068,))

# 388-byte console-only tail [2448..2836) FILL template: 277 bytes are constant
# across all 49 weapons (a class default the WiiU loader expects); the 111
# per-weapon bytes are zeroed here (no PC source). This IS the documented FILL
# policy — for a weapon WITH a genuine console reference the assembler transplants
# the genuine tail positionally (SndBank/Glasses-overlay pattern).
_TAIL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'weapon_tail_template.bin'), 'rb').read()

# WeaponDef inline sub-load stream (after the 2836 struct), in Load_WeaponDef
# order. kind: xs=XString, u16=uint16[n], f32=float[n], vec2=vec2[count@arg],
# xsarr=XString[n], xmod=XModelPtr[16] array, asset=single sub-asset (recurse),
# flame=flameTable. Everything but xs/u16/f32/vec2/xsarr recurses -> Rung 3.
_WD_STREAM = (
    ('xs', 0),                                   # szOverlayName
    ('xmod', 4), ('asset', 8, 'xmodel'),         # gunXModel[16], handXModel
    ('xs', 12),
    ('u16', 16), ('u16', 20),                    # notetrack keys/values[20]
    ('xs', 64),                                  # parentWeaponName
    ('asset', 108, 'fx'), ('asset', 112, 'fx'), ('asset', 116, 'fx'),
) + tuple(('xs', o) for o in range(148, 436, 4)) + (  # pickupSound..shellCasingPlayer
    ('xsarr', 436, SURF_TYPE_NUM),               # bounceSound[32]
    ('xs', 440), ('xs', 444), ('xs', 448),
    ('asset', 464, 'fx'), ('asset', 468, 'fx'),
    ('asset', 472, 'fx'), ('asset', 476, 'fx'),
    ('asset', 528, 'material'), ('asset', 532, 'material'),
    ('xmod', 916),                               # worldModel[16]
    ('asset', 920, 'xmodel'), ('asset', 924, 'xmodel'),
    ('asset', 928, 'xmodel'), ('asset', 932, 'xmodel'),
    ('asset', 936, 'material'), ('asset', 940, 'material'),
    ('asset', 956, 'material'),
    ('xs', 980),                                 # szSharedAmmoCapName
    ('xs', 1100), ('xs', 1104), ('xs', 1108),
    ('xs', 1112), ('xs', 1116), ('xs', 1120),
    ('xs', 1388),                                # stackSound
    ('asset', 1632, 'material'), ('asset', 948, 'material'),
    ('xs', 1652), ('xs', 1656),
    ('asset', 1768, 'xmodel'),                   # projectileModel
    ('asset', 1776, 'fx'), ('asset', 1784, 'fx'), ('asset', 1792, 'fx'),
    ('asset', 1800, 'fx'), ('asset', 1808, 'fx'), ('asset', 1816, 'fx'),
    ('xs', 1820), ('xs', 1824), ('xs', 1828), ('xs', 1832),
    ('f32', 1880, SURF_TYPE_NUM), ('f32', 1884, SURF_TYPE_NUM),  # par/perp bounce
    ('asset', 1888, 'fx'), ('asset', 1916, 'fx'),
    ('xs', 1920),                                # projIgnitionSound
    ('xs', 2124),                                # aiVsAiAccuracyGraphName
    # FIX D2 — the "OAT decompiler artifact" note that used to live here was WRONG.
    # The console loader really does size BOTH aiVsAi arrays from the count at
    # +2148 and BOTH aiVsPlayer arrays from the count at +2152; the +2156/+2160
    # originalKnotCount fields are never read by Load_WeaponDef. Raw RPL
    # (Load_WeaponDef @0x02202634), each site: ALLOC(3) -> lwz count -> slwi r5,3
    # (count * sizeof(vec2)) -> Load_Stream(1, ptr, r5):
    #   array +0x854 (2132 aiVsAiKnots)          0x02203850 lwz r9,0x864(r27)   -> 2148
    #   array +0x85c (2140 originalAiVsAiKnots)  0x022038a4 lwz r10,0x864(r27)  -> 2148
    #   array +0x858 (2136 aiVsPlayerKnots)      0x0220390c lwz r12,0x868(r27)  -> 2152
    #   array +0x860 (2144 originalAiVsPlayerKnots) 0x02203960 lwz r0,0x868(r27) -> 2152
    # (0x864 = 2148, 0x868 = 2152.) Stream ORDER below matches the same walk:
    # 2132, 2140, XString@2128, 2136, 2144.
    ('vec2', 2132, 2148), ('vec2', 2140, 2148),  # aiVsAiKnots, originalAiVsAiKnots
    ('xs', 2128),                                # aiVsPlayerAccuracyGraphName
    ('vec2', 2136, 2152), ('vec2', 2144, 2152),  # aiVsPlayerKnots, originalAiVsPlayerKnots
    ('xs', 2236), ('xs', 2240),                  # use/drop hint strings
    ('xs', 2284),                                # szScript
    ('f32', 2300, HITLOC_COUNT),                 # locationDamageMultipliers[21]
    ('xs', 2304), ('xs', 2308), ('xs', 2312), ('xs', 2316),
    ('asset', 2320, 'tracer'), ('asset', 2324, 'tracer'),
    ('xs', 2356), ('xs', 2360),
    ('flame', 2364), ('flame', 2368),
    ('asset', 2372, 'fx'), ('asset', 2376, 'fx'),
    ('asset', 2404, 'fx'), ('asset', 2408, 'fx'), ('asset', 2412, 'fx'),
    ('xs', 2416),                                # throwBackType
    ('asset', 2420, 'camo'),
)


def _convert_weapondef(s, reloc):
    """Load_WeaponDef: 2448-struct (field-aware BE swap) + 388 console-only tail
    + the inline sub-load stream. Rung 2 handles the struct, tail, and all
    non-recursive inline data (strings / notetrack u16 / bounce floats / accuracy
    knots / locationDamage); an inline sub-ASSET (FOLLOW model/fx/material/tracer/
    camo/flame) raises NotImplementedError -> Rung 3."""
    wd = s.o
    pc = s.pc
    W = lambda o: struct.unpack_from('<I', pc, wd + o)[0]
    # rule (EY), interior arm: minted strings for THIS WeaponDef's stale XString
    # fields, keyed by struct offset. `wd_b` is where the struct lands inside the
    # body being built, so the head words can be patched to FOLLOW in place.
    wdm = (MINT.get(_MINT_WD_KEY, {}) or {}).get(wd) if MINT is not None else None
    wdb = MINT2['bounce'].get(wd) if MINT2 is not None else None
    wd_b = len(s.b)
    # struct 2448 -> BE (records pointer spans for the validator)
    _emit_struct(s, wd, WEAPONDEF_PC, WEAPONDEF_PTR_OFFS, WEAPONDEF_VERB_RUNS,
                 reloc, ss_offs=WEAPONDEF_SS_OFFS)
    if wdm:
        for off in wdm:
            struct.pack_into('>I', s.b, wd_b + off, FOLLOW)
            MINT_STATS['wd-minted'] += 1
    # 388-byte console-only tail (template FILL; assembler may transplant genuine)
    if hasattr(s, 'tailspan'):
        s.tailspan = (len(s.b), len(s.b) + 388)
    s.b += _TAIL
    # inline stream (PC bytes continue at wd+2448)
    for op in _WD_STREAM:
        k, off = op[0], op[1]
        v = W(off)
        if k == 'xs':
            if wdm and off in wdm:
                # stale heap word -> mint the recovered string INLINE here, in
                # Load_WeaponDef order. Consumes no PC bytes (the dump carries no
                # string at that address), so s.o is untouched by construction.
                _mint_cstr(s, wdm[off])
            elif v == FOLLOW:
                s.cstr()
        elif k == 'u16':
            if v == FOLLOW:
                s.u16(NOTETRACK_COUNT)
        elif k == 'f32':
            if v == FOLLOW:
                s.u32(op[2])
        elif k == 'vec2':
            if v == FOLLOW:
                cnt = W(op[2])
                s.u32(cnt * 2)
        elif k == 'xsarr':
            # MINT2['bounce'][wd] is the 32-element LIST for bounceSound (+436),
            # the only xsarr in WeaponDef; keying it by offset would index the
            # list by 436 and silently never fire (it did, first time round).
            if wdb and off == 436:            # stale bounceSound[32] head
                _mint_array(s, wd_b, off, wdb, 'bounce-array-minted')
            elif v == FOLLOW:
                _xstring_array(s, op[2], reloc)
        elif k == 'xmod':
            if v == FOLLOW:
                _ptr_array_assets(s, 16, reloc, 'xmodel')
            elif off == 916 and WORLDMODEL_NULL:
                # b104: this weapon's worldModel alias resolves into a foreign
                # asset body and no donor is derivable -> declare no model.
                # Keyed on the weapon's IDENTITY, never on a map name.
                _nm = (MINT.get(_CUR_VARIANT) or {}).get('name') \
                    if MINT is not None else None
                if _nm in WORLDMODEL_NULL:
                    _emit_null_worldmodel(s, wd_b, off)
        elif k == 'asset':
            if v in PTRS:
                _recurse(s, op[2], reloc)
        elif k == 'flame':
            if v == FOLLOW:
                _convert_flametable(s, reloc)


FLAMETABLE_SIZE = 484
FLAMETABLE_PTR_OFFS = frozenset((432, 436, 440, 444, 448, 452, 456, 460, 464,
                                 468, 472, 476, 480))


def _convert_flametable(s, reloc):
    """Load_flameTable: 484-byte all-float body (13 trailing ptr words) + name +
    fire/smoke/heat/drips/streamFuel/streamFuel2/streamFlame/streamFlame2
    Materials + 4 sound XStrings."""
    ft = s.o
    pc = s.pc
    F = lambda o: struct.unpack_from('<I', pc, ft + o)[0]
    _emit_struct(s, ft, FLAMETABLE_SIZE, FLAMETABLE_PTR_OFFS, (), reloc)
    if F(432) == FOLLOW:                                 # name
        s.cstr()
    for mo in (436, 440, 444, 448, 452, 456, 460, 464):  # 8 flame materials
        if F(mo) in PTRS:
            _recurse(s, 'material', reloc)
    for so in (468, 472, 476, 480):                      # 4 flame sounds
        if F(so) == FOLLOW:
            s.cstr()


def _inline_material(s, reloc):
    import material_convert as MC
    body, nxt = MC.convert_material(s.pc, s.o, reloc)
    s.b += body
    s.o = nxt
