"""core.hks_codegen -- Lua 5.1 AST -> HavokScript bytecode (back half of the HKS compiler).

`core.lua_lang` parses; this emits. Instruction encoding comes from `core.hks_dis`, whose field
layout was solved against hksc's own listing, and opcode numbers from `core/_opcodes_hks.py`.

REGISTER MODEL
--------------
Standard Lua: a per-function register file where locals occupy the low registers in declaration
order and temporaries are allocated above them (`freereg`). An expression is compiled *into* a
register; `rk()` lets an operand be a constant index instead, which is what the `_BK` opcode
variants and the RK bit (C & 0x100) exist for.

WHAT IS EMITTED
---------------
locals / assignment / globals / table constructors / field and index access / arithmetic,
comparison, logical and concat operators / if-elseif-else / while / repeat / numeric for /
generic for / function definitions and closures / method definitions and calls / varargs /
multiple assignment and multiple return / break.

Anything outside that RAISES. A compiler that silently emits plausible-but-wrong bytecode is
worse than one that refuses -- and on this platform it is much worse than for GSC, because
`LUI_Interface_Error` is a `blr` stub, so a miscompiled chunk fails SILENTLY with no diagnostic.

STATUS
------
  core/_hks_ladder.py    49/49 constructs accepted by hksc's validating loader
  core/hks_gauntlet.py   15/15 sources accepted; 3 match hksc's instruction stream exactly

hksc's loader checks registers, jump targets, constant and proto indices, so "accepted" means
the bytecode is structurally valid -- not merely parseable. The remaining ACCEPTED-but-not-
identical cases differ only in INSTRUCTION SELECTION (hksc picks more _R1/_BK/GETTABLE_S forms
and fewer MOVEs); the semantics are the same.

Eight defects were found by that harness, each recorded at its call site:
  1. `vararg` is a FLAG field -- hksc writes 2 (ISVARARG), not a boolean 1
  2. the 14-byte header + 13-entry type table is a fixed 238-byte prefix, not synthesisable
  3. instruction arrays align on the ABSOLUTE FILE offset, not the proto start
  4. the proto footer's first word is 1, not 0 -- zero is rejected once a sub-proto exists
  5. CLOSURE is iABx (the format miner mis-classified it as iABC)
  6. GETFIELD / SELF take a PLAIN constant index; a constant table key needs SETTABLE_S_BK,
     because SETTABLE_S reads B as a register and a constant index there is out of range
  7. SETLIST comes LAST in a table constructor, after the hash entries
  8. UPVALUES: CLOSURE is followed by one DATA per upvalue -- A=1 captures the parent's local
     register B, A=2 forwards the parent's upvalue B. Without them every upvalue is unbound,
     and a captured local silently compiled as a GLOBAL.

⚠ NOTHING FROM THIS MODULE HAS BEEN BOOT-TESTED.
"""
import os
import struct

from . import paths  # noqa: F401
from . import hks_dis as D
from ._opcodes_hks import MNEMONIC

OP = {v: k for k, v in MNEMONIC.items()}

T_NIL, T_BOOL, T_NUMBER, T_STRING = 0, 1, 3, 4
MAGIC = b'\x1bLua'
RK_BIT = 0x100
MAXREG = 250


class HksCompileError(Exception):
    pass


class Const(object):
    __slots__ = ('t', 'v')

    def __init__(self, t, v):
        self.t, self.v = t, v

    def key(self):
        return (self.t, self.v)


