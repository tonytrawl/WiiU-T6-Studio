"""zone_facts -- THE SUBSTRATE. The one module every instrument imports for the facts it
would otherwise re-derive: where the asset array is, what pass a counter came from, which
coordinate frame a number lives in, which byte order a word was read with, and what an
asset is called.

CANONICAL: native_linker/zone_facts.py  (relink_validation/ and WiiU_FF_Studio/dist/ carry
frozen v1 copies; canonical_gate.py guards them; substrate_gate.py guards the CALLERS.)

=====================================================================================
WHY THIS FILE WAS REWRITTEN (2026-08-17, USER-DIRECTED: "every lane re-derives the same
substrate facts; name the boundary explicitly")
=====================================================================================
ONE DEFECT CLASS, MANY COSTUMES -- every one a real incident from the previous 48 h:

  * cross-pass census ............ region_registration_gate v1 read 3 where the final pass = 21
  * frame-mismatched guard ....... our_arr 16,028 vs rtmap.rt(container_prefix) 39,654 --
                                   an emitted-stream-frame map fed a container-frame offset
  * wrong-endian decode .......... a BE read of the LE PC zone passed every injectivity check;
                                   only the ABSOLUTE COUNT (0 where 18,998 was expected) exposed it
  * span-head name helper ........ printable-run scan returned pooled neighbours: two wrong
                                   asset names, both plausible
  * duplicated container model ... stage1's own assets_off (depends unconsumed + spurious
                                   align4): 6/15 live patch zones + the live map zone wrong

Every one is a bespoke probe RE-DERIVING offsets / passes / frames / endianness that some
other module already owns. v1 of THIS file was one of them: a "dependency-free" reader that
re-implemented the container walk beside wiiu_zone.ZoneReader (its docstring justified that
by a ZoneReader defect that was FIXED on 2026-07-30, rule AM). The v1 source is preserved as
`substrate_fixtures/zone_facts_v1_rederive.py` -- the substrate gate's positive control.

THE COLLAPSE-TO-DELEGATION LAW (banked 2026-08-17): when a duplicated model collapses into a
delegation, the delegating module GAINS A DEPENDENCY it must declare. This file now depends
on `wiiu_ref/wiiu_zone.py` (console container OWNER), `native_linker/pc_zone.py` (PC
container owner), `alias_encode.Coord` / `loader_sim.RuntimeMap` (runtime-frame owners) and
`produce_nobackbone.Omap.branch_histogram` (pass-structure owner). Missing owners raise
`SubstrateDependencyMissing` naming the path -- never a silent local fallback.

=====================================================================================
THE LAWS THIS FILE ENFORCES BY CONSTRUCTION (cited by name so the code cites the law and the
law cites the code; each is a memory topic or a rule letter in HANDOFF_pipeline_bake_rules_v2)
=====================================================================================
  [[alias-coordinate-system-law]] (FO)   -- offsets carry their FRAME; cross-frame arithmetic
                                            and comparison RAISE FrameMismatch, never return
  "BEFORE YOU SUBTRACT TWO NUMBERS, PROVE THEY LIVE IN THE SAME SPACE" (instrument-laws)
  (HF) guards state their frame          -- runtime_of_stream() takes STREAM_B5 only;
                                            runtime_of_file() takes FILE only
  (HB)/(HE) OMAP STATS RESET PER PASS    -- pass_structure() delegates to branch_histogram();
                                            pass_census() scores the FINAL pass and reconciles
                                            its pass count against the omap's own
  "A CENSUS THAT ACCUMULATES ACROSS PASSES HIDES THE FINAL-PASS FAILURE" (mirage lane 08-17)
  "THE PC ZONE IS LITTLE-ENDIAN ..." (nuked lane 08-16) -- every word reader states its byte
                                            order IN ITS NAME (read_u32_be / read_u32_le); a
                                            console Facts exposes only *_be, a PC Facts only *_le
  "one model, one owner" / [[stringtable-cell-and-container-laws]] -- assets_off is READ from
                                            wiiu_zone.ZoneReader / pc_zone.PCZoneReader, never
                                            re-derived; PC dependCount>0 REFUSES (pc_zone.py:44
                                            latent defect, documented, not fixed here)
  "THE WRITER'S ARRAY BASE WAS A GUESS ACCUSING A GUESS" / et7 align4 (rule AX/AC, 71 genuine
                                            zones) -- array_base() is the ONE place raw / phase /
                                            align4 / (refuted) align8 are computed and LABELLED
  [[valid-index-wrong-base-law]]         -- entry_slot_rt() derives slots from the labelled base
  "THE CONSOLE BLOCK-5 RULE DOES NOT DECODE PC NAME ALIASES" (last-two lane 08-17) -- PC alias
                                            payloads are their OWN frame (PC_ALIAS_PAYLOAD) and
                                            reach a FILE offset only through NameAliasBase (per-
                                            zone empirical K, UNIQUENESS refusal; 505/506 vs OAT)
  "TRUNCATION IS INVISIBLE TO SANITY CHECKS"  -- name resolution refuses non-string-starts and
                                            non-names instead of returning a plausible tail
  "file_of_rt MUST DISCLOSE REGISTRANT COUNT" (08-16) -- file_of_runtime() returns registrants
  "UNRESOLVABLE = UNKNOWN, never broken and never skipped" -- refusals RAISE typed exceptions;
                                            nothing here returns None for "could not"
  "A CONVERTER REFUSAL MUST RAISE -- ONLY THE OUTERMOST DRIVER MAY sys.exit" -- no exit here
  "STATE THE POPULATION WITH EVERY CENSUS" -- every solver/census result carries n and counts
  [[gate-identity-pinning-law]] / (GX)  -- this file is pinned by its CANONICAL: line + the
                                            substrate_gate PASS, not by md5

=====================================================================================
CONTAINER LAYOUT (unchanged from v1; the owner's model, restated for the reader)
=====================================================================================
    0x00  u32 size            (decompressed payload size = len - 40)
    0x04  u32 externalSize
    0x08  u32 blockSizes[8]
    0x28  u32 stringCount     <-- XAssetList begins here (file offset 40)
    0x2C  u32 strings*
    0x30  u32 dependCount
    0x34  u32 depends*
    0x38  u32 assetCount
    0x3C  u32 assets*
    0x40  container content begins (== block-5 rt 0 == file 64; B5_BASE = 64)
Order: strings -> depends -> assets. The array follows the inline data UNALIGNED in the file;
the loader places it 4-ALIGNED at runtime (rule AX/AC), pad = (-AO) mod 4, K = 63 - pad.

FRAMES (first-class; see `Frame`, `Off`)
    FILE ............... byte offset into the zone bytes (header at 0)
    CONTAINER_B5 ....... FILE - 64: block-5 offset of a real container's content
    STREAM_B5 .......... offset in OUR EMITTED BODY STREAM (co_cursor space; 0 = first body
                         byte); a produced container puts it at CONTAINER_B5 = prefix + 8*narr
                         + STREAM_B5.  `RuntimeMap.rt()` speaks THIS frame on input.
    RUNTIME_RT ......... the loader's block-5 placement (pads, temp-block skips); handles
                         encode (5<<29 | rt) + 1.  Reachable ONLY through a loader-sim map
                         (`alias_encode.Coord` for a real zone, `RuntimeMap` for our stream) or,
                         for the asset array alone, the align4 rule.
    PC_ALIAS_PAYLOAD ... (v-1) & 0x1FFFFFFF of a PC handle: a PC-RUNTIME address; reaches a PC
                         FILE offset only through `NameAliasBase` (empirical K) or `InverseMap`.
"""
import io
import os
import struct
import sys
from collections import Counter, namedtuple
from contextlib import contextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
_WIIU_REF = os.path.normpath(os.path.join(_HERE, '..', 'wiiu_ref'))

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
ALIAS_LO, ALIAS_HI = 0xA0000000, 0xC0000000        # v1 names (half-open)
BLOCK5_LO, BLOCK5_HI = 0xA0000001, 0xBFFFFFFF      # inclusive handle range
BLOCK_VIRTUAL = 5
CONTAINER_RT_BASE = 64          # file offset where block-5 rt 0 sits
B5_BASE = 64                    # the fleet's name for the same constant


def a4(x):
    return (x + 3) & ~3


def a8(x):
    return (x + 7) & ~7


# ======================================================================================
# TYPED REFUSALS -- every "could not" is one of these; nothing here returns None for it
# ======================================================================================
class SubstrateRefusal(RuntimeError):
    """Base: a fact could not be established from the subject. Carries the datum."""


class SubstrateDependencyMissing(SubstrateRefusal, ImportError):
    """A declared owner module is not importable. Named, never silently replaced."""


class ContainerTruncated(SubstrateRefusal, ValueError):
    """The zone is too short for the structure the header declares."""


class ContainerMalformed(SubstrateRefusal, ValueError):
    """The container's own words contradict each other (owner-detected)."""


class PCDependsUnsupported(SubstrateRefusal):
    """pc_zone.PCZoneReader (pc_zone.py:44) never consumes the depends region; a PC zone with
    dependCount > 0 would get an assets_off short by that region. Documented latent defect,
    the mirage lane's post-fence item -- NOT fixed here, so the substrate REFUSES rather
    than hand back a known-wrong number. (All PC zones on disk 2026-08-17 have DC = 0.)"""


class FrameMismatch(SubstrateRefusal, TypeError):
    """Two offsets from different coordinate frames (or platforms) met in one expression."""


class NotABlock5Handle(SubstrateRefusal, ValueError):
    """The word is not in 0xA0000001..0xBFFFFFFF."""


class RuntimeUnplaceable(SubstrateRefusal):
    """alias_encode.Coord refused to place a file offset in the loader's frame."""


class PassUnnamed(SubstrateRefusal):
    """A pass-dependent number was requested without the omap's own pass structure."""


class NotUniquelySolved(SubstrateRefusal):
    """The per-zone name-alias base K did not solve to exactly one candidate."""


