#!/usr/bin/env python3
"""
LANE B -- PC(LE) -> console(BE) converters for TracerDef + WeaponCamo, the two
ZM "smalls" whose bodies can EMBED full inline sub-assets (see
HANDOFF_conv_B_tracer_camo.md).

Both roots' field grammars already exist in the parsed T6_Assets.h layout
(struct_layout.Layout) -- TracerDef, WeaponCamo, WeaponCamoSet,
WeaponCamoMaterialSet, WeaponCamoMaterial. Rather than re-reverse them, this
module drives that grammar in EMIT mode: a recursive walker that mirrors
walker.py's follow_dynamics ordering (all array bodies first, then each
element's dynamics) but writes big-endian, field-aware, with:

  * pointers      -> reloc(value) packed BE (FOLLOW/INSERT/null pass through);
  * scalars       -> swapped by their PRIMITIVE element size (uint16 halves
                     swap independently -- WeaponCamoMaterial.replaceFlags /
                     numBaseMaterials would be corrupted by a blind 4-byte swap);
  * inline strings -> copied verbatim (chars, incl. terminator);
  * inline assets  -> dispatched to the existing byte-exact converters
                     (material_convert.convert_material / convert_image), which
                     already own the Material -16/-32 size class and the GX2
                     inline-image emit. These sub-asset bodies are NOT byte-
                     identical to genuine -- same documented exception classes
                     as Track A Material / GfxImage (see FINDINGS_conv_b.md).

Entry points (both return (console_bytes, pc_end)):
    convert_tracerdef(pc, off, reloc=identity)
    convert_weaponcamo(pc, off, reloc=identity)

`pc_end` is the PC-stream offset just past the whole inline body (for the
no-backbone walk / produce_nobackbone dispatch).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))

import struct_layout
import walker as W
import material_convert as MC

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE

# One shared PC-mode layout + zone-code (field directives: string/count/...).
# W.HDR / W.ZC_DIR are relative to wiiu_ref/; resolve them absolutely so this
# module works regardless of the caller's cwd.
_WREF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref')
_HDR = W.HDR if os.path.isabs(W.HDR) else os.path.normpath(os.path.join(_WREF, W.HDR))
_ZCDIR = W.ZC_DIR if os.path.isabs(W.ZC_DIR) else os.path.normpath(os.path.join(_WREF, W.ZC_DIR))
_L = struct_layout.Layout(_HDR, console=False)
_ZC = W.ZoneCode(_ZCDIR)

# Asset-typed members that carry a full inline asset when FOLLOW/INSERT.
_ASSET_CONVERT = {
    'Material': MC.convert_material,
    'GfxImage': MC.convert_image,
}


# Rule (EX): asset kinds this converter can never name, because a zm map zone
# emits ZERO instances of them (camo/tracer textures are streamed from the ipak
# by name, not referenced as zone assets). A non-inline pointer to one of these
# is UNREPRESENTABLE -- emit NULL rather than let reloc's interior-approximate
# fallback invent a target. See _Emit._ptrval for the boot-73 evidence.
UNREPRESENTABLE_PTR_BASES = ('GfxImage',)


def _identity(v):
    return v


class _Emit:
    """Grammar-driven BE emitter over one PC inline body."""

    def __init__(self, pc, reloc):
        self.pc = pc
        self.reloc = reloc
        self.out = bytearray()
        self.pcur = 0            # PC read cursor for dynamic (inline) data
        self.refused_ptrs = []   # rule (EX): unrepresentable pointers nulled

    # -- primitive reads (PC is little-endian) --
    def _u8(self, o):
        return self.pc[o]

    def _u16(self, o):
        return struct.unpack_from('<H', self.pc, o)[0]

    def _u32(self, o):
        return struct.unpack_from('<I', self.pc, o)[0]

    def _ptrval(self, v, base=None):
        """⛔ RULE (EX) -- AN UNREPRESENTABLE POINTER MUST BE NULL, NEVER GUESSED.
        BOOT-73 CRASH, root-caused 2026-08-14 (dump Cemu.exe.7608.dmp).

        `Omap.reloc`'s last resort is an INTERIOR-APPROXIMATE region match: if a
        PC offset falls anywhere inside a region we mapped, it returns
        `cs + (b5 - ps)` -- i.e. it assumes the console body has the PC body's
        internal layout. For a pointer at an ASSET boundary that assumption is
        not merely imprecise, it is categorically wrong, and it answers
        confidently instead of refusing.

        MEASURED: all **60** non-inline `WeaponCamo.solidBaseImage` /
        `patternBaseImage` pointers in b73 resolved to block-5 aliases landing
        **inside WEAPON bodies** -- 60/60, not one at a GfxImage. In b70 all 60
        were NULL. On the first render frame `R_Stream_ForceLoadLowDetail`
        enumerates WEAPON_CAMO (XAssetType 32) and `R_StreamEnumCamoForce`
        dereferences them; NULL was skipped, garbage was not. It read alias
        words as image fields, overran while building the initial image list,
        clobbered the caller's callee-saved r30, and faulted at
        `lhz r8, 10(r30)` in DB_EnumXAssets_FastFile+0x1c4. A pointer that was
        null for 70 builds became non-null and wrong, ARMING a dormant path --
        the same shape as the dangling-FOLLOW class.

        WHY NULL IS THE CORRECT ANSWER AND NOT MERELY b70'S ANSWER: this zone
        emits **ZERO IMAGE assets** (measured: no image type id in the asset
        array at all -- camo textures are streamed from the ipak by name). So
        there exists NO console GfxImage for a cross-asset camo image pointer to
        name. The only representable forms are an INLINE image (FOLLOW, which
        still works -- 2 of the 62 take that path) or NULL. Approximating is the
        one option guaranteed to be wrong.

        Kept deliberately narrow: only pointer bases in UNREPRESENTABLE_PTR_BASES
        are refused, and FOLLOW/INSERT/0 are untouched, so inline images and
        every other field behave exactly as before."""
        if v in (FOLLOW, INSERT, 0):
            return v
        if base in UNREPRESENTABLE_PTR_BASES:
            self.refused_ptrs.append(base)
            return 0
        return self.reloc(v)

    # -- fixed struct body: field-aware BE, no dynamics --
    def emit_fixed(self, name, off):
        s = _L.get(name)
        buf = bytearray(s['size'])
        for f in s['fields']:
            if 'error' in f:
                continue
            base = f['base']
            bo = f['offset']
            fo = off + bo
            if f['is_ptr']:
                for i in range(f['arr']):
                    struct.pack_into('>I', buf, bo + i * 4,
                                     self._ptrval(self._u32(fo + i * 4), base))
            elif base in _L.structs:
                sz = _L.get(base)['size']
                for i in range(f['arr']):
                    buf[bo + i * sz:bo + (i + 1) * sz] = self.emit_fixed(base, fo + i * sz)
            else:
                prim = _L._resolve(base)[0]
                n = f['size'] // max(prim, 1)
                for i in range(n):
                    self._put_scalar(buf, bo + i * prim, fo + i * prim, prim)
        return bytes(buf)

    def _put_scalar(self, buf, bo, fo, prim):
        if prim == 1:
            buf[bo] = self.pc[fo]
        elif prim == 2:
            struct.pack_into('>H', buf, bo, self._u16(fo))
        elif prim == 4:
            struct.pack_into('>I', buf, bo, self._u32(fo))
        elif prim == 8:
            # WiiU has no 8-align on these structs; emit as two BE words to keep
            # any (unexpected) 64-bit scalar value-correct.
            hi = self._u32(fo)
            lo = self._u32(fo + 4)
            struct.pack_into('>II', buf, bo, hi, lo)
        else:
            buf[bo:bo + prim] = self.pc[fo:fo + prim]

    # -- ctx: scalar field values of a struct body (for count exprs) --
    def _read_ctx(self, name, body):
        ctx = {}
        s = _L.get(name)
        for f in s['fields']:
            if 'error' in f or f['is_ptr']:
                continue
            base = f['base']
            fo = body + f['offset']
            if base in _L.structs:
                ctx.update(self._read_ctx(base, fo))
                continue
            prim = _L._resolve(base)[0]
            if prim == 1:
                ctx[f['name']] = self._u8(fo)
            elif prim == 2:
                ctx[f['name']] = self._u16(fo)
            else:
                ctx[f['name']] = self._u32(fo)
        return ctx

    def _count(self, expr, ctx):
        if expr is None:
            return 1
        if expr in ctx:
            return int(ctx[expr])
        try:
            return int(expr)
        except (TypeError, ValueError):
            return 0

    def _directives(self, name, member):
        return _ZC.d.get(name, {}).get(member, {})

    # -- inline cstring: verbatim copy incl. terminator --
    def _emit_cstring(self):
        end = self.pc.index(b'\x00', self.pcur)
        self.out += self.pc[self.pcur:end + 1]
        self.pcur = end + 1

    # -- inline asset (Material / GfxImage): real converter --
    def _emit_inline_asset(self, base):
        fn = _ASSET_CONVERT.get(base)
        if fn is None:
            raise RuntimeError('no inline-asset converter for %s' % base)
        cob, src_end = fn(self.pc, self.pcur, self.reloc)
        self.out += cob
        self.pcur = src_end

    # -- array of struct elements: all bodies first, then per-element dynamics --
    def _emit_array(self, base, count):
        if count <= 0:
            return
        sz = _L.get(base)['size']
        bodies = self.pcur
        for i in range(count):
            self.out += self.emit_fixed(base, bodies + i * sz)
        self.pcur = bodies + count * sz
        for i in range(count):
            self.follow(base, bodies + i * sz)

    # -- follow the dynamic (FOLLOW/INSERT) members of one struct body --
    def follow(self, name, body):
        ctx = self._read_ctx(name, body)
        s = _L.get(name)
        for f in s['fields']:
            if 'error' in f:
                continue
            base = f['base']
            d = self._directives(name, f['name'])
            if not f['is_ptr']:
                # embedded struct: recurse for its own inner dynamics
                if base in _L.structs and f['arr'] == 1:
                    self.follow(base, body + f['offset'])
                continue
            marker = self._u32(body + f['offset'])
            if marker not in (FOLLOW, INSERT):
                continue                     # null or already-written alias
            if f.get('ptr2'):
                self._follow_ptr2(base, d, ctx)
            elif d.get('string'):
                self._emit_cstring()
            elif base in _ASSET_CONVERT:
                self._emit_inline_asset(base)
            elif base in _L.structs:
                self._emit_array(base, self._count(d.get('count'), ctx))
            else:
                # scalar array follow (not expected for these roots)
                cnt = self._count(d.get('count'), ctx)
                prim = _L._resolve(base)[0]
                for i in range(cnt):
                    tmp = bytearray(prim)
                    self._put_scalar(tmp, 0, self.pcur, prim)
                    self.out += tmp
                    self.pcur += prim

    # -- T** double pointer: N slots, then one inline element per FOLLOW slot --
    def _follow_ptr2(self, base, d, ctx):
        count = self._count(d.get('count'), ctx)
        if count <= 0:
            return
        slots = self.pcur
        for i in range(count):
            self.out += struct.pack('>I',
                                    self._ptrval(self._u32(slots + i * 4), base))
        self.pcur = slots + count * 4
        for i in range(count):
            m2 = self._u32(slots + i * 4)
            if m2 not in (FOLLOW, INSERT):
                continue
            if d.get('string'):
                self._emit_cstring()
            elif base in _ASSET_CONVERT:
                self._emit_inline_asset(base)
            elif base in _L.structs:
                self._emit_array(base, 1)
            else:
                prim = _L._resolve(base)[0]
                tmp = bytearray(prim)
                self._put_scalar(tmp, 0, self.pcur, prim)
                self.out += tmp
                self.pcur += prim


def _convert(root, pc, off, reloc):
    e = _Emit(pc, reloc)
    e.out += e.emit_fixed(root, off)
    e.pcur = off + _L.get(root)['size']
    e.follow(root, off)
    _convert.refused_ptrs = list(e.refused_ptrs)     # rule (EX) telemetry
    _REFUSED.extend(e.refused_ptrs)
    return bytes(e.out), e.pcur


_REFUSED = []                    # process-wide tally, read by the bake report


def convert_tracerdef(pc, off, reloc=_identity):
    return _convert('TracerDef', pc, off, reloc)


def convert_weaponcamo(pc, off, reloc=_identity):
    return _convert('WeaponCamo', pc, off, reloc)


if __name__ == '__main__':
    print(__doc__)
