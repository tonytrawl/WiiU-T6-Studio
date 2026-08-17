r"""core.gsc_assembler -- a GENERAL, lossless text <-> bytecode assembler for T6 GSC.

WHY THIS EXISTS
---------------
`core.gsc_codegen` compiles GSC *source* to bytecode, which only helps for scripts whose
source we happen to have. This module works the other way and needs nothing but the compiled
asset: it turns any ScriptParseTree blob into an editable assembly TEXT and turns that text
back into bytes. The contract is

    from_text(to_text(blob)) == blob          byte-identical, for every script

so anything the model cannot re-derive must be carried explicitly in the text rather than
guessed at re-emission time. Where a value IS derivable (jump displacements, table addresses,
string offsets, section offsets) the text carries the SYMBOL and the assembler recomputes --
that is what makes an edit that moves code work at all. Every such recomputation is checked
against the original at to_text() time, and to_text() emits an explicit literal override the
moment its model disagrees with the file. A round-trip therefore cannot pass by accident, and
an unmodelled corner shows up as a visible override instead of as silent corruption.

REFUSAL, NOT GUESSING
---------------------
Unknown opcodes, table shapes that do not close on their section boundary, string references
that do not land on a pool entry, and label references that do not resolve all raise
`AsmError`. Nothing plausible is ever emitted in place of something unknown.

WHAT THE TEXT LOOKS LIKE
------------------------
    .script s0                          ; "maps/mp/gametypes/_hud.gsc"
    .crc 0x8D4A1C33
    .flags 0x00
    .layout strings,include,cseg,exports,imports,animtree,stfix,fixup,profile

    .strings
      s0   "maps/mp/gametypes/_hud.gsc"
      s1   "init"
      ...
    .include s7, s8

    .cseg
      .raw 00 00 00 00 00 00
    L_004a:
      CheckClearParams
      PreScriptCall
      GetString 0x0000                  ; "hello"
      ScriptFunctionCall 0 0x0000002e   ; iprintlnbold/1
      DecTop
      End

    .exports
      export @L_004a name=s1 params=0 flags=0x00 crc=0x1BE5A0C1
    .imports
      import name=s5 ns=s6 params=1 flags=0x02 sites=L_0052
    .animtree
    .stfix
      strfix s9 type=0 sites=L_004e.o
    .fixup
    .profile

ANCHORS
-------
Table addresses do not all point at instruction starts: import sites point at the CALL OPCODE,
stringtablefixup sites at the ALIGNED OPERAND, animtree sites at a u32 operand, and switch
case targets at arbitrary code. So a label alone is not enough. Every address is written
relative to the element that contains it:

    L_004a          the instruction (or raw chunk) anchored at that address
    L_004a.o        that instruction's first multi-byte operand slot
    L_004a.l3       SafeCreateLocalVariables local #3's u16 name slot
    L_004a.c2       switch case #2's value word
    L_004a+6        a byte inside a raw chunk

The suffix is resolved from the re-emitted element, so it follows the code when the code moves.

BYTE ORDER
----------
Everything here is CONSOLE BIG-ENDIAN. `to_text` accepts a PC little-endian script and
transcodes it through the validated `gsc_diff.pc_gsc_to_console` first, recording
`.endian pc` so `from_text` hands back a PC buffer and the round-trip still closes.
"""
import re
import struct
import zlib

from . import paths  # noqa: F401

import gsc_diff
import gsc_spt

MAGIC = gsc_spt.MAGIC

# Sections in the order a genuine Treyarch script writes them (see core.gsc_codegen).
SECTIONS = ['strings', 'include', 'cseg', 'exports', 'imports', 'animtree', 'stfix',
            'fixup', 'profile']

# Header field name -> (offset, struct code) for the values the layout owns.
_OFF_FIELDS = {
    'include': 0x0C, 'animtree': 0x10, 'cseg': 0x14, 'stfix': 0x18,
    'exports': 0x1C, 'imports': 0x20, 'fixup': 0x24, 'profile': 0x28,
}

# Mnemonics. Anything absent renders as op_XX and assembles back from that spelling, so an
# unnamed opcode is never a blocker -- only less readable.
NAMES = {
    0x00: 'End', 0x01: 'Return', 0x02: 'GetUndefined', 0x03: 'GetZero',
    0x04: 'GetByte', 0x05: 'GetNegByte', 0x06: 'GetUnsignedShort',
    0x07: 'GetNegUnsignedShort', 0x08: 'GetInteger', 0x09: 'GetFloat',
    0x0A: 'GetString', 0x0B: 'GetIString', 0x0C: 'GetVector',
    0x13: 'GetAnimTree', 0x15: 'GetFunction',
    0x17: 'SafeCreateLocalVariables', 0x19: 'EvalLocalVariableCached',
    0x1A: 'EvalArray', 0x1B: 'EvalArray2', 0x1C: 'EvalArrayRef',
    0x1D: 'ClearArray', 0x1E: 'EmptyArray', 0x1F: 'GetSelfObject',
    0x20: 'EvalFieldVariable', 0x21: 'EvalFieldVariableRef', 0x22: 'ClearFieldVariable',
    0x26: 'CheckClearParams', 0x27: 'EvalLocalVariableRefCached',
    0x28: 'SetVariableField', 0x2A: 'CallBuiltin', 0x2C: 'Wait', 0x2D: 'PreScriptCall',
    0x2E: 'ScriptFunctionCall', 0x2F: 'ScriptFunctionCallPointer',
    0x30: 'ScriptMethodCall', 0x31: 'ScriptMethodCallPointer',
    0x32: 'ScriptThreadCall', 0x33: 'ScriptThreadCallPointer',
    0x34: 'ScriptMethodThreadCall', 0x35: 'ScriptMethodThreadCallPointer',
    0x36: 'DecTop', 0x37: 'CastFieldObject', 0x38: 'CastBool', 0x39: 'BoolNot',
    0x3A: 'BoolComplement', 0x3B: 'JumpOnFalse', 0x3C: 'JumpOnTrue',
    0x3D: 'JumpOnFalseExpr', 0x3E: 'JumpOnTrueExpr', 0x3F: 'Jump', 0x40: 'JumpBack',
    0x41: 'Inc', 0x42: 'Dec', 0x5B: 'Vector', 0x5C: 'GetHash',
}
# `core/_opcodes_mined.py` names 81 opcodes, derived by aligning this project's disassembler
# against gsc-tool over 319,570 corpus instructions (conflict-free), and it is more accurate
# than the hand table above: 0x5A is EndSwitch (the case TABLE), while Switch is 0x59.
# core.gsc still carries the old 0x5A: 'Switch' spelling; it is a display string there.
try:
    from ._opcodes_mined import MNEMONIC as _MINED
