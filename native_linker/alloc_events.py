#!/usr/bin/env python3
"""
ALLOCATION-EVENT walkers (Track G runtime/interior model,
HANDOFF_assemble_runtime_interior_model.md item 1).

For the big delimiter-walked assets (clipMap_t / GameWorldMp / SndBank) the
loader makes MANY interior allocations, each aligning the RUNTIME cursor
(stream stays packed), and some allocations are RUNTIME blocks consuming
virtual space with NO file bytes (GWMP basenodes, clipMap dynEnt lists).
The old verbatim register-once dispatch modeled those assets as one linear
region, which is why genuine alias values drift (~933.7K at raid GWMP).

Each walker returns (end_abs, events) where events are RELATIVE to the asset
body start:
    ('seg',  rel_off, size, align)   file bytes, one loader allocation
    ('skip', size, align)            runtime allocation, no file bytes
Because PC and console serialize byte-identically, ONE walker (endian-
parametrized) serves the PC sim, the genuine-console sim, and our-stream sim.

Alignments follow the calibrated loader model (loader_sim): allocations align
min(type_align, 4); u16 arrays 2; strings/byte blobs 1.
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)


TRACE = None        # set to a list to collect (abs_off, size, label)

# PTRACE (2026-08-08, pc-map session): a SECOND, fully PARALLEL trace channel
# that records every FIELD OFFSET the walkers test for pointer-ness. It exists
# because a field holding a dedup ALIAS (0xA0..0xBF) fails the `in PTRS` test and
# is skipped, so the set of pointer FIELDS cannot be recovered from the emitted
# events — only from the tests themselves. Needed by the PC-map dense anchor
# probe, whose content-sweep form is unusable (XModel interiors are mostly vertex
# data; the float-phantom law gives ~755K false candidates).
#
# ⚠ CONTRACT: this is ADDITIVE ONLY. It never modifies, reorders, or suppresses
# an emitted event, and the ('seg'/'temp'/'skip', ...) tuple SHAPES are untouched.
# With PTRACE = None (the default) `_pt` is a plain read and the walkers behave
# byte-identically to alloc_events.py.pre_ptrace. Same pattern as CLIPMAP_TRACE.
PTRACE = None       # set to a list to collect (abs_off, value, label)

# XM_OPTS (2026-08-08, pc-map session, candidate sweep (M)): ADDITIVE structural
# knobs for the PC XModel interior walk. Empty dict (the DEFAULT) reproduces the
# previous behaviour EXACTLY. Keys change only the ALIGN of emitted events (and
# hence virtual padding) — never stream consumption, span ends, or event SHAPES.
#   {'align': {name: n}, 'cstr_align': n}
XM_OPTS = {}


def _al(name, default):
    """Align for a labelled allocation under the current XM_OPTS."""
    return XM_OPTS.get('align', {}).get(name, default)


def _pt(c, off, label):
    """Read a pointer FIELD at absolute offset `off`, recording it in PTRACE.
    Returns the raw value so callers keep using `... in PTRS` unchanged."""
    v = c.u32(off)
    if PTRACE is not None:
        PTRACE.append((off, v, label))
    return v


class Ev:
    """Event collector with a stream cursor; rel offsets vs base."""
    def __init__(self, d, base, e):
        self.d = d
        self.base = base
        self.o = base
        self.e = e
        self.events = []

    def u32(self, o):
        return struct.unpack_from(self.e + 'I', self.d, o)[0]

    def u16(self, o):
        return struct.unpack_from(self.e + 'H', self.d, o)[0]

    def i16(self, o):
        return struct.unpack_from(self.e + 'h', self.d, o)[0]

    def seg(self, size, align, label=''):
        if size > 0:
            # CLIPMAP_TRACE (2026-08-08): the events tuple SHAPE IS UNCHANGED —
            # rt/loader_sim consumers unpack 4-tuples and must keep working.
            # The label goes to a separate module-level list instead, so this
            # walker can double as a structural REGISTRY (abs offset + size +
            # which array) without a second copy of the walk existing.
            if TRACE is not None and label:
                TRACE.append((self.o, size, label))
            self.events.append(('seg', self.o - self.base, size, align))
            self.o += size

    def temp(self, size):
        """File bytes that load into the TEMP block (roots of inline assets,
        per the T6 load db: MapEnts/PhysPreset LoadPtr push TEMP on native
        console): the stream advances, the VIRTUAL cursor does not.
        ('temp', rel_off, size)."""
        if size > 0:
            self.events.append(('temp', self.o - self.base, size))
            self.o += size

    def skip(self, size, align):
        if size > 0:
            self.events.append(('skip', size, align))

    def cstr(self, align=1):
        e = self.d.index(b'\x00', self.o)
        self.seg(e + 1 - self.o, align)

    def cstr_span(self):
        """Extent of a NUL-terminated string without emitting (caller merges)."""
        return self.d.index(b'\x00', self.o)


def _merge(events):
    """Coalesce adjacent seg events that are runtime-equivalent (align-1 seg
    directly following the previous seg's end): fewer events, same model."""
    out = []
    for ev in events:
        if (ev[0] == 'seg' and out and out[-1][0] == 'seg' and ev[3] == 1
                and out[-1][1] + out[-1][2] == ev[1]):
            out[-1] = ('seg', out[-1][1], out[-1][2] + ev[2], out[-1][3])
        else:
            out.append(list(ev) if ev[0] == 'seg' else ev)
    return [tuple(ev) if isinstance(ev, list) else ev for ev in out]


# =====================================================================
# GameWorldMp (gameworldmp_probe layout; PC-identical, node 144)
# =====================================================================
def gwmp_events(d, b, e, node_size=144, root_size=44):
    c = Ev(d, b, e)
    (nodeCount, orig, nodes_p, base_p, visBytes, vis_p, smoothBytes,
     smooth_p, treeCount, tree_p) = struct.unpack_from(e + '10I', d, b + 4)
    c.seg(root_size, 4)                       # root struct (temp on top level)
    if c.u32(b) in PTRS:                      # name string
        c.cstr()
    if nodes_p in PTRS:
        n = nodeCount + 128
        nbase = c.o
        c.seg(n * node_size, 4)               # ONE allocation for the array
        for i in range(n):
            nb = nbase + i * node_size
            total_links = c.u16(nb + 60)
            if c.u32(nb + 64) in PTRS:
                c.seg(total_links * 16, 4)    # pathlink_s array per node
    # basenodes ON CONSOLE ('>'): NOT a block-5 allocation.  Load_PathData
    # (guest 0x021c78e4) brackets the basenodes gate in DB_PushStreamPos(1)
    # ... DB_PopStreamPos (guest 0x021c7b30 `li r3,1; bl DB_PushStreamPos`;
    # ALLOC(0xf) + Load_Stream(1, basenodes, (nodeCount+0x80)<<4) at
    # +0x288..+0x290; Pop at 0x021c7b78) -- block 1 has its OWN cursor, so
    # this costs ZERO block-5 space and consumes ZERO file bytes.  The old
    # model charged it as a blk5 'skip' of (nodeCount+128)*16, over-allocating
    # every zone by exactly that (raid 14,048 / dockside 21,616 / transit
    # 91,392) -- measured 2026-07-30 by the FX-alias oracle campaign as the
    # residual step between the GWMP tree anchors and the SndBank name anchors
    # (raid -8,192 -> -22,240, dockside -16,384 -> -38,000); deleting it made
    # raid's blockSize[5] oracle 0 EXACT.  Do NOT re-add the console charge.
    #
    # basenodes ON PC ('<'): the charge STAYS.  The PC-side model exists only
    # to invert the PC linker's MINTED alias values (simulate_pc -> InverseMap
    # -> omap.reloc), and the whole baked PC calibration chain was fitted WITH
    # this charge in place: raid_oracle_control.PC_POLICY/DOCK_PC_POLICY's
    # gfx_residual_pc is anchored on the GWMP TREE PLATEAU itself (E@gfx-end
    # from the tree aliases, ADDENDUM 8 recipe), and pre_skip_pc/lump are
    # constant deltas downstream of it.  Deleting the '<' charge together with
    # the console one (gwmp nodeTree campaign, 2026-07-30) shifted EVERY
    # post-basenodes PC inversion +14,048 on raid: measured pc_inv.stream(
    # tree child_b) - true_pc_target = +14,032 = 14,048 - 16 uniformly on all
    # 258 split nodes (the -16 is the known one-node-low emit bias FIX 4
    # corrects), which threw the emitted children past the tree array into
    # neighbouring assets and broke produce_container FIX 4 with 258/258
    # 'other'.  Whether the PC linker truly charges basenodes or the fitted
    # knobs absorb it is unobservable (no PC alias family targets the
    # [gfx-end, basenodes) window) and irrelevant: (knob, charge) is the
    # calibrated PAIR.  Re-derive gfx_residual_pc under a no-charge model
    # before ever touching this branch.
    if base_p in PTRS:
        if e == '<':
            c.skip((nodeCount + 128) * 16, 4)  # (nodeCount+0x80)<<4, 16-B recs
        else:
            # MERGE FENCE (same campaign): zero-cost structural marker at the
            # SAME event position as the PC skip.  _merge coalesces an align-1
            # seg into the preceding seg only when nothing interposes, so a
            # '<'-only skip left the PC list with links|skip|vis+smooth while
            # '>' merged links+vis+smooth into ONE seg -> seg counts 1010 vs
            # 1009 -> _event_fine's +1 trim (meant for SndBank's trailing PC
            # pad) silently absorbed it and zip-paired every seg after the
            # last pathlink off by one; the pc tree-array start then mapped
            # into a wrong 4-byte fine region and split node 0's second child
            # emitted +one-tree-size (measured: the FIX 4 'other' histogram
            # {-16: 257, +8256: 1}).  size 0 + align 1 = replay_events does
            # NOTHING (no align call, +0 bytes): the disassembly-proven
            # console accounting is untouched; only the post-merge seg
            # STRUCTURE matches the PC side again, as it did when both sides
            # carried the skip.
            c.events.append(('skip', 0, 1))
    if vis_p in PTRS:
        c.seg(visBytes, 1)
    if smooth_p in PTRS:
        c.seg(smoothBytes, 1)
    if tree_p in PTRS:
        _gwmp_tree_array(c, treeCount)
    return c.o, _merge(c.events)


def _gwmp_tree_array(c, count):
    tbase = c.o
    c.seg(count * 16, 4)
    for i in range(count):
        _gwmp_tree_dyn(c, tbase + i * 16)


def _gwmp_tree_dyn(c, tb):
    axis = struct.unpack_from(c.e + 'i', c.d, tb)[0]
    if axis < 0:
        cnt = c.u32(tb + 8)
        if c.u32(tb + 12) in PTRS:
            c.seg(cnt * 2, 2)                 # u16 leaf node list
    else:
        for k in (8, 12):
            if c.u32(tb + k) in PTRS:
                _gwmp_tree_array(c, 1)


# =====================================================================
# SndBank (sndbank_probe layout; PC-identical)
# =====================================================================
def sndbank_events(d, b, e, body=4756, head_align=4):
    c = Ev(d, b, e)
    (name_p, aliasCount, alias_p, aliasIndex_p, radverbCount, radverbs_p,
     duckCount, ducks_p) = struct.unpack_from(e + '8I', d, b)
    c.seg(body, 4)
    if name_p in PTRS:
        c.cstr()
    if alias_p in PTRS:
        lbase = c.o
        c.seg(aliasCount * 20, 4)             # SndAliasList array
        for i in range(aliasCount):
            lb = lbase + i * 20
            lname_p, lid, head_p, cnt, seq = struct.unpack_from(
                e + '5I', d, lb)
            if lname_p in PTRS:
                c.cstr()
            if head_p in PTRS:
                ab = c.o
                c.seg(cnt * 100, head_align)  # SndAlias array
                for k in range(cnt):
                    a = ab + k * 100
                    for po in (a + 0, a + 8, a + 12, a + 20):
                        if c.u32(po) in PTRS:
                            c.cstr()
    if aliasIndex_p in PTRS:
        c.seg(aliasCount * 4, 4)
    if radverbs_p in PTRS:
        c.seg(radverbCount * 100, 4)
    if ducks_p in PTRS:
        dbase = c.o
        c.seg(duckCount * 76, 4)
        for i in range(duckCount):
            db = dbase + i * 76
            for po in (db + 64, db + 68):
                if c.u32(po) in PTRS:
                    c.seg(32 * 4, 4)          # attenuation/filter f32[32]
    for po in range(32, 0x126c, 4):           # runtime-bank zone/language strs
        if c.u32(b + po) == FOLLOW:
            c.cstr()
    entryCount = c.u32(b + 0x1270)
    dataSize = c.u32(b + 0x1278)
    if c.u32(b + 0x1274) == FOLLOW:
        c.seg(entryCount * 20, 4)             # SndAssetBankEntry
    if c.u32(b + 0x127c) == FOLLOW:
        c.seg(dataSize, 1)                    # inline loadedAssets data blob
    cnt = c.u32(b + 0x1280)
    if c.u32(b + 0x1284) == FOLLOW:
        c.seg(cnt * 8, 4)
    if e == '<':
        # PC-only: zero stream padding after the inline data blob, up to the
        # next asset body (sndbank_pc finding). Kept as file bytes (1:1).
        o = c.o
        n = len(d)
        while o < n and d[o] == 0:
            o += 1
        c.seg(o - c.o, 1)
    return c.o, _merge(c.events)


# =====================================================================
# clipMap_t (clipmap_probe layout; PC-identical, root 332)
# =====================================================================
_SIZES = dict(cLeafBrushNode_s=20, cbrush_t=96, cStaticModel_s=84,
              cLeaf_s=44, CollisionPartition=16, CollisionAabbTree=32,
              cmodel_t=76)

# runtime dynEnt element sizes (bytes of RUNTIME block per element).
# Solved from genuine anchors (handoff item 4); 0 = allocates nothing / in a
# non-virtual block until the anchors say otherwise.
DYNENT_RT = dict(pose=0, client=0, server=0, coll=0, rope=0, lump=0)


def clipmap_events(d, b, e, mat_span=None, dynent_rt=None, sizes=None):
    S = sizes or _SIZES
    R = dict(DYNENT_RT); R.update(dynent_rt or {})
    c = Ev(d, b, e)
    g = lambda off: c.u32(b + off)
    c.seg(332, 4, 'root')                      # root (332: incl triIndices)
    if g(0) in PTRS:
        c.cstr()                              # name
    if g(12) in PTRS:
        c.seg(g(8) * 20, 4, 'planes')         # planes
    nm = g(16)
    if g(20) in PTRS:                         # materials + inline names
        mb = c.o
        c.seg(nm * 12, 4, 'materials')
        for i in range(nm):
            if c.u32(mb + i * 12) in PTRS:
                c.cstr()
    if g(28) in PTRS:                         # brushsides + inline planes
        sb = c.o
        c.seg(g(24) * 12, 4, 'brushsides')
        for i in range(g(24)):
            if c.u32(sb + i * 12) in PTRS:
                c.seg(20, 4)
    if g(36) in PTRS:                         # leafbrushNodes + leafBrush u16
        lb = c.o
        c.seg(g(32) * S['cLeafBrushNode_s'], 4, 'leafbrushNodes')
        for i in range(g(32)):
            no = lb + i * S['cLeafBrushNode_s']
            cnt = c.i16(no + 2)
            if cnt > 0 and c.u32(no + 8) in PTRS:
                c.seg(cnt * 2, 2)
    if g(44) in PTRS:
        c.seg(g(40) * 2, 2, 'leafbrushes')    # leafbrushes u16
    if g(52) in PTRS:
        c.seg(g(48) * 12, 4, 'brushVerts')    # brushVerts vec3
    if g(60) in PTRS:
        c.seg(g(56) * 2, 2, 'uinds')          # uinds u16
    nbr = c.u16(b + 64)
    if g(68) in PTRS:
        c.seg(nbr * S['cbrush_t'], 128, 'brushes')  # AllocOutOfBlock<cbrush_array_t>(128)
    if g(72) in PTRS:
        c.seg(nbr * 24, 4, 'brushBounds')
    if g(76) in PTRS:
        c.seg(nbr * 4, 4, 'brushContents')
    if g(80) in PTRS:
        c.seg(72, 4)                          # pInfo inline
    if g(88) in PTRS:
        c.seg(g(84) * S['cStaticModel_s'], 4, 'staticModels')
    if g(96) in PTRS:
        c.seg(g(92) * 8, 4, 'nodes')          # cNode_t
    if g(104) in PTRS:
        c.seg(g(100) * S['cLeaf_s'], 4, 'leafs')
    if g(112) in PTRS:
        c.seg(g(108) * 12, 4, 'verts')        # verts
    tc = g(116)
    if g(120) in PTRS:
        c.seg(tc * 6, 2, 'triIndices')        # triIndices u16[3]
    if g(124) in PTRS:
        c.seg(((3 * tc + 31) // 32) * 4, 1, 'triEdgeIsWalkable')  # triEdgeIsWalkable: Alloc<char>(1)
    if g(132) in PTRS:
        c.seg(g(128) * S['CollisionPartition'], 4, 'partitions')
    if g(140) in PTRS:
        c.seg(g(136) * S['CollisionAabbTree'], 16, 'aabbTrees')  # Alloc<CollisionAabbTree>(16)
    if g(148) in PTRS:
        c.seg(g(144) * S['cmodel_t'], 4, 'cmodels')
    if g(160) in PTRS:
        c.seg(g(152) * g(156), 1, 'visibility')  # visibility bytes
    if g(168) in PTRS:                        # inline MapEnts asset
        if g(168) == INSERT:
            c.skip(4, 4)                      # InsertPointerAliasLookup slot
        me = c.o
        c.temp(36)                            # MapEnts root: TEMP (asset loader)
        if c.u32(me) in PTRS:
            c.cstr()
        if c.u32(me + 4) in PTRS:
            c.seg(c.u32(me + 8), 1)           # entityString
        for cnt_o, ptr_o, rsz in ((12, 16, 8), (20, 24, 32), (28, 32, 20)):
            if c.u32(me + ptr_o) in PTRS:
                c.seg(c.u32(me + cnt_o) * rsz, 4)
    if g(172) in PTRS:
        c.seg(96, 16)                         # box_brush: AllocOutOfBlock(16)
    # dynEnt defs + runtime lists (probe tail order)
    counts = [c.u16(b + 254 + i * 2) for i in range(4)]
    for i, off in enumerate((264, 268)):
        cnt = counts[i]
        if g(off) in PTRS:
            defbase = c.o
            c.seg(cnt * 84, 4)                # DynEntityDef array
            for j in range(cnt):
                db = defbase + j * 84
                for o2 in (32, 36, 44, 52):
                    if c.u32(db + o2) in PTRS:
                        raise RuntimeError('inline dynEnt sub-asset @+%d' % o2)
                if c.u32(db + 56) in PTRS:    # inline physPreset (asset)
                    if c.u32(db + 56) == INSERT:
                        c.skip(4, 4)          # insert-slot (virtual)
                    pp = c.o
                    c.temp(84)                # PhysPreset root: TEMP
                    if c.u32(pp + 0) in PTRS:
                        c.cstr()
                    if c.u32(pp + 28) in PTRS:
                        c.cstr()
    # RUNTIME lists (no file bytes): pose/client [2] by counts[0..1],
    # server [2] by counts[0..1], coll [4] by counts[0..3]
    for i in (0, 1):
        c.skip(counts[i] * R['pose'], 4)
    for i in (0, 1):
        c.skip(counts[i] * R['client'], 4)
    for i in (0, 1):
        c.skip(counts[i] * R['server'], 4)
    for i in (0, 1, 2, 3):
        c.skip(counts[i] * R['coll'], 4)
    if R['lump']:                             # unmodeled runtime total (fit;
        c.events.append(('skip', R['lump'], 4))  # may be NEGATIVE — dockside -492)
    if g(316) in PTRS:                        # constraints
        ncon = g(312)
        conbase = c.o
        c.seg(ncon * 168, 4)
        for j in range(ncon):
            cb = conbase + j * 168
            if c.u32(cb + 20) in PTRS:
                c.cstr()
            if c.u32(cb + 36) in PTRS:
                c.cstr()
            if c.u32(cb + 140) in PTRS:
                if mat_span is None:
                    raise RuntimeError('inline constraint material')
                end = mat_span(c.d, c.o)
                c.seg(end - c.o, 4)           # inline Material (coarse)
    c.skip(c.u32(b + 320) * R['rope'], 4)     # ropes: RUNTIME
    return c.o, _merge(c.events)


# =====================================================================
# XModel (xmodel_pc layout; PC v147 LE). Interior allocation events —
# the register-once PC_DELIM dispatch modeled each XModel as ONE linear
# region, missing the per-allocation runtime alignment pads (name str,
# u16 bone arrays, surface dynamic, collmap chain). Summed over ~440
# models that is the ~100 K PC pre-GfxWorld drift band
# (HANDOFF_assemble_runtime_interior_model item, XModel first).
#
# Endian-parametrized like the other walkers, but the LAYOUT is PC-side:
# PC XSurface = 80, collSurf = 44, body = 248, himip usually absent.
# The console side already per-allocation-aligns via SimEmitter.emit_asset,
# so this walker closes the PC/console interior-alignment asymmetry.
# `end` is byte-exact vs xmodel_pc.parse_xmodel_pc (self-check below).
#
# STATUS (FINDINGS ADDENDUM 5, 2026-07-10): built + byte-exact (440/440 raid,
# 491/491 dockside) but the gate residual it was meant to close is NOT interior
# drift — it is cross-asset CONTENT-DEDUP (delta-diff histogram = diff-asset
# 292/293). Real-align wiring REGRESSES (loader packs; only 3,400 B pad vs the
# 105 K deficit); packed wiring is EXACTLY NEUTRAL. Kept as verified dormant
# tooling (granular anchors are safe/neutral, a candidate for assemble string
# re-sourcing). Do NOT wire with real aligns.
# =====================================================================
_XM_SURF = 80
_XM_VTX = 32
_XM_XRVL = 12
_XM_CTREE = 40
_XM_COLLSURF = 44
_XM_COLLTRI = 48
_XM_BONEINFO = 44
_XM_PHYSPRESET = 84


def _xm_surface_dyn(c, sb, mat_span=None):
    """One PC XSurface's dynamic allocations (mirrors xmodel_pc._surface_dyn)."""
    flags = c.u16(sb + 2)
    vc = c.u16(sb + 4)
    tc = c.u16(sb + 6)
    vlc = c.d[sb + 1]
    vi = [c.i16(sb + 16 + j * 2) for j in range(4)]
    if _pt(c, sb + 24, 'xs_vertsBlend') in PTRS:                         # vertsBlend u16s
        c.seg((vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]) * 2, 2)
    if _pt(c, sb + 28, 'xs_tensionData') in PTRS:                         # tensionData f32s
        c.seg((vi[0] + vi[1] + vi[2] + vi[3]) * 4, 4)
    if not (flags & 1) and _pt(c, sb + 32, 'xs_verts0') in PTRS:     # verts0
        c.seg(vc * _XM_VTX, _al('verts0', 4), 'xs_verts0')
    if _pt(c, sb + 40, 'xs_vertList') in PTRS:                          # vertList + trees
        base = c.o
        c.seg(vlc * _XM_XRVL, _al('vertlist', 4), 'xs_vertList')
        for k in range(vlc):
            if _pt(c, base + k * _XM_XRVL + 8, 'xs_collTree') in PTRS:  # collisionTree
                tb = c.o
                c.seg(_XM_CTREE, 4)
                nc = c.u32(tb + 24)
                lc = c.u32(tb + 32)
                if _pt(c, tb + 28, 'xs_collNodes') in PTRS:
                    c.seg(nc * 16, 4)                   # XSurfaceCollisionNode
                if _pt(c, tb + 36, 'xs_collLeaves') in PTRS:
                    c.seg(lc * 2, 2)                    # XSurfaceCollisionLeaf
    if _pt(c, sb + 12, 'xs_triIndices') in PTRS:                          # triIndices u16[3]
        c.seg(tc * 6, _al('triidx', 2), 'xs_triIndices')


def _xm_collmaps(c, ncm):
    """PC collmaps chain (mirrors xmodel_pc._collmaps_span)."""
    base = c.o
    c.seg(4 * ncm, 4)
    for i in range(ncm):
        if c.u32(base + i * 4) not in PTRS:
            continue
        gl = c.o
        c.seg(12, 4)                                   # PhysGeomList
        cnt = c.u32(gl)
        if c.u32(gl + 4) in PTRS:
            gbase = c.o
            c.seg(68 * cnt, 4)                          # PhysGeomInfo array
            for g in range(cnt):
                if c.u32(gbase + g * 68) not in PTRS:
                    continue
                bw = c.o
                c.seg(96, 4)                            # BrushWrapper
                nsides = c.u32(bw + 28)
                nverts = c.u32(bw + 84)
                if c.u32(bw + 32) in PTRS:              # sides
                    sbase = c.o
                    c.seg(12 * nsides, 4)
                    for s in range(nsides):
                        if c.u32(sbase + s * 12) in PTRS:
                            c.seg(20, 4)                # cplane_s (inline)
                if c.u32(bw + 88) in PTRS:              # verts
                    c.seg(12 * nverts, 4)
                if c.u32(bw + 92) in PTRS:              # planes
                    c.seg(20 * nsides, 4)


def xmodel_events(d, b, e='<', mat_span=None, packed=True):
    """PC XModel at `b` -> (end_abs, events). end byte-exact vs parse_xmodel_pc.
    packed=True (DEFAULT, part-A-proven): segs carry align 1 — XModel arrays
    are fill-loaded packed; per-array aligns are spurious (real-align wiring
    regressed the gate by 11K; packed was exactly neutral). What this walker
    NOW adds (part B): the structural TEMP classes — inline Material roots +
    inline GfxImage bodies (material_events_pc) and the PhysPreset root(84) —
    file bytes with NO virtual, per the T6 load db."""
    if e != '<':
        raise NotImplementedError('xmodel_events: PC (LE) layout only')
    c = Ev(d, b, e)
    if packed:
        _orig_seg = c.seg
        def _pseg(size, align, label=''):
            _orig_seg(size, 1, label)
        c.seg = _pseg
    p = lambda o: c.u32(b + o)
    nb, nrb, ns = d[b + 4], d[b + 5], d[b + 6]
    n = nb - nrb
    ncoll = p(156)
    ncollmaps = d[b + 220]
    c.temp(248)                                         # XModel root: TEMP
    # bone-data prefix (convert_xmodel_bonedata order)
    if _pt(c, b + 0, 'xm_name') in PTRS:
        c.cstr(XM_OPTS.get('cstr_align', 1))                                        # name
    if _pt(c, b + 8, 'xm_boneNames') in PTRS:
        c.seg(2 * nb, _al('bonearr', 2), 'xm_boneNames')                                # boneNames u16
    if _pt(c, b + 12, 'xm_parentList') in PTRS:
        c.seg(n, 1)                                     # parentList u8
    if _pt(c, b + 16, 'xm_quats') in PTRS:
        c.seg(8 * n, _al('bonearr', 2), 'xm_quats')                                 # quats s16[4]
    if _pt(c, b + 20, 'xm_trans') in PTRS:
        c.seg(16 * n, _al('bonearr', 4), 'xm_trans')                                # trans f32[4]
    if _pt(c, b + 24, 'xm_partClass') in PTRS:
        c.seg(nb, _al('partclass', 2), 'xm_partClass')  # MEASURED align 2
    if _pt(c, b + 28, 'xm_baseMat') in PTRS:
        c.seg(32 * nb, _al('bonearr', 4), 'xm_baseMat')                               # baseMat f32[8]
    # surfaces
    if _pt(c, b + 32, 'xm_surfaces') in PTRS:
        sb = c.o
        c.seg(ns * _XM_SURF, _al('surfarr', 16), 'xm_surfaces')
        for i in range(ns):
            _xm_surface_dyn(c, sb + i * _XM_SURF, mat_span)
    # materialHandles (ns refs; FOLLOW -> inline Material sub-events:
    # temp root 112 + temp inline-image bodies + virtual tables)
    if _pt(c, b + 36, 'xm_materialHandles') in PTRS:
        base = c.o
        c.seg(4 * ns, _al('mharr', 4), 'xm_mharr')
        for i in range(ns):
            if _pt(c, base + i * 4, 'xm_mharr_slot') in PTRS:
                material_events_pc(c, c.o)
    # collSurfs (+ collTris followers)
    if _pt(c, b + 152, 'xm_collSurfs') in PTRS:
        base = c.o
        c.seg(_XM_COLLSURF * ncoll, _al('collsurf', 4), 'xm_collSurfs')
        for i in range(ncoll):
            cs = base + i * _XM_COLLSURF
            if _pt(c, cs + 0, 'xm_collTris') in PTRS:
                c.seg(c.u32(cs + 4) * _XM_COLLTRI, _al('collsurf', 4), 'xm_collTris')   # collTris
    if _pt(c, b + 164, 'xm_boneInfo') in PTRS:
        c.seg(_XM_BONEINFO * nb, 4)                     # boneInfo
    if _pt(c, b + 200, 'xm_himip') in PTRS:
        c.seg(4 * ns, _al('himip', 4), 'xm_himip')                                # himipInvSqRadii
    if _pt(c, b + 216, 'xm_physPreset') in PTRS:                                  # physPreset (asset)
        pb = c.o
        c.temp(_XM_PHYSPRESET)                          # root: TEMP
        if _pt(c, pb + 0, 'pp_name') in PTRS:
            c.cstr()
        if _pt(c, pb + 28, 'pp_sndalias') in PTRS:
            c.cstr()
    if _pt(c, b + 224, 'xm_collMaps') in PTRS and ncollmaps:
        _xm_collmaps(c, ncollmaps)
    if _pt(c, b + 228, 'xm_physConstraints') in PTRS:
        raise RuntimeError('inline physConstraints (not built)')
    return c.o, _merge(c.events)


# =====================================================================
# Material / FxEffectDef PC event walkers (part B: the structural TEMP
# classes that compose the PC pre-GfxWorld deficit).
# Per the T6 load db: inline-ASSET roots load into TEMP (Material root 112,
# GfxImage whole inline body incl. loadDef+pixels, PhysPreset 84) — file
# bytes, no virtual. Segs are PACKED (align 1): part A proved per-array
# aligns are spurious for these fill-loaded types (packed test was exactly
# neutral; real aligns regressed).
# =====================================================================
_PC_MAT_ROOT = 112
_TEXDEF = 16
_CONSTDEF = 32
_STATEBITS = 20        # PC GfxStateBits (console is 8; the -24 sub-span proof)


def material_events_pc(c, off):
    """One inline/standalone PC Material at `off`: append events to Ev `c`
    (c.o must equal off). Returns end. Mirrors material_convert layout."""
    import material_convert as MC
    d = c.d
    u = lambda o: c.u32(o)
    texc, constc, sbc = d[off + 84], d[off + 85], d[off + 86]
    ts, tt, ct, sbt, th = (u(off + 92), u(off + 96), u(off + 100),
                           u(off + 104), u(off + 108))
    # XM_OPTS['promote']: move a class from TEMP to VIRTUAL (sweep (M)).
    _pro = XM_OPTS.get('promote', ())
    if 'mat' in _pro:
        c.seg(_PC_MAT_ROOT, 4, 'mat_root')  # promoted: consumes virtual
    else:
        c.temp(_PC_MAT_ROOT)                   # Material root: TEMP (asset)
    if _pt(c, off + 0, 'mat_name') in PTRS:
        c.cstr()                               # info.name
    if ts in PTRS:                             # inline techset (zm class):
        import techset_pc                      # root TEMP + interior virtual
        nxt = techset_pc.parse_techset_pc(d, c.o)
        c.temp(152)                            # techset root
        c.seg(nxt - c.o, 1)
    if tt in PTRS:
        tb = c.o
        # MEASURED (pluto_mp_raid.dmp, n=546 located materials): textureTable
        # align 4. DEFAULT STAYS 1 — this is an additive knob, not a bake.
        c.seg(texc * _TEXDEF, _al('mat_texture', 1), 'mat_textureTable')
        for i in range(texc):
            if _pt(c, tb + i * _TEXDEF + 12, 'mat_texdef_image') in PTRS:
                end = MC.pc_image_span(d, c.o)  # inline GfxImage: WHOLE body
                if 'img_name' in _pro:
                    # promote ONLY the inline name cstr; the rest stays TEMP
                    # ⛔⛔ OFFSET CORRECTED 2026-08-10: this branch used +64 and
                    # therefore promoted a mean of 0.9 BYTES per image (938 segs
                    # / 938 B total across raid) — it never implemented the fix
                    # it appeared to. MEASURED in the zone: the inline GfxImage
                    # name begins at **+80** (a 4-byte hash occupies 76..79).
                    # At +80: mean name 28 B, ~27.7 KB across raid's 989 inline
                    # images — which independently matches hydro's 28,564 B of
                    # within-material gap mass measured in the live dump.
                    # +80 PROVEN, not inferred: printable-rate by body offset
                    # steps 0.0% (+70..75) -> ~34% (+76..79, the 4-byte hash,
                    # i.e. random bytes printable ~1/3 of the time) -> 99.9%
                    # (+80..89) across all 989 raid inline images. That ~34%
                    # band is exactly why an earlier "first printable run"
                    # detector reported name starts at 76-79.
                    # ⚠ 1 image of 989 (@96081848) has NO printable byte at +80
                    # and promotes a few garbage bytes through the guard below.
                    nb0 = c.o
                    _IMG_NAME_OFF = 80
                    try:
                        ne = d.index(b'\x00', nb0 + _IMG_NAME_OFF,
                                     min(end, nb0 + 240)) + 1
                    except ValueError:
                        ne = nb0
                    if nb0 < ne <= end:
                        c.temp(_IMG_NAME_OFF)                   # body root: TEMP
                        c.seg(ne - (nb0 + _IMG_NAME_OFF), 1, 'img_name')  # VIRTUAL
                        c.temp(end - ne)                # loadDef+pixels: TEMP
                    else:
                        c.temp(end - c.o)
                elif 'img' in _pro:
                    c.seg(end - c.o, 4, 'img_body')
                else:
                    c.temp(end - c.o)           # (root+loadDef+pixels) = TEMP
    if ct in PTRS:
        # MEASURED align 16 (n=323, pure mod16 {0}) — independently reproduces
        # boot-35's ct16 from the PC side. DEFAULT STAYS 1.
        c.seg(constc * _CONSTDEF, _al('mat_const', 1), 'mat_constantTable')
    if sbt in PTRS:
        # MEASURED align 8 (n=546) — reproduces boot-35's sb8 EXACTLY.
        # ⚠ an n=2 sample of this row read 16; the population says 8.
        c.seg(sbc * _STATEBITS, _al('mat_statebits', 1), 'mat_stateBitsTable')
    if th in PTRS:
        material_events_pc(c, c.o)             # inline thermal material
    return c.o


def fx_events(d, b, e='<'):
    """PC FxEffectDef at `b` -> (end_abs, events): coarse packed segs with
    inline materials emitted as material_events_pc sub-events (temp roots +
    temp images). End byte-exact vs fx_pc.parse_fx_pc."""
    if e != '<':
        raise NotImplementedError('fx_events: PC (LE) layout only')
    import fx_pc
    mats = []
    orig = fx_pc._material_span

    def rec(dd, cur):
        start = cur.o
        orig(dd, cur)
        mats.append((start, cur.o))
    fx_pc._material_span = rec
    try:
        end, _nm = fx_pc.parse_fx_pc(d, b)
    finally:
        fx_pc._material_span = orig
    c = Ev(d, b, e)
    c.temp(fx_pc.HDR)                          # FxEffectDef root: TEMP (asset)
    # elemDefs ARRAY SPLIT (additive): the array was previously swallowed by the
    # first coarse stretch, so it had no align of its own. MEASURED align 4
    # (n=40 raid + 47 skate). DEFAULT 1 reproduces the old coarse emission
    # EXACTLY — with align 1 a split seg and a merged seg consume identically,
    # and _merge recoalesces them. Proven by the neutrality gate.
    _fx_ea = _al('fx_elemdefs', 1)
    if _fx_ea != 1:
        _n = (struct.unpack_from('<h', d, b + 8)[0] +
              struct.unpack_from('<h', d, b + 10)[0] +
              struct.unpack_from('<h', d, b + 12)[0])
        if struct.unpack_from('<I', d, b + 28)[0] in PTRS and _n > 0:
            if struct.unpack_from('<I', d, b)[0] in PTRS:
                c.cstr()                       # inline name, align 1 (measured)
            c.seg(_n * fx_pc.ED, _fx_ea, 'fx_elemDefs')
    for (ms, me) in mats:
        if ms > c.o:
            c.seg(ms - c.o, 1)                 # non-material stretch
        saved = c.o
        assert saved == ms
        material_events_pc(c, ms)
        if c.o != me:                          # material sub-walk must agree
            raise RuntimeError('fx material sub-span %d != %d' % (c.o, me))
    if end > c.o:
        c.seg(end - c.o, 1)
    return c.o, _merge(c.events)


def material_events(d, b, e='<'):
    """Standalone PC Material asset -> (end_abs, events)."""
    if e != '<':
        raise NotImplementedError('material_events: PC (LE) layout only')
    c = Ev(d, b, e)
    end = material_events_pc(c, b)
    return end, _merge(c.events)


# ---------------------------------------------------------------------
# self-check: event extents must equal the validated probes' extents
# ---------------------------------------------------------------------
def _selfcheck():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), '..', 'wiiu_ref'))
    import clipmap_console as CC
    import sndbank_probe as SP
    import gameworldmp_probe as GW
    d = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'wiiu_ref', 'mp_raid_genuine.zone'), 'rb').read()
    # clipMap
    b = 0x4117f02
    ref = CC.parse_clipmap_console(d, b)
    end, ev = clipmap_events(d, b, '>', mat_span=CC._mat_span)
    print('clipMap: ref=0x%x ev=0x%x %s (%d events)' %
          (ref, end, 'OK' if ref == end else 'MISMATCH', len(ev)))
    # GWMP
    b = 0x040aa61d
    ref = GW.Walker(d, '>', 144).walk(b)[0]
    end, ev = gwmp_events(d, b, '>')
    print('GWMP:    ref=0x%x ev=0x%x %s (%d events)' %
          (ref, end, 'OK' if ref == end else 'MISMATCH', len(ev)))
    # SndBank x2
    b = 0x45bea9e
    for i in (0, 1):
        ref = SP.parse_sndbank(d, b, '>')[0]
        end, ev = sndbank_events(d, b, '>')
        print('SndBank[%d]: ref=0x%x ev=0x%x %s (%d events)' %
              (i, ref, end, 'OK' if ref == end else 'MISMATCH', len(ev)))
        b = end
    # PC side (LE): same walkers
    dp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'PC ff', 'mp_raid.zone'), 'rb').read()
    import clipmap_pc, sndbank_pc
    b = None
    # PC GWMP body from probe main() finding
    ref = GW.Walker(dp, '<', 144).walk(0x056da3fb)[0]
    end, ev = gwmp_events(dp, 0x056da3fb, '<')
    print('PC GWMP: ref=0x%x ev=0x%x %s' %
          (ref, end, 'OK' if ref == end else 'MISMATCH'))
    ref = sndbank_pc.parse_sndbank_pc(dp, 0x5bcc5a6)
    end, ev = sndbank_events(dp, 0x5bcc5a6, '<')
    print('PC SndBank: ref=0x%x ev=0x%x %s' %
          (ref, end, 'OK' if ref == end else 'MISMATCH'))
    _xmodel_selfcheck()


