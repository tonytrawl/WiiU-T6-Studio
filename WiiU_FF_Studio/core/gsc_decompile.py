r"""core.gsc_decompile -- compiled T6 GSC bytecode -> readable GSC source.

Milestone C. `core.gsc_assembler` already proves the text form carries every bit of a script;
this goes further and reconstructs the SOURCE, so a script can be read and edited as GSC rather
than as an instruction listing.

HOW IT IS DERIVED
-----------------
It is the inverse of `core.gsc_codegen`, which is not a guess about the compiler -- that module
was validated against gsc-tool instruction-for-instruction (see its docstring). Every pattern
matched here is one of its emitters read backwards:

    assignment      <value> <ref> SetVariableField          (value THEN target)
    call statement  PreScriptCall <argN..arg0> [target] Call DecTop
    call arguments  pushed RIGHT-TO-LEFT, so the first popped is arg0
    method call     the object is pushed AFTER the arguments
    if / else       <cond> JumpOnFalse els ... Jump end ... els: ... end:
    ternary         the same shape, but each arm leaves exactly ONE value and no statement
    && / ||         <a> JumpOnFalseExpr/JumpOnTrueExpr end <b> end:
    while           top: <cond> JumpOnFalse end <body> Jump top  end:
    for             ... the same, but something jumps to the STEP rather than to top
    do/while        top: <body> <cond> JumpOnTrue top
    foreach         <coll>->_a  FirstArrayKey->k  top: k IsDefined JumpOnFalse end
                    k _a EvalArray -> v  <body>  cont: k _a NextArrayKey -> k  Jump top
    switch          <subj> Switch(->table) <bodies> EndSwitch <{value, rel target}...>
    local indices   REVERSED: index = (count - 1) - declaration_position
    locals          named by the stringtablefixup entries on SafeCreateLocalVariables

REFUSAL, NOT PLAUSIBLE SOURCE
-----------------------------
Anything that does not match a known shape raises `DecompileError` with the reason and the
instruction that caused it. A function that cannot be structured is emitted as a stub carrying
that reason, and `decompile()` reports which functions were refused. Wrong source that compiles
is far worse than no source, because it looks like it worked.

WHAT THE GATE MEASURES
----------------------
`core.gsc_asm_selftest` gate C1: decompile -> `gsc_codegen.compile_source` -> compare the
NORMALISED instruction streams (mnemonics, plus operands resolved to strings / call targets /
local names / relative instruction distances). Byte-identity is NOT the bar and never could be:
string-pool order and table order are free choices for any compiler.
"""
import struct

from . import paths  # noqa: F401
from . import gsc_assembler as GA


class DecompileError(Exception):
    pass


OPN = GA.NAMES


def _n(op):
    return OPN.get(op, 'op_%02X' % op)


# Opcodes whose u16 operand is a string reference patched by the stringtablefixup table.
FIELD_OPS = {0x20, 0x21, 0x22}
# Value-position and object-position forms of the four special objects.
SPECIAL_VAL = {0x0F: 'self', 0x10: 'level', 0x11: 'game', 0x12: 'anim'}
SPECIAL_OBJ = {0x1F: 'self', 0x0D: 'level', 0x14: 'game', 0x0E: 'anim'}
BINOP = {
    0x43: '|', 0x44: '^', 0x45: '&', 0x46: '==', 0x47: '!=', 0x48: '<', 0x49: '>',
    0x4A: '<=', 0x4B: '>=', 0x4C: '<<', 0x4D: '>>', 0x4E: '+', 0x4F: '-',
    0x50: '*', 0x51: '/', 0x52: '%',
}
CALL_OPS = {0x2E: ('call', False, False), 0x30: ('call', True, False),
            0x32: ('call', False, True), 0x34: ('call', True, True)}
PTR_CALL_OPS = {0x2F: (False, False), 0x31: (True, False),
                0x33: (False, True), 0x35: (True, True)}
# VectorConstant (0x5E) packs a whole literal vector into ONE u8: two bits per component,
# x in bits 4-5, y in 2-3, z in 0-1, with 0 -> 00, +1 -> 10, -1 -> 01. REVERSE-ENGINEERED
# 2026-08-13 against gsc-tool over all 27 combinations of {-1,0,1}^3, exact on 27/27 -- the
# module docstring of gsc_codegen previously recorded this encoding as NOT pinned down.
#   (-1,-1,-1) -> 0x15   (0,0,0) -> 0x00   (1,1,1) -> 0x2A   (0,1,0) -> 0x08
VC_BITS = {0: '0', 1: '-1', 2: '1'}
# Single-opcode builtins: {opcode: (source name, arity)}. Mined per function against
# gsc-tool 2026-08-13 -- 711 functions, zero conflicting votes. Each has a matching entry in
# gsc_codegen.INTRINSIC, so decompiled source recompiles to the SAME opcode rather than to an
# import call.
INTRINSIC_OPS = {
    0x61: ('anglestoup', 1), 0x62: ('anglestoright', 1),
    0x63: ('anglestoforward', 1), 0x64: ('angleclamp180', 1),
    0x65: ('vectortoangles', 1), 0x66: ('abs', 1), 0x67: ('gettime', 0),
    0x68: ('getdvar', 1), 0x6A: ('getdvarfloat', 1), 0x6C: ('getdvarcolorred', 1),
    0x6D: ('getdvarcolorgreen', 1), 0x6E: ('getdvarcolorblue', 1),
}
COND_JUMPS = {0x3B: False, 0x3C: True}          # -> jump when the value is (False|True)
EXPR_JUMPS = {0x3D: '&&', 0x3E: '||'}

# REAL OPERATOR PRECEDENCE. Every binary operator used to render at one flat level, so
# `(a - b) % 360` came out as `a - b % 360` -- which re-parses as `a - (b % 360)` and
# recompiles to a DIFFERENT instruction order. A left-associative operator of precedence P
# needs its left operand rendered at P and its right operand at P+1.
PREC = {
    '*': 11, '/': 11, '%': 11,
    '+': 10, '-': 10,
    '<<': 9, '>>': 9,
    '<': 8, '>': 8, '<=': 8, '>=': 8,
    '==': 7, '!=': 7,
    '&': 6, '^': 5, '|': 4,
    '&&': 3, '||': 2,
}
P_TERNARY = 1
P_UNARY = 12
P_PRIMARY = 100


# =========================================================================== expressions

class E(object):
    """A reconstructed expression. `prec` drives parenthesisation only."""
    __slots__ = ('txt', 'prec')

    def __init__(self, txt, prec=100):
        self.txt = txt
        self.prec = prec

    def __str__(self):
        return self.txt

    def wrap(self, need):
        return '(%s)' % self.txt if self.prec < need else self.txt


