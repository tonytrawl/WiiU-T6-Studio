"""grow_relink — cumulative-delta relink of an ARBITRARY zone (patch_mp) after a rawfile
grow, INCLUDING the verbatim tail that the structural walk can't individualize.

Generalizes ui_splice.rebuild: does the 2-pass structural relink up to the walk break,
then relinks the un-walked tail region by an OMAP-WHITELISTED cumulative-delta pass (only
words whose decoded block-5 offset is a registered pointer target get bumped -> GPU/data
words are left alone). Returns the fully relinked, still-decryptable zone bytes.
"""
import sys, os, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'wiiu_ref'))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'native_linker'))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'WiiU_FF_Studio'))
import wiiu_zone, struct_layout, zone_stream as zs
import walker as W
import body_relayout as BR
import stage1_roundtrip as S1
import ui_splice as US


def relink_grow(zone, subs, tail_relink=True, verbose=True):
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    total_delta = sum(len(b) - (e - o) for o, (e, b) in subs.items())
    deltas = sorted((o, len(b) - (e - o)) for o, (e, b) in subs.items())

    def cumdelta(fo):
        d = 0
        for off, dl in deltas:
            if off < fo:
                d += dl
            else:
                break
        return d

    class WLEdit(US.MultiEdit):
        """Omap-WHITELISTED remap: only bump a block-5 pointer whose decoded offset is a
        REGISTERED file-space target. Runtime-baked pointers (SndBank zone*/language* etc.)
        decode to runtime offsets that are NOT registered file targets, so they are left
        untouched here and must be re-baked separately with the RUNTIME delta."""
        def remap_ptr(self, v):
            if v in (zs.FOLLOW, zs.INSERT, 0):
                return v
            if not (0xA0000000 <= v < 0xC0000000):
                return v
            blk, off = zs.decode_ptr(v)
            if blk == zs.BLOCK_VIRTUAL and self.delta and off in self.omap:
                cd = self.cumdelta(off + BR.B5_BASE)
                if cd:
                    return zs.encode_ptr(blk, off + cd)
            return v

    def emit_pass(omap0):
        w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
        w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - BR.B5_BASE
        em = (WLEdit if omap0 is not None else US.MultiEdit)(zone, L, zc, w, subs); em.delta = 1
        if omap0 is not None:
            em.omap.update(omap0)
        cur = r.assets_end; wb = None
        for (cid, pc_, nm) in r.assets:
            root = W.ASSET_ROOT.get(nm)
            if root is None or root not in L.structs:
                continue
            try:
                cur = em.emit_asset(root, cur)
            except Exception as ex:
                wb = (nm, cur, str(ex)[:60]); break
        return em, w, cur, wb

    em1, *_ = emit_pass(None)          # pass 1: build complete omap
    omap = dict(em1.omap)
    em, w, cur, wb = emit_pass(omap)   # pass 2: relink with full omap
    if len(em.subbed) != len(subs):
        raise RuntimeError('only %d/%d subs fired; break=%r' % (len(em.subbed), len(subs), wb))
    if verbose and wb:
        print("  structural walk broke at %r (0x%x); %d assets relinked, tail verbatim"
              % (wb[0], wb[1], len(r.assets)))

    tail_src = cur                     # original-zone offset where verbatim tail starts
    tail = bytearray(zone[tail_src:])
    bumped = 0
    if tail_relink and total_delta:
        # OMAP-whitelisted cumulative-delta relink of the un-walked tail
        for fo in range(0, len(tail) - 3, 4):
            v = struct.unpack_from('>I', tail, fo)[0]
            if not (0xA0000000 <= v < 0xC0000000):
                continue
            blk, off = zs.decode_ptr(v)
            if blk != zs.BLOCK_VIRTUAL or off not in omap:
                continue               # only bump words pointing at a REAL registered target
            cd = cumdelta(off + BR.B5_BASE)
            if cd:
                struct.pack_into('>I', tail, fo, zs.encode_ptr(blk, off + cd)); bumped += 1
    if verbose:
        print("  tail relink: %d whitelisted pointers bumped (tail %d B from 0x%x)"
              % (bumped, len(tail), tail_src))

    c = S1.parse_container(zone)
    c['size'] += total_delta
    c['block_sizes'][zs.BLOCK_VIRTUAL] += total_delta
    edited = bytearray(S1.emit_container(c)[:r.assets_end] + bytes(w.buf) + bytes(tail))

    # XAssetList headerPtr relink (cumulative)
    ao = r.assets_off; nhp = 0
    for i in range(r.asset_count):
        ho = ao + i * 8 + 4; h = struct.unpack_from('>I', edited, ho)[0]
        if 0xA0000001 <= h <= 0xBFFFFFFF:
            blk, off = zs.decode_ptr(h)
            if blk == zs.BLOCK_VIRTUAL:
                cd = cumdelta(off + BR.B5_BASE)
                if cd:
                    struct.pack_into('>I', edited, ho, zs.encode_ptr(blk, off + cd)); nhp += 1
    return bytes(edited), total_delta, nhp, bumped
