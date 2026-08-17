"""add_asset — ADDITIVE zone growth: append a brand-new asset to a linked Wii U zone
without replacing an existing one.

WHY THIS SHAPE
--------------
Adding an XAssetList array entry inserts 8 bytes at the container/body boundary, which is
BEFORE every block-5 payload byte in the zone. So the relink degenerates to a UNIFORM
delta: every genuine block-5 pointer moves by exactly +8 (+ any appended-body growth is
at EOF and moves nothing). No threshold, no cumulative-delta bookkeeping, no ambiguity
about which side of an insertion point a target sits on. This is strictly the easiest
growth case in the zone -- easier than the proven-on-hardware loading.lua +92 rawfile grow.

The only hard part is the same one grow_relink already solved: telling a genuine pointer
from GX2 register garbage that merely *looks* like an alias. We reuse its answer -- the
OMAP WHITELIST (a word is only bumped if its decoded block-5 offset is a REGISTERED
pointer target harvested from a full structural pass). Blind value-scanning is the
`_editT` failure mode and corrupts ~88.5k GPU words. See FINDINGS_patch_fullrelink.md.

⚠ LANDMINE THIS MODULE AVOIDS
-----------------------------
`stage1_roundtrip.parse_container` does `o = (o + 3) & ~3` before the asset array. That is
WRONG: in patch_mp the array starts UNALIGNED at 0x11F9 (the script-string blob ends with
"..._attach\0" at 0x11F8 and the array follows immediately). parse_container reads the
array from 0x11FC -- a 3-byte-shifted garbage window.

It round-trips byte-identically ONLY BY COINCIDENCE: the 3 alignment zeros it writes happen
to equal the top 3 bytes of entry[0]'s big-endian type field (0x00000001), and everything
after is copied verbatim from the shifted window. The model is wrong even though the bytes
match.

The coincidence collapses the instant you GROW the array: an appended entry lands at
0x41E4 instead of 0x41E1, i.e. 3 bytes too late, silently corrupting the boundary. So this
module never calls emit_container -- it splices the container raw in ZoneReader
coordinates (r.assets_off = 0x11F9), which are the correct ones.
"""
import sys, os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for rel in ('wiiu_ref', 'native_linker', 'WiiU_FF_Studio'):
    sys.path.insert(0, os.path.join(HERE, '..', '..', '..', rel))

import wiiu_zone, struct_layout, zone_stream as zs
import walker as W
import body_relayout as BR
import ui_splice as US

XASSETLIST_OFF   = 40          # raw XAssetList prefix, before block 5
ASSET_COUNT_OFF  = XASSETLIST_OFF + 16
BLOCK_SIZES_OFF  = 8
TOTAL_SIZE_OFF   = 0
SPT_TYPE_CONSOLE = 50          # SCRIPTPARSETREE


def build_spt_body(name, gsc):
    """ScriptParseTree record, exactly as measured in patch_mp:
         +0  u32 name   = FOLLOW
         +4  u32 len    = len(gsc)
         +8  u32 buffer = FOLLOW
         +12 name cstr
             gsc bytes
             one trailing NUL   (measured: consecutive SPT bodies are separated by exactly 1)
    """
    if isinstance(name, str):
        name = name.encode()
    assert gsc[:4] == b'\x80GSC', 'not console GSC bytecode: %r' % gsc[:4]
    return (struct.pack('>III', zs.FOLLOW, len(gsc), zs.FOLLOW)
            + name + b'\x00' + gsc + b'\x00')


def _harvest_omap(zone, r, L, zc):
    """Pass 1: full structural walk with no edits, purely to register every genuine
    block-5 pointer target. Returns (omap, walk_break, walk_end)."""
    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - BR.B5_BASE
    em = US.MultiEdit(zone, L, zc, w, {}); em.delta = 0
    cur, wb = r.assets_end, None
    for (cid, pc_, nm) in r.assets:
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            continue
        try:
            cur = em.emit_asset(root, cur)
        except Exception as ex:
            wb = (nm, cur, str(ex)[:70]); break
    return dict(em.omap), wb, cur


