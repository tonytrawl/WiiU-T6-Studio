#!/usr/bin/env python3
"""
Track A: PC -> Wii U image port pass (inline pixels -> IPAK streaming).

Works with the OAT write-path hooks:
  1. run Unlinker with OAT_IMAGE_DUMP=<dump_dir>  (dumps <name>.meta [+ .pcpix]
     for every console image; the produced zone is a throwaway)
  2. python ipak_stream.py prepare <dump_dir> <out_dir> [--ipak out.ipak] ...
     builds a ready 328-byte console body per image (<safe name>.body, plus
     <safe name>.pix tiled pixels for inline images) and authors the map ipak
     for images that need new streamed payloads
  3. run Unlinker with OAT_IMAGE_DIR=<out_dir> -> <map>_rewrite.ff -> wiiu_ff.py pack

Per image the prepare pass picks the first source that hits:
  A. genuine corpus: the image exists in a genuine Wii U zone -> copy the
     genuine 328-byte body verbatim (streamed keys resolve in the retail
     base_split/lowmip_split/map ipaks; genuine inline images also copy their
     already-tiled pixels). No new ipak entry needed.
  B. PC ipak: pull the IWI via the cross-platform nameHash, split the mip
     chain into console parts, GX2-tile each part payload, stamp the streamed
     body and add the parts to the authored map ipak.
     Layout rules (derived + crc-proven against genuine lowmip_split/map
     ipak payloads, see scratchpad probe_part0):
       - parts: each top mip with min(w,h) > 128 gets its own part, capped at
         3 parts total; the remaining chain is part 0
       - part payload: zero-filled buffer of align(imageSize + mipSize,
         0x2000) bytes with each level's tiled surface at its GX2 mip_chain
         offset (single-mip parts degenerate to the tiled surface)
       - GfxStreamedPartInfo packed word = cumulativeLevelCount |
         cumulativePayloadBytes << 4 (ascending part order)
       - streamed body tail: baseSize=0, width=height=depth=1, levelCount=1,
         streaming=1, pixels=0
  C. dumped inline pixels (.pcpix, custom-map path): tile the linear mips and
     keep the image inline (tiled .pix companion).
  D. nothing found: no .body emitted; the C++ computed body stands.

selftest mode rebuilds genuine streamed mp_raid bodies from PC sources and
diffs them field by field against the genuine zone (validates every rule).
"""
import argparse
import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gx2_texture as gx
import ipak as IP

ROOT = os.path.dirname(os.path.abspath(__file__))
FOLLOW = 0xFFFFFFFF

DEFAULT_GENUINE_ZONES = [
    os.path.join(ROOT, 'mp_raid_genuine.zone'),
    os.path.join(ROOT, 'zm_transit_original.zone'),
    os.path.join(ROOT, '..', 'common_mp.zone'),
]
DEFAULT_PC_IPAKS = [
    r'E:\pluto_t6_full_game\zone\all\base.ipak',
    r'E:\pluto_t6_full_game\zone\all\mp.ipak',
]

# T6 ImageFormat <-> GX2 (mirror of ConsoleWriterT6::T6FormatToGx2), rule (B) baked 2026-07-30:
# TRANSLATE OR RAISE, never default. The old `t6_to_gx2` silently returned 0x1a (RGBA8) for ANY
# unknown enum — the same silent-default class as material_convert's `.get(..., 0)` (which NULLed
# $identitynormalmap and ~2810 material bindings at once, cm34). Measured T6 table (from healthy
# genuine records, unanimous): RGBA8=5, BC1=6, BC2=9, BC3=0x0A, BC5=0x14; valid observed set
# {1,2,3,4,5,6,9,10,20}. RGBA8 rows: the engine's own table maps T6 4/5 -> GX2 0x1a; healthy
# streamed records carry 5. R8: T6 1 -> GX2 0x01 (the inline gray path's landed convention).
T6_TO_GX2 = {0x6: 0x31, 0x9: 0x32, 0xA: 0x33, 0x14: 0x35, 0x3: 0x07,
             0x5: 0x1a, 0x1: 0x01}
