#!/usr/bin/env python3
"""
CONTAINER AUTHOR (Track G, final assemble stage). Authors the REAL console
zone around the no-backbone body emit (produce_nobackbone.assemble_zone):
script-string table (PC verbatim — proven byte-equal to genuine), console
XAsset array (type remap + MP inserts), XFile header + block sizes, body
stream in true console order (english bank threaded through the pass-3 sim).

  author_zone(pc_path, map_name) -> (zone_bytes, info)
  raid_dryrun()  : author the raid container and diff every container level
                   (strings, array rows, hp aliases, header words) against
                   mp_raid_genuine + re-walk our own zone. Mandated first gate.

MP insert set (pinned vs mp_raid_genuine, 889 = 887 + 2):
  * GLASSES ALIAS row at console index 1. Its hp is a stale linker-heap
    don't-care (raid 0xe1af0513 "block 7", dockside 0x41b01f13 "block 2" —
    arbitrary values into zero-sized blocks; loader resolves aliases by name).
    We carry raid's genuine word verbatim.
  * PC GLASSES asset relabels to MAP_ENTS (console type 47), body stays inline.
  * localized SndBank row + body (mpl_<map>.english, author_english_bank)
    inserted BEFORE the main SOUND row/body (genuine: .english @871, .all @872).

Header block sizes (measured raid+dockside genuine):
  block0 = 4780, block2 = 12,976,128 : MP CONSTANTS (fixed pools)
  block1 (RT_TEMP) varies/unmodeled  : carry raid's 2,969,732 (>= dockside;
                                       registered approximation, boot sheet)
  block5 = our pass-3 sim total + SAFETY_B5 margin (sim under-reads genuine by
           1,065 raid / 18,934 dock; undersize is fatal, oversize is safe)
"""
import sys, os, struct
sys.path.insert(0, '.'); sys.path.insert(0, os.path.join('..', 'wiiu_ref'))
import pc_zone, wiiu_zone
import produce_nobackbone as PN
import smalls_convert as SC
import _assetlist_author as ALA

FOLLOW = 0xFFFFFFFF
B5_BASE = 64
GLASSES_ALIAS_HP = 0xe1af0513          # genuine raid stale-heap word (don't-care)
BLOCK0_MP = 4780
BLOCK1_MP = 2969732                    # raid genuine (registered approximation)
BLOCK2_MP = 12976128
SAFETY_B5 = 262144                     # block-5 allocation margin
RUNTIME_BAND = 4 << 20                  # block5 must exceed content by the runtime
                                        # DPVS band (genuine 0.44-1.09MB); 4MB is a
                                        # generous floor for a blind zone (oversize safe)

# ---- ZM container constants (measured genuine zm_transit_original, the ONLY
# genuine console ZM map zone; header = 2652, 5637384, 11534336, 0, 0, b5) ----
BLOCK0_ZM = 2652
BLOCK1_ZM = 5637384                    # transit genuine (registered approximation)
BLOCK2_ZM = 11534336
# genuine transit rows 1..3: three GLASSES alias rows (stale-heap hp don't-cares,
# carried verbatim like MP carries raid's single word). ZM has NO MAP_ENTS
# relabel (a ZM map's GLASSES row is a REAL glasses asset, stays type 48) and NO
# english-SndBank insert (genuine ZM map zones carry no localized bank; transit
# has no SndBank row at all). The genuine 9-row LOCALIZE_ENTRY block @2779+ is
# map-content hint text (ZOMBIE_TRANSIT_*) — cosmetic, omitted for the blind
# build (missing localize falls back to the key string, no boot impact).
GLASSES_ALIAS_HP_ZM = (0x95b01013, 0x39b11013, 0x6db11013)


def _pc_string_region(PC, rp):
    """(ptr_words, inline_bytes): the script-string table exactly as PC
    serializes it — reused verbatim on console (proven byte-equal)."""
    sc = rp.string_count
    o = 64
    ptrs = list(struct.unpack_from('<%dI' % sc, PC, o))
    o += sc * 4
    body = bytearray()
    for p in ptrs:
        if p == FOLLOW:
            e = PC.index(b'\x00', o)
            body += PC[o:e + 1]
            o = e + 1
    return ptrs, bytes(body)


def author_rows(rp):
    """Console asset rows [(console_type, name, kind)] with kind in
    {'follow','alias-glasses','relabel-mapents','english'} + the PC row index
    each console row derives from (None for inserts)."""
    rows = []
    for i, (t, nm, hp) in enumerate(rp.assets):
        ct = ALA.pc_to_console_type(t, nm)
        if i == 0:
            rows.append((ct, nm, 'follow', i))
            rows.append((48, 'GLASSES', 'alias-glasses', None))
            continue
        if nm == 'GLASSES':
            rows.append((47, 'MAP_ENTS', 'relabel-mapents', i))
            continue
        if nm == 'SOUND':
            rows.append((ct, nm, 'english', None))     # localized bank FIRST
            rows.append((ct, nm, 'follow', i))         # then the main bank
            continue
        rows.append((ct, nm, 'follow', i))
    return rows


def author_rows_zm(rp):
    """ZM console asset rows (template: genuine zm_transit). PC rows in order;
    3 GLASSES alias rows inserted at console indices 1..3. No SOUND duplication
    (a ZM zone's own SOUND row, if any, stays a single follow row). The genuine
    transit XMODEL relocation (PC 1345 -> CO 1349) is a genuine-linker ordering
    artifact, NOT replicated: our array only has to match OUR emitted stream
    order.

    GLASSES CONTENT relabels to console type 47 exactly like MP: a row TYPED
    48/GLASSES is a 16-byte no-follow stub on console (walker override,
    genuine-calibrated) — the full glasses body loads under type 47 (raid
    idx851, walked via _looks_like_glasses). Discovered on the zm_nuked rewalk
    (glasses row typed 48 desynced the walk at +16). Type enum shared with the
    ZM engine (patch_zm walks byte-identical under the same remap).

    SOUND (SndBank) row is DROPPED: genuine console ZM map zones carry ZERO
    SndBank rows (transit: 0/3266) — zombie audio ships in the GLOBAL
    zmb_patch.all/.english banks loaded by patch_zm.ff + the per-map .sab files
    on disc. Our PC-converted bank ("zmb_nuked_real.all", 37.4MB) has NO console
    oracle, so its aliases carry PC-computed hashes the AX voice relink faults on
    AND it references streamed files by PLUTONIUM ".all.sabs" names that do not
    exist on WiiU (only ".english.sabs") — CONFIRMED boot-2 hang: the dump shows
    the loader stuck opening /vol/aoc.../sound/zmb_nuked_real_intro.all.sabs.
    Dropping matches genuine ZM and removes both faults. Paired with drops={} in
    assemble_zone (the body is skipped there); this only removes the array row."""
    rows = []
    for i, (t, nm, hp) in enumerate(rp.assets):
        if nm == 'SOUND':
            continue                                    # drop the map SndBank (see docstring)
        if nm == 'GLASSES' and hp == FOLLOW:
            rows.append((47, 'MAP_ENTS', 'relabel-mapents', i))
        else:
            ct = ALA.pc_to_console_type(t, nm)
            rows.append((ct, nm, 'follow', i))
        if i == 0:
            for k in range(3):
                rows.append((48, 'GLASSES', 'alias-glasses-zm%d' % k, None))
    return rows


