"""core.gsc_codegen -- T6 GSC code generator: AST -> compiled `\\x80GSC` bytecode.

The back half of the native compiler. `core.gsc_lang` parses; this emits.

EVERYTHING HERE WAS DERIVED, NOT GUESSED
----------------------------------------
The opcode numbering comes from `core/_opcodes_mined.py`, produced by aligning this project's
disassembler against gsc-tool over 319,570 corpus instructions plus a source exercising every
operator -- conflict-free. The lowering patterns come from compiling a specimen source with
gsc-tool and reading back its own disassembly. The measured facts that shape this file:

  * LOCAL INDICES ARE REVERSED.  `SafeCreateLocalVariables a b i k v _a1 s f` gives `a` index 7
    and `f` index 0, i.e. index = (count - 1) - declaration_position.
  * PARAMS ARE LOCALS, declared first, same reversed indexing.
  * ASSIGNMENT is value-then-target:  <value>, <ref>, SetVariableField.
  * CALL ARGUMENTS PUSH RIGHT-TO-LEFT.  `helper(a, b)` pushes b then a.
  * A CALL USED AS A STATEMENT is wrapped:  PreScriptCall ... <call> ... DecTop.
  * A METHOD CALL pushes the object AFTER the arguments.
  * JUMPS: `target = addr + insn_size + int16(operand)`; backward jumps store the two's
    complement (verified: 0xC9 + 3 + (65513-65536) = 0xB5).
  * SWITCH: `Switch <rel to count word>` ... per-case bodies ... `EndSwitch` + case table of
    `{u32 value, u32 rel_addr}`; `case 1` encodes 0x00800001, `default` 0; rel_addr is relative
    to the position AFTER that addr word.
  * OPERANDS ARE ALIGNED RELATIVE TO BUFFER START, not to the instruction.
  * Each function is preceded by a 6-byte `{u16 0, u32 0}` pad and entered 4-aligned.
  * export.crc = zlib.crc32 over the function's code bytes.

SCOPE
-----
Covers the language `core.gsc_lang` parses. Unsupported constructs RAISE rather than emitting
something plausible -- a compiler that silently emits wrong bytecode is worse than one that
refuses, and this project has already paid for that lesson twice.

STATUS
------
Validated by `core.gsc_gauntlet` (PLAN §P4.10): compile the same source with this module and
with gsc-tool, disassemble BOTH with gsc-tool, compare the instruction streams. Disassembling
both with the same tool keeps our own reader out of the loop, so a shared bug cannot make a
wrong compiler look right.

  specimen sources    2/2   OPERANDS -- identical mnemonics AND operands
    ops.gsc  144 instructions   every operator, arrays, fields, funcrefs, endon/waittill/notify
    t.gsc    107 instructions   if/else, for, while, foreach, switch, method/thread/local calls
  specimen sources    3/3   OPERANDS
  60 real game sources (`tools/ref_jezuz` sample):
    OPERANDS 28 · OPCODES 1 · ACCEPTED 22 · SKIP 9 · **FAIL 0**
    i.e. every source we compile is accepted by gsc-tool; 28 match its stream exactly.
    Of the 9 SKIPs, 4 are sources GSC-TOOL ITSELF cannot compile (`local variable '_hash_...'
    not found` -- its own decompiler notation does not round-trip), and 5 are constructs this
    compiler refuses rather than guess: `%anim` / `#animtree` emission (2) and `waittillmatch`
    (2, it needs a WaitTillMatch opcode whose number could not be pinned cleanly).

Nine divergences were found and fixed by that harness; each is recorded at its call site:
  1. the IMPORT table records the INSTRUCTION address, the STRINGTABLEFIXUP table records the
     OPERAND address -- different conventions (6/6 vs 0/39 measured on a genuine script)
  2. a method-call / endon target uses the VALUE form (GetSelf), not the object form
  3. `notify` is preceded by a VoidCodePos marker
  4. no unconditional Jump between the last case body and EndSwitch
  5. NO 6-byte inter-function pad -- gsc-tool emits 4-byte alignment only
  6. the scan pass's temp counter must be rewound before codegen re-derives the same names
  7. a function with NO locals opens with CheckClearParams, not SafeCreateLocalVariables
  8. `isdefined` and friends are INTRINSIC opcodes, not import calls
  9. `game` differs by position: GetGameRef (ref) vs GetGame (value)

Two further fixes came from the real-source run:
 10. FUNCTION ENTRIES ARE AT `align4(cur + 4)` -- gsc-tool reserves 4 bytes before each entry
     then aligns. Verified on its own output: cseg 0x43B -> first function 0x440, cseg 0x6D ->
     0x74. Using plain `align4(cur)` leaves a 1-3 byte gap where gsc-tool leaves 4-7, and its
     decoder trips over the difference ("bad instruction size"). This alone fixed 8 of 11
     failures.
 11. IMPORT FLAGS ENCODE THE CALL KIND, NOT THE NAMESPACE. Census over 10,757 corpus call
     sites: GetFunction 0x01, Call 0x02 (namespaced or not), Thread 0x03, Method 0x04,
     MethodThread 0x05, `| 0x10` inside a devblock.

 12. A STRING `case` LABEL stores its reference in the LOW u16 OF THE VALUE WORD, and the
     stringtablefixup records THAT u16's address (entry+2), not the u32's. Measured on
     gsc-tool's output: case table at 0x68, `case "a"` word 0x00000001, stf address 0x6A.
     Writing a u32 string offset and registering the u32 address instead made gsc-tool's
     fixup->instruction lookup fail with `invalid map<K, T> key` -- the last 3 rejections.
 13. `_hash_XXXXXXXX` is gsc-tool's decompiler notation for a hashed string constant, not a
     variable: emit GetHash with that literal. `assert`/`breakpoint` are ordinary calls.

⛔ REMAINING GAPS (refusals, not silent miscompiles):
  * `VectorConstant` (0x5E) is not emitted: gsc-tool compresses a literal like `(0,1,0)` into
    one opcode plus a u8 bitmask. We emit the equivalent GetZero/GetByte/Vector long form, which
    is accepted but not identical. The bitmask encoding was NOT reverse-engineered -- one sample
    (`(0,1,0)` -> 8) is not enough to pin the bit order, and guessing it is exactly the kind of
    thing this project has been burned by.
  * animtree emission raises; byte-identical (EXACT) output is not expected or attempted, since
    string-pool and table ORDER are free choices.

NOTHING FROM THIS MODULE HAS BEEN BOOT-TESTED.

⚠ 2026-08-13 -- FILE RECOVERED AFTER ACCIDENTAL TRUNCATION. A tooling mistake (a cp1252
console encoding failing part-way through a rewrite) left this file 0 bytes and clobbered its
.pyc. It was rebuilt from the compiled copy inside `build/WiiU_FF_IDE/PYZ-00.pyz`: every
docstring and constant is byte-exact from that code object, and the bodies were reconstructed
from its disassembly plus the verbatim source read earlier in the same session. Verified by
re-running `core.gsc_gauntlet` (differential against gsc-tool) and `core.scripts_selftest`.
Explanatory COMMENTS inside FuncGen.__init__..call_op, _collect_strings and compile_ast could
not be recovered and were rewritten; nothing else changed.
"""
import struct
import zlib