GX2_TO_T6_STRICT = {v: k for k, v in T6_TO_GX2.items()}
T6_VALID = frozenset((1, 2, 3, 4, 5, 6, 9, 10, 20))


class ImageFormatError(ValueError):
    """An image format enum with no measured translation reached the console authoring path.
    Rule (B)/(B-sub): never let a source-side enum leak, never default — measure the format,
    add the row with its evidence, and only then convert."""


def t6_to_gx2(fmt):
    g = T6_TO_GX2.get(fmt)
    if g is None:
        raise ImageFormatError('T6 ImageFormat 0x%X has no measured GX2 row (valid T6 set %s) '
                               '-- rule (B): translate or raise, never default'
                               % (fmt, sorted(T6_VALID)))
    return g


def gx2_to_t6(gfmt):
    t = GX2_TO_T6_STRICT.get(gfmt)
    if t is None:
        raise ImageFormatError('GX2 format 0x%X has no measured T6 row -- rule (B): translate '
                               'or raise, never default (the silent 0 = IMG_FORMAT_INVALID '
                               'made the loader skip Load_WiiuTexture entirely)' % (gfmt,))
    return t


def safe_name(name):
    return name.replace('/', '__')


def next_pow2(x):
    p = 1
    while p < x:
        p <<= 1
    return p


# ------------------------------------------------------- genuine corpus ---

def scan_genuine_bodies(zone_bytes):
    """-> {nameHash: (body328, pixels or None, name)} for every full console
    GfxImage body in a big-endian zone (streamed and inline)."""
    d = zone_bytes
    out = {}
    NAME_CHARS = set(b"abcdefghijklmnopqrstuvwxyz0123456789_/~$#&+.-")
    o = d.find(b'\xff\xff\xff\xff')
    while o != -1:
        B = o - 320                      # name ptr FOLLOW at +320
        o = d.find(b'\xff\xff\xff\xff', o + 4)
        if B < 0 or B + 328 > len(d):
            continue
        e = d.find(b'\x00', B + 328, B + 328 + 112)
        if e <= B + 328:
            continue
        nm = d[B + 328:e]
        if len(nm) < 3 or not all(c in NAME_CHARS for c in nm):
            continue
        name = nm.decode('latin-1')
        h = struct.unpack('>I', d[B + 324:B + 328])[0]
        if IP.r_hash_string(name) != h:
            continue
        streaming = d[B + 171]
        spc = d[B + 316]
        pixptr = struct.unpack('>I', d[B + 176:B + 180])[0]
        base = struct.unpack('>I', d[B + 160:B + 164])[0]
        if streaming == 1 and 1 <= spc <= 3 and d[B + 317:B + 320] == b'\x00\x00\x00':
            out[h] = (d[B:B + 328], None, name)
        elif streaming == 0 and spc == 0 and pixptr == FOLLOW and 0 < base <= 0x1000000:
            pix = d[e + 1:e + 1 + base]
            if len(pix) == base:
                out[h] = (d[B:B + 328], pix, name)
    return out


# ------------------------------------------------ streamed body authoring ---

def split_parts(mips):
    """mips: [(w, h, off, size)] top-first. -> list of part mip-lists in part
    order (part 0 first = the low-res chain)."""
    k = 0
    for w, h, _, _ in mips:
        if min(w, h) > 128:
            k += 1
        else:
            break
    k = min(k, 2, len(mips) - 1)    # part 0 must keep at least one level
    parts = [mips[k:]]
    for i in range(k - 1, -1, -1):
        parts.append([mips[i]])
    return parts


