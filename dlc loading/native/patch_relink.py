#!/usr/bin/env python3
"""
patch_relink.py -- console->console relink of genuine Wii U patch_mp / patch_zm with
an extended mapsTable (add mp_skate row). NEW file; uses native_linker/wiiu_ref
machinery READ-ONLY (import, never edit).

Stage 1 (this file, `recon`): unlink each patch zone, walk to EOF with the console
walker (ReEmitter round-trip), locate the mapsTable StringTable asset(s), record the
asset inventory, and PRECISELY characterize any walk desync (for routing to the main
session -- do NOT hack shared walkers here).

Stage 2 (`roundtrip`): re-emit UNMODIFIED, require byte-identical, pack, deploy control.
Stage 3 (`edit`): add the mp_skate row, re-encode via loader_sim, re-walk, pack.

Usage:
  python patch_relink.py recon patch_mp.ff
  python patch_relink.py recon patch_zm.ff
"""
import sys, os, struct, argparse
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # .../Testing enviroment
for _p in (os.path.join(_ROOT, 'native_linker'), os.path.join(_ROOT, 'wiiu_ref'),
           os.path.join(_ROOT, 'WiiU_FF_Studio')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# struct_layout reads ../tools/ref_oat/... relative to CWD -> run from native_linker.
# Preserve the invocation CWD so zone paths on the cmdline still resolve.
_ORIG_CWD = os.getcwd()
os.chdir(os.path.join(_ROOT, 'native_linker'))

import wiiu_ff                     # decrypt/pack (read-only)
import wiiu_zone                   # console zone reader (read-only)
import struct_layout               # asset struct layouts (read-only)
import walker as W                 # ASSET_ROOT, Walker, ZoneCode (read-only)
import zone_stream as zs           # ZoneWriter (read-only)
import body_relayout as BR         # ReEmitter round-trip (read-only)

B5_BASE = 64


def load_zone(path):
    if not os.path.isabs(path):
        path = os.path.join(_ORIG_CWD, path)
    data = open(path, 'rb').read()
    _hdr, zone, _n = wiiu_ff.decrypt(data)
    return zone


def orig_ff_name(path):
    """The fastfile's INTERNAL name from its header. This seeds the Salsa20 keystream
    (wiiu_ff.HashChain), so a repack MUST reuse it -- packing under the local filename
    (e.g. 'upd_patch_mp') encrypts with the wrong seed and the console, which decrypts
    patch_mp.ff using the name 'patch_mp', gets garbage -> boot crash."""
    if not os.path.isabs(path):
        path = os.path.join(_ORIG_CWD, path)
    return wiiu_ff.parse_header(open(path, 'rb').read())['name']


def inventory(zone):
    r = wiiu_zone.ZoneReader(zone)
    r.read_string_table(); r.read_asset_list()
    return r


def find_stringtables(zone, r):
    """Locate every StringTable asset body by its inline name; return list of
    (name, file_off). StringTable body: {name* FOLLOW, cols, rows, values*,
    cellIndex*} then inline name -- so a '<name>.csv\\0' preceded 20B by the
    header start is a table."""
    out = []
    off = 0
    while True:
        i = zone.find(b'.csv\x00', off)
        if i < 0:
            break
        # walk back to string start
        s = i
        while s > 0 and 32 <= zone[s-1] < 127:
            s -= 1
        name = zone[s:i+4]
        hdr = s - 20
        if hdr >= 0:
            np, cols, rows, vals, cidx = struct.unpack_from('>IIIII', zone, hdr)
            if np == 0xFFFFFFFF and 0 < cols < 64 and 0 < rows < 4096:
                out.append((name.decode('latin1'), hdr, cols, rows))
        off = i + 5
    return out


import stage1_roundtrip as S1        # container parse/emit (read-only)


def emit_bodies(zone, r):
    """Re-emit every body via the ReEmitter. Returns (writer, cur_end). The per-asset
    RETURN cursor is unreliable for some console asset types (StringTable/techset),
    but the ReEmitter's internal block-5 accounting is correct -- the emitted buffer
    is byte-identical to the original body region (verified). So we ignore the return
    value for desync detection and rely on whole-buffer identity."""
    L = struct_layout.Layout(W.HDR, console=True)
    zc = W.ZoneCode(W.ZC_DIR)
    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - B5_BASE
    em = BR.ReEmitter(zone, L, zc, w)
    cur = r.assets_end
    for i, (cid, pc, nm) in enumerate(r.assets):
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            continue
        try:
            cur = em.emit_asset(root, cur)
        except Exception:
            pass                       # unreliable trailing cursor; buffer stays valid
    if cur < len(zone):
        w.write_bytes(zone[cur:])
    return w, cur


def roundtrip_zone(zone, r):
    """No-edit byte-exact round-trip. Container sliced at assets_end (NOT container_end
    -- parse_container rounds up; bodies start at assets_end even when unaligned, e.g.
    patch_mp assets_end=14973 vs container_end=14976). Returns (full_bytes, identical)."""
    c = S1.parse_container(zone)
    w, _cur = emit_bodies(zone, r)
    full = S1.emit_container(c)[:r.assets_end] + bytes(w.buf)
    return full, (full == zone)


def cmd_recon(path):
    zone = load_zone(path)
    r = inventory(zone)
    print('== %s : zone %d B, %d assets, assets_end=%d' %
          (os.path.basename(path), len(zone), r.asset_count, r.assets_end))
    types = Counter(a[2] for a in r.assets)
    print('   inventory:', dict(types.most_common(30)))

    sts = find_stringtables(zone, r)
    print('   StringTables (%d):' % len(sts))
    for nm, hdr, cols, rows in sts:
        star = '  <-- MAPSTABLE' if 'mapstable' in nm.lower() or 'mapstable' in nm.lower() else ''
        print('     %-32s @%-8d %dx%d%s' % (nm, hdr, cols, rows, star))

    full, identical = roundtrip_zone(zone, r)
    if identical:
        print('   STAGE 2 GATE: byte-identical no-edit round-trip PASS (%d B)' % len(full))
    else:
        d = next((j for j in range(min(len(full), len(zone))) if full[j] != zone[j]), None)
        print('   STAGE 2 GATE FAIL: %d vs %d, first diff @%s' % (len(full), len(zone), d))


FOLLOW = 0xFFFFFFFF


def djb2ci(s):
    h = 5381
    for c in s.encode('latin1', 'replace').lower():
        h = ((h * 33) + c) & 0xFFFFFFFF
    return h


def read_table(zone, hdr, le):
    """Read a StringTable's rows resolving BOTH inline (FOLLOW) and ALIAS cells via
    the cell's djb2 hash against a zone-wide hash->string map (MT.dump_stringtable
    only handles inline cells -> alias-heavy tables like zm come back as '<a:...>')."""
    import re
    end = ord('\x00')
    hmap = {}
    for m in re.finditer(rb'[\x20-\x7e]{1,96}\x00', zone):
        s = m.group(0)[:-1]
        hmap.setdefault(djb2ci(s.decode('latin1')), s)
    ifmt = '<5I' if le else '>5I'
    cfmt = '<2I' if le else '>2I'
    _np, cols, rows, _v, _c = struct.unpack_from(ifmt, zone, hdr)
    name_end = zone.index(b'\x00', hdr + 20); cells0 = name_end + 1
    n = cols * rows
    o = cells0 + n * 8; inline = {}
    for k in range(n):
        p, h = struct.unpack_from(cfmt, zone, cells0 + k * 8)
        if p == 0xFFFFFFFF:
            se = zone.index(b'\x00', o); inline[k] = zone[o:se]; o = se + 1
    def cell(k):
        p, h = struct.unpack_from(cfmt, zone, cells0 + k * 8)
        if p == 0:
            return ''
        if p == 0xFFFFFFFF:
            return inline[k].decode('latin1')
        v = hmap.get(h)
        return v.decode('latin1') if v else ''
    return cols, rows, [[cell(r * cols + c) for c in range(cols)] for r in range(rows)]


def emit_stringtable(rows, cols, asset_name):
    """Console (BE) self-contained StringTable: all cells FOLLOW-inline, cellIndex =
    cells sorted by SIGNED hash (matches genuine; binary-search name lookup works)."""
    nrows = len(rows)
    n = nrows * cols
    flat = [(rows[rr][cc] if cc < len(rows[rr]) else '') for rr in range(nrows) for cc in range(cols)]
    hashes = [djb2ci(s) for s in flat]
    body = bytearray()
    body += struct.pack('>IIIII', FOLLOW, cols, nrows, FOLLOW, FOLLOW)
    body += asset_name.encode('latin1') + b'\x00'
    for k in range(n):
        body += struct.pack('>II', FOLLOW, hashes[k])
    for k in range(n):
        body += flat[k].encode('latin1', 'replace') + b'\x00'
    sgn = lambda h: h - 0x100000000 if h >= 0x80000000 else h
    for idx in sorted(range(n), key=lambda k: sgn(hashes[k])):
        body += struct.pack('>h', idx)
    return bytes(body)


# console columns that DIVERGE from PC (must take the reference console value, not PC).
# zm: console cols 0..18 == PC cols 0..18 (PC's extra col19 = compass pos, dropped) -> none.
# mp: c01/c02 player-group SYMBOL (PC has numbers), c09/c10 ICOPTER_COMLINK/_DESTROYED_HELICOPTER
#     (PC has NO/YES). Everything else (incl. c05 map-index and c11 DLC-pack-index) copies from PC.
DIVERGENT_COLS = {'mp': [1, 2, 9, 10], 'zm': []}


def build_console_maprows(console_table, pc_table, cols, prefix='mp_', tag='mp'):
    """Keep console meta rows + existing console map rows; APPEND a console-format row
    for every PC map not already present. Each new row COPIES all PC columns 0..cols-1
    (so c05 map-index and c11 DLC-pack-index are correct), then overrides the
    schema-divergent columns (DIVERGENT_COLS[tag]) with a reference console row's value.
    maxnum_map updated. Trailing console 'default' row(s) kept last."""
    ismap = lambda r: bool(r) and r[0].startswith(prefix)
    ref = next(r for r in console_table if ismap(r))
    div = DIVERGENT_COLS.get(tag, [])
    present = {r[0] for r in console_table if ismap(r)}
    first_map = next(i for i, r in enumerate(console_table) if ismap(r))
    last_map = max(i for i, r in enumerate(console_table) if ismap(r))
    fit = lambda r: (list(r[:cols]) + [''] * (cols - len(r)))
    head = [fit(r) for r in console_table[:first_map]]
    existing = [fit(r) for r in console_table[first_map:last_map+1]]
    tail = [fit(r) for r in console_table[last_map+1:]]
    new_rows = []
    for pr in pc_table:
        if not ismap(pr) or pr[0] in present:
            continue
        row = [pr[c] if c < len(pr) else '' for c in range(cols)]   # copy PC cols
        for c in div:
            row[c] = ref[c] if c < len(ref) else ''                 # console constant
        new_rows.append(row)
    allmaps = existing + new_rows
    for r in head:
        if r and r[0] == 'maxnum_map':
            r[1] = str(len(allmaps))
    return head + allmaps + tail, new_rows


class EditEmitter(BR.ReEmitter):
    """ReEmitter that substitutes ONE asset's body (by source file offset) with a
    supplied blob. The mapstable is a leaf (no incoming aliases) so the base omap
    machinery relinks all downstream back-aliases across the size delta."""
    def __init__(self, zone, layout, zc, writer, sub_off, sub_end, sub_body):
        super().__init__(zone, layout, zc, writer)
        self.sub_off, self.sub_end, self.sub_body = sub_off, sub_end, sub_body

    def emit_asset(self, root, src_file):
        if src_file == self.sub_off:
            # mirror ReEmitter.emit_asset block handling + register(src) for omap,
            # but write OUR body and skip the original source span
            blkname = self.zc.default_block.get(root, 'XFILE_BLOCK_TEMP')
            self.w.push_block(BR.BLOCKMAP.get(blkname, zs.BLOCK_VIRTUAL))
            self.register(src_file)
            self.w.write_bytes(self.sub_body)
            self.src = self.sub_end
            self.w.pop_block()
            self.subbed = True
            return self.sub_end                      # skip original body in source
        return super().emit_asset(root, src_file)


def cmd_roundtrip(path, out_dir):
    """Stage 2 control: re-emit UNMODIFIED byte-identical, pack, write .ff. Booting
    this on Cemu isolates pack/deploy faults from content faults (the dlc0 lesson)."""
    zone = load_zone(path)
    r = inventory(zone)
    full, identical = roundtrip_zone(zone, r)
    name = os.path.basename(path)
    if not identical:
        d = next((j for j in range(min(len(full), len(zone))) if full[j] != zone[j]), None)
        print('%-14s GATE FAIL first diff @%s -- not packing' % (name, d))
        return
    ff = wiiu_ff.pack(full, orig_ff_name(path))
    outp = os.path.join(out_dir if os.path.isabs(out_dir) else
                        os.path.join(_ORIG_CWD, out_dir), name)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    open(outp, 'wb').write(ff)
    # sanity: re-decrypt our packed ff -> must equal the walked zone
    _h, z2, _n = wiiu_ff.decrypt(open(outp, 'rb').read())
    print('%-14s GATE PASS byte-identical; packed %d B ff (repack-decrypt matches: %s) -> %s'
          % (name, len(ff), z2 == full, outp))


def _decrypt_pc(path):
    import ff_decrypt
    raw = open(path if os.path.isabs(path) else os.path.join(_ORIG_CWD, path), 'rb').read()
    e, k, v, l = ff_decrypt.detect_platform(raw)
    return ff_decrypt.decrypt_ff(raw, k, e)[1]


def _st_header(zone, needle, le):
    off = 0
    while True:
        i = zone.find(needle, off)
        if i < 0:
            return None
        hdr = i - 20
        if hdr >= 0:
            fmt = '<5I' if le else '>5I'
            np, cols, rows, vals, cidx = struct.unpack_from(fmt, zone, hdr)
            if np == 0xFFFFFFFF and 0 < cols < 64 and 0 < rows < 4096 and vals == 0xFFFFFFFF:
                return hdr, cols, rows
        off = i + 1


def cmd_edit(con_path, pc_path, out_dir, tag='mp'):
    import mapstable_tool as MT
    zone = load_zone(con_path)
    r = inventory(zone)
    needle = ('%s/mapstable.csv' % tag).encode()
    chdr, ccols, crows = _st_header(zone, needle, le=False)
    ccols, crows, con_table = read_table(zone, chdr, le=False)
    # span of console mapstable body (header..cellIndex end) to skip in source
    name_end = zone.index(b'\x00', chdr + 20); cells0 = name_end + 1
    n = ccols * crows; o = cells0 + n * 8
    for k in range(n):
        p, _h = struct.unpack_from('>2I', zone, cells0 + k*8)
        if p == 0xFFFFFFFF:
            o = zone.index(b'\x00', o) + 1
    con_body_end = o + n * 2

    pc = _decrypt_pc(pc_path)
    phdr, pcols, prows = _st_header(pc, needle, le=True)
    _pcols, _prows, pc_table = read_table(pc, phdr, le=True)

    prefix = tag + '_'
    merged, new_rows = build_console_maprows(con_table, pc_table, ccols, prefix, tag)
    print('%s: console had %d maps, adding %d PC maps -> %d rows total' %
          (tag, sum(1 for x in con_table if x and x[0].startswith(prefix)),
           len(new_rows), len(merged)))
    print('   new maps:', ', '.join(row[0] for row in new_rows))
    new_body = emit_stringtable(merged, ccols, '%s/mapstable.csv' % tag)
    print('   mapstable %d B -> %d B (delta +%d)' %
          (con_body_end - chdr, len(new_body), len(new_body) - (con_body_end - chdr)))

    # re-emit with substitution. TWO PASSES: pass 1 walks the whole zone to build a COMPLETE
    # source->writer offset map (omap); pass 2 re-emits with that full map so remap_ptr resolves
    # EVERY alias -- backward AND forward (e.g. the weapon's refs into later attachment/xmodel
    # assets, and every delimiter-verbatim asset's cross-refs). remap_ptr only rewrites words
    # whose decoded offset is a genuine registered target, so shader/pixel/audio data that merely
    # resembles an alias is left untouched (no false-positive corruption).
    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    delta = len(new_body) - (con_body_end - chdr)

    def emit_pass(omap0):
        w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
        w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - B5_BASE
        em = EditEmitter(zone, L, zc, w, chdr, con_body_end, new_body)
        em.delta = delta
        em.shift_from = con_body_end - B5_BASE   # block-5 offsets >= old mapstable end shift +delta
        if omap0 is not None:
            em.omap.update(omap0)
        em.subbed = False
        cur = r.assets_end
        wb = None
        for i, (cid, pc_, nm) in enumerate(r.assets):
            root = W.ASSET_ROOT.get(nm)
            if root is None or root not in L.structs:
                continue
            try:
                cur = em.emit_asset(root, cur)
            except Exception as ex:
                wb = (i, nm, cur, str(ex)[:60]); break
        return em, w, cur, wb

    em1, _w1, _cur1, _wb1 = emit_pass(None)            # pass 1: build omap
    em, w, cur, walk_break = emit_pass(em1.omap)       # pass 2: relink with full omap
    if not em.subbed:
        raise RuntimeError('mapstable substitution never fired (sub_off=%d not reached; '
                           'walk_break=%r)' % (chdr, walk_break))
    # walk_break at source EOF (cur==len(zone)) is COMPLETE, not a desync: the trailing
    # bodyless (streamed) assets have no block-5 body. A break BEFORE EOF means a real
    # un-relinked tail -- surface it.
    if walk_break and cur < len(zone):
        print('   walk stopped at asset %d %s @%d: %s (tail copied verbatim -- INCOMPLETE)'
              % walk_break)
    if cur < len(zone):
        w.write_bytes(zone[cur:])
    c = S1.parse_container(zone)
    # the +delta bytes land in block 5 (VIRTUAL, where the mapstable/StringTable lives): bump the
    # XFileHeader decompressed size AND blockSize[5] so the loader allocates/reads the grown zone
    # (stale sizes truncate the tail on decompress -> corrupt zone -> boot crash).
    c['size'] += delta
    c['block_sizes'][zs.BLOCK_VIRTUAL] += delta
    edited = bytearray(S1.emit_container(c)[:r.assets_end] + bytes(w.buf))
    # RELINK the XAssetList: most assets' headerPtr is FOLLOW (inline, stream order), but a few are
    # actual block-5 offsets (aliased/shared headers). The asset array sits in the container header
    # (copied verbatim) so those offsets are NOT touched by the body walk -- if they point past the
    # mapstable they go stale after the +delta shift and the loader reads those assets from the wrong
    # place -> boot crash. Bump every block-5 headerPtr with offset >= insertion point by +delta.
    shift_from = con_body_end - B5_BASE
    ao = r.assets_off; nhp = 0
    for i in range(r.asset_count):
        ho = ao + i * 8 + 4
        h = struct.unpack_from('>I', edited, ho)[0]
        if 0xA0000001 <= h <= 0xBFFFFFFF:
            blk, off = zs.decode_ptr(h)
            if blk == zs.BLOCK_VIRTUAL and off >= shift_from:
                struct.pack_into('>I', edited, ho, zs.encode_ptr(blk, off + delta)); nhp += 1
    edited = bytes(edited)
    print('   edited zone %d B (orig %d, +%d); hdr.size=%d blockSize[5]=%d; relinked %d XAssetList ptrs' %
          (len(edited), len(zone), len(edited) - len(zone), c['size'], c['block_sizes'][zs.BLOCK_VIRTUAL], nhp))

    # self-consistency: re-walk edited zone, re-read the mapstable
    r2 = wiiu_zone.ZoneReader(edited); r2.read_string_table(); r2.read_asset_list()
    e2 = _st_header(edited, needle, le=False)
    ok_walk = False
    if e2:
        cc, rr, t2 = read_table(edited, e2[0], le=False)
        names2 = [row[0] for row in t2 if row and row[0].startswith(prefix)]
        ok_walk = (len(names2) == len([row for row in merged if row and row[0].startswith(prefix)]))
        print('   re-read edited mapstable: %d maps present (%s)' %
              (len(names2), 'OK' if ok_walk else 'MISMATCH'))

    ff = wiiu_ff.pack(edited, orig_ff_name(con_path))
    outp = os.path.join(out_dir if os.path.isabs(out_dir) else os.path.join(_ORIG_CWD, out_dir),
                        os.path.basename(con_path))
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    open(outp, 'wb').write(ff)
    print('   packed %d B ff -> %s%s' % (len(ff), outp, '' if ok_walk else '  (WALK MISMATCH - inspect)'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['recon', 'roundtrip', 'edit'])
    ap.add_argument('zone_ff')
    ap.add_argument('-o', '--out-dir', default='.')
    ap.add_argument('--pc', default=None, help='PC zone .ff (for edit: source of map rows)')
    ap.add_argument('--tag', default='mp', help='mp or zm')
    a = ap.parse_args()
    if a.cmd == 'recon':
        cmd_recon(a.zone_ff)
    elif a.cmd == 'roundtrip':
        cmd_roundtrip(a.zone_ff, a.out_dir)
    elif a.cmd == 'edit':
        cmd_edit(a.zone_ff, a.pc, a.out_dir, a.tag)


if __name__ == '__main__':
    main()
