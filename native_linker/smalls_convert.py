#!/usr/bin/env python3
"""
PC(LE) -> console(BE) converters for the remaining small asset types of the
no-backbone assemble loop (Track G): SndBank (byte-copy), XAnimParts,
DestructibleDef, PhysPreset (standalone), GfxLightDef, Glasses, SkinnedVertsDef.

All layouts are PC-identical (probes: xanimparts_probe, destructibledef_probe;
T6_Assets.h structs) — conversion is per-field byte-swap + pointer reloc, with
strings/byte-arrays copied verbatim. Each convert_* returns (body_bytes, pc_end).
"""
import struct
import os
import bake_errors as _bake_errors
import material_convert as MC

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def _default_reloc(v):
    return v


# FIX B (glass/skybox handoff 2026-07-18, boot-53 class): top-level asset NAME
# words emitted as rtmap-computed dedup aliases can drift -> the asset registers
# under a garbage/truncated name -> the engine's lookup misses -> silent
# DEFAULT-asset substitution (skybox -> mc/global_black; lightdefs -> garbage
# names). Pipeline rule: when the PC name word is a dedup ALIAS, re-emit it
# INLINE (FOLLOW + string). The assembler (produce_nobackbone) installs the
# resolver; standalone/oracle-reproduction use keeps it None (no behavior
# change, byte-exact vs genuine).
INLINE_NAME_RESOLVER = None      # PC b5-alias word -> name bytes | None
# I1 stage 1b: the build's string_identity table, installed by the assembler ONLY
# for maps in I1_STRID_MAPS. When it can name a GfxLightDef's alias, that alias is
# already resolved correctly by Omap.reloc and convert_lightdef must NOT overwrite
# it with the attenuation-image name (that rule is refuted -- see convert_lightdef).
# None => every map behaves exactly as it does today.
LIGHTDEF_STRID = None


def _inline_name(s, off, word_out_pos=0):
    """Emit the root NAME payload for the converter at its name sequence point.
    FOLLOW/INSERT -> copy the PC string (unchanged behavior). Dedup ALIAS with
    a resolver installed -> rewrite the emitted word to FOLLOW and append the
    resolved string (kills the name-drift class). Returns True if a string was
    emitted."""
    v = s.peek32(off)
    if v in PTRS:
        s.cstr()
        return True
    if INLINE_NAME_RESOLVER is None or not (0xA0000001 <= v <= 0xBFFFFFFF):
        return False
    nm = INLINE_NAME_RESOLVER(v)
    if not nm:
        return False
    struct.pack_into('>I', s.b, word_out_pos, FOLLOW)
    s.b += nm + b'\x00'
    return True


class Sw:
    """LE->BE emit helper over a PC buffer with an advancing cursor."""
    def __init__(self, pc, o, reloc):
        self.pc = pc
        self.o = o
        self.b = bytearray()
        self.reloc = reloc

    def u16(self, n=1):
        for _ in range(n):
            self.b += struct.pack('>H', struct.unpack_from('<H', self.pc, self.o)[0])
            self.o += 2

    def u32(self, n=1):
        for _ in range(n):
            self.b += struct.pack('>I', struct.unpack_from('<I', self.pc, self.o)[0])
            self.o += 4

    def ptr(self, n=1):
        for _ in range(n):
            v = struct.unpack_from('<I', self.pc, self.o)[0]
            self.b += struct.pack('>I', self.reloc(v))
            self.o += 4

    def raw(self, n):
        self.b += self.pc[self.o:self.o + n]
        self.o += n

    def cstr(self):
        e = self.pc.index(b'\x00', self.o)
        self.b += self.pc[self.o:e + 1]
        self.o = e + 1

    def peek32(self, off):
        return struct.unpack_from('<I', self.pc, off)[0]


# ---------------------------------------------------------------- SndBank
# Optional overlay of the fixed SndBank head (4756 B) for the MAIN bank: the console
# SndAssetBankHeader carries .sab-file checksums (@body+0x830 = .sab header @0x38, engine
# Sys_Error's on mismatch -- hw-confirmed) plus the loadedAssets entryCount/dataSize that
# must match the DEPLOYED .sab. When the deployed .sab is genuine (raid control), overlay the
# genuine head; for converted .sab, this should instead be built from the console .sab headers.
SNDBANK_HEAD_OVERLAY = None
# .sab checksum / SndAssetBankHeader hash blocks (offset, length) in the fixed head, taken
# from genuine-vs-authored raid diff. @0x830 is the primary (.sab header @0x38).
SNDBANK_CKSUM_BLOCKS = [(0x830, 16), (0x940, 12), (0x1150, 20), (0x1264, 8)]

# Optional RAID oracle: supply the genuine console hash fields we CANNOT recompute (the
# console sound string-hash is custom/non-standard, and SndAlias.name is a per-build
# string-pool ptr-id). name@+0 & assetId@+16 are per-alias (positional: our emit order ==
# genuine alias order, proven by the BE re-walk); aliasIndex is the whole genuine array.
# Needed only where the DEPLOYED .sab uses genuine console hashes (raid oracle). For maps
# whose .sab we convert (skate etc.) the .sab id == our PC-derived assetId already, so leave
# these None. When set they overwrite our PC-derived values in-place (size-preserving).
SNDBANK_ALIAS_ORACLE = None       # list[(name_be:int, assetId_be:int)] in emit order
SNDBANK_ALIASINDEX_ORACLE = None  # bytes: genuine aliasIndex array (BE), len == aliasCount*4

# Whole-body overlay for the MAIN bank (raid control): dict {name: bytes}. The console bank
# inlines the list-name and assetFileName STRINGS (the paths the engine opens) plus its own
# custom-hash name/id fields; the PC bank stores those as hashes and omits the strings, so a
# field-aware convert leaves the alias region ~102KB short with the wrong FOLLOW-vs-hash layout
# -> a runtime sound-list pointer lands in the audio buffer -> +0x3817ce. Those strings/hashes
# are not derivable from PC (custom hash, strings absent). For a map WITH a genuine reference,
# emit the genuine main-bank body verbatim (self-contained FOLLOW+inline, loader relinks it).
# Keyed by the bank's walked name so it only replaces the intended bank.
SNDBANK_MAIN_OVERLAY = None       # {bank_name: genuine_body_bytes} or None
# Diagnostic/stopgap: emit the main bank with alias/radverb/duck tables EMPTIED (the
# same structurally-valid shape author_english_bank's cross-map fallback uses). For maps
# with NO genuine console reference (skate) the field-converted aliases carry PC
# string-hashes where the console builds its own registration -> the AX voice callback
# walks a dangling voice and faults at +0x3817ce. Emptying the tables removes the bad
# registration (silent/limited SFX) so the map BOOTS; correct alias emission is the
# follow-up. Set of bank names to empty (matched on the walked bank name).
SNDBANK_EMPTY_ALIASES = None      # set([bank_name, ...]) or None
SNDBANK_LOADEDASSETS_ORACLE = None  # (entryCount, dataSize) genuine console values; overrides
                                    # console_zone_fields (which is off for raid: our dataSize was
                                    # 749KB too big -> shifted downstream GEN_POLICY bodies -> wild ptr)
# 2026-07-14 (skate sndcap boots 1-2): the loadedAssets HEAD carried swap32'd PC heap garbage in
# the runtime-pointer/hash words, and PC's FOLLOW zone*/language* (@0x20/0x24) made us emit two
# inline strings the CONSOLE loader does not consume (genuine banks never inline them; genuine
# main banks bake pre-resolved b5 string pointers at 0x1264/0x1268, genuine english banks ship
# -1/-1 and load fine). Result: the engine read wild zone/language pointers and the loaded-asset
# fill ran unbounded past any capacity (crash just past cap in both boots). Sanitize to the
# genuine-english-raid shape (byte-validated vs wiiu_ref/mp_raid_genuine.zone @0x45bea9e):
# zero 0x20..0x28 / 0x830..0x840 / 0x940..0x94c / 0x1150..0x1164, set 0x1264..0x126c = FF,
# suppress the two inline strings (folded into the zeroed data buffer -> size-preserving).
SNDBANK_HEAD_SANITIZE = None      # set([bank_name, ...]) or None

