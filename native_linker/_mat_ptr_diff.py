"""Material pointer-field diff: pipeline (crashing) vs gfxtail46 (answer key).
Both zones same layout -> same offset = same field. We enumerate EVERY Material
body (2 top-level MATERIAL assets + sunflare inline + all inline materials in the
GfxWorld materialMemory region) with the VALIDATED _matconst_map.walk_material,
collect the genuine pointer-field stream offsets, then diff ONLY those words.
"""
import sys, struct, collections
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import _matconst_map as MM
import shader_probe as SP
import xmodel_probe as XP

PIPE = 'mp_skate_pipecheck.zone'
KEY  = 'mp_skate_gfxtail46.zone'
PC_PATH = '../mp_skate_pc.zone'
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]

# ---- pointer-field-recording material walk (mirrors MM.walk_material) --------
def walk_material_ptrfields(Z, off):
    """Return (list_of (abs_off, field_label), next_off) for one console Material."""
    fields = []
    c = XP.Cur(Z, off)
    c.skip(MM.CO_MAT_SIZE)
    texc = Z[off + 72]; constc = Z[off + 73]; sbc = Z[off + 74]
    namep = be32(Z, off + 0)
    tsp   = be32(Z, off + 80)
    ttp   = be32(Z, off + 84)
    ctp   = be32(Z, off + 88)
    sbp   = be32(Z, off + 92)
    thp   = be32(Z, off + 96)
    # the 6 fixed pointer fields (always genuine pointers)
    fields.append((off + 0,  'name'))
    fields.append((off + 80, 'techSet'))
    fields.append((off + 84, 'textureTable'))
    fields.append((off + 88, 'constantTable'))
    fields.append((off + 92, 'stateBits'))
    fields.append((off + 96, 'thermal'))
    if namep in PTRS:
        c.cstr()
    if tsp in PTRS:                       # inline techset asset FOLLOWS
        c.o, _ = SP.parse_techset(Z, c.o)
    if ttp in PTRS:
        defs = c.o
        c.skip(texc * 16)
        for i in range(texc):
            imgp = be32(Z, defs + i * 16 + 12)
            fields.append((defs + i * 16 + 12, 'texdef[%d].image' % i))
            if imgp in PTRS:
                XP.consume_image(Z, c)
    if ctp in PTRS:
        c.skip(constc * MM.CONSTDEF)
    if sbp in PTRS:
        c.skip(sbc * MM.CO_SB)
    if thp in PTRS:                       # inline thermal material FOLLOWS
        subfields, c.o = walk_material_ptrfields(Z, c.o)
        fields.extend(subfields)
    return fields, c.o

# ---- enumerate every Material body's start offset in the console zone --------
def material_offsets(ZKEY):
    """Uses KEY zone layout (identical to pipe). Returns list of (start_off, source)."""
    em, spans, CO = LS.simulate(KEY, verbose=False)
    offs = []
    # (1) top-level MATERIAL assets
    for (i, ename, root, s, e) in spans:
        if root == 'Material' and e > s:
            offs.append((s, 'toplevel#%d' % i))
    gw = [(s, e) for (i, nm, root, s, e) in spans if root == 'GfxWorld'][0]
    gwo_console = gw[0]
    # (2) locate materialMemory region inside GfxWorld body by re-emitting from PC
    import gfxworld_emit as GEM
    pc = open(PC_PATH, 'rb').read()
    # find PC GfxWorld body offset
    import produce_nobackbone as PN
    bodies, _ = PN.walk_pc_bodies(pc)
    pcgw = [(s) for (i, nm, root, s, e, hp) in bodies if root == 'GfxWorld'][0]
    data, fx, log = GEM.emit_gfxworld(pc, pcgw, ctx={'image_source': None,
                                                     'sampler_lookup': None,
                                                     'defer_tail_rebase': True})
    total = sum(ln for (_, _, ln, _) in log)
    mm_off_in_body = None
    acc = 0
    for (key, method, ln, note) in log:
        if key == 'materialMemory':
            mm_off_in_body = acc
        acc += ln
    nmm = struct.unpack_from('<I', pc, pcgw + 572)[0]
    info = dict(gwo_console=gwo_console, gw_span_len=gw[1] - gw[0],
                emit_total=total, mm_off_in_body=mm_off_in_body, nmm=nmm,
                emit_matches_span=(total == gw[1] - gw[0]))
    # console mm region start
    mm_start = gwo_console + mm_off_in_body
    # entries: which have inline bodies (ptr in PTRS)
    ZK = open(KEY, 'rb').read()
    n_inline = 0
    inline_flags = []
    for i in range(nmm):
        ptr = be32(ZK, mm_start + i * 8)
        isinl = ptr in PTRS
        inline_flags.append(isinl)
        if isinl:
            n_inline += 1
    info['n_inline_entries'] = n_inline
    # walk inline bodies sequentially
    cur = mm_start + nmm * 8
    idx = 0
    for i in range(nmm):
        if inline_flags[i]:
            offs.append((cur, 'matmem#%d' % i))
            try:
                _, cur = walk_material_ptrfields(ZK, cur)
            except Exception as ex:
                info['walk_error'] = 'matmem#%d @%d: %s' % (i, cur, ex)
                break
            idx += 1
    info['inline_walked'] = idx
    return offs, info, ZK