def _make_pc_image_source(ipak_paths):
    """Resolver `callable(name_hash) -> iwi dict|None` over the map's PC ipak(s),
    for streamed/resident image pixels (GfxWorld tail lut + materialMemory images,
    standalone streamed material images). Falls back to a RAW blob for entries that
    aren't standard IWI (the resident lut is stored as raw pixels) — the consumer
    (gfxworld_gx2.conv_tail_material) then takes dims from the console img_body."""
    import ipak as _IP
    paths = [p for p in ipak_paths if p and os.path.exists(p)]
    if not paths:
        return None
    src = _IP.PcImageSource(paths)

    def resolve(nh):
        # per-entry parse: one non-IWI match (e.g. a console-format artifact ipak in the
        # source list) must not poison a valid PC IWI hit from another pak. find_pc_source
        # parses ALL by_name matches in one try — skate's 263 matmem colorMaps that exist
        # in both dlc1.ipak (IWI) and the console artifact (GX2-tiled) resolved RAW and
        # were silently stubbed past the scoped resident branch (found 2026-07-13).
        parts = []
        for pak, en in src.by_name.get(nh, []):
            try:
                blob = pak.extract(en, verify=True)
                iwi = _IP.parse_iwi(blob)
                if iwi and iwi.get('mips') and 'gx2_format' in iwi:
                    parts.append((en.part_index, iwi, blob))
            except Exception:
                continue
        if parts:
            parts.sort(key=lambda t: t[0])
            iwi = dict(parts[0][1]); iwi['blob'] = parts[0][2]
            return iwi
        ents = src.by_name.get(nh)          # raw-pixel fallback (no IWI header)
        if ents:
            pak, en = ents[0]
            return {'blob': pak.extract(en)}
        return None
    return resolve


def _make_resident_test(stream_ipak_paths):
    """Build `callable(name_hash) -> bool` (True == resident == emit INLINE) for the A1
    XModel-inline image discriminator. Resident iff the image is NOT present in the map's
    console streaming ipak(s) — genuine ships resident images inline and streams the rest
    (ipak membership is the signal, NOT mapType/semantic; verified raid 2026-07-12).
    name_hash is platform-independent, so the console ipak's name_hash set is directly
    comparable to the PC image's hash. None if no ipak available -> caller keeps legacy
    resident=True (over-inline)."""
    import ipak as _IP
    paths = [p for p in stream_ipak_paths if p and os.path.exists(p)]
    if not paths:
        return None
    streamed = set()
    for p in paths:
        pak = _IP.IPak(open(p, 'rb').read())
        streamed.update(en.name_hash for en in pak.entries)
    return lambda nh: nh not in streamed


# genuine Wii U content dir. The linker-side streamed/resident discriminator is membership
# in the ENTIRE console ipak universe (all 120 content/*.ipak), not just the map's mounted
# set: measured raid 2026-07-12, genuine-streamed images are 832/832 present in the union
# while genuine-inline images are 0/51 — including images that raid itself cannot stream
# (present only in other maps' ipaks, still emitted as streamed bodies).
CONSOLE_CONTENT_DIR = (r'C:\Users\Tony - Main Rig\Downloads'
                       r'\Wii U Call of Duty Black Ops 2 USA WUP'
                       r'\Wii U Call of Duty Black Ops 2 USA WUP\content')
_CONSOLE_UNIVERSE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       '_console_ipak_universe.pkl')

# inline-scoping fix (skate heap stomp): stream EVERY sourceable matmem colorMap
# instead of inlining the "resident" subset; caller must merge MC.COLLECT_ENTRIES
# into the map ipak. See author_zone's MATMEM_RESIDENT_TEST wiring.
MATMEM_STREAM_ALL = False


def _boot_safe_same_type_handle(omap, ct):
    """QUEUE ITEM 1: a boot-safe runtime handle for an aliased asset row whose
    PC handle can't be structurally resolved (aliases into the GfxWorld
    interior). Returns a handle to the FIRST real emitted body of the SAME
    asset type in this zone (build-correct: valid struct + valid name +
    mapped). Cached on omap. Falls back to FOLLOW if no same-type body exists
    (should not happen for the observed techset case)."""
    import wiiu_zone as WZ
    import walker as W
    cache = getattr(omap, '_bootsafe_body', None)
    if cache is None:
        cache = {}
        st = omap.cur_stream
        for (si, snm, sroot, ss, se) in omap.rt_spans:
            if se > ss and sroot and sroot not in cache:
                # ss is a stream offset into the 64-prefixed pass-3 stream;
                # cur_stream has no prefix so co_b5 = ss - 64 (the _encode
                # domain). Only take bodies with an inline header (FOLLOW@0)
                # so the loader can read a real name.
                if struct.unpack_from('>I', st, ss - 64)[0] == FOLLOW:
                    cache[sroot] = omap._encode(ss - 64)
        omap._bootsafe_body = cache
    pc = WZ.console_to_pc(ct)
    tn = (WZ.PC_ASSET_TYPES[pc]
          if pc is not None and 0 <= pc < len(WZ.PC_ASSET_TYPES) else None)
    root = W.ASSET_ROOT.get(tn)
    return cache.get(root, FOLLOW)


def console_image_universe():
    """name_hash set over ALL console content/*.ipak (cached; None if dir absent).

    2026-07-14: the Downloads game-dump dir went missing mid-project; a silent
    None here flips the resident/streamed discriminator and changes every
    inline-image decision (~1.45MB layout shift on skate = stale measured
    anchors = pointer regression). Fall back to the CACHED universe (the exact
    set every measured build used) instead of None when the dir is gone."""
    import glob, pickle
    paths = sorted(glob.glob(os.path.join(CONSOLE_CONTENT_DIR, '*.ipak')))
    if not paths:
        if os.path.exists(_CONSOLE_UNIVERSE_CACHE):
            try:
                n, univ = pickle.load(open(_CONSOLE_UNIVERSE_CACHE, 'rb'))
                print('console_image_universe: content dir MISSING -> using '
                      'cached universe (n=%d, %d hashes)' % (n, len(univ)))
                return univ
            except Exception:
                pass
        return None
    if os.path.exists(_CONSOLE_UNIVERSE_CACHE):
        try:
            n, univ = pickle.load(open(_CONSOLE_UNIVERSE_CACHE, 'rb'))
            if n == len(paths):
                return univ
        except Exception:
            pass
    import ipak as _IP
    univ = set()
    for p in paths:
        try:
            pak = _IP.IPak(open(p, 'rb').read())
        except Exception:
            continue
        univ.update(en.name_hash for en in pak.entries)
    pickle.dump((len(paths), univ), open(_CONSOLE_UNIVERSE_CACHE, 'wb'))
    return univ


def make_console_resident_test():
    """RESIDENT_IMAGE_TEST over the console ipak universe (None if unavailable)."""
    univ = console_image_universe()
    if univ is None:
        return None
    return lambda nh: nh not in univ


def _walk_sndbank_aliases(d, b, e):
    """Mirror sndbank_probe.parse_sndbank's string consumption, returning per-alias
    (name_word, assetId_word) in emit order + the aliasIndex bytes + end offset.
    `e` is the struct endianness ('>' genuine console, '<' PC). Self-checks against
    parse_sndbank's end via the caller."""
    import sndbank_probe as S
    u32 = lambda o: struct.unpack_from(e + 'I', d, o)[0]
    name_p, ac, alias_p, ai_p, rc, rp, dc, dp = struct.unpack_from(e + '8I', d, b)
    o = b + S.BODY
    aliases = []
    if name_p in S.PTRS:
        o = d.index(b'\x00', o) + 1
    idx = None
    if alias_p in S.PTRS:
        base = o; o += ac * S.ALIASLIST
        for i in range(ac):
            lb = base + i * S.ALIASLIST
            ln, lid, hp, cnt, sq = struct.unpack_from(e + '5I', d, lb)
            if ln in S.PTRS:
                o = d.index(b'\x00', o) + 1
            if hp in S.PTRS:
                ab = o; o += cnt * S.ALIAS
                for k in range(cnt):
                    a = ab + k * S.ALIAS
                    aliases.append((u32(a + 0), u32(a + 16)))
                    for po in (a + 0, a + 8, a + 12, a + 20):
                        if u32(po) in S.PTRS:
                            o = d.index(b'\x00', o) + 1
    if ai_p in S.PTRS:
        idx = d[o:o + ac * 4]; o += ac * 4
    return aliases, idx, o


