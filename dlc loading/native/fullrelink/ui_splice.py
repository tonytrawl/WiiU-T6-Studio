#!/usr/bin/env python3
"""
Splice PC (transcoded->BE) LUI rawfiles into the genuine WiiU patch_ui_zm zone, replacing
the WiiU-truncated versions with PC's fuller ones (e.g. the 8-map zombie globe). Multiple
rawfile-buffer substitutions grow the zone -> cumulative-delta pointer relink (generalizes
patch_relink.EditEmitter). UI-only zone (rawfile/material/techset).

  python "../dlc loading/native/fullrelink/ui_splice.py" validate     # offline round-trip
  python "../dlc loading/native/fullrelink/ui_splice.py" build -o OUTDIR
"""
import sys, os, struct, argparse
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _p in (os.path.join(_ROOT, 'native_linker'), os.path.join(_ROOT, 'wiiu_ref'),
           os.path.join(_ROOT, 'WiiU_FF_Studio'), os.path.join(_ROOT, 'tools'),
           os.path.join(_ROOT, 'dlc loading', 'native')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(os.path.join(_ROOT, 'native_linker'))

import wiiu_ff, wiiu_zone, struct_layout, ff_decrypt
import walker as W
import zone_stream as zs
import body_relayout as BR
import stage1_roundtrip as S1
import patch_relink as PR
import lua_endian as LE

SRC = 'C:/Users/Tony - Main Rig/AppData/Roaming/Cemu/mlc01/usr/title/0005000e/1010cf00/content/english/'
PC_UI = 'E:/pluto_t6_full_game/zone/all/patch_ui_zm.ff'

# zombie map/globe cluster (WiiU-truncated) to replace with PC versions
SCRIPTS = os.environ.get('UI_SPLICE_SCRIPTS', ','.join([
    'ui_mp/t6/zombie/basezombie.lua',
])).split(',')
FOLLOW = 0xFFFFFFFF


def load():
    stock = SRC+'patch_ui_zm.ff.stockbak'
    ui = stock if os.path.exists(stock) else SRC+'patch_ui_zm.ff'
    _h, wz, _n = wiiu_ff.decrypt(open(ui, 'rb').read())
    raw = open(PC_UI, 'rb').read(); e, k, v, l = ff_decrypt.detect_platform(raw)
    pz = ff_decrypt.decrypt_ff(raw, k, e)[1]
    return wz, pz


def rawfile_spans(z, be):
    """name -> (H header_off, buffer_off, old_len, name_bytes)."""
    e = '>' if be else '<'; out = {}; o = 0
    while True:
        i = z.find(b'\x1bLua', o)
        if i < 0: break
        j = i-1
        if z[j] == 0: j -= 1
        st = j
        while st > 0 and 32 <= z[st-1] < 127: st -= 1
        nm = (z[st:i-1] if z[i-1] == 0 else z[st:i])
        H = st-12; ln = struct.unpack_from(e+'I', z, H+4)[0] if H >= 0 else -1
        if 0 < ln < 2_000_000 and z[i:i+4] == b'\x1bLua':
            out[nm.decode('latin1')] = (H, i, ln, nm); o = i+ln
        else:
            o = i+4
    return out


def build_subs(wz, pz):
    """Return subs {H: (sub_end, new_body)} and info list."""
    Wsp = rawfile_spans(wz, True)
    Psp = rawfile_spans(pz, False)
    subs = {}; info = []
    for nm in SCRIPTS:
        H, boff, oldlen, nmb = Wsp[nm]
        pH, pboff, plen, _ = Psp[nm]
        pc_buf = pz[pboff:pboff+plen]
        be_buf, consumed = LE.transcode(pc_buf, want_le=False)     # PC LE -> BE
        assert consumed == plen, ('%s transcode consumed %d/%d' % (nm, consumed, plen))
        # console RawFile asset = {name*,len,buffer*}(12) + name\0 + buffer(len) + 1 trailing \0
        trail = wz[boff+oldlen:boff+oldlen+1]
        assert trail == b'\x00', ('%s trailing byte %r != null' % (nm, trail))
        new_body = struct.pack('>III', FOLLOW, len(be_buf), FOLLOW) + nmb + b'\x00' + be_buf + b'\x00'
        sub_end = boff + oldlen + 1
        subs[H] = (sub_end, new_body)
        info.append((nm, H, oldlen, len(be_buf), sub_end - H, len(new_body)))
    return subs, info


class MultiEdit(BR.ReEmitter):
    def __init__(self, zone, L, zc, w, subs):
        super().__init__(zone, L, zc, w)
        self.subs = subs
        self.deltas = sorted((off, len(body)-(end-off)) for off, (end, body) in subs.items())
        self.subbed = set()
    def cumdelta(self, file_off):
        d = 0
        for off, dl in self.deltas:
            if off < file_off: d += dl
            else: break
        return d
    def remap_ptr(self, v):
        if v in (zs.FOLLOW, zs.INSERT, 0): return v
        if not (0xA0000000 <= v < 0xC0000000): return v
        blk, off = zs.decode_ptr(v)
        if blk == zs.BLOCK_VIRTUAL and self.delta:
            cd = self.cumdelta(off + BR.B5_BASE)
            if cd: return zs.encode_ptr(blk, off + cd)
        return v
    def emit_asset(self, root, src_file):
        if src_file in self.subs:
            end, body = self.subs[src_file]
            blkname = self.zc.default_block.get(root, 'XFILE_BLOCK_TEMP')
            self.w.push_block(BR.BLOCKMAP.get(blkname, zs.BLOCK_VIRTUAL))
            self.register(src_file)
            self.w.write_bytes(body)
            self.src = end
            self.w.pop_block()
            self.subbed.add(src_file)
            return end
        return super().emit_asset(root, src_file)


def rebuild(wz, subs):
    r = wiiu_zone.ZoneReader(wz); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    total_delta = sum(len(b)-(e-o) for o, (e, b) in subs.items())

    def emit_pass(omap0):
        w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
        w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - PR.B5_BASE
        em = MultiEdit(wz, L, zc, w, subs); em.delta = 1
        if omap0 is not None: em.omap.update(omap0)
        cur = r.assets_end; wb = None
        for (cid, pc_, nm) in r.assets:
            root = W.ASSET_ROOT.get(nm)
            if root is None or root not in L.structs: continue
            try: cur = em.emit_asset(root, cur)
            except Exception as ex: wb = (nm, cur, str(ex)[:70]); break
        return em, w, cur, wb

    em1, *_ = emit_pass(None)
    em, w, cur, wb = emit_pass(em1.omap)
    if len(em.subbed) != len(subs):
        raise RuntimeError('only %d/%d subs fired; walk_break=%r' % (len(em.subbed), len(subs), wb))
    if cur < len(wz): w.write_bytes(wz[cur:])
    c = S1.parse_container(wz)
    c['size'] += total_delta
    c['block_sizes'][zs.BLOCK_VIRTUAL] += total_delta
    edited = bytearray(S1.emit_container(c)[:r.assets_end] + bytes(w.buf))
    # XAssetList headerPtr relink (cumulative)
    deltas = sorted((o, len(b)-(e-o)) for o, (e, b) in subs.items())
    def cumdelta(fo):
        d = 0
        for off, dl in deltas:
            if off < fo: d += dl
            else: break
        return d
    ao = r.assets_off; nhp = 0
    for i in range(r.asset_count):
        ho = ao + i*8 + 4; h = struct.unpack_from('>I', edited, ho)[0]
        if 0xA0000001 <= h <= 0xBFFFFFFF:
            blk, off = zs.decode_ptr(h)
            if blk == zs.BLOCK_VIRTUAL:
                cd = cumdelta(off + PR.B5_BASE)
                if cd:
                    struct.pack_into('>I', edited, ho, zs.encode_ptr(blk, off+cd)); nhp += 1
    return bytes(edited), total_delta, nhp


def cmd_validate():
    wz, pz = load()
    subs, info = build_subs(wz, pz)
    print('subs:')
    for nm, H, ol, nl, os_, ns in info:
        print('  %-48s old_buf=%-6d new_buf=%-6d (asset %d->%d)' % (nm, ol, nl, os_, ns))
    edited, td, nhp = rebuild(wz, subs)
    print('rebuilt zone %d -> %d (+%d); relinked %d XAssetList ptrs' % (len(wz), len(edited), td, nhp))
    # GOLD: re-extract each spliced rawfile, transcode BE->LE, must equal PC source
    Esp = rawfile_spans(edited, True); Psp = rawfile_spans(pz, False)
    ok = 0
    for nm in SCRIPTS:
        H, boff, ln, _ = Esp[nm]
        be = edited[boff:boff+ln]
        le, _ = LE.transcode(be, want_le=True)
        pH, pboff, plen, _ = Psp[nm]
        if le == pz[pboff:pboff+plen]:
            ok += 1
        else:
            print('  MISMATCH re-extract %s' % nm)
    print('spliced-rawfile round-trip vs PC: %d/%d OK' % (ok, len(SCRIPTS)))
    # sanity: zone re-walks + asset count preserved
    r2 = wiiu_zone.ZoneReader(edited); r2.read_string_table(); r2.read_asset_list()
    print('edited zone re-walk: %d assets (orig %d)' % (r2.asset_count,
          wiiu_zone.ZoneReader(wz).read_string_table() or 0))


def cmd_build(out_dir):
    wz, pz = load()
    subs, info = build_subs(wz, pz)
    edited, td, nhp = rebuild(wz, subs)
    ff = wiiu_ff.pack(edited, 'patch_ui_zm')
    outp = os.path.join(out_dir if os.path.isabs(out_dir) else os.path.join(_ROOT, out_dir), 'patch_ui_zm.ff')
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    open(outp, 'wb').write(ff)
    _h, z2, _n = wiiu_ff.decrypt(open(outp, 'rb').read())
    print('packed %d B ff (+%d zone, repack-decrypt matches: %s) -> %s'
          % (len(ff), td, z2 == edited, outp))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['validate', 'build'])
    ap.add_argument('-o', '--out-dir', default='../dlc loading/native/fullrelink/_ui_splice')
    a = ap.parse_args()
    if a.cmd == 'validate': cmd_validate()
    else: cmd_build(a.out_dir)
