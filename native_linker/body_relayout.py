#!/usr/bin/env python3
"""
Stage 1 (increment 2): asset-BODY re-layout.

Walks a genuine console zone's asset graph exactly like wiiu_ref/walker.py, but
instead of only advancing a cursor it RE-EMITS every consumed region through the
native ZoneWriter, patching alias pointers via a source->writer offset map. Byte-
identity of the re-emitted stream proves the writer reproduces the original linker's
layout for that asset type. Where it diverges, the first mismatch pinpoints the exact
struct/directive to fix (the "resync at divergence" method, made exact for writing:
the reader could paper over a size error with next-body resync; the writer cannot).

Design: the walk consumes source bytes sequentially (self.src = a cursor into the
genuine stream). Each consumed region is copied to the writer with its pointer fields
patched (FOLLOW/null kept; alias remapped through self.omap), and its source block-5
offset registered so later back-aliases resolve. For a faithful round-trip the writer
offset equals the source offset, so remap is identity — but it is computed, not
assumed, so any layout drift shows up as a byte divergence.

Usage: python body_relayout.py [genuine.zone] [max_assets]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import zone_stream as zs
import struct_layout
import walker as W
import shader_probe as SP
import fx_probe as FP
import xmodel_probe as XP
import destructibledef_probe as DP
import xanimparts_probe as XAP

# Solved external delimiters for asset types whose console layout has no structural
# re-emitter yet. Each returns the END file offset given the body start; the region is
# copied verbatim (byte-exact for a round-trip). Stage 2/3 replaces these with real
# structural emission when authoring/synthesis needs it.
def _techset_end(z, o): return SP.parse_techset(z, o)[0]
def _xanim_end(z, o):   return XAP.parse_xanim(z, o, '>')[0]   # console 104-B XAnimParts + streamed data
def _fx_end(z, o):      return FP.parse_fx(z, o)[0]
def _xmodel_end(z, o):  return XP.parse_xmodel(z, o)[0]
def _destruct_end(z, o): return DP.parse_destructible(z, o, '>')[0]

def _lightdef_end(z, o):
    # console GfxLightDef: 16B body {name, attenuation.image, samplerState, lmapLookupStart}
    # + [name string if name FOLLOW] + [inline console GfxImage(328B) if image FOLLOW/INSERT].
    c = XP.Cur(z, o + 16)
    name_p = struct.unpack_from('>I', z, o)[0]
    img_p = struct.unpack_from('>I', z, o + 4)[0]
    if name_p in XP.PTRS:
        c.cstr()
    if img_p in XP.PTRS:
        XP.consume_image(z, c)
    return c.o

def _material_end(z, o):
    c = XP.Cur(z, o)
    XP.consume_material(z, c)
    return c.o

def _spt_end(z, o):
    # console ScriptParseTree {name* FOLLOW, len, buffer* FOLLOW} = 12-B body + inline name string
    # + len-byte script buffer (+1 trailing). The generic walker consumes only the 12-B body (it
    # can't size the buffer from the `len` field), so a big GSC text blob gets read as dozens of
    # bogus 12-B SPT assets -> drift. scriptparsetree_probe.parse_spt sizes it exactly.
    F = 0xffffffff
    if z[o:o+4] == b'\xff' * 4 and z[o+8:o+12] == b'\xff' * 4:
        import scriptparsetree_probe as SPT
        return SPT.parse_spt(z, o, '>')[0]
    ln = struct.unpack_from('>I', z, o + 4)[0]
    c = o + 12
    if struct.unpack_from('>I', z, o)[0] == F:
        c = z.index(b'\x00', c) + 1
    if struct.unpack_from('>I', z, o + 8)[0] == F:
        c += ln + 1
    return c

def _sndbank_end(z, o):
    # console SndBank: large inline asset (alias/radverb/duck lists, embedded runtime bank
    # strings, SndAssetBankEntry array + dataSize blob). sndbank_probe walks it exactly.
    import sndbank_probe as SB
    return SB.parse_sndbank(z, o, '>')[0]

# next-asset hint for _weapon_end resync: a list of the UPCOMING body-bearing
# (FOLLOW) asset root names after the weapon being walked. Set by walk loops
# (loader_sim.simulate_stream) before dispatching a WeaponVariantDef; None ->
# the legacy RAWFILE-cluster heuristic below (raid/MP weapons, unchanged).
WEAPON_NEXT_HINT = None

def _rawfile_span(z, c):
    """Console RawFile: {name* , len, buffer*} 12-B body + inline name +
    len+1 buffer bytes (name/buffer ptrs may be FOLLOW or dedup aliases)."""
    F = 0xffffffff
    u = lambda o: struct.unpack_from('>I', z, o)[0]
    ln = u(c + 4)
    if ln == 0 or ln > 0x400000:
        raise RuntimeError('rawfile len implausible')
    o = c + 12
    if u(c) == F:
        e = z.index(b'\x00', o, o + 128)
        if e == o or not all(0x20 <= ch < 0x7f for ch in z[o:e]):
            raise RuntimeError('rawfile name implausible')
        o = e + 1
    if u(c + 8) == F:
        o += ln + 1
    return o

def _spt_span(z, c):
    """console ScriptParseTree span: {name* FOLLOW, len, buffer*} 12-B + inline
    name + (inline GSC buffer + NUL when buffer==FOLLOW & len>0). Handles the
    comma-referenced NULL-buffer variant (len 0, buffer 0 -> name only). Used as
    a weapon-resync chain link (zm_nuked: weapon followed by XModel then 2 SPTs;
    without an SPT span the chain stalled at the 2nd link and the resync failed
    -> the weapon over-consumed 27KB into the next XModel, 2026-07-15)."""
    F = 0xffffffff
    u = lambda p: struct.unpack_from('>I', z, p)[0]
    ln, buf_p = u(c + 4), u(c + 8)
    e = z.index(b'\x00', c + 12) + 1                # inline name string
    if buf_p == F and ln > 0:
        e += ln + 1                                # inline buffer + trailing NUL
    return e

# console span parsers usable as chain validators in the weapon resync
_RESYNC_SPAN = {
    'FxEffectDef':          lambda z, c: DELIMITERS['FxEffectDef'](z, c),
    'Material':             lambda z, c: DELIMITERS['Material'](z, c),
    'MaterialTechniqueSet': lambda z, c: DELIMITERS['MaterialTechniqueSet'](z, c),
    'XModel':               lambda z, c: DELIMITERS['XModel'](z, c),
    'XAnimParts':           lambda z, c: DELIMITERS['XAnimParts'](z, c),
    'ScriptParseTree':      _spt_span,
    'RawFile':              _rawfile_span,
}

def _detect_weapon_start(z, p):
    """WeaponVariantDef start: szInternalName* == FOLLOW, word @4 == 0,
    szDisplayName @8 == FOLLOW or a block-5 string alias, and a printable
    NUL-terminated internal name inline at +716 that does NOT begin
    mid-string (both PC and console layouts share the 716-byte variant
    body; measured on zm_transit genuine weapon starts vs interior
    false-fires)."""
    u = lambda o: struct.unpack_from('>I', z, o)[0]
    F = 0xffffffff
    ptr = lambda v: v == F or 0xA0000001 <= v <= 0xBFFFFFFF
    # measured console variant-body fingerprint (5 genuine starts): words
    # 0..13 ALL pointer-space (name/displayName/attachment table) except
    # word1 == 0, then a long run of zero words from word14
    v0 = u(p)
    if not ptr(v0) or u(p + 4) != 0:
        return False
    if not all(ptr(u(p + k)) for k in range(8, 56, 4)):
        return False
    for k in range(56, 120, 4):
        v = u(p + k)
        # zeros, small counts, or positive floats (fov/scale tunables) only —
        # excludes text bytes (0x61+ leading), aliases and FOLLOW (measured)
        if not (v < 0x10000 or 0x38000000 <= v <= 0x49FFFFFF):
            return False
    if v0 != F:
        # alias-named weapon (all its strings dedup'd): no inline name to
        # verify — the field fingerprint above is the whole signature
        return True
    q = p + 716
    if 0x20 <= z[q - 1] < 0x7f:            # name begins mid-string: not a start
        return False
    e = z.find(b'\x00', q, q + 64)
    return (e > q + 2 and
            all(c in b'abcdefghijklmnopqrstuvwxyz0123456789_+' for c in z[q:e]))

def _detect_zbarrier_start(z, p):
    """ZBarrier start: name* == FOLLOW and a printable inline name at +560
    not beginning mid-string (zm_transit measured; an alias-name acceptance
    here false-fires on interior alias words — keep FOLLOW-only)."""
    v = struct.unpack_from('>I', z, p)[0]
    if v != 0xffffffff:
        return False
    q = p + 560
    if 0x20 <= z[q - 1] < 0x7f:
        return False
    e = z.find(b'\x00', q, q + 64)
    return (e > q + 2 and
            all(0x21 <= ch < 0x7f for ch in z[q:e]))


def _weapon_end_resync(z, o, roots, depth=3, try_ends=None, scan_lo=716):
    """ZM weapons (zm_transit): the weapon is NOT followed by the MP
    attachment+RAWFILE cluster — resync on the NEXT listed asset instead.
    Scan forward for the first offset where a chain of the next `depth`
    body-bearing assets parses cleanly with the validated console span
    parsers. FX name pointers may be dedup ALIASES (no inline name), so
    candidates anchor on any pointer-space first word (FOLLOW or block-5)."""
    F = 0xffffffff
    u = lambda p: struct.unpack_from('>I', z, p)[0]
    DETECT_ONLY = {'WeaponVariantDef': _detect_weapon_start,
                   'ZBarrierDef': _detect_zbarrier_start}
    if not roots or (roots[0] not in _RESYNC_SPAN and roots[0] not in DETECT_ONLY):
        raise RuntimeError('weapon resync: unsupported follower %r' % (roots and roots[0],))

    def _name_ok(q, minlen=4):
        e = z.find(b'\x00', q, q + 96)
        return e >= q + minlen and all(0x21 <= ch < 0x7f for ch in z[q:e])

    # name-ptr sanity per root: a FOLLOW name ptr must be backed by a sane
    # inline name at the known body size; a block-5 ALIAS name (linker string
    # dedup — observed on zm_transit FX) is accepted, the parse validates it.
    # (console body sizes: FX 76, Material 104, XAnimParts 104, XModel 244,
    # MaterialTechniqueSet 136 — measured on zm_transit genuine)
    NAME_AT = {'FxEffectDef': 76, 'Material': 104, 'XAnimParts': 104,
               'XModel': 244, 'MaterialTechniqueSet': 136, 'ScriptParseTree': 12}
    # a real asset always exceeds its bare body size — a bare-body parse is
    # the signature of a mis-aligned zero/alias region ("104-byte anims")
    MIN_SPAN = {'FxEffectDef': 80, 'Material': 108, 'XAnimParts': 140,
                'XModel': 250, 'MaterialTechniqueSet': 140, 'ScriptParseTree': 13}

    def _start_ok(root, c):
        na = NAME_AT.get(root)
        if na is None:
            return True
        v = u(c)
        if 0xA0000001 <= v <= 0xBFFFFFFF:
            return True
        return v == F and _name_ok(c + na)

    def _chain(p, chain_roots, need_terminal):
        """Parse chain_roots starting at p. Returns True iff every span parses
        AND (when need_terminal) the chain lands EXACTLY on a weapon-start
        signature — the global landing constraint that kills the interior
        false-fires a short local chain can't."""
        c = p
        parsed = 0
        for root in chain_roots:
            det = DETECT_ONLY.get(root)
            if det is not None:
                return det(z, c)
            fn = _RESYNC_SPAN.get(root)
            if fn is None:      # unsupported type: chain proven this far
                return parsed >= 2
            if not _start_ok(root, c):
                return False
            try:
                c2 = fn(z, c)
            except Exception:
                return False
            if not (c + MIN_SPAN.get(root, 1) <= c2 <= len(z)):
                return False
            c = c2
            parsed += 1
        return not need_terminal or parsed >= 2

    # tier 0: PC-pair size PRIORS (console full weapon == PC size + 388,
    # variant-only == PC size; measured exact on zm_transit) — chain-validated
    # before any scan, so alias-named weapons resolve deterministically
    for e0 in (try_ends or ()):
        if o + scan_lo <= e0 < len(z) - 16 and _chain(
                e0, roots, 'WeaponVariantDef' in roots):
            return e0

    hi = min(len(z) - 16, o + (1 << 21))
    # tier 1: the FULL follower chain (up to the next weapon, terminal-checked);
    # tier 2: progressively shorter local chains if a mid-chain parser can't
    # handle some body (accepting a weaker but still multi-asset proof)
    tiers = [roots]
    if len(roots) > depth:
        tiers.append(roots[:depth])
    if depth > 2:
        tiers.append(roots[:2])
    for chain_roots in tiers:
        need_terminal = chain_roots is roots and 'WeaponVariantDef' in roots
        p = o + scan_lo
        while p < hi:
            v = u(p)
            if v != F and not (0xA0000001 <= v <= 0xBFFFFFFF):
                p += 1
                continue
            if _chain(p, chain_roots, need_terminal):
                return p
            p += 1
    raise RuntimeError('weapon resync: no follower chain (%s) after 0x%x' %
                       ('/'.join(roots[:depth]), o))