# genuine console zones that carry a map's real SndBank (for the alias/aliasIndex oracle).
_SNDBANK_ORACLE_ZONE = {
    'mp_raid': os.path.join('..', 'wiiu_ref', 'mp_raid_genuine.zone'),
}


def _make_sndbank_overlay(map_name):
    """For a map with a genuine console reference, return {bank_name: genuine_main_body} for
    smalls_convert.SNDBANK_MAIN_OVERLAY. The console main bank inlines list-name/assetFileName
    strings and custom-hash id fields the PC bank lacks (a field-aware convert is ~102KB short
    with the wrong layout -> +0x3817ce); those aren't derivable from PC, so emit the genuine
    body verbatim. The main .all bank starts at the end of the english bank. Returns None if
    no genuine reference (skate etc. -> a proper fix needs the console hash algo + strings)."""
    zp = _SNDBANK_ORACLE_ZONE.get(map_name)
    if not zp:
        return None
    zp = zp if os.path.isabs(zp) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), zp)
    if not os.path.exists(zp):
        return None
    import sndbank_probe as S
    d = open(zp, 'rb').read()
    eng_off = 0x45bea9e                       # genuine raid english bank
    main_off = S.parse_sndbank(d, eng_off, '>')[0]   # main .all bank == english end
    main_end, name, ac, _st = S.parse_sndbank(d, main_off, '>')
    return {name: bytes(d[main_off:main_end])}


def _make_material_overlay(map_name):
    """For a map with a genuine console reference, return {material_name: genuine_body} for
    the TOP-LEVEL Material assets (raid: compass_map_<map>[_scrambled], the minimap display
    layer). Genuine ships a big RESIDENT inline image our converter drops (256KB) -> the engine
    loops streaming it -> black-screen hang. Those pixels aren't in base/mp so emit the genuine
    body verbatim. Returns None if no genuine reference (skate needs the real compass pixels)."""
    if map_name not in _SNDBANK_ORACLE_ZONE:
        return None
    import loader_sim as LS, raid_oracle_control as RC
    em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
    ov = {}
    for (i, nm, root, s, e) in gsp:
        if root != 'Material' or e - s < 200:
            continue
        body = CO[s:e]
        try:
            end = body.index(b'\x00', 104)             # console material name string @+104
        except ValueError:
            continue
        name = body[104:end].decode('latin-1', 'replace')
        if name:
            ov[name] = body
    return ov or None


def _make_smalls_overlays(map_name):
    """Track E raid control: genuine-body overlays for the remaining resident-image
    droppers — standalone GfxImage (48KB inline pixels) and GfxLightDef (8KB cookie),
    same pattern as _make_material_overlay. Returns (image_ov, lightdef_ov):
    image_ov = {name: genuine_body} (GfxImage inline name chars @328, after the GX2
    body); lightdef_ov = LIST of genuine bodies (the genuine lightdef name is an
    ALIAS, so substitution is positional — applied only when unambiguous, len==1)."""
    if map_name not in _SNDBANK_ORACLE_ZONE:
        return None, None
    import loader_sim as LS, raid_oracle_control as RC
    em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
    img_ov, ld_ov = {}, []
    for (i, nm, root, s, e) in gsp:
        body = CO[s:e]
        if root == 'GfxImage' and e - s > 328:
            try:
                end = body.index(b'\x00', 328)
            except ValueError:
                continue
            name = body[328:end].decode('latin-1', 'replace')
            if name:
                img_ov[name] = body
        elif root == 'GfxLightDef' and e - s > 16:
            ld_ov.append(body)
    return (img_ov or None), (ld_ov or None)


def _make_glasses_ptr_overlay(map_name):
    """Genuine intra-asset pointer transplant for Glasses (see
    smalls_convert.GLASSES_PTR_OVERLAY): body word @12 + the shared glassDef alias
    (glass 1 @16; glasses 1..N-1 all carry the same value). None without a genuine ref."""
    if map_name not in _SNDBANK_ORACLE_ZONE:
        return None
    import struct as _st
    import loader_sim as LS, raid_oracle_control as RC
    em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
    for (i, nm, root, s, e) in gsp:
        if root != 'Glasses' or e <= s:        # skip ALIASED rows (empty body)
            continue
        body = CO[s:e]
        w12 = _st.unpack_from('>I', body, 12)[0]
        num = _st.unpack_from('>I', body, 4)[0]
        gbase = 56
        if _st.unpack_from('>I', body, 0)[0] >= 0xFFFFFFFE:   # inline name
            gbase = body.index(b'\x00', 56) + 1
        gd = None
        for g in range(1, num):
            v = _st.unpack_from('>I', body, gbase + g * 140 + 16)[0]
            if v < 0xFFFFFFFE:
                gd = v
                break
        if gd is not None:
            return {'w12': w12, 'glassdef': gd}
    return None