class NameUnresolved(SubstrateRefusal):
    """A name word could not be resolved to a string START that looks like a name."""


class EmptyPopulation(SubstrateRefusal):
    """A solve/census was asked over zero subjects; a zero population validates nothing."""


# ======================================================================================
# DECLARED DEPENDENCIES (lazy; each names its owner path when missing)
# ======================================================================================
def _wiiu_zone():
    if _WIIU_REF not in sys.path:
        sys.path.insert(0, _WIIU_REF)
    try:
        import wiiu_zone
    except ImportError as ex:
        raise SubstrateDependencyMissing(
            'zone_facts delegates the CONSOLE container model to wiiu_ref/wiiu_zone.py '
            '(ZoneReader, rule AM depends-aware) and it is not importable from %s: %s'
            % (_WIIU_REF, ex))
    return wiiu_zone


def _pc_zone():
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    try:
        import pc_zone
    except ImportError as ex:
        raise SubstrateDependencyMissing(
            'zone_facts delegates the PC container model to native_linker/pc_zone.py '
            '(PCZoneReader) and it is not importable: %s' % ex)
    return pc_zone


def _alias_encode():
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    try:
        import alias_encode
    except ImportError as ex:
        raise SubstrateDependencyMissing(
            'zone_facts delegates handle encoding / file->runtime placement to '
            'native_linker/alias_encode.py (Coord) and it is not importable: %s' % ex)
    return alias_encode


# ======================================================================================
# ENDIAN-STATED WORD READERS -- the byte order is in the NAME, never implied by the subject
# ======================================================================================
def read_u32_be(buf, off):
    return struct.unpack_from('>I', buf, off)[0]


def read_u32_le(buf, off):
    return struct.unpack_from('<I', buf, off)[0]


def read_u16_be(buf, off):
    return struct.unpack_from('>H', buf, off)[0]


def read_u16_le(buf, off):
    return struct.unpack_from('<H', buf, off)[0]


def read_s32_be(buf, off):
    return struct.unpack_from('>i', buf, off)[0]


def read_s32_le(buf, off):
    return struct.unpack_from('<i', buf, off)[0]


def read_s16_be(buf, off):
    return struct.unpack_from('>h', buf, off)[0]


def read_s16_le(buf, off):
    return struct.unpack_from('<h', buf, off)[0]


def read_f32_be(buf, off):
    return struct.unpack_from('>f', buf, off)[0]


def read_f32_le(buf, off):
    return struct.unpack_from('<f', buf, off)[0]


def read_words_be(buf, off, n):
    """n big-endian u32 words starting at off (tuple)."""
    return struct.unpack_from('>%dI' % n, buf, off) if n else ()


def read_words_le(buf, off, n):
    return struct.unpack_from('<%dI' % n, buf, off) if n else ()


def read_cstr(buf, off, cap=None):
    """NUL-terminated bytes at off (endian-free). Raises ContainerTruncated if unterminated
    within cap / EOF -- a truncated string is never returned as a plausible tail."""
    hi = len(buf) if cap is None else min(len(buf), off + cap)
    e = buf.find(b'\x00', off, hi)
    if e < 0:
        raise ContainerTruncated('unterminated string at file offset %d (searched to %d)'
                                 % (off, hi))
    return bytes(buf[off:e])


def pack_u32_be(v):
    return struct.pack('>I', v)


def pack_u32_le(v):
    return struct.pack('<I', v)


class BigEndianWords(object):
    """A reader BOUND to a big-endian subject (console). Only *_be methods exist, so a
    call site reads `f.words.u32_be(o)` and the byte order is visible where the read is."""
    endian = 'be'
    platform = 'wiiu'
    __slots__ = ('buf',)

    def __init__(self, buf):
        self.buf = buf

    def u32_be(self, off):
        return read_u32_be(self.buf, off)

    def u16_be(self, off):
        return read_u16_be(self.buf, off)

    def s32_be(self, off):
        return read_s32_be(self.buf, off)

    def f32_be(self, off):
        return read_f32_be(self.buf, off)

    def words_be(self, off, n):
        return read_words_be(self.buf, off, n)

    def cstr(self, off, cap=None):
        return read_cstr(self.buf, off, cap)


class LittleEndianWords(object):
    """A reader BOUND to a little-endian subject (PC). Only *_le methods exist."""
    endian = 'le'
    platform = 'pc'
    __slots__ = ('buf',)

    def __init__(self, buf):
        self.buf = buf

    def u32_le(self, off):
        return read_u32_le(self.buf, off)

    def u16_le(self, off):
        return read_u16_le(self.buf, off)

    def s32_le(self, off):
        return read_s32_le(self.buf, off)

    def f32_le(self, off):
        return read_f32_le(self.buf, off)

    def words_le(self, off, n):
        return read_words_le(self.buf, off, n)

    def cstr(self, off, cap=None):
        return read_cstr(self.buf, off, cap)


def words_for(platform, buf):
    """The bound reader for a platform ('wiiu' -> big-endian, 'pc' -> little-endian)."""
    if platform == 'wiiu':
        return BigEndianWords(buf)
    if platform == 'pc':
        return LittleEndianWords(buf)
    raise SubstrateRefusal('unknown platform %r (want "wiiu" or "pc")' % (platform,))


# ======================================================================================
# COORDINATE FRAMES -- first-class, refusing cross-frame comparison instead of returning
# ======================================================================================
class Frame(object):
    FILE = 'file'
    CONTAINER_B5 = 'container-b5'
    STREAM_B5 = 'emitted-stream-b5'
    RUNTIME_RT = 'runtime-rt'
    PC_ALIAS_PAYLOAD = 'pc-alias-payload'
    ALL = (FILE, CONTAINER_B5, STREAM_B5, RUNTIME_RT, PC_ALIAS_PAYLOAD)


class Off(object):
    """An offset that KNOWS its frame and platform.

    * `Off +/- int` stays in the frame.  `Off - Off` (same frame+platform) is a plain distance.
    * Any arithmetic or comparison across frames/platforms, or against a bare int, RAISES
      FrameMismatch -- the whole point ("prove they live in the same space" is now a type).
    * `int(off)` / `.v` is the explicit escape hatch; the substrate gate polices its callers.
    * `how` records the provenance ('exact', 'interp+12', 'rule AX/AC align4', ...)."""
    __slots__ = ('v', 'frame', 'platform', 'how')

    def __init__(self, v, frame, platform, how=''):
        if frame not in Frame.ALL:
            raise SubstrateRefusal('unknown frame %r' % (frame,))
        if platform not in ('wiiu', 'pc'):
            raise SubstrateRefusal('unknown platform %r' % (platform,))
        self.v = int(v)
        self.frame = frame
        self.platform = platform
        self.how = how

    # ---- construction helpers -----------------------------------------------------------
    def _same(self, v, how=None):
        return Off(v, self.frame, self.platform, self.how if how is None else how)

    def _check(self, other, op):
        if not isinstance(other, Off):
            raise FrameMismatch(
                '%s %s %r: a frame-labelled offset met a bare number. Label the number '
                '(zone_facts.file_off / container_b5 / stream_b5 / runtime_rt) or take '
                '`.v` EXPLICITLY.' % (self, op, other))
        if other.frame != self.frame or other.platform != self.platform:
            raise FrameMismatch(
                '%s %s %s: different frames (%s/%s vs %s/%s). Convert first '
                '(zone_facts.file_to_container / stream_to_container / runtime_of_stream / '
                'runtime_of_file); a cross-frame number is the (FO) defect.'
                % (self, op, other, self.frame, self.platform, other.frame, other.platform))

    # ---- arithmetic -------------------------------------------------------------------
    def __add__(self, k):
        if isinstance(k, Off):
            raise FrameMismatch('%s + %s: adding two offsets is meaningless in any frame'
                                % (self, k))
        return self._same(self.v + int(k))

    __radd__ = __add__

    def __sub__(self, k):
        if isinstance(k, Off):
            self._check(k, '-')
            return self.v - k.v            # a DISTANCE: frame-free int
        return self._same(self.v - int(k))

    def __neg__(self):
        raise FrameMismatch('%s: negating an offset has no frame' % self)

    def __int__(self):
        return self.v

    def __index__(self):
        return self.v

    # ---- comparison -------------------------------------------------------------------
    def __eq__(self, other):
        if other is None:
            return False
        self._check(other, '==')
        return self.v == other.v

    def __ne__(self, other):
        if other is None:
            return True
        self._check(other, '!=')
        return self.v != other.v

    def __lt__(self, other):
        self._check(other, '<')
        return self.v < other.v

    def __le__(self, other):
        self._check(other, '<=')
        return self.v <= other.v

    def __gt__(self, other):
        self._check(other, '>')
        return self.v > other.v

    def __ge__(self, other):
        self._check(other, '>=')
        return self.v >= other.v

    def __hash__(self):
        return hash((self.v, self.frame, self.platform))

    def __repr__(self):
        return 'Off(%d %s/%s%s)' % (self.v, self.frame, self.platform,
                                     ' ' + self.how if self.how else '')


def file_off(v, platform='wiiu', how=''):
    return Off(v, Frame.FILE, platform, how)


def container_b5(v, platform='wiiu', how=''):
    return Off(v, Frame.CONTAINER_B5, platform, how)


def stream_b5(v, how=''):
    return Off(v, Frame.STREAM_B5, 'wiiu', how)


def runtime_rt(v, platform='wiiu', how=''):
    return Off(v, Frame.RUNTIME_RT, platform, how)


def pc_alias_payload(v, how=''):
    return Off(v, Frame.PC_ALIAS_PAYLOAD, 'pc', how)