class FuncState(object):
    def __init__(self, parent=None, params=(), vararg=False, name='?'):
        self.parent = parent
        self.name = name
        self.params = list(params)
        self.vararg = vararg
        self.code = []                 # instruction words
        self.consts = []
        self.constmap = {}
        self.protos = []
        self.actives = []              # list of (name, reg) currently in scope
        self.blocks = []               # break-patch lists
        self.freereg = len(self.params)
        self.maxreg = max(2, self.freereg)
        self.upvals = []
        for i, p in enumerate(self.params):
            self.actives.append((p, i))

    # -- registers --------------------------------------------------------
    def reserve(self, n=1):
        r = self.freereg
        self.freereg += n
        if self.freereg > MAXREG:
            raise HksCompileError('out of registers in %s()' % self.name)
        self.maxreg = max(self.maxreg, self.freereg)
        return r

    def free_to(self, n):
        self.freereg = n

    # -- constants --------------------------------------------------------
    def k(self, t, v):
        key = (t, v)
        i = self.constmap.get(key)
        if i is None:
            i = len(self.consts)
            self.consts.append(Const(t, v))
            self.constmap[key] = i
        return i

    def kstr(self, s):
        return self.k(T_STRING, s)

    def knum(self, x):
        return self.k(T_NUMBER, float(x))

    def rk_const(self, i):
        """A constant index about to be used as an RK operand.

        ⛔ AN RK CONSTANT INDEX ONLY HAS 8 BITS. C is 9 bits and bit 8 IS the RK flag, so
        `index | RK_BIT` is only reversible while index <= 255. At 256 the index's own bit 8
        collides with the flag and the instruction silently reads a DIFFERENT constant --
        index 300 decodes as constant 44, in a chunk that passes every structural check because
        44 is a perfectly valid index.

        The real fix is to spill the constant to a register with LOADK and use the register
        form, which this codegen does not do yet. Until it does, refusing is the only honest
        option: a wrong constant is a wrong script that looks correct.
        """
        if i > 0xFF:
            raise HksCompileError(
                'constant index %d cannot be used as an RK operand (limit 255): this function '
                'has %d constants. The compiler does not yet spill to a register, so it refuses '
                'rather than silently encode constant %d.' % (i, len(self.consts), i & 0xFF))
        return i

    # -- emit -------------------------------------------------------------
    def emit(self, mnem, a=0, b=0, c=0):
        if mnem not in OP:
            raise HksCompileError('opcode %r is not in the mined table' % mnem)
        self.code.append(D.encode(OP[mnem], a, b, c))
        return len(self.code) - 1

    def emit_bx(self, mnem, a, bx):
        self.code.append(D.encode_bx(OP[mnem], a, bx))
        return len(self.code) - 1

    def emit_sbx(self, mnem, a, sbx):
        self.code.append(D.encode_sbx(OP[mnem], a, sbx))
        return len(self.code) - 1

    def jump(self, mnem='JMP', a=0):
        return self.emit_sbx(mnem, a, 0)

    def here(self):
        return len(self.code)

    def patch(self, at, target=None):
        """Point a jump at `target` (default: here). sBx is relative to the NEXT instruction."""
        if at is None:
            return
        tgt = self.here() if target is None else target
        w = self.code[at]
        op = (w >> D.OP_SHIFT) & D.OP_MASK
        a = w & 0xFF
        self.code[at] = D.encode_sbx(op, a, tgt - (at + 1))

    def patch_list(self, lst, target=None):
        for j in lst:
            self.patch(j, target)

    # -- scopes -----------------------------------------------------------
    def local_reg(self, name):
        for nm, r in reversed(self.actives):
            if nm == name:
                return r
        return None

    def declare(self, name, reg):
        self.actives.append((name, reg))

    def upval_index(self, name):
        """Resolve `name` as an upvalue, creating the capture chain. None if not found.

        Each entry is (name, from_parent_local, index): from_parent_local True means the parent
        captures its own register `index`; False means it forwards its own upvalue `index`.
        """
        for i, (nm, _fl, _ix) in enumerate(self.upvals):
            if nm == name:
                return i
        if self.parent is None:
            return None
        r = self.parent.local_reg(name)
        if r is not None:
            self.upvals.append((name, True, r))
            return len(self.upvals) - 1
        pu = self.parent.upval_index(name)
        if pu is None:
            return None
        self.upvals.append((name, False, pu))
        return len(self.upvals) - 1

    def scope_mark(self):
        return (len(self.actives), self.freereg)

    def scope_close(self, mark):
        n, fr = mark
        del self.actives[n:]
        self.freereg = fr


# ---------------------------------------------------------------------------- compiler