def _weapon_end(z, o):
    # console WeaponVariantDef with an INLINE weapDef (FOLLOWING). The genuine WiiU v148
    # WeaponDef struct diverges from the OAT T6_Assets.h header (field offsets AND body size),
    # so neither the generic walker nor a load_db interpreter reproduces its extent (both assume
    # the PC 2448-B body and drift into the inline reticle/hud material's image pixels). Until the
    # genuine console WeaponDef layout is reversed, bound the weapon by its trailing structure:
    # a WeaponVariantDef is followed by its WeaponAttachment asset (szInternalName inline), then
    # the weapon's RAWFILE cluster (rumble/*). Anchor on the first RAWFILE (name* FOLLOW, sane len,
    # buffer* FOLLOW, printable inline name), walk back over the attachment's inline name string(s)
    # and its 284-B body -> weapon end == attachment body start. The weapon body is copied
    # verbatim with its alias pointers relinked (VERBATIM_RELINK); the WeaponAttachment then walks
    # generically. VALIDATES the attachment body (szInternalName* == FOLLOW) or raises.
    if WEAPON_NEXT_HINT:
        # ZM weapon path (zm_transit): resync on the hinted next-asset chain;
        # fall through to the legacy heuristic if the chain never matches
        # (e.g. an MP weapon walked with a hint set). Hint = list of roots, or
        # dict {'roots': [...], 'ends': [PC-pair-derived end candidates]}.
        h = WEAPON_NEXT_HINT
        try:
            if isinstance(h, dict):
                return _weapon_end_resync(z, o, h['roots'],
                                          try_ends=h.get('ends'))
            return _weapon_end_resync(z, o, h)
        except Exception:
            pass
    F = 0xffffffff
    def u(p): return struct.unpack_from('>I', z, p)[0]
    ATT_BODY = 284
    R = None
    p = o + 3164                                   # min WeaponVariantDef(716)+WeaponDef(2448) body
    while p + 16 < len(z):
        if (u(p) == F and 0 < u(p + 4) < 0x40000 and u(p + 8) == F
                and 0x20 <= z[p + 12] < 0x7f):
            # confirm a second rawfile follows within the first's extent+slack (cluster)
            R = p
            break
        p += 1
    if R is None:
        raise RuntimeError('WeaponVariantDef: no trailing RAWFILE cluster after 0x%x' % o)
    # walk back over the attachment's inline name string(s) directly preceding R
    s = R
    while s > o and 0x20 <= z[s - 1] < 0x7f:
        s -= 1
    # s = start of the last printable run (attachment szInternalName). Skip a preceding
    # szDisplayName string if present (another printable run + its null).
    att_body = s - ATT_BODY
    if not (o < att_body < R and u(att_body) == F):
        # try one extra preceding string (szDisplayName inline before szInternalName)
        s2 = s - 1
        while s2 > o and 0x20 <= z[s2 - 1] < 0x7f:
            s2 -= 1
        att_body = s2 - ATT_BODY
        if not (o < att_body < R and u(att_body) == F):
            raise RuntimeError('WeaponVariantDef: attachment body not found before 0x%x' % R)
    return att_body

