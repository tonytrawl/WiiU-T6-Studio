#!/usr/bin/env python3
"""
PC (v147, LE) -> console (WiiU v148, BE) asset-body CONVERTER — pipeline core.

For asset types whose PC and console struct layouts are identical (the "simple"
FOLLOW-only types: StringTable, KeyValuePairs, RawFile, ScriptParseTree, Localize,
FootstepTable, SkinnedVerts, Leaderboard, ...), converting a PC body to console is a
structural byte-swap: walk the body's fields (struct_layout) and dynamic children
(ZoneCode DFS directives), emit every scalar big-endian, keep FOLLOW/null sentinels,
copy raw string/char bytes verbatim, and remap alias pointers through an offset map.

This module converts one such PC body and returns the console bytes. It is validated
against the genuine console zone as an oracle (convert PC body -> must equal genuine
console body, byte-for-byte). Complex GX2-divergent types (image/techset/xmodel/world)
are NOT handled here; the assembler falls back to genuine console bodies for those.
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import struct_layout, walker as W

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE

# Simple types with identical PC/console layout, converted structurally here.
# (SkinnedVertsDef excluded: console adds a trailing u32 / FOLLOW-name divergence.)
SIMPLE = {
    'StringTable', 'KeyValuePairs', 'RawFile', 'ScriptParseTree', 'LocalizeEntry',
    'FootstepTableDef', 'LeaderboardDef', 'FxImpactTable',
}
# World assets whose PC and console struct layouts are identical (byte-swap convertible).
# These are the OAT crash points — the native pipeline authors them with correct cross-refs.
# GfxWorld is NOT here (console-only gump/lighting/dpvs -> Stage-3 synthesis).
WORLD = {
    'ComWorld', 'MapEnts', 'GameWorldMp', 'clipMap_t',
}
# GfxLightDef is NOT a clean byte-swap: it embeds an inline console GX2 cookie image
# (8548B on mp_raid) -> complex type, handled via the image converter / genuine fallback.

# FIX B (boot-53 name-drift class): roots safe for INLINE name re-emission when
# the PC name word is a dedup alias. Name must be FIELD 0, and the root must
# NOT be allocation-event-paired (EVENT_FINE roots — clipMap_t/GameWorldMp/
# SndBank — must not gain a console-only allocation event, or the PC<->console
# event pairing the fine maps rely on shifts by one).
NAME_INLINE_ROOTS = {
    'StringTable', 'KeyValuePairs', 'RawFile', 'ScriptParseTree',
    'FootstepTableDef', 'LeaderboardDef', 'FxImpactTable',
    'ComWorld', 'MapEnts',
}   # LocalizeEntry excluded: name @+4, not field 0

B5_BASE = 64   # block-5 offset == stream offset - 64
BLOCK5_LO = 0xA0000001
BLOCK5_HI = 0xBFFFFFFF
HIALIAS_LO = 0xC0000000          # high "streamed-ref" alias class (console block bump)
HIALIAS_HI = 0xE0000000
VEC_TYPES = {'vec2_t', 'vec3_t', 'vec4_t'}   # float-vector typedefs (mis-parsed layout)


class PCConverter:
    """Reads PC (LE) bytes, emits console (BE) bytes for one simple asset."""
    def __init__(self, pc_zone, layout, zc, pc_base=0, co_base=0):
        self.z = pc_zone
        self.L = layout
        self.zc = zc
        self.out = bytearray()
        self.wk = W.Walker(pc_zone, layout, zc, [0]*8)   # scalar/count/cond/followers helper
        # override its readers to LE (PC source)
        self.wk.u16 = lambda o: struct.unpack_from('<H', pc_zone, o)[0]
        self.wk.u32 = lambda o: struct.unpack_from('<I', pc_zone, o)[0]
        self.wk._scalar = self._scalar_le
        self.src = 0
        # relocation: PC block-5 offset -> console block-5 offset for every emitted
        # region, plus pending alias fixups (out_pos, pc_target_off) to patch once the
        # target has been registered (single forward pass resolves back-references).
        self.co0 = None                 # console block-5 offset of out position 0 (set on 1st convert)
        self.regions = []               # (pc_b5_start, co_b5_start, length) registered in emit order
        self.fixups = []                # (out_pos, pc_target_b5) block-5 alias relocations

    # ---- relocation bookkeeping ----
    def _reg(self, pc_stream_start, out_pos_before):
        """Register a written region: pc block-5 start -> console block-5 start, length."""
        length = len(self.out) - out_pos_before
        self.regions.append((pc_stream_start - B5_BASE, self.co0 + out_pos_before, length))

    def _remap_b5(self, pc_b5):
        for ps, cs, ln in self.regions:
            if ps <= pc_b5 < ps + ln:
                return cs + (pc_b5 - ps)
        return None                     # target not (yet) registered -> forward ref

    def finalize(self):
        """Patch all block-5 alias fixups now that every region is registered.
        Fixups outside this converter's own regions (targets in complex-type bodies
        it didn't emit) fall back to `ext_reloc` (the assemble loop's shared Omap)."""
        unresolved = 0
        ext = getattr(self, 'ext_reloc', None)
        # encode hook: the assemble loop's Omap._encode maps our-stream offsets
        # through the loader-simulation runtime map before encoding (pointer pass)
        enc = getattr(self, 'encode', None) or (lambda co_b5: 0xA0000000 + co_b5 + 1)
        inv = getattr(self, 'pc_inv', None)
        for out_pos, pc_b5 in self.fixups:
            if inv is not None:
                # PC alias values encode PC RUNTIME addresses -> PC stream first
                pc_b5 = inv.stream(pc_b5)
            co_b5 = self._remap_b5(pc_b5)
            if co_b5 is None:
                if ext is not None:
                    v = 0xA0000000 + pc_b5 + 1
                    nv = ext(v)
                    if nv != v:
                        struct.pack_into('>I', self.out, out_pos, nv)
                        continue
                unresolved += 1
                continue
            struct.pack_into('>I', self.out, out_pos, enc(co_b5))
        return unresolved

    def _scalar_le(self, base, o):
        s, _ = self.L._resolve(base)
        if s == 1: return self.z[o]
        if s == 2: return struct.unpack_from('<H', self.z, o)[0]
        return struct.unpack_from('<I', self.z, o)[0]

    def _pc_alias_cstring(self, v):
        """Resolve a PC b5-alias word to the C-string it targets (via pc_inv),
        or None if unavailable / not string-like."""
        inv = getattr(self, 'pc_inv', None)
        if inv is None:
            return None
        o = inv.stream((v - 1) & 0x1FFFFFFF) + B5_BASE
        if o < B5_BASE or o >= len(self.z):
            return None
        try:
            e = self.z.index(b'\x00', o, o + 96)
        except ValueError:
            return None
        s = self.z[o:e]
        if len(s) < 3 or any(c < 0x20 or c > 0x7e for c in s):
            return None
        return bytes(s)

    def lu32(self, o): return struct.unpack_from('<I', self.z, o)[0]

    # ---- pure recursive body byte-swap (fills `buf` at buf_off; collects fixups
    #      as (relative_out_off, pc_target_b5)) — no self.out side effects ----
    def _swap_body(self, struct_name, off, buf, buf_off, fixups):
        s = self.wk.gs(struct_name)
        size = s['size']
        buf[buf_off:buf_off+size] = self.z[off:off+size]   # raw copy (covers padding)
        for f in s['fields']:
            if 'error' in f:
                continue
            fo = f['offset']
            base = f['base']
            arr = max(f['arr'], 1)
            if f.get('is_ptr'):
                for k in range(arr):
                    o = fo + k*4
                    v = struct.unpack_from('<I', self.z, off + o)[0]
                    if BLOCK5_LO <= v <= BLOCK5_HI:        # block-5 alias -> omap relocate
                        struct.pack_into('>I', buf, buf_off + o, v)
                        fixups.append((buf_off + o, (v - 1) & 0x1FFFFFFF))
                    elif HIALIAS_LO <= v < HIALIAS_HI:     # high streamed-ref: +0x10000000 block bump
                        struct.pack_into('>I', buf, buf_off + o, (v + 0x10000000) & 0xFFFFFFFF)
                    else:
                        struct.pack_into('>I', buf, buf_off + o, v)   # sentinels/other symmetric
                continue
            if base in VEC_TYPES and not f.get('is_ptr'):
                # float-vector typedefs (vec2/3/4_t): struct_layout mis-parses their
                # inner x/y/z offsets (all 0), so handle as contiguous 4-byte words.
                total = f['size']
                for w in range(total // 4):
                    struct.pack_into('>I', buf, buf_off + fo + w*4,
                                     struct.unpack_from('<I', self.z, off + fo + w*4)[0])
                continue
            if base in self.L.structs and not f.get('is_ptr'):
                esz = self.L._resolve(base)[0]
                for k in range(arr):
                    self._swap_body(base, off + fo + k*esz, buf, buf_off + fo + k*esz, fixups)
                continue
            esz = f['size'] // arr                          # scalar field (possibly array)
            for k in range(arr):
                o = fo + k*esz
                if esz == 1:
                    continue                                # bytes already copied
                elif esz == 2:
                    struct.pack_into('>H', buf, buf_off + o, struct.unpack_from('<H', self.z, off + o)[0])
                elif esz == 8:
                    struct.pack_into('>Q', buf, buf_off + o, struct.unpack_from('<Q', self.z, off + o)[0])
                elif esz % 4 == 0:
                    # 4-byte-element scalars: float, int, AND float-vector typedefs
                    # (vec2/3/4_t = 8/12/16B) that struct_layout treats as one scalar.
                    for w in range(esz // 4):
                        struct.pack_into('>I', buf, buf_off + o + w*4,
                                         struct.unpack_from('<I', self.z, off + o + w*4)[0])
                # other odd sizes: leave as raw copy
        return size

    # ---- emit one struct body (BE), byte-swapping scalars ----
    def emit_body(self, struct_name, off):
        s = self.wk.gs(struct_name)
        size = s['size']
        buf = bytearray(size)
        out_base = len(self.out)
        local = []
        self._swap_body(struct_name, off, buf, 0, local)
        for rel, pcb5 in local:
            self.fixups.append((out_base + rel, pcb5))
        self.out += buf
        self._reg(off, out_base)
        self.src = off + size

    def emit_cstring(self):
        end = self.z.index(b'\x00', self.src)
        out_base = len(self.out)
        self.out += self.z[self.src:end+1]
        self._reg(self.src, out_base)
        self.src = end + 1

    def emit_raw(self, nbytes):
        out_base = len(self.out)
        self.out += self.z[self.src:self.src+nbytes]
        self._reg(self.src, out_base)
        self.src += nbytes

    def follow(self, struct_name, body, ctx):
        c = dict(ctx)
        self.wk.read_scalars(struct_name, body, c)
        for nm, f, d in self.wk.followers(struct_name):
            fo = body + f['offset']
            base = f['base']

            if d.get('arraysize') and base in self.L.structs:
                count = self.wk.eval_count(d['arraysize'], c)
                esz = self.wk.gs(base)['size']
                for i in range(max(count, 0)):
                    self.follow(base, fo + i*esz, c)
                continue

            if f.get('is_ptr') and f['arr'] > 1:
                if not self.wk.eval_cond(d.get('condition'), c):
                    continue
                for i in range(f['arr']):
                    if self.lu32(fo + i*4) not in (FOLLOW, INSERT):
                        continue
                    if d.get('string'):
                        self.emit_cstring(); continue
                    cnt = self.wk.eval_count(d['count'], c) if d.get('count') else 1
                    if cnt > 0:
                        self.emit_array(base, cnt)
                continue

            if not f.get('is_ptr') and base in self.L.structs and f['arr'] == 1:
                self.follow(base, fo, c)
                continue
            if not f.get('is_ptr'):
                continue

            if not self.wk.eval_cond(d.get('condition'), c):
                continue
            if self.lu32(fo) not in (FOLLOW, INSERT):
                continue
            if d.get('string'):
                self.emit_cstring(); continue
            if d.get('assetref'):
                continue
            count = self.wk.eval_count(d['count'], c) if d.get('count') else 1
            if count <= 0:
                continue
            self.emit_array(base, count)

    def emit_array(self, base, count):
        if base in self.L.structs:
            s = self.wk.gs(base)
            if s['align'] > 4:
                pad = (-len(self.out)) % s['align']
                self.out += b'\x00' * pad
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
            sz = self.L._resolve(base)[0]
            n = count * sz
            src0 = self.src
            out_base = len(self.out)
            if sz in (2, 4, 8):
                for k in range(count):
                    v = int.from_bytes(self.z[self.src+k*sz:self.src+(k+1)*sz], 'little')
                    self.out += v.to_bytes(sz, 'big')
            else:
                self.out += self.z[self.src:self.src+n]
            self._reg(src0, out_base)
            self.src += n

    def convert(self, root, pc_body, co_stream_start, keep_regions=False):
        """Convert one simple asset (PC stream offset pc_body) -> console BE bytes.
        co_stream_start = the console stream offset where this asset's body lands
        (to relocate self/back-referencing block-5 aliases). Per-call out/fixups reset;
        `regions` (the global PC->console offset map) PERSIST when keep_regions=True so
        later assets' aliases into earlier assets resolve (needs assets converted in
        stream order with their true console starts)."""
        self.out = bytearray()
        self.src = pc_body
        self.fixups = []
        if not keep_regions:
            self.regions = []
        self.co0 = co_stream_start - B5_BASE
        self.emit_body(root, pc_body)
        # FIX B (boot-53 name-drift class): aliased root NAME -> FOLLOW+string.
        # Name is field 0 for every NAME_INLINE_ROOTS root, so its payload
        # belongs immediately after the fixed body (before follow()'s output).
        if getattr(self, 'inline_names', False) and root in NAME_INLINE_ROOTS:
            v = self.lu32(pc_body)
            if BLOCK5_LO <= v <= BLOCK5_HI:
                nm = self._pc_alias_cstring(v)
                if nm:
                    struct.pack_into('>I', self.out, 0, FOLLOW)
                    self.fixups = [f for f in self.fixups if f[0] != 0]
                    self.out += nm + b'\x00'
        self.follow(root, pc_body, {})
        self.unresolved = self.finalize()
        return bytes(self.out), self.src
