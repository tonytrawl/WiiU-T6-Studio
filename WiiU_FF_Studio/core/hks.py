r"""core.hks -- native HavokScript (T6 LUI Lua) container model, tiling parser, safe patcher.

WHY THIS SHAPE
--------------
`dlc loading/native/fullrelink/lua_endian.py` walks the format, but only to swap bytes -- it
builds no model, so nothing can inspect or edit a script.

THE FOOTER IS PER-PROTO, NOT PER-FILE  (corrected 2026-08-18)
-------------------------------------------------------------
This module used to DETECT a whole-file "footer size" of 12 or 8 by trying each and keeping
whichever consumed the buffer exactly. That is the wrong shape. It survived because every chunk
measured so far happens to be internally uniform, so a per-file guess lands on the right answer
-- 821 chunks checked (761 console, 60 from the checkout), 0 of them mixed. The per-proto rule
below is therefore justified by the LOADER'S CONDITION, not by a mixed file anyone has seen.

The real layout, after a proto's constants, is:

    i32  debug_flag
    i32  hash            <-- PRESENT IF AND ONLY IF debug_flag == 1
    i32  n_subprotos

So the gap is 12 bytes when the flag is 1 and 8 bytes when it is not, decided SEPARATELY FOR
EVERY PROTO. A file whose protos disagree parses under neither constant, and the old detector's
fallback then kept whichever guess left the smaller remainder -- producing a self-consistent
model of the wrong shape with the deficit parked in `trailing`.

  ⚠ BOTH VALUES OCCUR, AND WHICH ONE YOU SEE DEPENDS ON WHERE THE FILE CAME FROM. Measured:

      E:\Wii U Black ops 2\content\english\patch_{ui_zm,ui_mp,mp,zm}.ff
          761 chunks, 9,160 protos, debug_flag 0 in EVERY ONE  -> 8-byte gap
      chunks this project compiles, and PC-sourced chunks
          debug_flag 1 plus a hash                             -> 12-byte gap

  So a tool calibrated on one source is wrong on the other, which is exactly what happened.
  `ui_mp/t6/zombie/selectstartloczombie.lua` in the retail Wii U patch_ui_zm.ff is 30,513 bytes
  and 34 protos; assuming 12 parses ONE proto and stops 3,534 bytes in -- and re-parsing that
  3,534-byte result then consumes it exactly, so every "the parse consumed the buffer" check
  passes on a chunk that lost 89% of itself.

TILING, NOT "CONSUMED"  -- AND WHAT TILING IS ACTUALLY WORTH
------------------------------------------------------------
"The parse consumed the buffer" is a weaker claim than it sounds: it is satisfied by a mis-sized
proto that happens to land on the right end offset. So the parser records an explicit REGION for
every byte it accounts for.

⛔ But be honest about which half of that does the work. The regions are recorded from the SAME
cursor the parse advances, so a CONTIGUOUS cover is guaranteed for any parse that does not raise
-- it cannot witness an error in the arithmetic that produced it. What can fail is the SIZE
AUDIT: `tiling_report()` recomputes every region's length from the decoded model and compares.
`check()` then adds the two independent tests -- a byte-identical re-emit, and a refusal of any
tail -- and those are what actually catch a wrong walk.

One case stays undecidable from the buffer alone: a walk that stops early AND ends exactly at the
buffer end is indistinguishable from a shorter, valid chunk. Only a comparison against a known
input catches that, which is why every edit goes through `hks_patch.prove_product`.

NEVER BYTE-SPLICE A CHUNK
-------------------------
Instruction arrays are 4-aligned and the alignment padding is NOT stored as a field -- the reader
recomputes it from its own position (hksc: `aligned2instr(S->pos) - S->pos`). So inserting or
removing any number of bytes that is not a multiple of 4 silently changes the padding that every
DOWNSTREAM proto needs, and a splice that keeps the old padding desynchronises the rest of the
file. The game reports this as "LUI: Out of Memory", which names neither the file nor the cause.

`patch_constant` therefore mutates the model and re-emits through `serialize()`, which recomputes
every pad. Raw byte edits raise `SpliceRefused`. This is an enforced check, not advice.
"""
import struct

from . import paths  # noqa: F401

