"""core.gsc -- native GSC (ScriptParseTree) reading, disassembly and linting.

Roadmap P4. This is the READ half of the native GSC toolchain, and it depends on NO external
tool -- no gsc-tool.exe.

It composes three things this project already owns and had never connected:

  tools/gsc_spt.py      the table model (header, exports, imports, includes, strfix, animtrees),
                        validated EOF-exact across the whole patch_mp corpus
  wiiu_ref/gsc_diff.py  the opcode operand table, validated by stf-coverage: 28/28 byte-exact
                        roundtrip with 0/5236 stringtablefixup operands unswapped
  tools/gsc_preboot_check.py  the 6-point static pre-boot checklist

What this adds is the cseg walk: turning the opcode stream into a readable listing with
operands resolved back to strings, imports and locals.

STILL MISSING for full P4 (honest scope): the source-language front-end (lexer/parser/codegen)
and a control-flow reconstructor that turns this listing into source. Disassembly is the
prerequisite for both, and it is what the editor needs today to show a script's real content.
"""
import struct

from . import paths  # noqa: F401

import gsc_spt
import gsc_diff

MAGIC = gsc_spt.MAGIC

# Mnemonics for the opcodes the operand table documents. Anything not named here renders as
# op_XX -- deliberately, so an unnamed opcode is visible rather than silently plausible.
NAMES = {
    0x00: 'End', 0x01: 'Return', 0x02: 'GetUndefined', 0x03: 'GetZero',
    0x04: 'GetByte', 0x05: 'GetNegByte', 0x06: 'GetUnsignedShort',
    0x07: 'GetNegUnsignedShort', 0x08: 'GetInteger', 0x09: 'GetFloat',
    0x0A: 'GetString', 0x0B: 'GetIString', 0x0C: 'GetVector',
    0x13: 'GetAnimTree', 0x15: 'GetFunction',
    0x17: 'SafeCreateLocalVariables', 0x19: 'EvalLocalVariableCached',
    0x1A: 'EvalArray', 0x1B: 'EvalArray', 0x1C: 'EvalArrayRef',
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
    0x41: 'Inc', 0x42: 'Dec', 0x5A: 'Switch', 0x5B: 'Vector', 0x5C: 'GetHash',
}

# Opcodes whose operand is a string offset resolved through the stringtablefixup table.
STR_OPS = {0x0A, 0x0B, 0x20, 0x21, 0x22}
# Opcodes whose u32 operand is an import-table reference.
IMPORT_OPS = {0x2E, 0x30, 0x32, 0x34, 0x15}


def _align(o, n):
    return (o + n - 1) & ~(n - 1)


def _looks_console(buf):
    """Are the header's section offsets sane when read BIG-endian?

    Both PC and console scripts share the `\\x80GSC\\r\\n` magic and the same opcode numbering,
    so magic alone does not identify the byte order -- only the u32 fields differ. A PC script
    read as big-endian yields offsets in the billions, which is unmistakable.

    Measured: our authored raid zones (`mp_raid_*.zone`, `test_wiiu_raw.zone` in `wiiu_ref/`)
    carry PC LITTLE-ENDIAN GSC. `maps/mp/mp_raid.gsc` is 518 B; read BE its cseg_off is
    2,634,088,448, read LE it is 413. Every genuine console script in the shipped corpus
    (185 of them) reads correctly as BE.
    """
    if len(buf) < 0x40:
        return True
    n = len(buf)
    offs = struct.unpack_from('>10I', buf, 8)[1:9]     # the 8 section offsets
    return all(o <= n for o in offs)


def _to_console(buf):
    """Return (source_endian, console_bytes).

    A PC chunk is transcoded to console byte order through the validated transcoder
    (`gsc_diff.pc_gsc_to_console`, byte-exact on 28/28 with 0/5236 stf operands unswapped) so
    everything downstream can assume big-endian.
    """
    if _looks_console(buf):
        return 'console', buf
    try:
        import gsc_diff as _gd
        return 'pc', _gd.pc_gsc_to_console(buf)
    except Exception as ex:
        raise ValueError('script is not console-endian and PC->console transcode failed: '
                         '%s: %s' % (type(ex).__name__, ex))


class Insn(object):
    __slots__ = ('addr', 'op', 'name', 'operands', 'comment', 'size')

    def __init__(self, addr, op, name, operands, comment, size):
        self.addr, self.op, self.name = addr, op, name
        self.operands, self.comment, self.size = operands, comment, size

    def __repr__(self):
        s = '%06X  %-30s %s' % (self.addr, self.name,
                                ' '.join(str(o) for o in self.operands))
        return s + ('   ; ' + self.comment if self.comment else '')