def _lit_str(s):
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\t':
            out.append('\\t')
        elif 0x20 <= ord(ch) < 0x7F:
            out.append(ch)
        else:
            # The T6 lexer has no \xNN escape, so a string that cannot be written back
            # literally is a refusal rather than a silently mangled literal.
            raise DecompileError('string literal contains byte 0x%02X, which the GSC lexer '
                                 'cannot express' % ord(ch))
    out.append('"')
    return ''.join(out)


def _num(v):
    return str(v)


def _flt(bits):
    (f,) = struct.unpack('>f', struct.pack('>I', bits))
    if f != f or f in (float('inf'), float('-inf')):
        raise DecompileError('non-finite float constant 0x%08X' % bits)
    r = repr(f)
    if 'e' in r or 'E' in r:
        r = '%.9g' % f
    if '.' not in r and 'e' not in r:
        r += '.0'
    return r


# =========================================================================== lifting

class Ins(object):
    __slots__ = ('idx', 'addr', 'op', 'ops', 'size', 'tgt', 's', 'imp', 'lv', 'cases')

    def __init__(self, idx, el):
        self.idx = idx
        self.addr = el.addr
        self.op = el.op
        self.ops = el.ops
        self.size = el._decoded_size
        self.tgt = None        # jump target, as an instruction INDEX
        self.s = None          # resolved string operand
        self.imp = None        # resolved import record
        self.lv = None         # SafeCreateLocalVariables names, declaration order
        self.cases = None      # EndSwitch: [(value_word, target_index, string_or_None)]

    @property
    def name(self):
        return _n(self.op)

    def __repr__(self):
        return '%d: %s' % (self.idx, self.name)


class Fn(object):
    def __init__(self, name, params, flags, ins, locals_):
        self.name = name
        self.params = params
        self.flags = flags
        self.ins = ins
        self.locals = locals_          # declaration order
        self.error = None
        self.body = None

    def local(self, k):
        """Local slot k -> name. Indices are REVERSED: index = (count-1) - decl_position."""
        n = len(self.locals)
        j = n - 1 - k
        if not (0 <= j < n):
            raise DecompileError('local index %d out of range (%d locals)' % (k, n))
        return self.locals[j]


def lift(prog, greedy=False):
    """Program -> [Fn], with every operand resolved back to what it means.

    `greedy` is the assumed inter-function PAD WIDTH in bytes: when set, each function is
    decoded linearly from its export address to `next_export - greedy` instead of trusting
    the transcoder's walk. The pad is not a fixed width in shipped scripts (6 bytes before
    `setfaceroot`'s successor, 4 before `growling`'s), so `decompile()` tries several and
    keeps whichever structures -- see PAD_CANDIDATES.
    """
    text = dict((sym, t) for sym, t, _r in prog.pool.entries)

    def sval(sym):
        t = text.get(sym)
        if t is None:
            raise DecompileError('string symbol %r has no text' % sym)
        return t

    str_at, imp_at = {}, {}
    for sf in prog.stfix:
        for a in sf['sites']:
            str_at[a] = sval(sf['str'])
    for im in prog.imports:
        rec = dict(name=sval(im['name']), ns=(sval(im['ns']) if im['ns'] != 's_null' else ''),
                   params=im['params'], flags=im['flags'])
        for a in im['sites']:
            imp_at[a] = rec

    els = [e for e in prog.body if not isinstance(e, GA.Label)]
    insn = [e for e in els if e.kind == 'insn']
    by_addr = dict((e.addr, k) for k, e in enumerate(insn))

    fns = []
    starts = sorted(set(e['addr'] for e in prog.exports))
    for ex in sorted(prog.exports, key=lambda x: x['addr']):
        lo = by_addr.get(ex['addr'])
        if lo is None:
            fns.append(Fn(sval(ex['name']), ex['params'], ex['flags'], [], []))
            fns[-1].error = 'entry 0x%X is not a decoded instruction' % ex['addr']
            continue
        nxt = [a for a in starts if a > ex['addr']]
        hi = len(insn)
        if nxt:
            # stop at the next entry, and also at the first gap (the inter-function pad)
            hi = by_addr.get(nxt[0], len(insn))
        run = []
        prev_end = None
        for k in range(lo, hi):
            e = insn[k]
            if prev_end is not None and e.addr != prev_end:
                break                       # a raw chunk intervenes: the function ended
            prev_end = e.addr + e._decoded_size
            run.append(e)
        if greedy and prog.console:
            # Ignore the walk entirely: decode straight through to the assumed pad width.
            end = (nxt[0] - greedy) if nxt else prog.cseg_range[1]
            run, o = [], ex['addr']
            while o < end:
                try:
                    e, no = GA._decode_insn(prog.console, o)
                except GA.AsmError:
                    break
                if no > end:
                    break
                run.append(e)
                o = no
        # ⭐⭐ THE WALK'S BREAK RULE CUTS THE LAST INSTRUCTION OF MOST FUNCTIONS.
        # `gsc_diff.swap_cseg` stops as soon as only the next function's 6-byte
        # `{u16, u32 0}` prefix pad can remain: `_align(o,4)+4 >= nxt and d[nxt-4:nxt]==0`.
        # When the final real instruction is a single byte, that test fires BEFORE decoding
        # it. Measured over 80 scripts: the gap between the last decoded instruction and the
        # next entry is 6 in 111 functions and 7 in 696 -- and a gap of 7 is exactly one
        # stranded opcode byte plus the pad. (setfaceroot ended on EvalArrayRef with its
        # SetVariableField in the gap: `28 00 60 00 00 00 00`.) That is harmless for the
        # assembler, which re-emits the gap verbatim as `.raw`, but it silently truncates
        # every function the decompiler sees. Extend the run to the real code end.
        d = prog.console
        if d and run:
            # The prefix pad is exactly 6 bytes wide, so the real code cannot extend past
            # `next_entry - 6`. Deriving the width from "are the last 4 bytes zero" instead
            # let a non-zero pad word set pad=0, and the walk then decoded the pad itself as
            # instructions -- 49 stack underflows and a NET LOSS of 7 points.
            # Only a function that HAS a successor has a 6-byte pad to measure against. The
            # last function of a script runs to the end of the code segment, where the walk
            # already stops on its own; extending there decodes trailing alignment as
            # instructions (SizeOf / VectorScale underflows, measured).
            code_end = (nxt[0] - 6) if nxt else 0
            o = run[-1].addr + run[-1]._decoded_size
            # ⚠ ONLY the stranded tail. The walk can also stop mid-function (its
            # stringtablefixup safety net breaks when an opcode position is a live string
            # operand), and extending from there decodes a long misaligned region as
            # instructions -- measured as 49 stack underflows and a NET LOSS. The measured
            # gap is 6 or 7 bytes, i.e. at most ONE stranded opcode, so bound it.
            if code_end - o > 4:
                code_end = o
            # ⚠ THE BYTES BEFORE THE PAD ARE GARBAGE ALIGNMENT FILL, not code. Measured:
            # `... End | 60 | 53 5e 00 00 00 00` -- the 0x60 is fill, and decoding it as
            # VectorScale underflows the stack. A stranded REAL instruction and a stranded
            # FILL byte are positionally identical (both leave a gap of 7), so position
            # cannot separate them. What separates them is that a function whose tail was
            # truncated does NOT yet end in a terminator: setfaceroot stopped on
            # EvalArrayRef with its SetVariableField stranded, while every false case
            # already ended on End / Return / Jump.
            if run[-1].op in (0x00, 0x01, 0x3F):
                code_end = o
            while o < code_end:
                try:
                    extra, no = GA._decode_insn(d, o)
                except GA.AsmError:
                    break
                if no > code_end:
                    break
                run.append(extra)
                o = no
        ins = [Ins(k, e) for k, e in enumerate(run)]
        base = dict((e.addr, k) for k, e in enumerate(run))

        fn = Fn(sval(ex['name']), ex['params'], ex['flags'], ins, [])
        try:
            _resolve(fn, ins, base, str_at, imp_at, prog)
        except DecompileError as ex2:
            fn.error = str(ex2)
        except Exception as ex2:
            # A function that cannot be lifted must not take the whole script (or a whole
            # boundary candidate) down with it.
            fn.error = '%s: %s' % (type(ex2).__name__, ex2)
        fns.append(fn)
    return fns