def author_zone(pc_path, map_name, verbose=True, pc_policy=None,
                our_policy=None, override_rtmap=None, image_ipak=None,
                stream_ipak=None, mode='mp'):
    # ITEM 7: apply this map's per-map config (matmem/sndbank/vshader-delta
    # globals) from the registry by name. No-op for unregistered maps. Callers
    # may still override any global afterward (e.g. the _rebake_* scripts).
    try:
        import map_config
        map_config.apply(map_name, verbose=verbose)
    except Exception:
        pass
    PC = open(pc_path, 'rb').read()
    rp = pc_zone.PCZoneReader(PC); rp.read_string_table(); rp.read_asset_list()

    # A1: XModel-inline-material image source over the PC SOURCE ipaks (pixels for
    # skybox_<map> and the other inline-pixel droppers). SEPARATE from GFXWORLD_IMAGE_SOURCE
    # and the global MC.IMAGE_SOURCE; consulted ONLY while MC.XMODEL_INLINE_ACTIVE (set by
    # parse_xmodel_pc around its inline-material convert) so the GfxWorld materialMemory
    # path is untouched (avoids the 16,734 unres:GfxWorld a global source causes).
    import material_convert as _MCX, ipak_stream as _ISM
    _pc_src = [p for p in _ISM.DEFAULT_PC_IPAKS]
    if mode == 'zm':
        # ZM PC source universe: base + zm (mp.ipak is the MP streaming set)
        _pc_src = [_ISM.DEFAULT_PC_IPAKS[0],
                   os.path.join(os.path.dirname(_ISM.DEFAULT_PC_IPAKS[0]), 'zm.ipak')]
    if image_ipak:
        _pc_src += (image_ipak if isinstance(image_ipak, (list, tuple)) else [image_ipak])
    _MCX.XMODEL_IMAGE_SOURCE = _make_pc_image_source(_pc_src)
    # emitter #3 (GfxWorld materialMemory inline materials): same PC-source resolver, consulted
    # only while MC.MATMEM_INLINE_ACTIVE (conv_material_memory brackets it) -> resident colorMaps
    # inline, streamed ones get real bodies, instead of the null-inducing 1x1 stub. Separate from
    # the global IMAGE_SOURCE (which corrupts this path) and from GFXWORLD_IMAGE_SOURCE (tail-lut).
    _MCX.MATMEM_IMAGE_SOURCE = _make_pc_image_source(_pc_src)
    # matmem-scoped resident calibration (2026-07-13): genuine inlines only a SMALL resident
    # subset (raid: 6 img_pix) and streams the rest. For maps with an authored/deployed ipak
    # (skate), images present there ARE streamable at runtime -> stream them; matmem-resident
    # only if absent from BOTH the console universe and the map's own ipak(s). MATMEM-local;
    # the global RESIDENT_IMAGE_TEST (Track A XModel calibration) is untouched.
    _mm_stream = set()
    for _p in ([] if image_ipak is None else
               (image_ipak if isinstance(image_ipak, (list, tuple)) else [image_ipak])):
        if _p and os.path.exists(_p):
            import ipak as _IPk
            _mm_stream.update(en.name_hash for en in _IPk.IPak(open(_p, 'rb').read()).entries)
    _mm_uni = console_image_universe() or set()
    if MATMEM_STREAM_ALL:
        # inline-scoping fix (2026-07-13, skate heap stomp): resident matmem
        # colorMaps emitted INLINE consume runtime image-arena memory the
        # measured layout never reserved (~11.6MB of pixel copies -> the
        # 0x101xxxxx-0x10ef1400 stomp over engine objects). For maps whose
        # ipak WE author, stream EVERY sourceable matmem colorMap and carry
        # the payloads in the map ipak (MC.COLLECT_ENTRIES gathers them for
        # the merge). Unsourceable images still fall to the stub path.
        _MCX.MATMEM_RESIDENT_TEST = lambda nh: False
    else:
        _MCX.MATMEM_RESIDENT_TEST = (
            lambda nh, _u=_mm_uni, _s=_mm_stream: nh not in _u and nh not in _s)
    # Track C (FxEffectDef inline materials): same PC-source resolver, consulted only
    # while MC.FX_INLINE_ACTIVE (fx_convert brackets its convert_material calls) ->
    # resident FX colorMaps emit their genuine inline pixel chains (raid: 21 images,
    # ~1.3MB the converter previously dropped); non-colorMaps stream; PC alias stubs
    # emit the genuine zeroed body. See material_convert._convert_image_fx.
    _MCX.FX_IMAGE_SOURCE = _make_pc_image_source(_pc_src)

    # A1 discriminator (measured raid 2026-07-12): the streaming ipak set IS the map's
    # PC source ipaks (base+mp[+image_ipak]) — 2643/2670 XModel-inline images resolve from
    # them (360 MB if all inlined); genuine STREAMS exactly those. Resident images (genuinely
    # inline, e.g. skybox_mp_raid) are ABSENT from base/mp and ship their pixels via the PC
    # zone (branch 1, `if pixels:`), never reaching this branch. So: resident iff NOT in the
    # source set -> anything the A1 branch catches (resolved from source) STREAMS, not inlines.
    # name_hash is platform-independent, so PC-ipak membership == console-ipak membership.
    # CORRECTION (raid 2026-07-12): membership must be tested against the CONSOLE ipak
    # UNIVERSE (all content/*.ipak) — not the PC set, and not even just the map's mounted
    # set. Genuine-streamed images are 832/832 in the union (some only in OTHER maps'
    # ipaks) while genuine-inline are 0/51. PC mp.ipak has no console counterpart, so
    # most of its images are console-resident and must emit inline (the −3.4MB A1 gap).
    if stream_ipak is not None:
        _stream_src = (stream_ipak if isinstance(stream_ipak, (list, tuple))
                       else [stream_ipak])
        _MCX.RESIDENT_IMAGE_TEST = _make_resident_test(_stream_src)
    else:
        # AUTHORED-map correction (2026-07-13): the map's OWN ipak (image_ipak, e.g. the
        # deployed mp_skate.ipak) is runtime-mounted and therefore streamABLE, but it is not
        # in the console-universe dir -> without this, every DLC texture tests resident and
        # inlines (~93MB on skate). Union it into the universe. NO-OP for stock maps (their
        # own ipak is already in the universe: raid verified).
        _uni = console_image_universe()
        if _uni is not None:
            _MCX.RESIDENT_IMAGE_TEST = (
                lambda nh, _u=_uni, _s=_mm_stream: nh not in _u and nh not in _s)
        else:
            _MCX.RESIDENT_IMAGE_TEST = _make_resident_test(_pc_src)

    # image source for the GfxWorld tail-lut resident image (CAVEATS_gfxworld_trackF.md
    # item 4). Set the DEDICATED GfxWorld hook (raw-fallback resolver) — NOT
    # material_convert.IMAGE_SOURCE, whose raw blobs would corrupt the materialMemory
    # inline-image path (latent _console_material_pieces overrun). Without it the lut
    # stubs and the GfxWorld stream is ~262KB short. (Fixed 2026-07-12: skate GfxWorld
    # now 22.89MB with the lut resident; needed the include_techset parse fix in
    # gfxworld_gx2/gfxworld_regions so the injected inline techset doesn't hide the img_body.)
    if image_ipak is not None:
        PN.GFXWORLD_IMAGE_SOURCE = _make_pc_image_source(
            image_ipak if isinstance(image_ipak, (list, tuple)) else [image_ipak])
        # the GfxWorld emit is memoized; drop any entry cached earlier (e.g. during
        # derive_pc_policy) with the resolver still unset, or the lut stays stubbed.
        PN._GFX_EMIT_CACHE.clear(); PN._GFX_PAIR_CACHE.clear()

    ptrs, str_body = _pc_string_region(PC, rp)
    # asset array follows the raw string bytes with NO alignment pad (verified
    # across 5 genuine console zones incl. unaligned dockside/la/village).
    prefix = len(ptrs) * 4 + len(str_body)
    PN.BOOT_SAFE_UNRESOLVED = (mode == 'zm')     # blind ZM: in-bounds mirror, not poison tag
    if mode == 'zm':
        SC.SKINNEDVERTS_MAX = 0x20000            # ZM skinned-vert pool = 131072
        rows = author_rows_zm(rp)
        narr = len(rows)
        # +3 for the GLASSES alias rows at console idx 1..3; the SOUND row is
        # DROPPED (author_rows_zm skips it + drops={} skips its body), so every
        # console index AFTER the SOUND asset shifts down by 1.
        pc_sound = next((k for k, (t, nm, hp) in enumerate(rp.assets)
                         if nm == 'SOUND'), None)
        idx_remap = (lambda i, ps=pc_sound:
                     i + (3 if i >= 1 else 0) - (1 if (ps is not None and i > ps) else 0))
        inserts = None
        drops = {pc_sound} if pc_sound is not None else None
    else:
        rows = author_rows(rp)
        narr = len(rows)

        # PC->console array index remap (slot-handle relocation): +1 for the
        # GLASSES alias at console idx 1, +2 once at/after the SOUND row (the
        # english insert precedes the main bank).
        pc_sound = next(i for i, (t, nm, hp) in enumerate(rp.assets)
                        if nm == 'SOUND')
        idx_remap = lambda i: i + (1 if i >= 1 else 0) + (2 - 1 if i >= pc_sound else 0)

        # console-only body: the localized bank, inserted BEFORE the main SOUND
        # body == after the previous PC asset's body
        eng = SC.author_english_bank(map_name)
        inserts = {pc_sound - 1: ('SOUND', 'SndBank', eng)}
        drops = None

    # RAID main-bank alias oracle: the console recomputes SndAlias.name@+0 / assetId@+16
    # with a custom (uncrackable-offline) string hash and rebuilds the aliasIndex hash
    # table; our PC-derived values leave the engine's alias/voice list linking a garbage
    # pointer -> the AX HLE callback faults relinking node->next (+0x360/+0x364, disasm
    # 2026-07-12). For raid we have the genuine .sab's values -> transplant them positionally
    # (emit order == genuine order, proven by list-id sequence). Skate has no genuine ref.
    SC.SNDBANK_MAIN_OVERLAY = _make_sndbank_overlay(map_name)
    _MCX.MATERIAL_BODY_OVERLAY = _make_material_overlay(map_name)
    _MCX.IMAGE_BODY_OVERLAY, SC.LIGHTDEF_BODY_OVERLAY = _make_smalls_overlays(map_name)
    SC.GLASSES_PTR_OVERLAY = _make_glasses_ptr_overlay(map_name)
    # ITEM 6 / BOOT-41 (2026-07-20: promoted from opt-in to pipeline DEFAULT).
    # FxElemDef.flags & 0x8000 gates RB_AllocOcclusionQuery (disasm-proven at
    # FX_SpawnElem+0x4a0). Cemu's GX2 occlusion-query emulation stops retiring at the
    # first query command buffer, so the end-frame fence never completes and
    # FX_WaitForFXDrawWorkers spins forever: the map freezes on the round countdown with
    # a healthy 60 fps frame loop and no fault. Boot-42 PROVED clearing the bit fixes it
    # (0 GX2QueryBegin, map ran 1.94M log lines vs 852K).
    # On skate exactly ONE elem carries it (zone_off 135890, flags 0x00018082) — still
    # present in the current by-construction build, which is why the countdown hang came
    # back once the earlier one-off gfxtail34 patch fell out of the line.
    # ⚠ This is a CEMU WORKAROUND and is WRONG on real hardware (sprites stop fading by
    # occlusion visibility). Cemu is the only validated target, so it defaults ON;
    # set T6_REAL_HW=1 to build for console.
    # MUST be set BEFORE assemble_zone: fx_convert reads it during the body emit.
    import fx_convert as _FXC
    _FXC.CEMU_COMPAT_OCCLUSION_STRIP = (os.environ.get('T6_REAL_HW') != '1')
    if verbose:
        print('fx occlusion-query strip: %s'
              % ('ON (Cemu default)' if _FXC.CEMU_COMPAT_OCCLUSION_STRIP
                 else 'OFF (T6_REAL_HW=1)'))
    try:

        stat, out_assets, omap = PN.assemble_zone(
            pc_path, verbose=verbose, pc_policy=pc_policy, our_policy=our_policy,
            container_prefix=prefix, container_narr=narr,
            inserts=inserts, idx_remap=idx_remap, override_rtmap=override_rtmap,
            drops=drops)
    finally:
        SC.SNDBANK_MAIN_OVERLAY = None
        _MCX.MATERIAL_BODY_OVERLAY = None
        _MCX.IMAGE_BODY_OVERLAY = None
        SC.LIGHTDEF_BODY_OVERLAY = None
        SC.GLASSES_PTR_OVERLAY = None

    # ---- asset array: hp column ----
    pc_hp = {i: hp for i, (t, nm, hp) in enumerate(rp.assets)}
    hp_rows = []                                   # (row_idx, pc_idx, value)
    arr = bytearray()
    for ri, (ct, nm, kind, pi) in enumerate(rows):
        arr += struct.pack('>I', ct)
        if kind == 'alias-glasses':
            arr += struct.pack('>I', GLASSES_ALIAS_HP)
        elif kind.startswith('alias-glasses-zm'):
            arr += struct.pack('>I', GLASSES_ALIAS_HP_ZM[int(kind[-1])])
        elif pi is not None and pc_hp[pi] != FOLLOW:
            # aliased row: hp = the asset's first INLINE occurrence (mid-body
            # header). omap.reloc maps it to the same structural position in
            # our stream. PC targets inside SUBSTITUTED techset blobs take the
            # boot-safe in-bounds ts-dangle mirror (genuine ships equivalent
            # dangles; a poison tag would be a wild out-of-block pointer) —
            # enabled by claiming an allowed source family in ctx.
            omap.ctx = (-1, nm, 'Material', 0)
            v = omap.reloc(pc_hp[pi])              # PC alias -> our runtime addr
            omap.ctx = None
            # QUEUE ITEM 1 (pipecheck boot crashes, 2026-07-18): an aliased row
            # whose PC handle aliases into the GfxWorld interior can't be
            # structurally resolved, so reloc returns a poison tag (0xBF00_00nn
            # -> ~0.5 GB out-of-block) -> load-deref host crash (skate row 803,
            # an unbound/bodyless MaterialTechniqueSet). BUILD-CORRECT GENERAL
            # FIX: point the handle at a REAL emitted body of the SAME asset
            # type IN THIS ZONE. Two dead ends proved why nothing else works:
            #   - block-5 self-slot -> the loader reads the array bytes AS the
            #     asset struct -> NULL name ptr -> Com_HashString crash;
            #   - a hardcoded block-7 constant (gfxtail2's 0xf06d1815) -> that
            #     offset is unmapped in a DIFFERENTLY-laid-out build -> crash.
            # A real same-type body is valid struct + valid name + mapped, and
            # is derived from OUR layout so it is correct for every map. The row
            # is unbound so it collapses harmlessly in the name pool.
            if 0xBF000000 <= v < 0xC0000000:
                v = _boot_safe_same_type_handle(omap, ct)
                if verbose:
                    print('  item1: aliased row %d (%s) type=%d hp -> GfxWorld '
                          'poison; same-type body handle 0x%08x' % (ri, nm, ct, v))
            hp_rows.append((ri, pi, v))
            arr += struct.pack('>I', v)
        else:
            arr += struct.pack('>I', FOLLOW)

    # ---- serialize ----
    body_stream = omap.cur_stream                  # emitted console bodies
                                                   # (cur_stream has NO 64-B prefix)
    xlist = struct.pack('>6I', rp.string_count, FOLLOW, 0, 0, narr, FOLLOW)
    strp = b''.join(struct.pack('>I', FOLLOW if p == FOLLOW else 0)
                    for p in ptrs)
    content = bytearray()
    content += xlist + strp + str_body
    content += arr
    assert len(content) == 24 + prefix + narr * 8
    content += body_stream

    # block-5 must cover the ACTUAL runtime layout. With a dump-measured
    # override_rtmap the pointers use measured offsets that exceed the sim's
    # (under-counting) block size — size block-5 to the measured max END or a
    # late pointer lands out-of-block and resolves to null (host-null crash).
    b5 = omap.block_size[5]
    if override_rtmap is not None and getattr(override_rtmap, 'max_rt', 0):
        b5 = max(b5, override_rtmap.max_rt)
    # INVARIANT (measured on every genuine zone): block5 > total content by a
    # runtime headroom band (dockside +0.44MB, raid +0.78MB, transit +1.09MB —
    # the DPVS/runtime reservation). The sim's block_size[5] can under-count to
    # BELOW the content size (zm_nuked: sim 194,068,518 < content 194,243,499 =
    # a −0.17MB block5 → the loader overflows block5 during load → host crash,
    # no PPC exception). Floor block5 at content + a generous band so it always
    # satisfies the invariant. "Oversize is safe"; undersize is fatal.
    b5 = max(b5 + SAFETY_B5, len(content) + RUNTIME_BAND)
    if mode == 'zm':
        blocks = [BLOCK0_ZM, BLOCK1_ZM, BLOCK2_ZM, 0, 0, b5, 0, 0]
    else:
        blocks = [BLOCK0_MP, BLOCK1_MP, BLOCK2_MP, 0, 0, b5, 0, 0]
    # size field = len(zone) - 40 = len(content); externalSize = 0 on console
    header = struct.pack('>II', len(content), 0) + struct.pack('>8I', *blocks)
    zone = header + bytes(content)

    # R2 (2026-07-19): family-9 techset name-rebind. Inline GfxWorld materials
    # convert during the CACHED GfxWorld emit with the identity reloc, so their
    # techSet@80 handles stay RAW PC aliases and bind the wrong console asset-
    # array slot (console-only inserts + family-9 name-pool reorder). Rebind each
    # to the slot whose techset has the intended NAME (boot-44/gfxtail37 method,
    # dump-free). Size-neutral + clipMap-gated + fail-safe (see techset_rebind).
    import techset_rebind
    zone = techset_rebind.rebind_matmem_techsets(zone, PC, map_name, verbose=verbose)

    # R1 (2026-07-19): same identity-reloc root — matmem inline-material texdef
    # colorMap image handles stay RAW PC aliases (broken; point into PC GfxWorld/
    # XModel interiors) → garbage GfxImage* → draw-time crash (dump 30004). Repair
    # each to a boot-safe in-zone GfxImage handle (the answer key itself resolves
    # ~78% to one neutral fallback — there is no genuine per-image resolution).
    import colormap_rebind
    # IPAK Half A (intent recovery) — OPT-IN via SKATE_IPAK_COLORMAPS=1 until validated.
    # pc=None  -> every matmem colorMap points at ONE boot-safe in-zone image (R1 placeholder).
    # pc=PC    -> dump-free per-image INTENT RECOVERY (colormap_intent): ~630 real intents
    #             recovered, rest fall back to the placeholder. SIZE-NEUTRAL repoint only
    #             (colormap_rebind guards len(zone) and the clipMap gate) -> the frozen
    #             _skate6_* measured map stays valid. Half B (pull from dlc1.ipak, GX2-convert,
    #             dedup ~2810->~334, EMBED) is NOT implemented and WOULD grow the zone.
    _ipak = os.environ.get('SKATE_IPAK_COLORMAPS') == '1'
    zone = colormap_rebind.rebind_matmem_colormaps(
        zone, omap, map_name, verbose=verbose,
        pc=(PC if (_ipak and map_name == 'mp_skate') else None),
        assets_end=64 + prefix + narr * 8)

    # FIX 3 (2026-07-20, WIRE-IN): ROOT NAME resolution. clipMap_t.name*, GfxWorld.name*
    # and GfxWorld.baseName* all b5-dedup-alias ComWorld's single inline copy of
    # "maps/mp/<map>.d3dbsp". The converter relocates the PC alias through a PC runtime
    # model that is short at that depth (omap.pc_name -> None), so it lands on a WRONG
    # TARGET: the loader cannot find the bsp -> "BSP missing for map" -> bounce to lobby.
    # NO runtime-map work can close this (proven: both _skate2_ and _skate6_ already map
    # the true string offset to the correct payload) -- the SOURCE was wrong, not the map.
    # root_name_fix.py had been written and validated but was NEVER CALLED from anywhere,
    # so every pure by-construction build shipped the defect; only oracle-reconciled builds
    # masked it by copying the key's word. Size-neutral (value-only) => frozen measured
    # layouts stay valid, no INLINE_ASSET_NAMES, no re-measure.
    # Body file offsets: bodies emit CONTIGUOUSLY from assets_end (verified 0 gaps across
    # 837 spans), so a running sum over out_assets in emit order is exact.
    import root_name_fix
    _ae = 64 + prefix + narr * 8
    _sites = {'name': [], 'basename': []}
    _gwmp_body = None
    _glasses_body = None
    _glasses_end = None
    _fx_bodies = []                                 # for FIX 6 (visuals), below
    _cur = _ae
    for (_i, _nm, _root, _body, _why) in out_assets:
        if _body is None:
            continue
        if _root == 'clipMap_t':
            _sites['name'].append(_cur)             # clipMap_t.name   = body+0
        elif _root == 'GfxWorld':
            _sites['name'].append(_cur)             # GfxWorld.name    = body+0
            _sites['basename'].append(_cur + 4)     # GfxWorld.baseName= body+4
        elif _root == 'GameWorldMp':
            _gwmp_body = _cur                       # for FIX 4 (nodeTree), below
        elif _root == 'Glasses':
            _glasses_body = _cur                    # for FIX 5 (glassDef), below
            _glasses_end = _cur + len(_body)        # bounds FIX 5's inline-name scan
        elif _root == 'FxEffectDef':
            # FX is NOT a singleton (79 bodies on skate) -> collect a LIST.
            _fx_bodies.append((_cur, len(_body)))
        _cur += len(_body)
    if _sites['name']:
        _zb = bytearray(zone)
        root_name_fix.resolve_root_names(_zb, map_name, omap.rtmap.rt, _ae,
                                         _sites, verbose=verbose)
        assert len(_zb) == len(zone), 'root_name_fix changed zone size'
        zone = bytes(_zb)
    elif verbose:
        print('root_name_fix: no clipMap_t/GfxWorld body — skipped')

    # FIX 4 (2026-07-20): GameWorldMp pathnode nodeTree child targets emit ONE NODE (16 B)
    # LOW, because _gwmp_tree_node resolves both child pointers through the generic reloc.
    # The second child of a split node IS the node itself under that error => 187 SELF-LOOPS
    # => Path_NodesInCylinder_r recurses forever => the server never finishes G_InitGame and
    # the client hangs at "awaiting challenge" in an infinite loading loop (boot-proven).
    # Detection and correction are ORACLE-FREE: in a DFS-serialized pathnode_tree_t array the
    # second child is always the immediate DFS follower (target == self+1 record), an
    # invariant that holds in genuine console output AND in the PC source.
    # ⚠ Do NOT instead rebase the interior-anchor carry: 1,200,527 is CORRECT (the answer key
    # decodes to a perfect tree under it, and to 187 self-loops under carry+16).
    import smalls_convert as _sc
    _tree = getattr(_sc, 'LAST_GWMP_TREE', None)
    if _gwmp_body is not None and _tree:
        import gwmp_tree_fix
        _zb = bytearray(zone)
        gwmp_tree_fix.fix_gwmp_nodetree(_zb, _gwmp_body + _tree[0], _tree[1],
                                        omap.rtmap.rt, _ae, verbose=verbose)
        assert len(_zb) == len(zone), 'gwmp_tree_fix changed zone size'
        zone = bytes(_zb)
    elif verbose:
        print('gwmp_tree_fix: no GameWorldMp nodeTree — skipped')

    # FIX 5 (2026-07-20): Glasses.glasses[i].glassDef targets the wrong offset.
    # The field must point at the START of the single inline GlassDef record, which WE
    # emit, so it is computable from our own output: gd = gbase + num*140, where
    # gbase = 56 + len(inline asset name)+1. The generic reloc routes this PC dedup alias
    # through pc_inv.stream(), a per-ASSET piecewise-linear inverse; the PC layout INSIDE
    # the asset is not linear, so it lands one sub-record early — on skate 19 B, exactly
    # len("glass_dec_clear_mp")+1, the GlassDef's own inline name. Nothing in the reloc
    # chain can see that the result must be a record start.
    # Consequence: R_AddSceneEntSurfs_SceneGlassBrush reads GlassDef scalars + the
    # pristine/cracked/shard Material pointers out of float payload -> the jq batch fn
    # returns nonzero -> REQUEUE forever -> frame never completes -> GPU never retires
    # (boot-proven livelock; reconciling these words cleared it).
    # ORACLE-FREE: confirmed on GENUINE raid (num=43, gbase=70, glass 0 owns the inline
    # GlassDef, gbase+43*140 lands on the same FF FF FF FF GlassDef header).
    # ⚠ GATED on GLASSES_PTR_OVERLAY: for raid we transplant the genuine glassDef word,
    # and loader_sim's rt is 3363 LOW there — running this fix would overwrite a genuine
    # value and break the raid byte-oracle. It applies only to the blind-map case.
    if _glasses_body is not None and SC.GLASSES_PTR_OVERLAY is None:
        import glassdef_alias_fix
        _zb = bytearray(zone)
        glassdef_alias_fix.fix_glassdef_aliases(_zb, _glasses_body, omap.rtmap.rt,
                                                _ae, verbose=verbose,
                                                span_end=_glasses_end)
        assert len(_zb) == len(zone), 'glassdef_alias_fix changed zone size'
        zone = bytes(_zb)
    elif verbose:
        print('glassdef_alias_fix: %s'
              % ('no Glasses body — skipped' if _glasses_body is None
                 else 'GLASSES_PTR_OVERLAY set (genuine transplant) — skipped'))

    # FIX 6 (2026-07-20): FxElemDef.visuals (+196) dedup back-references.
    # fx_convert routes them through the generic omap.reloc, which falls to the
    # COARSE per-asset linear branch (FxEffectDef is in neither P2C.SIMPLE nor
    # P2C.WORLD, so it registers exact=False). An FX body is NOT size-preserving
    # PC->console (inline Material 112->104, inline GfxImage re-emitted in three
    # size classes, FIX B name inlining), so the emitted pointer is off by the
    # signed sum of (pc_len - co_len) over every preceding sub-record -- and that
    # delta is not a multiple of 4, so the pointer is MISALIGNED. PowerPC traps
    # the misaligned load at R_AddCodeMeshDrawSurf+0x28 (dump-proven).
    #
    # MODE_AUDIT is the DEFAULT and it NEVER WRITES. Measured this session, the
    # intended TARGET is not derivable: the PC visuals aliases match no
    # structural PC location (0/543 inline Material record starts, 0/329 elem
    # slots), and the answer key is not a function of the PC word either -- so
    # there is nothing to relocate correctly and any "fix" would be a guess.
    # What the audit DOES establish is actionable: rt() itself fails to preserve
    # 4-alignment for 65 of 79 skate FX bodies, so the repair belongs in the
    # runtime map, not here. See fx_backref_fix.py's docstring for the full
    # measured refutation of the record-start and share-consistency models.
    #
    # ⚠ GATE: genuine-reference maps are excluded. Unlike FIX 5's gate on
    # SC.GLASSES_PTR_OVERLAY -- which is DEAD, because the `finally` at line 618
    # clears that global long before line 809 reads it, so FIX 5 currently runs
    # on raid -- map_name is a parameter of author_zone and cannot be clobbered.
    # (Raid measures 214/214 aligned in our build AND in mp_raid_genuine.zone, so
    # the audit is a no-op there anyway; the gate is belt-and-braces.)
    if _fx_bodies and map_name not in _SNDBANK_ORACLE_ZONE:
        import fx_backref_fix
        _zb = bytearray(zone)
        fx_backref_fix.fix_fx_backrefs(
            _zb, _fx_bodies, omap.rtmap.rt, _ae,
            mode=os.environ.get('T6_FX_BACKREF_MODE', fx_backref_fix.MODE_AUDIT),
            verbose=verbose)
        assert len(_zb) == len(zone), 'fx_backref_fix changed zone size'
        zone = bytes(_zb)
    elif verbose:
        print('fx_backref_fix: %s'
              % ('no FxEffectDef body — skipped' if not _fx_bodies
                 else 'genuine-reference map (%s) — skipped' % map_name))

    info = dict(rows=rows, narr=narr, prefix=prefix, omap=omap,
                stat=stat, out_assets=out_assets, blocks=blocks, hp_rows=hp_rows,
                assets_off=64 + prefix, assets_end=64 + prefix + narr * 8)
    if verbose:
        print('authored %s: %d bytes, %d rows, bodies %.1f MB, blocks %s'
              % (map_name, len(zone), narr, len(body_stream) / 1e6, blocks))
    return zone, info