from . import paths  # noqa: F401
from .gsc_lang import Node, parse

MAGIC = b'\x80GSC\r\n\x00\x06'
HDR_END = 0x40

# ---- opcodes (mined; see module docstring) -------------------------------------------
OP = {
    'End': 0x00, 'Return': 0x01, 'GetUndefined': 0x02, 'GetZero': 0x03,
    'GetByte': 0x04, 'GetNegByte': 0x05, 'GetUnsignedShort': 0x06,
    'GetNegUnsignedShort': 0x07, 'GetInteger': 0x08, 'GetFloat': 0x09,
    'GetString': 0x0A, 'GetIString': 0x0B, 'GetVector': 0x0C,
    'GetLevelObject': 0x0D, 'GetAnimObject': 0x0E, 'GetSelf': 0x0F,
    'GetLevel': 0x10, 'GetGame': 0x11, 'GetAnim': 0x12, 'GetAnimation': 0x13,
    'GetGameRef': 0x14, 'GetFunction': 0x15,
    'SafeCreateLocalVariables': 0x17, 'EvalLocalVariableCached': 0x19,
    'EvalArray': 0x1A, 'EvalArrayRef': 0x1C, 'ClearArray': 0x1D, 'EmptyArray': 0x1E,
    'GetSelfObject': 0x1F, 'EvalFieldVariable': 0x20, 'EvalFieldVariableRef': 0x21,
    'ClearFieldVariable': 0x22,
    'SafeSetWaittillVariableFieldCached': 0x24, 'ClearParams': 0x25,
    'CheckClearParams': 0x26, 'EvalLocalVariableRefCached': 0x27,
    'SetVariableField': 0x28, 'CallBuiltin': 0x2A, 'Wait': 0x2B,
    'PreScriptCall': 0x2D, 'ScriptFunctionCall': 0x2E,
    'ScriptFunctionCallPointer': 0x2F, 'ScriptMethodCall': 0x30,
    'ScriptMethodCallPointer': 0x31, 'ScriptThreadCall': 0x32,
    'ScriptThreadCallPointer': 0x33, 'ScriptMethodThreadCall': 0x34,
    'ScriptMethodThreadCallPointer': 0x35, 'DecTop': 0x36, 'CastFieldObject': 0x37,
    'CastBool': 0x38, 'BoolNot': 0x39, 'BoolComplement': 0x3A,
    'JumpOnFalse': 0x3B, 'JumpOnTrue': 0x3C, 'JumpOnFalseExpr': 0x3D,
    'JumpOnTrueExpr': 0x3E, 'Jump': 0x3F, 'JumpBack': 0x40,
    'Inc': 0x41, 'Dec': 0x42,
    'Bit_Or': 0x43, 'Bit_Xor': 0x44, 'Bit_And': 0x45,
    'Equal': 0x46, 'NotEqual': 0x47, 'LessThan': 0x48, 'GreaterThan': 0x49,
    'LessThanOrEqualTo': 0x4A, 'GreaterThanOrEqualTo': 0x4B,
    'ShiftLeft': 0x4C, 'ShiftRight': 0x4D,
    'Plus': 0x4E, 'Minus': 0x4F, 'Multiply': 0x50, 'Divide': 0x51, 'Modulus': 0x52,
    'SizeOf': 0x53, 'WaitTill': 0x55, 'Notify': 0x56, 'EndOn': 0x57,
    'VoidCodePos': 0x58, 'Switch': 0x59, 'EndSwitch': 0x5A, 'Vector': 0x5B,
    'GetHash': 0x5C, 'VectorConstant': 0x5E, 'IsDefined': 0x5F, 'VectorScale': 0x60,
    'GetDvarInt': 0x69, 'WaitTillFrameEnd': 0x2C,
    'AnglesToUp': 0x61, 'AnglesToRight': 0x62, 'AnglesToForward': 0x63, 'AngleClamp180': 0x64,
    'VectorToAngles': 0x65, 'Abs': 0x66, 'GetTime': 0x67, 'GetDvar': 0x68,
    'GetDvarFloat': 0x6A, 'GetDvarColorRed': 0x6C, 'GetDvarColorGreen': 0x6D,
    'GetDvarColorBlue': 0x6E,
    'FirstArrayKey': 0x70, 'NextArrayKey': 0x71, 'DevblockBegin': 0x7B,
}

# Calls that lower to a DEDICATED OPCODE, not an import-table reference. `isdefined(e)` is the
# common one: gsc-tool emits `EvalLocalVariableCached e; IsDefined`, with no PreScriptCall
# bracketing and no import record. Treating it as a script call produces a 3-instruction
# sequence where the engine wants 2, and adds a bogus import.
INTRINSIC = {
    'isdefined': ('IsDefined', 1),
    'vectorscale': ('VectorScale', 2),
    'getdvarint': ('GetDvarInt', 1),
    'gethash': ('GetHash', 1),
    # Mined per-function against gsc-tool 2026-08-13 (711 functions, zero conflicts).
    # Without these the calls compile as ordinary imports -- a PreScriptCall/DecTop bracket
    # and a bogus import record where the engine wants one opcode.
    'anglestoup': ('AnglesToUp', 1),
    'anglestoright': ('AnglesToRight', 1),
    'anglestoforward': ('AnglesToForward', 1),
    'angleclamp180': ('AngleClamp180', 1),
    'vectortoangles': ('VectorToAngles', 1),
    'abs': ('Abs', 1),
    'gettime': ('GetTime', 0),
    'getdvar': ('GetDvar', 1),
    'getdvarfloat': ('GetDvarFloat', 1),
    'getdvarcolorred': ('GetDvarColorRed', 1),
    'getdvarcolorgreen': ('GetDvarColorGreen', 1),
    'getdvarcolorblue': ('GetDvarColorBlue', 1),
}

BINOP = {
    '+': 'Plus', '-': 'Minus', '*': 'Multiply', '/': 'Divide', '%': 'Modulus',
    '==': 'Equal', '!=': 'NotEqual', '<': 'LessThan', '>': 'GreaterThan',
    '<=': 'LessThanOrEqualTo', '>=': 'GreaterThanOrEqualTo',
    '<<': 'ShiftLeft', '>>': 'ShiftRight',
    '|': 'Bit_Or', '^': 'Bit_Xor', '&': 'Bit_And',
}

