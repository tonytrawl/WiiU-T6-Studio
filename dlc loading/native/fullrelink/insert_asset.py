"""insert_asset — TRUE additive relink: insert new assets at their correct position in the
XAssetList's dependency order, with a real multi-region cumulative-delta pointer relink.

WHY POSITION MATTERS (measured)
-------------------------------
The XAssetList array is NOT sorted by type id. It is the linker's **dependency order**, a fine-grained
interleaving of short same-type runs, e.g.:

    [1438..1453] MATERIAL x16 · [1454] TECHNIQUE_SET · [1455..1457] MATERIAL x3 ·
    [1458] XANIMPARTS · [1459..1462] RAWFILE x4 · ... · [1509] RAWFILE · [1510..1532] IMAGE x23

`add_asset.py` appended new entries AFTER the trailing IMAGE run. That build produced a graceful
OSFatal during the link ("Could not load default asset 'default' for asset type 'techset'"), even
though its byte layout was provably correct (every walked span exactly baseline+16, both new bodies
byte-exactly where intended, container fields consistent). This module places each new asset
immediately after the last existing asset of the SAME TYPE instead.

ZERO-BYTE ASSETS — the trap that makes naive index math wrong
-------------------------------------------------------------
Six array entries carry an **aliased headerPtr** (indices 1510, 1511, 1512, 1513, 1520, 1524, all
inside the trailing IMAGE run) instead of FOLLOW. Their header is shared with another asset, so they
consume **ZERO stream bytes**. The structural walker does not model this: it hands each of them a
328-byte body, over-consuming 6 x 328 = 1,968 bytes and then breaking 6 entries early.

⇒ Walker span[k] corresponds to array index k **only for k < 1510**. This module asserts every
insertion index is below the first aliased index, so all anchors come from the trustworthy region.

WHAT ACTUALLY NEEDS RELINKING
-----------------------------
Measured, not assumed:
  * The +92 mid-body growth (boot-proven, shipping) relinks **zero** structural pointer fields. Of
    56,856 alias-range words sitting in struct-defined pointer fields, none resolve to a registered
    target under any constant offset -- they are FOLLOW, NULL, or runtime-domain handles
    (cf. the texdef handle law, a runtime address rather than a file offset).
  * Every word the blind omap scan rewrote inside a VERBATIM body was a false positive (28/28,
    all landing in a StringTable interior). Verbatim bodies must be byte-copies. See verbatim_gate.
  * The genuine block-5 pointers are the **aliased headerPtrs** in the XAssetList array.

So a correct relink = shift the regions, recompute each aliased headerPtr through the cumulative
delta AT ITS TARGET, and then PROVE nothing else moved. The proof is not optional decoration -- it is
what distinguishes this from a patch-the-symptom fix, so `insert_assets` refuses to return a zone
that fails its own checks.

INSERTION SEMANTICS
-------------------
An insertion at file offset X of d bytes means the new bytes occupy [X, X+d) and the byte formerly at
X now lives at X+d. So a pointer whose target is AT X must also move: cumdelta uses `off <= X`,
unlike grow_relink's substitution semantics which use `off < X`.
"""
import struct

import wiiu_zone, zone_stream as zs
import verbatim_gate as VG

XASSETLIST_OFF  = 40
ASSET_COUNT_OFF = XASSETLIST_OFF + 16
BLOCK_SIZES_OFF = 8
TOTAL_SIZE_OFF  = 0
B5_BASE         = 64
HEAD_WINDOW     = 0x1000   # rt bytes past arrayEnd that a +8N shift can still reach


def build_body(kind, name, data):
    """SCRIPTPARSETREE and RAWFILE share one measured console layout:
         +0 u32 name=FOLLOW | +4 u32 len | +8 u32 buffer=FOLLOW | name\\0 | data | one NUL
    (verified: consecutive records of both types are separated by exactly 1 byte)."""
    if isinstance(name, str):
        name = name.encode()
    if kind == 'spt':
        assert data[:4] == b'\x80GSC', 'not console GSC bytecode: %r' % data[:4]
    return (struct.pack('>III', zs.FOLLOW, len(data), zs.FOLLOW)
            + name + b'\x00' + data + b'\x00')


def _aliased_indices(zone, r):
    return [k for k in range(r.asset_count)
            if 0xA0000001 <= struct.unpack_from('>I', zone, r.assets_off + k*8 + 4)[0] <= 0xBFFFFFFF]


def plan_after_last_of_type(zone, type_name):
    """Array index a new asset should OCCUPY to sit immediately after the last existing asset of
    `type_name`, plus the file offset its body must start at."""
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    spans = VG.asset_spans(zone)
    idx = [k for k, (c, p, n) in enumerate(r.assets) if n == type_name]
    if not idx:
        raise KeyError('no existing %s asset to anchor to' % type_name)
    last = max(idx)
    first_aliased = min(_aliased_indices(zone, r))
    assert last < first_aliased, (
        '%s anchor at array index %d is at/after the first zero-byte aliased entry %d -- walker '
        'span boundaries are not trustworthy there' % (type_name, last, first_aliased))
    return last + 1, spans[last][1]      # (array index to occupy, body insertion file offset)


