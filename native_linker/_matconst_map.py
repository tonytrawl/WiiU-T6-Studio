"""Family 9: build the real material <-> techset <-> constant map for a console zone.

- inline Materials live in the GfxWorld materialMemory region (entries 8B {Material*, mem},
  then inline material bodies). Console Material body = 104B: name*@0, texc@72, constc@73,
  sbc@74, ts*@80, tt*@84, ct*@88, sbt*@92, th*@96; trailing = name, texdefs, constantTable
  (32B each), stateBits(8B), [inline thermal material].
- MaterialTechniqueSet assets are TOP-LEVEL. technique = {name*@0, u16 flags@4, u16 passCount@6},
  passes at t+8 (24B: vd@0 vs@4 ps@8 perPrim@12 perObj@13 stable@14 args*@20),
  arg = 8B {u16 type, u16 dest, u32 value}. type-6 = material-constant-by-nameHash
  (the unbounded constantTable search that crashes -> family 9).

The technique walk below MIRRORS shader_probe.parse_technique but also records each pass's
ARGS file offset (parse_technique skips it internally).
"""
import sys, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import shader_probe as SP

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
CO_MAT_SIZE = 104
CONSTDEF = 32
CO_SB = 8
ARG_CONST_HASH = 6          # arg type that triggers the material constantTable hash search


def be32(d, o):
    return struct.unpack_from('>I', d, o)[0]


def be16(d, o):
    return struct.unpack_from('>H', d, o)[0]


def walk_technique(d, t):
    """Mirror of SP.parse_technique that also returns each pass's args offset.
    Returns (end, [ {pass_off, args_off, counts:(p,o,s), nargs} ])."""
    name_p = be32(d, t)
    pc = be16(d, t + 6)
    if not (1 <= pc <= 8) or not SP.ptr_ok(name_p) or name_p == 0:
        raise SP.Fail('bad tech header @0x%x' % t)
    c = SP.Cur(d, t + 8 + pc * 24)
    passes = []
    for i in range(pc):
        po = t + 8 + i * 24
        vd, vs, ps = be32(d, po), be32(d, po + 4), be32(d, po + 8)
        args_p = be32(d, po + 20)
        p, o_, st = d[po + 12], d[po + 13], d[po + 14]
        nargs = p + o_ + st
        if vs == FOLLOW:
            SP.parse_shader_ref(c, 'vs')
        if vd == FOLLOW:
            c.skip(SP.VD_SIZE)
        if ps == FOLLOW:
            SP.parse_shader_ref(c, 'ps')
        if args_p == FOLLOW:
            base = c.o
            lits = 0
            for j in range(nargs):
                atype = be16(d, base + j * 8)
                if be32(d, base + j * 8 + 4) == FOLLOW and atype in (1, 4):
                    lits += 1
            c.skip(nargs * 8 + lits * 16)
            passes.append(dict(pass_off=po, args_off=base, counts=(p, o_, st),
                               nargs=nargs, lits=lits))
    if name_p == FOLLOW:
        nm = SP.name_run(d, c.o, minlen=1)
        if nm is None:
            raise SP.Fail('no technique name at end (tech 0x%x)' % t)
        c.skip(len(nm.encode()) + 1)
    return c.o, passes


def walk_techset(d, s):
    """[pass dicts] for a console MaterialTechniqueSet asset body at `s`.
    MIRRORS SP.parse_techset: the techset name is emitted inline right after the
    136-byte body ONLY when it FOLLOWs (aliased names have no inline string —
    reading one desyncs the walk). Only slots == FOLLOW are techniques."""
    slots = [be32(d, s + 8 + i * 4) for i in range(32)]
    c = SP.Cur(d, s + 136)
    if be32(d, s) == FOLLOW:
        c.cstr(160)
    out = []
    for v in slots:
        if v == FOLLOW:
            end, passes = walk_technique(d, c.o)
            out.extend(passes)
            c.o = end
    return out, c.o