def _vehicle_end(z, o):
    # console VehicleDef: layout drifts from the PC header (zm_transit bus:
    # console 2682 vs PC 2658; the generic walk under-consumes 71 B) — resync
    # on the hinted next-asset chain like ZM weapons. Vehicles are small, so
    # the scan starts just past the alias-name head.
    if WEAPON_NEXT_HINT:
        h = WEAPON_NEXT_HINT
        roots = h['roots'] if isinstance(h, dict) else h
        return _weapon_end_resync(z, o, roots, scan_lo=1024)
    raise RuntimeError('VehicleDef: no next-asset hint set')


def _fonticon_end(z, o):
    # console FontIcon (fonticon_t6_load_db). 20-B body {name*, numEntries, numAliasEntries,
    # fontIconEntry*, fontIconAlias*}. The generic walker mis-sizes it (it has NO structural
    # model for the per-entry inline sub-tree) and slurps to EOF. Layout per the load_db:
    #   name XString (inline if FOLLOW)
    #   fontIconEntry[] : first the whole 24-B array block (FontIconName{string*,hash},
    #     Material* handle, size, xScale, yScale), THEN per-entry sub-data in order:
    #     the FontIconName.string (inline cstr if FOLLOW) + a full inline console Material
    #     (if fontIconMaterialHandle is FOLLOW/INSERT; an aliased handle has no inline data).
    #   fontIconAlias[] : numAliasEntries x 20 B  (Wii U console FontIconAlias is 20 B, NOT the
    #     PC 8-B {aliasHash,buttonHash} -- measured byte-exact: patch_mp buttons.csv alias run =
    #     1960 B / 98 = 20, and the next asset's Material header lands exactly at end.)
    F = 0xffffffff
    def u(p): return struct.unpack_from('>I', z, p)[0]
    name_p, num, num_alias, entry_p, alias_p = struct.unpack_from('>IIIII', z, o)
    c = o + 20
    if name_p in XP.PTRS:
        c = z.index(b'\x00', c) + 1
    if entry_p in XP.PTRS:
        arr = c
        c += 24 * num
        for i in range(num):
            e = arr + 24 * i
            if u(e + 0) in XP.PTRS:            # FontIconName.string inline
                c = z.index(b'\x00', c) + 1
            if u(e + 8) in XP.PTRS:            # fontIconMaterialHandle inline Material
                cc = XP.Cur(z, c); XP.consume_material(z, cc); c = cc.o
    if alias_p in XP.PTRS:
        c += 20 * num_alias
    return c