def add_assets(zone, new_assets, verbose=True):
    """new_assets: list of (console_type_id, body_bytes). Bodies are appended at EOF in
    the given order; array entries are appended in the same order so the loader's
    sequential body reads stay in lockstep with the array.

    Returns (new_zone_bytes, stats dict).
    """
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)

    hdr_delta = 8 * len(new_assets)          # array growth -> uniform shift of ALL of block 5
    appended  = b''.join(b for _, b in new_assets)

    omap, wb, walk_end = _harvest_omap(zone, r, L, zc)
    if verbose:
        print('  omap: %d registered block-5 targets' % len(omap))
        if wb:
            print('  structural walk broke at %r (0x%x): %s' % wb)

    # ---- pass 2: structural relink, uniform +hdr_delta on whitelisted pointers ----
    class Uniform(US.MultiEdit):
        def cumdelta(self, file_off):
            return hdr_delta                  # insertion precedes every block-5 byte

        def remap_ptr(self, v):
            if v in (zs.FOLLOW, zs.INSERT, 0):
                return v
            if not (0xA0000000 <= v < 0xC0000000):
                return v
            blk, off = zs.decode_ptr(v)
            if blk == zs.BLOCK_VIRTUAL and off in self.omap:
                return zs.encode_ptr(blk, off + hdr_delta)
            return v

    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - BR.B5_BASE
    em = Uniform(zone, L, zc, w, {}); em.delta = 1; em.omap.update(omap)
    cur = r.assets_end
    for (cid, pc_, nm) in r.assets:
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            continue
        try:
            cur = em.emit_asset(root, cur)
        except Exception:
            break
    assert cur == walk_end, 'pass-2 walk diverged from pass-1 (0x%x vs 0x%x)' % (cur, walk_end)

    # ---- un-walked verbatim tail: same omap whitelist, uniform delta ----
    tail = bytearray(zone[walk_end:])
    bumped = 0
    for fo in range(0, len(tail) - 3, 4):
        v = struct.unpack_from('>I', tail, fo)[0]
        if not (0xA0000000 <= v < 0xC0000000):
            continue
        blk, off = zs.decode_ptr(v)
        if blk != zs.BLOCK_VIRTUAL or off not in omap:
            continue
        struct.pack_into('>I', tail, fo, zs.encode_ptr(blk, off + hdr_delta)); bumped += 1
    if verbose:
        print('  tail relink: %d whitelisted pointers bumped (tail %d B from 0x%x)'
              % (bumped, len(tail), walk_end))

    # ---- container splice in ZoneReader (correct, UNALIGNED) coordinates ----
    out = bytearray(zone[:r.assets_end])              # header + strings + 1533 entries
    for t, _ in new_assets:
        out += struct.pack('>II', t, zs.FOLLOW)       # FOLLOW = header is inline at the body
    out += bytes(w.buf) + bytes(tail) + appended

    total_delta = hdr_delta + len(appended)
    struct.pack_into('>I', out, ASSET_COUNT_OFF,
                     r.asset_count + len(new_assets))
    struct.pack_into('>I', out, TOTAL_SIZE_OFF,
                     struct.unpack_from('>I', out, TOTAL_SIZE_OFF)[0] + total_delta)
    bs = BLOCK_SIZES_OFF + zs.BLOCK_VIRTUAL * 4
    struct.pack_into('>I', out, bs,
                     struct.unpack_from('>I', out, bs)[0] + total_delta)

    # ---- XAssetList headerPtr relink (the few real aliases among the FOLLOWs) ----
    nhp = 0
    for i in range(r.asset_count):
        ho = r.assets_off + i * 8 + 4
        h = struct.unpack_from('>I', out, ho)[0]
        if 0xA0000001 <= h <= 0xBFFFFFFF:
            blk, off = zs.decode_ptr(h)
            if blk == zs.BLOCK_VIRTUAL:
                struct.pack_into('>I', out, ho, zs.encode_ptr(blk, off + hdr_delta)); nhp += 1

    stats = dict(hdr_delta=hdr_delta, appended=len(appended), total_delta=total_delta,
                 omap=len(omap), tail_bumped=bumped, headerptrs=nhp,
                 walk_break=wb, walk_end=walk_end,
                 old_count=r.asset_count, new_count=r.asset_count + len(new_assets))
    return bytes(out), stats


def verify(zone, expect_count, expect_names=()):
    """Re-read the produced zone the way the loader would: array at the UNALIGNED offset,
    every declared asset resolvable, sizes self-consistent."""
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    ok = True
    if r.asset_count != expect_count:
        print('  FAIL asset_count %d != %d' % (r.asset_count, expect_count)); ok = False
    size = struct.unpack_from('>I', zone, TOTAL_SIZE_OFF)[0]
    if size != len(zone) - 40:
        print('  FAIL size field %d != len-40 %d' % (size, len(zone) - 40)); ok = False
    for nm in expect_names:
        if zone.find(nm.encode() + b'\x00') < 0:
            print('  FAIL new asset name %r absent' % nm); ok = False
    tail_names = [n for _, _, n in r.assets[-len(expect_names):]] if expect_names else []
    print('  verify: count=%d size_ok=%s tail_types=%s' %
          (r.asset_count, size == len(zone) - 40, tail_names))
    return ok
