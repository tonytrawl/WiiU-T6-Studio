#!/usr/bin/env python3
"""
PC map-zone walk / dispatcher (HANDOFF Track E).

Walks a PC (v147, LE) MAP zone end-to-end, dispatching each asset to the consumer that knows its
exact body span, and resyncing on the next asset's name pointer. This is the PC-side analogue of
the console `body_relayout` dispatcher; it is what Track G needs to traverse a no-backbone PC map.

Target = a MAP zone (mp_raid), NOT common_mp: common_mp is the shared backbone that ships on the
console and is never converted; a no-backbone map aliases it. common_mp is also dominated by
menu/weapon/anim assets that lack probes (the console dispatcher itself only clears ~120 there),
so it is the wrong validation target.

Dispatch table:
  * FxEffectDef  -> fx_pc.parse_fx_pc        (NEW this session — the drift that blocked the walk)
  * everything else -> the generic struct walker (walker.py, PC mode)
Aliased assets (asset-list header ptr != FOLLOW) consume 0 bytes.

Known remaining drift: an FX whose element visuals are INLINE materials that themselves reference
INLINE images — the material span (material_convert) does not yet consume inline image pixel data
(a Track A/C extension). Everything up to that point walks clean.
"""
import struct, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import struct_layout, walker as W, pc_zone, fx_pc, xmodel_pc, techset_pc, lightdef_pc, gfxworld_pc, glasses_pc, clipmap_pc, material_convert, sndbank_pc
import destructibledef_probe as _DP
import gameworldmp_probe as _GW
import xanimparts_probe as _XA

FOLLOW = 0xFFFFFFFF


def _okptr(v):
    """A plausible next-asset name pointer: FOLLOW, null, or a block alias."""
    return v in (FOLLOW, 0xFFFFFFFE, 0) or 0xA0000000 <= v < 0xF0000000


def walk_pc_zone(path, verbose=False, spans=None):
    """Walk the zone; if `spans` is a list, append (index, type, start, end) per inline asset."""
    PC = open(path, 'rb').read()
    r = pc_zone.PCZoneReader(PC); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=False)
    zc = W.ZoneCode(W.ZC_DIR)
    wk = W.Walker(PC, L, zc, r.block_sizes)
    wk.u16 = lambda o: struct.unpack_from('<H', PC, o)[0]
    wk.u32 = lambda o: struct.unpack_from('<I', PC, o)[0]
    wk._scalar = lambda base, o: (lambda s: PC[o] if s == 1 else
                                  struct.unpack_from('<H' if s == 2 else '<I', PC, o)[0])(L._resolve(base)[0])
    # span consumers for inline (FOLLOW/INSERT) asset refs inside other assets
    # (e.g. TracerDef.material -> inline Material, WeaponDef.gunXModel slots)
    wk.asset_span = {
        'Material':             lambda c: material_convert.convert_material(PC, c)[1],
        'GfxImage':             lambda c: material_convert.pc_image_span(PC, c),
        'XModel':               lambda c: xmodel_pc.parse_xmodel_pc(PC, c),
        'FxEffectDef':          lambda c: fx_pc.parse_fx_pc(PC, c)[0],
        'XAnimParts':           lambda c: _XA.parse_xanim(PC, c, '<')[0],
        'MaterialTechniqueSet': lambda c: techset_pc.parse_techset_pc(PC, c),
        'GfxLightDef':          lambda c: lightdef_pc.parse_lightdef_pc(PC, c),
        'SndBank':              lambda c: sndbank_pc.parse_sndbank_pc(PC, c),
        'DestructibleDef':      lambda c: _DP.parse_destructible(PC, c, '<')[0],
    }
    cur = r.assets_end
    clean = 0
    drift = None
    for i, (t, nm, hp) in enumerate(r.assets):
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            continue
        if hp != FOLLOW:                       # aliased asset: no inline body
            continue
        start = cur
        try:
            if root == 'FxEffectDef':
                cur, _ = fx_pc.parse_fx_pc(PC, start)
            elif root == 'XModel':
                cur = xmodel_pc.parse_xmodel_pc(PC, start)
            elif root == 'DestructibleDef':
                cur = _DP.parse_destructible(PC, start, '<')[0]
            elif root == 'MaterialTechniqueSet':
                cur = techset_pc.parse_techset_pc(PC, start)
            elif root == 'GfxLightDef':
                cur = lightdef_pc.parse_lightdef_pc(PC, start)
            elif root == 'GfxWorld':
                cur = gfxworld_pc.parse_gfxworld_pc(PC, start)
            elif root == 'GameWorldMp':
                cur = _GW.Walker(PC, '<', 144).walk(start)[0]
            elif root == 'Glasses':
                cur = glasses_pc.parse_glasses_pc(PC, start)
            elif root == 'clipMap_t':
                cur = clipmap_pc.parse_clipmap_pc(PC, start)
            elif root == 'Material':
                _, cur = material_convert.convert_material(PC, start)
            elif root == 'SndBank':
                cur = sndbank_pc.parse_sndbank_pc(PC, start)
            elif root == 'XAnimParts':
                cur = _XA.parse_xanim(PC, start, '<')[0]
            elif root == 'GfxImage':
                cur = material_convert.pc_image_span(PC, start)
            else:
                cur = wk.walk(root, cur)
        except Exception as e:
            drift = (i, nm, start, 'EXC: %s' % e); break
        if spans is not None:
            spans.append((i, nm, start, cur))
        nxt = struct.unpack_from('<I', PC, cur)[0] if cur + 4 <= len(PC) else 0
        if _okptr(nxt):
            clean += 1
        else:
            drift = (i, nm, start, 'next name ptr implausible 0x%08x @0x%x' % (nxt, cur)); break
    return len(r.assets), clean, drift


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join('..', 'PC ff', 'mp_raid.zone')
    total, clean, drift = walk_pc_zone(path)
    print("PC walk of %s" % os.path.basename(path))
    print("  cleanly consumed & resynced: %d assets" % clean)
    if drift:
        i, nm, start, why = drift
        print("  first drift: asset [%d] %s @0x%x — %s" % (i, nm, start, why))
    else:
        print("  walked to end (%d assets)" % total)


if __name__ == '__main__':
    main()