def _resolve(fn, ins, base, str_at, imp_at, prog):
    end_addr = (ins[-1].addr + ins[-1].size) if ins else 0
    for it in ins:
        op = it.op
        if op in (0x0A, 0x0B) or op in FIELD_OPS:
            a = it.addr + 1
            a = (a + 1) & ~1
            it.s = str_at.get(a)
            if it.s is None:
                raise DecompileError('%s at 0x%X has no stringtablefixup entry'
                                     % (it.name, it.addr))
        elif op == 0x17:                       # SafeCreateLocalVariables
            names = []
            a = it.addr + 2
            for _k in range(len(it.ops[0])):
                a = (a + 1) & ~1
                nm = str_at.get(a)
                if nm is None:
                    raise DecompileError('local #%d of SafeCreateLocalVariables at 0x%X has '
                                         'no stringtablefixup entry' % (_k, it.addr))
                names.append(nm)
                a += 2
            it.lv = names
            fn.locals = names
        elif op in CALL_OPS or op == 0x15:
            it.imp = imp_at.get(it.addr)
            if it.imp is None:
                raise DecompileError('%s at 0x%X has no import record' % (it.name, it.addr))
        elif op in COND_JUMPS or op in EXPR_JUMPS or op in (0x3F, 0x40, 0x7B):
            r = it.ops[-1]
            if isinstance(r, GA.Ref):
                t = _ref_addr(r)
            else:
                # A greedily re-decoded instruction carries the RAW displacement, because
                # symbolisation happens in the assembler's own anchor pass over its own
                # element list. Resolve it here with the assembler's rule: unsigned first
                # (so nothing that already closes can regress), signed as the fallback.
                after = it.addr + it.size
                if op in GA.FWD_JUMPS:
                    cands = [after + r] + ([after + r - 0x10000] if r & 0x8000 else [])
                else:
                    cands = [after - r]
                t = next((x for x in cands if x in base or x == end_addr), cands[0])
            if t not in base:
                # A jump to the address ONE PAST the last instruction means "fall off the
                # bottom of the function". Treyarch's compiler emits it whenever the last
                # statement is inside an `if` or a `/# #/` that runs to the end, so there is
                # no instruction there to name -- the target is the function's end, which is
                # exactly the exclusive upper bound the structurer already works in.
                if t == end_addr:
                    it.tgt = len(ins)
                else:
                    raise DecompileError('%s at 0x%X targets 0x%X, which is not an '
                                         'instruction in this function'
                                         % (it.name, it.addr, t))
            else:
                it.tgt = base[t]
        elif op == 0x5A:                        # EndSwitch: the case table
            # Layout: opcode, align4, u32 count, count x {u32 value, u32 rel}. A STRING case
            # keeps its reference in the LOW u16 of the value word, and the stringtablefixup
            # records THAT u16's address -- i.e. value_addr + 2 (gsc_codegen fix 12).
            va = ((it.addr + 1 + 3) & ~3) + 4
            cases = []
            for k, (val, ref) in enumerate(it.ops[-1]):
                t = _ref_addr(ref)
                if t not in base:
                    raise DecompileError('switch case %d targets 0x%X, outside the function'
                                         % (k, t))
                cases.append([val, base[t], str_at.get(va + 8 * k + 2)])
            it.cases = cases
    if not fn.locals:
        fn.locals = []


def _ref_addr(r):
    """A Ref produced by the assembler, back to an absolute address.

    A GREEDILY re-decoded instruction carries raw values instead: `_decode_insn` stores a
    switch case target as the absolute address it already computed, so an int is passed
    straight through. Missing this raised AttributeError out of `_resolve`, which is not a
    DecompileError -- so it escaped the per-function guard and killed EVERY pad candidate for
    the whole script, not just the one function.
    """
    if isinstance(r, int):
        return r
    m = GA._ANCHOR_RE.match(r.sym)
    if r.absolute or m is None:
        raise DecompileError('unresolvable reference %r' % r.sym)
    if m.group(2):
        raise DecompileError('reference %r points inside an instruction, which no source '
                             'construct produces' % r.sym)
    return int(m.group(1)[2:], 16)


# =========================================================================== structuring

class _Loop(object):
    __slots__ = ('cont', 'end', 'kind', 'head')

    def __init__(self, cont, end, kind, head=None):
        self.cont, self.end, self.kind = cont, end, kind
        # The header index, so an ALREADY-OPEN loop is not re-detected as a new one. A
        # `for(;;)` and a do/while both start their body AT the header, so block() would
        # otherwise call loop() again for the same index -- and this time with a region that
        # excludes the back edge, which reads as "no matching back-edge inside the region".
        self.head = head


class _Stack(object):
    """The operand stack during reconstruction, plus the statements produced so far."""

    def __init__(self):
        self.v = []
        self.out = []

    def push(self, e):
        self.v.append(e)

    def pop(self, why):
        if not self.v:
            raise DecompileError('stack underflow: %s' % why)
        return self.v.pop()


