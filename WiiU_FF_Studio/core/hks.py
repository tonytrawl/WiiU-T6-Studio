"""core.hks -- native HavokScript (T6 LUI Lua) container model + surgical patcher.

Roadmap P6.1 / P6.2. No hksc, no Docker, no dotnet decompiler.

WHY THIS SHAPE
--------------
`dlc loading/native/fullrelink/lua_endian.py` walks the format, but only to swap bytes -- it
builds no model, so nothing can inspect or edit a script. It also HARDCODES the 12-byte
function footer (`i32, f32, i32 subcount`).

This module DETECTS the footer variant rather than assuming it, using the only test that cannot
lie: the parse must consume the buffer exactly.

⚠ MEASUREMENT vs THE RECORDED MEMORY -- unresolved, flagged deliberately
------------------------------------------------------------------------
`hks-footer-patchmp-variant` records that `patch_mp` LUI rawfiles use an **8-byte** footer
(`i32 + i32 subcount`, no float) while `patch_ui_*` keeps the 12-byte form, established by
brute force on `ui/t6/options.lua` (35,878 B at the time).

Measured here 2026-08-07 against the LIVE files:
    patch_ui_zm.ff   45/45  HKS scripts parse exactly with footer=12
    patch_mp.ff     330/330 HKS scripts parse exactly with footer=12   <-- not 8

So on these files the 8-byte form is not needed anywhere. The likely explanation is that every
available copy of `options.lua` is MODIFIED -- it measures 44,734 B in the live file and
46,656 B in `patch_mp.ff.bak`, against the memory's genuine 35,878 B, and this project has
spliced that script repeatedly (FOV slider, TWEAKS submenu, in-menu console). A truly pristine
`patch_mp` was not available to settle it.

Do NOT treat this as refuting the memory. Both readings are kept: the detector tries 12 then 8,
and records which one won, so whichever is true for a given file is handled and visible.

WHAT THIS ENABLES TODAY (P6.2)
------------------------------
The surgical patcher. Most real menu edits are "change this string" or "change this number",
and the project's own guidance is emphatic that big frontend scripts must NOT be
decompiled-and-recompiled (the reference decompiler mangles `mainmenu.lua`, and the recompile
fails with zero functional edits). Editing a constant in place sidesteps that entirely -- and
removes the i386 Docker container from the loop for the common case.

SCOPE -- what is deliberately NOT here
--------------------------------------
Instruction-level disassembly (P6.3) needs the HavokScript opcode table, which is a distinct
research task: HKS is Lua 5.1 plus Havok extensions and the opcode numbering is not Lua's. This
module models the CONTAINER exactly and treats each instruction as an opaque 4-byte word. That
is honest and still sufficient for P6.2.
"""
import struct

from . import paths  # noqa: F401

MAGIC = b'\x1bLua'

T_NIL, T_BOOL, T_NUMBER, T_STRING, T_HASH = 0, 1, 3, 4, 13


class HksError(Exception):
    pass


class Constant(object):
    __slots__ = ('type', 'value', 'off', 'size')

    def __init__(self, type_, value, off, size):
        self.type, self.value, self.off, self.size = type_, value, off, size

    @property
    def type_name(self):
        return {T_NIL: 'nil', T_BOOL: 'bool', T_NUMBER: 'number',
                T_STRING: 'string', T_HASH: 'hash'}.get(self.type, 'type%d' % self.type)

    def __repr__(self):
        v = self.value
        if self.type == T_STRING:
            v = '"%s"' % (v.decode('latin1', 'replace').rstrip('\x00')[:60])
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
        self.constants = []
        self.subs = []
        self.off = 0
        self.end = 0
        self.index = 0

    def walk(self):
        yield self
        for s in self.subs:
            for x in s.walk():
                yield x

    def __repr__(self):
        return '<Proto #%d params=%d regs=%d instr=%d consts=%d subs=%d>' % (
            self.index, self.params, self.registers, self.n_instr,
            len(self.constants), len(self.subs))