# Import-record flags. CENSUS over the whole shipped corpus (10,757 call sites, 16 zones):
#     GetFunction              0x01      (a function REFERENCE, ::f)
#     ScriptFunctionCall       0x02      -- regardless of namespace
#     ScriptThreadCall         0x03
#     ScriptMethodCall         0x04
#     ScriptMethodThreadCall   0x05
#     | 0x10                             -- call site inside a devblock
# The flag encodes the CALL KIND, not whether the callee is namespaced: a plain call and a
# namespaced call are both 0x02. Deriving it from namespace presence (0x01 local / 0x02 far)
# made gsc-tool reject the file outright with "invalid map<K, T> key".
F_REF, F_CALL, F_THREAD, F_METHOD, F_METHOD_THREAD = 0x01, 0x02, 0x03, 0x04, 0x05
F_DEV = 0x10
F_LOCAL = F_REF          # alias: a funcref records 0x01

_HASH_RE = __import__('re').compile(r'^_hash_([0-9a-fA-F]{1,8})$')
F_FAR = F_CALL

SPECIAL_OBJ = {'self': 'GetSelfObject', 'level': 'GetLevelObject',
               'game': 'GetGameRef', 'anim': 'GetAnimObject'}
# ⚠ `game` differs by POSITION: ref -> GetGameRef (0x14), value -> GetGame (0x11). Using the
# ref form in value position desyncs gsc-tool's decoder ("bad instruction size").
# Measured: `game["x"] = 1` emits GetString/GetGameRef/EvalArrayRef, while `a = game["x"]`
# emits GetString/GetGame/EvalArray. `level` uses GetLevelObject in BOTH positions.
SPECIAL_VAL = {'self': 'GetSelf', 'level': 'GetLevel',
               'game': 'GetGame', 'anim': 'GetAnim'}


class GscCompileError(Exception):
    pass


# VectorConstant component encoding: 0 -> 00, +1 -> 10, -1 -> 01 (see rvalue()).
_VEC_CONST = {0.0: 0, 1.0: 2, -1.0: 1}


def _vec_const_mask(comps):
    """-> the u8 mask for a literal (x,y,z) of 0/+1/-1, or None if it does not compress."""
    if len(comps) != 3:
        return None
    bits = []
    for c in comps:
        neg = False
        while c.kind == 'unary' and c.a == '-':
            neg = not neg
            c = c.b
        if c.kind != 'num':
            return None
        try:
            v = float(c.a if not c.a.lower().startswith('0x') else int(c.a, 0))
        except ValueError:
            return None
        if neg:
            v = -v
        if v not in _VEC_CONST:
            return None
        bits.append(_VEC_CONST[v])
    return (bits[0] << 4) | (bits[1] << 2) | bits[2]


def _align(o, n):
    return (o + n - 1) & ~(n - 1)