MAGIC = b'\x1bLua'
HEADER_SIZE = 14                 # hksc: lua_static_assert(LUAC_HEADERSIZE == 14)

#: Constant type tags, exactly the set the loader accepts -- anything else is "bad constant".
#: ⚠ There is NO type 13. An earlier `T_HASH = 13` with an 8-byte payload was a conflation of
#: two unrelated things: the 8-byte constant is LUA_TUI64 = 11, and the per-proto `hash` field
#: is not a constant at all. The old value was never contradicted because no chunk in the
#: console corpus carries either type (measured: 761 chunks hold only types 0/1/3/4).
T_NIL, T_BOOL, T_LIGHTUSERDATA, T_NUMBER, T_STRING, T_UI64 = 0, 1, 2, 3, 4, 11

#: byte 4 = Lua version, byte 5 = HKS format. Bytes 7..10 are the widths of int, size_t,
#: Instruction and lua_Number. The parser below is written for 4/4/4/4 and REFUSES anything
#: else rather than misreading it -- a format-14 or 64-bit chunk parses into plausible garbage.
HDR_VERSION, HDR_FORMAT = 0x51, 0x0D
HDR_WIDTHS = (4, 4, 4, 4)

#: a proto carries a 4-byte hash after the debug flag only when the flag is exactly this
FLAG_WITH_HASH = 1


class HksError(Exception):
    pass


class TilingError(HksError):
    """The parsed regions do not cover the buffer exactly."""


class SpliceRefused(HksError):
    """A byte-level edit was attempted on a chunk. Parse, mutate, re-emit instead."""


class Region(object):
    """One accounted-for byte range. Regions must tile the file exactly."""
    __slots__ = ('start', 'end', 'kind', 'proto')

    def __init__(self, start, end, kind, proto=None):
        self.start, self.end, self.kind, self.proto = start, end, kind, proto

    @property
    def size(self):
        return self.end - self.start

    def __repr__(self):
        p = '' if self.proto is None else ' #%d' % self.proto
        return '<%s%s 0x%X..0x%X (%d)>' % (self.kind, p, self.start, self.end, self.size)


class Constant(object):
    __slots__ = ('type', 'value', 'off', 'size')

    def __init__(self, type_, value, off, size):
        self.type, self.value, self.off, self.size = type_, value, off, size

    @property
    def type_name(self):
        return {T_NIL: 'nil', T_BOOL: 'bool', T_LIGHTUSERDATA: 'lightuserdata',
                T_NUMBER: 'number', T_STRING: 'string',
                T_UI64: 'ui64'}.get(self.type, 'type%d' % self.type)

    def encode(self, e):
        """Re-encode this constant. `serialize` uses this, so it defines the write format."""
        t = self.type
        if t == T_NIL:
            return bytes([t])
        if t == T_BOOL:
            return bytes([t, 1 if self.value else 0])
        if t == T_LIGHTUSERDATA:
            return bytes([t]) + struct.pack(e + 'I', int(self.value))
        if t == T_NUMBER:
            return bytes([t]) + struct.pack(e + 'f', float(self.value))
        if t == T_STRING:
            # ⚠ length 0 means the NULL string and NOTHING follows it -- it is not an empty
            # string. Appending a terminator here would emit length 1 plus a NUL, growing the
            # chunk by one byte and knocking every downstream proto off its 4-byte alignment.
            if self.value is None:
                return bytes([t]) + struct.pack(e + 'I', 0)
            v = self.value
            if isinstance(v, str):
                v = v.encode('latin1', 'replace')
            if not v.endswith(b'\x00'):
                v += b'\x00'
            return bytes([t]) + struct.pack(e + 'I', len(v)) + v
        if t == T_UI64:
            return bytes([t]) + struct.pack(e + 'Q', int(self.value))
        raise HksError('cannot encode a %s constant' % self.type_name)

    def __repr__(self):
        v = self.value
        if self.type == T_STRING:
            v = 'NULL' if v is None else \
                '"%s"' % (v.decode('latin1', 'replace').rstrip('\x00')[:60])
        return '%s %s' % (self.type_name, v)