def techset_const_hashes(d, s):
    """set of type-6 arg nameHashes demanded by this techset."""
    passes, _ = walk_techset(d, s)
    hs = set()
    for p in passes:
        for j in range(p['nargs']):
            a = p['args_off'] + j * 8
            if be16(d, a) == ARG_CONST_HASH:
                hs.add(be32(d, a + 4))
    return hs, passes


def walk_material(Z, off):
    """CORRECT console Material walk -- mirrors the VALIDATED xmodel_probe.consume_material.

    *** USE THIS, NOT parse_material. *** parse_material (below) is BROKEN: it ignores both
    inline GfxImages and inline techsets, so for any material carrying one it computes ct_off
    SHORT and reads garbage "constants". That silently dropped `wpc/skt_video_screen_lrg` (3 of
    its 4 texdefs are FOLLOW => 3 inline images => ct_off was 1034 bytes short) from every audit,
    which is how boot 24's crash slipped through a "0 unsatisfied" gate.

    The two rules parse_material missed (xmodel_probe.consume_material):
        texdef.image* (texdef+12) in PTRS  -> an inline GfxImage FOLLOWS  (328 + name + pixels)
        Material.techSet* (+80)   in PTRS  -> an inline techset asset FOLLOWS

    Returns (info, next_off); info['consts'] / info['ct_off'] are trustworthy.
    """
    import xmodel_probe as XP
    b = off
    c = XP.Cur(Z, off)
    c.skip(CO_MAT_SIZE)
    texc, constc, sbc = Z[b + 72], Z[b + 73], Z[b + 74]
    tsp, ttp, ctp, sbp = (be32(Z, b + 80), be32(Z, b + 84), be32(Z, b + 88), be32(Z, b + 92))
    thermal = be32(Z, b + 96)
    name = None
    if be32(Z, b) in PTRS:
        name = c.cstr()
    if tsp in PTRS:                       # inline techset asset
        c.o, _ = SP.parse_techset(Z, c.o)
    if ttp in PTRS:
        defs = c.o
        c.skip(texc * 16)
        for i in range(texc):
            if be32(Z, defs + i * 16 + 12) in PTRS:
                XP.consume_image(Z, c)    # <-- the step parse_material omits
    consts, ct_off = [], None
    if ctp in PTRS:
        ct_off = c.o
        consts = [be32(Z, ct_off + i * CONSTDEF) for i in range(constc)]
        c.skip(constc * CONSTDEF)
    if sbp in PTRS:
        c.skip(sbc * CO_SB)
    if thermal in PTRS:
        _, c.o = walk_material(Z, c.o)
    return dict(off=off, name=name, ts=tsp, constc=constc, consts=consts,
                ct_off=ct_off, texc=texc), c.o


def parse_material(Z, off, texdef=16):
    """DEPRECATED / BROKEN -- see walk_material. Ignores inline images and inline techsets, so
    ct_off/consts are WRONG for any material that carries either. Kept only so the already-applied
    _patch_gfxtail14/17/20 scripts still reproduce their historical behaviour. Do not use in new
    audits. Walk one console Material at `off`. Returns (info, next_off)."""
    name_p = be32(Z, off + 0)
    texc, constc, sbc = Z[off + 72], Z[off + 73], Z[off + 74]
    ts, tt, ct, sbt, th = (be32(Z, off + 80), be32(Z, off + 84), be32(Z, off + 88),
                           be32(Z, off + 92), be32(Z, off + 96))
    src = off + CO_MAT_SIZE
    name = None
    if name_p in PTRS:
        e = Z.index(b'\x00', src)
        name = Z[src:e].decode('latin-1', 'replace'); src = e + 1
    if tt in PTRS:
        src += texc * texdef
    consts = []; ct_off = None
    if ct in PTRS:
        ct_off = src
        consts = [be32(Z, src + i * CONSTDEF) for i in range(constc)]
        src += constc * CONSTDEF
    if sbt in PTRS:
        src += sbc * CO_SB
    if th in PTRS:
        _, src = parse_material(Z, src, texdef)
    return dict(off=off, name=name, ts=ts, constc=constc, consts=consts,
                ct_off=ct_off), src
