#!/usr/bin/env python3
"""LANE C — VehicleDef PC(LE) -> console(BE) converter (zm_transit bus).

RE RESULT (2026-07-15, from genuine zm_transit_original.zone vs PC zm_transit.zone,
the ONE paired instance: console row 2641 <-> PC row 2638):

  * console struct = 2628 (Load_VehicleDef @0x21e0eb4 first Load_Stream r5=0xa44),
    PC/OAT struct  = 2604. Delta +24.
  * The fixed struct is PC-IDENTICAL in layout through offset 0xa2c (2604): every
    scalar (int/float/int16) matches after a per-field endian swap, pointers only
    reloc. Verified field-by-field with the OAT VehicleDef layout (see _veh_probe.py;
    computed PC size = 2604 exact, string tail lands at 2604 PC / 2628 console).
  * THE +24 IS A CONSOLE-ONLY 24-BYTE TAIL appended AFTER customBool2 (@2604) --
    NOT a mid-struct insertion (csvInclude/customFloat/customBool sit at identical
    offsets in both; the byte-identical inline-string tail simply starts 24 B later
    on console). 6 words; bus values (BE): 00000000 42200000 00000000 3f800000
    3f800000 00000000  = {int 0, float 40.0, int 0, float 1.0, float 1.0, int 0}.
    These are WiiU-only VehicleDef fields absent from T6_Assets.h -> not derivable
    from PC. (This is why struct_layout(console) under-reported by 24: it has no
    field list for the console tail.)
  * SEPARATE console divergence: antenna[2] (@0x9f0, 32 B) is ZEROED on the genuine
    console body while PC carries springK/damp/length = 20/0.05/32. Same offset,
    same size -- a value/content difference, not endian and not the +24. Also not
    derivable from PC.

Both non-derivable regions are handled the same way the rest of the linker handles
console-authored fields it cannot compute from PC (SNDBANK_ALIAS_ORACLE,
GLASSES_PTR_OVERLAY): an optional positional VEHICLE_ORACLE transplanted from the
genuine twin gives a byte-EXACT body; without it the converter emits a
structurally-valid body (PC antenna swapped through, tail = the measured default).

Only ONE VehicleDef exists in zm_transit. zm_nuked and other zm maps must
re-validate the tail/antenna interpretation when their oracles exist (record any
per-map delta here).
"""
import os
import struct
import _veh_probe as VP

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

# console-only 24-byte struct tail (BE), default = the measured zm_transit bus tail.
DEFAULT_TAIL = bytes.fromhex('000000004220000000000000' '3f8000003f80000000000000')
assert len(DEFAULT_TAIL) == 24

# Optional per-instance oracle transplanted from the genuine console body for the
# regions not derivable from PC. Keyed nowhere (single instance); set before calling.
#   'antenna' : 32 raw BE bytes for offset 0x9f0..0xa10  (None -> swap PC through)
#   'tail'    : 24 raw BE bytes for the console-only tail (None -> DEFAULT_TAIL)
VEHICLE_ORACLE = None

# field-offset map (validated: PC size == 2604), built once
_FIELDS, _PC_SIZE = VP.build()
assert _PC_SIZE == 2604, _PC_SIZE
ANTENNA_OFF, ANTENNA_LEN = 0x9f0, 32
PTR_OFFSETS = tuple(o for (n, t, o) in _FIELDS if t == 'ptr')


# ─────────────────────────────────── b93: the empty-char* column string mint
# ⭐ WHAT IT REPAIRS. MEASURED on b92: 69 of the 79 fields the ENGINE declares
# CSPFT_STRING all carry the SAME word 0xA394FE43 -- the relocated form of the PC
# zone's deduped-empty-string handle 0xA3C8D2B9. On the console that word decodes
# to block-5 rt 60,096,066, which has ZERO REGISTRANTS (`rt_of_file` calls it
# `interp+14402`) and lands 14 KB inside an XMODEL. The b91 crash dump shows the
# console really reading `c6 9b 41 a0 6b e8 41 fc ...` there -- 17 bytes of float
# data -- and pasting them into `va("vehicle/%s", steerGraph)`, which is the
# Com_Error that aborts the map load.
#
# ⭐ THE REPAIR IS RETAIL'S OWN SHAPE. Retail zm_transit points steerGraph AND
# accelGraph at ONE object: ('cstr', 2535843, 2535844), a length-1 empty string,
# owner WEAPON, rt 2,479,424 (mod4 0, exactly ONE registrant), word 0xA025D541 --
# an ADDRESS-alias to the "" BYTES. b93 emits the same shape against an
# exact-registered "" in our own zone.
#
# ⛔ SAME SIZE, NEVER A RESHAPE. Each substitution is a 4-byte word -> 4-byte
# word inside the STRUCT BODY. Nothing moves, the struct stays 2628, the span
# stays 2683, and `convert_vehicledef_span`'s VERBATIM inline-string tail copy is
# untouched -- which is the whole reason Route A keeps the b91 dump's frame valid.
#
# ⛔ DEFAULT OFF, AND ARMED ONLY BY A PROVEN ARM FILE. The converter runs before
# `loader_sim` exists, so it cannot certify a runtime offset itself; inventing one
# from the artefact under construction would be a tautology. `_b93_preflight.py`
# proves the target (exact registrant, sole registrant, and a BYTE READ of real
# guest memory) and freezes the result. Three independent guards stop that file
# being applied to the wrong bake: the PC zone LENGTH, the exact old word in every
# cell it overwrites, and an absolute count of 69.
#   arm : T6_VEH_STRING_MINT=1        -> use ./_b93_veh_mint_arm.json
#         T6_VEH_STRING_MINT=<path>   -> use that arm file
#   off : unset, or =0 / off / none, or set VEHICLE_STRING_MINT = False
# ⛔ `produce_nobackbone.py` (the fenced dispatcher at its `root == 'VehicleDef'`
# branch, line 1204-1206, which calls `convert_vehicledef_span(PC, s, e, reloc)`)
# needs NO edit: arming is environment-side, not call-site-side.
VEHICLE_STRING_MINT = None      # None -> consult the env ; False -> hard OFF
_MINT_OFF = ('', '0', 'off', 'no', 'none', 'false')
_MINT_ON = ('1', 'on', 'yes', 'true', 'b93')