def _gwmp_end(z, o):
    # GameWorldMp {alias name, PathData}. gameworldmp_probe walker, console node_size=144.
    import gameworldmp_probe as GW
    end, _ = GW.Walker(z, '>', 144).walk(o)
    return end

def _image_end(z, o):
    c = XP.Cur(z, o)
    XP.consume_image(z, c)
    return c.o

def _gfxworld_end(z, o):
    # gfxworld_probe2's structural walker, driven at body offset `o`. Bypass its
    # file-reading __init__ and feed our zone bytes; suppress its diagnostic prints.
    import gfxworld_probe2 as G2, io, contextlib
    cfg = dict(G2.CFG['wiiu']); cfg['body'] = o
    p = G2.W.__new__(G2.W)
    p.c = cfg; p.d = z; p.e = cfg['endian']; p.b = o; p.o = o + cfg['bodysize']
    with contextlib.redirect_stdout(io.StringIO()):
        G2.walk(p)
    end = p.o
    # gfxworld_probe2 stops before the console siege-skin shader tail (fixed 11085B block,
    # starts ffffffff ffffffff 00000000 "gpuskin1"). Append it if present.
    SIEGE = 11085
    if z[end:end+12] == b'\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00':
        end += SIEGE
    return end

def _comworld_end(z, o):
    # console ComWorld: 16B body {name FOLLOW, isInUse, primaryLightCount, primaryLights}
    # + primaryLightCount x ComPrimaryLight(168B, defNames aliased so no inline strings)
    # + the ComWorld name string (reordered to AFTER the lights). Walk lights while the
    # first word is a valid light type (1..8); the name string starts with ASCII (>8).
    p = o + 16
    while p + 168 <= len(z):
        t = struct.unpack_from('>I', z, p)[0]
        if not (1 <= t <= 8):
            break
        p += 168
    return z.index(b'\x00', p) + 1     # trailing name c-string

def _menulist_end(z, o):
    # console MenuList: the WiiU (v148) menu graph — menuDef_t/itemDef_s/windowDef_t and the
    # ExpressionStatement/inline-Material sub-tree — does NOT match the T6 load_db's console
    # (SwapEndianness) FillStruct offsets: the genuine menuDef_t body is 424 B (not the load_db's
    # 392), window.name is the first dynamic string, and itemCount/expression fields sit at
    # offsets the load_db doesn't describe (verified on patch_mp "ui/default.menu": body @0x9726
    # ends at 0x98ce where window.name "default_menu" begins, with an inline material + an item
    # "@MENU_MENU_COULDNT_BE_FOUND" following). Re-deriving that whole sub-graph from one mostly
    # -empty stub is unreliable, and menus are copied UNCHANGED for the mapsTable relink — so bound
    # the MenuList like the other hard console assets (GfxWorld/techset/XModel) and copy verbatim.
    #
    # Bound structurally where possible, else by the next asset's header.
    # MenuList body {name* FOLLOW, menuCount, menus** FOLLOW} + inline name string, then (if
    # menuCount>0) menuCount menuDef pointers + inline menuDef graphs.
    #  * menuCount == 0 (empty menulist, common on the TU zm splitscreen HUDs): body is just the
    #    12-B header + the inline name -> bound EXACTLY, no scan.
    #  * menuCount  > 0: the inline menuDef sub-graph has no structural re-emitter (genuine WiiU
    #    menuDef_t is 424 B and diverges from the load_db); bound at the NEXT asset start. In these
    #    zones a MenuList is always followed by another MenuList or a StringTable, both with a
    #    distinctive inline name: MenuList {name* F, menuCount 0..64, menus* F} + "<ui...>.txt", or
    #    StringTable {name* F, cols<64, rows<4096, values* F, cellIndex* F} + "<path>.csv". Byte-step
    #    (console bodies are not 4-aligned). ".txt" (not ".menu") avoids matching menuDef window
    #    names inside the graph.
    F = 0xffffffff
    def u(p): return struct.unpack_from('>I', z, p)[0]
    name_p, count, menus_p = u(o), u(o + 4), u(o + 8)
    c = o + 12
    if name_p in (F, zs.INSERT):
        c = z.index(b'\x00', c) + 1              # inline name string
    if count == 0:
        return c
    p = c
    while p + 24 <= len(z):
        if u(p) == F:
            # StringTable? {name* F, cols<64, rows<4096, values* F, cellIndex* F} + ".csv"
            if u(p + 4) < 64 and u(p + 8) < 4096 and u(p + 12) == F and u(p + 16) == F:
                nm = z[p + 20:p + 20 + 64]; e = nm.find(b'\x00')
                if e > 4 and b'.csv' in nm[:e] and all(32 <= ch < 127 for ch in nm[:e]):
                    return p
            # next MenuList? {name* F, menuCount 0..64, menus* F} + ".txt"
            if u(p + 8) == F and u(p + 4) <= 64:
                nm = z[p + 12:p + 12 + 64]; e = nm.find(b'\x00')
                if 4 < e < 64 and b'.txt' in nm[:e] and all(32 <= ch < 127 for ch in nm[:e]):
                    return p
        p += 1
    raise RuntimeError('MenuList: no following MenuList(.txt)/StringTable(.csv) header after 0x%x' % o)

