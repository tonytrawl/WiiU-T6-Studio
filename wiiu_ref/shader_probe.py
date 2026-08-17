#!/usr/bin/env python3
"""
Task #25: triangulate the console (Wii U GX2) MaterialVertexShader /
MaterialPixelShader / shader-program layout for T6 (Black Ops II).

Method: scan a genuine zone for standalone MaterialTechniqueSet(136) assets
(name FOLLOW @+0, 32 slot markers @+8, techset name chars @+136, and the
first FOLLOW slot must parse as a MaterialTechnique). Then parse every FOLLOW
technique's full dynamic stream and require perfect resync onto the next
technique / next asset. This is the same approach that solved GfxImage (328).

RESULT (2026-07-02, verified: mp_raid 172/172 techsets & 3221 techniques,
zm_transit 176/176 & 3283, common_mp 123 real techsets, zero failures):

MaterialTechnique: reorder — `name` chars are written LAST (after all pass
dynamic data), not first. Body = 8 (name ptr, u16 flags, u16 passCount)
+ passCount*24 (MaterialPass, layout as on PC minus nothing).

MaterialPass dynamic order per pass:
  1. vertexShader subtree (if FOLLOW)
  2. vertexDecl body (if FOLLOW) = 92 bytes on console, NOT 36 and NOT 116:
     +0 streamCount u8, +1 hasOptionalSource u8, +2..3 pad,
     +4 routing data[16] x {u8 source, u8 dest} (32 B), +36..91 always zero
     (runtime decl/fetch-shader space). OAT's ?36:116 conditional is WRONG.
  3. pixelShader subtree (if FOLLOW)
  4. args: argCount x MaterialShaderArgument(8). Literal-const u-ptrs were
     always aliases in genuine zones (never FOLLOW inline).

MaterialVertexShader / MaterialPixelShader = 12 bytes (PC 16):
  +0 name ptr (FOLLOW -> chars right after body, or alias)
  +4 loadDef/program ptr (FOLLOW -> inline GX2 shader struct, or alias)
  +8 u32, always 0 (PC's programSize slot; unused on console)

Inline GX2VertexShader = 308 bytes, ALL u32 BIG-endian (unlike the GX2Texture
inside GfxImage, which is LE):
  +0    GX2VertexShaderRegs, 52 words: sq_pgm_resources_vs, vgt_primitiveid_en,
        spi_vs_out_config, num_spi_vs_out_id, spi_vs_out_id[10],
        pa_cl_vs_out_cntl, sq_vtx_semantic_clear, num_sq_vtx_semantic,
        sq_vtx_semantic[32], vgt_strmout_buffer_en,
        vgt_vertex_reuse_block_cntl(=0xe), vgt_hos_reuse_depth(=0x10)
  +208  programSize u32 (real microcode sizes: ~0x190..0xb30 seen)
  +212  program ptr — always FOLLOW; microcode bytes are the FIRST dynamic data
  +216  mode u32 (always 0 = GX2_SHADER_MODE_UNIFORM_REGISTER)
  +220  uniformBlockCount/ptr   (always 0 in genuine data; rec = 12 B)
  +228  uniformVarCount/ptr     rec 20 B {name ptr, type, count, offset, block=-1}
  +236  initialValueCount/ptr   (always 0; rec 20 B)
  +244  loopVarCount/ptr        rec 8 B {offset, value}
  +252  samplerVarCount/ptr     rec 12 B {name ptr, type, location}
  +260  attribVarCount/ptr      rec 16 B {name ptr, type, count, location}
  +268  ringItemsize, +272 hasStreamOut, +276 streamOutStride[4],
  +292  gx2rBuffer[16] — all zero on disk
Inline GX2PixelShader = 232 bytes, BE:
  +0    GX2PixelShaderRegs, 41 words: sq_pgm_resources_ps, sq_pgm_exports_ps,
        spi_ps_in_control_0/1, num_spi_ps_input_cntl, spi_ps_input_cntls[32],
        cb_shader_mask, cb_shader_control, db_shader_control, spi_input_z
  +164  programSize, +168 program ptr (FOLLOW), +172 mode (0)
  +176  5 count/ptr pairs (uniformBlocks, uniformVars, initialValues,
        loopVars, samplerVars — same recs as VS; no attribVars)
  +216  gx2rBuffer[16] zero
Table dynamics: for each count/ptr pair with ptr==FOLLOW, count*recSize bytes,
then name chars appended for each record whose own name ptr is FOLLOW
(record name ptrs are frequently aliases to already-written strings).

Per-shader stream consumption:
  12 + nameChars(if name FOLLOW)
     + [if loadDef FOLLOW] gx2Size(308/232) + programSize
       + sum over FOLLOW tables (count*recSize + FOLLOW record names)

Sample: console_shader_sample.bin = full technique
pimp_technique_zfeather_5d3514a4 from mp_raid_genuine.zone 0x27f44..0x28b0d.
Full write-up: WIIU_UNLINK_STATUS.md section 0g.
"""
import struct
import sys
from collections import Counter