def insert_assets(zone, items, verbose=True, freeze_headerptrs=True):
    """items: list of dicts {type_id, type_name, body, array_index, body_off}.
    Returns (new_zone, stats). Raises if any self-check fails."""
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    spans_before = VG.asset_spans(zone)
    aliased = _aliased_indices(zone, r)

    # ---- build the insertion list in FILE order ----
    ins = []                                     # (file_offset, delta, payload)
    for it in items:
        ins.append((r.assets_off + it['array_index'] * 8, 8,
                    struct.pack('>II', it['type_id'], zs.FOLLOW)))
    for it in items:
        ins.append((it['body_off'], len(it['body']), it['body']))
    ins.sort(key=lambda t: t[0])
    total_delta = sum(d for _, d, _ in ins)

    def cumdelta(fo):
        """Bytes inserted at or before `fo`. Insertion semantics: content at the insertion offset
        is pushed forward, so `<=` (not `<`)."""
        return sum(d for off, d, _ in ins if off <= fo)

    # ---- splice ----
    out = bytearray()
    cur = 0
    for off, d, payload in ins:
        out += zone[cur:off]
        out += payload
        cur = off
    out += zone[cur:]
    assert len(out) == len(zone) + total_delta

    # ---- container fields ----
    struct.pack_into('>I', out, ASSET_COUNT_OFF, r.asset_count + len(items))
    struct.pack_into('>I', out, TOTAL_SIZE_OFF,
                     struct.unpack_from('>I', out, TOTAL_SIZE_OFF)[0] + total_delta)
    bs = BLOCK_SIZES_OFF + zs.BLOCK_VIRTUAL * 4
    struct.pack_into('>I', out, bs, struct.unpack_from('>I', out, bs)[0] + total_delta)

    # ---- aliased headerPtrs: FREEZE, do not relink ----
    #
    # These 6 entries (patch_mp: 1510-1513, 1520, 1524) hold a non-FOLLOW headerPtr. They were
    # assumed to be intra-zone pointers to a shared asset header and were relinked by the growth
    # delta. MEASURED: they resolve to NOTHING in this file --
    #   * 0/6 land on a walked asset start under file = b5+64
    #   * 0/6 land on one under the allocation-exact rt model's inverse mapping
    #   * 0/6 land on any of the 61 GfxImage-shaped structures found zone-wide
    #   * no constant C exists for which all 6 hit an asset start
    # patch_mp is a TU PATCH zone, so the overwhelmingly likely reading is that these are references
    # into an ALREADY-LOADED base zone (common / code_post_gfx), baked by the linker. A pointer into
    # another zone's memory is not a file offset and adding a growth delta to it is pure corruption.
    #
    # Both additive builds that failed with 0 broken container refs (probe1 @1510, v1 @1533) had all
    # 6 relinked; the freeze policy has been correct everywhere else it has been tested.
    # Set freeze_headerptrs=False only if a dump ever proves these are intra-zone.
    relinked = []
    for k in (() if freeze_headerptrs else aliased):
        ho_old = r.assets_off + k * 8 + 4
        # entry k only shifts by the entries inserted at or before its own index
        ho_new = (r.assets_off + k * 8 + 4
                  + 8 * sum(1 for it in items if it['array_index'] <= k))
        h = struct.unpack_from('>I', zone, ho_old)[0]
        blk, off = zs.decode_ptr(h)
        assert blk == zs.BLOCK_VIRTUAL
        tgt_file = off + B5_BASE
        cd = cumdelta(tgt_file)
        nh = zs.encode_ptr(blk, off + cd)
        struct.pack_into('>I', out, ho_new, nh)
        # PROOF: the relinked pointer must resolve to the very same bytes it did before
        assert out[off + cd + B5_BASE: off + cd + B5_BASE + 64] == zone[tgt_file: tgt_file + 64], \
            'aliased headerPtr for array[%d] no longer resolves to identical bytes' % k
        relinked.append((k, h, nh, cd))

    # ---- HEAD-WINDOW RELINK: the one thing an asset-count change actually breaks ----
    #
    # The XAsset array is allocated from the SAME monotonically-advancing block-5 cursor as every
    # asset body, so assetCount += N moves arrayEnd_rt by 8*N and displaces the block-5 data that
    # follows it. The shift does NOT propagate: DB_AllocStreamPos @0x0223FC34 is
    #     pos = (pos + MASK) & ~MASK
    # so after any ALLOC(mask) both layouts are congruent mod (mask+1) and a delta of 8 cannot
    # survive a single ALLOC(15). Only a short HEAD WINDOW right after the array moves; everything
    # downstream is already correct and must NOT be touched.
    #
    # patch_mp asset #0 is a PhysPreset whose 84-byte root goes to TEMP, so its name string
    # "default NUL" is the FIRST block-5 body datum -- it sits exactly AT arrayEnd_rt. Asset #5 is the
    # MaterialTechniqueSet literally named "default", and it is the ONLY 1 of 66 techsets whose name
    # field is an ALIAS instead of FOLLOW. g_defaultAssetName == "default" and
    # DB_FindXAssetDefaultHeaderInternal is a pure NAME lookup, so the whole techset-default safety
    # net hangs on that single alias. Leave it stale and the techset registers under the empty
    # string, the default lookup returns NULL, and the first comma-ref that misses raises
    #     Could not load default asset 'default' for asset type 'techset'.
    # -- which is precisely how all four earlier additive builds died.
    #
    # STRUCTURAL rule (no value-scanning): an asset whose name field (+0) is an ALIAS pointing at or
    # past the OLD arrayEnd_rt must be bumped by 8*N.
    arr = r.assets_off - B5_BASE
    arr_end_old = ((arr + 3) & ~3) + 8 * r.asset_count          # array is 4-aligned in rt, NOT 8
    head = []
    for s0, e0, nm, vb in spans_before:
        cd = cumdelta(s0)
        v = struct.unpack_from('>I', out, s0 + cd)[0]
        if not (0xA0000000 <= v < 0xC0000000):
            continue
        raw = (v - 1) & 0x1FFFFFFF
        # Bound the window: the delta collapses at the first ALLOC(mask>=15) wall, so only names
        # AT arrayEnd move. Measured on patch_mp: exactly 2 sit at +0 and the next is +256,756
        # away, so any bound in [0, 4096] selects the same 2. HEAD_WINDOW is deliberately
        # generous -- the natural gap, not the constant, is what makes this safe.
        if ((v - 1) >> 29) != zs.BLOCK_VIRTUAL:
            continue
        if not (arr_end_old <= raw < arr_end_old + HEAD_WINDOW):
            continue
        # encoding is value = ((block<<29) | offset) + 1, so a pure delta on the VALUE keeps the
        # +1 and the block bits intact. Rebuilding from `raw` and forgetting the +1 lands one
        # byte short -- which is exactly the off-by-one this line replaced.
        nv = v + 8 * len(items)
        struct.pack_into('>I', out, s0 + cd, nv)
        head.append((nm, s0, v, nv))
    if verbose:
        print('   head-window relink: arrayEnd_rt %d -> %d, %d name alias(es) bumped +%d %s'
              % (arr_end_old, arr_end_old + 8*len(items), len(head), 8*len(items),
                 [h[0] for h in head]))

    # ---- PROOF 1: every pre-existing asset body is byte-identical, just moved ----
    moved = 0
    for s, e, nm, vb in spans_before:
        cd = cumdelta(s)
        if bytes(out[s + cd:e + cd]) != zone[s:e]:
            if any(h[1] == s for h in head):        # the deliberate head-window name bump
                if bytes(out[s+cd+4:e+cd]) != zone[s+4:e]:
                    raise AssertionError('asset %s @0x%x changed beyond its name field' % (nm, s))
            else:
                raise AssertionError('asset %s @0x%x changed content (cumdelta %+d)' % (nm, s, cd))
        if cd:
            moved += 1

    # ---- PROOF 2: no verbatim body word differs from the source ----
    for s, e, nm, vb in spans_before:
        if not vb:
            continue
        cd = cumdelta(s)
        skip = s if any(h[1] == s for h in head) else -1
        for fo in range(s, e - 3, 4):
            if fo == skip:
                continue
            if struct.unpack_from('>I', out, fo + cd)[0] != struct.unpack_from('>I', zone, fo)[0]:
                raise AssertionError('verbatim word changed in %s @0x%x' % (nm, fo))

    # ---- PROOF 3: the new zone re-reads with the expected shape ----
    r2 = wiiu_zone.ZoneReader(bytes(out)); r2.read_string_table(); r2.read_asset_list()
    assert r2.asset_count == r.asset_count + len(items)
    assert struct.unpack_from('>I', out, TOTAL_SIZE_OFF)[0] == len(out) - 40
    for it in items:
        # `array_index` is an ORIGINAL-array position (the new entry is inserted before the entry
        # that used to sit there). Its FINAL index also counts every entry inserted ahead of it.
        final = it['array_index'] + sum(1 for o in items if o['array_index'] < it['array_index'])
        got = r2.assets[final][2]
        assert got == it['type_name'], \
            'array[%d] (orig %d) is %s, expected the new %s' \
            % (final, it['array_index'], got, it['type_name'])
        it['final_index'] = final

    stats = dict(total_delta=total_delta, insertions=len(ins), relinked=relinked, head=head,
                 frozen_headerptrs=len(aliased) if freeze_headerptrs else 0,
                 moved_assets=moved, old_count=r.asset_count, new_count=r2.asset_count)
    if verbose:
        print('   insertions: %d regions, %+d bytes total' % (len(ins), total_delta))
        print('   aliased headerPtrs: %d relinked, %d FROZEN (not file pointers)'
              % (len(relinked), len(aliased) if freeze_headerptrs else 0))
        print('   pre-existing bodies verified byte-identical: %d (%d moved)'
              % (len(spans_before), moved))
    return bytes(out), stats