class Body(object):
    """Recovers the statement tree of ONE function."""

    def __init__(self, fn):
        self.fn = fn
        self.ins = fn.ins
        # Every index that some jump targets, and every BACKWARD jump target (a loop header).
        self.targets = set()
        self.back = set()
        for it in self.ins:
            if it.tgt is not None:
                self.targets.add(it.tgt)
                if it.tgt <= it.idx:
                    self.back.add(it.tgt)

    # ------------------------------------------------------------------ helpers
    def at(self, i):
        return self.ins[i]

    def is_call_stmt_end(self, i):
        return self.ins[i].op == 0x36

    # ------------------------------------------------------------------ blocks
    def block(self, lo, hi, loops):
        out = []
        i = lo
        guard = 0
        while i < hi:
            guard += 1
            if guard > 100000:
                raise DecompileError('structuring did not converge at %d' % i)
            st, i = self.statement(i, hi, loops)
            if st is not None:
                out.extend(st if isinstance(st, list) else [st])
        if i != hi:
            raise DecompileError('a construct ran past its region (%d != %d)' % (i, hi))
        return out

    # ------------------------------------------------------------------ statements
    def statement(self, i, hi, loops):
        if self.ins[i].op == 0x7B:
            return self.devblock(i, hi, loops)
        fe = self.foreach(i, hi, loops)
        if fe is not None:
            return fe
        if i in self.back and not any(l.head == i for l in loops):
            return self.loop(i, hi, loops)
        return self.simple(i, hi, loops)

    def devblock(self, i, hi, loops):
        """`/# ... #/` -- DevblockBegin is a forward jump over the block's own code."""
        end = self.ins[i].tgt
        if end is None or not (i < end <= hi):
            raise DecompileError('DevblockBegin at %d does not bound a block inside the '
                                 'region' % i)
        return (Stmt('devblock', None, self.block(i + 1, end, loops)), end)

    def foreach(self, i, hi, loops):
        """Match gsc_codegen.foreach_stmt WHOLE, prologue included, or return None.

            <coll>  _a=          _a FirstArrayKey  key=
          top:      key IsDefined JumpOnFalse end
                    key _a EvalArray  val=
                    <body>
          cont:     key _a NextArrayKey  key=      Jump top
          end:

        The prologue has to be matched together with the loop: taken separately it decompiles
        to `_a1 = coll; key = _a1 firstarraykey;`, which is not source anyone wrote and does
        not recompile.
        """
        ins = self.ins
        # find the header: the first backward-jump target ahead of us that opens `k IsDefined`
        top = None
        for t in range(i + 6, min(hi, i + 4096)):
            if t in self.back and ins[t].op == 0x19 and t + 2 < hi                     and ins[t + 1].op == 0x5F and ins[t + 2].op in COND_JUMPS:
                top = t
                break
            if t in self.back:
                break
        if top is None or top - 6 < i:
            return None
        pre = ins[top - 6:top]
        if [x.op for x in pre] != [0x27, 0x28, 0x19, 0x70, 0x27, 0x28]:
            return None
        g = top + 2
        end = ins[g].tgt
        bj = self._back_jump(top, hi)
        if bj is None or end != bj + 1 or ins[bj].op != 0x3F:
            return None
        if bj - 5 < top or [x.op for x in ins[bj - 5:bj]] != [0x19, 0x19, 0x71, 0x27, 0x28]:
            return None
        body_lo = g + 1 + 5
        if [x.op for x in ins[g + 1:body_lo]] != [0x19, 0x19, 0x1A, 0x27, 0x28]:
            return None
        try:
            tmp = self.fn.local(pre[0].ops[0])
            key = self.fn.local(pre[4].ops[0])
            val = self.fn.local(ins[g + 4].ops[0])
        except DecompileError:
            return None
        # every slot must name the SAME temp / key, or this is some other loop
        if (self.fn.local(pre[2].ops[0]) != tmp or self.fn.local(ins[top].ops[0]) != key
                or self.fn.local(ins[g + 1].ops[0]) != key
                or self.fn.local(ins[g + 2].ops[0]) != tmp
                or self.fn.local(ins[bj - 5].ops[0]) != key
                or self.fn.local(ins[bj - 4].ops[0]) != tmp
                or self.fn.local(ins[bj - 2].ops[0]) != key):
            return None
        coll = self._pure(i, top - 6, loops)
        if coll is None:
            return None
        cont = bj - 5
        body = self.block(body_lo, cont, loops + [_Loop(cont, end, 'foreach', top)])
        return (Stmt('foreach', (key, val, coll), body), end)

    # ---- loops ----
    def loop(self, i, hi, loops):
        """A loop header: something later jumps back to `i`."""
        bj = self._back_jump(i, hi)
        if bj is None:
            raise DecompileError('index %d is a backward-jump target with no matching '
                                 'back-edge inside the region' % i)
        bji = self.ins[bj]
        if bji.op in COND_JUMPS:                # -> do { } while (cond)
            cond, ci = self.expr_region(i, bj)
            if not COND_JUMPS[bji.op]:          # JumpOnFalse back-edge: the test was negated
                cond = E('!%s' % cond.wrap(P_UNARY), P_UNARY)
            end = bj + 1
            body = self.block(i, ci, loops + [_Loop(ci, end, 'do', i)])
            return (Stmt('dowhile', cond, body), end)
        if bji.op != 0x3F:
            raise DecompileError('back edge at %d is %s, not Jump/JumpOnTrue'
                                 % (bj, bji.name))
        # while / for / foreach: the guard is the first conditional jump out of the loop
        g = self._guard(i, bj)
        if g is None:
            end = bj + 1
            body = self.block(i, bj, loops + [_Loop(i, end, 'for', i)])
            return (Stmt('forever', None, body), end)
        cond, gi = self.expr_region(i, g, want_cond=True)
        if COND_JUMPS[self.ins[g].op]:          # JumpOnTrue guard: `while (!c)`
            cond = E('!%s' % cond.wrap(P_UNARY), P_UNARY)
        end = self.ins[g].tgt
        if end != bj + 1:
            raise DecompileError('loop guard at %d exits to %d, not past the back edge %d'
                                 % (g, end, bj))
        body_lo, body_hi = gi + 1, bj
        step_at = self._step_start(body_lo, body_hi)
        if step_at is not None:
            # `_step_start` picks the last unconditional-jump target inside the body, which is
            # a `continue` in a real `for`, but is ALSO where an `if` at the end of the body
            # joins. Rather than try to tell those apart positionally, attempt the `for` and
            # fall back to the plain `while` with the step as the body's last statement. The
            # fallback is SAFE: if a genuine `continue` did target the step, the while-form
            # gives it a `cont` of the loop top instead, and `_goto` then refuses it rather
            # than emitting a `continue` that would skip the step.
            try:
                step = self.block(step_at, body_hi, loops)
                if len(step) != 1 or not _step_ok(step[0]):
                    raise DecompileError('for-step at %d is not a single expression statement'
                                         % step_at)
                body = self.block(body_lo, step_at, loops + [_Loop(step_at, end, 'for', i)])
                return (Stmt('for', (cond, step[0]), body), end)
            except DecompileError:
                pass
        body = self.block(body_lo, body_hi, loops + [_Loop(i, end, 'while', i)])
        return (Stmt('while', cond, body), end)

    def _back_jump(self, i, hi):
        # A do/while back-edge is JumpOnTrue, or JumpOnFalse when the source wrote
        # `do {} while (!c)` and the compiler folded the negation.
        cands = [it.idx for it in self.ins[i:hi]
                 if it.tgt == i and it.op in (0x3F, 0x3C, 0x3B)]
        return max(cands) if cands else None

    def _guard(self, i, bj):
        """The conditional jump that leaves the loop, if the loop is head-controlled.

        `while (!c)` folds to JumpOnTrue, so both polarities are accepted and the condition
        is negated for the JumpOnTrue form.
        """
        for k in range(i, bj):
            it = self.ins[k]
            if it.op in COND_JUMPS and it.tgt == bj + 1:
                return k
            if it.op in (0x3F, 0x3C, 0x3B) and it.tgt == i:
                break
        return None

    def _step_start(self, lo, hi):
        """A `for` marks `cont` before the step; only a `continue` ever references it."""
        cands = set()
        for it in self.ins[lo:hi]:
            if it.op == 0x3F and it.tgt is not None and lo < it.tgt < hi:
                cands.add(it.tgt)
        return max(cands) if cands else None

    # ---- straight-line statements ----
    def simple(self, i, hi, loops):
        st = _Stack()
        i = self.run(st, i, hi, loops, stop_on_stmt=True)
        if st.v:
            raise DecompileError('%d value(s) left on the stack at %d: %s'
                                 % (len(st.v), i,
                                    ' | '.join(v.txt[:40] for v in st.v)))
        return (st.out or None), i

    def run(self, st, i, hi, loops, stop_on_stmt=False):
        """Execute the stack machine from `i`. Returns the next index."""
        fn = self.fn
        while i < hi:
            it = self.ins[i]
            op = it.op
            if stop_on_stmt and st.out and not st.v and (i in self.targets or op == 0x7B):
                return i
            i += 1
            # ---- constants ----
            if op == 0x02:
                st.push(E('undefined'))
            elif op == 0x03:
                st.push(E('0'))
            elif op == 0x04:
                st.push(E(_num(it.ops[0])))
            elif op == 0x05:
                st.push(E('-%d' % it.ops[0], P_UNARY))
            elif op == 0x06:
                st.push(E(_num(it.ops[0])))
            elif op == 0x07:
                st.push(E('-%d' % it.ops[0], P_UNARY))
            elif op == 0x08:
                v = it.ops[0]
                st.push(E(_num(v - 0x100000000 if v & 0x80000000 else v)))
            elif op == 0x09:
                st.push(E(_flt(it.ops[0])))
            elif op == 0x0A:
                st.push(E(_lit_str(it.s)))
            elif op == 0x0B:
                st.push(E('&' + _lit_str(it.s)))
            elif op == 0x1E:
                st.push(E('[]'))
            elif op == 0x5C:
                st.push(E('_hash_%08x' % it.ops[0]))
            elif op in SPECIAL_VAL:
                st.push(E(SPECIAL_VAL[op]))
            elif op in SPECIAL_OBJ:
                st.push(E(SPECIAL_OBJ[op]))
            # ---- locals / fields / arrays ----
            elif op == 0x19:
                st.push(E(fn.local(it.ops[0])))
            elif op == 0x27:
                st.push(Ref('local', fn.local(it.ops[0])))
            elif op == 0x20:
                o = st.pop('EvalFieldVariable')
                st.push(E('%s.%s' % (o.wrap(90), it.s)))
            elif op == 0x21:
                o = st.pop('EvalFieldVariableRef')
                st.push(Ref('field', '%s.%s' % (o.wrap(90), it.s)))
            elif op == 0x22:
                o = st.pop('ClearFieldVariable')
                st.out.append(Stmt('raw', '%s.%s = undefined;' % (o.wrap(90), it.s), None))
            elif op in (0x1A, 0x1B):
                arr = st.pop('EvalArray array')
                idx = st.pop('EvalArray index')
                st.push(E('%s[%s]' % (arr.wrap(90), idx.txt)))
            elif op == 0x1C:
                arr = st.pop('EvalArrayRef array')
                idx = st.pop('EvalArrayRef index')
                st.push(Ref('index', '%s[%s]' % (arr.wrap(90), idx.txt)))
            elif op == 0x1D:
                arr = st.pop('ClearArray array')
                idx = st.pop('ClearArray index')
                st.out.append(Stmt('raw', '%s[%s] = undefined;'
                                   % (arr.wrap(90), idx.txt), None))
            elif op == 0x53:
                a = st.pop('SizeOf')
                st.push(E('%s.size' % a.wrap(90)))
            elif op == 0x37 or op == 0x38:
                pass                                    # Cast*: transparent to the source
            # ---- operators ----
            elif op in BINOP:
                b = st.pop('binop rhs')
                a = st.pop('binop lhs')
                sym = BINOP[op]
                pr = PREC[sym]
                st.push(E('%s %s %s' % (a.wrap(pr), sym, b.wrap(pr + 1)), pr))
            elif op == 0x39:
                a = st.pop('BoolNot')
                st.push(E('!%s' % a.wrap(P_UNARY), P_UNARY))
            elif op == 0x3A:
                a = st.pop('BoolComplement')
                st.push(E('~%s' % a.wrap(P_UNARY), P_UNARY))
            elif op == 0x5E:
                m = it.ops[0]
                parts = []
                for sh in (4, 2, 0):
                    b = (m >> sh) & 3
                    if b not in VC_BITS:
                        raise DecompileError('VectorConstant mask 0x%02X has an unmodelled '
                                             '2-bit field (%d)' % (m, b))
                    parts.append(VC_BITS[b])
                if m >> 6:
                    raise DecompileError('VectorConstant mask 0x%02X sets bits above 5' % m)
                st.push(E('(%s, %s, %s)' % tuple(parts)))
            elif op == 0x5B:
                # components were pushed RIGHT-TO-LEFT, so x is on top
                a = st.pop('Vector x')
                b = st.pop('Vector y')
                c = st.pop('Vector z')
                st.push(E('(%s, %s, %s)' % (a.txt, b.txt, c.txt)))
            elif op == 0x5F:
                st.push(E('isdefined(%s)' % st.pop('IsDefined').txt))
            elif op == 0x60:
                b = st.pop('VectorScale b')
                a = st.pop('VectorScale a')
                st.push(E('vectorscale(%s, %s)' % (a.txt, b.txt)))
            elif op == 0x69:
                st.push(E('getdvarint(%s)' % st.pop('GetDvarInt').txt))
            elif op in INTRINSIC_OPS:
                nm, arity = INTRINSIC_OPS[op]
                # arguments were pushed LEFT-TO-RIGHT by the intrinsic path in
                # gsc_codegen._emit_call, so pop them back into order
                args = [st.pop('%s arg' % nm) for _ in range(arity)][::-1]
                st.push(E('%s(%s)' % (nm, ', '.join(a.txt for a in args))))
            elif op == 0x2C:
                st.out.append(Stmt('raw', 'waittillframeend;', None))
            # ---- assignment / statements ----
            elif op == 0x28:
                ref = st.pop('SetVariableField target')
                val = st.pop('SetVariableField value')
                if not isinstance(ref, Ref):
                    raise DecompileError('SetVariableField target is not a reference')
                st.out.append(Stmt('raw', '%s = %s;' % (ref.txt, val.txt), None))
            elif op in (0x41, 0x42):
                ref = st.pop('Inc/Dec target')
                if not isinstance(ref, Ref):
                    raise DecompileError('%s target is not a reference' % it.name)
                st.out.append(Stmt('raw', '%s%s;' % (ref.txt, '++' if op == 0x41 else '--'),
                                   None))
            elif op == 0x36:
                st.out.append(Stmt('raw', '%s;' % st.pop('DecTop').txt, None))
            elif op == 0x01:
                st.out.append(Stmt('raw', 'return %s;' % st.pop('Return').txt, None))
            elif op == 0x00:
                st.out.append(Stmt('raw', 'return;', None))
            elif op == 0x2B:
                st.out.append(Stmt('raw', 'wait %s;' % st.pop('Wait').txt, None))
            elif op == 0x58:
                # VoidCodePos marks the start of notify's variadic argument list. It is NOT
                # `waittillframeend` -- that is its own opcode, 0x2C (mined against gsc-tool).
                st.push(E('\x00void'))
            elif op == 0x17 or op == 0x26:
                pass                                    # function prologue
            # ---- calls ----
            elif op in CALL_OPS:
                self._call(st, it)
            elif op in PTR_CALL_OPS:
                self._ptr_call(st, it)
            elif op == 0x2D:
                st.push(E('\x00pre'))
            elif op == 0x15:
                imp = it.imp
                st.push(E('%s::%s' % (imp['ns'].replace('/', '\\'), imp['name'])
                          if imp['ns'] else '::%s' % imp['name']))
            elif op == 0x55:
                i = self._waittill(st, it, i, hi)
            elif op == 0x56:
                self._notify(st, it)
            elif op == 0x57:
                self._endon(st, it)
            # ---- control flow ----
            elif op in EXPR_JUMPS:
                a = st.pop('short-circuit lhs')
                sub = _Stack()
                j = self.run(sub, i, it.tgt, [])
                if j != it.tgt or len(sub.v) != 1 or sub.out:
                    raise DecompileError('%s at %d does not bound a pure expression'
                                         % (it.name, it.idx))
                pr = PREC[EXPR_JUMPS[op]]
                st.push(E('%s %s %s'
                          % (a.wrap(pr), EXPR_JUMPS[op], sub.v[0].wrap(pr + 1)), pr))
                i = it.tgt
            elif op in COND_JUMPS:
                i = self._conditional(st, it, i, hi, loops)
            elif op == 0x3F:
                i = self._goto(st, it, i, hi, loops)
            elif op == 0x59:
                i = self._switch(st, it, i, hi, loops)
            elif op == 0x5A:
                raise DecompileError('EndSwitch at %d reached outside a switch' % it.idx)
            else:
                raise DecompileError('no source construct is known for %s (0x%02X) at 0x%X'
                                     % (it.name, op, it.addr))
            if stop_on_stmt and st.out and not st.v:
                return i
        return i

    def _notify_ahead(self, i, hi):
        for k in range(i, min(hi, i + 24)):
            if self.ins[k].op == 0x56:
                return True
            if self.ins[k].op in (0x36, 0x28, 0x00, 0x01):
                return False
        return False

    # ------------------------------------------------------------------ calls
    def _take_args(self, st, argc, method):
        """Arguments were pushed RIGHT-TO-LEFT, and a method target AFTER them."""
        target = st.pop('call target') if method else None
        args = [st.pop('call arg %d' % k) for k in range(argc)]
        pre = st.pop('PreScriptCall marker')
        if pre.txt != '\x00pre':
            raise DecompileError('call is not bracketed by PreScriptCall')
        return target, args

    def _call(self, st, it):
        _k, method, threaded = CALL_OPS[it.op]
        imp = it.imp
        target, args = self._take_args(st, imp['params'], method)
        ns = imp['ns'].replace('/', '\\')
        callee = ('%s::%s' % (ns, imp['name'])) if ns else imp['name']
        txt = '%s(%s)' % (callee, ', '.join(a.txt for a in args))
        if threaded:
            txt = 'thread ' + txt
        if target is not None:
            txt = '%s %s' % (target.wrap(90), txt)
        st.push(E(txt, 90))

    def _ptr_call(self, st, it):
        method, threaded = PTR_CALL_OPS[it.op]
        argc = it.ops[0]
        ptr = st.pop('pointer-call callee')
        target, args = self._take_args(st, argc, method)
        txt = '[[%s]](%s)' % (ptr.txt, ', '.join(a.txt for a in args))
        if threaded:
            txt = 'thread ' + txt
        if target is not None:
            txt = '%s %s' % (target.wrap(90), txt)
        st.push(E(txt, 90))

    def _endon(self, st, it):
        target = st.pop('endon target')
        ev = st.pop('endon event')
        st.out.append(Stmt('raw', '%s endon(%s);' % (target.wrap(90), ev.txt), None))

    def _notify(self, st, it):
        target = st.pop('notify target')
        args = []
        while True:
            v = st.pop('notify argument')
            if v.txt == '\x00void':
                break
            args.append(v)
        st.out.append(Stmt('raw', '%s notify(%s);'
                           % (target.wrap(90), ', '.join(a.txt for a in args)), None))

    def _waittill(self, st, it, i, hi):
        """WaitTill, then one SafeSetWaittillVariableFieldCached per extra name, then
        ClearParams -- exactly gsc_codegen._emit_builtin."""
        target = st.pop('waittill target')
        ev = st.pop('waittill event')
        names = []
        while i < hi and self.ins[i].op == 0x24:
            names.append(self.fn.local(self.ins[i].ops[0]))
            i += 1
        if i >= hi or self.ins[i].op != 0x25:
            raise DecompileError('waittill at %d is not terminated by ClearParams' % it.idx)
        i += 1
        parts = [ev.txt] + names
        st.out.append(Stmt('raw', '%s waittill(%s);'
                           % (target.wrap(90), ', '.join(parts)), None))
        return i

    # ------------------------------------------------------------------ control flow
    def _goto(self, st, it, i, hi, loops):
        if st.v:
            raise DecompileError('unconditional Jump at %d with a non-empty stack' % it.idx)
        if loops:
            # `break` leaves the innermost breakable construct -- a loop OR a switch.
            if it.tgt == loops[-1].end:
                st.out.append(Stmt('raw', 'break;', None))
                return i
            # `continue` belongs to the innermost real LOOP: a switch is breakable but not
            # continuable, so it has to be looked through. Treating the switch as the
            # continue target made every `continue` inside a `switch` unstructurable.
            for lp in reversed(loops):
                if lp.kind == 'switch':
                    continue
                if it.tgt == lp.cont:
                    st.out.append(Stmt('raw', 'continue;', None))
                    return i
                break
            # A `break` for an outer loop can also appear once a switch is open.
            for lp in reversed(loops[:-1]):
                if it.tgt == lp.end and lp.kind != 'switch':
                    st.out.append(Stmt('raw', 'break;', None))
                    return i
                break
        raise DecompileError('Jump at %d to %d is neither break nor continue; the control '
                             'flow is not reducible to a source construct' % (it.idx, it.tgt))

    def _conditional(self, st, it, i, hi, loops):
        """A conditional jump: an if / if-else statement, or a ternary in an expression.

        MEASURED: Treyarch's compiler FOLDS `if (!c)` into `JumpOnTrue`, where ours emits
        `BoolNot; JumpOnFalse`. Both shapes appear in the corpus, so both are read here and
        the negated one is written back as `if (!c)` -- see the matching peephole in
        `gsc_codegen.if_stmt`, without which the recompiled stream would gain a BoolNot.
        """
        cond = st.pop('condition')
        if COND_JUMPS[it.op]:                       # JumpOnTrue: the source tested !cond
            cond = E('!%s' % cond.wrap(P_UNARY), P_UNARY)
        T = it.tgt
        if not (i <= T <= hi):
            raise DecompileError('JumpOnFalse at %d exits its region (to %d, region ends %d)'
                                 % (it.idx, T, hi))
        then_hi, else_lo, else_hi = T, None, None
        j = T - 1
        if j >= i and self.ins[j].op == 0x3F and self.ins[j].tgt is not None \
                and T < self.ins[j].tgt <= hi and not self._is_loop_exit(self.ins[j], loops):
            then_hi, else_lo, else_hi = j, T, self.ins[j].tgt

        if not st.v:
            tern = self._try_ternary(i, then_hi, else_lo, else_hi, loops)
            if tern is not None:
                st.push(E('%s ? %s : %s' % (cond.wrap(P_TERNARY + 1), tern[0].wrap(P_TERNARY + 1),
                             tern[1].wrap(P_TERNARY + 1)), P_TERNARY))
                return else_hi
        elif st.v:
            tern = self._try_ternary(i, then_hi, else_lo, else_hi, loops)
            if tern is None:
                raise DecompileError('JumpOnFalse at %d sits inside an expression but is not '
                                     'a ternary' % it.idx)
            st.push(E('%s ? %s : %s' % (cond.wrap(P_TERNARY + 1), tern[0].wrap(P_TERNARY + 1),
                         tern[1].wrap(P_TERNARY + 1)), P_TERNARY))
            return else_hi

        then = self.block(i, then_hi, loops)
        els = self.block(else_lo, else_hi, loops) if else_lo is not None else None
        st.out.append(Stmt('if', cond, then, els))
        return else_hi if else_lo is not None else T

    def _is_loop_exit(self, ins, loops):
        return bool(loops) and ins.tgt in (loops[-1].end, loops[-1].cont)

    def _try_ternary(self, i, then_hi, else_lo, else_hi, loops):
        if else_lo is None:
            return None
        a = self._pure(i, then_hi, loops)
        if a is None:
            return None
        b = self._pure(else_lo, else_hi, loops)
        if b is None:
            return None
        return (a, b)

    def _pure(self, lo, hi, loops):
        """The region yields exactly one value and no statements, or None."""
        if lo >= hi:
            return None
        sub = _Stack()
        try:
            j = self.run(sub, lo, hi, loops)
        except DecompileError:
            return None
        if j != hi or sub.out or len(sub.v) != 1:
            return None
        return sub.v[0]

    def expr_region(self, lo, hi, want_cond=False):
        """Evaluate [lo, hi) as a single expression; returns (expr, index_of_hi)."""
        sub = _Stack()
        j = self.run(sub, lo, hi, [])
        if j != hi or sub.out or len(sub.v) != 1:
            raise DecompileError('region %d..%d is not a single expression '
                                 '(%d stmt(s), %d value(s))' % (lo, hi, len(sub.out),
                                                                len(sub.v)))
        return sub.v[0], hi

    def _switch(self, st, it, i, hi, loops):
        subj = st.pop('switch subject')
        depth, es = 0, None
        for k in range(i, hi):
            if self.ins[k].op == 0x59:
                depth += 1
            elif self.ins[k].op == 0x5A:
                if depth == 0:
                    es = k
                    break
                depth -= 1
        if es is None:
            raise DecompileError('Switch at %d has no matching EndSwitch in the region'
                                 % it.idx)
        end = es + 1
        cases = self.ins[es].cases
        if not cases:
            raise DecompileError('Switch at %d has an empty case table' % it.idx)
        # The table is written in BODY order (gsc_codegen.switch_stmt), so the bodies are the
        # gaps between consecutive targets. A non-monotonic table is not a shape any source
        # produces, so it is refused rather than sorted into something plausible.
        # The table is USUALLY written in body order, but not always -- a case whose body is
        # shared or empty can point backwards. Grouping by TARGET and ordering the groups by
        # target is correct either way: the bodies are the gaps between consecutive distinct
        # targets, and the labels on a body are every case that points at it, kept in table
        # (source) order.
        order = sorted(set(c[1] for c in cases))
        groups = []
        for t in order:
            groups.append([[], t])
        pos = dict((t, k) for k, t in enumerate(order))
        for k, (val, t, s) in enumerate(cases):
            lbl = ('default' if val == 0 else
                   'case %d' % (val & 0x7FFFFF) if val & 0x00800000 else
                   'case %s' % _lit_str(s if s is not None else ''))
            if val == 0 and s is None and not (val & 0x00800000):
                lbl = 'default'
            groups[pos[t]][0].append(lbl)
        inner = loops + [_Loop(end, end, 'switch')]
        body = []
        for k, (lbls, t) in enumerate(groups):
            stop = groups[k + 1][1] if k + 1 < len(groups) else es
            body.append((lbls, self.block(t, stop, inner)))
        st.out.append(Stmt('switch', subj, body))
        return end