FOLLOW = 0xFFFFFFFF
VS_GX2_SIZE = 308
PS_GX2_SIZE = 232
VS_REGS = 208
PS_REGS = 164
VD_SIZE = 92
# (recordSize, offset-of-name-ptr-or-None) for the count/ptr tables, in order
VS_TABLES = [(12, 0), (20, 0), (20, None), (8, None), (12, 0), (16, 0)]
PS_TABLES = VS_TABLES[:5]
NAME_CHARS = set(b"abcdefghijklmnopqrstuvwxyz0123456789_/~$#&+.-")

STATS = Counter()
SIZES = {'vs': Counter(), 'ps': Counter()}


class Fail(Exception):
    pass


def u32(d, o):
    return struct.unpack('>I', d[o:o+4])[0]


def u16(d, o):
    return struct.unpack('>H', d[o:o+2])[0]


def name_run(d, o, minlen=3, maxlen=160):
    e = o
    n = len(d)
    while e < n and d[e] in NAME_CHARS and e - o <= maxlen:
        e += 1
    if e - o < minlen or e >= n or d[e] != 0:
        return None
    return d[o:e].decode('latin-1')


def ptr_ok(v):
    return v in (0, FOLLOW, 0xFFFFFFFE) or ((v - 1) >> 29) < 8


class Cur:
    def __init__(self, d, o):
        self.d = d
        self.o = o

    def u32(self):
        v = u32(self.d, self.o)
        self.o += 4
        return v

    def skip(self, n):
        self.o += n

    def cstr(self, maxlen=256):
        e = self.d.index(b'\x00', self.o)
        if e - self.o > maxlen:
            raise Fail('string too long')
        s = self.d[self.o:e]
        self.o = e + 1
        return s.decode('latin-1', 'replace')


def parse_gx2_shader(c, kind):
    d = c.d
    body = c.o
    regs = VS_REGS if kind == 'vs' else PS_REGS
    total = VS_GX2_SIZE if kind == 'vs' else PS_GX2_SIZE
    tables = VS_TABLES if kind == 'vs' else PS_TABLES
    size = u32(d, body + regs)
    prog = u32(d, body + regs + 4)
    if size == 0 or size > 0x20000:
        raise Fail('%s bad progsize 0x%x' % (kind, size))
    if prog != FOLLOW:
        raise Fail('%s program not FOLLOW' % kind)
    counts = [(u32(d, body + regs + 12 + i*8), u32(d, body + regs + 16 + i*8))
              for i in range(len(tables))]
    c.skip(total)
    c.skip(size)                       # microcode is the first dynamic datum
    for (cnt, ptr), (rsz, noff) in zip(counts, tables):
        if ptr == FOLLOW:
            if cnt == 0 or cnt > 64:
                raise Fail('%s bad table count %d' % (kind, cnt))
            base = c.o
            nfollow = 0
            if noff is not None:
                nfollow = sum(1 for i in range(cnt)
                              if u32(d, base + i*rsz + noff) == FOLLOW)
            c.skip(cnt * rsz)
            for _ in range(nfollow):
                c.cstr(96)
        elif ptr != 0 and cnt == 0:
            # linker memory garbage: table slots often hold stale non-zero
            # words with count 0 (loader ignores them) — tolerate, count
            STATS['%s table alias with count 0' % kind] += 1
    SIZES[kind][size] += 1


def parse_shader_ref(c, kind):
    name_p = c.u32()
    ld_p = c.u32()
    w3 = c.u32()
    if w3 != 0:
        STATS['%s word3 nonzero' % kind] += 1
    if name_p == FOLLOW:
        c.cstr(96)
    if ld_p == FOLLOW:
        parse_gx2_shader(c, kind)
    elif ld_p == 0:
        STATS['%s loadDef null' % kind] += 1