def classify(v):
    if v == 0: return 'null'
    if v == FOLLOW: return 'FOLLOW'
    if v == INSERT: return 'INSERT'
    if 0xBF000000 <= v <= 0xBF00FFFF: return 'poison'
    if 0xA0000000 <= v <= 0xBFFFFFFF: return 'b5-alias'
    if 0xC0000000 <= v <= 0xDFFFFFFF: return 'block-6'
    if 0xE0000000 <= v <= 0xFFFFFFFD: return 'block-7'
    return 'wild'

def main():
    offs, info, ZK = material_offsets(KEY)
    ZP = open(PIPE, 'rb').read()
    print('INFO', info)
    print('total material bodies enumerated:', len(offs))
    # collect all pointer fields from KEY layout
    allfields = []
    for (start, src) in offs:
        try:
            flds, _ = walk_material_ptrfields(ZK, start)
        except Exception as ex:
            print('WALK FAIL', src, start, ex); continue
        for (o, lbl) in flds:
            allfields.append((o, lbl, src, start))
    print('total pointer fields:', len(allfields))
    # diff
    mism = []
    for (o, lbl, src, start) in allfields:
        vk = be32(ZK, o); vp = be32(ZP, o)
        if vk != vp:
            mism.append((o, lbl, src, start, vp, vk, classify(vp), classify(vk)))
    print('pointer-field mismatches:', len(mism))
    # crash-causing = pipeline worse: key is resolvable, pipe is not
    GOOD = ('b5-alias', 'FOLLOW', 'INSERT')
    BAD  = ('block-7', 'poison', 'wild', 'null', 'block-6')
    crash = [m for m in mism if m[7] in GOOD and m[6] in BAD]
    print('CRASH-CAUSING (key=%s / pipe=%s):' % (GOOD, BAD), len(crash))
    # family by (pipe_class -> key_class, field label kind)
    fam = collections.Counter()
    for (o, lbl, src, start, vp, vk, cp, ck) in crash:
        lblkind = lbl.split('[')[0]
        fam[(cp, ck, lblkind)] += 1
    print('\n=== CRASH FAMILIES (pipe_class -> key_class, field) ===')
    for k, c in fam.most_common():
        print('  %4d  %-10s -> %-9s  %s' % (c, k[0], k[1], k[2]))
    print('\n=== sample crash rows ===')
    for m in crash[:40]:
        (o, lbl, src, start, vp, vk, cp, ck) = m
        print('  off=%d %-16s %-14s pipe=0x%08x(%s) key=0x%08x(%s)' %
              (o, lbl, src, vp, cp, vk, ck))
    # also non-crash mismatches summary (both alias etc = benign)
    other = [m for m in mism if m not in crash]
    ofam = collections.Counter((m[6], m[7], m[1].split('[')[0]) for m in other)
    print('\n=== OTHER (non-crash) mismatch families ===')
    for k, c in ofam.most_common(20):
        print('  %4d  %-10s -> %-9s  %s' % (c, k[0], k[1], k[2]))
    # dump crash rows for structured output
    import pickle
    pickle.dump(dict(info=info, crash=crash, mism_count=len(mism),
                     nfields=len(allfields), nmats=len(offs), fam=dict(fam)),
                open('_mat_ptr_diff_out.pkl', 'wb'))

if __name__ == '__main__':
    main()