def _want(off, frame, fn):
    if not isinstance(off, Off):
        raise FrameMismatch('%s: expected a frame-labelled Off in frame %s, got bare %r. '
                            'Label it (zone_facts.%s(...)) -- an unlabelled number is exactly '
                            'the (HF) guard defect.' % (fn, frame, off,
                                                        {Frame.FILE: 'file_off',
                                                         Frame.CONTAINER_B5: 'container_b5',
                                                         Frame.STREAM_B5: 'stream_b5',
                                                         Frame.RUNTIME_RT: 'runtime_rt',
                                                         Frame.PC_ALIAS_PAYLOAD: 'pc_alias_payload'}[frame]))
    if off.frame != frame:
        raise FrameMismatch('%s: expected frame %s, got %s' % (fn, frame, off))
    return off


# ---- pure conversions (arithmetic that IS lawful) ---------------------------------------
def file_to_container(off):
    """FILE -> CONTAINER_B5 (minus B5_BASE). Same platform."""
    o = _want(off, Frame.FILE, 'file_to_container')
    return Off(o.v - B5_BASE, Frame.CONTAINER_B5, o.platform, o.how)


def container_to_file(off):
    o = _want(off, Frame.CONTAINER_B5, 'container_to_file')
    return Off(o.v + B5_BASE, Frame.FILE, o.platform, o.how)


ProducedLayout = namedtuple('ProducedLayout', 'container_prefix_raw narr')
ProducedLayout.__doc__ = """The produced container's shape: the RAW string-region size
(container_prefix = assets_off - 64 of the produced zone, UNALIGNED -- the container writes
the array at raw prefix; `assert len(content) == 24 + prefix + narr*8`) and the row count."""


def stream_to_container(off, layout):
    """STREAM_B5 (our emitted body stream, co_cursor space) -> CONTAINER_B5 of the produced
    zone: bodies begin right after the array, at container b5 = prefix + 8*narr."""
    o = _want(off, Frame.STREAM_B5, 'stream_to_container')
    if not isinstance(layout, ProducedLayout):
        raise SubstrateRefusal('stream_to_container needs a ProducedLayout, got %r' % (layout,))
    return Off(layout.container_prefix_raw + 8 * layout.narr + o.v, Frame.CONTAINER_B5,
               'wiiu', o.how)


def container_to_stream(off, layout):
    o = _want(off, Frame.CONTAINER_B5, 'container_to_stream')
    if not isinstance(layout, ProducedLayout):
        raise SubstrateRefusal('container_to_stream needs a ProducedLayout, got %r' % (layout,))
    v = o.v - (layout.container_prefix_raw + 8 * layout.narr)
    if v < 0:
        raise FrameMismatch('%s lies BEFORE the body stream of %r (array/strings region) -- '
                            'it has no STREAM_B5 image' % (o, layout))
    return Off(v, Frame.STREAM_B5, 'wiiu', o.how)


# ---- conversions that need the LOADER's map (never arithmetic) --------------------------
def runtime_of_stream(rtmap, off):
    """STREAM_B5 -> RUNTIME_RT through loader_sim.RuntimeMap (our emitted stream). The frame
    check on `off` IS the (HF) guard: feeding `container_prefix` here raised 39,654 vs 16,028
    on mirage and blocked 18 FX."""
    o = _want(off, Frame.STREAM_B5, 'runtime_of_stream')
    if not hasattr(rtmap, 'rt'):
        raise SubstrateRefusal('runtime_of_stream needs a loader_sim.RuntimeMap-like object '
                               'with .rt(stream_b5); got %r' % (rtmap,))
    return Off(int(rtmap.rt(o.v)), Frame.RUNTIME_RT, 'wiiu', 'RuntimeMap.rt')


def runtime_of_file(coord, off, align=None):
    """FILE -> RUNTIME_RT through alias_encode.Coord.rt_of_file (loader_sim of a REAL zone).
    Returns Off with .how = 'exact' | 'interp+N'. Refusal -> RuntimeUnplaceable (typed)."""
    o = _want(off, Frame.FILE, 'runtime_of_file')
    AE = _alias_encode()
    try:
        if align is None:
            rt, how = coord.rt_of_file(o.v)
        else:
            rt, how = coord.rt_of_file(o.v, align=align)
    except AE.Refused as ex:
        raise RuntimeUnplaceable(str(ex))
    return Off(rt, Frame.RUNTIME_RT, o.platform, how)


Placement = namedtuple('Placement', 'off registrants how')
Placement.__doc__ = """file_of_runtime()'s answer: the FILE Off, how many omap anchors map to
that runtime offset (0 = interpolated/unregistered, >1 = ambiguous -- (GL) last-wins), how."""


def file_of_runtime(coord, off):
    """RUNTIME_RT -> FILE through alias_encode.Coord.file_of_rt, DISCLOSING the registrant
    count (law: file_of_rt MUST DISCLOSE REGISTRANT COUNT; a verdict resting on >1 registrant
    is AMBIGUOUS, and 0 means the answer is an interpolation, never a fact)."""
    o = _want(off, Frame.RUNTIME_RT, 'file_of_runtime')
    regs = getattr(coord, '_zone_facts_rt_regs', None)
    if regs is None:
        regs = Counter(coord.omap.values())
        try:
            coord._zone_facts_rt_regs = regs
        except Exception:
            pass
    fo = coord.file_of_rt(o.v)
    if fo is None:
        raise RuntimeUnplaceable('runtime %d has no anchor at or below it in the omap' % o.v)
    n = regs.get(o.v, 0)
    return Placement(Off(fo, Frame.FILE, o.platform, 'exact' if n else 'interp'), n,
                     'exact(%d registrant%s)' % (n, '' if n == 1 else 's') if n else 'interp')


def console_handle_to_runtime(v):
    """A CONSOLE block-5 handle -> RUNTIME_RT (the (v-1)&0x1FFFFFFF rule -- console ONLY)."""
    if not (BLOCK5_LO <= v <= BLOCK5_HI):
        raise NotABlock5Handle('0x%08X is not a block-5 handle (0xA0000001..0xBFFFFFFF)' % v)
    return Off((v - 1) & 0x1FFFFFFF, Frame.RUNTIME_RT, 'wiiu', 'handle')


def runtime_to_console_handle(off):
    o = _want(off, Frame.RUNTIME_RT, 'runtime_to_console_handle')
    if o.platform != 'wiiu':
        raise FrameMismatch('%s: only a CONSOLE runtime offset encodes to a console handle' % o)
    return _alias_encode().Coord.encode_rt(o.v)


def pc_handle_payload(v):
    """A PC block-5 handle -> PC_ALIAS_PAYLOAD (a PC-RUNTIME address). It is NOT a file offset:
    `payload + 64` lands 2 B inside the string on PC name aliases (measured; no delta in -4..+4
    beats 3/13). Reach a PC FILE offset through NameAliasBase or a loader_sim InverseMap."""
    if not (BLOCK5_LO <= v <= BLOCK5_HI):
        raise NotABlock5Handle('0x%08X is not a block-5 handle (0xA0000001..0xBFFFFFFF)' % v)
    return Off((v - 1) & 0x1FFFFFFF, Frame.PC_ALIAS_PAYLOAD, 'pc', 'handle')


# ======================================================================================
# CONTAINER FACTS -- delegating to the owners
# ======================================================================================
ArrayBase = namedtuple('ArrayBase', 'file container_raw phase4 phase8 runtime_align4 '
                                    'align8_refuted end_runtime n')
ArrayBase.__doc__ = """The XAsset array's base in every frame anyone has ever wanted it in:
  file            Off FILE          -- where the array's first byte sits in the zone bytes
  container_raw   Off CONTAINER_B5  -- file - 64 (the writer places the array HERE, unaligned)
  phase4/phase8   int               -- container_raw % 4 / % 8 (the phase that decides whether
                                       align4 and align8 differ: they do iff phase8 in {1,2,3,4})
  runtime_align4  Off RUNTIME_RT    -- the LOADER's placement, rule AX/AC (71 genuine zones;
                                       16-zone census: align4 wins 60-200x on every differing phase)
  align8_refuted  int               -- (raw+7)&~7, the SHIFT8 rule REFUTED for et7 (mirage 0/69 ->
                                       69/69 under align4). Provided so a census can COMPARE without
                                       re-deriving; never use it as a base.
  end_runtime     Off RUNTIME_RT    -- runtime_align4 + 8*n (== v1 `arrend_rt`)
  n               int               -- assetCount"""


def _classify_depends_region(region, DC):
    """CLASSIFY the raw depends bytes the OWNER bounded (strings_end .. assets_off) -- which
    layout the bytes support. This is classification, not derivation: the region's extent
    comes from the owner's assets_off. Returns one of 'n/a' (DC=0), 'batched', 'interleaved',
    'both(DC=n, ambiguous)', 'neither'."""
    if DC == 0:
        return 'n/a'
    L = len(region)

    def batched():
        o = 4 * DC
        if o > L:
            return False
        if any(w != FOLLOW for w in read_words_be(region, 0, DC)):
            return False
        for _ in range(DC):
            e = region.find(b'\x00', o)
            if e < 0 or e - o > 128:
                return False
            o = e + 1
        return o == L

    def interleaved():
        o = 0
        for _ in range(DC):
            if o + 4 > L or read_u32_be(region, o) != FOLLOW:
                return False
            o += 4
            e = region.find(b'\x00', o)
            if e < 0 or e - o > 128:
                return False
            o = e + 1
        return o == L

    b, i = batched(), interleaved()
    if b and i:
        return 'both(DC=%d, ambiguous)' % DC
    if b:
        return 'batched'
    if i:
        return 'interleaved'
    return 'neither'