# --- P1/P2: the PC SndAlias stride, and the console platform bank paths -------
# * sizeof(SndAlias) is 96 on PC and 100 on Wii U (OAT ZoneCode
# sndbank_t6_load_db.cpp:202 `LoadWithFill(96 * count)`; genuine console mp_raid
# walks byte-exact at 100 and collapses at 96). `sndbank_probe.ALIAS` was serving
# as BOTH, so every PC walk strode 4 B too far per alias and this emitter read
# SndAlias records from a cursor drifting 4 B per record -- garbage for every
# alias after the first, and only ~12% of the bank's strings emitted at all.
# mp_mirage boot 1 fast-failed opening a bank name that was actually an alias
# name. FLAG-GATED so the fleet default is unchanged until it is ruled in.
SNDBANK_PC_STRIDE_FIX = False

# Platform bank paths. The console zone must not carry PC paths; genuine console
# zones hold `.wiiu.snd`. REQUIRES the stride fix -- without it the walk emits
# the WRONG ~12% of strings, so renaming them would turn platform_string_gate
# GREEN on a zone that boots exactly as badly (the missing-input shape).
SNDBANK_PLATFORM_RENAME = None    # e.g. [(b'.pc.snd', b'.wiiu.snd')] or None

# Keep the emitted body BYTE-LENGTH IDENTICAL to the legacy output by taking the
# growth out of the zeroed loadedAssets capacity, so nothing downstream of the
# largest body in the zone moves. Costs one extra emitter pass (the same function
# called with legacy parameters -- ONE implementation, never a second walk).
SNDBANK_SIZE_NEUTRAL = True
# The capacity is a runtime buffer sized at PC_dataSize * 0.21. The two GENUINE
# console/PC ratios are mp_raid 0.1972 and common_mp 0.1948. Absorbing growth may
# not push below the larger -- that would trade a loud walk bug for a silent
# undersized-buffer failure. REFUSES; it does not clamp.
SNDBANK_CAPACITY_FLOOR_RATIO = 0.1972

# --- P5: the .sab checksums the ENGINE VALIDATES ------------------------------
# The console SndAssetBankHeader embeds each deployed bank FILE's own
# header[0x38:0x48] and the engine Sys_Error's on mismatch (hw-confirmed
# 2026-07-09). CALIBRATED on genuine console mp_raid -- the genuine zone head vs
# the console's own deployed banks on E:\ -- both spans matched EXACTLY:
#
#     head[0x0830:0x0840]  ==  <bank>.all.sabs  header[0x38:0x48]   VERBATIM
#     head[0x1152:0x1162]  ==  <bank>.all.sabl  header[0x38:0x48]   VERBATIM
#
# Note the .sabl span starts at 0x1152, i.e. +2 INSIDE the (0x1150, 20) entry of
# SNDBANK_CKSUM_BLOCKS. That block is 2 + 16 + 2.
#
# * VERBATIM, AND THAT IS THE SECOND DEFECT AT THIS OFFSET. The value is a 16-byte
# MD5 -- a BYTE STRING, not four u32s -- and the blanket _swapw over the 4756-byte
# head word-swaps it. So the PC checksum we were carrying was not merely the wrong
# bank's, it was also byte-order-mangled. Shipping even the PC bank would have
# failed the engine's check.
#
# * SNDBANK_CKSUM_BLOCKS IS MISNAMED: only (0x830,16) and (0x1150,20) are
# checksums. (0x940,12) and (0x1264,8) are the zone*/language* POINTER PAIRS (one
# unaligned, at 0x942/0x946) -- see sndbank_head_reloc. Overlaying a genuine head
# fixes both at once, which is why the conflation never showed. A map with
# CONVERTED banks needs them handled separately: checksums from our banks (here),
# pointers minted through reloc (P3).
SNDBANK_BANK_FILES = None     # {bank_name: {'sabs': path, 'sabl': path}} or None
# (head offset, which bank file) -- 16 bytes each, copied VERBATIM
SNDBANK_CKSUM_SPANS = ((0x830, 'sabs'), (0x1152, 'sabl'))
SNDBANK_SAB_HDR_CKSUM = (0x38, 16)      # where the checksum lives in the .sab FILE

# --- P3 (option A): the bank-name POINTER PAIRS ------------------------------
# The head carries the bank's zone*/language* at THREE sites. On a converted map
# they are PC-frame values carried verbatim -- and the pair at 0x942/0x946 is not
# 4-aligned, so the blanket _swapw over the head SHREDS it (mirage measured:
# 0000F0DC / A5C60000). Neither points at anything in our frame.
#
# ⛔ WHY WE DO NOT MINT THEM. The obvious repair is to re-mint through the rt model,
# which is what sndbank_head_reloc does for raid. CALIBRATED AND REFUTED 2026-08-18:
# resolving GENUINE console raid's OWN head pointers through loader_sim.InverseMap
# lands 1,668 B and 2,397 B short of the strings they name -- two different
# residuals, so not a constant. raid's mint works because map_config carries a
# DUMP-MEASURED `sndbank_rt_sim_residual`; the model itself is knowingly wrong in
# this region (map_config's own raid note: "+0x4000 LOW at the SndBank tail").
# Mirage has no dump, so no residual exists, and minting through a model just
# measured missing by 2 KB is the approx-path class of error.
#
# ⭐ WHAT WE DO INSTEAD, AND ITS EVIDENCE. Zero the two pairs. That is the shape of
# a GENUINE console english bank (mp_raid_genuine: +0x20/+0x24 and +0x942/+0x946 all
# 00000000) and the shape SNDBANK_HEAD_SANITIZE produces for mp_skate, WHICH BOOTS
# ON REAL HARDWARE. The stream bank pair at +0x20/+0x24 is left FOLLOW so the
# engine still composes `<zone>.<language>.sabs` from our inline strings -- that is
# the field boot 1 died on and it is the one thing here that must keep working.
#
# ⚠ STATED SO IT IS NOT OVER-READ: skate ships this head shape TOGETHER WITH
# SNDBANK_EMPTY_ALIASES, so skate's bank is a stub. A bank with REAL alias arrays
# and NULL pairs is a FIRST, not a proven combination. Boot 2's pre-registration
# carries it as a predicted-outcome row, not as a claim.
SNDBANK_NULL_BANK_PAIRS = None    # set([bank_name, ...]) or None
# (start, end) of each pair. 0x940..0x94c covers the UNALIGNED pair at 0x942/0x946
# plus the word either side -- the same span SNDBANK_HEAD_SANITIZE zeroes.
SNDBANK_PAIR_SPANS = ((0x940, 0x94c), (0x1264, 0x126c))
# The checksum spans the pair-zeroing must never touch. Kept as its own name so the
# overlap check reads as an invariant rather than as two magic numbers.
SNDBANK_CKSUM_SPANS_GUARD = ((0x830, 16), (0x1152, 16))


def bank_checksum(path):
    """The 16 bytes the zone must carry for this bank file. RAISES if unreadable --
    a missing bank is a refusal, never a zero-filled placeholder."""
    co, cl = SNDBANK_SAB_HDR_CKSUM
    if not path or not os.path.exists(path):
        raise SndBankConversionRefusal(
            'bank file %r does not exist, so its checksum cannot be read. The zone '
            'embeds this value and the engine Sys_Error\'s on mismatch; emitting a '
            'placeholder would ship a zone that refuses to load its own sound.'
            % (path,))
    with open(path, 'rb') as f:
        hdr = f.read(co + cl)
    if len(hdr) < co + cl:
        raise SndBankConversionRefusal(
            'bank file %r is only %d B, too short to contain a header checksum at '
            '0x%X..0x%X.' % (path, len(hdr), co, co + cl))
    return hdr[co:co + cl]


def verify_bank_checksums(body, files):
    """-> list of (span_offset, kind, found, expected) for every span that does NOT
    match. Empty list == the zone names the banks it will be shipped with.

    Separate from the emitter ON PURPOSE: this reads the FINISHED artefact, so it
    can be run by a gate against the zone we are actually about to deploy rather
    than against the emitter's intention."""
    bad = []
    for span_off, kind in SNDBANK_CKSUM_SPANS:
        want = bank_checksum(files.get(kind))
        got = bytes(body[span_off:span_off + len(want)])
        if got != want:
            bad.append((span_off, kind, got, want))
    return bad