except Exception:                     # pragma: no cover - the listing degrades to op_XX
    _MINED = {}
NAMES.update(_MINED)

# ⛔ A MNEMONIC THAT NAMES TWO OPCODES SILENTLY REWRITES ONE INTO THE OTHER.
# The hand table above had `0x2C: 'Wait'` while the mined table (and gsc_codegen.OP) say
# `0x2B: 'Wait'`. With both present, every 0x2C in a file rendered as `Wait` and assembled
# back as 0x2B -- 40 of 323 corpus scripts stopped round-tripping the moment the tables were
# merged, which is exactly how the A1 gate earns its keep. Duplicates are resolved in favour
# of the MINED entry (319,570 instructions aligned against gsc-tool, conflict-free) and the
# loser reverts to `op_XX` rather than being guessed at.
_seen = {}
for _k in sorted(NAMES):
    _v = NAMES[_k]
    if _v in _seen:
        _drop = _seen[_v] if _k in _MINED else _k
        del NAMES[_drop]
        if _drop != _k:
            _seen[_v] = _k
    else:
        _seen[_v] = _k
BY_NAME = {}
for _k, _v in NAMES.items():
    if _v in BY_NAME:
        raise AssertionError('mnemonic %r names both 0x%02X and 0x%02X' % (_v, BY_NAME[_v], _k))
    BY_NAME[_v] = _k
for _k in range(0x100):
    BY_NAME.setdefault('op_%02X' % _k, _k)

# Forward-relative jumps: displacement is added to the address AFTER the operand.
# 0x7B DevblockBegin belongs here too: it is a forward jump OVER a `/# ... #/` block, with
# exactly the same encoding (measured on face_utility_mp.csc -- DevblockBegin at 0x66C, u16
# 0x0011, size 4, target 0x681, a real instruction). Before it was listed, a dev block's skip
# distance stayed a literal and would NOT have followed an edit that moved the block.
FWD_JUMPS = {0x3B, 0x3C, 0x3D, 0x3E, 0x3F, 0x7B}
# Backward-relative jump: displacement is subtracted.
BACK_JUMPS = {0x40}


class AsmError(Exception):
    pass


def _align(o, n):
    return (o + n - 1) & ~(n - 1)


def _hex(b):
    return ' '.join('%02X' % x for x in b)


def _unhex(s):
    s = s.replace(',', ' ').split()
    try:
        return bytes(int(x, 16) for x in s)
    except ValueError as ex:
        raise AsmError('bad hex byte list %r: %s' % (' '.join(s), ex))


def _q(s):
    """Quote a pool string for the listing (round-trip is by id, this is display only)."""
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif 0x20 <= ord(ch) < 0x7F:
            out.append(ch)
        else:
            out.append('\\x%02X' % ord(ch))
    return '"' + ''.join(out) + '"'