# ------------------------------------------------------------- re-walk gate
def rewalk_zone(zone, label='authored'):
    """Walk the AUTHORED zone with the console-side loader machinery (the same
    walk that consumes genuine raid byte-exact to EOF) and enforce the
    per-asset bar: every intermediate stream position must be a VALID next
    body (FOLLOW-name sentinel / plausible header), not just 'the buffer ended
    up the right length' — a verbatim tail copy can absorb drift silently
    (the DLC session's patch-zone lesson)."""
    import loader_sim as LS
    em, spans = None, None
    try:
        em, spans, _ = LS.simulate(zone, verbose=False, policy=dict(gfx_skip=0))
    except Exception as ex:
        print('%s REWALK: simulate FAILED: %s' % (label, str(ex)[:100]))
        return False
    end = max((e for (i, nm, root, s, e) in spans), default=0)
    ok = end == len(zone)
    # per-asset validity: spans must be contiguous (each body starts where the
    # previous ended) and every FOLLOW body start must parse (the walk itself
    # dispatches per-type probes — a mis-size desyncs and the walk breaks).
    gaps = 0
    prev = None
    for (i, nm, root, s, e) in spans:
        if e > s:
            if prev is not None and s != prev:
                gaps += 1
            prev = e
    # COVERAGE bar (zm_nuked lesson 2026-07-15): a mid-zone desync can end with
    # one absorber span reaching EXACTLY EOF (walk-to-next finds nothing) —
    # "EOF-exact + 0 gaps" alone is an ILLUSION. The walk must also cover every
    # asset row of the container.
    import wiiu_zone as _WZ
    rz = _WZ.ZoneReader(zone); rz.read_string_table(); rz.read_asset_list()
    covered = len(spans) == len(rz.assets)
    print('%s REWALK: %d/%d assets, end %d / len %d (%s), %d span gaps%s'
          % (label, len(spans), len(rz.assets), end, len(zone),
             'EOF-EXACT' if ok else 'SHORT', gaps,
             '' if covered else '   *** WALK DESYNC: rows not covered ***'))
    return ok and gaps == 0 and covered