class SndBankConversionRefusal(_bake_errors.FatalBakeError):
    """RAISES, and now actually REFUSES.

    ⛔ THIS WAS A BARE `RuntimeError` AND THAT MADE IT DECORATIVE.
    `produce_nobackbone.emit_one` ends in `except FatalBakeError: raise` followed
    by `except Exception as ex: body = None; why = 'EXC:...'`. A RuntimeError takes
    the second branch: all three of the refusals below were downgraded to a dropped
    SndBank body and a one-line note in a per-asset table, and the bake continued.

    I wrote them, tested that they raise, and never checked what happened to them on
    the way out -- I VERIFIED THE THING I BUILT AND NOT THE PATH IT TRAVELS. The
    nuked lane shipped the identical defect in the same file on the same morning.

    Inheriting the contract is the fix; a third `except ...: raise` beside the
    existing one would be a list of special cases someone forgets to extend."""


def _swapw(b):
    """Byte-swap every 4-byte word (console v148 is big-endian). len(b) MUST be a
    multiple of 4 (all SndBank struct arrays are 4-aligned in size)."""
    return b''.join(b[i:i + 4][::-1] for i in range(0, len(b), 4))


def _swap16(b):
    """Byte-swap every 2-byte half in place (u16/i16 fields)."""
    return b''.join(b[i:i + 2][::-1] for i in range(0, len(b), 2))


# --- field-aware SndAlias/SndRadverb/SndDuck endian (2026-07-12) ---------------
# The old blanket _swapw over these arrays was WRONG: it byte-reversed 4 bytes at a
# time across sub-u32 fields, corrupting (a) the SndAlias uint16/int16/uint8 tail
# (bytes 52..95) and its pad, and (b) the char name[32] of SndRadverb/SndDuck
# ("amb_"->"_bma"). Layouts from T6_Assets.h (SndAlias@6328, SndRadverb@3115,
# SndDuck@3139), byte-validated vs genuine raid mp_raid bank[1] (aligned aliases 0..3
# reproduce byte-exact except the two console-recomputed hash fields name@+0/assetId@+16
# and the per-alias flags1 bit26 'unknown1_1', neither of which is endian or a crash
# source). SndAliasList(20) and SndIndexEntry stay pure swap32.
def _alias_be(p100):
    """SndAlias(100) PC(LE)->console(BE), field-aware.
      +0..+51  : 6 ptr/u32 (name*,id,subtitle*,secondaryName*,assetId,assetFileName*)
                 + SndAliasFlags(8) + duck/contextType/contextValue/stopOnPlay/futzPatch -> swap32
      +52..+85 : 17 x u16/i16 (fluxTime..dopplerScale)                                  -> swap16
      +86..+95 : 10 x u8  (minPriorityThreshold..duckGroup)                             -> verbatim
      +96..+99 : pad                                                                    -> zero
    name@+0 & assetId@+16 carry the PC string-hash (console uses its own hash; wrong
    hash = sound not found by name = silent, NOT a wild-ptr crash) -- deferred."""
    return (_swapw(p100[0:52]) + _swap16(p100[52:86]) + p100[86:96]
            + b'\x00\x00\x00\x00')


def _radverb_be(p100):
    """SndRadverb(100): char name[32] verbatim + id + 16 floats -> swap32."""
    return p100[0:32] + _swapw(p100[32:100])


def _duck_be(p76):
    """SndDuck(76): char name[32] verbatim + (id,5 floats,2 u32,2 ptr,int) -> swap32."""
    return p76[0:32] + _swapw(p76[32:76])