def _unq(s):
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise AsmError('expected a quoted string, got %r' % s)
    body, out, i = s[1:-1], [], 0
    while i < len(body):
        c = body[i]
        if c == '\\':
            n = body[i + 1]
            if n == 'x':
                out.append(chr(int(body[i + 2:i + 4], 16)))
                i += 4
                continue
            out.append({'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


# =========================================================================== cseg elements

class Raw(object):
    """A run of cseg bytes the walk did not decode (function prefix pads, alignment fill)."""
    kind = 'raw'

    def __init__(self, data):
        self.data = bytes(data)
        self.addr = None

    @property
    def size(self):
        return len(self.data)

    def emit(self, out, ctx):
        out += self.data

    def anchor(self, suffix):
        if suffix == '':
            return self.addr
        if suffix.startswith('+'):
            return self.addr + int(suffix[1:], 0)
        raise AsmError('raw chunk has no anchor %r' % suffix)


class Insn(object):
    """One decoded instruction.

    `ops` holds the decoded operand values in spec order. `pads` holds the alignment filler
    bytes consumed before each aligned operand, in order -- they are NOT read by the engine
    but they ARE part of the file, so they are preserved verbatim.
    """
    kind = 'insn'

    def __init__(self, op, spec, ops, pads):
        self.op = op
        self.spec = spec
        self.ops = ops              # per spec token; see _decode_insn
        self.pads = pads            # list[bytes]
        self.addr = None
        self._slots = {}            # suffix -> absolute address, filled by emit()

    # ------------------------------------------------------------------ sizing / emit
    def emit(self, out, ctx):
        """Append this instruction's bytes to `out` (a bytearray whose index 0 is file 0)."""
        self.addr = len(out)
        self._slots = {}
        out.append(self.op)
        pi = 0

        def pad_to(n):
            nonlocal pi
            need = _align(len(out), n) - len(out)
            fill = self.pads[pi] if pi < len(self.pads) else b'\x00' * need
            pi += 1
            if len(fill) != need:
                # The stored filler no longer matches the alignment this position needs.
                # Zero-extend or trim rather than mis-sizing the instruction; the values are
                # never read. Report it so a surprise is visible.
                fill = (fill + b'\x00' * need)[:need]
            out.extend(fill)

        first = True
        for ti, tok in enumerate(self.spec):
            v = self.ops[ti]
            if tok == 'u8':
                out.append(v & 0xFF)
            elif tok == 'u16':
                pad_to(2)
                if first:
                    self._slots['.o'] = len(out)
                    first = False
                out += struct.pack('>H', ctx.resolve16(self, ti, v, len(out)))
            elif tok == 'u32':
                pad_to(4)
                if first:
                    self._slots['.o'] = len(out)
                    first = False
                out += struct.pack('>I', ctx.resolve32(self, ti, v, len(out)))
            elif tok == 'vec3':
                pad_to(4)
                if first:
                    self._slots['.o'] = len(out)
                    first = False
                out += struct.pack('>3I', *v)
            elif tok == 'lvars':
                out.append(len(v) & 0xFF)
                for k, w in enumerate(v):
                    pad_to(2)
                    self._slots['.l%d' % k] = len(out)
                    out += struct.pack('>H', w)
            elif tok == 'switch':
                pad_to(4)
                self._slots['.o'] = len(out)
                first = False
                out += struct.pack('>I', len(v))
                for k, (val, tgt) in enumerate(v):
                    self._slots['.c%d' % k] = len(out)
                    out += struct.pack('>I', val)
                    rel = ctx.resolve_case(tgt, len(out) + 4)
                    out += struct.pack('>I', rel & 0xFFFFFFFF)
            else:
                raise AsmError('unhandled operand token %r' % tok)

    def anchor(self, suffix):
        if suffix == '':
            return self.addr
        if suffix in self._slots:
            return self._slots[suffix]
        if suffix.startswith('+'):
            return self.addr + int(suffix[1:], 0)
        raise AsmError('instruction at 0x%X has no anchor %r' % (self.addr or 0, suffix))


class Label(object):
    kind = 'label'

    def __init__(self, name):
        self.name = name


# =========================================================================== the program

class Pool(object):
    """The string pool: an ordered list of entries covering the pool region exactly.

    Entries are addressed by SYMBOL (s0, s1, ...), never by text, because a pool can and does
    hold the same text twice -- resolving a reference by text would then silently retarget it.
    """

    def __init__(self):
        self.entries = []      # list of (sym, text_or_None, raw_bytes)
        self.by_sym = {}
        self.offsets = {}      # sym -> absolute offset (after layout)

    def add(self, sym, text, raw):
        self.by_sym[sym] = len(self.entries)
        self.entries.append((sym, text, raw))

    def blob(self, base):
        out = bytearray()
        self.offsets = {}
        for sym, text, raw in self.entries:
            self.offsets[sym] = base + len(out)
            out += raw
        return bytes(out)

    def off(self, sym):
        if sym not in self.offsets:
            raise AsmError('unknown string symbol %r' % sym)
        return self.offsets[sym]


class Program(object):
    def __init__(self):
        self.endian = 'console'
        self.crc = 0
        self.flags = 0
        self.name_sym = None
        self.layout = list(SECTIONS)
        self.pool = Pool()
        self.include = []          # list of sym
        self.body = []             # cseg: Label / Insn / Raw
        self.exports = []          # dicts
        self.imports = []
        self.animtrees = []
        self.stfix = []
        self.fixup_raw = b''
        self.profile_raw = b''
        self.fixup_n = 0
        self.profile_n = 0
        self.gaps = {}             # section name -> bytes written AFTER that section
        self.tail = b''
        self.overrides = {}        # header field name -> literal value forced by to_text
        self.notes = []
        self.console = b''         # big-endian bytes this Program was parsed from
        self.cseg_range = (0, 0)

    # ------------------------------------------------------------------ assembly
    def assemble(self):
        """Lay everything out and return the console big-endian blob."""
        return _Assembler(self).run()


# =========================================================================== disassembly

def _read_tables(d):
    """Parse the tables straight from the buffer (gsc_spt gives names, we need raw fields)."""
    if not d.startswith(MAGIC):
        raise AsmError('not a T6 script: bad magic %r' % d[:8])
    h = {}
    (h['crc'], h['include'], h['animtree'], h['cseg'], h['stfix'], h['exports'],
     h['imports'], h['fixup'], h['profile'], h['cseg_size']) = struct.unpack_from('>10I', d, 8)
    (h['name'], h['stfix_n'], h['exports_n'], h['imports_n'], h['fixup_n'],
     h['profile_n']) = struct.unpack_from('>6H', d, 0x30)
    h['include_n'], h['animtree_n'], h['flags'] = struct.unpack_from('>3B', d, 0x3C)
    return h


def _pool_region(d, h):
    """[0x40, first_section_start) -- the string pool."""
    starts = [h[k] for k in _OFF_FIELDS if h[k] > 0x40]
    return 0x40, (min(starts) if starts else len(d))


def _decode_pool(d, lo, hi, needed):
    """Split the pool into NUL-terminated entries.

    `needed` is the set of offsets something actually references. An offset that is not the
    start of an entry means the pool is shared at a suffix -- that is legal C-string practice,
    so the entry is SPLIT there rather than guessed at.
    """
    cuts = sorted(set([lo, hi]) | set(o for o in needed if lo <= o < hi))
    pool = Pool()
    n = 0
    at = {}
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        # extend to the NUL terminator unless the next cut interrupts us
        e = d.find(b'\x00', a)
        e = (e + 1) if 0 <= e < b else b
        if e < b:
            # entry ends before the next cut: the remainder is separate filler
            segs = [(a, e), (e, b)]
        else:
            segs = [(a, b)]
        for (x, y) in segs:
            if x >= y:
                continue
            raw = d[x:y]
            text = raw[:-1].decode('latin-1') if raw.endswith(b'\x00') else None
            sym = 's%d' % n
            n += 1
            pool.add(sym, text, raw)
            at[x] = sym
    return pool, at


def _walk_cseg(d, h, cseg_lo, cseg_hi, stf_addrs):
    """Decode the code segment. Returns an ordered list of (addr, element).

    The walk mirrors `gsc_diff.swap_cseg`, which is byte-exact validated: start at every export
    address, stop when only the next function's `{u16, u32}` prefix pad can remain. Bytes the
    walk does not reach are emitted as Raw, so coverage is never a correctness question -- only
    a readability one.
    """
    exports = sorted(struct.unpack_from('>I', d, h['exports'] + 12 * i + 4)[0]
                     for i in range(h['exports_n']))
    decoded = {}                       # addr -> Insn
    consumed = bytearray(cseg_hi - cseg_lo)

    for fi, fstart in enumerate(exports):
        nxt = exports[fi + 1] if fi + 1 < len(exports) else None
        o = fstart
        if not (cseg_lo <= o < cseg_hi):
            continue
        while True:
            if nxt is not None:
                if o >= nxt:
                    break
                if _align(o, 4) + 4 >= nxt and d[nxt - 4:nxt] == b'\0\0\0\0':
                    break
            elif o >= cseg_hi:
                break
            if o in decoded or consumed[o - cseg_lo]:
                break                                  # already walked (shared tail)
            if o in stf_addrs:
                break     # a live string operand where an opcode belongs: leave it raw
            try:
                ins, no = _decode_insn(d, o)
            except AsmError:
                break
            if no > cseg_hi or (nxt is not None and no > nxt):
                break                                  # would straddle the next function
            decoded[o] = ins
            for x in range(o, no):
                consumed[x - cseg_lo] = 1
            o = no

    out = []
    p = cseg_lo
    run = bytearray()
    while p < cseg_hi:
        if p in decoded:
            if run:
                r = Raw(run)
                r.addr = p - len(run)
                out.append((r.addr, r))
                run = bytearray()
            ins = decoded[p]
            out.append((p, ins))
            p += ins._decoded_size
        else:
            run.append(d[p])
            p += 1
    if run:
        r = Raw(run)
        r.addr = cseg_hi - len(run)
        out.append((r.addr, r))
    return out


def _decode_insn(d, o):
    """Decode one instruction at `o`. Returns (Insn, next_offset)."""
    op = d[o]
    spec = gsc_diff.OPS.get(op)
    if spec is None:
        raise AsmError('unknown opcode 0x%02X at 0x%X' % (op, o))
    start = o
    o += 1
    ops, pads = [], []

    def eat_pad(n):
        nonlocal o
        a = _align(o, n)
        pads.append(bytes(d[o:a]))
        o = a

    for tok in spec:
        if tok == 'u8':
            ops.append(d[o])
            o += 1
        elif tok == 'u16':
            eat_pad(2)
            ops.append(struct.unpack_from('>H', d, o)[0])
            o += 2
        elif tok == 'u32':
            eat_pad(4)
            ops.append(struct.unpack_from('>I', d, o)[0])
            o += 4
        elif tok == 'vec3':
            eat_pad(4)
            ops.append(struct.unpack_from('>3I', d, o))
            o += 12
        elif tok == 'lvars':
            n = d[o]
            o += 1
            words = []
            for _ in range(n):
                eat_pad(2)
                words.append(struct.unpack_from('>H', d, o)[0])
                o += 2
            ops.append(words)
        elif tok == 'switch':
            eat_pad(4)
            (ncase,) = struct.unpack_from('>I', d, o)
            o += 4
            if ncase > 0x10000:
                raise AsmError('implausible switch count %d at 0x%X' % (ncase, o - 4))
            cases = []
            for _ in range(ncase):
                val, rel = struct.unpack_from('>II', d, o)
                o += 8
                srel = rel - 0x100000000 if rel & 0x80000000 else rel
                cases.append((val, o + srel))          # absolute target address
            ops.append(cases)
        else:
            raise AsmError('unhandled operand token %r' % tok)
    ins = Insn(op, spec, ops, pads)
    ins._decoded_size = o - start
    ins.addr = start
    return ins, o


# =========================================================================== references

class Ref(object):
    """A symbolic address: an anchor name, or an absolute literal that cannot follow an edit."""
    __slots__ = ('sym',)

    def __init__(self, sym):
        self.sym = sym

    def __repr__(self):
        return self.sym

    @property
    def absolute(self):
        return self.sym.startswith('@')


_ANCHOR_RE = re.compile(r'^(L_[0-9A-Fa-f]+)(\.o|\.l\d+|\.c\d+|\+\d+)?$')


class _SymTab(object):
    """Anchor name -> address, resolved from the element that owns the anchor."""

    def __init__(self):
        self.base = {}          # 'L_004A' -> element

    def addr(self, ref):
        if ref.absolute:
            return int(ref.sym[1:], 0)
        m = _ANCHOR_RE.match(ref.sym)
        if not m:
            raise AsmError('malformed address reference %r' % ref.sym)
        name, suffix = m.group(1), m.group(2) or ''
        el = self.base.get(name)
        if el is None:
            raise AsmError('reference to undefined label %r' % name)
        return el.anchor(suffix)


def _looks_console(buf):
    if len(buf) < 0x40:
        return True
    offs = struct.unpack_from('>10I', buf, 8)[1:9]
    return all(o <= len(buf) for o in offs)


# =========================================================================== to_text

def parse(blob):
    """Compiled GSC bytes -> Program. Raises AsmError on anything unmodelled."""
    raw = bytes(blob)
    endian = 'console'
    d = raw
    if not _looks_console(raw):
        endian = 'pc'
        d = gsc_diff.pc_gsc_to_console(raw)
    h = _read_tables(d)

    # ---------------------------------------------------------------- tables (raw fields)
    exports = []
    for i in range(h['exports_n']):
        crc, addr, noff, params, flags = struct.unpack_from('>IIHBB', d, h['exports'] + 12 * i)
        exports.append(dict(crc=crc, addr=addr, name=noff, params=params, flags=flags))
    imports, p = [], h['imports']
    for _ in range(h['imports_n']):
        noff, nsoff, na, params, flags = struct.unpack_from('>HHHBB', d, p)
        p += 8
        sites = list(struct.unpack_from('>%dI' % na, d, p)) if na else []
        p += 4 * na
        imports.append(dict(name=noff, ns=nsoff, params=params, flags=flags, sites=sites))
    imports_end = p
    animtrees, p = [], h['animtree']
    for _ in range(h['animtree_n']):
        noff, nsingle, npair, pad = struct.unpack_from('>4H', d, p)
        p += 8
        singles = list(struct.unpack_from('>%dI' % nsingle, d, p)) if nsingle else []
        p += 4 * nsingle
        pairs = []
        for _k in range(npair):
            a, b = struct.unpack_from('>II', d, p)
            p += 8
            pairs.append((a, b))
        animtrees.append(dict(name=noff, pad=pad, singles=singles, pairs=pairs))
    animtree_end = p
    stfix, p = [], h['stfix']
    for _ in range(h['stfix_n']):
        soff, na, ty = struct.unpack_from('>HBB', d, p)
        p += 4
        sites = list(struct.unpack_from('>%dI' % na, d, p)) if na else []
        p += 4 * na
        stfix.append(dict(str=soff, type=ty, sites=sites))
    stfix_end = p
    includes = (list(struct.unpack_from('>%dI' % h['include_n'], d, h['include']))
                if h['include_n'] else [])

    # ---------------------------------------------------------------- string pool
    needed = set()
    if h['name']:
        needed.add(h['name'])
    needed |= set(o for o in includes if o)
    needed |= set(e['name'] for e in exports if e['name'])
    for im in imports:
        if im['name']:
            needed.add(im['name'])
        if im['ns']:
            needed.add(im['ns'])
    for at in animtrees:
        if at['name']:
            needed.add(at['name'])
        for a, _b in at['pairs']:
            if a:
                needed.add(a)
    for sf in stfix:
        if sf['str']:
            needed.add(sf['str'])
    lo, hi = _pool_region(d, h)
    pool, sym_at = _decode_pool(d, lo, hi, needed)

    def S(off):
        if off == 0:
            return 's_null'
        if off not in sym_at:
            raise AsmError('string offset 0x%X is not the start of a pool entry in '
                           '[0x%X,0x%X)' % (off, lo, hi))
        return sym_at[off]

    # ---------------------------------------------------------------- cseg
    cseg_lo = h['cseg']
    cseg_hi = h['cseg'] + h['cseg_size']
    if h['cseg_size'] == 0:
        nxt = [h[k] for k in _OFF_FIELDS if h[k] > cseg_lo]
        cseg_hi = min(nxt) if nxt else len(d)
    stf_addrs = set(a for sf in stfix for a in sf['sites'])
    elements = _walk_cseg(d, h, cseg_lo, cseg_hi, stf_addrs)

    # ---------------------------------------------------------------- anchors
    anchor = {}
    for addr, el in elements:
        lab = 'L_%04X' % addr
        anchor.setdefault(addr, lab)
        if el.kind == 'insn':
            o, pi, first = addr + 1, 0, True
            for ti, tok in enumerate(el.spec):
                v = el.ops[ti]
                if tok == 'u8':
                    o += 1
                elif tok in ('u16', 'u32', 'vec3', 'switch'):
                    o += len(el.pads[pi]) if pi < len(el.pads) else 0
                    pi += 1
                    if first:
                        anchor.setdefault(o, lab + '.o')
                        first = False
                    if tok == 'u16':
                        o += 2
                    elif tok == 'u32':
                        o += 4
                    elif tok == 'vec3':
                        o += 12
                    else:
                        o += 4
                        for k in range(len(v)):
                            anchor.setdefault(o, lab + '.c%d' % k)
                            o += 8
                elif tok == 'lvars':
                    o += 1
                    for k in range(len(v)):
                        o += len(el.pads[pi]) if pi < len(el.pads) else 0
                        pi += 1
                        anchor.setdefault(o, lab + '.l%d' % k)
                        o += 2
        # Any byte of the element that is not a named slot still gets a symbol, so a table
        # address that points at, say, a SECOND aligned operand stays relative to its
        # instruction and follows it when the code moves.
        for k in range(el.size if el.kind == 'raw' else el._decoded_size):
            anchor.setdefault(addr + k, lab if k == 0 else '%s+%d' % (lab, k))

    stats = dict(absolute=0, jump_literals=0)
    used = set()

    def A(addr):
        s = anchor.get(addr)
        if s is None:
            stats['absolute'] += 1
            return Ref('@0x%X' % addr)
        used.add(s.split('.')[0].split('+')[0])
        return Ref(s)

    for e in exports:
        e['sym'] = A(e['addr'])
        e['name'] = S(e['name'])
    for im in imports:
        im['symsites'] = [A(a) for a in im['sites']]
        im['name'] = S(im['name'])
        im['ns'] = S(im['ns'])
    for at in animtrees:
        at['symsingles'] = [A(a) for a in at['singles']]
        at['sympairs'] = [(S(a), A(b)) for a, b in at['pairs']]
        at['name'] = S(at['name'])
    for sf in stfix:
        sf['symsites'] = [A(a) for a in sf['sites']]
        sf['str'] = S(sf['str'])

    # jump + switch targets, each checked against the displacement model before it is trusted
    for addr, el in elements:
        if el.kind != 'insn':
            continue
        if el.op in FWD_JUMPS or el.op in BACK_JUMPS:
            disp = el.ops[-1]
            after = addr + el._decoded_size
            # MEASURED (whole corpus, 2026-08-13): the u16 displacement of the forward-jump
            # family is SIGNED -- 2,902 jumps whose unsigned target lands outside the code
            # segment all land exactly on an instruction start when the top bit is read as a
            # sign. Unsigned is tried FIRST so the 425k jumps that already close cannot
            # regress; the signed reading is only a fallback, and either way the re-encoding
            # is checked against the file before the symbol is trusted.
            cands = []
            if el.op in FWD_JUMPS:
                cands.append(after + disp)
                if disp & 0x8000:
                    cands.append(after + disp - 0x10000)
            else:
                cands.append(after - disp)
            chosen = None
            for tgt in cands:
                sym = anchor.get(tgt)
                if sym is None:
                    continue
                back = (tgt - after) if el.op in FWD_JUMPS else (after - tgt)
                if (back & 0xFFFF) == disp:
                    chosen = sym
                    break
            if chosen is None:
                stats['jump_literals'] += 1     # model does not close here: keep the literal
            else:
                used.add(chosen.split('.')[0].split('+')[0])
                el.ops[-1] = Ref(chosen)
        elif el.op == 0x5A:
            cases = []
            for val, tgt in el.ops[-1]:
                sym = anchor.get(tgt)
                if sym is None:
                    stats['absolute'] += 1
                    cases.append((val, Ref('@0x%X' % tgt)))
                else:
                    used.add(sym.split('.')[0].split('+')[0])
                    cases.append((val, Ref(sym)))
            el.ops[-1] = cases

    # ---------------------------------------------------------------- assemble the Program
    prog = Program()
    prog.console = d          # the big-endian bytes, for consumers that must re-decode
    prog.cseg_range = (cseg_lo, cseg_hi)
    prog.endian = endian
    prog.crc = h['crc']
    prog.flags = h['flags']
    prog.name_sym = S(h['name']) if h['name'] else 's_null'
    prog.pool = pool
    prog.include = [S(o) for o in includes]
    for addr, el in elements:
        lab = 'L_%04X' % addr
        if lab in used:
            prog.body.append(Label(lab))
        prog.body.append(el)
    prog.exports = exports
    prog.imports = imports
    prog.animtrees = animtrees
    prog.stfix = stfix
    prog.fixup_n = h['fixup_n']
    prog.profile_n = h['profile_n']
    prog.fixup_raw = d[h['fixup']:h['fixup'] + 8 * h['fixup_n']]
    prog.profile_raw = d[h['profile']:h['profile'] + 8 * h['profile_n']]

    # ---------------------------------------------------------------- section layout
    real = {'strings': lo, 'include': h['include'], 'cseg': h['cseg'],
            'exports': h['exports'], 'imports': h['imports'], 'animtree': h['animtree'],
            'stfix': h['stfix'], 'fixup': h['fixup'], 'profile': h['profile']}
    size = {'strings': hi - lo, 'include': 4 * h['include_n'], 'cseg': cseg_hi - cseg_lo,
            'exports': 12 * h['exports_n'], 'imports': imports_end - h['imports'],
            'animtree': animtree_end - h['animtree'], 'stfix': stfix_end - h['stfix'],
            'fixup': 8 * h['fixup_n'], 'profile': 8 * h['profile_n']}
    order = sorted(SECTIONS, key=lambda s: (real[s], SECTIONS.index(s)))
    prog.layout = order
    pos = 0x40
    for i, s in enumerate(order):
        if size[s] and real[s] != pos:
            if real[s] < pos:
                raise AsmError('section %s starts at 0x%X but the previous section ends at '
                               '0x%X -- overlapping sections are not modelled'
                               % (s, real[s], pos))
            prev = order[i - 1] if i else None
            key = prev if prev else '<head>'
            prog.gaps[key] = d[pos:real[s]]
            pos = real[s]
        pos += size[s]
    if pos < len(d):
        prog.tail = d[pos:]
        pos = len(d)
    elif pos > len(d):
        raise AsmError('sections run past EOF (0x%X > 0x%X)' % (pos, len(d)))

    # header offset fields whose computed position disagrees with the file get carried
    # literally -- an empty table's offset is free to point anywhere, and some compilers do.
    computed, q = {}, 0x40
    if '<head>' in prog.gaps:
        q += len(prog.gaps['<head>'])
    for s in order:
        computed[s] = q
        q += size[s] + len(prog.gaps.get(s, b''))
    for fld in _OFF_FIELDS:
        if computed[fld] != h[fld]:
            prog.overrides[fld] = h[fld]
    if h['cseg_size'] != size['cseg']:
        prog.overrides['cseg_size'] = h['cseg_size']

    prog.stats = dict(stats,
                      insns=sum(1 for _a, e in elements if e.kind == 'insn'),
                      raw_bytes=sum(e.size for _a, e in elements if e.kind == 'raw'),
                      cseg_bytes=cseg_hi - cseg_lo,
                      overrides=len(prog.overrides),
                      gaps=sum(len(v) for v in prog.gaps.values()),
                      tail=len(prog.tail))
    return prog


# =========================================================================== rendering

def _fmt_ops(el, pool):
    """Operand text for one instruction, in spec order."""
    out = []
    for ti, tok in enumerate(el.spec):
        v = el.ops[ti]
        if tok == 'u8':
            out.append('%d' % v)
        elif tok == 'u16':
            out.append(v.sym if isinstance(v, Ref) else '0x%04X' % v)
        elif tok == 'u32':
            out.append(v.sym if isinstance(v, Ref) else '0x%08X' % v)
        elif tok == 'vec3':
            out.append('vec3(0x%08X,0x%08X,0x%08X)' % tuple(v))
        elif tok == 'lvars':
            out.append('lvars[%s]' % ','.join('0x%04X' % w for w in v))
        elif tok == 'switch':
            out.append('switch[%s]' % ','.join('0x%08X->%s' % (val, r.sym) for val, r in v))
    return ' '.join(out)


def _comments(prog):
    """anchor name -> trailing comment, so the listing reads like code rather than hex.

    The comments are DISPLAY ONLY -- `from_text` strips everything after ';' on a cseg line.
    They are derived from the tables, which is the only place the real meaning lives: a
    GetString operand in the file is a placeholder the loader overwrites via stringtablefixup.
    """
    text = {}
    for sym, txt, _raw in prog.pool.entries:
        text[sym] = txt
    out = {}
    for sf in prog.stfix:
        t = text.get(sf['str'])
        if t is None:
            continue
        for r in sf['symsites']:
            out[r.sym] = _q(t)
    for im in prog.imports:
        ns = text.get(im['ns']) or ''
        nm = text.get(im['name']) or '?'
        lbl = '%s%s/%d%s' % (ns + '::' if ns else '', nm, im['params'],
                             '  [devblock]' if im['flags'] & 0x10 else '')
        for r in im['symsites']:
            out[r.sym] = lbl
    for at in prog.animtrees:
        for _s, r in at['sympairs']:
            out.setdefault(r.sym, 'animtree %s' % (text.get(at['name']) or '?'))
    return out


def render(prog, name='<asset>'):
    L = []
    st = getattr(prog, 'stats', {})
    L.append('; gsc-asm v1  --  %s' % name)
    if st:
        L.append('; %(insns)d insns, %(cseg_bytes)d cseg bytes (%(raw_bytes)d undecoded), '
                 '%(absolute)d absolute ref(s), %(jump_literals)d literal jump(s), '
                 '%(overrides)d layout override(s)' % st)
    L.append('')
    L.append('.endian %s' % prog.endian)
    L.append('.script %s' % prog.name_sym)
    L.append('.crc 0x%08X' % prog.crc)
    L.append('.flags 0x%02X' % prog.flags)
    L.append('.layout %s' % ','.join(prog.layout))
    for k, v in sorted(prog.overrides.items()):
        L.append('.off %s 0x%X' % (k, v))
    for k, v in sorted(prog.gaps.items()):
        L.append('.gap %s %s' % (k, _hex(v)))
    if prog.tail:
        L.append('.tail %s' % _hex(prog.tail))
    L.append('')

    L.append('.strings')
    for sym, text, raw in prog.pool.entries:
        if text is None:
            L.append('  %-6s raw %s' % (sym, _hex(raw)))
        else:
            L.append('  %-6s %s' % (sym, _q(text)))
    L.append('')

    L.append('.include %s' % ', '.join(prog.include))
    L.append('')

    L.append('.cseg')
    cmt = _comments(prog)
    exp_at = {}
    _txt = dict((sym, t) for sym, t, _r in prog.pool.entries)
    for e in prog.exports:
        exp_at.setdefault(e['sym'].sym, []).append(
            '%s(%s)' % (_txt.get(e['name']) or e['name'],
                        ', '.join('p%d' % i for i in range(e['params']))))
    for el in prog.body:
        if isinstance(el, Label):
            who = exp_at.get(el.name)
            L.append('%s:%s' % (el.name, ('    ; ' + ', '.join(who)) if who else ''))
        elif el.kind == 'raw':
            L.append('  .raw %s' % _hex(el.data))
        else:
            pads = '/'.join(''.join('%02X' % b for b in q) for q in el.pads)
            mn = NAMES.get(el.op, 'op_%02X' % el.op)
            ops = _fmt_ops(el, prog.pool)
            line = '  %-28s %s' % (mn, ops) if ops else '  %s' % mn
            if pads.strip('0/'):
                line = '%s pad=%s' % (line.rstrip(), pads)
            line = line.rstrip()
            lab = 'L_%04X' % el.addr
            c = cmt.get(lab) or cmt.get(lab + '.o')
            if c:
                line = '%-52s ; %s' % (line, c)
            L.append(line.rstrip())
    L.append('')

    L.append('.exports')
    for e in prog.exports:
        L.append('  export @%s name=%s params=%d flags=0x%02X crc=0x%08X'
                 % (e['sym'].sym, e['name'], e['params'], e['flags'], e['crc']))
    L.append('')
    L.append('.imports')
    for im in prog.imports:
        L.append('  import name=%s ns=%s params=%d flags=0x%02X sites=%s'
                 % (im['name'], im['ns'], im['params'], im['flags'],
                    ','.join(r.sym for r in im['symsites']) or '-'))
    L.append('')
    L.append('.animtree')
    for at in prog.animtrees:
        L.append('  animtree name=%s pad=0x%04X singles=[%s] pairs=[%s]'
                 % (at['name'], at['pad'],
                    ','.join(r.sym for r in at['symsingles']),
                    ','.join('%s->%s' % (s, r.sym) for s, r in at['sympairs'])))
    L.append('')
    L.append('.stfix')
    for sf in prog.stfix:
        L.append('  strfix %s type=%d sites=%s'
                 % (sf['str'], sf['type'],
                    ','.join(r.sym for r in sf['symsites']) or '-'))
    L.append('')
    L.append('.fixup %s' % _hex(prog.fixup_raw))
    L.append('.profile %s' % _hex(prog.profile_raw))
    return '\n'.join(L) + '\n'


def to_text(blob, name='<asset>'):
    return render(parse(blob), name)


# =========================================================================== assembling

class _Ctx(object):
    """Operand resolver handed to Insn.emit(). Pass 1 only measures; pass 2 resolves."""

    def __init__(self, syms, live):
        self.syms = syms
        self.live = live

    def _disp(self, ins, ref, after):
        tgt = self.syms.addr(ref)
        if ins.op in FWD_JUMPS:
            d = tgt - after
            lo, hi = -0x8000, 0xFFFF          # signed backward, or a long unsigned forward
        else:
            d = after - tgt
            lo, hi = 0, 0xFFFF
        if not (lo <= d <= hi):
            raise AsmError('jump at 0x%X to %s needs a displacement of %d, which does not fit '
                           'the u16 operand' % (ins.addr, ref.sym, d))
        return d & 0xFFFF

    def resolve16(self, ins, ti, v, at):
        if isinstance(v, Ref):
            return self._disp(ins, v, at + 2) if self.live else 0
        return v & 0xFFFF

    def resolve32(self, ins, ti, v, at):
        if isinstance(v, Ref):
            return self.syms.addr(v) if self.live else 0
        return v & 0xFFFFFFFF

    def resolve_case(self, tgt, after):
        if not isinstance(tgt, Ref):
            return int(tgt) & 0xFFFFFFFF
        if not self.live:
            return 0
        return (self.syms.addr(tgt) - after) & 0xFFFFFFFF


class _Assembler(object):
    def __init__(self, prog):
        self.p = prog

    def _table_sizes(self, pool_len):
        p = self.p
        return {
            'strings': pool_len,
            'include': 4 * len(p.include),
            'cseg': None,                                   # measured after emission
            'exports': 12 * len(p.exports),
            'imports': sum(8 + 4 * len(e['symsites']) for e in p.imports),
            'animtree': sum(8 + 4 * len(a['symsingles']) + 8 * len(a['sympairs'])
                            for a in p.animtrees),
            'stfix': sum(4 + 4 * len(e['symsites']) for e in p.stfix),
            'fixup': len(p.fixup_raw),
            'profile': len(p.profile_raw),
        }

    def run(self):
        p = self.p
        head_gap = p.gaps.get('<head>', b'')
        pool_base = 0x40 + len(head_gap)
        pool_blob = p.pool.blob(pool_base)
        size = self._table_sizes(len(pool_blob))

        # cseg base = everything the layout puts before it
        base = pool_base
        for s in p.layout:
            if s == 'cseg':
                break
            if size[s] is None:
                raise AsmError('layout puts cseg before itself')
            base += size[s] + len(p.gaps.get(s, b''))

        # cseg: pass 1 fixes every address, pass 2 resolves the symbolic operands. Operand
        # WIDTHS never depend on operand VALUES, so the two passes measure identically.
        syms = _SymTab()
        cseg = b''
        for live in (False, True):
            out = bytearray(b'\x00' * base)
            ctx = _Ctx(syms, live)
            pending = None
            for el in p.body:
                if isinstance(el, Label):
                    pending = el.name
                    continue
                el.addr = len(out)
                if pending:
                    syms.base[pending] = el
                    pending = None
                syms.base.setdefault('L_%04X' % el.addr, el)
                el.emit(out, ctx)
            cseg = bytes(out[base:])
        size['cseg'] = len(cseg)

        # ---- section offsets
        off, q = {}, pool_base
        for s in p.layout:
            off[s] = q
            q += size[s] + len(p.gaps.get(s, b''))
        total = q
        for fld, v in p.overrides.items():
            if fld in off:
                off[fld] = v

        def soff(sym):
            return 0 if sym == 's_null' else p.pool.off(sym)

        # ---- tables
        inc = b''.join(struct.pack('>I', soff(s)) for s in p.include)
        exp = b''.join(struct.pack('>IIHBB', e['crc'], syms.addr(e['sym']), soff(e['name']),
                                   e['params'], e['flags']) for e in p.exports)
        imp = bytearray()
        for e in p.imports:
            imp += struct.pack('>HHHBB', soff(e['name']), soff(e['ns']),
                               len(e['symsites']), e['params'], e['flags'])
            for r in e['symsites']:
                imp += struct.pack('>I', syms.addr(r))
        ant = bytearray()
        for a in p.animtrees:
            ant += struct.pack('>4H', soff(a['name']), len(a['symsingles']),
                               len(a['sympairs']), a['pad'])
            for r in a['symsingles']:
                ant += struct.pack('>I', syms.addr(r))
            for s, r in a['sympairs']:
                ant += struct.pack('>II', soff(s), syms.addr(r))
        stf = bytearray()
        for e in p.stfix:
            stf += struct.pack('>HBB', soff(e['str']), len(e['symsites']), e['type'])
            for r in e['symsites']:
                stf += struct.pack('>I', syms.addr(r))
        blobs = {'strings': pool_blob, 'include': inc, 'cseg': cseg, 'exports': exp,
                 'imports': bytes(imp), 'animtree': bytes(ant), 'stfix': bytes(stf),
                 'fixup': p.fixup_raw, 'profile': p.profile_raw}
        for s in p.layout:
            if len(blobs[s]) != size[s]:
                raise AsmError('section %s measured %d bytes but emitted %d'
                               % (s, size[s], len(blobs[s])))

        # ---- header
        hdr = bytearray(0x40)
        hdr[0:8] = MAGIC
        struct.pack_into('>I', hdr, 0x08, p.crc)
        for fld, at in _OFF_FIELDS.items():
            struct.pack_into('>I', hdr, at, off[fld])
        struct.pack_into('>I', hdr, 0x2C, p.overrides.get('cseg_size', size['cseg']))
        struct.pack_into('>6H', hdr, 0x30, soff(p.name_sym), len(p.stfix), len(p.exports),
                         len(p.imports), len(p.fixup_raw) // 8, len(p.profile_raw) // 8)
        hdr[0x3C] = len(p.include)
        hdr[0x3D] = len(p.animtrees)
        hdr[0x3E] = p.flags
        hdr[0x3F] = 0

        out = bytearray(hdr)
        out += head_gap
        for s in p.layout:
            out += blobs[s]
            out += p.gaps.get(s, b'')
        if len(out) != total:
            raise AsmError('assembled %d bytes, layout predicted %d' % (len(out), total))
        out += p.tail
        blob = bytes(out)
        if p.endian == 'pc':
            blob = gsc_diff.console_gsc_to_pc(blob)
        return blob


# =========================================================================== parsing text

def _INT(s):
    return int(s, 0)


def _split_kv(parts):
    kv, rest = {}, []
    for t in parts:
        if '=' in t and not t.startswith('0x'):
            k, v = t.split('=', 1)
            kv[k] = v
        else:
            rest.append(t)
    return kv, rest


def _refs(s):
    if s in ('-', ''):
        return []
    return [Ref(x) for x in s.split(',') if x]


_SECTION_DIRECTIVES = {'.strings', '.include', '.cseg', '.exports', '.imports',
                       '.animtree', '.stfix', '.fixup', '.profile'}


def from_text(text):
    """Assembly text -> compiled GSC bytes. Raises AsmError with a reason, never guesses."""
    p = Program()
    st = {'section': None, 'syms': set()}
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        s = raw_line.strip()
        if not s or s.startswith(';'):
            continue
        try:
            _parse_line(p, s, st)
        except AsmError as ex:
            raise AsmError('line %d: %s%s    %s' % (lineno, ex, chr(10), s))
        except Exception as ex:
            raise AsmError('line %d: %s: %s%s    %s'
                           % (lineno, type(ex).__name__, ex, chr(10), s))
    return p.assemble()


def _parse_line(p, s, st):
    head = s.split()[0]
    if head in _SECTION_DIRECTIVES:
        st['section'] = head[1:]

    if head == '.endian':
        p.endian = s.split()[1]
        return
    if head == '.script':
        p.name_sym = s.split()[1]
        return
    if head == '.crc':
        p.crc = _INT(s.split()[1])
        return
    if head == '.flags':
        p.flags = _INT(s.split()[1])
        return
    if head == '.layout':
        p.layout = s.split()[1].split(',')
        if sorted(p.layout) != sorted(SECTIONS):
            raise AsmError('.layout must list all %d sections exactly once; got %r'
                           % (len(SECTIONS), p.layout))
        return
    if head == '.off':
        _, fld, val = s.split()
        p.overrides[fld] = _INT(val)
        return
    if head == '.gap':
        parts = s.split(None, 2)
        p.gaps[parts[1]] = _unhex(parts[2]) if len(parts) > 2 else b''
        return
    if head == '.tail':
        p.tail = _unhex(s[len('.tail'):])
        return

    # section openers that carry their whole payload on the same line
    if head == '.include':
        p.include = s[len('.include'):].replace(',', ' ').split()
        return
    if head == '.fixup':
        p.fixup_raw = _unhex(s[len('.fixup'):])
        return
    if head == '.profile':
        p.profile_raw = _unhex(s[len('.profile'):])
        return
    if head in _SECTION_DIRECTIVES:
        return

    cur = st['section']
    if cur == 'strings':
        parts = s.split(None, 1)
        sym = parts[0]
        if sym in st['syms']:
            raise AsmError('duplicate string symbol %r' % sym)
        if sym == 's_null':
            raise AsmError('s_null is reserved for the null string offset')
        st['syms'].add(sym)
        body = parts[1] if len(parts) > 1 else '""'
        if body.startswith('raw '):
            p.pool.add(sym, None, _unhex(body[4:]))
        else:
            txt = _unq(body)
            p.pool.add(sym, txt, txt.encode('latin-1') + bytes([0]))
        return

    if cur == 'cseg':
        _parse_cseg_line(p, s)
        return

    if cur == 'exports':
        kv, rest = _split_kv(s.split())
        if rest[0] != 'export':
            raise AsmError('expected an `export` record, got %r' % rest[0])
        at = [t for t in rest if t.startswith('@')][0][1:]
        p.exports.append(dict(sym=Ref(at), name=kv['name'], params=_INT(kv['params']),
                              flags=_INT(kv['flags']), crc=_INT(kv['crc'])))
        return

    if cur == 'imports':
        kv, rest = _split_kv(s.split())
        if rest[0] != 'import':
            raise AsmError('expected an `import` record, got %r' % rest[0])
        p.imports.append(dict(name=kv['name'], ns=kv['ns'], params=_INT(kv['params']),
                              flags=_INT(kv['flags']), symsites=_refs(kv['sites'])))
        return

    if cur == 'animtree':
        kv, rest = _split_kv(s.split())
        if rest[0] != 'animtree':
            raise AsmError('expected an `animtree` record, got %r' % rest[0])
        pairs = []
        for tok in kv['pairs'].strip('[]').split(','):
            if not tok:
                continue
            a, b = tok.split('->')
            pairs.append((a, Ref(b)))
        p.animtrees.append(dict(name=kv['name'], pad=_INT(kv['pad']),
                                symsingles=_refs(kv['singles'].strip('[]')),
                                sympairs=pairs))
        return

    if cur == 'stfix':
        kv, rest = _split_kv(s.split())
        if rest[0] != 'strfix':
            raise AsmError('expected a `strfix` record, got %r' % rest[0])
        p.stfix.append(dict(str=rest[1], type=_INT(kv['type']),
                            symsites=_refs(kv['sites'])))
        return

    raise AsmError('unexpected line outside any section: %r' % s)


_PAD_RE = re.compile(r'(?:^|\s)pad=([0-9A-Fa-f/]*)(?=\s|$)')


def _parse_cseg_line(p, s):
    if ';' in s:              # cseg operands never contain ';', so this is always a comment
        s = s.split(';', 1)[0].rstrip()
        if not s:
            return
    if s.endswith(':') and s.startswith('L_'):
        p.body.append(Label(s[:-1]))
        return
    if s.startswith('.raw'):
        p.body.append(Raw(_unhex(s[4:])))
        return
    # `pad=` carries the (unread) alignment filler of each aligned operand in consumption
    # order, '/'-separated so an EMPTY filler stays distinguishable from a missing one.
    pads = []
    m = _PAD_RE.search(s)
    if m:
        s = (s[:m.start()] + ' ' + s[m.end():]).strip()
        pads = [bytes.fromhex(x) for x in m.group(1).split('/')]
    toks = s.split()
    mn = toks[0]
    if mn not in BY_NAME:
        raise AsmError('unknown mnemonic %r' % mn)
    op = BY_NAME[mn]
    spec = gsc_diff.OPS.get(op)
    if spec is None:
        raise AsmError('opcode 0x%02X has no operand spec' % op)
    args = toks[1:]
    if len(args) != len(spec):
        raise AsmError('%s takes %d operand(s), got %d' % (mn, len(spec), len(args)))
    ops = []
    for tok, a in zip(spec, args):
        if tok == 'u8':
            ops.append(_INT(a))
        elif tok in ('u16', 'u32'):
            ops.append(Ref(a) if (a.startswith('L_') or a.startswith('@')) else _INT(a))
        elif tok == 'vec3':
            ops.append(tuple(_INT(x) for x in a[len('vec3('):-1].split(',')))
        elif tok == 'lvars':
            ops.append([_INT(x) for x in a[len('lvars['):-1].split(',') if x])
        elif tok == 'switch':
            cases = []
            for t in a[len('switch['):-1].split(','):
                if not t:
                    continue
                val, tgt = t.split('->')
                cases.append((_INT(val), Ref(tgt)))
            ops.append(cases)
        else:
            raise AsmError('unhandled operand token %r' % tok)
    p.body.append(Insn(op, spec, ops, pads))