# =========================================================================== statements

class Ref(E):
    """An assignable location (the operand of SetVariableField / Inc / Dec)."""
    __slots__ = ('rk',)

    def __init__(self, rk, txt):
        E.__init__(self, txt, 90)
        self.rk = rk


class Stmt(object):
    __slots__ = ('kind', 'a', 'b', 'c')

    def __init__(self, kind, a, b, c=None):
        self.kind, self.a, self.b, self.c = kind, a, b, c


def _render(stmts, ind, out):
    pad = '    ' * ind
    for s in stmts:
        k = s.kind
        if k == 'raw':
            out.append(pad + s.a)
        elif k == 'if':
            out.append('%sif (%s)' % (pad, s.a.txt))
            _brace(s.b, ind, out)
            if s.c is not None:
                out.append(pad + 'else')
                _brace(s.c, ind, out)
        elif k == 'while':
            out.append('%swhile (%s)' % (pad, s.a.txt))
            _brace(s.b, ind, out)
        elif k == 'forever':
            out.append('%sfor (;;)' % pad)
            _brace(s.b, ind, out)
        elif k == 'for':
            cond, step = s.a
            out.append('%sfor (; %s; %s)' % (pad, cond.txt, _inline(step)))
            _brace(s.b, ind, out)
        elif k == 'dowhile':
            out.append('%sdo' % pad)
            _brace(s.b, ind, out)
            out.append('%swhile (%s);' % (pad, s.a.txt))
        elif k == 'foreach':
            key, val, coll = s.a
            head = ('%s, %s' % (key, val)) if not key.startswith('_k') else val
            out.append('%sforeach (%s in %s)' % (pad, head, coll.txt))
            _brace(s.b, ind, out)
        elif k == 'devblock':
            out.append(pad + '/#')
            _render(s.b, ind + 1, out)
            out.append(pad + '#/')
        elif k == 'switch':
            out.append('%sswitch (%s)' % (pad, s.a.txt))
            out.append(pad + '{')
            for lbls, body in s.b:
                for l in lbls:
                    out.append('%s%s:' % ('    ' * (ind + 1), l))
                _render(body, ind + 2, out)
            out.append(pad + '}')
        else:
            raise DecompileError('cannot render statement %r' % k)


