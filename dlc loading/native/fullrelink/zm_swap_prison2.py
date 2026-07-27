#!/usr/bin/env python3
"""
COUNT-PRESERVING SWAP (isolated side project): replace the zm_transit_dr row with a
zm_prison (Mob of the Dead) row. Keeps 4 zm maps / indices 0-3, so it stays under the
zombie-UI count limit AND prison is already in the UI's LUI map table -> should both
LIST and DISPLAY. col5(map index) kept at transit_dr's slot (3); col11(pack) forced 0
(un-gated) so no DLC ownership gate.
"""
import sys, os, struct
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _p in (os.path.join(_ROOT, 'native_linker'), os.path.join(_ROOT, 'wiiu_ref'),
           os.path.join(_ROOT, 'WiiU_FF_Studio'), os.path.join(_ROOT, 'tools'),
           os.path.join(_ROOT, 'dlc loading', 'native')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(os.path.join(_ROOT, 'native_linker'))
import wiiu_ff, wiiu_zone, struct_layout
import walker as W
import zone_stream as zs
import stage1_roundtrip as S1
import patch_relink as PR

CON = os.path.join(_ROOT, 'dlc loading', 'native', 'upd_patch_zm.ff')
PC  = os.path.join(_ROOT, 'dlc loading', 'native', 'pc_authentic', 'pc_patch_zm.ff')
OUT = os.path.join(_ROOT, 'dlc loading', 'native', 'fullrelink', '_zm_swap_prison2')
SWAP_OUT = 'zm_transit_dr'
SWAP_IN  = 'zm_prison'          # DLC map to put in that slot


def main():
    zone = PR.load_zone(CON); r = PR.inventory(zone)
    needle = b'zm/mapstable.csv'
    chdr, ccols, crows = PR._st_header(zone, needle, le=False)
    ccols, crows, con_table = PR.read_table(zone, chdr, le=False)
    name_end = zone.index(b'\x00', chdr + 20); cells0 = name_end + 1
    n = ccols * crows; o = cells0 + n * 8
    for k in range(n):
        p, _h = struct.unpack_from('>2I', zone, cells0 + k*8)
        if p == 0xFFFFFFFF:
            o = zone.index(b'\x00', o) + 1
    con_body_end = o + n * 2

    pc = PR._decrypt_pc(PC)
    phdr, pcols, prows = PR._st_header(pc, needle, le=True)
    _pc, _pr, pc_table = PR.read_table(pc, phdr, le=True)
    prow = next(rr for rr in pc_table if rr and rr[0] == SWAP_IN)

    merged = [list(rr) for rr in con_table]
    done = False
    for i, rr in enumerate(merged):
        if rr and rr[0] == SWAP_OUT:
            newrow = list(rr)                              # base = transit_dr's row
            newrow[0] = SWAP_IN                            # internal name -> zm_prison
            newrow[3] = 'ZMUI_PRISON'                      # display name (localize INSERTED)
            newrow[12] = 'ZMUI_GUARDS_SHORT'               # faction short names (INSERTED)
            newrow[13] = 'ZMUI_INMATES_SHORT'
            newrow[16] = '122'                             # globe pin coords (PC prison)
            newrow[17] = '35'
            # keep borrowed: col4 signpost, col6 desc, col7 compass, col14/15 faction mats
            merged[i] = newrow
            print('swapped slot %d: %s -> %s (map index kept=%s, pack=0)'
                  % (i, SWAP_OUT, SWAP_IN, newrow[5] if 5<len(newrow) else ''))
            print('   new row:', newrow)
            done = True
            break
    assert done, 'SWAP_OUT row not found'

    new_body = PR.emit_stringtable(merged, ccols, 'zm/mapstable.csv')
    delta = len(new_body) - (con_body_end - chdr)
    print('   mapstable %d B -> %d B (delta +%d); maps still 4' % (con_body_end - chdr, len(new_body), delta))

    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    def emit_pass(omap0):
        w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
        w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - PR.B5_BASE
        em = PR.EditEmitter(zone, L, zc, w, chdr, con_body_end, new_body)
        em.delta = delta; em.shift_from = con_body_end - PR.B5_BASE
        if omap0 is not None: em.omap.update(omap0)
        em.subbed = False; cur = r.assets_end; wb = None
        for (cid, pc_, nm) in r.assets:
            root = W.ASSET_ROOT.get(nm)
            if root is None or root not in L.structs: continue
            try: cur = em.emit_asset(root, cur)
            except Exception as ex: wb = (nm, cur, str(ex)[:60]); break
        return em, w, cur, wb
    em1, *_ = emit_pass(None)
    em, w, cur, wb = emit_pass(em1.omap)
    assert em.subbed, ('sub never fired %r' % (wb,))
    if cur < len(zone): w.write_bytes(zone[cur:])
    c = S1.parse_container(zone); c['size'] += delta; c['block_sizes'][zs.BLOCK_VIRTUAL] += delta
    edited = bytearray(S1.emit_container(c)[:r.assets_end] + bytes(w.buf))
    shift_from = con_body_end - PR.B5_BASE; ao = r.assets_off; nhp = 0
    for i in range(r.asset_count):
        ho = ao + i*8 + 4; h = struct.unpack_from('>I', edited, ho)[0]
        if 0xA0000001 <= h <= 0xBFFFFFFF:
            blk, off = zs.decode_ptr(h)
            if blk == zs.BLOCK_VIRTUAL and off >= shift_from:
                struct.pack_into('>I', edited, ho, zs.encode_ptr(blk, off + delta)); nhp += 1
    edited = bytes(edited)
    e2 = PR._st_header(edited, needle, le=False); cc, rr2, t2 = PR.read_table(edited, e2[0], le=False)
    print('   re-read edited maps:', [x[0] for x in t2 if x and x[0].startswith('zm_')])
    ff = wiiu_ff.pack(edited, PR.orig_ff_name(CON))
    os.makedirs(OUT, exist_ok=True); outp = os.path.join(OUT, 'patch_zm.ff')
    open(outp, 'wb').write(ff)
    print('   packed %d B -> %s' % (len(ff), outp))


if __name__ == '__main__':
    main()