def _xmodel_selfcheck():
    """xmodel_events end must equal parse_xmodel_pc for EVERY PC XModel on
    raid + dockside (the ST-calibration regression is the sim's guard; this
    is the walker's own byte-exact guard)."""
    import sys, os
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import loader_sim as LS
    for label, path in (('raid', '../PC ff/mp_raid.zone'),
                        ('dockside', '../wiiu_ref/mp_dockside_pc.zone')):
        p = os.path.join(here, path)
        if not os.path.exists(p):
            print('xmodel[%s]: SKIP (no %s)' % (label, path))
            continue
        em, spans, PC = LS.simulate_pc(p, policy={})
        ok = bad = tot = 0
        first_bad = None
        for (i, nm, root, cs, ce) in spans:
            if root != 'XModel' or ce <= cs:
                continue
            tot += 1
            try:
                end, ev = xmodel_events(PC, cs, '<')
            except Exception as ex:
                bad += 1
                first_bad = first_bad or (cs, str(ex)[:50])
                continue
            if end == ce:
                ok += 1
            else:
                bad += 1
                first_bad = first_bad or (hex(cs), hex(ce), hex(end))
        print('xmodel[%s]: %d/%d OK%s' %
              (label, ok, tot, '' if bad == 0 else ' BAD=%d first=%s' %
               (bad, first_bad)))


if __name__ == '__main__':
    _selfcheck()