def _brace(body, ind, out):
    pad = '    ' * ind
    out.append(pad + '{')
    _render(body, ind + 1, out)
    out.append(pad + '}')


# `wait`, `return`, `break` and friends are STATEMENT KEYWORDS: the GSC grammar does not
# accept them in a for-step, so a loop whose step is one of those is written as a `while`
# with the step as the last statement of the body instead.
_STEP_KW = ('wait ', 'return', 'break', 'continue', 'waittillframeend')


def _step_ok(stmt):
    return stmt.kind == 'raw' and not stmt.a.startswith(_STEP_KW)


def _inline(stmt):
    if not _step_ok(stmt):
        raise DecompileError('a for-step must be a simple expression statement')
    return stmt.a.rstrip(';')


# =========================================================================== driver

# Inter-function pad widths to try, in order. 6 is the documented `{u16, u32 0}` prefix; 4 is
# the bare zero word; the others cover the 0-3 bytes of alignment fill that can sit in front
# of either. Each is validated by actually structuring the function.
PAD_CANDIDATES = (6, 4, 5, 7, 8, 9)


def decompile_function(fn, alt=None):
    """-> (source_lines, error_or_None). Never returns plausible source for a refusal.

    `alt` is the same function lifted the other way (see `lift(greedy=...)`); if the primary
    lift does not structure, the alternative is tried before giving up. Whichever one
    succeeds is a complete, self-consistent decode of the function -- a bad boundary shows up
    as a refusal, never as source.
    """
    lines, err = _decompile_one(fn)
    if not err:
        return lines, None
    for cand in ([alt] if alt is not None and not isinstance(alt, list) else (alt or [])):
        if cand is None or cand.error:
            continue
        lines2, err2 = _decompile_one(cand)
        if not err2:
            return lines2, None
    return lines, err