class Compiler(object):
    def __init__(self):
        self.fs = None

    # -- expressions ------------------------------------------------------
    def expr_to_reg(self, e, reg=None):
        """Compile `e` so its value lands in a register; returns that register."""
        fs = self.fs
        k = e.k
        if reg is None:
            reg = fs.reserve()
        # ⚠ The destination must be OWNED before compiling sub-expressions, or their temporaries
        # allocate over it. `local t = {1,...,33}` compiled the table into r0 while freereg was
        # still 0, so the first array element was handed r0 as well -- and `maxreg` came out one
        # short (33 where hksc declares 34), which the loader rejects.
        fs.freereg = max(fs.freereg, reg + 1)
        fs.maxreg = max(fs.maxreg, fs.freereg)
        if k == 'num':
            fs.emit_bx('LOADK', reg, fs.knum(_num(e.a)))
        elif k == 'str':
            fs.emit_bx('LOADK', reg, fs.kstr(e.a))
        elif k == 'nil':
            fs.emit('LOADNIL', reg, reg, 0)
        elif k in ('true', 'false'):
            fs.emit('LOADBOOL', reg, 1 if k == 'true' else 0, 0)
        elif k == 'vararg':
            fs.emit('VARARG', reg, 2, 0)
        elif k == 'paren':
            self.expr_to_reg(e.a, reg)
        elif k == 'name':
            src = fs.local_reg(e.a)
            if src is not None:
                if src != reg:
                    fs.emit('MOVE', reg, src, 0)
            else:
                uv = fs.upval_index(e.a)
                if uv is not None:
                    fs.emit('GETUPVAL', reg, uv, 0)
                else:
                    fs.emit_bx('GETGLOBAL', reg, fs.kstr(e.a))
        elif k == 'index':
            obj = self.expr_to_reg(e.a)
            key = e.b
            if key.k == 'str':
                # GETFIELD C is a PLAIN constant index -- no RK bit. Setting 0x100 pushes it
                # out of range and the loader rejects the chunk. (Confirmed by hksc's own
                # listings: `GETFIELD 0 0 3` / `SETFIELD_R1 2 2 1 ; "x" -`, where the key is a
                # bare constant index and the remaining operand is a register.)
                fs.emit('GETFIELD', reg, obj, fs.kstr(key.a))
            else:
                kr = self.rk(key)
                fs.emit('GETTABLE_S', reg, obj, kr)
            fs.free_to(max(reg + 1, fs.freereg if obj < reg else obj))
            fs.freereg = max(reg + 1, min(fs.freereg, obj))
            fs.freereg = reg + 1
        elif k == 'binop':
            self.binop(e, reg)
        elif k == 'unop':
            self.unop(e, reg)
        elif k == 'table':
            self.table(e, reg)
        elif k == 'function':
            self.closure(e, reg)
        elif k in ('call', 'methcall'):
            self.call(e, reg, nresults=1)
        else:
            raise HksCompileError('unsupported expression %r (line %d)' % (k, e.line))
        fs.freereg = max(fs.freereg, reg + 1)
        return reg

    def rk(self, e):
        """Return an RK operand: a constant index with the RK bit, or a register.

        A local variable is used IN PLACE. Copying it to a fresh temporary first is valid but
        emits a redundant MOVE before every operator, which is the bulk of the divergence from
        hksc's instruction stream (`local b = a + 2` became MOVE;ADD instead of a single ADD).
        """
        fs = self.fs
        if e.k == 'str':
            return fs.rk_const(fs.kstr(e.a)) | RK_BIT
        if e.k == 'num':
            return fs.rk_const(fs.knum(_num(e.a))) | RK_BIT
        if e.k == 'name':
            r = fs.local_reg(e.a)
            if r is not None:
                return r
        if e.k == 'paren' and e.a.k == 'name':
            r = fs.local_reg(e.a.a)
            if r is not None:
                return r
        return self.expr_to_reg(e)

    def binop(self, e, reg):
        fs = self.fs
        op, (lhs, rhs) = e.a, e.b
        arith = {'+': 'ADD', '-': 'SUB', '*': 'MUL', '/': 'DIV', '%': 'MOD', '^': 'POW'}
        if op in arith:
            base = fs.freereg
            b = self.rk(lhs)
            c = self.rk(rhs)
            # _BK variants put the CONSTANT on the left; use them when lhs is constant
            if (b & RK_BIT) and not (c & RK_BIT):
                fs.emit(arith[op] + '_BK', reg, b & 0xFF, c)
            else:
                fs.emit(arith[op], reg, b if b < 256 else b & 0xFF, c)
            fs.free_to(base)
            fs.freereg = max(base, reg + 1)
            return
        if op == '..':
            base = fs.freereg
            parts = _concat_chain(e)
            first = fs.freereg
            for p in parts:
                self.expr_to_reg(p, fs.reserve())
            fs.emit('CONCAT', reg, first, first + len(parts) - 1)
            fs.free_to(base)
            fs.freereg = max(base, reg + 1)
            return
        if op in ('==', '~=', '<', '<=', '>', '>='):
            self.compare_to_reg(e, reg)
            return
        if op in ('and', 'or'):
            self.expr_to_reg(lhs, reg)
            j = fs.emit('TESTSET', reg, reg, 0 if op == 'and' else 1)
            jj = fs.jump()
            self.expr_to_reg(rhs, reg)
            fs.patch(jj)
            del j
            return
        raise HksCompileError('unsupported operator %r (line %d)' % (op, e.line))

    def compare_to_reg(self, e, reg):
        """Materialise a comparison as a boolean in `reg`."""
        fs = self.fs
        self.cond_jump(e, invert=True)
        j_false = fs.jump()
        fs.emit('LOADBOOL', reg, 1, 1)
        fs.patch(j_false)
        fs.emit('LOADBOOL', reg, 0, 0)

    def cond_jump(self, e, invert=False):
        """Emit a test whose following JMP is taken when the condition is `invert`."""
        fs = self.fs
        cmpmap = {'==': ('EQ', False), '~=': ('EQ', True),
                  '<': ('LT', False), '<=': ('LE', False),
                  '>': ('LT', True), '>=': ('LE', True)}
        if e.k == 'binop' and e.a in cmpmap:
            mnem, swap = cmpmap[e.a]
            lhs, rhs = e.b
            if e.a in ('>', '>='):
                lhs, rhs = rhs, lhs
                swap = False
            base = fs.freereg
            b = self.rk(lhs)
            c = self.rk(rhs)
            want = 0 if (e.a == '~=') != invert else 1
            if e.a in ('==', '~='):
                want = 1 if invert == (e.a == '~=') else 0
            else:
                want = 0 if invert else 1
            if (b & RK_BIT) and not (c & RK_BIT):
                fs.emit(mnem + '_BK', want, b & 0xFF, c)
            else:
                fs.emit(mnem, want, b, c)
            fs.free_to(base)
            return
        base = fs.freereg
        r = self.expr_to_reg(e)
        fs.emit('TEST', r, 0, 0 if invert else 1)
        fs.free_to(base)

    def unop(self, e, reg):
        fs = self.fs
        base = fs.freereg
        r = self.expr_to_reg(e.b)
        fs.emit({'-': 'UNM', 'not': 'NOT', '#': 'LEN'}[e.a], reg, r, 0)
        fs.free_to(base)
        fs.freereg = max(base, reg + 1)

    def table(self, e, reg):
        """Table constructor.

        Ordering and operand forms follow hksc exactly:
            NEWTABLE ; <array items into consecutive regs> ; <hash entries> ; SETLIST

        ⚠ SETLIST comes LAST, after the hash entries -- it consumes the pending array items off
        the top of the stack, so emitting it early leaves them stale.
        ⚠ A CONSTANT key needs the _BK form. `SETTABLE_S` takes B as a REGISTER; passing a
        constant index there (`[3] = 4` -> `SETTABLE_S 0 3 ...`) reads as register 3, which is
        out of range and the loader rejects the chunk. hksc emits `SETTABLE_S_BK 0 3 -5`, where
        B is the constant INDEX of the key.
        """
        fs = self.fs
        array, hash_ = e.a, e.b
        fs.emit('NEWTABLE', reg, len(array), len(hash_))
        base = fs.freereg
        for item in array:
            self.expr_to_reg(item, fs.reserve())
        after_array = fs.freereg
        for kx, vx in hash_:
            b0 = fs.freereg
            if kx.k == 'str':
                vr = self.rk(vx)
                fs.emit('SETFIELD', reg, fs.kstr(kx.a), vr)
            elif kx.k == 'num':
                vr = self.rk(vx)
                fs.emit('SETTABLE_S_BK', reg, fs.knum(_num(kx.a)), vr)
            else:
                kr = self.expr_to_reg(kx, fs.reserve())     # register key
                vr = self.rk(vx)
                fs.emit('SETTABLE_S', reg, kr, vr)
            fs.free_to(b0)
        fs.freereg = after_array
        if array:
            fs.emit('SETLIST', reg, len(array), 1)
        fs.free_to(base)
        fs.freereg = max(base, reg + 1)

    def closure(self, e, reg):
        fs = self.fs
        # CLOSURE is iABx -- Bx is the sub-proto index. The opcode-format miner classified it
        # iABC because hksc prints that index as a plain number rather than a -(k+1) constant
        # reference, so the iABx test did not fire.
        self.emit_closure(self.compile_function(e, name='(anonymous)'), reg)

    def call(self, e, reg, nresults=1, tail=False):
        """Compile a call so its results start at `reg`. Returns the base register."""
        fs = self.fs
        if reg is None:
            reg = fs.reserve()
        fs.freereg = reg
        if e.k == 'methcall':
            obj = e.a
            meth = e.b
            args = e.c
            base = fs.reserve()          # function slot
            fs.reserve()                 # self slot
            o = self.expr_to_reg(obj, base + 1) if False else None
            del o
            tmp = fs.freereg
            objr = self.expr_to_reg(obj, tmp)
            fs.emit('SELF', base, objr, fs.kstr(meth))
            fs.freereg = base + 2
            for a in args:
                self.expr_to_reg(a, fs.reserve())
            nargs = len(args) + 1
        else:
            base = fs.reserve()
            self.expr_to_reg(e.a, base)
            fs.freereg = base + 1
            for a in (e.b or []):
                self.expr_to_reg(a, fs.reserve())
            nargs = len(e.b or [])
        if tail:
            fs.emit('TAILCALL_I', base, nargs + 1, 0)
        else:
            fs.emit('CALL_I', base, nargs + 1, nresults + 1)
        fs.freereg = base + max(nresults, 1)
        return base


    def emit_closure(self, sub, reg):
        """CLOSURE plus one DATA descriptor per upvalue.

        Lua 5.1 follows CLOSURE with nups pseudo-instructions (MOVE / GETUPVAL) describing where
        each upvalue is captured from; HavokScript uses DATA for the same purpose. Measured in
        hksc output: `CLOSURE [11, 7]` then `DATA [1, 10]`.
            A=1  capture the PARENT's local register B
            A=2  forward the parent's UPVALUE B      (NOT 0 -- measured on a grandparent
                 capture, where hksc emits `DATA 2 0` and `DATA 0 0` is rejected)
        Omitting them entirely leaves every upvalue unbound.
        """
        fs = self.fs
        fs.protos.append(sub)
        fs.emit_bx('CLOSURE', reg, len(fs.protos) - 1)
        for _nm, from_local, idx in sub.upvals:
            fs.emit('DATA', 1 if from_local else 2, idx, 0)

    # -- statements -------------------------------------------------------
    def block(self, n):
        for st in n.a:
            self.stmt(st)

    def stmt(self, n):
        fs = self.fs
        k = n.k
        if k == 'local':
            base = fs.freereg
            for i, ex in enumerate(n.b):
                self.expr_to_reg(ex, base + i)
            for i in range(len(n.b), len(n.a)):
                fs.emit('LOADNIL', base + i, base + i, 0)
            fs.freereg = base + len(n.a)
            fs.maxreg = max(fs.maxreg, fs.freereg)
            for i, nm in enumerate(n.a):
                fs.declare(nm, base + i)
        elif k == 'assign':
            self.assign(n)
        elif k == 'callstat':
            base = fs.freereg
            self.call(n.a, None, nresults=0)
            fs.free_to(base)
        elif k == 'do':
            m = fs.scope_mark()
            self.block(n.a)
            fs.scope_close(m)
        elif k == 'if':
            self.if_stmt(n)
        elif k == 'while':
            self.while_stmt(n)
        elif k == 'repeat':
            self.repeat_stmt(n)
        elif k == 'fornum':
            self.fornum(n)
        elif k == 'forin':
            self.forin(n)
        elif k == 'return':
            self.return_stmt(n)
        elif k == 'break':
            if not fs.blocks:
                raise HksCompileError('break outside a loop (line %d)' % n.line)
            fs.blocks[-1].append(fs.jump())
        elif k == 'funcstat':
            self.funcstat(n)
        elif k == 'localfunc':
            r = fs.reserve()
            fs.declare(n.a, r)          # declared BEFORE the body, so it can recurse
            self.emit_closure(self.compile_function(n.b, name=n.a), r)
        else:
            raise HksCompileError('unsupported statement %r (line %d)' % (k, n.line))

    def assign(self, n):
        fs = self.fs
        targets, values = n.a, n.b
        base = fs.freereg

        # Single target/value is the overwhelmingly common case and needs no staging: use the
        # source's own register. ⚠ It is only safe when there is ONE pair -- with several,
        # Lua evaluates every RHS before assigning any target, so `i, j = j, i` requires the
        # temporaries. Eliding them there would produce `i, j = j, j`.
        if len(targets) == 1 and len(values) == 1:
            t = targets[0]
            v = values[0]
            if t.k == 'name':
                lr = fs.local_reg(t.a)
                if lr is not None:
                    self.expr_to_reg(v, lr)       # compile straight into the local
                    fs.free_to(base)
                    return
                r = self.expr_to_reg(v, fs.reserve())
                uv = fs.upval_index(t.a)
                if uv is not None:
                    fs.emit('SETUPVAL', r, uv, 0)
                else:
                    fs.emit_bx('SETGLOBAL', r, fs.kstr(t.a))
                fs.free_to(base)
                return
            if t.k == 'index':
                o = self.rk(t.a) if t.a.k == 'name' and fs.local_reg(t.a.a) is not None \
                    else self.expr_to_reg(t.a, fs.reserve())
                vr = self.rk(v)
                if t.b.k == 'str':
                    fs.emit('SETFIELD', o, fs.kstr(t.b.a), vr)
                elif t.b.k == 'num':
                    # constant key -> the _BK form, exactly as in the table constructor
                    fs.emit('SETTABLE_S_BK', o, fs.knum(_num(t.b.a)), vr)
                else:
                    kr = self.rk(t.b)
                    fs.emit('SETTABLE_S', o, kr, vr)
                fs.free_to(base)
                return

        regs = [self.expr_to_reg(v, fs.reserve()) for v in values]
        while len(regs) < len(targets):
            r = fs.reserve()
            fs.emit('LOADNIL', r, r, 0)
            regs.append(r)
        for t, r in zip(targets, regs):
            if t.k == 'name':
                lr = fs.local_reg(t.a)
                if lr is not None:
                    fs.emit('MOVE', lr, r, 0)
                else:
                    uv = fs.upval_index(t.a)
                    if uv is not None:
                        fs.emit('SETUPVAL', r, uv, 0)
                    else:
                        fs.emit_bx('SETGLOBAL', r, fs.kstr(t.a))
            elif t.k == 'index':
                o = self.expr_to_reg(t.a)
                if t.b.k == 'str':
                    fs.emit('SETFIELD', o, fs.kstr(t.b.a), r)
                else:
                    fs.emit('SETTABLE_S', o, self.rk(t.b), r)
            else:
                raise HksCompileError('cannot assign to %r (line %d)' % (t.k, n.line))
        fs.free_to(base)

    def if_stmt(self, n):
        fs = self.fs
        ends = []
        for cond, body in n.a:
            self.cond_jump(cond, invert=True)
            jf = fs.jump()
            m = fs.scope_mark()
            self.block(body)
            fs.scope_close(m)
            if n.b is not None or cond is not n.a[-1][0]:
                ends.append(fs.jump())
            fs.patch(jf)
        if n.b is not None:
            m = fs.scope_mark()
            self.block(n.b)
            fs.scope_close(m)
        fs.patch_list(ends)

    def while_stmt(self, n):
        fs = self.fs
        top = fs.here()
        self.cond_jump(n.a, invert=True)
        jf = fs.jump()
        fs.blocks.append([])
        m = fs.scope_mark()
        self.block(n.b)
        fs.scope_close(m)
        fs.patch(fs.jump(), top)
        fs.patch(jf)
        fs.patch_list(fs.blocks.pop())

    def repeat_stmt(self, n):
        fs = self.fs
        top = fs.here()
        fs.blocks.append([])
        m = fs.scope_mark()
        self.block(n.a)
        self.cond_jump(n.b, invert=True)
        fs.patch(fs.jump(), top)
        fs.scope_close(m)
        fs.patch_list(fs.blocks.pop())

    def fornum(self, n):
        fs = self.fs
        var, e1, e2, e3 = n.a
        base = fs.freereg
        self.expr_to_reg(e1, fs.reserve())
        self.expr_to_reg(e2, fs.reserve())
        if e3 is not None:
            self.expr_to_reg(e3, fs.reserve())
        else:
            r = fs.reserve()
            fs.emit_bx('LOADK', r, fs.knum(1))
        ctrl = fs.reserve()              # the visible loop variable
        prep = fs.emit_sbx('FORPREP', base, 0)
        m = fs.scope_mark()
        fs.declare(var, ctrl)
        fs.blocks.append([])
        top = fs.here()
        self.block(n.b)
        fs.blocks_end = None
        loop = fs.emit_sbx('FORLOOP', base, 0)
        fs.patch(prep, top)
        fs.patch(loop, top)
        fs.scope_close(m)
        fs.patch_list(fs.blocks.pop())
        fs.free_to(base)

    def forin(self, n):
        fs = self.fs
        names, exprs = n.a
        base = fs.freereg
        for i in range(3):
            if i < len(exprs):
                self.expr_to_reg(exprs[i], fs.reserve())
            else:
                r = fs.reserve()
                fs.emit('LOADNIL', r, r, 0)
        varbase = fs.freereg
        for nm in names:
            fs.reserve()
        prep = fs.jump()
        m = fs.scope_mark()
        for i, nm in enumerate(names):
            fs.declare(nm, varbase + i)
        fs.blocks.append([])
        top = fs.here()
        self.block(n.b)
        fs.patch(prep)
        fs.emit('TFORLOOP', base, 0, len(names))
        fs.patch(fs.jump(), top)
        fs.scope_close(m)
        fs.patch_list(fs.blocks.pop())
        fs.free_to(base)

    def return_stmt(self, n):
        fs = self.fs
        exprs = n.a
        if not exprs:
            fs.emit('RETURN', 0, 1, 0)
            return
        # `return f(...)` is a TAIL CALL: hksc emits TAILCALL_I followed by `RETURN base 0`,
        # not CALL_I + RETURN. Matching it keeps the stream comparable and, more importantly,
        # gives the tail-call stack behaviour the engine expects for recursive helpers.
        if len(exprs) == 1 and exprs[0].k in ('call', 'methcall'):
            base = fs.freereg
            self.call(exprs[0], base, nresults=-1, tail=True)
            fs.emit('RETURN', base, 0, 0)
            fs.free_to(base)
            return
        # If the returned values are already locals sitting in CONSECUTIVE registers, return
        # them in place. RETURN takes a base and a count, so no copy is needed -- hksc emits
        # `RETURN 0 2` for `return x` where our staging produced `MOVE 1 0; RETURN 1 2`.
        regs = [fs.local_reg(x.a) if x.k == 'name' else None for x in exprs]
        if regs and all(r is not None for r in regs) \
                and all(regs[i] + 1 == regs[i + 1] for i in range(len(regs) - 1)):
            fs.emit('RETURN', regs[0], len(exprs) + 1, 0)
            return
        base = fs.freereg
        for ex in exprs:
            self.expr_to_reg(ex, fs.reserve())
        fs.emit('RETURN', base, len(exprs) + 1, 0)
        fs.free_to(base)

    def funcstat(self, n):
        fs = self.fs
        path, body, is_method = n.a, n.b, n.c
        sub = self.compile_function(body, name='.'.join(path))
        base = fs.freereg
        if len(path) > 1:
            # Resolve the target table FIRST, then build the closure -- hksc emits
            # GETGLOBAL/GETFIELD before CLOSURE, and matching the order keeps the streams
            # comparable (it also keeps the closure's register adjacent to the SETFIELD).
            root = path[0]
            lr = fs.local_reg(root)
            if lr is not None and len(path) == 2:
                obj = lr                      # already a local -- use it in place, no MOVE
            else:
                obj = fs.reserve()
                if lr is not None:
                    fs.emit('MOVE', obj, lr, 0)
                else:
                    fs.emit_bx('GETGLOBAL', obj, fs.kstr(root))
                for mid in path[1:-1]:
                    fs.emit('GETFIELD', obj, obj, fs.kstr(mid))
            r = fs.reserve()
            self.emit_closure(sub, r)
            fs.emit('SETFIELD', obj, fs.kstr(path[-1]), r)
            fs.free_to(base)
            return
        r = fs.reserve()
        self.emit_closure(sub, r)
        if len(path) == 1:
            lr = fs.local_reg(path[0])
            if lr is not None:
                fs.emit('MOVE', lr, r, 0)
            else:
                fs.emit_bx('SETGLOBAL', r, fs.kstr(path[0]))
        else:
            obj = fs.reserve()
            root = path[0]
            lr = fs.local_reg(root)
            if lr is not None:
                fs.emit('MOVE', obj, lr, 0)
            else:
                fs.emit_bx('GETGLOBAL', obj, fs.kstr(root))
            for mid in path[1:-1]:
                fs.emit('GETFIELD', obj, obj, fs.kstr(mid))
            fs.emit('SETFIELD', obj, fs.kstr(path[-1]), r)
        fs.free_to(base)

    # -- functions --------------------------------------------------------
    def compile_function(self, fnode, name='?'):
        parent = self.fs
        fs = FuncState(parent, fnode.a, fnode.c, name)
        self.fs = fs
        try:
            self.block(fnode.b)
            fs.emit('RETURN', 0, 1, 0)
        finally:
            self.fs = parent
        return fs

    def compile_chunk(self, ast):
        fs = FuncState(None, (), True, 'main')
        self.fs = fs
        self.block(ast)
        fs.emit('RETURN', 0, 1, 0)
        self.fs = None
        return fs