def convert_sndbank(pc, off, reloc=_default_reloc, _legacy_params=False):
    """PC(LE) SndBank -> console(BE): the layout is PC-IDENTICAL but the console
    is BIG-ENDIAN, so every struct WORD is byte-swapped while string bytes and the
    (zeroed) sample-data blob are kept verbatim (sndbank_probe: "console serializes
    PC-identically, byte-swap only"). The old verbatim copy left aliasCount/etc.
    little-endian -> the console read aliasCount as ~1.6e9 and walked the alias list
    off into unmapped memory (raid boot crash, 2026-07-12; the audio AXVPB frame
    callback). loadedAssets entries+data are a ZEROED RUNTIME BUFFER on console
    (FINDINGS_sndbank_loadedassets.md; audio lives in the .sabl/.sabs files). The walk
    mirrors sndbank_probe.parse_sndbank so word-regions and string-regions are emitted
    in stream order (arrays follow variable-length strings, so a blanket swap is wrong).
    NOTE: the SndAssetBankHeader hash blocks are byte-swapped structurally but their
    CONTENT is .sab-specific (genuine's hashes differ) -- bank-load hash validation is a
    separate transplant/recompute step, not the wild-pointer crash."""
    import sndbank_pc
    import sndbank_probe as _S
    import sndbank_audio_convert as SAC
    body = _S.BODY
    # * THE PC STRIDE IS A READ PARAMETER; THE CONSOLE RECORD SIZE IS A WRITE
    # PARAMETER. They are different numbers (96 vs 100) and one symbol was serving
    # both. `_legacy_params` reproduces the pre-fix behaviour EXACTLY and exists so
    # the size-neutral pass can measure the legacy length with THIS SAME EMITTER
    # rather than a second implementation of the walk.
    stride = (_S.PC_ALIAS if (SNDBANK_PC_STRIDE_FIX and not _legacy_params)
              else _S.ALIAS)
    renames = (SNDBANK_PLATFORM_RENAME
               if (SNDBANK_PLATFORM_RENAME and not _legacy_params) else ())
    if renames and not SNDBANK_PC_STRIDE_FIX:
        raise SndBankConversionRefusal(
            "SNDBANK_PLATFORM_RENAME is set but SNDBANK_PC_STRIDE_FIX is not. The "
            "un-fixed walk emits the wrong ~12% of the bank's strings, so renaming "
            "them would satisfy platform_string_gate on a zone that still names a "
            "file that cannot exist. Refusing rather than going green.")
    end, name, ac, stats = _S.parse_sndbank(pc, off, '<', alias_stride=stride)
    nxt = sndbank_pc.parse_sndbank_pc(pc, off)
    # `nxt` is deliberately taken at the DEFAULT stride: parse_sndbank_pc skips the
    # trailing zero run to the next asset, and that skip absorbs the deficit, so the
    # next-asset boundary is INVARIANT under the stride (verified on all five
    # subject zones). Leaving it alone keeps the asset chain untouched.
    # raid control: emit the genuine main-bank body verbatim (see SNDBANK_MAIN_OVERLAY).
    if SNDBANK_MAIN_OVERLAY is not None and name in SNDBANK_MAIN_OVERLAY:
        return SNDBANK_MAIN_OVERLAY[name], nxt
    u32 = lambda o: struct.unpack_from('<I', pc, o)[0]
    (name_p, aliasCount, alias_p, aliasIndex_p, radverbCount, radverbs_p,
     duckCount, ducks_p) = struct.unpack_from('<8I', pc, off)

    # emptied-alias stopgap (no genuine reference, e.g. skate): zero the alias/radverb/
    # duck/scriptIdLookup counts+ptrs in the head and SUPPRESS the array bytes; the walk
    # below still advances `o` through the PC arrays so the zone/language strings and the
    # loadedAssets zeroed buffers emit at the right stream position. Removes the dangling
    # voice registration that faults the AX callback at +0x3817ce.
    empty = SNDBANK_EMPTY_ALIASES is not None and name in SNDBANK_EMPTY_ALIASES
    sanitize = SNDBANK_HEAD_SANITIZE is not None and name in SNDBANK_HEAD_SANITIZE

    out = bytearray()
    out += _swapw(pc[off:off + body])           # fixed head (counts, bank headers)
    # body+0x1290..0x1294 are per-byte runtime-default flags, NOT a swappable u32
    # (0x1291 = load-error flag read by SND_BankLoadUpdateState @top; 0x1292 = the
    # "has loaded/.sabl" flag). Console keeps them in PC byte order — genuine mp_raid
    # console == PC verbatim here (00 00 01 00). The blanket _swapw reverses the word
    # to 00 01 00 00, putting 1 on the error-flag byte -> SND_BankLoadUpdateState bails
    # to BankLoadError before any file open -> "sound bank failed to load ... build
    # problem" (skate boot, dump 37788). Raid dodged this via SNDBANK_MAIN_OVERLAY.
    out[0x1290:0x1294] = pc[off + 0x1290:off + 0x1294]
    if empty:
        for _o8 in (4, 8, 12, 16, 20, 24, 28):  # alias/radverb/duck counts+ptrs
            struct.pack_into('>I', out, _o8, 0)
    o = off + body
    freed = [0]                                 # bytes suppressed under `empty`
    grown = [0]                                 # bytes ADDED by the platform rename

    def emit_string(suppress=False):            # NUL-terminated string
        nonlocal o
        nul = pc.index(b'\x00', o)
        if not suppress:
            sb = pc[o:nul + 1]
            for pc_tok, co_tok in renames:      # platform bank path, in-emitter
                if pc_tok in sb:
                    nb = sb.replace(pc_tok, co_tok)
                    grown[0] += len(nb) - len(sb)
                    sb = nb
            out.extend(sb)
        else:
            freed[0] += nul + 1 - o
        o = nul + 1

    alias_i = 0                                 # global alias index (for the oracle)
    if name_p in PTRS:
        emit_string()
    if alias_p in PTRS:
        arr_s = o; o += aliasCount * _S.ALIASLIST
        if not empty:
            out += _swapw(pc[arr_s:o])          # SndAliasList[] array
        else:
            freed[0] += aliasCount * _S.ALIASLIST
        for i in range(aliasCount):
            lname_p, lid, head_p, cnt, seq = struct.unpack_from(
                '<5I', pc, arr_s + i * _S.ALIASLIST)
            if lname_p in PTRS:
                emit_string(empty)              # list name
            if head_p in PTRS:
                ab = o; o += cnt * stride
                for k in range(cnt):                # SndAlias[] array (field-aware)
                    a = ab + k * stride
                    # _alias_be consumes 96 B and appends the console's 4 pad bytes
                    # (its own docstring says '+96..+99 : pad -> zero') -- it was
                    # ALWAYS written for a 96-byte PC record. Slicing 96 here is
                    # byte-identical under either stride; only the CURSOR was wrong.
                    ab_out = bytearray(_alias_be(pc[a:a + 96]))
                    if SNDBANK_ALIAS_ORACLE is not None:   # genuine name/assetId hashes
                        nm_be, aid_be = SNDBANK_ALIAS_ORACLE[alias_i]
                        # keep FOLLOW name fields (their inline string still follows -> the
                        # walk must still consume it); only replace the hash-name case.
                        if struct.unpack_from('>I', ab_out, 0)[0] not in PTRS:
                            struct.pack_into('>I', ab_out, 0, nm_be)
                        struct.pack_into('>I', ab_out, 16, aid_be)  # assetId never FOLLOW
                    if not empty:
                        out += ab_out
                    else:
                        freed[0] += len(ab_out)
                    alias_i += 1
                for k in range(cnt):
                    a = ab + k * stride
                    for po in (a + 0, a + 8, a + 12, a + 20):   # name/sub/sec/file
                        if u32(po) in PTRS:
                            emit_string(empty)
    if aliasIndex_p in PTRS:                       # SndIndexEntry{u16 value,u16 next}
        s = o; o += aliasCount * 4
        if empty:
            freed[0] += aliasCount * 4
        elif SNDBANK_ALIASINDEX_ORACLE is not None:  # genuine console-rebuilt hash table
            out += SNDBANK_ALIASINDEX_ORACLE
        else:
            out += _swap16(pc[s:o])
        # NOTE: values are a name-hash open-addressing table the console REBUILDS from
        # its own string hashes (genuine != any transform of PC); swap16 fixes the field
        # endian only. It's a play-time name->alias lookup, not the boot bank walk, so a
        # PC-derived table is a silent miss at worst, not the +0x3817ce wild-ptr crash.
    if radverbs_p in PTRS:
        rs = o; o += radverbCount * _S.RADVERB    # SndRadverb[] (name[32] verbatim)
        if empty:
            freed[0] += radverbCount * _S.RADVERB
        for i in range(radverbCount):
            r = rs + i * _S.RADVERB
            if not empty:
                out += _radverb_be(pc[r:r + _S.RADVERB])
    if ducks_p in PTRS:
        ds_s = o; o += duckCount * _S.DUCK        # SndDuck[] (name[32] verbatim)
        if empty:
            freed[0] += duckCount * _S.DUCK
        for i in range(duckCount):
            d = ds_s + i * _S.DUCK
            if not empty:
                out += _duck_be(pc[d:d + _S.DUCK])
        for i in range(duckCount):
            db = ds_s + i * _S.DUCK
            for po in (db + 64, db + 68):        # attenuation/filter -> 32 f32
                if u32(po) in PTRS:
                    s = o; o += 32 * 4
                    if not empty:
                        out += _swapw(pc[s:o])
                    else:
                        freed[0] += 32 * 4
    # zone/language strings for each FOLLOW pointer in body[32..0x126c)
    # (under `sanitize` the console loader must not see them -> suppress, size folded below)
    for po in range(32, 0x126c, 4):
        if u32(off + po) == FOLLOW:
            emit_string(sanitize)

    ec = u32(off + 0x1270)
    ds = u32(off + 0x1278)
    cec, cds = SAC.console_zone_fields(ec, ds)
    if SNDBANK_LOADEDASSETS_ORACLE is not None:     # genuine console entryCount/dataSize
        cec, cds = SNDBANK_LOADEDASSETS_ORACLE
    if u32(off + 0x1274) == FOLLOW:              # entries: zeroed capacity
        o += ec * 20; out += b'\x00' * (cec * 20)
    silc = u32(off + 0x1280)
    if empty and u32(off + 0x1284) == FOLLOW:    # scriptIdLookups suppressed size
        freed[0] += silc * 8
    # SIZE-PRESERVING: fold the freed alias/index/radverb/duck/scriptId bytes into the
    # loadedAssets zeroed data buffer so the emitted SndBank body is byte-length-identical
    # to the non-emptied one -> the zone layout and the measured runtime map stay valid
    # (no re-measure needed; the extra zeros are harmless runtime capacity).
    cds_emit = cds + (freed[0] if (empty or sanitize) else 0) - grown[0]
    data_ins = None
    if u32(off + 0x127c) == FOLLOW:              # data: zeroed runtime buffer
        o += ds; data_ins = len(out)             # blob inserted after sizing below
    if u32(off + 0x1284) == FOLLOW:
        s = o; o += silc * 8
        if not empty:
            out += _swapw(pc[s:o])                # scriptIdLookups
    assert o == end, (hex(o), hex(end))

    # ---- SIZE-NEUTRAL: emitted body length identical to the legacy one ---------
    # The corrected walk emits the strings that were being skipped (mirage 13,522 ->
    # 108,385 B). The loadedAssets data blob is a ZEROED RUNTIME CAPACITY, i.e. a
    # free parameter, so the growth comes out of it and NOTHING DOWNSTREAM OF THE
    # LARGEST BODY IN THE ZONE MOVES. This is the `freed[0]` mechanism already in
    # this function, run in the other direction.
    if (SNDBANK_SIZE_NEUTRAL and not _legacy_params
            and (SNDBANK_PC_STRIDE_FIX or renames)):
        legacy_len = len(convert_sndbank(pc, off, reloc, _legacy_params=True)[0])
        if data_ins is None:
            if legacy_len != len(out):
                raise SndBankConversionRefusal(
                    "bank %r has no loadedAssets data blob, so there is no capacity "
                    "to absorb the %+d B change; size-neutrality is unreachable here."
                    % (name, len(out) - legacy_len))
        else:
            cds_emit = legacy_len - len(out)
            floor = int(ds * SNDBANK_CAPACITY_FLOOR_RATIO)
            if cds_emit < floor:
                raise SndBankConversionRefusal(
                    "bank %r: absorbing the corrected walk would leave a loadedAssets "
                    "capacity of %d B (ratio %.4f of the PC %d B), BELOW the genuine "
                    "console floor %d B (ratio %.4f). Refusing: an undersized runtime "
                    "buffer is a SILENT audio failure, and trading a loud bug for a "
                    "silent one is not a fix."
                    % (name, cds_emit, cds_emit / float(ds or 1), ds, floor,
                       SNDBANK_CAPACITY_FLOOR_RATIO))
    if data_ins is not None:
        out[data_ins:data_ins] = b'\x00' * cds_emit
    # loadedAssets counts: BIG-ENDIAN, console-sized
    struct.pack_into('>I', out, 0x1270, cec)
    struct.pack_into('>I', out, 0x1278, cds_emit)
    if empty:                                     # scriptIdLookups reference alias idxs
        struct.pack_into('>I', out, 0x1280, 0)    # -> emptied bank has none
        struct.pack_into('>I', out, 0x1284, 0)
    if sanitize:
        # 2026-07-14 rev2 (boot 14 hang): zero ONLY the PC-heap-garbage runtime words (md5
        # slots + baked-ptr slots; the build script re-bakes real values after authoring).
        # Genuine MAIN banks keep FOLLOW+inline strings at +0x20/+0x24 — leave them intact
        # (the earlier english-bank-shaped zeroing broke sound-bank mount registration).
        for zs, ze in ((0x830, 0x840), (0x940, 0x94c), (0x1150, 0x1164), (0x1264, 0x126c)):
            out[zs:ze] = b'\x00' * (ze - zs)
    # main bank: overlay the deployed .sab's checksum/hash blocks (the SndAssetBankHeader hashes;
    # @0x830 = .sab header @0x38 -- engine Sys_Error's on mismatch). ONLY these blocks, NOT the
    # loadedAssets counts (those stay ours so the walk sizing remains self-consistent).
    if (SNDBANK_HEAD_OVERLAY is not None
            and u32(off + 0x1274) == FOLLOW and u32(off + 0x127c) == FOLLOW):
        for co, cl in SNDBANK_CKSUM_BLOCKS:
            out[co:co + cl] = SNDBANK_HEAD_OVERLAY[co:co + cl]

    # ---- P5: point the zone at the banks WE will deploy ------------------------
    # Only for banks we converted ourselves. A map with a genuine console oracle
    # takes the branch above instead; doing both would be two sources of truth for
    # the same 16 bytes, so that combination REFUSES rather than picking one.
    if SNDBANK_BANK_FILES and name in SNDBANK_BANK_FILES:
        if SNDBANK_HEAD_OVERLAY is not None:
            raise SndBankConversionRefusal(
                "bank %r has BOTH a genuine-console head overlay and converted bank "
                "files. Those are two different answers for the same checksum bytes "
                "and I will not silently prefer one. Supply exactly one." % (name,))
        for span_off, kind in SNDBANK_CKSUM_SPANS:
            blk = bank_checksum(SNDBANK_BANK_FILES[name].get(kind))
            out[span_off:span_off + len(blk)] = blk
        # Assert what we just did, against the same reader a gate will use later.
        bad = verify_bank_checksums(out, SNDBANK_BANK_FILES[name])
        if bad:
            raise SndBankConversionRefusal(
                "bank %r: checksum write did not take at %s" % (name, bad))

    # ---- P3 (A): zero the bank-name pointer pairs -----------------------------
    # AFTER the checksum write on purpose: the spans do not overlap (0x830 and
    # 0x1152 are checksums; 0x940..0x94c and 0x1264..0x126c are pointers), but
    # ordering them explicitly means a future overlap shows up as a test failure
    # rather than as whichever write happened to run last.
    if SNDBANK_NULL_BANK_PAIRS and name in SNDBANK_NULL_BANK_PAIRS:
        for _s, _e in SNDBANK_PAIR_SPANS:
            for _co, _cl in SNDBANK_CKSUM_SPANS_GUARD:
                if not (_e <= _co or _s >= _co + _cl):
                    raise SndBankConversionRefusal(
                        "pair span 0x%X..0x%X overlaps checksum span 0x%X+%d; one "
                        "would silently overwrite the other" % (_s, _e, _co, _cl))
            out[_s:_e] = b'\x00' * (_e - _s)
    return bytes(out), nxt