def base_tile_mode(gfmt, w, h):
    """Stock linker: macro (4) unless the padded level-0 surface is smaller
    than one macro tile -> micro (2). (Same rule as ConsoleWriterT6.)"""
    bpp = gx.BPP_BY_FORMAT[gfmt & 0x3F]
    we, he = gx.block_dims(gfmt, w, h)
    micro_tile_bytes = (64 * bpp) >> 3
    waf = max(1, 256 // micro_tile_bytes) if micro_tile_bytes < 256 else 1
    if next_pow2(we) < waf * 32 or next_pow2(he) < 16:
        return 2
    return 4


def tile_part_payload(blob, part_mips, gfmt):
    """One part's ipak payload from the PC IWI blob."""
    w0, h0 = part_mips[0][0], part_mips[0][1]
    tm = base_tile_mode(gfmt, w0, h0)
    img_size, mip_offs, mip_size, infos = gx.mip_chain(gfmt, w0, h0, tm, len(part_mips))
    # payload allocation: imageSize + mipSize aligned to 0x2000, zero-filled
    # (fits all 1018 genuine mp_raid part-0 payloads; single-mip parts are
    # already 0x2000-multiples so the align is a no-op there)
    total = (img_size + mip_size + 0x1FFF) & ~0x1FFF
    buf = bytearray(total)
    for i, (mw, mh, mo, ms) in enumerate(part_mips):
        inf = infos[i]
        tight = blob[mo:mo + ms]
        lin = gx.pad_linear(tight, mw, mh, gfmt, inf.pitch, inf.height)
        tiled = gx.tile(lin, mw, mh, gfmt, inf.tile_mode, pitch=inf.pitch)
        at = 0 if i == 0 else img_size + (0 if i == 1 else mip_offs[i - 1])
        buf[at:at + len(tiled)] = tiled
    return bytes(buf)


def build_gx2_header(gfmt, w, h, mips, faces=1):
    """156-byte GX2Texture header (u32 words little-endian inside the BE
    zone), same math as ConsoleWriterT6/gx2_texture. faces=6 -> cubemap
    (dim=cube, depth=6, imageSize covers all 6 face slices)."""
    tm = base_tile_mode(gfmt, w, h)
    img_size, mip_offs, mip_size, infos = gx.mip_chain(gfmt, w, h, tm, mips)
    hdr = bytearray(156)

    def le(i, v):
        struct.pack_into('<I', hdr, i * 4, v & 0xFFFFFFFF)

    le(0, 3 if faces == 6 else 1)   # dim: cube / 2D
    le(1, w)
    le(2, h)
    le(3, faces if faces == 6 else 1)   # depth = face count for cube
    le(4, mips)
    le(5, gfmt)
    le(6, 0)               # aa
    le(7, 1)               # use: texture
    le(8, img_size * faces)
    le(9, 0)               # image
    le(10, mip_size * faces)
    le(11, img_size * faces)   # mipmaps = image + imageSize
    le(12, infos[0].tile_mode)
    # swizzle word13 = (count of macro-tiled mip levels) << 16 — proven exact
    # vs genuine raid (2026-07-13): 185/185 GfxWorld matmem images + 2/2
    # standalone GfxImage. Our previous hardcoded 0 was the ONLY systematic
    # GX2-header field diff vs genuine (the skate texture-registration ~14MB
    # heap-stomp suspect).
    le(13, sum(1 for inf in infos if inf.tile_mode >= 4) << 16)
    le(14, infos[0].base_align)
    le(15, infos[0].pitch)
    mlo = [0] * 13
    if mips > 1:
        mlo[0] = img_size
        for i in range(1, min(mips - 1, 13)):
            mlo[i] = mip_offs[i]
    for i in range(13):
        le(16 + i, mlo[i])
    return bytes(hdr)


# ⚠ the Downloads dump vanished 2026-07-14 (see memory console-content-dir-missing); this E:
# copy has 121 ipaks vs the 120 the measured builds used — fine for (BY) TIER dedup (only the
# 10 tier paks are globbed, identical between dumps) but do NOT point residency decisions at it.
DEFAULT_WIIU_CONTENT = r'E:\Wii U Black ops 2\content'


def load_resident_universe(content_dir):
    """Rule (BY): the (name_hash, data_hash) set of every part in the RESIDENT retail tiers
    (base_split1..7, lowmip_split1..2, common_mp). An authored map pak must not duplicate any
    of them — 13/14 retail map paks carry ZERO part-0 entries because the whole low-mip chain
    lives once in lowmip_split; cross-pak resolution is how every shipped map already works.
    Cemu-validated on mp_skate (2237 -> 1008 entries, -44.5%, zone-neutral)."""
    uni = set()
    n_paks = 0
    for pat in ('base_split*.ipak', 'lowmip_split*.ipak', 'common_mp*.ipak'):
        for p in sorted(glob.glob(os.path.join(content_dir, pat))):
            pk = IP.IPak.read(p)
            n_paks += 1
            for en in pk.entries:
                uni.add((en.name_hash, en.data_hash))
    if not uni:
        raise RuntimeError('rule (BY): no resident-tier paks found under %r -- refusing to '
                           'author an undeduped map pak (pass an explicit content dir, or '
                           '--no-dedup to override loudly)' % (content_dir,))
    return uni, n_paks


def dedup_entries(entries, universe):
    """Filter (name_hash, data_hash, payload) triples against the resident universe (gate
    G-103). Returns (kept, dropped_count, dropped_bytes)."""
    kept, dropped, dbytes = [], 0, 0
    for (nh, dh, payload) in entries:
        if (nh, dh) in universe:
            dropped += 1
            dbytes += len(payload)
        else:
            kept.append((nh, dh, payload))
    return kept, dropped, dbytes


def build_streamed_body(meta, iwi, name_hash):
    """Full 328-byte streamed console GfxImage body + the ipak part entries
    [(part_index, payload)] from a parsed PC IWI.

    Rule (B), baked 2026-07-30: body+180 is DERIVED from the actual iwi gx2_format via the
    strict reverse map (raise on unmapped, never default). A caller-supplied meta['format']
    is cross-checked and a disagreement RAISES — that is exactly how a stale/leaked source
    enum (the B-sub DXGI class, or the silent-0 class) gets caught at build time instead of
    shipping as an image the loader silently never loads."""
    gfmt = iwi['gx2_format']
    if meta.get('mapType') == 5 or iwi.get('cube'):
        raise ImageFormatError('cube via build_streamed_body is unsupported: genuine console '
                               'cubes are INLINE, never streamed (3669/3669 genuine streamed '
                               'records, zero mapType==5) -- route the image resident (rule B3)')
    t6 = gx2_to_t6(gfmt)
    cf = meta.get('format')
    if cf not in (None, 0) and cf != t6:
        raise ImageFormatError('caller meta[format]=0x%X disagrees with derived T6 0x%X for '
                               'GX2 0x%X (nameHash 0x%08X) -- a leaked/stale source enum'
                               % (cf, t6, gfmt, name_hash))
    w, h = iwi['width'], iwi['height']
    mips = iwi['mips']
    body = bytearray(328)
    body[0:156] = build_gx2_header(gfmt, w, h, len(mips))
    body[156] = meta.get('mapType', 3) & 0xFF
    body[157] = meta.get('semantic', 0) & 0xFF
    body[158] = meta.get('category', 0) & 0xFF
    body[159] = 1                                   # delayLoadPixels (genuine: 1 on streamed)
    struct.pack_into('>I', body, 160, 0)            # baseSize
    struct.pack_into('>HHH', body, 164, 1, 1, 1)    # width, height, depth
    body[170] = 1                                   # levelCount
    body[171] = 1                                   # streaming
    struct.pack_into('>I', body, 176, 0)            # pixels: none
    struct.pack_into('>I', body, 180, t6)           # rule (B): derived, never copied

    parts = split_parts(mips)
    blob = iwi['blob']
    cum_levels = 0
    cum_bytes = 0
    entries = []
    for pi, pmips in enumerate(parts):
        payload = tile_part_payload(blob, pmips, gfmt)
        cum_levels += len(pmips)
        cum_bytes += len(payload)
        p = 184 + 44 * pi
        struct.pack_into('>I', body, p, (cum_levels & 0xF) | (cum_bytes << 4))
        struct.pack_into('>I', body, p + 4, IP.data_hash(payload, pi))
        struct.pack_into('>HH', body, p + 8, pmips[0][0], pmips[0][1])
        entries.append((pi, payload))
    body[316] = len(parts)
    struct.pack_into('>I', body, 320, FOLLOW)       # name follows
    struct.pack_into('>I', body, 324, name_hash)
    return bytes(body), entries


def build_inline_body(meta, pcpix):
    """Inline path (custom maps): linear top-first tight mips -> GX2-tiled
    contiguous pixel blob + 328-byte inline body.

    Cubemaps (meta['mapType']==5, e.g. skybox_<map>): pcpix = 6 faces stored
    consecutively (each face a top-first tight mip chain); every face is tiled
    independently into its own slice (genuine raid skybox: dim=cube, depth=6,
    imageSize/baseSize = 6x the per-face tiled size)."""
    fmt = meta.get('format', 0)
    if fmt not in T6_VALID:
        # rule (B)/(B-sub): a caller passing a raw PC/DXGI code (28/71/74/77/83 measured) or the
        # silent 0 lands here. t6_to_gx2 would also raise, but name the defect class precisely.
        raise ImageFormatError('build_inline_body got format 0x%X which is not a valid T6 '
                               'ImageFormat (valid set %s) -- the caller leaked a source-side '
                               'enum (rule B-sub) or defaulted (rule B); translate first'
                               % (fmt, sorted(T6_VALID)))
    gfmt = t6_to_gx2(fmt)
    w, h = meta['width'], meta['height']
    mips = max(1, meta.get('levelCount', 1))
    faces = 6 if meta.get('mapType') == 5 else 1
    tm = base_tile_mode(gfmt, w, h)
    img_size, mip_offs, mip_size, infos = gx.mip_chain(gfmt, w, h, tm, mips)
    # genuine pads the whole inline pixel allocation to 0x2000 (raid oracle: every
    # inline baseSize is a 8192-multiple; tiny mip chains all land at exactly 8192)
    total = ((img_size + mip_size) * faces + 0x1FFF) & ~0x1FFF
    buf = bytearray(total)
    src = 0
    for f in range(faces):
        fbase = f * (img_size + mip_size)
        for i in range(mips):
            mw, mh = max(1, w >> i), max(1, h >> i)
            we, he = gx.block_dims(gfmt, mw, mh)
            bpe = gx.BPP_BY_FORMAT[gfmt & 0x3F] >> 3
            tight_len = we * he * bpe
            tight = pcpix[src:src + tight_len]
            if len(tight) < tight_len:
                break
            src += tight_len
            inf = infos[i]
            lin = gx.pad_linear(tight, mw, mh, gfmt, inf.pitch, inf.height)
            # rule (B3): slice_index is LOAD-BEARING for cubes — without it only 2 of 6 faces
            # match the genuine tiling (proven byte-exact vs skybox_mp_carrier_ft: per-face
            # gx.tile(..., slice_index=<face>) reproduces the retail blob 1572864/1572864).
            tiled = gx.tile(lin, mw, mh, gfmt, inf.tile_mode, pitch=inf.pitch,
                            slice_index=f if faces == 6 else 0)
            at = 0 if i == 0 else img_size + (0 if i == 1 else mip_offs[i - 1])
            buf[fbase + at:fbase + at + len(tiled)] = tiled

    body = bytearray(328)
    body[0:156] = build_gx2_header(gfmt, w, h, mips, faces=faces)
    body[156] = meta.get('mapType', 3) & 0xFF
    body[157] = meta.get('semantic', 0) & 0xFF
    body[158] = meta.get('category', 0) & 0xFF
    body[159] = 0
    struct.pack_into('>I', body, 160, total)        # baseSize = tiled size
    struct.pack_into('>HHH', body, 164, w, h, max(1, meta.get('depth', 1)))
    # levelCount byte mirrors the source zone's value verbatim (genuine keeps 0 for
    # e.g. $identitynormalmap); tiling above still uses max(1, ...).
    body[170] = meta.get('levelCount_raw', mips) & 0xFF
    body[171] = 0                                   # inline
    struct.pack_into('>I', body, 176, FOLLOW)       # pixels follow
    struct.pack_into('>I', body, 180, fmt & 0xFFFFFFFF)
    struct.pack_into('>I', body, 320, FOLLOW)
    struct.pack_into('>I', body, 324, meta['hash'])
    return bytes(body), bytes(buf)


# ------------------------------------------------------------- commands ---

def read_meta(path):
    meta = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            k, v = parts[0], parts[1]
            meta[k] = v if k == 'name' else int(v)
    return meta


def cmd_prepare(args):
    corpus = {}
    for zp in args.genuine_zones:
        if os.path.exists(zp):
            found = scan_genuine_bodies(open(zp, 'rb').read())
            for k, v in found.items():
                corpus.setdefault(k, v)
            print('genuine corpus: %s -> %d bodies (total %d)' %
                  (os.path.basename(zp), len(found), len(corpus)))
    pc = None
    pc_paths = [p for p in args.pc_ipaks if os.path.exists(p)]
    if pc_paths:
        pc = IP.PcImageSource(pc_paths)
        print('PC ipaks: %s' % ', '.join(os.path.basename(p) for p in pc_paths))

    os.makedirs(args.out_dir, exist_ok=True)
    metas = sorted(glob.glob(os.path.join(args.dump_dir, '*.meta')))
    stats = {'genuine': 0, 'pc_ipak': 0, 'inline': 0, 'skipped': 0}
    ipak_entries = []
    for mp in metas:
        meta = read_meta(mp)
        name = meta['name']
        nh = meta['hash']
        base = os.path.join(args.out_dir, safe_name(name))
        if nh in corpus:
            body, pix, _ = corpus[nh]
            with open(base + '.body', 'wb') as f:
                f.write(body)
            if pix is not None:
                with open(base + '.pix', 'wb') as f:
                    f.write(pix)
            stats['genuine'] += 1
            continue
        parts = pc.find_pc_source(nh) if pc is not None else []
        if parts:
            iwi = dict(parts[0]['iwi'])
            iwi['blob'] = parts[0]['blob']
            body, entries = build_streamed_body(meta, iwi, nh)
            with open(base + '.body', 'wb') as f:
                f.write(body)
            for pi, payload in entries:
                ipak_entries.append((nh, IP.data_hash(payload, pi), payload))
            stats['pc_ipak'] += 1
            continue
        pcpix_path = mp[:-5] + '.pcpix'
        if os.path.exists(pcpix_path) and meta.get('width', 0) > 0:
            pcpix = open(pcpix_path, 'rb').read()
            try:
                body, pix = build_inline_body(meta, pcpix)
            except Exception as ex:
                print('  inline tiling FAILED for %s: %s' % (name, ex))
                stats['skipped'] += 1
                continue
            with open(base + '.body', 'wb') as f:
                f.write(body)
            with open(base + '.pix', 'wb') as f:
                f.write(pix)
            stats['inline'] += 1
            continue
        stats['skipped'] += 1

    print('prepared %d images: %d genuine-copy, %d PC-ipak streamed, '
          '%d inline-tiled, %d skipped (computed body stands)' %
          (len(metas), stats['genuine'], stats['pc_ipak'], stats['inline'],
           stats['skipped']))
    if args.ipak:
        if ipak_entries:
            # rule (BY), baked 2026-07-30: never ship a part the console already has.
            if getattr(args, 'no_dedup', False):
                print('⚠⚠ (BY) DEDUP DISABLED by --no-dedup: this pak may duplicate the '
                      'resident tiers (a load pattern NO retail pak produces). G-103 will fail.')
            else:
                uni, n_paks = load_resident_universe(
                    getattr(args, 'wiiu_content', None) or DEFAULT_WIIU_CONTENT)
                ipak_entries, ndrop, dbytes = dedup_entries(ipak_entries, uni)
                print('(BY) dedup vs %d resident paks (%d keys): dropped %d duplicate '
                      'part(s), %d bytes reclaimed' % (n_paks, len(uni), ndrop, dbytes))
            blob = IP.write_ipak(ipak_entries, endian='>')
            with open(args.ipak, 'wb') as f:
                f.write(blob)
            print('wrote %s: %d entries, %d bytes' %
                  (args.ipak, len(ipak_entries), len(blob)))
        else:
            print('no new ipak entries needed (all images genuine/skipped); '
                  'no ipak written')


def cmd_selftest(args):
    """Rebuild genuine streamed mp_raid bodies from PC sources; diff."""
    zone = open(os.path.join(ROOT, 'mp_raid_genuine.zone'), 'rb').read()
    corpus = scan_genuine_bodies(zone)
    pc = IP.PcImageSource([p for p in args.pc_ipaks if os.path.exists(p)])
    same = diff = nopc = 0
    part_ok = part_bad = 0
    content = args.wiiu_content
    ref_paks = []
    if content and os.path.isdir(content):
        for pat in ('lowmip_split*.ipak', 'base_split*.ipak', 'mp_raid.ipak'):
            for p in glob.glob(os.path.join(content, pat)):
                ref_paks.append(IP.IPak.read(p))
    by_key = {}
    for pk in ref_paks:
        for en in pk.entries:
            by_key[(en.name_hash, en.data_hash)] = (pk, en)

    for nh, (gbody, gpix, name) in sorted(corpus.items()):
        if gpix is not None:
            continue                    # inline: copied verbatim, nothing to prove
        parts = pc.find_pc_source(nh)
        if not parts:
            nopc += 1
            continue
        iwi = dict(parts[0]['iwi'])
        iwi['blob'] = parts[0]['blob']
        meta = dict(mapType=gbody[156], semantic=gbody[157], category=gbody[158],
                    format=struct.unpack('>I', gbody[180:184])[0], hash=nh)
        body, entries = build_streamed_body(meta, iwi, nh)
        # FULL byte-exact first (build_gx2_header now computes swizzle word13 exactly — the
        # old mask that zeroed +52 predates that fix and MISFIRED on every record whose
        # word13 we now reproduce correctly: 963 false "diffs" on 2026-07-30). The masked
        # compare is kept only as a legacy fallback for genuinely cosmetic +52 variants.
        gcmp = gbody[:52] + b'\x00\x00\x00\x00' + gbody[56:]
        if body == gbody:
            same += 1
        elif body == gcmp or (body[:52] + b'\x00\x00\x00\x00' + body[56:]) == gcmp:
            same += 1
        else:
            diff += 1
            if diff <= 5:
                print('DIFF %s:' % name)
                for i in range(0, 328, 4):
                    if body[i:i+4] != gbody[i:i+4]:
                        print('  +%3d ours=%s genuine=%s' %
                              (i, body[i:i+4].hex(), gbody[i:i+4].hex()))
        if by_key:
            for pi, payload in entries:
                key = (nh, IP.data_hash(payload, pi))
                hit = by_key.get(key)
                if hit is not None and hit[0].extract(hit[1]) == payload:
                    part_ok += 1
                else:
                    part_bad += 1
                    # a genuine key exists for this (name, partIndex) but our
                    # payload hashes differently -> construction mismatch;
                    # no genuine key at all -> just not in the loaded paks
                    gkeys = [k for k in by_key if k[0] == nh and k[1] >> 29 == pi]
                    if part_bad <= 10:
                        print('  part miss %s part%d: genuine keys for this part: %s'
                              % (name, pi, ['%08x' % k[1] for k in gkeys] or 'NONE in loaded paks'))
    print('selftest: %d bodies byte-exact (swizzle alias masked), %d diff, '
          '%d without PC source' % (same, diff, nopc))
    if by_key:
        print('          %d part payloads byte-exact in retail paks, %d not' %
              (part_ok, part_bad))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('dump_dir')
    p.add_argument('out_dir')
    p.add_argument('--ipak', default=None)
    p.add_argument('--genuine-zones', dest='genuine_zones', nargs='*',
                   default=DEFAULT_GENUINE_ZONES)
    p.add_argument('--pc-ipaks', dest='pc_ipaks', nargs='*',
                   default=DEFAULT_PC_IPAKS)
    p.add_argument('--wiiu-content', dest='wiiu_content', default=DEFAULT_WIIU_CONTENT,
                   help='console content dir holding the resident tiers for rule-(BY) dedup')
    p.add_argument('--no-dedup', dest='no_dedup', action='store_true',
                   help='(BY) escape hatch: author WITHOUT resident-universe dedup (loud)')
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser('selftest')
    p.add_argument('--pc-ipaks', dest='pc_ipaks', nargs='*',
                   default=DEFAULT_PC_IPAKS)
    p.add_argument('--wiiu-content', dest='wiiu_content', default=(
        r'C:\Users\Tony - Main Rig\Downloads'
        r'\Wii U Call of Duty Black Ops 2 USA WUP'
        r'\Wii U Call of Duty Black Ops 2 USA WUP\content'))
    p.set_defaults(func=cmd_selftest)
    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