def _num(text):
    if text.lower().startswith('0x'):
        return int(text, 16)
    return float(text) if ('.' in text or 'e' in text.lower()) else int(text)


def _concat_chain(e):
    """Flatten a right-nested `..` chain so one CONCAT covers the whole run."""
    out = []

    def walk(n):
        if n.k == 'binop' and n.a == '..':
            walk(n.b[0])
            walk(n.b[1])
        else:
            out.append(n)
    walk(e)
    return out


# ---------------------------------------------------------------------------- writer

def _write_proto(fs, e, fs_size, base):
    """`base` is the ABSOLUTE file offset at which this proto starts.

    The instruction array is 4-aligned against the FILE position, not against the start of the
    proto: the reader does `extra = 4 - (p % 4)` on its absolute cursor. Aligning proto-relative
    desyncs every nested proto whose parent did not happen to begin on a multiple of 4.
    """
    b = bytearray()
    b += struct.pack(e + 'I', len(fs.upvals))
    b += struct.pack(e + 'I', len(fs.params))
    # ⚠ vararg is NOT a boolean. Lua 5.1 packs flags here: HASARG=1, ISVARARG=2, NEEDSARG=4.
    # hksc writes 2 for a plain vararg chunk; writing 1 is rejected as "bad code in precompiled
    # chunk". Measured on `return 1`: real byte = 0x02, ours was 0x01.
    b += bytes([2 if fs.vararg else 0])
    b += struct.pack(e + 'I', max(fs.maxreg, 2))
    b += struct.pack(e + 'I', len(fs.code))
    extra = 4 - ((base + len(b)) % 4)
    if 0 < extra < 4:
        b += b'\x5f' * extra          # hksc fills instruction-array alignment with 0x5F
    for w in fs.code:
        b += struct.pack(e + 'I', w)
    b += struct.pack(e + 'I', len(fs.consts))
    for c in fs.consts:
        b += bytes([c.t])
        if c.t == T_NIL:
            pass
        elif c.t == T_BOOL:
            b += bytes([1 if c.v else 0])
        elif c.t == T_NUMBER:
            b += struct.pack(e + 'f', c.v)
        elif c.t == T_STRING:
            s = c.v.encode('latin-1') + b'\x00'
            b += struct.pack(e + 'I', len(s)) + s
        else:
            raise HksCompileError('constant type %d cannot be written' % c.t)
    # Footer, minus the sub-count written next. The FIRST word is 1 in every hksc-produced
    # proto. The second is a per-chunk hash (0xd58361e4, 0xc7cfc84e, 0x90d8e958 ... -- it
    # differs per file), almost certainly the chunkname hash the LUI pipeline notes describe as
    # "hashed into the function footers and checked at load".
    b += struct.pack(e + 'I', 1)
    b += b'\x00' * (fs_size - 8)
    b += struct.pack(e + 'I', len(fs.protos))
    for sub in fs.protos:
        b += _write_proto(sub, e, fs_size, base + len(b))
    return bytes(b)


def write_chunk(fs, little=True, footer_size=12):
    """Serialise a compiled FuncState tree into a HavokScript chunk."""
    e = '<' if little else '>'
    # The 14-byte header plus the 13-entry HKS TYPE TABLE (TNIL, TBOOLEAN, ... TSTRUCT) form a
    # fixed prefix. It is copied verbatim from a reference chunk rather than reconstructed: the
    # table is a type registry the loader expects, and emitting an empty one (count 0) makes the
    # chunk unparseable. Byte 6 is the endianness flag and is the only field patched.
    prefix = bytearray(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         '_hks_typetable.bin'), 'rb').read())
    prefix[6] = 1 if little else 0
    b = bytearray(prefix)
    b += _write_proto(fs, e, footer_size, len(b))
    return bytes(b)


def compile_source(src, little=True, footer_size=12):
    from .lua_lang import parse
    return write_chunk(Compiler().compile_chunk(parse(src)), little, footer_size)
