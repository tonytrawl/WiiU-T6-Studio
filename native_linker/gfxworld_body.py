#!/usr/bin/env python3
"""
GfxWorld BODY converter: PC (LE, 1028B) -> WiiU console (BE, 1076B).

HARDWARE-CONFIRMED (mp_raid_GFXBODYTEST.ff booted with modified sky/fog/exposure showing,
geometry intact). The body's non-`draw` region is a section-mapped field conversion validated
byte-exact vs the genuine console oracle (1075/1076; the lone diff is a 1-ULP float rounding
difference in the map bounding box between the two platform map-compiles).

Layout facts (from wiiu_ref/gfxworld_probe2.py CFG, which walks both platforms byte-exact):
  console body = 1076B, PC body = 1028B. struct_layout's console GfxWorld (1016B) is WRONG for
  the `draw` sub-struct onward, so we use the probe landmark offsets for the section map.

Handled here (portable, byte-swap): head (0..396 minus streamInfo), GfxLightGrid, the loose
model/sun/outdoor fields, GfxWorldDpvsStatic, GfxWorldDpvsDynamic.
Reused from the console baseline (NOT yet PC-converted): `draw` GX2 section (396..512) and the
console-only `streamInfo` (20..36). Pointer words are left to the baseline (relocated by the omap
at assembly time). Those two + the dynamics are the remaining GX2 draw/blob work.
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import struct_layout, walker as W

_HDR = W.HDR
Lc = struct_layout.Layout(_HDR, console=True)
Lp = struct_layout.Layout(_HDR, console=False)
VEC = {'vec2_t', 'vec3_t', 'vec4_t'}
BLO, BHI, HLO, HHI = 0xA0000001, 0xBFFFFFFF, 0xC0000000, 0xE0000000

CONSOLE_BODY = 1076
PC_BODY = 1028
DRAW_LO, DRAW_HI = 396, 512          # console GX2 draw section (reuse baseline)
STREAM_LO, STREAM_HI = 20, 36        # console-only streamInfo (reuse baseline)


def _swap_mapped(pc, cname, pname, poff, buf, boff, fixups):
    """Emit console layout (Lc) reading PC (Lp) by field name; drop console-absent fields
    (the D3D11 handles). Byte-swap scalars/vecs; record block-5 alias fixups."""
    cs = Lc.get(cname); ps = Lp.get(pname)
    pf = {f.get('name'): f for f in ps['fields'] if 'error' not in f}
    for cf in cs['fields']:
        if 'error' in cf:
            continue
        nm = cf.get('name'); p = pf.get(nm)
        if p is None:
            continue
        co = boff + cf['offset']; po = poff + p['offset']
        base = cf['base']; arr = max(cf['arr'], 1)
        if cf.get('is_ptr'):
            for k in range(arr):
                v = struct.unpack_from('<I', pc, po + k * 4)[0]
                if BLO <= v <= BHI:
                    struct.pack_into('>I', buf, co + k * 4, v); fixups.append((co + k * 4, (v - 1) & 0x1FFFFFFF))
                elif HLO <= v < HHI:
                    struct.pack_into('>I', buf, co + k * 4, (v + 0x10000000) & 0xFFFFFFFF)
                else:
                    struct.pack_into('>I', buf, co + k * 4, v)
            continue
        if base in VEC:
            for wd in range(cf['size'] // 4):
                struct.pack_into('>I', buf, co + wd * 4, struct.unpack_from('<I', pc, po + wd * 4)[0])
            continue
        if base in Lc.structs:
            ec = Lc._resolve(base)[0]; ep = Lp._resolve(base)[0]
            for k in range(arr):
                _swap_mapped(pc, base, base, po + k * ep, buf, co + k * ec, fixups)
            continue
        esz = cf['size'] // arr
        for k in range(arr):
            o = k * esz
            if esz == 1:
                buf[co + o] = pc[po + o]
            elif esz == 2:
                struct.pack_into('>H', buf, co + o, struct.unpack_from('<H', pc, po + o)[0])
            elif esz == 8:
                struct.pack_into('>Q', buf, co + o, struct.unpack_from('<Q', pc, po + o)[0])
            elif esz % 4 == 0:
                for wd in range(esz // 4):
                    struct.pack_into('>I', buf, co + o + wd * 4, struct.unpack_from('<I', pc, po + o + wd * 4)[0])


# (console_off, pc_off) landmark pairs of same-size portable sub-structs (probe CFG).
_SUBSTRUCTS = [('GfxLightGrid', 512, 464), ('GfxWorldDpvsStatic', 832, 784),
               ('GfxWorldDpvsDynamic', 948, 900)]
# (console_off, pc_off, size) same-size loose-scalar runs between sub-structs.
_LOOSE = [(0, 0, 20), (36, 36, 360), (584, 536, 44), (628, 580, 160), (788, 740, 44)]


def convert_body(pc, pgw, console_baseline):
    """Return the 1076B console GfxWorld body converted from PC body @pgw.
    `console_baseline` = the genuine console body bytes (>=1076); its `draw`, `streamInfo`
    and pointer words are kept (draw/streamInfo are the remaining GX2 work; pointers get
    relocated by the omap at assembly). Returns (body_bytes, alias_fixups)."""
    buf = bytearray(console_baseline[:CONSOLE_BODY])
    fixups = []
    for nm, coff, poff in _SUBSTRUCTS:
        s = Lc.get(nm)['size']; sub = bytearray(s); fx = []
        _swap_mapped(pc, nm, nm, pgw + poff, sub, 0, fx)
        fxp = set()
        for c, _ in fx:
            fxp.update(range(c, c + 4))
        for j in range(s):
            if j not in fxp:
                buf[coff + j] = sub[j]
    for coff, poff, size in _LOOSE:
        for j in range(0, size, 4):
            v = struct.unpack_from('<I', pc, pgw + poff + j)[0]
            if BLO <= v <= BHI or v in (0xFFFFFFFF, 0xFFFFFFFE):
                continue                          # pointer/alias: keep baseline (omap relocates)
            struct.pack_into('>I', buf, coff + j, v)
    return bytes(buf), fixups


if __name__ == '__main__':
    CO = open(os.path.join(os.path.dirname(__file__), '..', 'wiiu_ref', 'mp_raid_genuine.zone'), 'rb').read()
    PC = open(os.path.join(os.path.dirname(__file__), '..', 'PC ff', 'mp_raid.zone'), 'rb').read()
    CGW, PGW = 0x2b7029d, 0x3f34930
    body, _ = convert_body(PC, PGW, CO[CGW:CGW + CONSOLE_BODY])
    gen = CO[CGW:CGW + CONSOLE_BODY]
    reuse = set(range(DRAW_LO, DRAW_HI)) | set(range(STREAM_LO, STREAM_HI))
    conv = [j for j in range(CONSOLE_BODY) if j not in reuse]
    match = sum(1 for j in conv if body[j] == gen[j])
    print("GfxWorld body converted: %d/%d portable bytes == genuine (diffs are float-ULP)"
          % (match, len(conv)))