def string_mint_spec():
    """-> the frozen arm spec, or None when the mint is not armed."""
    if VEHICLE_STRING_MINT is False:
        return None
    if isinstance(VEHICLE_STRING_MINT, dict):
        return VEHICLE_STRING_MINT
    raw = VEHICLE_STRING_MINT or os.environ.get('T6_VEH_STRING_MINT', '')
    raw = str(raw).strip()
    if raw.lower() in _MINT_OFF:
        return None
    import veh_string_mint as VSM
    return VSM.load_arm(None if raw.lower() in _MINT_ON else raw)


def _apply_string_mint(out, pc_len, spec):
    """Substitute the minted words IN PLACE in the console struct body.
    -> {offset: new_word} actually written. Raises rather than guessing."""
    import veh_string_mint as VSM
    t0 = VSM.string_field_offsets(spec['rows'])
    for off, name in t0:
        if off + 4 > len(out):
            raise VSM.MintRefusal(
                'declared string field %r is at +%d but the console struct body '
                'is only %d bytes -- the field table and the body disagree'
                % (name, off, len(out)))
    cells = {off: struct.unpack_from('>I', out, off)[0] for off, _n in t0}
    subs = VSM.build_from_arm(spec, cells, pc_zone_len=pc_len)
    for off, word in subs.items():
        struct.pack_into('>I', out, off, word)
    return subs


def _default_reloc(v):
    return v


def convert_vehicledef(pc, off, reloc=_default_reloc):
    """PC(LE) VehicleDef -> console(BE): field-swap the 2604 struct per the OAT
    layout, append the console-only 24-B tail, copy the inline-string tail verbatim.
    Returns (body_bytes, pc_end). pc_end = off + 2604 + len(string tail)."""
    struct_pc = pc[off:off + _PC_SIZE]
    out = bytearray(struct_pc)                      # pad bytes stay verbatim (zero)
    for (name, ty, o) in _FIELDS:
        if ty == 'bool':                            # 1 byte, no swap
            continue
        if ty in ('i16', 'u16'):
            out[o:o + 2] = struct_pc[o:o + 2][::-1]
        elif ty == 'ptr':
            v = struct.unpack_from('<I', struct_pc, o)[0]
            struct.pack_into('>I', out, o, reloc(v))
        else:                                       # f / i / u : 4-byte swap
            out[o:o + 4] = struct_pc[o:o + 4][::-1]

    # non-derivable region 1: antenna[2] (transplant from genuine when available)
    if VEHICLE_ORACLE and VEHICLE_ORACLE.get('antenna') is not None:
        ant = VEHICLE_ORACLE['antenna']
        assert len(ant) == ANTENNA_LEN
        out[ANTENNA_OFF:ANTENNA_OFF + ANTENNA_LEN] = ant

    # +24 console-only tail (non-derivable region 2)
    tail = (VEHICLE_ORACLE.get('tail') if VEHICLE_ORACLE else None) or DEFAULT_TAIL
    assert len(tail) == 24
    out += tail

    # b93 EMPTY-CHAR*-COLUMN MINT -- STRUCT BODY ONLY, same size, after reloc.
    # It runs here (not earlier) because it rewrites the RELOCATED word, and
    # `out` is now the full 2628-byte console struct so every declared offset,
    # including any in the console-only +24 tail, is addressable.
    spec = string_mint_spec()
    convert_vehicledef.last_mint = None
    if spec is not None:
        subs = _apply_string_mint(out, len(pc), spec)
        convert_vehicledef.last_mint = dict(
            count=len(subs), word='0x%08X' % next(iter(subs.values())),
            target_rt=spec['target_rt'], arm=spec.get('_path'),
            provenance=spec.get('provenance'))

    # dynamic inline-string tail: byte-identical console<->PC (all XString content),
    # copied verbatim. The end of the asset = the walker's pc_end.
    # Caller supplies pc_end via the walk; here we mirror smalls_convert's contract of
    # returning the body up to that end. We only know the struct end; the string span
    # is appended by the caller from the walked PC span. Return struct-body + a marker
    # so the assembler concatenates the verbatim string tail. For standalone/byte-exact
    # validation the helper convert_vehicledef_span() does the whole span.
    return bytes(out), off + _PC_SIZE


def convert_vehicledef_span(pc, off, pc_end, reloc=_default_reloc):
    """Full-span variant used by the validator/assembler: struct(2628) + verbatim
    inline-string tail taken from the PC span [off+2604, pc_end)."""
    body, struct_end = convert_vehicledef(pc, off, reloc)
    return body + pc[struct_end:pc_end], pc_end