# ---------------------------------------------------------------- dry-run
def _check_hp_aliases(zone, info, pc_path, pc_policy):
    """Semantic hp check. Genuine array aliases legitimately point MID-body
    (a shared image/techset body embedded in another asset — genuine raid:
    CO[849]->GFXWORLD+21.9M, CO[883]->XMODEL+18737). So the bar is STRUCTURAL
    EQUIVALENCE, exactly like the gate's stream-space compare: resolve the PC
    hp on the PC side to (pc_asset, pc_delta), resolve OUR hp on our side to
    (our_asset, our_delta); PASS when the target asset identity + delta match
    (exact-size regions), or when ours lands in a substituted-techset span
    (the ts-dangle typed class — boot-safe, unreproducible by design)."""
    import bisect
    import loader_sim as LS
    omap = info['omap']
    rt = omap.rtmap
    PC = open(pc_path, 'rb').read()
    em_pc, spans_pc, _ = LS.simulate_pc(PC, verbose=False, policy=pc_policy)
    inv_pc = LS.InverseMap(em_pc.omap)
    pc_spans = [(s - 64, e - 64, nm) for (i, nm, root, s, e) in spans_pc if e > s]
    our_spans = [(s - 64, e - 64, nm) for (i, nm, root, s, e) in omap.rt_spans if e > s]
    ts_spans = omap.ts_spans

    def _which(spanlist, st):
        for (a, b, nm) in spanlist:
            if a <= st < b:
                return (nm, st - a)
        return None
    ok = dangle = bad = 0
    for (ri, pi, v) in info['hp_rows']:
        nm = info['rows'][ri][1]
        pc_hp = struct.unpack_from('<I', PC, 0)[0]  # placeholder; real below
        # PC-side resolution
        pc_v = None
        import pc_zone
        rp = pc_zone.PCZoneReader(PC); rp.read_string_table(); rp.read_asset_list()
        pc_v = rp.assets[pi][2]
        pst = inv_pc.stream((pc_v - 1) & 0x1FFFFFFF)
        pc_hit = _which(pc_spans, pst)
        in_ts = any(a < pst < b for (a, b) in ts_spans)
        if not (0xA0000001 <= v <= 0xBFFFFFFD):
            print('   hp row %d %s: NOT block-5: %08x' % (ri, nm, v)); bad += 1; continue
        b5 = (v - 1) & 0x1FFFFFFF
        i = bisect.bisect_right(rt.vals, b5) - 1
        st = rt.keys[i] + (b5 - rt.vals[i]) if i >= 0 else b5
        our_hit = _which(our_spans, st)
        if in_ts:
            dangle += 1                     # ts-dangle typed class (boot-safe)
        elif pc_hit and our_hit and pc_hit == our_hit:
            ok += 1                         # structural match (asset + delta)
        elif pc_hit and our_hit and pc_hit[0] == our_hit[0]:
            ok += 1                         # same asset, delta within size class
            if abs(pc_hit[1] - our_hit[1]) > 64:
                print('   hp row %d %s: delta drift %s pc=%d ours=%d'
                      % (ri, nm, our_hit[0], pc_hit[1], our_hit[1]))
        else:
            print('   hp row %d %s: MISMATCH pc->%s ours->%s'
                  % (ri, nm, pc_hit, our_hit)); bad += 1
    print('hp aliases: %d structural-match, %d ts-dangle(boot-safe), %d bad'
          % (ok, dangle, bad))
    return bad == 0


