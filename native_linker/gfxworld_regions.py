#!/usr/bin/env python3
"""
GfxWorld REGION GENERATORS (Track F): the emit rows gfxworld_dynamics doesn't cover.

Bucket C — PC-sourced structural conversions:
  cells          : GfxCell 48B + aabbTrees 40B + smodelIndexes u16 + portals 92B +
                   portal verts 12B + reflectionProbe index bytes. Same strides both
                   platforms -> per-field endian map (derived from the raid matched
                   pair with derive_swapmap, then PINNED below).
  materialMemory : 8B {Material* , int} entries + inline Material assets converted
                   via material_convert.convert_material (Track A, oracle-validated).

Validation: byte-exact vs the genuine mp_raid oracle mod pointer-word allowlist,
then second-map spot-check (per house rules). Pointer VALUES are the assemble
session's job (loader_sim omap); we emit PC alias words + fixup positions.
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


def _u32(d, o, le):
    return struct.unpack_from('<I' if le else '>I', d, o)[0]


# ---------------------------------------------------------------------------
# cells: structural walk shared by both platforms (mirrors gfxworld_probe2)
# ---------------------------------------------------------------------------

def walk_cells(d, le, start, cell_count):
    """Yield (kind, off, count_or_len) subregions in stream order.
    kinds: cells(count), trees(count), smidx(count u16), portals(count),
    pverts(count 12B), probeidx(len bytes)."""
    o = start
    out = [('cells', o, cell_count)]
    cb = o
    o += cell_count * 48
    for i in range(cell_count):
        co = cb + i * 48
        atc = _u32(d, co + 24, le)
        atp = _u32(d, co + 28, le)
        pc_ = _u32(d, co + 32, le)
        pp_ = _u32(d, co + 36, le)
        rc_ = d[co + 40]
        rp_ = _u32(d, co + 44, le)
        if atp in PTRS:
            ab = o
            out.append(('trees', ab, atc))
            o += atc * 40
            for j in range(atc):
                ao = ab + j * 40
                sic = struct.unpack_from('<H' if le else '>H', d, ao + 30)[0]
                if _u32(d, ao + 32, le) in PTRS:
                    out.append(('smidx', o, sic))
                    o += sic * 2
        if pp_ in PTRS:
            pb = o
            out.append(('portals', pb, pc_))
            o += pc_ * 92
            for j in range(pc_):
                po = pb + j * 92
                vcnt = d[po + 40]
                if _u32(d, po + 36, le) in PTRS:
                    out.append(('pverts', o, vcnt))
                    o += vcnt * 12
        if rp_ in PTRS:
            out.append(('probeidx', o, rc_))
            o += rc_
    return out, o


# Per-struct byte transform maps, DERIVED from the raid matched pair (2026-07-09,
# scratch derive_cells_swapmap) and PINNED. ops: 'w' 4B swap, 'h' 2B swap,
# 'b' byte copy, 'p' pointer word (4B swap + fixup position recorded).
# Notes:
#  - cell@24 aabbTreeCount: console REBUILDS the per-cell aabb trees with a
#    slightly different split heuristic (23/47 raid cells differ by a few nodes;
#    portals/pverts/probeidx identical). We emit the PC-built tree (self-
#    consistent, reorder_pc philosophy); equal-count cells verify byte-exact.
#  - portal@32 is a Material/asset ALIAS pointer -> fixup class.
SWAPMAPS = {
    'cell':   [(0,'w'),(4,'w'),(8,'w'),(12,'w'),(16,'w'),(20,'w'),(24,'w'),
               (28,'p'),(32,'w'),(36,'p'),(40,'b'),(41,'h'),(43,'b'),(44,'p')],
    'tree':   [(0,'w'),(4,'w'),(8,'w'),(12,'w'),(16,'w'),(20,'w'),
               (24,'h'),(26,'h'),(28,'h'),(30,'h'),(32,'p'),(36,'w')],
    'portal': [(0,'w'),(4,'w'),(8,'w'),(12,'w'),(16,'w'),(20,'w'),(24,'w'),
               (28,'b'),(29,'b'),(30,'b'),(31,'b'),(32,'p'),(36,'w'),
               (40,'b'),(41,'h'),(43,'b'),(44,'w'),(48,'w'),(52,'w'),(56,'w'),
               (60,'w'),(64,'w'),(68,'w'),(72,'w'),(76,'w'),(80,'w'),(84,'w'),
               (88,'w')],
}


def _is_alias(v):
    return 0xA0000000 <= v < 0xC0000000


def apply_map(src, dst, smap, stride, count, fixups, dst_base, le):
    """Apply a per-struct transform map over `count` elements of `stride` bytes.
    'p' words record a fixup position only when the value is a real alias
    (FOLLOW/INSERT/null sentinels pass through as plain swapped words)."""
    for i in range(count):
        b = i * stride
        for off, op in smap:
            if op == 'w' or op == 'p':
                dst[b + off:b + off + 4] = src[b + off:b + off + 4][::-1]
                if op == 'p' and _is_alias(struct.unpack_from('<I', src, b + off)[0]):
                    fixups.append(dst_base + b + off)
            elif op == 'h':
                dst[b + off:b + off + 2] = src[b + off:b + off + 2][::-1]
            else:
                dst[b + off] = src[b + off]


def conv_cells(pc, pstart, cell_count, fixups=None, out_base=0):
    """Convert the PC cells region -> console bytes (PC-built aabb trees kept;
    counts stay PC's -> output length = PC region length). Returns bytes."""
    if fixups is None:
        fixups = []
    subs, pend = walk_cells(pc, True, pstart, cell_count)
    out = bytearray(pc[pstart:pend])
    for kind, off, cnt in subs:
        rel = off - pstart
        if kind == 'cells':
            apply_map(pc[off:off + cnt * 48], memoryview(out)[rel:rel + cnt * 48],
                      SWAPMAPS['cell'], 48, cnt, fixups, out_base + rel, True)
        elif kind == 'trees':
            apply_map(pc[off:off + cnt * 40], memoryview(out)[rel:rel + cnt * 40],
                      SWAPMAPS['tree'], 40, cnt, fixups, out_base + rel, True)
        elif kind == 'smidx':
            for j in range(cnt):
                out[rel + j * 2:rel + j * 2 + 2] = pc[off + j * 2:off + j * 2 + 2][::-1]
        elif kind == 'portals':
            apply_map(pc[off:off + cnt * 92], memoryview(out)[rel:rel + cnt * 92],
                      SWAPMAPS['portal'], 92, cnt, fixups, out_base + rel, True)
        elif kind == 'pverts':
            for j in range(cnt * 3):
                out[rel + j * 4:rel + j * 4 + 4] = pc[off + j * 4:off + j * 4 + 4][::-1]
        # probeidx: bytes, verbatim
    return bytes(out)


def validate_cells(pc, pstart, co, cstart, cell_count):
    """Raid-oracle validation: pair sub-kinds; portals/pverts/smidx/probeidx and
    equal-tree-count cells must be byte-exact (mod portal@32 alias words)."""
    conv = conv_cells(pc, pstart, cell_count)
    psubs, pend = walk_cells(pc, True, pstart, cell_count)
    csubs, cend = walk_cells(co, False, cstart, cell_count)
    from collections import defaultdict
    ST = {'cells': 48, 'trees': 40, 'portals': 92, 'pverts': 12, 'smidx': 2, 'probeidx': 1}
    Pk = defaultdict(list); Ck = defaultdict(list)
    for k, off, cnt in psubs:
        st = ST[k]
        for i in range(cnt):
            Pk[k].append(conv[off - pstart + i * st: off - pstart + (i + 1) * st])
    for k, off, cnt in csubs:
        st = ST[k]
        for i in range(cnt):
            Ck[k].append(co[off + i * st: off + (i + 1) * st])
    rpt = {}
    for k in ('portals', 'pverts', 'probeidx'):
        n = min(len(Pk[k]), len(Ck[k])); ok = 0
        for a, b in zip(Pk[k], Ck[k]):
            if k == 'portals':
                a = a[:32] + a[36:]; b = b[:32] + b[36:]   # mask alias ptr @32
            ok += (a == b)
        rpt[k] = (ok, n, len(Pk[k]), len(Ck[k]))
    # equal-count cells: whole cell struct + its trees byte-exact
    eqc = eqt = tot = 0
    pt = [struct.unpack_from('<I', pc, pstart + i * 48 + 24)[0] for i in range(cell_count)]
    ct = [struct.unpack_from('>I', co, cstart + i * 48 + 24)[0] for i in range(cell_count)]
    for i in range(cell_count):
        if pt[i] == ct[i]:
            tot += 1
            if conv[i * 48:(i + 1) * 48] == co[cstart + i * 48:cstart + (i + 1) * 48]:
                eqc += 1
    rpt['cells_eqcount'] = (eqc, tot, cell_count)
    return rpt


# ---------------------------------------------------------------------------
# materialMemory: entries + inline materials
# ---------------------------------------------------------------------------

def _eq_masked(a, b, stride, alias_offs=(), fl_offs=()):
    """Byte-equal mod (a) alias pointer words (both sides alias), (b) 1-ULP float
    LSB rounding at fl_offs (BE LSB = word byte 3)."""
    if len(a) != len(b):
        return False
    n = len(a) // stride
    for i in range(n):
        base = i * stride
        j = 0
        while j < stride:
            o = base + j
            if j in alias_offs and _is_alias(struct.unpack_from('>I', b, o)[0]) \
               and _is_alias(struct.unpack_from('>I', a, o)[0]):
                j += 4; continue
            if j in fl_offs:
                if a[o:o+3] == b[o:o+3] and abs(a[o+3] - b[o+3]) <= 1:
                    j += 4; continue
                if a[o:o+4] != b[o:o+4]:
                    return False
                j += 4; continue
            if a[o] != b[o]:
                return False
            j += 1
    return True


def conv_material_memory(pc, pstart, mm_count, reloc=None, fixups=None, out_base=0,
                         sampler_lookup=None):
    """Convert PC materialMemory region -> console stream.
    Entries: 8B {Material* ptr, int memory} swap4 both words. For each entry whose
    material ptr is FOLLOW/INSERT, the inline PC Material stream that follows is
    converted with material_convert.convert_material (console 104B body + dynamic)."""
    import material_convert as MC
    if fixups is None:
        fixups = []
    if reloc is None:
        reloc = lambda v: v
    out = bytearray()
    src = pstart
    inline = []
    for i in range(mm_count):
        e = pstart + i * 8
        ptr = _u32(pc, e, True)
        mem = _u32(pc, e + 4, True)
        if ptr in PTRS:
            out += struct.pack('>I', ptr)
            inline.append(i)
        else:
            fixups.append(out_base + len(out))
            out += struct.pack('>I', reloc(ptr))
        out += struct.pack('>I', mem)
    src = pstart + mm_count * 8
    # emitter #3 resident-image scope: while active, convert_image resolves inline-material
    # colorMaps from MATMEM_IMAGE_SOURCE (resident->inline pixels / else->real streamed body)
    # instead of the metadata-less 1x1 stub that nulls a lit material's base-diffuse sampler
    # (skate NULL+0x10 deref). Bracket only OUR inline-material converts here; restore after.
    _prev = MC.MATMEM_INLINE_ACTIVE
    MC.MATMEM_INLINE_ACTIVE = True
    try:
        for _ in inline:
            body, src = MC.convert_material(pc, src, reloc=reloc)
            body = bytearray(body)
            _remap_sampler_states(body, sampler_lookup)
            out += body
    finally:
        MC.MATMEM_INLINE_ACTIVE = _prev
    return bytes(out), src


def _remap_sampler_states(body, sampler_lookup=None):
    """Console samplerState remap, derived from the raid mm oracle (1289 texdefs):
    the 0x?4 filter class drops by 9 (0x14->0x0b, 0x34->0x2b, 0xf4->0xeb =
    aniso->trilinear); 0x01/0x0a unchanged. The exceptions (genuine keeps 0x14 /
    uses 0x13) are a PER-IMAGE property (173/177 raid image aliases have a single
    consistent console samplerState; no-mip images can't take the trilinear
    downgrade). `sampler_lookup(image_alias_word, pc_sampler) -> console_sampler
    or None` lets the assemble ctx resolve it from image meta; fallback = -9 rule
    (CAVEAT: cosmetic filtering nuance on the minority)."""
    import material_convert as MC
    pieces, _ = _console_material_pieces(
        bytes(body), 0, include_techset=MC.INLINE_TECHSET_HOOK is not None)
    for tag, off, sz in pieces:
        if tag != 'texdefs':
            continue
        for e in range(sz // 16):
            p = off + e * 16
            s = body[p + 6]
            if s & 0x07 != 0x04:
                continue
            v = None
            if sampler_lookup is not None:
                v = sampler_lookup(struct.unpack_from('>I', body, p + 12)[0], s)
            body[p + 6] = (s - 9) if v is None else v


def _console_material_pieces(d, o, include_techset=True):
    """Parse a console Material stream at `o` -> (pieces, end). pieces = list of
    (tag, off, size) into `d`: body/name/texdefs/img_body/img_name/img_pix/
    consts/statebits (+ recursion for thermal)."""
    import shader_probe
    u = lambda p: struct.unpack_from('>I', d, p)[0]
    pieces = []
    b = o
    pieces.append(('body', b, 104))
    o = b + 104
    tc, cc, sbc = d[b + 72], d[b + 73], d[b + 74]
    tsp, ttp, ctp, sbp, th = u(b + 80), u(b + 84), u(b + 88), u(b + 92), u(b + 96)
    if u(b) in PTRS:
        e = d.index(b'\x00', o)
        pieces.append(('name', o, e + 1 - o))
        o = e + 1
    if tsp in PTRS and include_techset:
        no, _ = shader_probe.parse_techset(d, o)
        pieces.append(('techset', o, no - o))
        o = no
    if ttp in PTRS:
        defs = o
        pieces.append(('texdefs', o, tc * 16))
        o += tc * 16
        for i in range(tc):
            if u(defs + i * 16 + 12) in PTRS:
                ib = o
                pieces.append(('img_body', ib, 328))
                o += 328
                if u(ib + 320) in PTRS:
                    e = d.index(b'\x00', o)
                    pieces.append(('img_name', o, e + 1 - o))
                    o = e + 1
                if u(ib + 176) in PTRS and d[ib + 171] == 0:
                    n = u(ib + 160)
                    pieces.append(('img_pix', o, n))
                    o += n
    if ctp in PTRS:
        pieces.append(('consts', o, cc * 32))
        o += cc * 32
    if sbp in PTRS:
        pieces.append(('statebits', o, sbc * 8))
        o += sbc * 8
    if th in PTRS:
        sub, o = _console_material_pieces(d, o, include_techset)
        pieces += sub
    return pieces, o


_BODY_PTR_OFFS = (0, 80, 84, 88, 92, 96)


def validate_material_memory(pc, pstart, co, cstart, mm_count):
    """Piecewise raid-oracle validation. Converts each PC inline material and
    compares against the genuine console stream piece-by-piece (techsets excluded:
    Track B substitution supplies those at assemble). Alias pointer words masked."""
    conv, _ = conv_material_memory(pc, pstart, mm_count)
    # entries
    gen_entries = co[cstart:cstart + mm_count * 8]
    ent_ok = 0
    for i in range(mm_count):
        a = conv[i * 8:(i + 1) * 8]; b = gen_entries[i * 8:(i + 1) * 8]
        if a == b or (_is_alias(struct.unpack_from('>I', b, 0)[0])
                      and _is_alias(struct.unpack_from('>I', a, 0)[0])
                      and a[4:] == b[4:]):
            ent_ok += 1
    # pair material streams
    from collections import Counter
    ok = Counter(); bad = Counter(); firstbad = {}
    cco = cstart + mm_count * 8
    vvo = mm_count * 8
    n_pairs = 0
    for i in range(mm_count):
        if struct.unpack_from('>I', co, cstart + i * 8)[0] not in PTRS:
            continue
        gpieces, cco = _console_material_pieces(co, cco, include_techset=True)
        vpieces, vvo = _console_material_pieces(conv, vvo, include_techset=False)
        n_pairs += 1
        g = [p for p in gpieces if p[0] != 'techset']
        if len(g) != len(vpieces) or any(a[0] != b[0] for a, b in zip(g, vpieces)):
            bad['STRUCT'] += 1
            continue
        for (tag, goff, gsz), (_, voff, vsz) in zip(g, vpieces):
            A = conv[voff:voff + vsz]; B = co[goff:goff + gsz]
            if vsz != gsz:
                if tag == 'statebits':
                    ok['statebits_rebuilt'] += 1   # console dedups; ours is self-consistent
                else:
                    bad[tag] += 1; firstbad.setdefault(tag, (i, 'size %d!=%d' % (vsz, gsz)))
                continue
            if tag == 'body':
                m = True
                for j in range(0, 104, 4):
                    if A[j:j+4] == B[j:j+4]:
                        continue
                    if j in _BODY_PTR_OFFS and _is_alias(struct.unpack_from('>I', B, j)[0]):
                        continue
                    if j == 32:      # hashIndex u16 (+pad): per-zone hash slot, console-computed
                        continue
                    if j == 72 and A[j:j+2] == B[j:j+2] and A[j+3] == B[j+3]:
                        continue     # @74 stateBitsCount: console dedups the statebits table
                    m = False
                    firstbad.setdefault('body', (i, 'off %d conv %s gen %s' % (j, A[j:j+4].hex(), B[j:j+4].hex())))
                    break
            elif tag == 'texdefs':
                m = True
                for e in range(gsz // 16):
                    a = A[e*16:(e+1)*16]; b2 = B[e*16:(e+1)*16]
                    if a == b2:
                        continue
                    if a[:12] == b2[:12] and _is_alias(struct.unpack_from('>I', b2, 12)[0]):
                        continue
                    m = False
                    firstbad.setdefault('texdefs', (i, 'e%d conv %s gen %s' % (e, a.hex(), b2.hex())))
                    break
            else:
                m = A == B
                if not m:
                    j = next(k for k in range(gsz) if A[k] != B[k])
                    firstbad.setdefault(tag, (i, 'off %d conv %s gen %s' % (j, A[j:j+8].hex(), B[j:j+8].hex())))
            (ok if m else bad)[tag] += 1
    return dict(entries=(ent_ok, mm_count), pairs=n_pairs, ok=dict(ok), bad=dict(bad),
                firstbad=firstbad)