def parse_technique(d, t):
    name_p = u32(d, t)
    pc = u16(d, t + 6)
    if not (1 <= pc <= 8) or not ptr_ok(name_p) or name_p == 0:
        raise Fail('bad tech header @0x%x' % t)
    c = Cur(d, t + 8 + pc * 24)
    for i in range(pc):
        po = t + 8 + i * 24
        vd, vs, ps = u32(d, po), u32(d, po+4), u32(d, po+8)
        args_p = u32(d, po + 20)
        nargs = d[po+12] + d[po+13] + d[po+14]
        if vs == FOLLOW:
            parse_shader_ref(c, 'vs')
        if vd == FOLLOW:
            c.skip(VD_SIZE)
        if ps == FOLLOW:
            parse_shader_ref(c, 'ps')
        if args_p == FOLLOW:
            base = c.o
            lits = 0
            for j in range(nargs):
                atype = u16(d, base + j * 8)
                if u32(d, base + j * 8 + 4) == FOLLOW and atype in (1, 4):
                    lits += 1      # literal const inline; never seen in genuine data
            c.skip(nargs * 8 + lits * 16)
    if name_p == FOLLOW:                 # reorder: technique name written LAST
        nm = name_run(d, c.o, minlen=1)
        if nm is None:
            raise Fail('no technique name at end (tech 0x%x)' % t)
        c.skip(len(nm.encode()) + 1)
    return c.o


def parse_techset(d, o):
    slots = [u32(d, o + 8 + i*4) for i in range(32)]
    c = Cur(d, o + 136)
    # The techset name (ptr @o+0) is emitted inline (right after the 136-byte
    # body) ONLY when it FOLLOWs. When it is an ALIAS (a shared name, e.g.
    # menu/loadscreen techsets on the patch zones) there is no inline string —
    # reading one over-consumes into the first technique and desyncs the walk.
    if u32(d, o) == FOLLOW:
        c.cstr(160)
    ntech = 0
    for v in slots:
        if v == FOLLOW:
            c.o = parse_technique(d, c.o)
            ntech += 1
    return c.o, ntech


def find_techsets(d):
    """Candidate techset bodies; a shifted/false candidate fails parsing or is
    skipped by the caller's extent dedupe."""
    out = []
    o = d.find(b'\xff\xff\xff\xff')
    n = len(d)
    while o != -1 and o + 336 < n:
        nm = name_run(d, o + 136, minlen=4)
        if nm and u32(d, o) == FOLLOW:
            slots = [u32(d, o + 8 + i*4) for i in range(32)]
            if all(ptr_ok(v) for v in slots) and any(v == FOLLOW for v in slots):
                # pre-validate: first FOLLOW technique header must be sane
                t = o + 136 + len(nm.encode()) + 1
                np, pc = u32(d, t), u16(d, t + 6)
                if (ptr_ok(np) and np != 0 and 1 <= pc <= 8
                        and all(ptr_ok(u32(d, t + 8 + i*24 + j)) and
                                u32(d, t + 8 + i*24 + 4) != 0
                                for i in range(pc) for j in (0, 4, 8, 20))):
                    out.append((o, nm))
        o = d.find(b'\xff\xff\xff\xff', o + 4)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'mp_raid_genuine.zone'
    d = open(path, 'rb').read()
    cands = find_techsets(d)
    ok = bad = skipped = 0
    fails = Counter()
    last_end = 0
    ntechs = 0
    for (o, nm) in cands:
        if o < last_end:        # inside an already-parsed techset (shifted dup)
            skipped += 1
            continue
        try:
            end, nt = parse_techset(d, o)
            nx = u32(d, end)
            if nx != 0 and ptr_ok(nx):   # next asset (FOLLOW or alias name)
                ok += 1
                ntechs += nt
                last_end = end
            else:
                bad += 1
                fails['resync miss'] += 1
        except (Fail, ValueError, IndexError) as e:
            bad += 1
            fails[str(e).split('@')[0][:40]] += 1
    print("zone=%s" % path)
    print("techsets OK=%d bad=%d (dup/nested skipped=%d), techniques=%d" %
          (ok, bad, skipped, ntechs))
    print("fail reasons:", dict(fails.most_common(8)))
    print("stats:", dict(STATS))
    print("vs programSize top:", SIZES['vs'].most_common(5), "n=%d" % sum(SIZES['vs'].values()))
    print("ps programSize top:", SIZES['ps'].most_common(5), "n=%d" % sum(SIZES['ps'].values()))


if __name__ == '__main__':
    main()