class HksFile(object):
    """A parsed T6 HavokScript chunk.

    `footer_size` is 12 (i32,f32,i32) or 8 (i32,i32) -- detected, never assumed.
    """

    def __init__(self, blob, footer_size=None):
        self.b = bytes(blob)
        if self.b[:4] != MAGIC:
            raise HksError('not a Lua/HavokScript chunk (magic %r)' % self.b[:4])
        self.little = (self.b[6] == 1)
        self.e = '<' if self.little else '>'
        self.footer_size = footer_size
        self.const_types = []
        self.root = None
        self.consumed = 0
        self.trailing = 0
        if footer_size is None:
            self._detect()
        else:
            self._parse(footer_size)
            self.trailing = len(self.b) - self.consumed

    # ------------------------------------------------------------------ detection
    def _detect(self):
        """Try each known footer layout; prefer the one that consumes the buffer exactly.

        A wrong footer size desyncs the recursive walk almost immediately, so
        'consumed == len(buffer)' is a very strong discriminator.

        ⚠ A parse that completes the whole proto tree but leaves a TRAILING remainder is still
        a valid parse: the remainder is a trailing section (retained debug info in a chunk that
        was not compiled with `-s`), not a structural failure. Measured: 1 of 45 scripts in
        patch_ui_zm leaves 12 B, 1 of 330 in patch_mp leaves 106 B, both at the very end. Those
        are accepted with `trailing` recorded, because refusing them would hide a whole class of
        editable script. A remainder is NEVER silently ignored -- `exact` is False and callers
        that resize must preserve the trailing bytes.
        """
        errs = []
        partial = None
        for fs in (12, 8):
            try:
                self._parse(fs)
                if self.consumed == len(self.b):
                    self.footer_size = fs
                    self.trailing = 0
                    return
                if partial is None or (len(self.b) - self.consumed) < partial[1]:
                    partial = (fs, len(self.b) - self.consumed)
                errs.append('footer=%d left %d B' % (fs, len(self.b) - self.consumed))
            except Exception as ex:
                errs.append('footer=%d %s: %s' % (fs, type(ex).__name__, str(ex)[:70]))
        if partial is not None:
            self._parse(partial[0])
            self.footer_size = partial[0]
            self.trailing = partial[1]
            return
        raise HksError('no footer layout parses this chunk: ' + '; '.join(errs))

    # ------------------------------------------------------------------ parse
    def _u32(self, p):
        return struct.unpack_from(self.e + 'I', self.b, p)[0], p + 4

    def _parse(self, footer_size):
        self._fs = footer_size
        self.const_types = []
        p = 14
        n, p = self._u32(p)
        for _ in range(n):
            _id, p = self._u32(p)
            ln, p = self._u32(p)
            self.const_types.append(self.b[p:p + ln])
            p += ln
        self._counter = 0
        self.root, p = self._proto(p)
        self.consumed = p

    def _proto(self, p):
        b = self.b
        pr = Proto()
        pr.off = p
        pr.index = self._counter
        self._counter += 1
        pr.upvals, p = self._u32(p)
        pr.params, p = self._u32(p)
        pr.vararg = b[p]
        p += 1
        pr.registers, p = self._u32(p)
        pr.n_instr, p = self._u32(p)
        extra = 4 - (p % 4)
        if 0 < extra < 4:
            p += extra
        pr.instr_off = p
        p += 4 * pr.n_instr
        nc, p = self._u32(p)
        for _ in range(nc):
            t = b[p]
            c_off = p
            p += 1
            if t == T_NIL:
                val = None
            elif t == T_BOOL:
                val = bool(b[p])
                p += 1
            elif t == T_NUMBER:
                val = struct.unpack_from(self.e + 'f', b, p)[0]
                p += 4
            elif t == T_STRING:
                ln, p = self._u32(p)
                val = b[p:p + ln]
                p += ln
            elif t == T_HASH:
                val = struct.unpack_from(self.e + 'Q', b, p)[0]
                p += 8
            else:
                raise HksError('constant type %d at 0x%x' % (t, c_off))
            pr.constants.append(Constant(t, val, c_off, p - c_off))
        p += self._fs - 4          # footer, minus the subcount we read next
        ns, p = self._u32(p)
        for _ in range(ns):
            sub, p = self._proto(p)
            pr.subs.append(sub)
        pr.end = p
        return pr, p

    # ------------------------------------------------------------------ views
    @property
    def protos(self):
        return list(self.root.walk()) if self.root else []

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
        return dict(endian='LE' if self.little else 'BE',
                    footer_size=self.footer_size,
                    bytes=len(self.b), consumed=self.consumed,
                    exact=self.consumed == len(self.b), trailing=self.trailing,
                    const_types=len(self.const_types),
                    protos=len(ps),
                    instructions=sum(p.n_instr for p in ps),
                    constants=sum(len(p.constants) for p in ps),
                    strings=len(self.strings()))

    def listing(self, max_protos=40):
        s = self.summary()
        L = ['; HavokScript chunk, %s, footer %d B, %d bytes%s'
             % (s['endian'], s['footer_size'], s['bytes'],
                '' if s['exact'] else '  !! PARSE NOT EXACT'),
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

    # ------------------------------------------------------------------ patching
    def patch_constant(self, proto_index, const_index, new_value):
        """Surgical in-place constant edit (P6.2). Returns new chunk bytes.

        Strings may change length; the caller then owns the size delta (the rawfile grow path).
        A shorter string is NOT auto-padded -- the length prefix is rewritten, which is the
        correct representation and keeps the file self-consistent.
        """
        pr = next((p for p in self.protos if p.index == proto_index), None)
        if pr is None:
            raise HksError('no proto #%d' % proto_index)
        if not (0 <= const_index < len(pr.constants)):
            raise HksError('proto #%d has %d constants' % (proto_index, len(pr.constants)))
        c = pr.constants[const_index]
        if c.type == T_STRING:
            if isinstance(new_value, str):
                new_value = new_value.encode('latin1', 'replace')
            if not new_value.endswith(b'\x00'):
                new_value += b'\x00'
            body = struct.pack(self.e + 'I', len(new_value)) + new_value
            blob = bytes([T_STRING]) + body
        elif c.type == T_NUMBER:
            blob = bytes([T_NUMBER]) + struct.pack(self.e + 'f', float(new_value))
        elif c.type == T_BOOL:
            blob = bytes([T_BOOL, 1 if new_value else 0])
        elif c.type == T_HASH:
            blob = bytes([T_HASH]) + struct.pack(self.e + 'Q', int(new_value))
        else:
            raise HksError('cannot patch a %s constant' % c.type_name)
        out = self.b[:c.off] + blob + self.b[c.off + c.size:]
        return out

    def replace_string(self, old, new):
        """Replace every string constant equal to `old`. Returns (bytes, count)."""
        if isinstance(old, str):
            old = old.encode('latin1', 'replace')
        target = old if old.endswith(b'\x00') else old + b'\x00'
        n = 0
        cur = self
        blob = self.b
        while True:
            hit = next(((pi, ci, c) for pi, ci, c in cur.strings() if c.value == target), None)
            if hit is None:
                break
            blob = cur.patch_constant(hit[0], hit[1], new)
            cur = HksFile(blob, footer_size=self.footer_size)
            n += 1
            if n > 4096:
                raise HksError('replace_string did not converge')
        return blob, n


# ---------------------------------------------------------------------------- helpers

def is_hks(buf):
    return bool(buf) and buf[:4] == MAGIC


def parse(buf):
    return HksFile(buf)


def transcode(buf, want_le):
    """BE<->LE, delegated to the validated transcoder (45/45 corpus)."""
    import lua_endian
    return lua_endian.transcode(buf, want_le)[0]