DELIMITERS = {
    'MaterialTechniqueSet': _techset_end,
    'FxEffectDef': _fx_end,
    'XModel': _xmodel_end,
    'DestructibleDef': _destruct_end,
    'GfxLightDef': _lightdef_end,
    'Material': _material_end,
    'FontIcon': _fonticon_end,
    'WeaponVariantDef': _weapon_end,
    'VehicleDef': _vehicle_end,
    'SndBank': _sndbank_end,
    'ScriptParseTree': _spt_end,
    'GfxImage': _image_end,
    'GfxWorld': _gfxworld_end,
    'GameWorldMp': _gwmp_end,
    'MenuList': _menulist_end,
    'XAnimParts': _xanim_end,   # struct_layout has the PC 92-B body; console is 104-B (xanimparts_probe)
    # ComWorld is handled by the GENERIC walker (console layout == PC), reached via the
    # pre-check recovery that fixes the destructibledef under-read before it.
}
# kept for reference / next session
_UNUSED = _comworld_end


# ---- body detectors: is `o` the start of a valid body of PC asset-type `t`? ----
# Used for forward-scan recovery when a delimiter/probe UNDER-reads (e.g.
# destructibledef_probe misses inline piece sub-assets). We scan for the KNOWN next
# asset's body and extend the previous asset's verbatim copy to that boundary.
def _u32(z, o): return struct.unpack_from('>I', z, o)[0]

def _detect_comworld(z, o):
    # {name FOLLOW, isInUse=1, primaryLightCount 1..64, primaryLights FOLLOW}, then the
    # name string inline right after the 16-byte body (console ComWorld has no reorder).
    if not (o + 20 <= len(z) and _u32(z, o) == zs.FOLLOW and _u32(z, o+4) == 1
            and 1 <= _u32(z, o+8) <= 64 and _u32(z, o+12) == zs.FOLLOW):
        return False
    c = z[o+16]                       # first byte of the name string
    return 0x20 <= c < 0x7f

def _detect_techset(z, o):
    try:
        end, nt = SP.parse_techset(z, o)
        nxt = _u32(z, end)
        return nt >= 1 and (nxt == zs.FOLLOW or 0xA0000000 <= nxt < 0xC0000000)
    except Exception:
        return False

# PC asset type id -> detector. Keep this CONSERVATIVE: a detector with false negatives
# would spuriously trigger the pre-check recovery. Only very specific signatures belong here.
DETECTORS = {
    13: _detect_comworld,   # COMWORLD ({name FOLLOW, isInUse=1, count 1..64, lights FOLLOW})
}

def find_next_body(z, start, pc_type, window=0x8000):
    det = DETECTORS.get(pc_type)
    if det is None:
        return None
    for o in range(start, min(start + window, len(z) - 20)):   # byte-step: bodies pack at any align
        if det(z, o):
            return o
    return None

#: ⛔⛔ B5_BASE IS A CONTAINER-ONLY CONSTANT. IT IS NOT A b5 <-> FILE CONVERSION.
#:
#: The old comment here read "block-5 offset == stream/file offset - 64" as settled fact, and two
#: relinkers built their whole relocation decision on it (`cumdelta(off + B5_BASE)`). It is exact
#: for the script-string table and the asset array, and measurably false everywhere else.
#:
#: MEASURED on the stock live patch_zm (10,316,826 B), oracle = content only (a StringTable cell
#: stores its target's djb2ci hash, so the target is proven by bytes, not by a model):
#:      D = b5 - file over 483 content-confirmed targets
#:      min -1,980,635   max +113,055   31 distinct values   ** D == 64 in 0 of 483 **
#: D is a STEP FUNCTION: adjacent steps as small as 2 bytes (+113,033 -> +113,035 -> +113,039),
#: and as large as -952,067 where non-virtual data intervenes. Bake rule (U) said this already.
#:
#: Cost of ignoring it, on one real 2-table grow: ~1,335 wrongly-relocated pointers in a zone that
#: parses, registers, passes every static gate and dies with NO Com_Error.
#:
#: ⭐ LEGITIMATE USES: seeding a ZoneWriter's own contiguous block-5 counter (self-consistent by
#: construction), and container-region math (rule Q). ⛔ ILLEGITIMATE: resolving or relocating any
#: body-region target. For that, ask native_linker/reloc_model.py, which decides in block-5 space.
B5_BASE = 64

# zonecode block name -> block index; console redirects TEMP -> VIRTUAL
BLOCKMAP = {
    'XFILE_BLOCK_TEMP': zs.BLOCK_VIRTUAL,       # console TEMP->VIRTUAL redirect
    'XFILE_BLOCK_VIRTUAL': zs.BLOCK_VIRTUAL,
    'XFILE_BLOCK_PHYSICAL': zs.BLOCK_PHYSICAL,
    'XFILE_BLOCK_RUNTIME': zs.BLOCK_RT_VIRT,
    'XFILE_BLOCK_RUNTIME_VIRTUAL': zs.BLOCK_RT_VIRT,
    'XFILE_BLOCK_RUNTIME_PHYSICAL': zs.BLOCK_RT_PHYS,
    'XFILE_BLOCK_DELAY': zs.BLOCK_RT_VIRT,
}