class FuncGen(object):
    """Emits one function's cseg bytes with symbolic fixups resolved by the linker."""

    def __init__(self, script, node):
        self.s = script
        self.node = node
        self.name = node.a
        # A parameter is either a bare name or a (name, default) pair.
        self.params = [p if isinstance(p, str) else p[0] for p in (node.b or [])]
        self.defaults = {p[0]: p[1] for p in (node.b or []) if not isinstance(p, str)}
        self.locals = list(self.params)
        self.buf = bytearray()
        self.fixups = []
        self.labels = {}
        self.jumps = []
        self.loops = []
        self._tmp = 0
        self._lbl = 0
        self._term = False
        self.base = 0

    def here(self):
        return len(self.buf)

    def emit(self, mnem):
        self.buf.append(OP[mnem])
        self._term = mnem in ('End', 'Return')

    def pad_to(self, n):
        """Align RELATIVE TO BUFFER START -- self.base is 4-aligned so this is well-defined."""
        while (self.base + len(self.buf)) % n:
            self.buf.append(0)

    def u8(self, v):
        self.buf.append(v & 0xFF)

    def u16(self, v):
        self.pad_to(2)
        self.buf += struct.pack('>H', v & 0xFFFF)

    def u32(self, v):
        self.pad_to(4)
        self.buf += struct.pack('>I', v & 0xFFFFFFFF)

    def new_label(self, hint='L'):
        self._lbl += 1
        return '%s%d' % (hint, self._lbl)

    def mark(self, label):
        self.labels[label] = self.here()

    def jump(self, mnem, label):
        self.emit(mnem)
        self.pad_to(2)
        off = self.here()
        self.buf += b'\x00\x00'
        self.jumps.append((off, self.here(), label))

    def temp(self):
        self._tmp += 1
        nm = '_a%d' % self._tmp
        self.local(nm)
        return nm

    def local(self, name):
        if name not in self.locals:
            self.locals.append(name)
        return name

    def lidx(self, name):
        """Local slot index -- REVERSED: index = (count-1) - declaration_position."""
        if name not in self.locals:
            raise GscCompileError('local %r used before it was declared' % name)
        return len(self.locals) - 1 - self.locals.index(name)

    # -- string / import fixups -------------------------------------------
    def str_op(self, mnem, text, kind=0):
        self.emit(mnem)
        self.pad_to(2)
        self.fixups.append((self.here(), 'str', (text, kind)))
        self.buf += b'\x00\x00'

    def call_op(self, mnem, name, ns, argc, flags):
        # ⚠ The IMPORT table records the address of the CALL OPCODE, while the
        # stringtablefixup table records the address of the OPERAND -- two different
        # conventions (divergence 1 in the module docstring; 6/6 vs 0/39 measured on a
        # genuine script). So `insn_off` is captured BEFORE the opcode is emitted.
        # The operand itself is a placeholder the loader overwrites; genuine scripts
        # store 46 there uniformly.
        insn_off = self.here()
        self.emit(mnem)
        self.u8(argc)
        self.pad_to(4)
        self.fixups.append((insn_off, 'import', (name, ns, argc, flags)))
        self.buf += struct.pack('>I', 46)

    # -- driver -----------------------------------------------------------
    def generate(self):
        # Pre-scan so every local exists before the first SafeCreateLocalVariables operand is
        # written; gsc-tool emits the complete list up front and indices depend on the count.
        self._scan(self.node.c)
        # ⚠ The scan pass allocates compiler temporaries (`_a1`, ...) to fix the local ORDER and
        # therefore the slot indices. Codegen then re-derives the same names, so the counter has
        # to be rewound -- otherwise `foreach` emits `_a2` at codegen where the scan registered
        # `_a1`, appending an extra local and shifting every index (indices are reversed, so ONE
        # extra local moves all of them).
        self._tmp = 0
        # A function with NO locals at all (no params, no assignments) opens with
        # CheckClearParams instead of an empty SafeCreateLocalVariables. Measured on 23 of 60
        # real game sources, where gsc-tool's very first instruction is CheckClearParams.
        if not self.locals:
            self.emit('CheckClearParams')
        else:
            self.emit('SafeCreateLocalVariables')
            self.u8(len(self.locals))
            for nm in self.locals:
                self.pad_to(2)
                self.fixups.append((self.here(), 'str', (nm, 0)))
                self.buf += b'\x00\x00'
        self.block(self.node.c)
        # ⚠ Only add the implicit `End` if control can actually fall off the bottom. gsc-tool
        # does not emit one after a trailing `return`, so an unconditional End is an extra
        # instruction and a real divergence.
        if not self._term:
            self.emit('End')
        return self

    def _scan(self, n):
        """Collect local names in first-appearance order (params already present)."""
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for x in n:
                self._scan(x)
            return
        if not isinstance(n, Node):
            return
        if n.kind == 'assign' and isinstance(n.b, Node) and n.b.kind == 'name':
            self.local(n.b.a)
        elif n.kind in ('preincdec', 'postincdec') and isinstance(n.b, Node) \
                and n.b.kind == 'name':
            self.local(n.b.a)
        elif n.kind == 'builtincall' and n.a in ('waittill', 'waittillmatch'):
            # waittill's extra names ARE locals, but they were only registered at EMIT time.
            # A function whose ONLY locals come from a waittill therefore opened with
            # CheckClearParams and then appended locals afterwards -- and since indices are
            # reversed, that shifts every one of them. Registering them here is what makes the
            # count right before the first SafeCreateLocalVariables operand is written.
            for extra in (n.b or [])[1:]:
                if isinstance(extra, Node) and extra.kind == 'name':
                    self.local(extra.a)
            self._scan(n.c)
            return
        elif n.kind == 'foreach':
            # ⚠ A KEYLESS `foreach` STILL DECLARES A KEY LOCAL. `foreach_stmt` invents one
            # when `n.a` is absent, but the scan pass used to skip it, so the function was
            # laid out with ONE fewer local than codegen went on to create -- and since
            # indices are reversed, that shifts every one of them. Measured on the shipped
            # corpus: `_k71`/`_a71` in the original against a lone `_a1` from us.
            # Both passes now derive the name from the SAME temp counter.
            # ⚠ ORDER: with an EXPLICIT key gsc-tool declares key then value; with an
            # IMPLICIT one it declares the VALUE first and the invented key after it.
            # Measured on a two-foreach specimen: `a v _k2 _a1 k v2 _a3`.
            tmpn = self._tmp + 1
            if n.a:
                self.local(n.a)
                self.local(n.b)
            else:
                self.local(n.b)
                self.local('_k%d' % tmpn)
            self._scan(n.c)
            self.local('_a%d' % tmpn)
            self._tmp += 1
            self._scan(n.d)
            return
        for x in (n.a, n.b, n.c, n.d):
            self._scan(x)

    # -- statements -------------------------------------------------------
    def block(self, n):
        for st in (n.a or []):
            self.stmt(st)

    def stmt(self, n):
        k = n.kind
        if k == 'block':
            self.block(n)
        elif k == 'empty':
            pass
        elif k == 'exprstmt':
            self.expr_stmt(n.a)
        elif k == 'return':
            if n.a is None:
                self.emit('End')
            else:
                self.rvalue(n.a)
                self.emit('Return')
        elif k == 'if':
            self.if_stmt(n)
        elif k == 'while':
            self.while_stmt(n)
        elif k == 'dowhile':
            self.dowhile_stmt(n)
        elif k == 'for':
            self.for_stmt(n)
        elif k == 'foreach':
            self.foreach_stmt(n)
        elif k == 'switch':
            self.switch_stmt(n)
        elif k == 'wait':
            self.rvalue(n.a)
            self.emit('Wait')
        elif k == 'waittillframeend':
            # ⚠ NOT VoidCodePos. `waittillframeend` is its own opcode, 0x2C, mined against
            # gsc-tool (9 sites, unanimous). VoidCodePos (0x58) is the marker that bounds
            # notify's variadic argument list -- emitting it here produced a script that
            # marked a notify boundary instead of waiting a frame.
            self.emit('WaitTillFrameEnd')
        elif k == 'break':
            if not self.loops:
                raise GscCompileError('break outside a loop (line %d)' % n.line)
            self.jump('Jump', self.loops[-1][1])
        elif k == 'continue':
            if not self.loops:
                raise GscCompileError('continue outside a loop (line %d)' % n.line)
            self.jump('Jump', self.loops[-1][0])
        elif k == 'devblock':
            # MEASURED on the shipped corpus: a retail script DOES carry its dev blocks.
            # `DevblockBegin` (0x7B) is a forward jump over the block -- target =
            # addr + insn_size + u16, the ordinary jump encoding -- and the block's
            # instructions sit right after it. (face_utility_mp.csc: DevblockBegin at 0x66C,
            # u16 0x0011, size 4 -> 0x681, a real instruction.) Emitting nothing, as gsc-tool
            # without -d does, silently DROPS that code, so a script decompiled and recompiled
            # would lose every assert and debug path it shipped with.
            endd = self.new_label('enddev')
            self.jump('DevblockBegin', endd)
            self.stmt(n.a)
            self.mark(endd)
        else:
            raise GscCompileError('unsupported statement %r (line %d)' % (k, n.line))

    def expr_stmt(self, e):
        """An expression evaluated for effect: calls get PreScriptCall/DecTop bracketing."""
        if e.kind == 'assign':
            self.assign(e)
        elif e.kind in ('preincdec', 'postincdec'):
            self.lvalue(e.b)
            self.emit('Inc' if e.a == '++' else 'Dec')
        elif e.kind in ('call', 'builtincall', 'thread'):
            self.call(e, want_value=False)
        else:
            self.rvalue(e)
            self.emit('DecTop')

    def cond_jump(self, cond, mnem, label):
        """Evaluate `cond` and jump on it, FOLDING a leading `!`.

        PEEPHOLE: `if (!c)` is ONE instruction (`<c> JumpOnTrue`), not two
        (`<c> BoolNot JumpOnFalse`). Both Treyarch's compiler and gsc-tool fold it, and at
        every conditional-jump site -- if, while, for, do/while and the ternary. Measured
        against gsc-tool on a specimen exercising all five: with the fold only in `if`, we
        emitted 38 instructions where it emits 37; with it everywhere, 37/37 mnemonics AND
        operands identical. The shipped corpus is full of `<cond> JumpOnTrue skip`, so
        without this a decompiled script recompiles one instruction longer at every negated
        test (see core.gsc_decompile).
        """
        flip = {'JumpOnFalse': 'JumpOnTrue', 'JumpOnTrue': 'JumpOnFalse',
                'JumpOnFalseExpr': 'JumpOnTrueExpr', 'JumpOnTrueExpr': 'JumpOnFalseExpr'}
        while cond.kind == 'unary' and cond.a == '!' and mnem in flip:
            cond, mnem = cond.b, flip[mnem]
        self.rvalue(cond)
        self.jump(mnem, label)

    def if_stmt(self, n):
        els = self.new_label('else')
        end = self.new_label('endif')
        self.cond_jump(n.a, 'JumpOnFalse', els if n.c else end)
        self.stmt(n.b)
        if n.c:
            self.jump('Jump', end)
            self.mark(els)
            self.stmt(n.c)
        else:
            self.mark(els)
        self.mark(end)

    def while_stmt(self, n):
        top, end = self.new_label('while'), self.new_label('endwhile')
        self.mark(top)
        self.cond_jump(n.a, 'JumpOnFalse', end)
        self.loops.append((top, end))
        self.stmt(n.b)
        self.loops.pop()
        self.jump('Jump', top)
        self.mark(end)

    def dowhile_stmt(self, n):
        top, cont, end = self.new_label('do'), self.new_label('docont'), self.new_label('enddo')
        self.mark(top)
        self.loops.append((cont, end))
        self.stmt(n.b)
        self.loops.pop()
        self.mark(cont)
        self.cond_jump(n.a, 'JumpOnTrue', top)
        self.mark(end)

    def for_stmt(self, n):
        top, cont, end = self.new_label('for'), self.new_label('forcont'), self.new_label('endfor')
        if n.a:
            self.stmt(n.a)
        self.mark(top)
        if n.b is not None:
            self.cond_jump(n.b, 'JumpOnFalse', end)
        self.loops.append((cont, end))
        self.stmt(n.d[0])
        self.loops.pop()
        self.mark(cont)
        if n.c:
            self.stmt(n.c)
        self.jump('Jump', top)
        self.mark(end)

    def foreach_stmt(self, n):
        """Lowered exactly as gsc-tool does it:

            <coll> -> _aN
            FirstArrayKey -> key
          top:
            key IsDefined  JumpOnFalse end
            key, _aN, EvalArray -> value
            <body>
          cont:
            key, _aN, NextArrayKey -> key
            Jump top
          end:
        """
        # Name the implicit key from the TEMP counter, not the label counter, so the scan
        # pass above derives exactly the same name (the label counter is not known to it).
        if n.a:
            key = self.local(n.a)
            val = self.local(n.b)
        else:
            val = self.local(n.b)
            key = self.local('_k%d' % (self._tmp + 1))
        coll = self.temp()
        self.rvalue(n.c)
        self.store_local(coll)
        self.load_local(coll)
        self.emit('FirstArrayKey')
        self.store_local(key)

        top, cont, end = self.new_label('fe'), self.new_label('fecont'), self.new_label('endfe')
        self.mark(top)
        self.load_local(key)
        self.emit('IsDefined')
        self.jump('JumpOnFalse', end)
        self.load_local(key)
        self.load_local(coll)
        self.emit('EvalArray')
        self.store_local(val)
        self.loops.append((cont, end))
        self.stmt(n.d[0])
        self.loops.pop()
        self.mark(cont)
        self.load_local(key)
        self.load_local(coll)
        self.emit('NextArrayKey')
        self.store_local(key)
        self.jump('Jump', top)
        self.mark(end)

    def switch_stmt(self, n):
        end = self.new_label('endsw')
        tbl = self.new_label('swtbl')
        self.rvalue(n.a)
        self.emit('Switch')
        self.pad_to(4)
        off = self.here()
        self.buf += b'\x00\x00\x00\x00'
        self.jumps.append((off, self.here(), tbl))       # u32 fixup, handled by width
        self._switch_u32 = getattr(self, '_switch_u32', set())
        self._switch_u32.add(off)

        entries = []
        self.loops.append((end, end))
        for value, stmts in n.b:
            lab = self.new_label('case')
            self.mark(lab)
            for st in stmts:
                self.stmt(st)
            entries.append((value, lab))
        self.loops.pop()
        # ⚠ NO trailing Jump here. Each case body ends with its own `break` (a Jump to `end`);
        # gsc-tool goes straight from the last body into the EndSwitch table, so an extra
        # unconditional Jump is a real divergence, not a harmless one.
        self.emit('EndSwitch')
        self.pad_to(4)
        self.mark(tbl)                                    # Switch points AT the count word
        self.buf += struct.pack('>I', len(entries))
        for value, lab in entries:
            if value is None:
                self.buf += struct.pack('>I', 0)
            elif value.kind == 'num' and '.' not in value.a:
                self.buf += struct.pack('>I', 0x00800000 | (int(value.a, 0) & 0x7FFFFF))
            elif value.kind == 'str':
                # A STRING case stores its reference in the LOW u16 of the value word, and the
                # stringtablefixup records the address of that u16 -- i.e. entry+2, not the u32
                # itself. Measured on gsc-tool's own output: case table at 0x68, `case "a"` word
                # 0x00000001, stf address 0x6A. Registering the u32 address instead makes
                # gsc-tool's fixup->instruction lookup fail with `invalid map<K, T> key`.
                self.buf += struct.pack('>H', 0)
                self.fixups.append((self.here(), 'str', (value.a, 0)))
                self.buf += struct.pack('>H', 0)
            else:
                raise GscCompileError('switch case must be an integer or string literal '
                                      '(line %d)' % n.line)
            self.fixups.append((self.here(), 'caseaddr', lab))
            self.buf += struct.pack('>I', 0)
        self.mark(end)

    # -- lvalues / locals -------------------------------------------------
    def store_local(self, name):
        self.emit('EvalLocalVariableRefCached')
        self.u8(self.lidx(name))
        self.emit('SetVariableField')

    def load_local(self, name):
        self.emit('EvalLocalVariableCached')
        self.u8(self.lidx(name))

    def assign(self, n):
        op, target, value = n.a, n.b, n.c
        if op != '=':
            # a += b  ->  a = a + b
            value = Node('bin', op[:-1], target, value, line=n.line)
        # ASSIGNING `undefined` TO A FIELD OR AN ARRAY SLOT HAS ITS OWN OPCODE. Measured
        # against gsc-tool: `self.a = undefined` is `GetSelfObject; ClearFieldVariable a`
        # (2 instructions), not `GetUndefined; GetSelfObject; EvalFieldVariableRef;
        # SetVariableField` (4); `c["k"] = undefined` is `GetString k;
        # EvalLocalVariableRefCached c; ClearArray`. A plain local keeps the long form.
        if op == '=' and value.kind == 'undefined':
            if target.kind == 'field':
                self.object_of(target.a)
                self.str_op('ClearFieldVariable', target.b, kind=1)
                return
            if target.kind == 'index':
                self.rvalue(target.b)
                self.lvalue(target.a)
                self.emit('ClearArray')
                return
        self.rvalue(value)
        self.lvalue(target)
        self.emit('SetVariableField')

    def lvalue(self, n):
        k = n.kind
        if k == 'name':
            self.local(n.a)
            self.emit('EvalLocalVariableRefCached')
            self.u8(self.lidx(n.a))
        elif k == 'field':
            self.object_of(n.a)
            self.str_op('EvalFieldVariableRef', n.b, kind=1)
        elif k == 'index':
            self.rvalue(n.b)
            self.lvalue(n.a)
            self.emit('EvalArrayRef')
        elif k == 'special':
            self.emit(SPECIAL_OBJ[n.a])
        else:
            raise GscCompileError('cannot assign to %r (line %d)' % (k, n.line))

    def object_of(self, n):
        """Push a value in OBJECT position -- the target of a FIELD ACCESS.

        `self.accuracy = 1` lowers to GetSelfObject + EvalFieldVariableRef.
        """
        if n.kind == 'special':
            self.emit(SPECIAL_OBJ[n.a])
        else:
            self.rvalue(n)
            self.emit('CastFieldObject')

    def target_of(self, n):
        """Push a CALL target (`self helper()`, `self endon("x")`).

        ⚠ This is NOT `object_of`. A method-call / endon / waittill target uses the VALUE form:
        gsc-tool emits `OP_GetSelf` here and `OP_GetSelfObject` only for field access, and it
        does NOT insert a CastFieldObject for a non-special target.
        """
        if n.kind == 'special':
            self.emit(SPECIAL_VAL[n.a])
        else:
            self.rvalue(n)

    # -- rvalues ----------------------------------------------------------
    def rvalue(self, n):
        k = n.kind
        if k == 'num':
            self.number(n.a)
        elif k == 'str':
            self.str_op('GetString', n.a, 0)
        elif k == 'istr':
            self.str_op('GetIString', n.a, 0)
        elif k == 'bool':
            self.emit('GetByte' if n.a else 'GetZero')
            if n.a:
                self.u8(1)
        elif k == 'undefined':
            self.emit('GetUndefined')
        elif k == 'emptyarray':
            self.emit('EmptyArray')
        elif k == 'special':
            self.emit(SPECIAL_VAL[n.a])
        elif k == 'name':
            # gsc-tool's decompiler renders a hashed string constant as the identifier
            # `_hash_XXXXXXXX`. It is not a variable -- emit GetHash with that literal.
            m = _HASH_RE.match(n.a)
            if m and n.a not in self.locals:
                self.emit('GetHash')
                self.pad_to(4)
                self.buf += struct.pack('>I', int(m.group(1), 16))
            else:
                self.load_local(n.a)
        elif k == 'field':
            if n.b == 'size':
                self.rvalue(n.a)
                self.emit('SizeOf')
            else:
                self.object_of(n.a)
                self.str_op('EvalFieldVariable', n.b, kind=1)
        elif k == 'index':
            self.rvalue(n.b)
            self.rvalue(n.a)
            self.emit('EvalArray')
        elif k == 'bin':
            self.binary(n)
        elif k == 'unary':
            self.unary(n)
        elif k == 'ternary':
            self.ternary(n)
        elif k in ('preincdec', 'postincdec'):
            # As an expression the ++/-- value is not modelled; refuse rather than guess.
            raise GscCompileError('++/-- is only supported as a statement (line %d)' % n.line)
        elif k == 'vector':
            # A literal vector whose components are all 0 / +1 / -1 compresses into a single
            # VectorConstant (0x5E) plus a u8 mask: two bits per component, x in bits 4-5,
            # y in 2-3, z in 0-1, with 0 -> 00, +1 -> 10, -1 -> 01. Reverse-engineered against
            # gsc-tool over all 27 combinations of {-1,0,1}^3 (exact 27/27); the long
            # GetZero/GetByte/Vector form is still emitted for anything else. Without this,
            # a decompiled `(0,1,0)` recompiles as three instructions where the original had
            # one -- 31 corpus functions diverged on exactly that.
            mask = _vec_const_mask(n.a)
            if mask is not None:
                self.emit('VectorConstant')
                self.u8(mask)
            else:
                # ⚠ COMPONENTS PUSH RIGHT-TO-LEFT, like call arguments. Measured against
                # gsc-tool on `(2,0,0)`: it emits GetZero, GetZero, GetByte 2, Vector -- z
                # first, x last. We pushed x first, which builds the vector reversed.
                for c in reversed(n.a):
                    self.rvalue(c)
                self.emit('Vector')
        elif k in ('funcref', 'nsref'):
            # GetFunction is an import site too -- record the INSTRUCTION address (see call_op).
            insn_off = self.here()
            self.emit('GetFunction')
            self.pad_to(4)
            self.fixups.append((insn_off, 'import', (n.b, n.a, 0, F_LOCAL)))
            self.buf += struct.pack('>I', 46)
        elif k in ('call', 'builtincall', 'thread'):
            self.call(n, want_value=True)
        elif k == 'deref':
            self.rvalue(n.a)
        elif k == 'anim':
            self.emit('GetAnimation')
            self.pad_to(4)
            self.fixups.append((self.here(), 'anim', n.a))
            self.buf += struct.pack('>I', 0)
        elif k == 'animtreeref':
            self.emit('GetAnimation')
            self.pad_to(4)
            self.fixups.append((self.here(), 'anim', ''))
            self.buf += struct.pack('>I', 0)
        else:
            raise GscCompileError('unsupported expression %r (line %d)' % (k, n.line))

    def number(self, text):
        if '.' in text or 'e' in text.lower() and not text.lower().startswith('0x'):
            self.emit('GetFloat')
            self.pad_to(4)
            self.buf += struct.pack('>f', float(text))
            return
        v = int(text, 0)
        if v == 0:
            self.emit('GetZero')
        elif 0 < v < 256:
            self.emit('GetByte')
            self.u8(v)
        elif -256 < v < 0:
            self.emit('GetNegByte')
            self.u8(-v)
        elif 0 < v < 65536:
            self.emit('GetUnsignedShort')
            self.u16(v)
        elif -65536 < v < 0:
            self.emit('GetNegUnsignedShort')
            self.u16(-v)
        else:
            self.emit('GetInteger')
            self.u32(v)

    def binary(self, n):
        op = n.a
        if op in ('&&', '||'):
            # short-circuit via the *Expr jump forms
            end = self.new_label('sc')
            self.rvalue(n.b)
            self.jump('JumpOnFalseExpr' if op == '&&' else 'JumpOnTrueExpr', end)
            self.rvalue(n.c)
            self.mark(end)
            return
        if op not in BINOP:
            raise GscCompileError('unsupported operator %r (line %d)' % (op, n.line))
        self.rvalue(n.b)
        self.rvalue(n.c)
        self.emit(BINOP[op])

    def unary(self, n):
        if n.a == '!':
            self.rvalue(n.b)
            self.emit('BoolNot')
        elif n.a == '~':
            self.rvalue(n.b)
            self.emit('BoolComplement')
        elif n.a == '-':
            if n.b.kind == 'num':
                self.number('-' + n.b.a)
            else:
                self.emit('GetZero')
                self.rvalue(n.b)
                self.emit('Minus')
        else:
            raise GscCompileError('unsupported unary %r (line %d)' % (n.a, n.line))

    def ternary(self, n):
        els, end = self.new_label('tel'), self.new_label('ten')
        self.cond_jump(n.a, 'JumpOnFalse', els)
        self.rvalue(n.b)
        self.jump('Jump', end)
        self.mark(els)
        self.rvalue(n.c)
        self.mark(end)

    # -- calls ------------------------------------------------------------
    def call(self, n, want_value):
        if n.kind == 'thread':
            inner = n.a
            if inner.kind != 'call':
                raise GscCompileError('`thread` must be followed by a call (line %d)' % n.line)
            return self._emit_call(inner, threaded=True, want_value=want_value)
        if n.kind == 'builtincall':
            return self._emit_builtin(n, want_value)
        return self._emit_call(n, threaded=(n.d == 'thread'), want_value=want_value)

    def _emit_builtin(self, n, want_value):
        """waittill / notify / endon -- stack-op builtins, not import calls."""
        kw, args, target = n.a, n.b, n.c
        if kw == 'endon':
            self.rvalue(args[0])
            self.target_of(target) if target else self.emit('GetSelf')
            self.emit('EndOn')
        elif kw == 'notify':
            # A VoidCodePos marker precedes notify's arguments (it bounds the variadic list on
            # the stack); gsc-tool emits it before the args, not after.
            self.emit('VoidCodePos')
            for a in reversed(args):
                self.rvalue(a)
            self.target_of(target) if target else self.emit('GetSelf')
            self.emit('Notify')
        elif kw in ('waittill', 'waittillmatch'):
            self.rvalue(args[0])
            self.target_of(target) if target else self.emit('GetSelf')
            self.emit('WaitTill')
            for extra in args[1:]:
                if extra.kind != 'name':
                    raise GscCompileError('waittill extra args must be plain locals '
                                          '(line %d)' % n.line)
                self.local(extra.a)
                self.emit('SafeSetWaittillVariableFieldCached')
                self.u8(self.lidx(extra.a))
            self.emit('ClearParams')
        else:
            raise GscCompileError('unsupported builtin %r (line %d)' % (kw, n.line))

    def _emit_call(self, n, threaded, want_value):
        callee, args, target = n.a, (n.b or []), n.c
        # intrinsic? (plain, unqualified, no target, not threaded)
        if (callee.kind == 'name' and target is None and not threaded
                and callee.a in INTRINSIC):
            mnem, arity = INTRINSIC[callee.a]
            if len(args) == arity:
                for a in args:
                    self.rvalue(a)
                self.emit(mnem)
                if not want_value:
                    self.emit('DecTop')
                return
        if not want_value:
            self.emit('PreScriptCall')
        else:
            self.emit('PreScriptCall')
        for a in reversed(args):                 # RIGHT-TO-LEFT
            self.rvalue(a)
        if target is not None:
            self.target_of(target)

        if callee.kind == 'deref':
            self.rvalue(callee.a)
            mnem = ('ScriptMethodThreadCallPointer' if (target and threaded) else
                    'ScriptMethodCallPointer' if target else
                    'ScriptThreadCallPointer' if threaded else 'ScriptFunctionCallPointer')
            self.emit(mnem)
            self.u8(len(args))
        else:
            if callee.kind == 'name':
                name, ns = callee.a, ''
            elif callee.kind == 'nsref':
                name, ns = callee.b, callee.a
            else:
                raise GscCompileError('unsupported callee %r (line %d)'
                                      % (callee.kind, n.line))
            mnem = ('ScriptMethodThreadCall' if (target and threaded) else
                    'ScriptMethodCall' if target else
                    'ScriptThreadCall' if threaded else 'ScriptFunctionCall')
            flags = (F_METHOD_THREAD if (target and threaded) else
                     F_METHOD if target else
                     F_THREAD if threaded else
                     F_CALL)
            self.call_op(mnem, name, ns, len(args), flags)
        if not want_value:
            self.emit('DecTop')