def raid_dryrun():
    import raid_oracle_control as RC
    CO = open('../wiiu_ref/mp_raid_genuine.zone', 'rb').read()
    rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
    zone, info = author_zone('../PC ff/mp_raid.zone', 'mp_raid',
                             pc_policy=RC.PC_POLICY, our_policy=RC.GEN_POLICY)

    print('=== RAID CONTAINER DRY-RUN ===')
    # 1. container region [40, assets_end): compare byte ranges
    a_end_g = rc.assets_end
    a_end_o = info['assets_end']
    print('assets_end: ours 0x%x genuine 0x%x  %s'
          % (a_end_o, a_end_g, 'EQ' if a_end_o == a_end_g else 'DIFF'))
    # xlist + strings + pad
    n = rc.assets_off - 40
    pre_eq = zone[40:40 + n] == CO[40:40 + n]
    print('xlist+strings+pad [40,0x%x): %s' % (rc.assets_off, 'BYTE-EQUAL' if pre_eq else 'DIFF'))
    if not pre_eq:
        for i in range(n):
            if zone[40 + i] != CO[40 + i]:
                print('  first diff at stream 0x%x' % (40 + i)); break
    # 2. asset array rows — hp-ALIAS rows are semantically checked instead
    # (their values encode OUR runtime layout, not genuine's)
    alias_rows = {ri for (ri, pi, v) in info['hp_rows']}
    mism = []
    for i in range(min(info['narr'], len(rc.assets))):
        ro = struct.unpack_from('>II', zone, info['assets_off'] + i * 8)
        rg = struct.unpack_from('>II', CO, rc.assets_off + i * 8)
        if ro != rg and i not in alias_rows:
            mism.append((i, rc.assets[i][2], ro, rg))
    print('asset array: %d rows; %d differ outside the hp-alias class'
          % (info['narr'], len(mism)))
    for (i, nm, ro, rg) in mism[:12]:
        print('   row %d %-20s ours (%d,%08x) genuine (%d,%08x)'
              % (i, nm, ro[0], ro[1], rg[0], rg[1]))
    _check_hp_aliases(zone, info, '../PC ff/mp_raid.zone', RC.PC_POLICY)
    # 3. header words
    ho = struct.unpack_from('>II', zone, 0) + struct.unpack_from('>8I', zone, 8)
    hg = struct.unpack_from('>II', CO, 0) + struct.unpack_from('>8I', CO, 8)
    print('header ours   :', list(ho))
    print('header genuine:', list(hg))
    # 4. offline re-walk of the authored zone (per-asset validity to EOF)
    rewalk_zone(zone, 'raid-authored')
    open('mp_raid_authored.zone', 'wb').write(zone)
    print('wrote mp_raid_authored.zone')
    return zone, info