# Console (v148) layout quirks the PC structs don't capture. Trailing bytes emitted
# after an asset's dynamics (verified against genuine bytes). SkinnedVertsDef: a
# u32=0 follows the name string (PC struct is just {name, maxSkinnedVerts}).
CONSOLE_TRAIL = {
    'SkinnedVertsDef': 4,
}


class ReEmitter:
    def __init__(self, zone, layout, zc, writer):
        self.z = zone
        self.L = layout
        self.zc = zc
        self.w = writer
        self.src = 0                 # source file cursor (mirrors walker's cur)
        self.omap = {}               # source block-5 offset -> writer block-5 offset
        # delta != 0 marks an EDIT pass: emit_verbatim then relinks its body's alias words via
        # omap (a no-edit round-trip keeps delta=0 and stays byte-identical). The EditEmitter
        # runs two passes so omap is complete before the relinking pass (resolves forward refs).
        self.delta = 0
        # EDIT threshold: every block-5 offset >= shift_from moves by +delta. Used to relink
        # KNOWN structural pointer fields (emit_body / emit_ptr2 / emit_array), which is both
        # correct and complete -- omap alone MISSES targets that aren't registered asset starts,
        # e.g. StringTable cells aliasing a shared string POOL. (Verbatim assets can't tell a
        # pointer from data, so they stay on the conservative omap path.)
        self.shift_from = 0
        # helper walker for scalar reads / count & condition eval / followers
        self.wk = W.Walker(zone, layout, zc, [0] * 8)

    def u32(self, o): return struct.unpack_from('>I', self.z, o)[0]

    # ---- register a region start so back-aliases resolve ------------------
    def register(self, src_file):
        self.omap[src_file - B5_BASE] = self.w.block_size[self.w.cur_block]

    def remap_ptr(self, v):
        """Relink a KNOWN structural pointer field (emit_body / emit_ptr2 / emit_array).
        Deterministic threshold: a block-5 alias whose offset is at/after the insertion point
        moves by +delta. This is correct AND complete for real pointers -- including ones that
        target a shared string pool interior (not a registered asset start, so omap can't see
        it). Safe from data false-positives because it's only ever applied to fields the struct
        layout marks as pointers. delta=0 (no-edit) => identity."""
        if v in (zs.FOLLOW, zs.INSERT, 0):
            return v
        if not (0xA0000000 <= v < 0xC0000000):
            return v                 # not an alias-encoded value: raw data / handle
        blk, off = zs.decode_ptr(v)
        if blk == zs.BLOCK_VIRTUAL and self.delta and off >= self.shift_from:
            return zs.encode_ptr(blk, off + self.delta)
        return v

    def remap_ptr_omap(self, v):
        """Conservative relink for VERBATIM assets (blind 4-aligned scan): only rewrite a word
        whose decoded offset is a genuine REGISTERED target (self.omap), so shader/pixel/audio
        data that merely resembles an alias is left untouched. Misses pool-interior targets, but
        console verbatim assets reference asset starts, not string-pool interiors."""
        if v in (zs.FOLLOW, zs.INSERT, 0):
            return v
        if not (0xA0000000 <= v < 0xC0000000):
            return v
        blk, off = zs.decode_ptr(v)
        if blk == zs.BLOCK_VIRTUAL and off in self.omap:
            return zs.encode_ptr(blk, self.omap[off])
        return v

    # ---- copy a struct body to the writer, patching its pointer fields ----
    def emit_body(self, struct_name, src_file):
        s = self.wk.gs(struct_name)
        size = s['size']
        raw = bytearray(self.z[src_file:src_file + size])
        for f in s['fields']:
            if 'error' in f or not f.get('is_ptr'):
                continue
            for k in range(max(f['arr'], 1)):
                fo = f['offset'] + k * 4
                if fo + 4 > size:
                    break
                v = struct.unpack_from('>I', raw, fo)[0]
                struct.pack_into('>I', raw, fo, self.remap_ptr(v))
        self.register(src_file)
        self.w.write_bytes(bytes(raw))
        self.src = src_file + size

    # ---- string ----------------------------------------------------------
    def emit_cstring(self):
        end = self.z.index(b'\x00', self.src)
        self.register(self.src)
        self.w.write_bytes(self.z[self.src:end + 1])
        self.src = end + 1

    # ---- emit an inline asset (ASSET_STRUCTS reached by a FOLLOW/INSERT pointer,
    # e.g. windowDef.background Material, TracerDef.material). The walker sizes these
    # with its asset_span consumer; here we bound the asset with the solved DELIMITER
    # and copy it verbatim (byte-exact for a round-trip). Returns True if handled. --
    def emit_inline_asset(self, base):
        delim = DELIMITERS.get(base)
        if delim is None:
            return False
        end = delim(self.z, self.src)
        self.emit_verbatim(self.src, end)
        return True

    # ---- emit a double-pointer array (T** with count N): N pointer slots, then per
    # FOLLOW/INSERT slot one element (string / inline asset / struct body+dynamics).
    # Mirrors walker._follow_one's ptr2 branch. --
    def emit_ptr2(self, base, count, d, c):
        slots = self.src
        # emit the N pointer slots (aliases remapped, FOLLOW/null kept)
        raw = bytearray(self.z[slots:slots + count * 4])
        for i in range(count):
            v = struct.unpack_from('>I', raw, i * 4)[0]
            struct.pack_into('>I', raw, i * 4, self.remap_ptr(v))
        self.register(slots)
        self.w.write_bytes(bytes(raw))
        self.src = slots + count * 4
        for i in range(count):
            m2 = struct.unpack_from('>I', self.z, slots + i * 4)[0]
            if m2 not in (W.FOLLOW, W.INSERT):
                continue
            if d.get('string'):
                self.emit_cstring()
            elif base in W.ASSET_STRUCTS:
                if not self.emit_inline_asset(base):
                    self.emit_array(base, 1)
            elif base in self.L.structs:
                self.emit_array(base, 1)
            else:
                sz, _ = self.L._resolve(base)
                self.register(self.src)
                self.w.write_bytes(self.z[self.src:self.src + sz])
                self.src += sz

    # ---- follow one struct's dynamic children (mirrors follow_dynamics) ---
    def follow(self, struct_name, body_src, ctx, alias=None):
        c = dict(ctx)
        self.wk.read_scalars(struct_name, body_src, c)
        for nm, f, d in self.wk.followers(struct_name, alias):
            fo = body_src + f['offset']
            base = f['base']

            # inline flexible array member (arraysize on an embedded struct)
            if d.get('arraysize') and base in self.L.structs:
                count = self.wk.eval_count(d['arraysize'], c)
                decl = f['arr']
                for i in range(max(count, 0)):
                    self.follow(base, fo + i * self.wk.gs(base)['size'], c)
                continue

            # inline array of pointers (e.g. techniques[N])
            if f.get('is_ptr') and f['arr'] > 1:
                if not self.wk.eval_cond(d.get('condition'), c):
                    continue
                for i in range(f['arr']):
                    marker = self.u32(fo + i * 4)
                    if marker not in (W.FOLLOW, W.INSERT):
                        continue
                    if d.get('string'):
                        self.emit_cstring(); continue
                    cnt = self.wk.eval_count(d['count'], c) if d.get('count') else 1
                    if cnt > 0:
                        self.emit_array(base, cnt)
                continue

            # embedded (non-pointer) struct: recurse into its pointers, same body
            # region. A FIXED array (arr>1, e.g. PhysConstraints.data[16]) recurses
            # PER ELEMENT — each element's own pointer followers (target_bone1/2 @+20/
            # +36, material @+140) emit in element order, matching the load_db's
            # LoadArray_PhysConstraint. Previously arr>1 embedded arrays were dropped
            # entirely, so their inline followers never emitted -> walk desync on the
            # patch zones (PhysConstraints is a top-level asset there). Pass alias=nm so
            # a union member (e.g. itemDef_s.typeData -> itemDefData_t) picks up its
            # path-keyed directives (the type-conditioned textDef/imageDef pointer).
            if not f.get('is_ptr') and base in self.L.structs:
                esz = self.wk.gs(base)['size']
                for i in range(max(f['arr'], 1)):
                    self.follow(base, fo + i * esz, c, alias=nm)
                continue
            if not f.get('is_ptr'):
                continue

            marker = self.u32(fo)
            if not self.wk.eval_cond(d.get('condition'), c):
                continue
            if marker in (0,) or (marker not in (W.FOLLOW, W.INSERT)):
                continue             # null or alias -> no inline child
            # double pointer (T** with count N, e.g. MenuList.menus, menuDef_t.items, and
            # ddlEnumDef_t.members = `const char** members` which is ptr2 AND string). MUST be
            # checked BEFORE the plain-string case: a ptr2 string field is an ARRAY of string
            # pointers (emit N slots then N cstrings via emit_ptr2), not a single cstring. The
            # walker orders it the same way (_follow_one ptr2 branch precedes its string case).
            if f.get('ptr2'):
                count = self.wk.eval_count(d['count'], c) if d.get('count') else 1
                if count > 0:
                    self.emit_ptr2(base, count, d, c)
                continue
            if d.get('string'):
                self.emit_cstring(); continue
            # single FOLLOW/INSERT pointer to an asset-typed struct: an inline asset
            # follows (e.g. windowDef.background Material). Bound via delimiter, verbatim.
            if base in W.ASSET_STRUCTS or d.get('assetref'):
                if self.emit_inline_asset(base):
                    continue
                if d.get('assetref'):
                    continue
            count = self.wk.eval_count(d['count'], c) if d.get('count') else 1
            if count <= 0:
                continue
            self.emit_array(base, count)

    # ---- emit an array of `count` `base` elements (bodies then dynamics) --
    def emit_array(self, base, count):
        if base in self.L.structs:
            s = self.wk.gs(base)
            # Console linker packs FOLLOW arrays; it only pads for OVER-aligned types
            # (vertex/shader blobs at 16/128). OAT's defensive Align(4) does NOT match
            # genuine (proven: StringTableCell array lands at an unaligned offset).
            if s['align'] > 4:
                self.w.align(s['align'])
            starts = []
            for i in range(count):
                starts.append(self.src)
                self.emit_body(base, self.src)
            has_dyn = any(self.zc.d.get(base, {}).get(f['name'], {}).get('string')
                          or self.zc.d.get(base, {}).get(f['name'], {}).get('count')
                          or f.get('is_ptr')
                          or (f['base'] in self.L.structs and not f.get('is_ptr'))
                          for f in s['fields'] if 'error' not in f)
            if has_dyn:
                for i in range(count):
                    self.follow(base, starts[i], {})
        else:
            sz, _ = self.L._resolve(base)
            self.register(self.src)
            self.w.write_bytes(self.z[self.src:self.src + count * sz])
            self.src = self.src + count * sz

    # ---- emit one top-level asset ----------------------------------------
    def emit_verbatim(self, src_file, end):
        """Copy [src_file, end) for assets whose console layout has a solved delimiter but
        no structural re-emitter. Byte-exact for a round-trip. For an EDIT (delta!=0), the
        body's alias pointers still need relinking: relink every 4-aligned block-5 alias word
        through remap_ptr, which ONLY rewrites words whose decoded offset is a genuine
        registered target (self.omap) -- data coincidences (shader bytecode, pixels, audio,
        floats) decode to unregistered offsets and are left untouched. Requires a complete
        omap (2-pass: see relink_pass), so forward cross-asset aliases resolve too."""
        if end <= src_file or end > len(self.z):
            raise RuntimeError('delimiter returned out-of-range end 0x%x (start 0x%x, zone 0x%x)'
                               % (end, src_file, len(self.z)))
        self.register(src_file)
        if self.delta:
            raw = bytearray(self.z[src_file:end])
            n = (end - src_file) & ~3
            for fo in range(0, n, 4):
                v = struct.unpack_from('>I', raw, fo)[0]
                rv = self.remap_ptr_omap(v)
                if rv != v:
                    struct.pack_into('>I', raw, fo, rv)
            self.w.write_bytes(bytes(raw))
        else:
            self.w.write_bytes(self.z[src_file:end])
        self.src = end
        return end

    def emit_asset(self, root, src_file):
        blkname = self.zc.default_block.get(root, 'XFILE_BLOCK_TEMP')
        self.w.push_block(BLOCKMAP.get(blkname, zs.BLOCK_VIRTUAL))
        self.src = src_file

        # solved external delimiter (techset/FX/...): bound the asset, copy verbatim.
        delim = DELIMITERS.get(root)
        if delim is not None:
            try:
                end = delim(self.z, src_file)
            except Exception as e:
                raise RuntimeError('%s delimiter failed: %s' % (root, e))
            self.emit_verbatim(src_file, end)
            self.w.pop_block()
            return self.src

        ov = self.wk.CONSOLE_OVERRIDE.get(root)
        if ov:
            # console-divergent struct (e.g. GLASSES = 16-byte stub); emit verbatim
            self.register(src_file)
            self.w.write_bytes(self.z[src_file:src_file + ov['size']])
            self.src = src_file + ov['size']
            if ov.get('no_follow'):
                self.w.pop_block()
                return self.src
        else:
            self.emit_body(root, src_file)
        self.follow(root, src_file, {})
        trail = CONSOLE_TRAIL.get(root)
        if trail:
            self.w.write_bytes(self.z[self.src:self.src + trail])
            self.src += trail
        self.w.pop_block()
        return self.src