def _collect_strings(ast, intern):
    """Walk the AST for every literal / field name / callee that needs a pool entry."""
    def walk(n):
        if n is None or isinstance(n, str):
            return
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not isinstance(n, Node):
            return
        if n.kind in ('str', 'istr'):
            intern(n.a)
        elif n.kind == 'field':
            intern(n.b)
        elif n.kind in ('funcref', 'nsref'):
            intern(n.a)
            intern(n.b)
        elif n.kind == 'assign' and isinstance(n.b, Node) and n.b.kind == 'name':
            intern(n.b.a)
        elif n.kind == 'name':
            intern(n.a)
        elif n.kind == 'foreach':
            if n.a:
                intern(n.a)
            intern(n.b)
        for x in (n.a, n.b, n.c, n.d):
            walk(x)
    for fn in ast.functions:
        walk(fn.c)
    # Compiler temporaries are named on demand during codegen, after the pool is already laid
    # out, so every name `temp()` / `foreach` can invent has to be interned up front.
    for i in range(1, 33):
        intern('_a%d' % i)
        intern('_k%d' % i)


def compile_source(src, script_path, path_hint='<source>'):
    """Compile GSC source text to console (big-endian) `\\x80GSC` bytes."""
    ast = parse(src, path_hint)
    return compile_ast(ast, script_path)