class Proto(object):
    """One function prototype."""

    def __init__(self):
        self.upvals = 0
        self.params = 0
        self.vararg = 0
        self.registers = 0
        self.n_instr = 0
        self.instr_off = 0
        self.pad = b''
        self.constants = []
        self.subs = []
        self.off = 0
        self.end = 0
        self.index = 0
        self.debug_flag = 0
        self.hash = None            # int when debug_flag == FLAG_WITH_HASH, else None
        self._code = None
        self._owner = None

    @property
    def footer_size(self):
        """Bytes between the end of the constants and the sub-count, INCLUSIVE of the flag."""
        return 12 if self.hash is not None else 8

    def code_words(self):
        """The instruction words, decoded on first use and cached."""
        if self._code is None:
            f = self._owner
            self._code = list(struct.unpack_from(
                '%s%dI' % (f.e, self.n_instr), f.b, self.instr_off)) if self.n_instr else []
        return self._code

    def set_code_words(self, words):
        self._code = list(words)
        self.n_instr = len(self._code)

    def walk(self):
        yield self
        for s in self.subs:
            for x in s.walk():
                yield x

    def fingerprint(self):
        """Identity of everything this proto owns, for 'no other proto changed' proofs."""
        import hashlib
        h = hashlib.sha1()
        # unsigned: every one of these is a u32 read straight from the file, and a corrupt chunk
        # should fail a CHECK rather than blow up inside the fingerprint that reports it
        h.update(struct.pack('<7I', self.upvals, self.params, self.vararg, self.registers,
                             self.n_instr, self.debug_flag, len(self.constants)))
        h.update(struct.pack('<i', -1 if self.hash is None else 0))
        if self.hash is not None:
            h.update(struct.pack('<I', self.hash))
        for w in self.code_words():
            h.update(struct.pack('<I', w))
        for c in self.constants:
            h.update(c.encode('<'))
        h.update(struct.pack('<i', len(self.subs)))
        return h.hexdigest()

    def __repr__(self):
        return '<Proto #%d params=%d regs=%d instr=%d consts=%d subs=%d flag=%d%s>' % (
            self.index, self.params, self.registers, self.n_instr,
            len(self.constants), len(self.subs), self.debug_flag,
            '' if self.hash is None else ' hash=0x%08X' % self.hash)