def _decompile_one(fn):
    if fn.error:
        return None, fn.error
    try:
        b = Body(fn)
        # The prologue (SafeCreateLocalVariables / CheckClearParams) is not a statement.
        lo = 0
        while lo < len(fn.ins) and fn.ins[lo].op in (0x17, 0x26):
            lo += 1
        stmts = b.block(lo, len(fn.ins), [])
    except DecompileError as ex:
        return None, str(ex)
    except RecursionError:
        return None, 'control flow nests too deeply to structure'
    # ⚠ DO NOT STRIP A TRAILING `return;`. gsc_codegen only re-adds an implicit End when the
    # body does NOT already terminate, so dropping an explicit bare return loses the End the
    # original had. The reverse case -- Treyarch omitting the End entirely when control falls
    # off the bottom -- is handled by the gate, which forgives exactly one appended End.
    out = []
    params = fn.locals[:fn.params]
    out.append('%s(%s)' % (fn.name, ', '.join(params)))
    out.append('{')
    try:
        _render(stmts, 1, out)
    except DecompileError as ex:
        return None, str(ex)
    out.append('}')
    return out, None


def best_lift(prog):
    """[Fn] using, per function, the boundary candidate that actually structures.

    The walk-based lift truncates most functions by one instruction and can stop early inside
    one, so it is the wrong body to decompile AND the wrong baseline to measure against. Both
    `decompile()` and the C1 gate go through here, so they judge the same instruction stream.
    """
    fns = lift(prog)
    alts = []
    for pad in PAD_CANDIDATES:
        try:
            alts.append(dict((f.name, f) for f in lift(prog, greedy=pad)))
        except Exception:
            pass
    out = []
    for fn in fns:
        if not fn.error and not _decompile_one(fn)[1]:
            out.append(fn)
            continue
        pick = fn
        for a in alts:
            cand = a.get(fn.name)
            if cand is not None and not cand.error and not _decompile_one(cand)[1]:
                pick = cand
                break
        out.append(pick)
    return out