def author_english_bank(map_name,
                        template_zone=os.path.join('..', 'wiiu_ref',
                                                   'mp_raid_genuine.zone'),
                        template_off=0x45bea9e):
    """Author the console-only localized SndBank insert `mpl_<map>.english`
    (the extra SOUND row of the MP insert set, HANDOFF item).

    When the template zone's english bank IS this map's (raid control: the
    genuine mp_raid english bank), return its FULL genuine span VERBATIM
    (body + the 2 real VO aliases + name/zone strings). The aliases are the
    localized VO the engine registers and the AX voice callback walks — an
    EMPTIED bank leaves those dangling and faults at +0x3817ce (the audio
    callback; genuine-english bisect proved the full bank clears it, 2026-07-12,
    HANDOFF_xmodel_inline_image.md). The span is self-contained (all pointers
    FOLLOW with their inline alias/string data), so the loader relinks it in place.

    Cross-map (e.g. skate with the raid template): fall back to the genuine
    header/body with the alias/radverb/duck tables EMPTIED and strings
    re-authored — structurally valid and without cross-map VO alias refs (raid's
    VO streams a different map doesn't ship). That fallback is a stopgap; a real
    per-map english bank still needs its own localized VO."""
    import sndbank_probe as _S
    tz = template_zone if os.path.isabs(template_zone) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), template_zone)
    d = open(tz, 'rb').read()
    end, tname, _ac, _st = _S.parse_sndbank(d, template_off, '>')
    base = 'mpl_%s' % map_name.replace('mp_', '', 1)
    # the template's own english bank matches this map -> ship it whole (aliases intact)
    if tname == '%s.english' % base:
        return bytes(d[template_off:end])
    # cross-map fallback: genuine header/body, alias tables emptied, strings re-authored
    body = bytearray(d[template_off:template_off + 4756])
    struct.pack_into('>I', body, 0, FOLLOW)      # name*
    for o in (4, 8, 12, 16, 20, 24, 28):         # aliasCount/alias/aliasIndex/
        struct.pack_into('>I', body, o, 0)       # radverbCount/radverbs/ducks*
    return bytes(body) + ('%s.english\x00%s\x00english\x00'
                          % (base, base)).encode('latin-1')


