#!/usr/bin/env python3
"""stale_position_scan.py — OFFLINE decisive test for the additive-growth blocker.

Question (task #19): after a correct block-5 relink, is the mid-stream-shift
failure caused by a NON-POINTER absolute-position value in the zone that the
relink can't see? (category a) — or is the zone structurally correct and the
failure is a RUNTIME placement/residency dependence? (category b)

Method: build the omap + per-asset body offsets, pick the insertion point, and
scan EVERY 4-aligned word for a plain integer (not a block-5 alias / FOLLOW /
INSERT) whose value EXACTLY equals a registered relink target or an asset-body
boundary that lies in the shifted region [insertion, EOF]. Any such word located
BEFORE the insertion is a stale absolute reference the relink misses.

RESULT (patch_mp TU stockbak, 1533 assets, insertion @asset 1373 = file 12444756):
  plain-int position-refs into shifted region: 3 total, 2 before insertion —
  BOTH coincidental (a MATERIAL constant @asset 751, an SPT bytecode operand
  @asset 1027; wrong asset types, implausible targets). FX bodies (1401-1428,
  the CROSSREF "pointer-free position-dependent" flag): 0 hits. SOUND_PATCH
  offsets are into the EXTERNAL .sabl (small, shift-invariant).
  => CATEGORY (a) ELIMINATED. The relinked zone is structurally correct;
     the blocker is category (b) = runtime buffer placement (task #21 / guest-debug).

CAVEAT: cannot check RUNTIME-address encodings (value == off + runtime_base)
offline — no patch_mp runtime dump, runtime base unknown. That gap only
reinforces (b): a runtime-address field is itself a runtime concern.
"""
import sys, os, struct, bisect, pickle

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, '..', '..', '..', 'wiiu_ref'),
          os.path.join(HERE, '..', '..', '..', 'native_linker'),
          os.path.join(HERE, '..', '..', '..', 'WiiU_FF_Studio')):
    sys.path.insert(0, p)
import wiiu_ff, wiiu_zone, struct_layout, zone_stream as zs
import walker as W, body_relayout as BR, ui_splice as US

isalias = lambda v: 0xA0000000 <= v < 0xC0000000


def build_map(zone):
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - BR.B5_BASE
    em = US.MultiEdit(zone, L, zc, w, {}); em.delta = 0
    cur = r.assets_end
    bodyoff = []
    for i, (cid, pc_, nm) in enumerate(r.assets):
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            bodyoff.append((i, nm, cur, None)); continue
        start = cur
        try:
            cur = em.emit_asset(root, cur)
        except Exception:
            bodyoff.append((i, nm, start, None)); break
        bodyoff.append((i, nm, start, cur))
    return r, dict(em.omap), bodyoff


def scan(zone, omap, bodyoff, ins):
    B5 = BR.B5_BASE
    tgt_b5 = set(omap)
    tgt_file = set(o + B5 for o in tgt_b5)
    body_starts = set(s for (_, _, s, e) in bodyoff)
    body_ends = set(e for (_, _, s, e) in bodyoff if e)
    starts_sorted = sorted((s, i, nm) for (i, nm, s, e) in bodyoff if e)
    sk = [s for s, _, _ in starts_sorted]

    def owner(fo):
        j = bisect.bisect_right(sk, fo) - 1
        return (starts_sorted[j][1], starts_sorted[j][2]) if 0 <= j < len(sk) else (None, None)

    hits = []
    for fo in range(0, len(zone) - 3, 4):
        v = struct.unpack_from('>I', zone, fo)[0]
        if isalias(v) or v in (0xFFFFFFFF, 0xFFFFFFFE, 0):
            continue
        kind = None
        if v in tgt_b5 and v + B5 >= ins:
            kind = 'b5target->file%d' % (v + B5)
        elif v in tgt_file and v >= ins:
            kind = 'filetarget'
        elif v in body_starts and v >= ins:
            kind = 'assetStart'
        elif v in body_ends and v >= ins:
            kind = 'assetEnd'
        if kind:
            oi, onm = owner(fo)
            hits.append((fo, v, kind, oi, onm, fo < ins))
    return hits


if __name__ == '__main__':
    ffpath = sys.argv[1] if len(sys.argv) > 1 else (
        r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr'
        r'\title\0005000e\1010cf00\content\english\patch_mp.ff.stockbak')
    ins = int(sys.argv[2]) if len(sys.argv) > 2 else 12444756
    z = wiiu_ff.decrypt(open(ffpath, 'rb').read())
    if isinstance(z, dict):
        z = [v for v in z.values() if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    elif isinstance(z, tuple):
        z = [v for v in z if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    zone = bytes(z)
    r, omap, bodyoff = build_map(zone)
    hits = scan(zone, omap, bodyoff, ins)
    print('omap=%d  assets walked=%d  insertion@%d' % (len(omap), len(bodyoff), ins))
    print('plain-int position-refs into shifted region: %d (%d before insertion)'
          % (len(hits), sum(1 for h in hits if h[5])))
    for fo, v, kind, oi, onm, before in hits:
        print('  word@%d %s (asset #%s %s) = %d [%s]'
              % (fo, 'BEFORE-INS' if before else 'in-shift', oi, onm, v, kind))