def decompile(blob, name='<asset>'):
    """Compiled GSC -> (source_text, report).

    `report` is {'functions': n, 'ok': n, 'refused': [(fname, reason), ...]}. A refused
    function appears in the source as a commented-out stub carrying its reason, so the output
    is never silently incomplete.
    """
    prog = GA.parse(blob)
    text = dict((sym, t) for sym, t, _r in prog.pool.entries)
    fns = best_lift(prog)
    alts = []
    L = []
    script = text.get(prog.name_sym) or ''
    L.append('// decompiled from %s' % (script or name))
    L.append('// core.gsc_decompile -- verify with core.gsc_asm_selftest gate C1')
    L.append('')
    for sym in prog.include:
        inc = text.get(sym) or ''
        L.append('#include %s;' % inc.replace('/', '\\'))
    for at in prog.animtrees:
        L.append('#using_animtree("%s");' % (text.get(at['name']) or ''))
    if prog.include or prog.animtrees:
        L.append('')
    refused = []
    ok = 0
    for fn in fns:
        lines, err = decompile_function(fn, [a[fn.name] for a in alts if fn.name in a])
        if err:
            refused.append((fn.name, err))
            L.append('/* REFUSED: %s(%s) -- %s */'
                     % (fn.name, ', '.join(fn.locals[:fn.params]), err))
            L.append('')
            continue
        ok += 1
        L.extend(lines)
        L.append('')
    return '\n'.join(L) + '\n', {'functions': len(fns), 'ok': ok, 'refused': refused}