class HksFile(object):
    """A parsed T6 HavokScript chunk.

    Every byte is accounted for by a `Region`; `tiling_report()` proves it.
    """

    def __init__(self, blob, footer_size=None):
        self.b = bytes(blob)
        if self.b[:4] != MAGIC:
            raise HksError('not a Lua/HavokScript chunk (magic %r)' % self.b[:4])
        if len(self.b) < HEADER_SIZE + 4:
            raise HksError('chunk is %d bytes, too short for a header' % len(self.b))
        self.little = (self.b[6] == 1)
        self.e = '<' if self.little else '>'
        self._check_header()
        self.const_types = []
        self.root = None
        self.consumed = 0
        self.trailing = 0
        self.regions = []
        self._parse()
        # `footer_size=` used to select the parse. It is now an ASSERTION: the shape is read
        # from each proto's own debug flag, so a caller that still passes a value is telling us
        # what it believes, and a mismatch is worth raising rather than quietly overriding.
        if footer_size is not None:
            odd = [p.index for p in self.protos if p.footer_size != footer_size]
            if odd:
                raise HksError('caller asserted footer=%d but proto(s) %s disagree (%s)'
                               % (footer_size, odd[:6],
                                  sorted({p.footer_size for p in self.protos})))

    # ------------------------------------------------------------------ header
    def _check_header(self):
        """Refuse a chunk this parser is not written for, instead of misreading it.

        The 14-byte header declares the Lua version, the HKS format number and the widths of
        int, size_t, Instruction and lua_Number. Everything below assumes 4/4/4/4 and format 13
        -- so a format-14 (T7) chunk, or one built with 8-byte size_t or doubles, would parse
        into plausible garbage rather than fail. The real loader memcmps the whole header and
        refuses on any difference; this is the same idea, stated as what we can actually model.
        """
        b = self.b
        got = (b[7], b[8], b[9], b[10])
        why = []
        if b[4] != HDR_VERSION:
            why.append('Lua version 0x%02X, expected 0x%02X' % (b[4], HDR_VERSION))
        if b[5] != HDR_FORMAT:
            why.append('HKS format %d, expected %d' % (b[5], HDR_FORMAT))
        if got != HDR_WIDTHS:
            why.append('field widths int/size_t/instr/number = %r, expected %r'
                       % (got, HDR_WIDTHS))
        if why:
            raise HksError('this parser cannot model the chunk: %s' % '; '.join(why))

    # ------------------------------------------------------------------ parse
    def _u32(self, p):
        if p + 4 > len(self.b):
            raise HksError('truncated: want 4 bytes at 0x%X of %d' % (p, len(self.b)))
        return struct.unpack_from(self.e + 'I', self.b, p)[0], p + 4

    def _add(self, start, end, kind, proto=None):
        if end > len(self.b):
            raise HksError('region %s runs past EOF (0x%X > %d)' % (kind, end, len(self.b)))
        self.regions.append(Region(start, end, kind, proto))

    def _parse(self):
        self.const_types = []
        self.regions = []
        p = HEADER_SIZE
        self._add(0, p, 'header')
        n, p = self._u32(p)
        self._add(p - 4, p, 'const_types.count')
        self.const_type_ids = []
        for _ in range(n):
            s = p
            tid, p = self._u32(p)
            ln, p = self._u32(p)
            self.const_type_ids.append(tid)
            self.const_types.append(self.b[p:p + ln])
            p += ln
            self._add(s, p, 'const_type')
        self._counter = 0
        self.root, p = self._proto(p)
        self.consumed = p
        self.trailing = len(self.b) - p
        if self.trailing:
            # Extraction slop: a caller that took an asset SPAN rather than the RawFile length
            # picks up whatever padded the span. (A structures block would also land here in
            # principle, but the structure extension is off in this build, so no shipped T6
            # chunk can carry one -- measured 0 tails across 761 console chunks extracted by
            # RawFile length.) Retained verbatim so the file still tiles and still re-emits.
            self._add(p, len(self.b), 'tail')

    def _proto(self, p):
        b = self.b
        pr = Proto()
        pr._owner = self
        pr.off = p
        pr.index = self._counter
        self._counter += 1
        s = p
        pr.upvals, p = self._u32(p)
        pr.params, p = self._u32(p)
        if p >= len(b):
            raise HksError('truncated at proto #%d vararg byte' % pr.index)
        pr.vararg = b[p]
        p += 1
        pr.registers, p = self._u32(p)
        pr.n_instr, p = self._u32(p)
        self._add(s, p, 'proto.head', pr.index)

        # hksc: LoadBlock(S, buff, aligned2instr(S->pos) - S->pos). The pad is implied by the
        # position, never stored as a length -- which is exactly why splicing is unsafe.
        extra = 4 - (p % 4)
        if 0 < extra < 4:
            pr.pad = b[p:p + extra]
            self._add(p, p + extra, 'proto.pad', pr.index)
            p += extra
        pr.instr_off = p
        if p + 4 * pr.n_instr > len(b):
            raise HksError('proto #%d claims %d instructions, past EOF' % (pr.index, pr.n_instr))
        self._add(p, p + 4 * pr.n_instr, 'proto.code', pr.index)
        p += 4 * pr.n_instr

        nc, p = self._u32(p)
        self._add(p - 4, p, 'proto.nconst', pr.index)
        for _ in range(nc):
            t = b[p]
            c_off = p
            p += 1
            if t == T_NIL:
                val = None
            elif t == T_BOOL:
                val = bool(b[p])
                p += 1
            elif t == T_LIGHTUSERDATA:
                val, p = self._u32(p)
            elif t == T_NUMBER:
                val = struct.unpack_from(self.e + 'f', b, p)[0]
                p += 4
            elif t == T_STRING:
                ln, p = self._u32(p)
                # length 0 is the NULL string, with no bytes following. Kept as None so it can
                # be told apart from a genuine empty string (which is stored as length 1).
                val = None if ln == 0 else b[p:p + ln]
                p += ln
            elif t == T_UI64:
                val = struct.unpack_from(self.e + 'Q', b, p)[0]
                p += 8
            else:
                raise HksError('constant type %d at 0x%x is not one of %s'
                               % (t, c_off, (T_NIL, T_BOOL, T_LIGHTUSERDATA,
                                             T_NUMBER, T_STRING, T_UI64)))
            pr.constants.append(Constant(t, val, c_off, p - c_off))
            self._add(c_off, p, 'constant', pr.index)

        pr.debug_flag, p = self._u32(p)
        self._add(p - 4, p, 'proto.debug_flag', pr.index)
        if pr.debug_flag == FLAG_WITH_HASH:
            pr.hash, p = self._u32(p)
            self._add(p - 4, p, 'proto.hash', pr.index)

        ns, p = self._u32(p)
        self._add(p - 4, p, 'proto.nsubs', pr.index)
        for _ in range(ns):
            sub, p = self._proto(p)
            pr.subs.append(sub)
        pr.end = p
        return pr, p

    # ------------------------------------------------------------------ proofs
    def tiling_report(self):
        """Do the regions cover [0, len) exactly, AT SIZES RE-DERIVED FROM THE MODEL?

        Returns (ok, [problem strings]).

        ⛔ CONTIGUITY ALONE IS A TAUTOLOGY. Every region is recorded as (cursor before the field,
        cursor after it), so a contiguous cover is guaranteed for any parse that does not raise --
        measured: 0 contiguity failures across 21,964 successfully-parsed byte mutations. Regions
        built from the parse cursor cannot witness an error in that same cursor's arithmetic.

        So the discriminating half is the SIZE AUDIT below: every region's length is recomputed
        from the decoded model (17 for a proto head, 4*len(code_words) for the code array, the
        re-encoded length of each constant, 4 for each counter) and compared with what the walk
        actually consumed. Those two agree only if the walk read what the model says is there.
        """
        probs = []

        # --- the half that can fail: sizes from the MODEL, not from the cursor ---------
        want = {}
        for pr in self.protos:
            want[('proto.head', pr.index)] = 17            # 4 + 4 + 1 + 4 + 4
            want[('proto.code', pr.index)] = 4 * len(pr.code_words())
            want[('proto.nconst', pr.index)] = 4
            want[('proto.debug_flag', pr.index)] = 4
            want[('proto.nsubs', pr.index)] = 4
            if pr.hash is not None:
                want[('proto.hash', pr.index)] = 4
        for r in self.regions:
            exp = want.get((r.kind, r.proto))
            if exp is not None and r.size != exp:
                probs.append('%r consumed %d B, the model says %d' % (r, r.size, exp))
        by_proto = {}
        for r in self.regions:
            if r.kind == 'constant':
                by_proto.setdefault(r.proto, []).append(r)
        for pr in self.protos:
            rs = by_proto.get(pr.index, [])
            if len(rs) != len(pr.constants):
                probs.append('proto #%d walked %d constant region(s), the model holds %d'
                             % (pr.index, len(rs), len(pr.constants)))
                continue
            for r, c in zip(rs, pr.constants):
                if r.size != len(c.encode(self.e)):
                    probs.append('proto #%d %s constant consumed %d B, re-encodes to %d'
                                 % (pr.index, c.type_name, r.size, len(c.encode(self.e))))
        rs = sorted(self.regions, key=lambda r: (r.start, r.end))
        total = len(self.b)
        if not rs:
            return False, ['no regions']
        if rs[0].start != 0:
            probs.append('first region starts at 0x%X, not 0' % rs[0].start)
        cur = 0
        for r in rs:
            if r.start < cur:
                probs.append('overlap: %r starts before 0x%X' % (r, cur))
            elif r.start > cur:
                probs.append('gap 0x%X..0x%X (%d B) before %r' % (cur, r.start, r.start - cur, r))
            if r.end < r.start:
                probs.append('inverted %r' % r)
            cur = max(cur, r.end)
        if cur != total:
            probs.append('regions end at 0x%X, file is %d bytes' % (cur, total))
        covered = sum(r.size for r in rs)
        if covered != total and not probs:
            probs.append('regions cover %d of %d bytes' % (covered, total))
        return (not probs), probs

    def serialize(self):
        """Rebuild the chunk from the model. Recomputes every alignment pad.

        For an unmodified parse this must be byte-identical to the input; `check()` asserts it.
        That equality is what makes the model trustworthy for editing -- if any field were
        unmodelled, the re-emit would differ.
        """
        e = self.e
        out = bytearray(self.b[:HEADER_SIZE])
        out += struct.pack(e + 'I', len(self.const_types))
        for tid, name in zip(self.const_type_ids, self.const_types):
            out += struct.pack(e + 'I', tid) + struct.pack(e + 'I', len(name)) + name
        self._emit_proto(self.root, out)
        if self.trailing:
            out += self.b[self.consumed:]
        return bytes(out)

    def _emit_proto(self, pr, out):
        e = self.e
        out += struct.pack(e + 'I', pr.upvals)
        out += struct.pack(e + 'I', pr.params)
        out += bytes([pr.vararg])
        out += struct.pack(e + 'I', pr.registers)
        words = pr.code_words()
        out += struct.pack(e + 'I', len(words))
        extra = 4 - (len(out) % 4)
        if 0 < extra < 4:
            # reuse the original filler when its length still fits, else hksc's 0x5F
            out += pr.pad if len(pr.pad) == extra else (b'\x5f' * extra)
        for w in words:
            out += struct.pack(e + 'I', w)
        out += struct.pack(e + 'I', len(pr.constants))
        for c in pr.constants:
            out += c.encode(e)
        out += struct.pack(e + 'I', pr.debug_flag)
        if pr.debug_flag == FLAG_WITH_HASH:
            if pr.hash is None:
                raise HksError('proto #%d has flag %d but no hash' % (pr.index, pr.debug_flag))
            out += struct.pack(e + 'I', pr.hash)
        out += struct.pack(e + 'I', len(pr.subs))
        for s in pr.subs:
            self._emit_proto(s, out)

    def check(self, strict_roundtrip=True, allow_tail=False):
        """Full structural proof of THIS chunk. Returns (ok, [problems]).

        ⛔ A TAIL IS A FAILURE BY DEFAULT, and this is the point. `tiling_report()` on its own
        cannot catch a parse that STOPS EARLY: the leftover bytes get their own `tail` region, so
        the regions still cover [0, len) and tiling still passes. That is precisely the shape of
        the bug this module was rewritten to kill -- a 12-byte footer assumption ends the walk
        3,534 bytes into a 30,513-byte chunk, and every "did it cover the buffer" test says yes.
        So the deficit is named here instead of absorbed. `allow_tail=True` is for the one honest
        case: a chunk extracted by asset SPAN rather than by RawFile length.
        """
        probs = []
        ok, tp = self.tiling_report()
        probs += tp
        if self.trailing and not allow_tail:
            probs.append('%d byte(s) past the end of the parse at 0x%X -- the walk stopped early '
                         'or the buffer is not exactly one chunk'
                         % (self.trailing, self.consumed))
        if strict_roundtrip:
            try:
                again = self.serialize()
            except Exception as ex:
                probs.append('serialize raised %s: %s' % (type(ex).__name__, ex))
            else:
                if again != self.b:
                    probs.append('re-emit differs from input (%d vs %d bytes%s)'
                                 % (len(again), len(self.b), _first_diff(again, self.b)))
        # These two are unreachable straight after a parse -- the parser only sets `hash` when
        # the flag is 1, and it aligns `instr_off` itself. They are here for HAND-BUILT models
        # (a caller that constructed protos, or mutated one) and are cheap enough to keep.
        for pr in self.protos:
            if pr.hash is not None and pr.debug_flag != FLAG_WITH_HASH:
                probs.append('proto #%d carries a hash with flag %d' % (pr.index, pr.debug_flag))
            if pr.n_instr and pr.instr_off % 4:
                probs.append('proto #%d code array at 0x%X is not 4-aligned'
                             % (pr.index, pr.instr_off))
        return (not probs), probs

    def fingerprints(self):
        """{proto index: fingerprint}. Compare two of these to name what an edit touched."""
        return {p.index: p.fingerprint() for p in self.protos}

    def verify(self, allow_tail=False):
        """`check()` as an assertion. Raises TilingError with the problems, returns self."""
        ok, probs = self.check(allow_tail=allow_tail)
        if not ok:
            raise TilingError('chunk does not verify: %s' % '; '.join(probs[:4]))
        return self

    # ------------------------------------------------------------------ views
    @property
    def protos(self):
        return list(self.root.walk()) if self.root else []

    @property
    def footer_size(self):
        """The single footer size if the file is uniform, else None.

        Kept because callers and older notes speak in these terms. `None` is not "unknown", it
        is the real answer for a mixed file -- which is why it was never a file-level property.
        """
        sizes = {p.footer_size for p in self.protos}
        return sizes.pop() if len(sizes) == 1 else None

    @property
    def footer_sizes(self):
        from collections import Counter
        return dict(Counter(p.footer_size for p in self.protos))

    def strings(self):
        """[(proto_index, const_index, Constant)] for every string constant."""
        out = []
        for pr in self.protos:
            for i, c in enumerate(pr.constants):
                if c.type == T_STRING:
                    out.append((pr.index, i, c))
        return out

    def summary(self):
        ps = self.protos
        ok, _ = self.tiling_report()
        return dict(endian='LE' if self.little else 'BE',
                    footer_size=self.footer_size, footer_sizes=self.footer_sizes,
                    bytes=len(self.b), consumed=self.consumed,
                    exact=self.consumed == len(self.b), trailing=self.trailing,
                    tiles=ok,
                    const_types=len(self.const_types),
                    protos=len(ps),
                    instructions=sum(p.n_instr for p in ps),
                    constants=sum(len(p.constants) for p in ps),
                    strings=len(self.strings()))

    def listing(self, max_protos=40):
        s = self.summary()
        L = ['; HavokScript chunk, %s, footers %s, %d bytes%s'
             % (s['endian'], s['footer_sizes'], s['bytes'],
                '' if s['tiles'] else '  !! DOES NOT TILE'),
             '; %d protos, %d instructions, %d constants (%d strings)'
             % (s['protos'], s['instructions'], s['constants'], s['strings']),
             '']
        for pr in self.protos[:max_protos]:
            L.append('proto #%d  params=%d vararg=%d regs=%d upvals=%d  instr=%d @0x%X  subs=%d'
                     % (pr.index, pr.params, pr.vararg, pr.registers, pr.upvals,
                        pr.n_instr, pr.instr_off, len(pr.subs)))
            for i, c in enumerate(pr.constants):
                L.append('    k[%-3d] %s' % (i, c))
        if len(self.protos) > max_protos:
            L.append('; ... %d more proto(s)' % (len(self.protos) - max_protos))
        return '\n'.join(L)

    def proto(self, index):
        pr = next((p for p in self.protos if p.index == index), None)
        if pr is None:
            raise HksError('no proto #%d' % index)
        return pr

    # ------------------------------------------------------------------ patching
    def patch_constant(self, proto_index, const_index, new_value):
        """Edit one constant and re-emit the whole chunk. Returns new chunk bytes.

        ⛔ This does NOT splice. A string constant may change length, and any length change that
        is not a multiple of 4 moves every downstream proto's code array off its 4-byte
        alignment. The pad is implied by position rather than stored, so a splice leaves the old
        filler in place and desynchronises the reader. Re-emitting recomputes every pad.
        """
        pr = self.proto(proto_index)
        if not (0 <= const_index < len(pr.constants)):
            raise HksError('proto #%d has %d constants' % (proto_index, len(pr.constants)))
        c = pr.constants[const_index]
        if c.type == T_STRING:
            if new_value is None:
                c.value = None                  # the NULL string, encoded as length 0
            else:
                v = (new_value.encode('latin1', 'replace')
                     if isinstance(new_value, str) else bytes(new_value))
                c.value = v if v.endswith(b'\x00') else v + b'\x00'
        elif c.type == T_NUMBER:
            c.value = float(new_value)
        elif c.type == T_BOOL:
            c.value = bool(new_value)
        elif c.type in (T_UI64, T_LIGHTUSERDATA):
            c.value = int(new_value)
        else:
            raise HksError('cannot patch a %s constant' % c.type_name)
        return self.serialize()

    def replace_string(self, old, new):
        """Replace every string constant equal to `old`. Returns (bytes, count).

        The match set is computed ONCE against the parsed model, then all of them are edited and
        the chunk is emitted once -- rather than re-parsing after each hit, which made the count
        depend on whether the replacement happened to equal the search term.
        """
        if isinstance(old, str):
            old = old.encode('latin1', 'replace')
        target = old if old.endswith(b'\x00') else old + b'\x00'
        hits = [(pi, ci) for pi, ci, c in self.strings() if c.value == target]
        for pi, ci in hits:
            pr = self.proto(pi)
            v = new.encode('latin1', 'replace') if isinstance(new, str) else new
            if not v.endswith(b'\x00'):
                v += b'\x00'
            pr.constants[ci].value = v
        # Always emit from the MODEL, even on zero hits. Returning `self.b` there would throw
        # away any edit a caller had already made to this same object and hand back the original
        # file as if it were the product.
        return self.serialize(), len(hits)

    def find_sites(self, predicate):
        """[(proto_index, instr_index, word)] for every instruction matching `predicate`.

        The point of returning ALL of them is `require_one` below: a patch that locates its
        target by pattern must prove the pattern is unique before it edits anything.
        """
        out = []
        for pr in self.protos:
            for i, w in enumerate(pr.code_words()):
                if predicate(pr, i, w):
                    out.append((pr.index, i, w))
        return out