class Facts(object):
    """Container facts for one CONSOLE (big-endian) zone -- attribute names stable since v1
    (rung0_one.py serialises them; ~60 callers). Every offset below is READ from
    wiiu_zone.ZoneReader (the owner); nothing here walks the string table itself.

    Legacy int attributes (assets_off, assets_end, pre_rt, arrend_rt, pad, K_pred, ...) are
    kept as plain ints for compatibility. The frame-labelled view is `array_base()`; the
    endian-stated reader is `words` (BigEndianWords: `f.words.u32_be(o)`)."""
    platform = 'wiiu'
    endian = 'be'

    def __init__(self, data, name=None):
        self.z = data
        self.name = name
        self.n = len(data)
        if self.n < 0x40:
            raise ContainerTruncated('zone is %d B, too short to hold a container header'
                                     % self.n)
        WZ = _wiiu_zone()
        r = WZ.ZoneReader(data)
        self._reader = r                       # the OWNER's object, exposed for callers
        self.words = BigEndianWords(data)
        self.size, self.external_size = r.size, r.external_size
        self.block_sizes = list(r.block_sizes)
        # XAssetList pointer words, at the owner's documented offsets (0x2C/0x34/0x3C); the
        # counts come from the owner after it parses.
        self.strings_p = read_u32_be(data, 0x2C)
        self.depends_p = read_u32_be(data, 0x34)
        self.assets_p = read_u32_be(data, 0x3C)

        self.warn = []
        try:
            r.read_string_table()              # <-- THE derivation, owner-side
            r.read_asset_list()
        except ValueError as ex:
            raise ContainerMalformed('%s: %s' % (name or 'zone', ex))
        except (struct.error, IndexError) as ex:
            raise ContainerTruncated('%s: container structure runs past EOF (%d B): %s'
                                     % (name or 'zone', self.n, ex))
        self.string_count = r.string_count
        self.depend_count = r.depend_count
        self.asset_count = r.asset_count
        self.assets_off = r.assets_off
        self.assets_end = r.assets_end
        self.assets = list(r.assets)           # (console_id, pc_id, pc_name)
        if self.assets_end > self.n:
            raise ContainerTruncated('asset array ends at 0x%x, past EOF 0x%x'
                                     % (self.assets_end, self.n))

        # the size field counts the payload AFTER the 40-byte header (patch_mp: 14713280-40).
        if self.size != self.n - 40:
            self.warn.append('header size 0x%x != actual-40 0x%x' % (self.size, self.n - 40))
        if self.strings_p not in (FOLLOW, 0):
            self.warn.append('strings* = 0x%08x (expected FOLLOW or NULL)' % self.strings_p)
        if self.depend_count and self.depends_p not in (FOLLOW, 0):
            self.warn.append('dependCount=%d but depends* = 0x%08x'
                             % (self.depend_count, self.depends_p))

        # ---- regions, bounded by the owner's offsets ---------------------------------------
        S = self.string_count
        ptrs = read_words_be(data, CONTAINER_RT_BASE, S)
        self.string_ptr_kinds = {'FOLLOW': 0, 'NULL': 0, 'OTHER': 0}
        for p in ptrs:
            self.string_ptr_kinds['FOLLOW' if p == FOLLOW else 'NULL' if p == 0 else 'OTHER'] += 1
        self.strings = [s.encode('latin-1') for s in r.strings[1:]]     # v1: bytes, no leading ''
        self.char_bytes = sum(len(s) + 1 for s in self.strings)
        strings_end = CONTAINER_RT_BASE + 4 * S + self.char_bytes
        self.depend_bytes = self.assets_off - strings_end
        if self.depend_bytes < 0:
            raise ContainerMalformed('owner assets_off 0x%x precedes strings_end 0x%x'
                                     % (self.assets_off, strings_end))
        self.depends = [d.encode('latin-1') for d in r.depends]
        self.depend_layout = _classify_depends_region(
            bytes(data[strings_end:self.assets_off]), self.depend_count)
        if self.depend_layout == 'neither':
            self.warn.append('dependCount=%d but neither depends layout fills 0x%x..0x%x'
                             % (self.depend_count, strings_end, self.assets_off))

        # ---- the derived quantities the relink model rests on (v1 names) --------------------
        raw = self.assets_off - CONTAINER_RT_BASE
        self.pad = (-self.assets_off) % 4
        self.K_pred = 63 - self.pad
        self.pre_rt = a4(raw)                  # == a4(4S + C + D): the array's runtime base
        self.arrend_rt = self.pre_rt + 8 * self.asset_count
        self.arrend_alias_word = (BLOCK_VIRTUAL << 29) + self.arrend_rt + 1
        self.b5 = self.block_sizes[BLOCK_VIRTUAL]
        self.blocks_used = [i for i, b in enumerate(self.block_sizes) if b]

    # ---------------------------------------------------------------- frame-labelled view
    def array_base(self):
        raw = self.assets_off - CONTAINER_RT_BASE
        return ArrayBase(
            file=Off(self.assets_off, Frame.FILE, 'wiiu', 'owner:wiiu_zone.ZoneReader'),
            container_raw=Off(raw, Frame.CONTAINER_B5, 'wiiu', 'writer places array here'),
            phase4=raw % 4, phase8=raw % 8,
            runtime_align4=Off(a4(raw), Frame.RUNTIME_RT, 'wiiu',
                               'rule AX/AC align4 (loader_sim.simulate:504, 71 genuine zones)'),
            align8_refuted=a8(raw),
            end_runtime=Off(a4(raw) + 8 * self.asset_count, Frame.RUNTIME_RT, 'wiiu',
                            'align4 base + 8*n'),
            n=self.asset_count)

    def entry_slot_rt(self, idx):
        """RUNTIME_RT of entry idx's headerPtr word (the et7 target slot): base + 8*idx + 4."""
        if not (0 <= idx < self.asset_count):
            raise SubstrateRefusal('entry %d out of range (assetCount %d)' % (idx, self.asset_count))
        return Off(self.pre_rt + 8 * idx + 4, Frame.RUNTIME_RT, 'wiiu', 'entry slot')

    def entry_slot_handle(self, idx):
        return runtime_to_console_handle(self.entry_slot_rt(idx))

    def produced_layout(self):
        """ProducedLayout of THIS zone (for stream<->container conversions on our own output)."""
        return ProducedLayout(self.assets_off - CONTAINER_RT_BASE, self.asset_count)

    # ---------------------------------------------------------------- asset array
    def asset_types(self):
        """Console type id of each entry (STRIDE 8: {u32 type; u32 headerPtr})."""
        return [c for (c, pc, nm) in self.assets]

    def asset_type_names(self):
        return [nm for (c, pc, nm) in self.assets]

    def header_ptrs(self):
        return [read_u32_be(self.z, self.assets_off + 8 * i + 4) for i in range(self.asset_count)]

    # ---------------------------------------------------------------- word census
    def find_word(self, word, limit=None):
        """Every file offset holding `word` as a BIG-ENDIAN u32, at BYTE granularity (console
        records are not 4-aligned; a 4-aligned scan is blind to ~77% of real sites)."""
        pat = pack_u32_be(word)
        out, i, z = [], 0, self.z
        while True:
            i = z.find(pat, i)
            if i < 0:
                break
            out.append(i)
            i += 1
            if limit and len(out) >= limit:
                break
        return out

    def arrend_alias_sites(self, limit=None):
        return self.find_word(self.arrend_alias_word, limit)

    def first_b5_string(self, window=512, minlen=3):
        """v1 heuristic kept for rung0_one.py: the first printable run after assets_end and
        its distance (== root size of asset #0's type, measured constant per type).

        ⛔ NOT A NAME RESOLVER. A printable-run scan returns whichever string sits first in
        the pool -- on a span head that is a POOLED NEIGHBOUR as often as the asset's own name
        (two wrong names, both plausible). Name resolution goes through resolve_name_word()
        / NameAliasBase, which refuse instead of guessing."""
        z, o = self.z, self.assets_end
        lim = min(len(z), o + window)
        i = o
        while i < lim:
            b = z[i]
            if 0x20 <= b < 0x7F:
                j = i
                while j < lim and 0x20 <= z[j] < 0x7F:
                    j += 1
                if j - i >= minlen and j < len(z) and z[j] == 0:
                    return z[i:j], i - o
                i = j + 1
            else:
                i += 1
        return None, None

    def summary(self):
        return dict(
            name=self.name, bytes=self.n, hdr_size=self.size, external=self.external_size,
            blocks={str(i): self.block_sizes[i] for i in self.blocks_used},
            blocks_used=self.blocks_used, nblocks=len(self.blocks_used),
            string_count=self.string_count, char_bytes=self.char_bytes,
            string_ptrs=self.string_ptr_kinds,
            depend_count=self.depend_count, depend_bytes=self.depend_bytes,
            depend_layout=self.depend_layout,
            depends=[d.decode('latin-1') for d in self.depends],
            asset_count=self.asset_count, assets_off=self.assets_off, assets_end=self.assets_end,
            pad=self.pad, K_pred=self.K_pred, pre_rt=self.pre_rt, arrend_rt=self.arrend_rt,
            arrend_alias_word='0x%08X' % self.arrend_alias_word,
            b5=self.b5, warn=self.warn, platform=self.platform, endian=self.endian,
            owner='wiiu_ref/wiiu_zone.py:ZoneReader',
        )