class Function(object):
    def __init__(self, export, start, end):
        self.export = export
        self.start, self.end = start, end
        self.insns = []
        self.error = None

    @property
    def name(self):
        return self.export.name if self.export else 'sub_%06X' % self.start

    @property
    def params(self):
        return self.export.params if self.export else -1

    def listing(self):
        head = '%s(%s)   ; addr 0x%X..0x%X, %d insn%s' % (
            self.name, ', '.join('p%d' % i for i in range(max(0, self.params))),
            self.start, self.end, len(self.insns), '' if len(self.insns) == 1 else 's')
        lines = [head]
        for i in self.insns:
            lines.append('    ' + repr(i))
        if self.error:
            lines.append('    !! %s' % self.error)
        return '\n'.join(lines)


class GscScript(object):
    """A parsed compiled script: tables from gsc_spt, code from the cseg walk here."""

    def __init__(self, data, path='<asset>'):
        self.raw = bytes(data)
        self.source_endian, self.data = _to_console(self.raw)
        self.spt = gsc_spt.Script(path, self.data)
        self.functions = []
        self.warnings = []
        if self.source_endian == 'pc':
            self.warnings.append(
                'this script is PC LITTLE-ENDIAN, not console big-endian; transcoded for reading')
        self._strfix_map = self._build_strfix_map()
        self._import_map = self._build_import_map()
        self._disassemble()

    # ------------------------------------------------------------------ tables
    def _build_strfix_map(self):
        """{code_address: string} from the stringtablefixup table.

        Each record is {str_off u16, num_addr u8, type u8} followed by num_addr x u32 code
        addresses. The operand at each address is a PLACEHOLDER the loader overwrites, so the
        table is the only way to know what a GetString really refers to.
        """
        out = {}
        d = self.data
        p = self.spt.stfix_off
        for _ in range(self.spt.stfix_n):
            soff, num_addr, _ty = struct.unpack_from('>HBB', d, p)
            s = self.spt._str(soff)
            p += 4
            for _i in range(num_addr):
                (a,) = struct.unpack_from('>I', d, p)
                out[a] = s
                p += 4
        return out

    def _build_import_map(self):
        """{code_address: Import} from the import table's per-call-site address lists."""
        out = {}
        d = self.data
        p = self.spt.imports_off
        for imp in self.spt.imports:
            noff, nsoff, num_addr, params, flags = struct.unpack_from('>HHHBB', d, p)
            p += 8
            for _i in range(num_addr):
                (a,) = struct.unpack_from('>I', d, p)
                out[a] = imp
                p += 4
        return out

    # ------------------------------------------------------------------ code
    def _function_bounds(self):
        """(export, start, end) per function, in address order.

        Function ends are derived from the next function's start; the last one ends at the end
        of the code segment. Genuine scripts 4-align each entry and precede it with a 6-byte
        {u16, u32} pad, so the raw next-start is an upper bound -- good enough for a listing,
        and the walk stops at End/Return anyway.
        """
        exps = sorted(self.spt.exports, key=lambda e: e.addr)
        cseg_end = self.spt.cseg_off + self.spt.cseg_size
        out = []
        for i, e in enumerate(exps):
            if i + 1 < len(exps):
                # Every function is preceded by a 6-byte `{u16, u32}` prefix pad and entered
                # 4-aligned, so the NEXT function's pad occupies exactly the 6 bytes before its
                # entry. Walking into it decodes pad bytes as instructions (they surface as
                # VectorScale/VectorConstant, 0x60/0x5E). ⚠ The pad's leading u16 is NOT always
                # zero in genuine scripts, so an "is the rest all zero?" test does not catch it
                # -- the bound has to be positional.
                end = exps[i + 1].addr
            else:
                end = cseg_end
            out.append((e, e.addr, min(end, cseg_end)))
        return out

    def _disassemble(self):
        for export, start, end in self._function_bounds():
            fn = Function(export, start, end)
            try:
                self._walk(fn)
            except Exception as ex:
                fn.error = '%s: %s' % (type(ex).__name__, ex)
                self.warnings.append('%s: %s' % (fn.name, fn.error))
            self.functions.append(fn)

    def _walk(self, fn):
        d = self.data
        o = fn.start
        end = fn.end
        while o < end:
            op = d[o]
            spec = gsc_diff.OPS.get(op)
            if spec is None:
                raise ValueError('unknown opcode 0x%02X at 0x%X' % (op, o))
            addr = o
            o += 1
            operands, comment = [], ''
            for tok in spec:
                if tok == 'u8':
                    operands.append(d[o])
                    o += 1
                elif tok == 'u16':
                    o = _align(o, 2)
                    (v,) = struct.unpack_from('>H', d, o)
                    operands.append(v)
                    o += 2
                elif tok == 'u32':
                    o = _align(o, 4)
                    (v,) = struct.unpack_from('>I', d, o)
                    operands.append(v)
                    o += 4
                elif tok == 'vec3':
                    o = _align(o, 4)
                    v = struct.unpack_from('>3f', d, o)
                    operands.append('(%g, %g, %g)' % v)
                    o += 12
                elif tok == 'lvars':
                    n = d[o]
                    o += 1
                    names = []
                    for _ in range(n):
                        o = _align(o, 2)
                        (nv,) = struct.unpack_from('>H', d, o)
                        names.append(self._strfix_map.get(o, '#%d' % nv))
                        o += 2
                    operands.append('%d' % n)
                    comment = 'locals: ' + ', '.join(names) if names else 'no locals'
                elif tok == 'switch':
                    # 0x5A EndSwitch -- the CASE TABLE, which is data embedded in the cseg and
                    # must be consumed, not decoded as instructions. Layout (measured):
                    #     opcode, align4, u32 count, count x {u32 value, u32 rel_addr}
                    # `case 1` encodes its value as 0x00800001 (0x00800000 flags an integer
                    # case; a string case carries a string offset), `default` as 0. Each
                    # rel_addr is relative to the position AFTER that addr word.
                    # Walking into this table produced 20 phantom instructions on a 107-
                    # instruction sample before it was handled.
                    o = _align(o, 4)
                    (ncase,) = struct.unpack_from('>I', d, o)
                    o += 4
                    cases = []
                    for _ in range(min(ncase, 4096)):
                        if o + 8 > len(d):
                            break
                        val, rel = struct.unpack_from('>II', d, o)
                        tgt = o + 8 + (rel - 0x100000000 if rel & 0x80000000 else rel)
                        cases.append('default' if val == 0 else
                                     ('case %d' % (val & 0x7FFFFF) if val & 0x00800000
                                      else 'case "%s"' % self._strfix_map.get(o, '#%d' % val)))
                        o += 8
                    operands.append('%d' % ncase)
                    comment = '; '.join(cases[:6]) + (' ...' if len(cases) > 6 else '')
                else:
                    raise ValueError('unhandled operand token %r' % tok)

            # resolve operands back to meaning
            if op in STR_OPS:
                s = self._strfix_map.get(addr + 1) or self._strfix_map.get(_align(addr + 1, 2))
                if s is not None:
                    comment = '"%s"' % s
            elif op in IMPORT_OPS:
                imp = self._import_map.get(addr)
                if imp is not None:
                    ns = (imp.ns + '::') if imp.ns else ''
                    comment = '%s%s/%d%s' % (ns, imp.name, imp.params,
                                             '  [devblock]' if imp.dev_only else '')
            elif op in (0x3B, 0x3C, 0x3D, 0x3E, 0x3F):
                if operands:
                    comment = '-> 0x%X' % (o + operands[-1])
            elif op == 0x40 and operands:
                comment = '-> 0x%X' % (o - operands[-1])

            fn.insns.append(Insn(addr, op, NAMES.get(op, 'op_%02X' % op),
                                 operands, comment, o - addr))
            # ⚠ Do NOT stop at End (0x00). End is a bare `return;` STATEMENT, not a function
            # terminator -- a function can and does continue past one:
            #     if (isdefined(e)) { return; }      <- End
            #     return a;                          <- EvalLocalVariableCached; Return
            # Stopping here silently truncated every function containing an early bare return
            # (measured: 2 instructions short on a 144-instruction sample, and it was the cause
            # of 13/185 corpus scripts failing to align against gsc-tool).
            # The real bound is the next function's entry, or the end of the cseg -- but that
            # range also covers the NEXT function's 6-byte `{u16 0, u32 0}` prefix pad plus any
            # 4-align filler, which would decode as a run of spurious `End`s. So a terminator
            # ends the function only when everything left really is that zero pad.
            # ⚠ KNOWN LIMITATION -- measured, not assumed.
            # `End` (0x00) is a bare `return;` STATEMENT, not a function terminator: a function
            # can continue past one (`if (x) { return; }` followed by more code). Stopping here
            # therefore truncates such functions.
            # But NOT stopping means walking into the next function's 6-byte `{u16 unk, u32 0}`
            # prefix pad plus 0-3 bytes of 4-align filler, which decodes as phantom
            # instructions (they surface as VectorScale/VectorConstant, 0x60/0x5E). The pad's
            # leading u16 is not always zero, so an all-zero test does not find it, and a
            # positional `next.addr - 6` bound cuts real trailing instructions.
            # MEASURED against gsc-tool, whole-corpus instruction-stream alignment:
            #     break here            172/185 scripts   <- best
            #     walk to next.addr      56/185           (phantom pad instructions)
            #     bound at next.addr-6  ~10/13 on a sample (truncates real instructions)
            # So this stays until the pad geometry is pinned down exactly. Affected functions
            # are under-listed, never mis-decoded; the listing is honest about where it stops.
            if op == 0x00:
                break

    # ------------------------------------------------------------------ animtree
    def animtree_walk(self):
        """Decoded animtree section walk. Returns (end_offset, entries) or raises.

        Entry layout (decoded 2026-08-07 by the gsc_spt session; see memory
        `gsc-animtree-header-12-bytes`):

            {name_off u16, num_single u16, num_pairs u16, pad u16}
            + num_single x u32                  bare code addresses
            + num_pairs  x {u32 name_off, u32 addr}

        The second u16 was modelled as padding everywhere, which is why the "header" appeared
        to be a VARIABLE 8 / 12 / 16 bytes wide. It never varied -- the field was simply unread:
            num_single=0 -> 8 B   num_single=1 -> 12 B   num_single=2 -> 16 B
        Confirmed against my own measurement of `clientscripts/mp/mp_drone_fx.csc`
        (`fxanim_props`): name_off=5072, num_single=2, num_pairs=21, pad=0
        => 8 + 2*4 + 21*8 = 184 B, exactly the measured 0x1EE1..0x1F99 region.
        """
        d = self.data
        s = self.spt
        p = s.animtree_off
        entries = []
        for _ in range(s.animtree_n):
            noff, n_single, n_pairs, pad = struct.unpack_from('>4H', d, p)
            p += 8 + n_single * 4 + n_pairs * 8
            entries.append(dict(name=s._str(noff), singles=n_single, pairs=n_pairs, pad=pad))
        return p, entries

    # ------------------------------------------------------------------ layout
    def layout_check(self):
        """Every table must end exactly where the next begins -- EXCEPT the animtree section,
        which is BOUNDARY-DRIVEN and must not be computed.

        Why: the animtree entry header size is not a constant. Measured values:
            8 + 8*n     the form in tools/gsc_spt.py
            12 + 8*n    the correction recorded for gsc_diff.py (fixed there 2026-07-27)
            16 + 8*n    clientscripts/mp/mp_drone_fx.csc -- 8-byte header plus TWO extra u32
                        before the {name_off, addr} pair array (name "fxanim_props", n=21,
                        region 0x1EE1..0x1F99 = 184 B = 16 + 8*21)

        Three different sizes across three samples is the signal to stop computing it. The
        header's own `stringtablefixup_offset` is authoritative, so the animtree section is
        simply "whatever lies between animtree_off and stfix_off". Every OTHER boundary is
        still checked arithmetically, because those formulas do hold.

        ⚠ FINDING: `tools/gsc_spt.py` still uses the 8 + 8*n form, so its `selfcheck()` reports
        a false layout failure on any script with an animtree. gsc_diff.py was corrected; the
        same correction was never applied to gsc_spt.py. Reported, not silently patched, because
        gsc_spt.py is shared with other work.
        """
        s = self.spt
        errs = []
        if s.cseg_off + s.cseg_size != s.exports_off:
            errs.append('cseg end 0x%X != exports 0x%X'
                        % (s.cseg_off + s.cseg_size, s.exports_off))
        if s.exports_off + s.exports_n * 12 != s.imports_off:
            errs.append('exports end 0x%X != imports 0x%X'
                        % (s.exports_off + s.exports_n * 12, s.imports_off))
        if s._imports_end != s.animtree_off:
            errs.append('imports end 0x%X != animtree 0x%X' % (s._imports_end, s.animtree_off))
        if s.include_off + s.include_n * 4 != s.cseg_off:
            errs.append('include end 0x%X != cseg 0x%X'
                        % (s.include_off + s.include_n * 4, s.cseg_off))
        # animtree -> stfix. The boundary stays AUTHORITATIVE (stfix_off comes from the header),
        # but now that the entry is decoded we can also gate it arithmetically, which is the
        # stronger check -- an exact adjacency is free evidence that the model is right.
        if s.animtree_off > s.stfix_off:
            errs.append('animtree 0x%X starts after stringtablefixup 0x%X'
                        % (s.animtree_off, s.stfix_off))
        elif s.animtree_n:
            try:
                end, _ents = self.animtree_walk()
                if end != s.stfix_off:
                    errs.append('animtree walk ends 0x%X != stringtablefixup 0x%X (%+d)'
                                % (end, s.stfix_off, s.stfix_off - end))
            except Exception as ex:
                errs.append('animtree walk failed: %s: %s' % (type(ex).__name__, ex))
        if s._strfix_end != s.fixup_off:
            errs.append('strfix end 0x%X != fixup 0x%X' % (s._strfix_end, s.fixup_off))
        return errs

    @property
    def animtree_bytes(self):
        return self.spt.stfix_off - self.spt.animtree_off

    # ------------------------------------------------------------------ output
    def header_summary(self):
        s = self.spt
        return {
            'embedded_name': s.name or '<empty>',
            'crc': '0x%08X' % s.crc,
            'flags': '0x%02X' % s.flags,
            'exports': len(s.exports),
            'imports': len(s.imports),
            'includes': len(s.includes),
            'strfix': len(s.strrefs),
            'animtrees': len(s.animtrees),
            'cseg_size': s.cseg_size,
            'bytes': len(self.data),
            'layout_errors': self.layout_check(),
        }

    def listing(self, max_functions=None):
        s = self.spt
        L = []
        L.append('; %s' % (s.name or '<no embedded name>'))
        L.append('; %d bytes, crc 0x%08X, flags 0x%02X' % (len(self.data), s.crc, s.flags))
        errs = self.layout_check()
        L.append('; layout: %s' % ('EOF-exact, every table abuts the next'
                                   if not errs else 'BROKEN -- ' + '; '.join(errs)))
        L.append('')
        if s.includes:
            for inc in s.includes:
                L.append('#include %s;' % inc)
            L.append('')
        if s.animtrees:
            for a in s.animtrees:
                L.append('#using_animtree("%s");' % a)
            L.append('')
        L.append('; ---- imports (%d) ----' % len(s.imports))
        for imp in s.imports:
            ns = (imp.ns + '::') if imp.ns else ''
            L.append(';   %s%s/%d  flags=0x%02X%s'
                     % (ns, imp.name, imp.params, imp.flags,
                        '  [devblock-only]' if imp.dev_only else ''))
        L.append('')
        fns = self.functions[:max_functions] if max_functions else self.functions
        for fn in fns:
            L.append(fn.listing())
            L.append('')
        if max_functions and len(self.functions) > max_functions:
            L.append('; ... %d more function(s)' % (len(self.functions) - max_functions))
        return '\n'.join(L)


# ---------------------------------------------------------------------------- helpers

def is_gsc(buf):
    return bool(buf) and (buf.startswith(MAGIC) or buf.startswith(b'GSC\r\n'))


def disassemble(buf, path='<asset>', max_functions=None):
    return GscScript(buf, path).listing(max_functions=max_functions)


def lint(buf, path='<asset>', corpus_dir=None):
    """Run the 6-point static pre-boot checklist, if the checker is importable.

    Returns (ok, lines). A checker that cannot run reports `ok=None` -- not a pass.
    """
    try:
        import gsc_preboot_check  # noqa: F401
    except Exception as ex:
        return None, ['pre-boot checker unavailable: %s: %s' % (type(ex).__name__, ex)]
    try:
        s = GscScript(buf, path)
    except Exception as ex:
        return False, ['not a parseable T6 script: %s: %s' % (type(ex).__name__, ex)]
    lines = []
    errs = s.layout_check()
    if errs:
        lines += ['LAYOUT: ' + e for e in errs]
    for fn in s.functions:
        if fn.error:
            lines.append('DISASM: %s: %s' % (fn.name, fn.error))
    return (not lines), (lines or ['no structural problems found'])