def _first_diff(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return ', first differing byte at 0x%X' % i
    return ''


# ---------------------------------------------------------------------------- helpers

def is_hks(buf):
    return bool(buf) and buf[:4] == MAGIC


def parse(buf):
    return HksFile(buf)


def require_one(sites, what='site'):
    """Return the single element of `sites`, or refuse.

    ⚠ This exists because a pattern that matches once in one build matches twice in another.
    Measured: the `startLocIcon` field fetch that appears ONCE in the PC build of
    selectstartloczombie.lua appears TWICE in the Wii U build (code indices 8 and 20 of proto
    0.14). A patcher that edited "the" match would have edited the wrong one here.
    """
    if len(sites) == 1:
        return sites[0]
    raise HksError('expected exactly one %s, found %d%s'
                   % (what, len(sites), ': %r' % (sites[:6],) if sites else ''))


def refuse_splice(*_a, **_k):
    """Explicit refusal hook for callers that still want to hand-edit bytes.

    Banked as a law, enforced here: HKS chunks are never byte-spliced. Parse with `HksFile`,
    change the model, call `serialize()`.
    """
    raise SpliceRefused(
        'refusing to byte-splice a HavokScript chunk: instruction arrays are 4-aligned with the '
        'padding implied by position, so any non-multiple-of-4 shift silently misaligns every '
        'downstream proto (the game reports it as "LUI: Out of Memory"). '
        'Parse -> mutate the model -> serialize().')


def transcode(buf, want_le):
    """BE<->LE, natively, from the parsed model.

    ⚠ Previously delegated to `lua_endian.transcode`, which hardcodes a 12-byte footer. That
    walk stops at the first proto whose debug flag is 0 and returns a truncated chunk WITHOUT
    error -- and the truncated chunk re-parses exactly, so the usual checks pass. Doing it here
    keeps it on the flag-driven model and lets the result be proved.
    """
    f = HksFile(buf)
    if f.little == bool(want_le):
        out = f.serialize()
    else:
        if f.trailing:
            raise HksError('refusing to transcode a chunk with a %d-byte tail: the tail is '
                           'opaque here, and copying it verbatim would leave its multi-byte '
                           'fields in the source endianness' % f.trailing)
        # ⚠ ORDER MATTERS. Instruction words are decoded lazily against `f.e`, so they must be
        # forced WHILE `f.e` is still the source endianness. Flipping first would decode the
        # source bytes backwards and then write those wrong values out byte-swapped -- which
        # round-trips cleanly and is silently wrong.
        for pr in f.protos:
            pr.code_words()
        f.little = bool(want_le)
        f.e = '<' if f.little else '>'
        hdr = bytearray(f.b[:HEADER_SIZE])
        hdr[6] = 1 if want_le else 0
        f.b = bytes(hdr) + f.b[HEADER_SIZE:]
        out = f.serialize()

    # a tail is carried through verbatim, so the product is allowed exactly the tail the input
    # had -- but no more, and none if the input had none
    back = HksFile(out).verify(allow_tail=bool(f.trailing))
    if back.trailing != f.trailing:
        raise HksError('transcode changed the tail (%d -> %d bytes)'
                       % (f.trailing, back.trailing))
    if len(back.protos) != len(f.protos):
        raise HksError('transcode changed the proto count (%d -> %d)'
                       % (len(f.protos), len(back.protos)))
    return out