class PCFacts(object):
    """Container facts for one PC (little-endian) zone, READ from pc_zone.PCZoneReader.

    ⛔ REFUSES (PCDependsUnsupported) when dependCount > 0: pc_zone.py:44 never consumes the
    depends region, so its assets_off would be short by that region. That defect is documented
    here and owned by the mirage lane's post-fence item; the substrate hands back no number it
    knows to be wrong. All PC zones on disk 2026-08-17 have DC = 0 (measured), so nothing on
    the current path is blocked -- the refusal only arms when the defect would bite."""
    platform = 'pc'
    endian = 'le'

    def __init__(self, data, name=None):
        self.z = data
        self.name = name
        self.n = len(data)
        if self.n < 0x40:
            raise ContainerTruncated('PC zone is %d B, too short for a container header' % self.n)
        PZ = _pc_zone()
        r = PZ.PCZoneReader(data)
        self._reader = r
        self.words = LittleEndianWords(data)
        self.size, self.external_size = r.size, r.external_size
        self.block_sizes = list(r.block_sizes)
        # PCZoneReader stores neither dependCount nor the ptr words; read them at the owner's
        # documented offsets so the refusal below can fire BEFORE a wrong assets_off exists.
        self.strings_p = read_u32_le(data, 0x2C)
        self.depend_count = read_u32_le(data, 0x30)
        self.depends_p = read_u32_le(data, 0x34)
        self.assets_p = read_u32_le(data, 0x3C)
        if self.depend_count:
            raise PCDependsUnsupported(
                '%s: PC dependCount=%d (depends*=0x%08X). pc_zone.PCZoneReader (pc_zone.py:44) '
                'does not consume the depends region, so its assets_off would be short by it. '
                'REFUSED rather than returned wrong -- latent owner defect, documented '
                '(mirage lane post-fence item), not fixed by the substrate.'
                % (name or 'pc zone', self.depend_count, self.depends_p))
        self.warn = []
        try:
            r.read_string_table()
            r.read_asset_list()
        except (struct.error, IndexError, ValueError) as ex:
            raise ContainerTruncated('%s: PC container structure runs past EOF (%d B): %s'
                                     % (name or 'pc zone', self.n, ex))
        self.string_count = r.string_count
        self.asset_count = r.asset_count
        self.assets_off = r.assets_off
        self.assets_end = r.assets_end
        self.assets = list(r.assets)           # (pc_id, pc_name, headerPtr)
        if self.assets_end > self.n:
            raise ContainerTruncated('PC asset array ends at 0x%x, past EOF 0x%x'
                                     % (self.assets_end, self.n))
        if self.size != self.n - 40:
            self.warn.append('header size 0x%x != actual-40 0x%x' % (self.size, self.n - 40))
        S = self.string_count
        ptrs = read_words_le(data, CONTAINER_RT_BASE, S)
        self.string_ptr_kinds = {'FOLLOW': 0, 'NULL': 0, 'OTHER': 0}
        for p in ptrs:
            self.string_ptr_kinds['FOLLOW' if p == FOLLOW else 'NULL' if p == 0 else 'OTHER'] += 1
        self.strings = [s.encode('latin-1') for s in r.strings[1:]]
        self.char_bytes = sum(len(s) + 1 for s in self.strings)
        self.depend_bytes = 0
        self.depends = []
        self.depend_layout = 'n/a'
        raw = self.assets_off - CONTAINER_RT_BASE
        self.pad = (-self.assets_off) % 4
        self.pre_rt = a4(raw)                  # PC array allocates 4-aligned too (et7 law)
        self.arrend_rt = self.pre_rt + 8 * self.asset_count
        self.b5 = self.block_sizes[BLOCK_VIRTUAL]
        self.blocks_used = [i for i, b in enumerate(self.block_sizes) if b]

    def array_base(self):
        raw = self.assets_off - CONTAINER_RT_BASE
        return ArrayBase(
            file=Off(self.assets_off, Frame.FILE, 'pc', 'owner:pc_zone.PCZoneReader'),
            container_raw=Off(raw, Frame.CONTAINER_B5, 'pc', 'PC stream'),
            phase4=raw % 4, phase8=raw % 8,
            runtime_align4=Off(a4(raw), Frame.RUNTIME_RT, 'pc',
                               'et7 law: PC array allocates 4-aligned (Omap.reloc_asset_entry)'),
            align8_refuted=a8(raw),
            end_runtime=Off(a4(raw) + 8 * self.asset_count, Frame.RUNTIME_RT, 'pc',
                            'align4 base + 8*n'),
            n=self.asset_count)

    def entry_slot_rt(self, idx):
        if not (0 <= idx < self.asset_count):
            raise SubstrateRefusal('entry %d out of range (assetCount %d)' % (idx, self.asset_count))
        return Off(self.pre_rt + 8 * idx + 4, Frame.RUNTIME_RT, 'pc', 'entry slot')

    def entry_of_payload(self, payload):
        """Which PC XAsset entry a PC_ALIAS_PAYLOAD names (the et7 decode; align4 base).
        Returns (idx, phase). Refuses (typed) unless it lands on a headerPtr slot (phase 4)."""
        p = _want(payload, Frame.PC_ALIAS_PAYLOAD, 'entry_of_payload').v
        a0 = self.pre_rt
        if not (a0 <= p < a0 + 8 * self.asset_count):
            raise SubstrateRefusal('payload %d is outside the PC XAsset array [%d, %d)'
                                   % (p, a0, a0 + 8 * self.asset_count))
        ph = (p - a0) % 8
        if ph != 4:
            raise SubstrateRefusal('payload %d lands at phase %d of entry %d (headerPtr is phase 4)'
                                   % (p, ph, (p - a0) // 8))
        return (p - a0) // 8, ph

    def asset_types(self):
        return [t for (t, nm, hp) in self.assets]

    def asset_type_names(self):
        return [nm for (t, nm, hp) in self.assets]

    def header_ptrs(self):
        return [hp for (t, nm, hp) in self.assets]

    def summary(self):
        return dict(
            name=self.name, bytes=self.n, hdr_size=self.size, external=self.external_size,
            blocks={str(i): self.block_sizes[i] for i in self.blocks_used},
            string_count=self.string_count, char_bytes=self.char_bytes,
            string_ptrs=self.string_ptr_kinds, depend_count=self.depend_count,
            asset_count=self.asset_count, assets_off=self.assets_off, assets_end=self.assets_end,
            pad=self.pad, pre_rt=self.pre_rt, arrend_rt=self.arrend_rt, b5=self.b5,
            warn=self.warn, platform=self.platform, endian=self.endian,
            owner='native_linker/pc_zone.py:PCZoneReader',
        )


def facts_for(data, platform, name=None):
    """The Facts object for a platform: 'wiiu' -> Facts (BE), 'pc' -> PCFacts (LE)."""
    if platform == 'wiiu':
        return Facts(data, name)
    if platform == 'pc':
        return PCFacts(data, name)
    raise SubstrateRefusal('unknown platform %r' % (platform,))


def sniff_platform(data):
    """Which endianness makes the header self-consistent (size == len-40)? Returns 'wiiu',
    'pc', or REFUSES when both or neither do -- never guesses. (A wrong-endian read of the
    header is the same defect as a wrong-endian read of a word; the size word is the
    absolute-count check that exposes it.)"""
    if len(data) < 0x40:
        raise ContainerTruncated('zone is %d B, too short to sniff' % len(data))
    n40 = len(data) - 40
    be = read_u32_be(data, 0) == n40
    le = read_u32_le(data, 0) == n40
    if be and not le:
        return 'wiiu'
    if le and not be:
        return 'pc'
    raise SubstrateRefusal('cannot sniff platform: header size BE=0x%08X LE=0x%08X vs len-40=0x%X'
                           % (read_u32_be(data, 0), read_u32_le(data, 0), n40))


# ======================================================================================
# PASS STRUCTURE -- delegating to Omap.branch_histogram(); the FINAL pass, NAMED
# ======================================================================================
PassStructure = namedtuple('PassStructure', 'pass_index passes_run per_pass_unres hist')
PassStructure.__doc__ = """From Omap.branch_histogram(): `pass_index` (0-based, the pass `hist`
came from = the LAST run = the one the fatal bar judged), `passes_run`, `per_pass_unres`
(the RESETS made visible), `hist` (final-pass counters). A consumer prints pass_index beside
every number it quotes ((HB)/(HE))."""


def pass_structure(omap):
    """The omap's own pass structure. Refuses (PassUnnamed) on anything that cannot name its
    pass -- an object without branch_histogram(), or a histogram whose per-pass list does not
    end at the pass it claims (the spliced-histogram shape build_provenance guards)."""
    bh = getattr(omap, 'branch_histogram', None)
    if bh is None:
        raise PassUnnamed('%r has no branch_histogram(): pass-dependent numbers cannot be '
                          'named. Read the omap AFTER assemble_zone via '
                          'produce_nobackbone.Omap.branch_histogram(); never omap.stats.'
                          % (omap,))
    h = bh()
    for k in ('pass', 'passes_run', 'per_pass_unres', 'hist'):
        if k not in h:
            raise PassUnnamed('branch_histogram() lacks %r: %r' % (k, h))
    p, runs, per, hist = h['pass'], h['passes_run'], list(h['per_pass_unres']), dict(h['hist'])
    if len(per) != runs or p != runs - 1:
        raise PassUnnamed('histogram claims pass %d of %d but per_pass_unres has %d entries'
                          % (p, runs, len(per)))
    if hist.get('unresolved', 0) != per[p]:
        raise PassUnnamed('SPLICED HISTOGRAM: hist[unresolved]=%r but per_pass_unres[%d]=%r'
                          % (hist.get('unresolved'), p, per[p]))
    return PassStructure(p, runs, per, hist)


class PassCensus(object):
    """Result of `pass_census()`: per-pass sets of pc_b5 registered by Omap.add()."""

    def __init__(self):
        self.passes = []           # list of sets, one per pass, in order
        self._cur = set()
        self.calls = 0

    def final(self):
        """The set the bar judged -- the LAST pass. Refuses if no pass was seen."""
        if not self.passes:
            raise EmptyPopulation('pass_census saw no pass at all (Omap.add never fired and '
                                  'begin_pass never ran): a zero population validates nothing')
        return self.passes[-1]

    def per_pass_counts(self):
        return [len(p) for p in self.passes]

    def reconcile(self, omap):
        """POSITIVE CONTROL: the number of passes this census saw must equal the omap's own
        passes_run (Track-F's 3x over-recording and mirage's cross-pass census both fail here)."""
        ps = pass_structure(omap)
        if len(self.passes) != ps.passes_run:
            raise PassUnnamed('pass_census saw %d pass(es) %r but the omap ran %d -- the census '
                              'is not aligned to the pass structure it claims to score'
                              % (len(self.passes), self.per_pass_counts(), ps.passes_run))
        return ps


@contextmanager
def pass_census(PN):
    """Install a per-pass census on `PN.Omap` (PN = the imported produce_nobackbone module)
    for the duration of a build/census run; restores the originals afterwards.

        with zone_facts.pass_census(PN) as pc:
            ... drive the emit ...
        final = pc.final(); pc.reconcile(omap)

    The census keys by pass BECAUSE the bar judges the LAST pass: region_registration_gate v1
    accumulated across passes and read 3 where the final pass = 21."""
    Omap = PN.Omap
    orig_add, orig_begin = Omap.add, Omap.begin_pass
    pc = PassCensus()

    def add(self, pc_b5, pc_len, co_b5, exact):
        pc._cur.add(pc_b5)
        pc.calls += 1
        return orig_add(self, pc_b5, pc_len, co_b5, exact)

    def begin_pass(self):
        pc.passes.append(set(pc._cur))
        pc._cur.clear()
        return orig_begin(self)

    Omap.add, Omap.begin_pass = add, begin_pass
    try:
        yield pc
    finally:
        Omap.add, Omap.begin_pass = orig_add, orig_begin
        pc.passes.append(set(pc._cur))          # the final pass, still live at exit
        pc._cur = set()


# ======================================================================================
# NAME RESOLUTION -- PC name aliases via the per-zone EMPIRICAL base (uniqueness refusal)
# ======================================================================================
NAME_SOLVE_MAX_K = 300000
NAME_SOLVE_MIN_SCORE = 0.75


def _is_name_bytes(raw):
    return len(raw) >= 2 and all(32 <= c < 127 for c in raw)


class NameAliasBase(object):
    """The per-zone empirical base K for PC NAME aliases: `file = payload + K`.

    WHY EMPIRICAL: the console block-5 rule (`payload + 64`) lands 2 B inside the string on PC
    (measured: no delta in -4..+4 beats 3/13 on mirage) and returns a plausible TAIL -- the
    wrong asset, one index off, and TRUNCATION IS INVISIBLE TO SANITY CHECKS. So the base is
    SOLVED per zone: the K under which the most aliased name slots land exactly at a string
    START (previous byte NUL, first byte printable), and it must be UNIQUE above the score
    floor or the solve REFUSES (NotUniquelySolved). Validated 505/506 vs OAT across the fleet
    (K = raid 101, skate 98, nuked 82, transit 102, nuketown 97, mirage 100, downhill 101,
    highrise 100 -- all unique, scores 0.91-1.00; FINDINGS_mirage_last_two.md §6).

    Population is stated on the object: n (aliased slots), score, n_winners."""

    def __init__(self, K, score, n, n_winners, max_k, min_score):
        self.K, self.score, self.n, self.n_winners = K, score, n, n_winners
        self.max_k, self.min_score = max_k, min_score

    def __repr__(self):
        return 'NameAliasBase(K=%d score=%.3f n=%d)' % (self.K, self.score, self.n)

    @staticmethod
    def _votes(pcb, payloads, max_k):
        """votes[K] = number of payloads whose payload+K is a string start."""
        L = len(pcb)
        try:
            import numpy as np
            b = np.frombuffer(pcb, dtype=np.uint8)
            printable = (b >= 32) & (b < 127)
            prev_nul = np.zeros(L, dtype=bool)
            prev_nul[1:] = (b[:-1] == 0)
            is_start = (printable & prev_nul).astype(np.int32)
            votes = np.zeros(max_k, dtype=np.int32)
            for p in payloads:
                lo, hi = p, min(p + max_k, L)
                if lo >= L:
                    continue
                votes[:hi - lo] += is_start[lo:hi]
            return votes.tolist()
        except ImportError:
            votes = [0] * max_k
            for p in payloads:
                for K in range(max_k):
                    o = p + K
                    if o >= L:
                        break
                    if o > 0 and pcb[o - 1] == 0 and 32 <= pcb[o] < 127:
                        votes[K] += 1
            return votes

    @classmethod
    def solve(cls, pcb, payloads, max_k=NAME_SOLVE_MAX_K, min_score=NAME_SOLVE_MIN_SCORE):
        """payloads: PC_ALIAS_PAYLOAD values (Off or int) of the aliased NAME slots. Refuses on
        an empty population and on a non-unique solve."""
        pays = [p.v if isinstance(p, Off) else int(p) for p in payloads]
        for p in payloads:
            if isinstance(p, Off) and p.frame != Frame.PC_ALIAS_PAYLOAD:
                raise FrameMismatch('NameAliasBase.solve wants PC_ALIAS_PAYLOAD offsets, got %s' % p)
        n = len(pays)
        if n == 0:
            raise EmptyPopulation('NameAliasBase.solve over ZERO aliased name slots: nothing to '
                                  'solve, and a K "solved" from nothing would be a guess')
        votes = cls._votes(bytes(pcb), pays, max_k)
        need = min_score * n
        winners = [(c, K) for K, c in enumerate(votes) if c >= need]
        best = max(votes) / float(n) if votes else 0.0
        if len(winners) != 1:
            raise NotUniquelySolved(
                'name-alias base NOT UNIQUE: %d candidate K(s) score >= %.2f over %d aliased '
                'slots (best %.3f%s). UNRESOLVABLE = UNKNOWN; refusing to pick.'
                % (len(winners), min_score, n, best,
                   ', e.g. K in %r' % [K for _, K in winners[:6]] if winners else ''))
        c, K = winners[0]
        return cls(K, c / float(n), n, 1, max_k, min_score)

    def file_of_payload(self, payload):
        """PC_ALIAS_PAYLOAD -> PC FILE offset under the solved K."""
        p = payload.v if isinstance(payload, Off) else int(payload)
        if isinstance(payload, Off) and payload.frame != Frame.PC_ALIAS_PAYLOAD:
            raise FrameMismatch('file_of_payload wants a PC_ALIAS_PAYLOAD, got %s' % payload)
        return Off(p + self.K, Frame.FILE, 'pc', 'name-alias K=%d' % self.K)

    def resolve(self, pcb, word):
        """A NAME slot's LE word -> (name:str, how). Refuses (NameUnresolved) unless the decode
        lands on a string START that looks like a name; refuses on a raw/non-handle word."""
        if not (BLOCK5_LO <= word <= BLOCK5_HI):
            raise NameUnresolved('word 0x%08X is not a block-5 handle' % word)
        fo = self.file_of_payload(pc_handle_payload(word)).v
        if not (0 < fo < len(pcb)) or pcb[fo - 1] != 0:
            raise NameUnresolved('alias 0x%08X -> file %d under K=%d is not a string start'
                                 % (word, fo, self.K))
        raw = read_cstr(pcb, fo, 256)
        if not _is_name_bytes(raw):
            raise NameUnresolved('alias 0x%08X -> file %d yields a non-name %r (different '
                                 'pointer class; e.g. the 0xA4-tagged mirage outlier)'
                                 % (word, fo, raw[:16]))
        return raw.decode('latin-1'), 'alias@0x%08x K=%d' % (word, self.K)


def resolve_name_word(pcb, slot_file_off, root_size, base=None):
    """Resolve the name at a PC asset's NAME slot (LE word at slot_file_off).
      FOLLOW  -> inline string right after the root (slot owner's root_size)
      alias   -> through `base` (NameAliasBase); refuses if base is None
    Returns (name, how). Every failure is a typed NameUnresolved -- never None."""
    v = read_u32_le(pcb, slot_file_off)
    if v == FOLLOW:
        o = slot_file_off + root_size
        raw = read_cstr(pcb, o, 512)
        if not _is_name_bytes(raw):
            raise NameUnresolved('FOLLOW name at file %d (slot %d + root %d) is not a name: %r'
                                 % (o, slot_file_off, root_size, raw[:16]))
        return raw.decode('latin-1'), 'FOLLOW(inline)'
    if BLOCK5_LO <= v <= BLOCK5_HI:
        if base is None:
            raise NameUnresolved('name slot %d holds alias 0x%08X but no NameAliasBase was '
                                 'solved for this zone' % (slot_file_off, v))
        return base.resolve(pcb, v)
    if v == 0:
        raise NameUnresolved('name slot %d is NULL' % slot_file_off)
    raise NameUnresolved('name slot %d holds raw 0x%08X (neither FOLLOW nor a handle)'
                         % (slot_file_off, v))


def name_word_offsets(console):
    """root struct -> offset of its name word, from the layout OWNER (wiiu_ref/struct_layout).
    Mirrors zone_gates._name_offsets without its private overrides; refuses if the owner is
    missing rather than answering from a hand table."""
    if _WIIU_REF not in sys.path:
        sys.path.insert(0, _WIIU_REF)
    try:
        import struct_layout
        import walker as W
    except ImportError as ex:
        raise SubstrateDependencyMissing('name_word_offsets delegates to wiiu_ref/struct_layout.py '
                                         '+ walker.py: %s' % ex)
    L = struct_layout.Layout(W.HDR, console=console)
    out = {}
    for root in set(W.ASSET_ROOT.values()):
        try:
            st = L.get(root)
        except Exception:
            continue
        if not st or not st.get('fields'):
            continue
        for f in st['fields']:
            if f.get('name') in ('name', 'szInternalName', 'fontName') and f.get('offset') is not None:
                out[root] = f['offset']
                break
    return out


NameCensus = namedtuple('NameCensus', 'base names unresolved n_slots n_follow n_alias n_other')
NameCensus.__doc__ = """resolve_pc_names(): base (NameAliasBase or None), names {idx: (name, how)},
unresolved {idx: reason}, and the ABSOLUTE counts (n_slots / n_follow / n_alias / n_other) so
the resolution RATE is legible beside every per-item answer (instrument law 1)."""


def resolve_pc_names(pcb, name_slots, root_sizes, max_k=NAME_SOLVE_MAX_K,
                     min_score=NAME_SOLVE_MIN_SCORE):
    """Population-first name resolution over a PC zone.
      name_slots: {idx: slot_file_off}   (from a body walk; e.g. produce_nobackbone.walk_pc_bodies
                                          + name_word_offsets(False): slot = start + name_off)
      root_sizes: {idx: root_size}       (inline FOLLOW names sit at slot + root_size)
    Solves K over ALL aliased slots first (uniqueness refusal), then resolves each slot. Never
    returns a per-item guess: every miss is in `unresolved` with its reason."""
    if not name_slots:
        raise EmptyPopulation('resolve_pc_names over zero name slots')
    words = {i: read_u32_le(pcb, o) for i, o in name_slots.items()}
    alias_idx = [i for i, w in words.items() if BLOCK5_LO <= w <= BLOCK5_HI]
    n_follow = sum(1 for w in words.values() if w == FOLLOW)
    n_other = len(words) - n_follow - len(alias_idx)
    base = None
    unresolved = {}
    if alias_idx:
        try:
            base = NameAliasBase.solve(pcb, [pc_handle_payload(words[i]) for i in alias_idx],
                                       max_k=max_k, min_score=min_score)
        except NotUniquelySolved as ex:
            for i in alias_idx:
                unresolved[i] = 'base not solved: %s' % ex
    names = {}
    for i, o in name_slots.items():
        if i in unresolved:
            continue
        try:
            names[i] = resolve_name_word(pcb, o, root_sizes.get(i, 0), base)
        except NameUnresolved as ex:
            unresolved[i] = str(ex)
    return NameCensus(base, names, unresolved, len(name_slots), n_follow, len(alias_idx), n_other)


# ======================================================================================
# .ff loading (unchanged helper)
# ======================================================================================
def load_ff(path):
    """Decrypt/decompress a .ff, or pass a .zone through. Returns raw zone bytes."""
    raw = open(path, 'rb').read()
    if path.lower().endswith('.zone'):
        return raw
    sys.path.insert(0, os.path.join(_HERE, '..', 'WiiU_FF_Studio'))
    try:
        import wiiu_ff
    except ImportError as ex:
        raise SubstrateDependencyMissing('load_ff needs WiiU_FF_Studio/wiiu_ff.py to decrypt a '
                                         '.ff: %s' % ex)
    return wiiu_ff.decrypt(raw)[1]


# ======================================================================================
# SELF-TEST -- fixture-first, both verdicts observed for every refusal path
# ======================================================================================
def _synth_container(be, S_strings, depends, n_assets, pad_len=0):
    """A tiny synthetic container: header + xlist + string table (+ batched depends) + array +
    a few body bytes. `pad_len` extra chars in the last string steers the array phase."""
    e = '>' if be else '<'
    strs = [s.encode('latin-1') for s in S_strings]
    if strs and pad_len:
        strs[-1] = strs[-1] + b'x' * pad_len
    body = b''
    ptrs = b''.join(struct.pack(e + 'I', FOLLOW) for _ in strs)
    chars = b''.join(s + b'\x00' for s in strs)
    dep = b''
    if depends:
        dep = b''.join(struct.pack(e + 'I', FOLLOW) for _ in depends) + \
              b''.join(d.encode('latin-1') + b'\x00' for d in depends)
    arr = b''.join(struct.pack(e + 'II', 5, FOLLOW) for _ in range(n_assets))
    tail = b'\x00' * 32
    content = ptrs + chars + dep + arr + tail
    xlist = struct.pack(e + '6I', len(strs), FOLLOW if strs else 0, len(depends),
                        FOLLOW if depends else 0, n_assets, FOLLOW)
    payload = xlist + content
    hdr = struct.pack(e + 'II', len(payload), 0) + struct.pack(e + '8I', 0, 0, 0, 0, 0,
                                                                len(content), 0, 0)
    z = hdr + payload
    assert len(z) - 40 == len(payload)
    return z, 64 + len(ptrs) + len(chars) + len(dep)


def _naive_assets_off_no_depends_align4(z, be):
    """THE RETIRED stage1 derivation (depends unconsumed + spurious align4) -- the negative
    control. Kept INSIDE the selftest so the substrate is SEEN TO DIFFER from it."""
    e = '>' if be else '<'
    S = struct.unpack_from(e + 'I', z, 40)[0]
    o = 64
    ptrs = struct.unpack_from(e + '%dI' % S, z, o) if S else ()
    o += 4 * S
    for p in ptrs:
        if p == FOLLOW:
            # substrate: re-derivation-by-design (selftest NEGATIVE CONTROL: the retired stage1 shape)
            o = z.index(b'\x00', o) + 1
    return (o + 3) & ~3


def selftest(verbose=True):
    ok = fail = 0

    def check(cond, msg):
        nonlocal ok, fail
        if cond:
            ok += 1
            if verbose:
                print('   PASS %s' % msg)
        else:
            fail += 1
            print('   FAIL %s' % msg)

    print('zone_facts selftest')
    # ---- 1. container facts: console, DC=2, odd phase --------------------------------------
    z, want_off = _synth_container(True, ['alpha', 'be', 'gamma'], ['dep_one', 'dep_two'], 3, pad_len=2)
    f = Facts(z, 'synth-be')
    check(f.assets_off == want_off, 'console assets_off %d == synthetic truth %d (owner)' % (f.assets_off, want_off))
    naive = _naive_assets_off_no_depends_align4(z, True)
    check(naive != f.assets_off, 'retired derivation (no depends + align4) gives %d != owner %d -- SEEN TO DIFFER' % (naive, f.assets_off))
    check(f.depend_count == 2 and f.depend_layout.startswith('batched') or f.depend_layout.startswith('both'),
          'depends classified as %r over the owner-bounded region' % f.depend_layout)
    check(f.depends == [b'dep_one', b'dep_two'], 'depends names via owner: %r' % f.depends)
    ab = f.array_base()
    check(ab.container_raw.v == f.assets_off - 64 and ab.runtime_align4.v == a4(f.assets_off - 64),
          'array_base: raw %d phase4 %d align4 %d' % (ab.container_raw.v, ab.phase4, ab.runtime_align4.v))
    check(ab.end_runtime.v == f.arrend_rt, 'end_runtime == arrend_rt (%d)' % f.arrend_rt)
    check(f.entry_slot_rt(1).v == f.pre_rt + 12, 'entry_slot_rt(1) = base+12')
    check(f.asset_types() == [5, 5, 5], 'asset_types stride-8: %r' % f.asset_types())
    # legacy names still there
    for attr in ('pad', 'K_pred', 'pre_rt', 'arrend_alias_word', 'b5', 'blocks_used', 'char_bytes',
                 'depend_bytes', 'string_ptr_kinds', 'warn', 'strings'):
        check(hasattr(f, attr), 'legacy attribute .%s present' % attr)
    check(f.summary()['assets_off'] == want_off, 'summary() serialises')

    # ---- 2. PC facts + the depends refusal ---------------------------------------------------
    zp, want_pc = _synth_container(False, ['pc_a', 'pc_bb'], [], 2, pad_len=1)
    fp = PCFacts(zp, 'synth-le')
    check(fp.assets_off == want_pc, 'PC assets_off %d == synthetic truth %d (owner)' % (fp.assets_off, want_pc))
    check(sniff_platform(zp) == 'pc' and sniff_platform(z) == 'wiiu', 'sniff_platform BE/LE by header self-consistency')
    zpd, _ = _synth_container(False, ['pc_a'], ['dep'], 1)
    try:
        PCFacts(zpd, 'synth-le-dc1')
        check(False, 'PC DC=1 must REFUSE (pc_zone.py:44 latent defect)')
    except PCDependsUnsupported as ex:
        check(True, 'PC DC=1 REFUSED: %s' % str(ex)[:60])
    # wrong-endian read of the LE zone through the BE reader must NOT parse silently
    try:
        Facts(zp, 'le-through-be')
        wrong = True
    except SubstrateRefusal:
        wrong = False
    check(not wrong, 'LE zone through the console Facts REFUSES (typed), not a plausible parse')
    check(not hasattr(fp.words, 'u32_be') and hasattr(fp.words, 'u32_le'), 'PC Facts exposes only *_le readers')
    check(not hasattr(f.words, 'u32_le') and hasattr(f.words, 'u32_be'), 'console Facts exposes only *_be readers')

    # ---- 3. frames refuse cross-frame comparison ------------------------------------------
    a = container_b5(16028)
    b = stream_b5(16028)
    try:
        a == b
        check(False, 'CONTAINER_B5 == STREAM_B5 must raise')
    except FrameMismatch:
        check(True, 'CONTAINER_B5 == STREAM_B5 raises FrameMismatch (the mirage guard, as a type)')
    try:
        a - 39654
        a - b
        check(False, 'CONTAINER_B5 - STREAM_B5 must raise')
    except FrameMismatch:
        check(True, 'cross-frame subtraction raises')
    try:
        a == 16028
        check(False, 'Off == bare int must raise')
    except FrameMismatch:
        check(True, 'Off == bare int raises (unlabelled number)')
    check((a + 4).frame == Frame.CONTAINER_B5 and (a + 4).v == 16032, 'Off + int stays in frame')
    check(container_to_file(file_to_container(file_off(100))).v == 100, 'file<->container round trip')
    lay = ProducedLayout(16028, 954)
    c = stream_to_container(stream_b5(0), lay)
    check(c.v == 16028 + 954 * 8, 'stream 0 -> container %d (prefix + 8*narr)' % c.v)
    check(container_to_stream(c, lay).v == 0, 'container -> stream inverse')

    class _RT(object):
        def rt(self, s):
            return s + 1000

    try:
        runtime_of_stream(_RT(), container_b5(16028))
        check(False, 'runtime_of_stream(container_b5) must refuse')
    except FrameMismatch:
        check(True, 'runtime_of_stream REFUSES a container-frame input ((HF): the 39,654 vs 16,028 guard)')
    check(runtime_of_stream(_RT(), stream_b5(28)).v == 1028, 'runtime_of_stream accepts STREAM_B5')
    try:
        runtime_of_stream(_RT(), 28)
        check(False, 'bare int must refuse')
    except FrameMismatch:
        check(True, 'runtime_of_stream REFUSES a bare int')
    h = console_handle_to_runtime(0xA025D541)
    check(h.v == 0x025D540 and h.frame == Frame.RUNTIME_RT, 'console handle -> runtime rt %d' % h.v)
    p = pc_handle_payload(0xA3C8D2B9)
    check(p.frame == Frame.PC_ALIAS_PAYLOAD, 'PC handle -> PC_ALIAS_PAYLOAD frame (not file, not rt)')
    try:
        file_to_container(p)
        check(False, 'PC payload must not convert as if it were a file offset')
    except FrameMismatch:
        check(True, 'PC payload refuses file arithmetic (the console-b5-rule-on-PC law)')
    try:
        console_handle_to_runtime(0x3F800000)
        check(False, 'float phantom must refuse')
    except NotABlock5Handle:
        check(True, '0x3F800000 (float phantom) refused as a handle')

    # ---- 4. runtime placement via a fake Coord: registrant disclosure ---------------------
    class _Coord(object):
        def __init__(self):
            self.omap = {0: 0, 100: 128, 200: 256, 250: 256, 301: 360}
            self._rt = {}
            for k in sorted(self.omap):
                self._rt[self.omap[k]] = k
            self._rts = sorted(self._rt)

        def rt_of_file(self, fo, align=4):
            f5 = fo - 64
            if f5 in self.omap:
                return self.omap[f5], 'exact'
            import bisect
            j = bisect.bisect_right(sorted(self.omap), f5) - 1
            ks = sorted(self.omap)
            return self.omap[ks[j]] + (f5 - ks[j]), 'interp+%d' % (f5 - ks[j])

        def file_of_rt(self, rt):
            if rt in self._rt:
                return self._rt[rt] + 64
            import bisect
            j = bisect.bisect_right(self._rts, rt) - 1
            if j < 0:
                return None
            r0 = self._rts[j]
            return self._rt[r0] + (rt - r0) + 64

    C = _Coord()
    r = runtime_of_file(C, file_off(164))
    check(r.v == 128 and r.how == 'exact', 'runtime_of_file exact 164 -> 128')
    pl = file_of_runtime(C, runtime_rt(256))
    check(pl.registrants == 2, 'file_of_runtime DISCLOSES 2 registrants at rt 256 (ambiguous, (GL) last-wins)')
    pl1 = file_of_runtime(C, runtime_rt(128))
    check(pl1.registrants == 1 and pl1.off.v == 164, 'file_of_runtime 1 registrant -> file 164')
    pl0 = file_of_runtime(C, runtime_rt(140))
    check(pl0.registrants == 0 and pl0.how == 'interp', 'file_of_runtime 0 registrants -> labelled interp')
    try:
        runtime_of_file(C, runtime_rt(128))
        check(False, 'runtime_of_file(runtime_rt) must refuse')
    except FrameMismatch:
        check(True, 'runtime_of_file refuses a RUNTIME_RT input')

    # ---- 5. pass structure --------------------------------------------------------------
    class _Om(object):
        def __init__(self, per, hist):
            self._per, self._hist = per, hist

        def branch_histogram(self):
            return {'pass': len(self._per) - 1, 'passes_run': len(self._per),
                    'per_pass_unres': list(self._per), 'hist': dict(self._hist)}

    ps = pass_structure(_Om([44115, 27394, 27394], {'unresolved': 27394, 'start': 5}))
    check(ps.pass_index == 2 and ps.passes_run == 3, 'pass_structure names pass %d of %d' % (ps.pass_index, ps.passes_run))
    try:
        pass_structure(_Om([44115, 27394, 27394], {'unresolved': 44115}))
        check(False, 'spliced histogram must refuse')
    except PassUnnamed:
        check(True, 'SPLICED histogram (pass-0 count with pass-2 label) refused')
    try:
        pass_structure(object())
        check(False, 'object without branch_histogram must refuse')
    except PassUnnamed:
        check(True, 'no branch_histogram() -> PassUnnamed')

    class _PN(object):
        class Omap(object):
            def __init__(self):
                self.regions = []
                self._pass_unres = []
                self.stats = {'unresolved': 0}

            def add(self, pc_b5, pc_len, co_b5, exact):
                self.regions.append(pc_b5)

            def begin_pass(self):
                self._pass_unres.append(self.stats.get('unresolved', 0))
                self.stats = {'unresolved': 0}

            def branch_histogram(self):
                per = list(self._pass_unres) + [self.stats.get('unresolved', 0)]
                return {'pass': len(per) - 1, 'passes_run': len(per), 'per_pass_unres': per,
                        'hist': dict(self.stats)}

    with pass_census(_PN) as pc:
        om = _PN.Omap()
        for i in range(3):
            om.add(i, 1, 0, True)               # pass 0: 3 adds
        om.begin_pass()
        for i in range(3):
            om.add(i, 1, 0, True)               # pass 1: 3 adds
        om.begin_pass()
        for i in range(2):
            om.add(i, 1, 0, True)               # FINAL pass: 2 adds -- one asset stopped emitting
    check(pc.per_pass_counts() == [3, 3, 2], 'pass_census per-pass %r' % pc.per_pass_counts())
    check(len(pc.final()) == 2, 'pass_census.final() scores the LAST pass (2), not the union (3)')
    check(pc.reconcile(om).passes_run == 3, 'pass_census reconciles to the omap\'s own passes_run')
    check(_PN.Omap.add.__name__ == 'add' and 'pc' not in _PN.Omap.add.__code__.co_freevars,
          'pass_census restored the original Omap.add')

    # ---- 6. name-alias base solve: fixture with a known K, uniqueness refusal ------------
    K_true = 100
    names = [b'fxanim_lantern_lrg_mod', b'fxanim_lantern_sm_mod', b'fxanim_lamp_mod',
             b'fxanim_lanterns_ruined_mod', b'fxanim_lanterns_string_mod', b'wall_lamp_a',
             b'wall_lamp_b', b'street_sign_01']
    pool = bytearray(b'\x00' * 4096)
    o = 512
    starts = []
    for nm in names:
        pool[o:o + len(nm)] = nm
        starts.append(o)
        o += len(nm) + 1 + (o % 3)          # ragged spacing so no second K fits
    import random
    rnd = random.Random(7)
    for i in range(0, 500):                 # noise before the pool (500..511 stay NUL so the
        pool[i] = rnd.choice(b'\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f')  # first name is a START)
    payloads = [pc_alias_payload(s - K_true) for s in starts]
    base = NameAliasBase.solve(bytes(pool), payloads, max_k=1200, min_score=0.75)
    check(base.K == K_true and base.n == len(names) and base.score == 1.0,
          'NameAliasBase solved K=%d score %.2f over n=%d' % (base.K, base.score, base.n))
    w = ((starts[1] - K_true) + 1) | 0xA0000000
    nm, how = base.resolve(bytes(pool), w)
    check(nm == 'fxanim_lantern_sm_mod', 'resolve -> %r (%s)' % (nm, how))
    # the console rule (+64) on the same word lands mid-string or off-string: it must NOT be
    # what resolve() returns
    # substrate: re-derivation-by-design (selftest NEGATIVE CONTROL: the console +64 rule on a PC word)
    wrong = bytes(pool)[((w - 1) & 0x1FFFFFFF) + 64:][:8]
    check(wrong != b'fxanim_l', 'console +64 rule on the PC word reads %r, not the name start' % wrong)
    try:
        NameAliasBase.solve(bytes(pool), [], max_k=1200)
        check(False, 'empty population must refuse')
    except EmptyPopulation:
        check(True, 'solve over ZERO slots refuses (EmptyPopulation)')
    # ambiguity: two names spaced so that K and K+d BOTH score full -> refuse
    pool2 = bytearray(b'\x00' * 2048)
    for s in (600, 700, 800):
        pool2[s:s + 3] = b'abc'
        pool2[s + 10:s + 13] = b'xyz'          # every payload+K AND payload+K+10 is a start
    try:
        NameAliasBase.solve(bytes(pool2), [pc_alias_payload(s - 50) for s in (600, 700, 800)], max_k=200)
        check(False, 'ambiguous solve must refuse')
    except NotUniquelySolved as ex:
        check('2 candidate' in str(ex), 'ambiguous K (50 and 60) REFUSED: %s' % str(ex)[:70])
    # resolve_name_word: FOLLOW inline, alias, NULL, raw
    zz = bytearray(b'\x00' * 300)
    zz[0:4] = struct.pack('<I', FOLLOW)
    zz[248:248 + 6] = b'my_mod'
    check(resolve_name_word(bytes(zz), 0, 248)[0] == 'my_mod', 'resolve_name_word FOLLOW inline after root')
    try:
        resolve_name_word(bytes(zz), 8, 248)
        check(False, 'NULL slot must refuse')
    except NameUnresolved:
        check(True, 'NULL name slot -> NameUnresolved (typed, not None)')
    census = resolve_pc_names(bytes(zz), {0: 0, 1: 8}, {0: 248, 1: 248})
    check(census.n_slots == 2 and len(census.names) == 1 and len(census.unresolved) == 1,
          'resolve_pc_names census: %d slots, %d named, %d unresolved (counts stated)'
          % (census.n_slots, len(census.names), len(census.unresolved)))

    print('zone_facts selftest: %d passed, %d failed' % (ok, fail))
    return fail == 0


if __name__ == '__main__':
    import json
    args = sys.argv[1:]
    if not args or args == ['--selftest']:
        sys.exit(0 if selftest() else 1)
    for p in args:
        try:
            data = load_ff(p)
            plat = sniff_platform(data)
            f = facts_for(data, plat, name=p)
            print(json.dumps(f.summary(), indent=1))
        except SubstrateRefusal as e:
            print('%s: %s: %s' % (p, type(e).__name__, e))
            sys.exit(2)