def main():
    zpath = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(__file__), '..', 'wiiu_ref', 'mp_raid_genuine.zone')
    max_assets = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    zone = open(zpath, 'rb').read()
    import importlib
    wz = importlib.import_module('wiiu_zone')
    r = wz.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True)
    zc = W.ZoneCode(W.ZC_DIR)

    w = zs.ZoneWriter()
    w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - B5_BASE   # start after the asset array

    em = ReEmitter(zone, L, zc, w)
    cur = r.assets_end
    validated = 0
    stuck = None
    for i, (cid, pc, nm) in enumerate(r.assets[:max_assets]):
        root = W.ASSET_ROOT.get(nm)
        start = cur
        if root is None or root not in L.structs:
            print("[%d] %-16s SKIP (no root)" % (i, nm)); continue
        # PRE-CHECK recovery: if THIS asset's type is detectable and its body isn't at
        # `start` but appears just ahead, the PREVIOUS asset under-read (its delimiter/probe
        # missed a tail, e.g. destructibledef inline piece sub-assets). Absorb the gap bytes
        # verbatim (they belong to the previous asset; the stream stays byte-identical) and
        # start this asset at its real body.
        if pc in DETECTORS and not DETECTORS[pc](zone, start):
            real = find_next_body(zone, start, pc)
            if real is not None and real > start:
                w.write_bytes(zone[start:real])
                print("     [recovery] previous asset under-read; absorbed 0x%x gap bytes -> %s body @0x%x"
                      % (real - start, nm, real))
                start = real; cur = real
        before_w = len(w.buf)
        try:
            cur = em.emit_asset(root, start)
        except Exception as e:
            del w.buf[before_w:]; cur = start
            stuck = (i, nm, "EMIT ERROR: %s" % e); break
        emitted = w.buf[before_w:]
        orig = zone[start:cur]
        drift = ""
        if i + 1 < max_assets and cur < len(zone) - 4:
            nxt = struct.unpack_from('>I', zone, cur)[0]
            name_ok = (nxt == zs.FOLLOW) or (0xA0000000 <= nxt < 0xC0000000) or (nxt == 0)
            if not name_ok:
                drift = "next body implausible (name ptr 0x%08x)" % nxt
        if bytes(emitted) == orig and not drift:
            print("[%d] %-16s 0x%07x..0x%07x  OK (%d bytes)" % (i, nm, start, cur, cur - start))
            validated += 1
        else:
            del w.buf[before_w:]; cur = start
            stuck = (i, nm, drift or "byte divergence"); break

    # ---- verbatim-tail completion + FULL-ZONE byte-identity check ----
    if stuck:
        print("[%d] %-16s STUCK: %s" % (stuck[0], stuck[1], stuck[2]))
    if cur < len(zone):
        w.write_bytes(zone[cur:])          # copy the un-parsed tail verbatim
    content_ok = bytes(w.buf) == zone[r.assets_end:]
    total = r.assets_end + len(w.buf)
    print("\n==== ROUND-TRIP RESULT ====")
    print("assets individually re-laid-out & byte-verified: %d / %d" % (validated, len(r.assets)))
    if stuck:
        print("verbatim tail from asset %d (%s) onward: 0x%x bytes"
              % (stuck[0], stuck[1], len(zone) - cur))
    print("full content bytes match genuine zone: %s (%d / %d bytes)"
          % (content_ok, total, len(zone)))
    if content_ok and total == len(zone):
        print("*** FULL ZONE ROUND-TRIP BYTE-IDENTICAL ***")


if __name__ == '__main__':
    main()