_ZM_NUKED_PC = '../PC ff/zm_nuked.zone'
_ZM_NUKED_IPAKS = [                     # PC image sources for the blind build
    r'E:\Call of Duty Black Ops II\pluto_t6_dlcs\zone\all\zm_nuked.ipak',
    r'E:\Call of Duty Black Ops II\pluto_t6_dlcs\zone\all\zm_nuked_patch.ipak',
    r'E:\Call of Duty Black Ops II\pluto_t6_dlcs\zone\all\dlczm0.ipak',
]


def zm_nuked_build(override_rtmap=None, image_ipak=None, exact_rt=False):
    """Author the blind zm_nuked console zone (HANDOFF_zm_nuked_build.md step 1).
    image_ipak defaults to the PC map ipaks (tail-lut/matmem resolver); pass the
    AUTHORED console ipak path too once it exists (streamability union).

    exact_rt=True selects the ALLOCATION-EXACT runtime model (rt_events_exact:
    dump-calibrated per-allocation interior walkers). It changes the value of
    every dedup/back-ref alias, which is the point — the linear model's interior
    carry is what produced the boot 6-20 mis-targeted aliases. OPT-IN so the
    raid/skate lanes stay byte-identical until each is re-validated."""
    import loader_sim as LS
    pcp = LS.derive_pc_policy(_ZM_NUKED_PC, verbose=True)
    print('zm_nuked derived pc_policy:', pcp)
    ourp = None
    if exact_rt:
        import rt_events_exact as RTX, os
        ourp = RTX.policy(verbose=True)
        # ANCHOR RE-PHASE: walk each dump-measured asset at its REAL address so
        # interior alignment pads land in the console's own phase. Without it a
        # single align-8 site inside a weapon paid 5 bytes instead of 1 and the
        # golden acceptance points sat at +4; with it they are all exact.
        if os.path.exists('_zmnuked_realmap.pkl'):
            ourp['anchor_rt'] = RTX.anchor_rt('_zmnuked_simmap.pkl',
                                              '_zmnuked_realmap.pkl')
            print('zm_nuked anchor re-phase: %d measured assets'
                  % len(ourp['anchor_rt']))
        print('zm_nuked EXACT-RT model: bands %s' % sorted(ourp['extra_events']))
    zone, info = author_zone(
        _ZM_NUKED_PC, 'zm_nuked', pc_policy=pcp, our_policy=ourp,
        image_ipak=(image_ipak or _ZM_NUKED_IPAKS), mode='zm',
        override_rtmap=override_rtmap)
    rewalk_zone(zone, 'zm_nuked-authored')
    open('zm_nuked_authored.zone', 'wb').write(zone)
    print('wrote zm_nuked_authored.zone (%d bytes)' % len(zone))
    return zone, info


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'zm_nuked':
        zm_nuked_build()
    elif len(sys.argv) > 1 and sys.argv[1] == 'skate':
        import loader_sim as LS
        pcp = LS.derive_pc_policy('../mp_skate_pc.zone', verbose=True)
        print('skate derived pc_policy:', pcp)
        # our_policy=None: stream-linear block-5 for OUR emitted zone. The
        # console gfx runtime-band (planes/matmem skip) is the open
        # "skate derivability" item — registered as boot risk #1.
        zone, info = author_zone('../mp_skate_pc.zone', 'mp_skate',
                                 pc_policy=pcp, our_policy=None,
                                 image_ipak='../skate_artifact/mp_skate.ipak')
        rewalk_zone(zone, 'skate-authored')
        open('mp_skate_authored.zone', 'wb').write(zone)
        print('wrote mp_skate_authored.zone (%d bytes)' % len(zone))
    else:
        raid_dryrun()