# ---------------------------------------------------------------- XAnimParts
def _conv_delta(s, numframes, idxw):
    pc = s.pc
    db = s.o
    ptrs = struct.unpack_from('<3I', pc, db)
    s.ptr(3)                                   # trans/quat2/quat ptrs
    if ptrs[0] in PTRS:                        # XAnimPartTrans
        size = struct.unpack_from('<H', pc, s.o)[0]
        small = pc[s.o + 2]
        if size == 0:
            s.u16(); s.raw(2); s.u32(3)        # u16+u8 small+pad, vec3 frame0
        else:
            frames_p = s.peek32(s.o + 28)
            s.u16(); s.raw(2); s.u32(6)        # hdr + mins/size vec3s
            s.ptr()                            # frames*
            if idxw == 1:
                s.raw(size + 1)
            else:
                s.u16(size + 1)
            if frames_p in PTRS:
                if small:
                    s.raw((size + 1) * 3)      # ByteVec
                else:
                    s.u16((size + 1) * 3)      # UShortVec
    for qsz in (4, 8):                         # quat2 (XQuat2=2xi16), quat (XQuat=4xi16)
        p = ptrs[1] if qsz == 4 else ptrs[2]
        if p not in PTRS:
            continue
        size = struct.unpack_from('<H', pc, s.o)[0]
        if size == 0:
            s.u16(); s.raw(2); s.u16(qsz // 2)  # u16+pad, inline frame0 quat
        else:
            frames_p = s.peek32(s.o + 4)
            s.u16(); s.raw(2)
            s.ptr()                            # frames*
            if idxw == 1:
                s.raw(size + 1)
            else:
                s.u16(size + 1)
            if frames_p in PTRS:
                s.u16((size + 1) * (qsz // 2))


def convert_xanim(pc, off, reloc=_default_reloc):
    """XAnimParts: PC-identical 104-B body + streamed data, per-field swap."""
    s = Sw(pc, off, reloc)
    (dbc, dsc, dic, rdbc, rdic, numframes) = struct.unpack_from('<6H', pc, off + 4)
    boneCount = pc[off + 24:off + 34]
    notifyCount = pc[off + 34]
    rdsc = s.peek32(off + 40)
    indexCount = s.peek32(off + 44)
    idxw = 1 if numframes < 256 else 2
    p = lambda o: s.peek32(off + o)
    # ---- 104-B body ----
    s.ptr()                                    # name
    s.u16(6)                                   # counts @4..15
    s.raw(4)                                   # bLoop..bLeftHandGripIK u8s @16
    s.u32()                                    # streamedFileSize @20
    s.raw(12)                                  # boneCount[10] + notifyCount + assetType @24..35
    s.raw(4)                                   # isDefault + pad @36
    s.u32(2)                                   # randomDataShortCount, indexCount @40,44
    s.u32(4)                                   # framerate/frequency/primedLength/loopEntryTime @48
    s.ptr(10)                                  # names..deltaPart @64..100
    assert s.o == off + 104
    # ---- dynamic stream (probe order) ----
    _inline_name(s, off)                       # FIX B: aliased name -> FOLLOW+string
    if p(64) in PTRS:
        s.u16(boneCount[9])                    # names: scriptstring u16s
    if p(96) in PTRS:
        for _ in range(notifyCount):           # notify: u16 name + pad2 + f32 time
            s.u16(); s.raw(2); s.u32()
    if p(100) in PTRS:
        _conv_delta(s, numframes, idxw)
    if p(68) in PTRS:
        s.raw(dbc)                             # dataByte
    if p(72) in PTRS:
        s.u16(dsc)                             # dataShort
    if p(76) in PTRS:
        s.u32(dic)                             # dataInt
    if p(80) in PTRS:
        s.u16(rdsc)                            # randomDataShort
    if p(84) in PTRS:
        s.raw(rdbc)                            # randomDataByte
    if p(88) in PTRS:
        s.u32(rdic)                            # randomDataInt
    if p(92) in PTRS:                          # indices
        if idxw == 1:
            s.raw(indexCount)
        else:
            s.u16(indexCount)
    return bytes(s.b), s.o


# ---------------------------------------------------------------- DestructibleDef
def _conv_physconstraints(s):
    """PhysConstraints 2696: name + count + 16 x PhysConstraint(168)."""
    cb0 = s.o
    s.ptr()                                    # name
    s.u32()                                    # count
    for c in range(16):
        s.u16(); s.raw(2)                      # targetname + pad
        s.u32(2)                               # type, attach_point_type1
        s.u32()                                # target_index1
        s.u16(); s.raw(2)                      # target_ent1 + pad
        s.ptr()                                # target_bone1
        s.u32(2)                               # attach_point_type2, target_index2
        s.u16(); s.raw(2)                      # target_ent2 + pad
        s.ptr()                                # target_bone2
        s.u32(25)                              # offset..maxAngle @40..139
        s.ptr()                                # material @140
        s.u32(6)                               # constraintHandle/rope_index/centity_num[4]
    assert s.o == cb0 + 2696
    # dynamic: name string + per-constraint bone strings
    if s.peek32(cb0) in PTRS:
        s.cstr()
    for c in range(16):
        cb = cb0 + 8 + c * 168
        if s.peek32(cb + 20) in PTRS:
            s.cstr()
        if s.peek32(cb + 36) in PTRS:
            s.cstr()


def convert_destructible(pc, off, reloc=_default_reloc):
    """DestructibleDef: PC-identical (destructibledef_probe), 24-B body +
    numPieces x 312-B pieces + per-piece strings/physConstraints."""
    s = Sw(pc, off, reloc)
    num = s.peek32(off + 12)
    s.ptr(3)                                   # name/model/pristineModel
    s.u32()                                    # numPieces
    s.ptr()                                    # pieces
    s.u32()                                    # clientOnly
    _inline_name(s, off)                       # FIX B: aliased name -> FOLLOW+string
    if s.peek32(off + 16) in PTRS:
        base = s.o
        for i in range(num):                   # 312-B piece bodies
            for st in range(5):                # 5 stages x 48
                s.u16(); s.raw(2)              # showBone + pad
                s.u32(3)                       # breakHealth/maxTime/flags
                s.ptr(8)                       # breakEffect..physPreset @16..47
            s.raw(4)                           # parentPiece + pad @240
            s.u32(6)                           # damage scales @244..267
            s.ptr()                            # physConstraints @268
            s.u32()                            # health @272
            s.ptr(3)                           # damageSound/burnEffect/burnSound
            s.u16(); s.raw(2)                  # enableLabel + pad @288
            s.u32(5)                           # hideBones[5]
        assert s.o == base + num * 312
        for i in range(num):                   # per-piece dynamics, probe order
            pb = base + i * 312
            for st in range(5):
                sb = pb + st * 48
                for so in (sb + 20, sb + 24, sb + 28):
                    if s.peek32(so) in PTRS:
                        s.cstr()
            if s.peek32(pb + 268) in PTRS:
                _conv_physconstraints(s)
            if s.peek32(pb + 276) in PTRS:
                s.cstr()
            if s.peek32(pb + 284) in PTRS:
                s.cstr()
    return bytes(s.b), s.o


# ---------------------------------------------------------------- PhysPreset
def convert_physpreset(pc, off, reloc=_default_reloc):
    """Standalone PhysPreset asset: 84-B all-4-byte body + name/sndAliasPrefix."""
    s = Sw(pc, off, reloc)
    s.ptr()                                    # name
    s.u32(6)                                   # flags..explosiveForceScale
    s.ptr()                                    # sndAliasPrefix @28
    s.u32(13)                                  # piecesSpreadFraction..buoyancyBoxMax
    assert s.o == off + 84
    _inline_name(s, off)                       # FIX B: aliased name -> FOLLOW+string
    if s.peek32(off + 28) in PTRS:
        s.cstr()
    return bytes(s.b), s.o


# ---------------------------------------------------------------- GfxLightDef
# Whole-body overlay (raid control, Track E): list of genuine GfxLightDef bodies. The raid
# light cookie ships an 8KB resident inline image absent from base/mp that convert_image
# stubs; emit the genuine body verbatim (compass/MATERIAL_BODY_OVERLAY pattern). The genuine
# body's name is an ALIAS (not inline chars), so name-keying is impossible — substitution is
# positional and only applied for the unambiguous single-lightdef case (raid has exactly 1).
LIGHTDEF_BODY_OVERLAY = None


def convert_lightdef(pc, off, reloc=_default_reloc):
    """GfxLightDef: 16-B body + name + inline GfxImage cookie (image converter)."""
    s = Sw(pc, off, reloc)
    s.ptr(3)                                   # name / attenuation.image / samplerState
    s.u32()                                    # lmapLookupStart
    v = s.peek32(off)
    if v in PTRS:
        s.cstr()                               # inline name string (PC order)
    img = b''
    if s.peek32(off + 4) in PTRS:
        img, nxt = MC.convert_image(pc, s.o, reloc)
        s.o = nxt
    if (LIGHTDEF_STRID is not None and v not in PTRS
            and 0xA0000001 <= v <= 0xBFFFFFFF
            and LIGHTDEF_STRID.answers.get((v - 1) & 0x1FFFFFFF)):
        # FIX B REFUTED (2026-08-13) -- do NOT overwrite the resolved alias.
        #
        # The rule below ("GfxLightDef name == its attenuation image name") is
        # FALSE. Measured on every genuine console zone available here, word0 of
        # each LIGHT_DEF body:
        #     zm_transit_original  8 lightdefs  ALIAS x7 (+1 inline 'mrt')
        #     mp_raid_genuine      1 lightdef   ALIAS 0xA22CAE81
        #     mp_dockside_wiiu     2 lightdefs  ALIAS 0xA20E30C9 / 0xA20E30E0
        # 8 of 9 ship the name as an ALIAS into a shared ComWorld defName run --
        # including RAID, the zone this rule was calibrated on, which is the
        # cleanest possible refutation. Ours shipped
        #     FFFFFFFF FFFFFFFE 00000073 00000000 "whitesquare\0"
        # where genuine transit ships
        #     A2A98F51 FFFFFFFE 00000073 00000000
        # i.e. a plausible name that is the WRONG STRING -- the recorded
        # "lightdefs -> garbage names" class, in the opposite direction.
        #
        # `s.ptr(3)` above has ALREADY resolved this alias correctly, via
        # Omap.reloc's string_identity branch, onto our own copy of the string.
        # The block below then overwrote that correct answer. The fix is simply
        # to stop overwriting it: keep the alias, which also matches genuine's
        # SHAPE and shrinks the body by len(name)+1.
        #
        # Gated on LIGHTDEF_STRID, which the assembler installs only for maps in
        # I1_STRID_MAPS -- so raid/skate frozen builds take the branch below,
        # byte-identically to today.
        pass
    elif (INLINE_NAME_RESOLVER is not None and v not in PTRS
            and 0xA0000001 <= v <= 0xBFFFFFFF):
        # FIX B (gfxtail43 lightdef class): the aliased name's PC payload
        # targets the PC string-alloc region (uninvertable) — but the T6 rule
        # 'GfxLightDef name == its attenuation image name' (raid lightdef rec
        # 0x1083fc88) derives it AUTHORITATIVELY from the CONVERTED console
        # image body (name FOLLOW @+320, string @+328). Generic PC-side
        # resolution is the fallback.
        nm = b''
        if len(img) > 329 and struct.unpack_from('>I', img, 320)[0] == FOLLOW:
            e = img.find(b'\x00', 328)
            if e > 328 and all(0x20 <= c <= 0x7e for c in img[328:e]):
                nm = bytes(img[328:e])
        if not nm:
            nm = INLINE_NAME_RESOLVER(v) or b''
        if nm:
            struct.pack_into('>I', s.b, 0, FOLLOW)
            s.b += nm + b'\x00'                # name payload precedes the image
    s.b += img
    if LIGHTDEF_BODY_OVERLAY and len(LIGHTDEF_BODY_OVERLAY) == 1:
        return LIGHTDEF_BODY_OVERLAY[0], s.o
    return bytes(s.b), s.o


# ---------------------------------------------------------------- Glasses
# Genuine-pointer transplant (raid control, Track E 2026-07-12): the 42 glassDef alias
# words (glasses 1..42 -> glass 0's inline GlassDef) and body word @12 are INTRA-ASSET
# runtime addresses that loader_sim's per-asset-linear runtime model cannot reproduce
# (genuine glassDef rt is +3299 over linear; measured, underdetermined from one anchor —
# Track 0/SPINE scope). Boot-proven fatal when wrong (~Glasses bisect, 2026-07-12).
# For a map with a genuine ref, transplant the genuine word values positionally, exactly
# like the SndBank alias oracle. dict {'w12': u32, 'glassdef': u32} or None.
GLASSES_PTR_OVERLAY = None


def convert_glasses(pc, off, reloc=_default_reloc):
    """Glasses: 56-B body + name + numGlasses x Glass(140) + per-glass
    inline GlassDef(60)/materials/FX/outline verts."""
    import fx_convert as FXC
    s = Sw(pc, off, reloc)
    num = s.peek32(off + 4)
    s.ptr()                                    # name
    s.u32()                                    # numGlasses
    s.ptr()                                    # glasses
    if GLASSES_PTR_OVERLAY is not None:        # word @12 (genuine: 0xffffffff)
        s.b += struct.pack('>I', GLASSES_PTR_OVERLAY['w12'])
        s.o += 4
    else:
        # blind maps (no genuine overlay): the PC word @12 is a PC-heap
        # don't-care; a swapped copy reads as a BAKED POINTER word and the
        # console walk stops at +16 (zm_nuked rewalk drift, 2026-07-15).
        # Genuine console ships 0xffffffff here (raid idx851) — emit that.
        s.b += b'\xff\xff\xff\xff'
        s.o += 4
    s.u32((56 - 16) // 4)                      # remainder of 56-B body (4-byte scalars)
    if s.peek32(off) in PTRS:
        s.cstr()
    if s.peek32(off + 8) in PTRS:
        gbase = s.o
        for i in range(num):                   # Glass 140-B bodies
            s.u32()                            # numCellIndices
            s.u16(6)                           # cellIndices[6]
            if (GLASSES_PTR_OVERLAY is not None
                    and s.peek32(gbase + i * 140 + 16) not in PTRS):
                s.b += struct.pack('>I', GLASSES_PTR_OVERLAY['glassdef'])
                s.o += 4                       # glassDef alias @16 (genuine transplant)
            else:
                s.ptr()                        # glassDef @16
            s.u32(2)                           # index/brushModel
            s.u32(12)                          # origin/angles/absmin/absmax
            s.raw(4)                           # isPlanar/numOutlineVerts/binormalSign/pad
            s.ptr()                            # outline @80
            s.u32(14)                          # outlineAxis[3]+outlineOrigin+uvScale+thickness
        assert s.o == gbase + num * 140
        for i in range(num):
            gb = gbase + i * 140
            if s.peek32(gb + 16) in PTRS:      # inline GlassDef
                gd = s.o
                s.ptr()                        # name
                s.u32(6)                       # maxHealth..maxShards
                s.ptr(3)                       # pristine/cracked/shard Material
                s.ptr(3)                       # crackSound/shatterShound/autoShatterShound
                s.ptr(2)                       # crack/shatterEffect
                if s.peek32(gd) in PTRS:
                    s.cstr()
                for mo in (28, 32, 36):
                    if s.peek32(gd + mo) in PTRS:
                        body, nxt = MC.convert_material(pc, s.o, reloc)
                        s.b += body
                        s.o = nxt
                for so in (40, 44, 48):
                    if s.peek32(gd + so) in PTRS:
                        s.cstr()
                for fo in (52, 56):
                    if s.peek32(gd + fo) in PTRS:
                        body, nxt, _ = FXC.convert_fx(pc, s.o, reloc)
                        s.b += body
                        s.o = nxt
            if s.peek32(gb + 80) in PTRS:      # outline verts
                s.u32(pc[gb + 77] * 2)         # numOutlineVerts x vec2
    return bytes(s.b), s.o


# ---------------------------------------------------------------- GameWorldMp
def _gwmp_pathnode(s):
    """pathnode_t 144: constant(68) + dynamic(48) + transient(28). Returns the
    Links pointer value. Field widths = the chase probe's executable spec
    (probe_gameworldmp_convert.py, byte-exact on raid + dockside)."""
    s.u32(2)                                   # type, spawnflags
    s.u16(5)                                   # targetname..animscript scriptstrings
    s.raw(2)                                   # pad
    s.u32()                                    # animscriptfunc
    s.u32(8)                                   # vOrigin[3] fAngle forward[2] fRadius minUseDistSq
    s.u16(3)                                   # wOverlapNode[2] totalLinkCount
    s.raw(2)                                   # pad
    links = s.peek32(s.o)
    s.ptr()                                    # Links
    s.u16(2)                                   # SentientHandle
    s.u32(8)                                   # iFreeTime iValidTime[3] danger[3] LOS
    s.u16(4)                                   # wLinkCount wOverlapCount turret userCount
    s.raw(4)                                   # bool + pad
    s.u32(7)                                   # transient
    return links


def _gwmp_tree_node(s):
    axis = struct.unpack_from('<i', s.pc, s.o)[0]
    s.u32(2)                                   # axis, dist
    if axis < 0:
        cnt = s.peek32(s.o)
        s.u32()                                # u.s.nodeCount
        p = s.peek32(s.o)
        s.ptr()                                # u.s.nodes
        return ('leaf', cnt, p)
    a = s.peek32(s.o); s.ptr()
    b = s.peek32(s.o); s.ptr()
    return ('split', a, b)


def _gwmp_tree_dyn(s, info):
    if info[0] == 'leaf':
        _, cnt, p = info
        if p in PTRS:
            s.u16(cnt)
    else:
        for child in info[1:]:
            if child in PTRS:
                _gwmp_tree_dyn(s, _gwmp_tree_node(s))


LAST_GWMP_TREE = None   # (offset-within-body, count) of the last emitted nodeTree


def convert_gameworldmp(pc, off, reloc=_default_reloc):
    """GameWorldMp: PC/console serialization IDENTICAL (chase findings §2).
    44-B body + (nodeCount+128) x pathnode_t(144) + per-node pathlink_s(16)
    + pathVis/smoothCache raw + nodeTree. basenodes are RUNTIME (0 bytes)."""
    s = Sw(pc, off, reloc)
    nodeCount = s.peek32(off + 4)
    nodes_p = s.peek32(off + 12)
    visBytes = s.peek32(off + 20)
    vis_p = s.peek32(off + 24)
    smoothBytes = s.peek32(off + 28)
    smooth_p = s.peek32(off + 32)
    treeCount = s.peek32(off + 36)
    tree_p = s.peek32(off + 40)
    s.ptr()                                    # name
    s.u32(2)                                   # nodeCount, originalNodeCount
    s.ptr(2)                                   # nodes, basenodes(runtime)
    s.u32()                                    # visBytes
    s.ptr()                                    # pathVis
    s.u32()                                    # smoothBytes
    s.ptr()                                    # smoothCache
    s.u32()                                    # nodeTreeCount
    s.ptr()                                    # nodeTree
    if nodes_p in PTRS:
        per_node = [_gwmp_pathnode(s) for _ in range(nodeCount + 128)]
        tots = [struct.unpack_from('<H', pc, off + 44 + i * 144 + 60)[0]
                for i in range(nodeCount + 128)]
        for tot, links in zip(tots, per_node):
            if links in PTRS:
                for _ in range(tot):
                    s.u32()                    # fDist
                    s.u16()                    # nodeNum
                    s.raw(10)                  # u8 fields + pad (16-B stride)
    if vis_p in PTRS:
        s.raw(visBytes)
    if smooth_p in PTRS:
        s.raw(smoothBytes)
    if tree_p in PTRS:
        # FIX 4: record where the nodeTree lands inside this body so author_zone can
        # run gwmp_tree_fix on it (child targets emit one node low -> self-loops ->
        # Path_NodesInCylinder_r infinite recursion -> server never finishes G_InitGame).
        global LAST_GWMP_TREE
        LAST_GWMP_TREE = (len(s.b), treeCount)
        infos = [_gwmp_tree_node(s) for _ in range(treeCount)]
        for inf in infos:
            _gwmp_tree_dyn(s, inf)
    return bytes(s.b), s.o


# ---------------------------------------------------------------- ScriptParseTree
def convert_scriptparsetree(pc, off, reloc=_default_reloc):
    """ScriptParseTree via the validated GSC transcoder (gsc_swap: 13/13 raid +
    17/17 dockside byte-exact). Body pointers are always FOLLOW (gsc_swap
    asserts), so no reloc is needed. Span: 12-B struct + name + buffer + NUL.

    REFERENCED variant (first seen zm_nuked ×2, no oracle instance): comma-
    prefixed name with {name FOLLOW, len 0, buffer NULL} and NO inline buffer
    or trailing NUL — every word is endian-neutral, so the console body is the
    PC body verbatim (span = 12 + name string)."""
    ln = struct.unpack_from('<I', pc, off + 4)[0]
    bufptr = struct.unpack_from('<I', pc, off + 8)[0]
    if bufptr == 0 and ln == 0:
        end = pc.index(b'\x00', off + 12) + 1
        return bytes(pc[off:end]), end
    import gsc_swap
    end = pc.index(b'\x00', off + 12) + 1 + ln + 1
    return gsc_swap.convert_spt_body(pc[off:end]), end


# ---------------------------------------------------------------- MenuList
def convert_menulist(pc, off, pc_end, reloc=_default_reloc):
    """MenuList {const char* name; int menuCount; menuDef_t** menus} (12 B).

    zm_nuked ×2 (first map zone with MenuLists): ui_mp/hud_zstandard.txt (3
    inline PC menuDef_t) + hud_zclassic.txt (3 alias ptrs into the first).
    These are PLUTONIUM-era ZM HUD lists — the WiiU engine's ZM HUD is
    patch_zm's ui_mp/hud_zombies.txt and NO genuine console map zone carries a
    MenuList, so the engine never requests these names. The console menuDef_t
    layout (424 B vs PC 400, FINDINGS_menu_console_layout.md) is not
    blind-derivable, so rather than convert menus the engine won't use, emit a
    loadable EMPTY list: {name FOLLOW, count 0, menus NULL} + name string.
    Structurally valid for Load_MenuList, engine-inert. The full PC span
    (incl. inline menuDefs / alias arrays) is consumed via pc_end."""
    name_ptr = struct.unpack_from('<I', pc, off)[0]
    assert name_ptr == FOLLOW, 'MenuList name not inline @0x%x' % off
    nul = pc.index(b'\x00', off + 12)
    body = b'\xff\xff\xff\xff' + b'\x00' * 8 + bytes(pc[off + 12:nul + 1])
    return body, pc_end


# ---------------------------------------------------------------- SkinnedVertsDef
# The WiiU runtime skinned-vertex pool is smaller than PC's. maxSkinnedVerts
# above the console budget makes Load_SkinnedVertsDefAsset's runtime buffer
# allocation fail -> the def pointer is left NULL -> DB_LinkXAssetEntry ->
# DB_SkinnedVertsDefGetName dereferences NULL and the load thread faults
# (zm_nuked boots 2+3, PC value 163840). Genuine console maps stay within
# budget: transit(ZM)=131072, raid(MP)=147456. DEFAULT clamp = the MP/raid
# budget so NO genuine map's value is changed (raid stays byte-exact); the ZM
# container path lowers this to the ZM budget (produce_container sets it).
SKINNEDVERTS_MAX = 0x24000                  # 147456 = genuine raid (MP) budget (default)


def convert_skinnedverts(pc, off, reloc=_default_reloc):
    """SkinnedVertsDef: console body is 24 B — {name*, maxSkinnedVerts} plus 4
    extra FOLLOW pointer words PC lacks (runtime vert buffers) — then the name
    string and a trailing u32=0 (verified against genuine raid: 41 B total).
    maxSkinnedVerts is CLAMPED to the console pool budget (see SKINNEDVERTS_MAX)."""
    s = Sw(pc, off, reloc)
    s.ptr()                                     # name
    mx = struct.unpack_from('<I', pc, s.o)[0]   # maxSkinnedVerts (clamp, don't just swap)
    s.b += struct.pack('>I', min(mx, SKINNEDVERTS_MAX))
    s.o += 4
    s.b += b'\xff' * 16
    if s.peek32(off) in PTRS:
        s.cstr()
    s.b += b'\x00\x00\x00\x00'
    return bytes(s.b), s.o


def fix_rawfile_atr_prefix(body):
    """RawFile {const char* name; int len; const char* buffer}.

    pc_to_console copies the RawFile PAYLOAD verbatim (PCConverter.emit_array,
    the sz == 1 / char branch). That is right for every TEXT rawfile in a T6
    zone (.rmb / .vision / .asd are plain ASCII, read by Com_LoadRawTextFile
    which hands the buffer back as-is) and WRONG for the one COMPRESSED class,
    '.atr': an animtree payload is [u32 uncompressedSize][raw deflate].

    `Scr_ReadFile_FastFile` (guest 0x025E4878) reads that prefix with
    `lwz r29,0(r31)` -- a BIG-ENDIAN load by definition -- and passes size + 1
    straight to Hunk_AllocateTempMemoryHigh. Shipping PC's LITTLE-endian word
    therefore asks for gigabytes: zm_nuked's 9959-byte animtree read back as
    0xE7260000 = 3.88 GB. Worse, the hunk's out-of-memory guard at 0x0250AA70
    is a SIGNED `cmpw`, so the huge value goes negative, skips Com_Error
    entirely, and the allocator memsets an unmapped address (boot 38).

    Byte-reversing the prefix is SIZE-PRESERVING, so the loader walk is
    untouched (Load_RawFile consumes len + 1 bytes from the struct's own `len`,
    which pc_to_console already swaps correctly). The deflate body itself is
    byte-order neutral (Com_UncompressData = inflateInit2 with windowBits -13).

    Verified: 7/7 resident RETAIL .atr RawFiles store the prefix big-endian;
    raw-inflating our payloads yields exactly the LITTLE-endian reading.
    """
    if len(body) < 13 or body[8:12] not in (b'\xff\xff\xff\xff', b'\xff\xff\xff\xfe'):
        return body                       # NULL buffer / dedup-alias record
    try:
        nul = body.index(b'\x00', 12)
    except ValueError:
        return body
    if not body[12:nul].lower().endswith(b'.atr'):
        return body
    p = nul + 1
    if p + 4 > len(body):
        return body
    b = bytearray(body)
    b[p:p + 4] = bytes(reversed(b[p:p + 4]))
    return bytes(b)