def compile_ast(ast, script_path):
    gens = []
    for fn in ast.functions:
        gens.append(FuncGen(ast, fn))

    # ---- string pool ----------------------------------------------------------------
    # Offsets are u16, and the pool sits immediately after the 0x40-byte header, so the
    # SCRIPT NAME is interned first and therefore lands at HDR_END -- which is what the
    # header's `name` field is set to below.
    strings, order = {}, []

    def intern(t):
        if t not in strings:
            strings[t] = None
            order.append(t)

    intern(script_path)
    intern('')
    for fn in ast.functions:
        intern(fn.a)
        for p in (fn.b or []):
            intern(p if isinstance(p, str) else p[0])
    for inc in ast.includes:
        intern(inc)
    for at in ast.animtrees:
        intern(at)
    _collect_strings(ast, intern)

    pool = bytearray()
    for t in order:
        strings[t] = HDR_END + len(pool)
        pool += t.encode('latin-1') + b'\x00'

    # ---- include table --------------------------------------------------------------
    include_off = HDR_END + len(pool)
    inc_tbl = b''.join(struct.pack('>I', strings[i]) for i in ast.includes)
    cseg_off = include_off + len(inc_tbl)

    # ---- code segment ---------------------------------------------------------------
    # Operand alignment is RELATIVE TO BUFFER START, so a function's size depends on the
    # address it starts at, which in turn depends on the sizes of the functions before it.
    # Iterate to a fixed point (8 rounds is far more than any real script needs) and then
    # assert convergence when the bodies are concatenated.
    bases = {}
    for _ in range(8):
        cur = cseg_off
        body = bytearray()
        changed = False
        for g in gens:
            # ⚠ FUNCTION ENTRIES ARE AT align4(cur + 4) -- gsc-tool reserves 4 bytes before
            # each entry and then aligns. Using plain align4(cur) leaves a 1-3 byte gap where
            # it leaves 4-7, and its decoder trips over the difference.
            cur = _align(cur + 4, 4)
            if bases.get(g.name) != cur:
                bases[g.name] = cur
                changed = True
            g.base = cur
            g.buf = bytearray()
            g.fixups = []
            g.labels = {}
            g.jumps = []
            g.locals = list(g.params)
            g._tmp = 0
            g._lbl = 0
            g.generate()
            cur += len(g.buf)
        if not changed:
            break

    cseg = bytearray()
    for g in gens:
        # Pad up to the address this function's operands were aligned against.
        while cseg_off + len(cseg) < g.base:
            cseg.append(0)
        g.buf_off = len(cseg)
        # If this ever fires the layout loop above did not reach a fixed point, and every
        # aligned operand in the function would be off. Refuse rather than emit it.
        if cseg_off + g.buf_off != g.base:
            raise GscCompileError(
                'layout did not converge for %s(): emitted at 0x%X but operands were '
                'aligned against base 0x%X'
                % (g.name, cseg_off + g.buf_off, g.base))
        cseg += g.buf
    cseg_size = len(cseg)

    # Every string reference in the file is a u16 OFFSET, so nothing may live at or past
    # 64 KiB.
    if cseg_off + cseg_size >= 65536:
        raise GscCompileError('script exceeds the u16 string-offset limit (%d bytes)'
                              % (cseg_off + cseg_size))

    # ---- resolve jumps --------------------------------------------------------------
    for g in gens:
        sw32 = getattr(g, '_switch_u32', set())
        for off, insn_end, label in g.jumps:
            if label not in g.labels:
                raise GscCompileError(f'unresolved label {label!r} in {g.name}')
            tgt = g.labels[label]
            if off in sw32:
                struct.pack_into('>I', cseg, g.buf_off + off, (tgt - insn_end) & 0xFFFFFFFF)
            else:
                # ⚠ A jump operand is a SIGNED 16-bit displacement. Masking an out-of-range
                # distance with & 0xFFFF wraps it silently and produces a jump into the middle
                # of another instruction -- gsc-tool then reports "bad instruction size" and the
                # engine would execute garbage. Refuse instead.
                rel = tgt - insn_end
                if not (-32768 <= rel <= 32767):
                    raise GscCompileError(
                        'jump out of 16-bit range in %s(): %+d bytes to %r. The function is '
                        'too large for a single branch; split it.'
                        % (g.name, rel, label))
                struct.pack_into('>h', cseg, g.buf_off + off, rel)

    # ---- resolve string / import / animtree / case fixups ---------------------------
    stf = {}
    stf1 = {}
    imports = {}
    anims = {}
    for g in gens:
        for off, kind, payload in g.fixups:
            abs_buf = g.buf_off + off
            addr = cseg_off + abs_buf
            if kind == 'str':
                text, ty = payload
                struct.pack_into('>H', cseg, abs_buf, strings[text] & 0xFFFF)
                (stf1 if ty else stf).setdefault(text, []).append(addr)
            elif kind == 'str32':
                struct.pack_into('>I', cseg, abs_buf, strings[payload[0]])
                stf.setdefault(payload[0], []).append(addr)
            elif kind == 'import':
                imports.setdefault(payload, []).append(addr)
            elif kind == 'anim':
                anims.setdefault(payload, []).append(addr)
            elif kind == 'caseaddr':
                tgt = g.labels[payload]
                # A case address is relative to the position AFTER the address word.
                struct.pack_into('>I', cseg, abs_buf,
                                 (tgt - (off + 4)) & 0xFFFFFFFF)

    # ---- exports --------------------------------------------------------------------
    exports_off = cseg_off + cseg_size
    exp_tbl = bytearray()
    for g in gens:
        code = bytes(cseg[g.buf_off:g.buf_off + len(g.buf)])
        crc = zlib.crc32(code) & 0xFFFFFFFF
        exp_tbl += struct.pack('>IIHBB', crc, g.base, strings[g.name],
                               len(g.params), 0)

    # ---- imports --------------------------------------------------------------------
    imports_off = exports_off + len(exp_tbl)
    imp_tbl = bytearray()
    for (name, ns, argc, flags), addrs in imports.items():
        imp_tbl += struct.pack('>HHHBB', strings[name], strings[ns], len(addrs), argc, flags)
        for a in addrs:
            imp_tbl += struct.pack('>I', a)

    # ---- animtrees ------------------------------------------------------------------
    animtree_off = imports_off + len(imp_tbl)
    anim_tbl = b''
    if anims:
        raise GscCompileError('animation references (%%anim / #animtree) are not yet emitted')

    # ---- stringtablefixup -----------------------------------------------------------
    # The per-record address count is a u8, so a string referenced more than 255 times needs
    # several records.
    stf_off = animtree_off + len(anim_tbl)
    stf_tbl = bytearray()
    n_stf = 0
    for table, ty in ((stf, 0), (stf1, 1)):
        for text, addrs in table.items():
            for i in range(0, len(addrs), 255):
                chunk = addrs[i:i + 255]
                stf_tbl += struct.pack('>HBB', strings[text], len(chunk), ty)
                for a in chunk:
                    stf_tbl += struct.pack('>I', a)
                n_stf += 1

    fixup_off = stf_off + len(stf_tbl)
    profile_off = fixup_off

    # ---- header ---------------------------------------------------------------------
    hdr = bytearray(HDR_END)
    hdr[0:8] = MAGIC
    struct.pack_into('>I', hdr, 8, 0)
    struct.pack_into('>8I', hdr, 12, include_off, animtree_off, cseg_off, stf_off,
                     exports_off, imports_off, fixup_off, profile_off)
    struct.pack_into('>I', hdr, 44, cseg_size)
    struct.pack_into('>6H', hdr, 48, HDR_END, n_stf, len(gens), len(imports), 0, 0)
    hdr[60] = len(ast.includes)
    hdr[61] = 0
    hdr[62] = 0

    out = bytearray()
    out += hdr
    out += pool
    assert len(out) == include_off, (len(out), include_off)
    out += inc_tbl
    assert len(out) == cseg_off, (len(out), cseg_off)
    out += cseg
    assert len(out) == exports_off, (len(out), exports_off)
    out += exp_tbl + imp_tbl + anim_tbl + stf_tbl
    return bytes(out)
