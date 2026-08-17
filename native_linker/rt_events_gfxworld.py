"""CONSOLE GfxWorld allocation-event model (the last unmodeled band).

Derived from the Wii U loader disassembly, NOT from dump-fitted constants:
`Load_GfxWorld__Fb` @0x022259c4 and its children (see _ALLOC_TABLE below for
the per-call-site evidence).  Every entry states the DB_AllocStreamPos mask
(r3 = align-1) and the XFILE block the data lands in.

LOADER SEMANTICS USED (re-derived here from the RPL, all four primitives):
  DB_AllocStreamPos(mask) @0x0223FC34 : pos = (pos + mask) & ~mask on the
      CURRENT block's cursor (global 0x109811B8).
  DB_PushStreamPos(blk)   @0x0223FB3C : saves (pos, blk); if blk != current,
      parks the current cursor in blockStreamPos[cur] and loads
      blockStreamPos[blk].  => EVERY BLOCK HAS ITS OWN CURSOR.  Block-1
      (RUNTIME_VIRTUAL) allocations therefore consume NO block-5 space at all
      -- they are not "skips" in the block-5 walk, they are invisible to it.
  DB_PopStreamPos         @0x0223FBB0 : if the current block is TEMP the
      cursor is RESTORED (temp never persists), then the block is swapped back.
  Load_Stream(atStart,ptr,size) @0x0223FD1C : NO-OP when atStart == 0 (the
      pointer word is already resident in the parent struct).  Dispatch on the
      current block: 0/5/6 read file bytes + advance, 1/2 memset + advance
      (no file bytes), 3/4 queue + advance, 7 no-op.

WHY THIS BAND WAS -119,559 B SHORT
  The old console model (gfxworld_events.gfxworld_console_events) consumed the
  whole asset as align-1 linear 'seg' regions plus three dump-calibrated knobs
  (gfx_planes_skip / gfx_matmem_skip / gfx_skip).  Two loader facts it missed:
    * Load_GfxImage @0x021b124c aligns the pixel blob with ALLOC(0x1fff) --
      EVERY inline image's resident pixels start on an 8 KB boundary.  zm_nuked
      GfxWorld carries 30+ inline images (26 reflection-probe cubemaps, the
      lightmap pair set, outdoorImage, the tail lut, matmem material images),
      i.e. ~4 KB of runtime-only pad each -- the dominant missing term.
    * the 328-byte GfxImage / 104-byte Material / 12-byte MaterialVertexShader
      roots reached through Load_*Ptr land in TEMP (PushStreamPos(0)), so their
      file bytes must NOT advance the block-5 cursor.
  Everything else is ordinary allocation alignment: 128 for the world vertex /
  lightmap-vertex buffers, 256 for GX2 microcode, 16 for the streamInfo aabb
  trees / brush models / dpvs surfaces / sunLight, 2 for the u16 index arrays,
  1 for strings and the per-cell reflection-probe index bytes.

RESULTS (harness `_gw_score.py`, zone zm_nuked_authored.zone, ground truth
_zmnuked_realmap.pkl; all other bands at rt_events_exact.all_events()):
  cross-span error step, adjacent measured anchors
      b5 79283897 (last before GFXWORLD) -> b5 115343831 (GFXWORLD end, the
      first measured start after it):        -135,843  ->  -4,258
  the anchor pair quoted in the brief (79283897 -> 115722956):
                                            -119,559  ->  +12,025
  the +16,272 difference between those two "after" anchors is NOT GfxWorld:
  it is the GameWorldMp band -- alloc_events.gwmp_events models PathData
  `basenodes` as a block-5 skip of (nodeCount+128)*16 = (889+128)*16 = 16,272,
  but Load_PathData @0x021c78e4 +250 brackets that allocation in
  DB_PushStreamPos(1), so it costs ZERO block-5 space.  Fixing that (other
  lane) makes both anchor pairs read -4,258.
  Structural walk extent == asset span EXACTLY (delta 0) on
  zm_nuked_authored.zone, mp_raid_authored.zone AND the retail-disc
  ../wiiu_ref/mp_raid_genuine.zone -- the last is the strong check: the walk
  is the loader's structure, not a fit to our own authoring.
  Span list, per-band medians and RESYNC/BREAK counts unchanged on all four
  console zones tested; every band downstream of GFXWORLD improved by exactly
  +131,585 (FxEffectDef -748,564 -> -616,980, XAnimParts -1,198,126 ->
  -1,066,542, the three weapon golden points in _rt_acceptance likewise).

STILL OPEN (-4,258 residual, ~0.012 % of the span):
  * candidate 1: measurement bias -- real[115343831] is back-projected from a
    needle INSIDE GameWorldMp assuming a linear interior, so it is inflated by
    whatever pad/skip precedes the needle.  Needs an anchor measured at rel 0.
  * candidate 2: one more 8 KB-aligned allocation somewhere (mean pad 4,096).
  * candidate 3: block-5 base phase -- the 0x1fff align is applied to the
    block-5 OFFSET; if the block base is not 8 KB aligned every image pad is
    off.  33 padded images totalling 246,063 B of pad make this testable.

Usage (auto-merged by rt_events_exact.all_events()):
    EXTRA = {"GfxWorld": (gfxworld_console_events, GFXWORLD_ROOT)}
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alloc_events import Ev, PTRS

FOLLOW = 0xFFFFFFFF

# console sizeof(GfxWorld): Load_GfxWorld's opening Load_Stream(r5=0x448).
# (gfxworld_probe2's CFG['wiiu']['bodysize'] says 1076 -- 20 short.)
GFXWORLD_ROOT = 0x448                                          # 1096

# --------------------------------------------------------------------------
# call-site -> (align mask, block) evidence table.  rel offsets are into the
# named Load_* function; "blk1" = inside a PushStreamPos(1) bracket.
# --------------------------------------------------------------------------
_ALLOC_TABLE = """
Load_GfxWorld        +0a4  0xf   blk5  sunLight (GfxLight, 0x160)
Load_GfxWorld        +110..+368 0x3 blk5  11 volume arrays
Load_GfxWorld        +3bc  0x3   blk5  cells array
Load_GfxWorld        +4fc  0xf   blk5  brush models
Load_GfxWorld        +53c  0x3   blk5  materialMemory
Load_GfxWorld        +7c4..+958 0x3 blk1  6 runtime arrays (fields 792..812)
Load_GfxWorld        +9ac  0xf   blk5  SSkinInstance array
Load_GfxWorld        +abc  0x3   blk5  shadowGeometry
Load_GfxWorld        +bcc  0x3   blk5  lightRegion (+0xc48 hulls, +0xcbc axes)
Load_GfxWorld        +ee4  0x3   blk5  waterBuffer[0..1]
Load_GfxWorld        +fc8  0x3   blk5  occluders
Load_GfxWorld        +1004 0x3   blk5  outdoorBounds
Load_GfxWorld        +1040 0x3   blk5  heroLights
Load_GfxWorld        +107c 0x3   blk5  heroLightTree
Load_GfxWorldStreamInfo +3c 0xf  blk5  aabbTrees
Load_GfxWorldStreamInfo +78 0x3  blk5  leafRefs
Load_GfxWorldDpvsPlanes +4c 0x3  blk5  planes
Load_GfxWorldDpvsPlanes +9c 0x1  blk5  nodes (u16)
Load_GfxWorldDpvsPlanes +e4 0x3  blk1  sceneEntCellBits
Load_GfxCell         +3c   0x3   blk5  aabbTrees
Load_GfxCell         +140  0x3   blk5  portals (+portal verts)
Load_GfxCell         (none)      blk5  reflectionProbeIndices -> align 1
Load_GfxAabbTree     +44   0x1   blk5  smodelIndexes (u16)
Load_GfxWorldDraw    +3c   0x3   blk5  reflectionProbes
Load_GfxWorldDraw    +d8   0x3   blk5  probe volume data
Load_GfxWorldDraw    +2e8  0x3   blk1  reflectionProbeTextures
Load_GfxWorldDraw    +328  0x3   blk5  lightmaps
Load_GfxWorldDraw    +5f8/+640 0x3 blk1 lightmap primary/secondary textures
Load_GfxWorldDraw    +6a4  0x7f  blk5  vertexData0   (128-aligned)
Load_GfxWorldDraw    +738  0x7f  blk5  vertexData1   (128-aligned)
Load_GfxWorldDraw    +7a8  0x1   blk5  indices (u16)
Load_GfxLightGrid    +3c   0x1   blk5  rowDataStart (u16)
Load_GfxLightGrid    +90/+c8/+104/+140/+17c 0x3 blk5  raw/entries/colors/coeffs/skyvols
Load_GfxWorldDpvsStatic +48..+288 0x7f blk1 smodel/surface visData (11 arrays)
Load_GfxWorldDpvsStatic +2c0 0x7f blk5  smodelCastsShadow
Load_GfxWorldDpvsStatic +2f4 0x1  blk5  sortedSurfIndex (u16)
Load_GfxWorldDpvsStatic +330 0x3  blk5  smodelInsts
Load_GfxWorldDpvsStatic +36c 0xf  blk5  surfaces
Load_GfxWorldDpvsStatic +5ac 0x3  blk5  smodelDrawInsts
Load_GfxWorldDpvsStatic +880 0x3  blk1  surfaceMaterials
Load_GfxStaticModelDrawInst +a8/+12c/+1b0/+234 0x3 blk5 lmapVertexInfo[4] data
Load_GfxWorldDpvsDynamic  ALL      blk1  (zero block-5 cost)
Load_GfxImage        +5c   0x1fff blk5  resident pixels  <-- 8 KB align
Load_XString         +44   0x0    blk5  string chars (align 1)
Load_WiiuVertexShader +3c  0xff   blk5  GX2 microcode (256-aligned)
"""

# element strides (console), cross-checked against the Track F emitters
_VOL = [                          # (countOff, ptrOff, stride)
    (268, 272, 32),               # coronas
    (276, 280, 16),               # shadowMapVolumes
    (284, 288, 16),               # shadowMapVolumePlanes
    (292, 296, 24),               # exposureVolumes
    (300, 304, 16),               # exposureVolumePlanes
    (308, 312, 100),              # fogVolumes
    (316, 320, 16),               # fogVolumePlanes
    (324, 328, 48),               # fogModVolumes (mulli 0x30)
    (332, 336, 16),               # fogModVolumePlanes
    (340, 344, 36),               # lutVolumes
    (348, 352, 16),               # lutVolumePlanes
]

CELL = 48
AABBTREE = 40
PORTAL = 92
GFXLIGHT = 0x160                  # Load_GfxWorld +c4 Load_Stream(r5=0x160)
BRUSHMODEL = 64
SURFACE = 80                      # Load_GfxSurface Load_Stream(r5=0x50)
SMODEL_INST = 36
SMODEL_DRAWINST = 0xd0            # Load_GfxStaticModelDrawInst(r5=0xd0)
LMAPINFO = 32                     # 4 x 32 at drawInst+0x50 (0x80 block)
IMAGE_BODY = 0x148                # Load_GfxImage(r5=0x148)
MATERIAL_BODY = 104
# Load_Material sub-allocation alignments, named so they can be MEASURED
# against a dump instead of being edited in place (values below are the
# shipped ones — this parameterization is exactly neutral by construction).
# ⚠ The FX band (rt_events_fx: AL_CONSTTABLE=16, AL_STATEBITS=8) disagrees
# with AL_CONSTS/AL_STATEBITS here; that contradiction is under measurement.
AL_TEXTABLE = 4
AL_CONSTS = 4
AL_STATEBITS = 4
DRAW = 400                        # &world->draw  (Load_GfxWorldDraw fldarg)
LG = 512                          # &world->lightGrid
DPVS = 832                        # &world->dpvs


class _W(Ev):
    """Ev + a debug region log (region ends are the iteration gate)."""

    def __init__(self, d, base, log=None):
        Ev.__init__(self, d, base, '>')
        self.log = log

    def g(self, o):
        return self.u32(self.base + o)

    def mark(self, label):
        if self.log is not None:
            self.log.append((label, self.o - self.base))


# --------------------------------------------------------------------------
# inline sub-assets reached through Load_*Ptr / Load_*Handle
# --------------------------------------------------------------------------

def _image(c):
    """Load_GfxImagePtr: PUSH(0) + ALLOC(3) + Load_GfxImage(true) + POP.
    328-byte root -> TEMP; name + ALLOC(0x1fff)-aligned pixels -> block 5."""
    ib = c.o
    c.temp(IMAGE_BODY)
    if c.u32(ib + 320) in PTRS:
        c.cstr()
    # pixel gate is PLAIN != 0 (Load_GfxImage +0x4c: `lwz r0,0xb0(r12);
    # cmpwi r0,0; beq`) -- there is no streaming-byte test and no -1 test in
    # the RPL.  The old `in PTRS and d[ib+171]==0` gate was refuted for the
    # XModel band on 2026-07-27 (rt_events_xmodel._image_events); measured
    # NEUTRAL on the genuine corpus (every non-zero pixel word is FOLLOW with
    # the streaming byte clear) -- aligned here 2026-07-30 so the bands cannot
    # silently diverge on a zone that breaks that invariant.
    if c.u32(ib + 176) != 0:
        c.seg(c.u32(ib + 160), 0x2000)
    return True


def _material(c, include_techset=True):
    """Load_MaterialHandle: PUSH(0) + ALLOC(3) + Load_Material(true) + POP.
    104-byte root -> TEMP; everything it points at -> block 5."""
    b = c.o
    c.temp(MATERIAL_BODY)
    tc, cc, sbc = c.d[b + 72], c.d[b + 73], c.d[b + 74]
    tsp, ttp, ctp, sbp, th = (c.u32(b + 80), c.u32(b + 84), c.u32(b + 88),
                              c.u32(b + 92), c.u32(b + 96))
    if c.u32(b) in PTRS:
        c.cstr()
    if tsp in PTRS and include_techset:
        # Load_MaterialTechniqueSetPtr @0x021ba4b8: PUSH(0) + ALLOC(3) +
        # Load_Stream(true, 0x88) -> the 136-byte MTS root lands in TEMP, so
        # rt_events_mts's leading root 'seg' is re-tagged 'temp' on splice.
        import rt_events_mts as MTS
        rel0 = c.o - c.base
        end, ev = MTS.mts_events(c.d, c.o, '>')
        for k, e in enumerate(ev):
            if e[0] == 'seg':
                kind = 'seg'
                if k == 0 and e[1] == 0 and e[2] == MTS.MTS_ROOT:
                    c.events.append(('temp', rel0, e[2]))
                    continue
                c.events.append((kind, rel0 + e[1], e[2], e[3]))
            elif e[0] == 'temp':
                c.events.append(('temp', rel0 + e[1], e[2]))
            else:
                c.events.append(e)
        c.o = end
    if ttp in PTRS:
        defs = c.o
        # 'mat.textable' label (2026-08-08, RT-instrument successor): rides
        # Ev.seg's default-off label channel (alloc_events.TRACE) so the
        # FIX-25-family texdef enumeration can see GfxWorld-hosted materials.
        # Adjudicated positionally exact vs zone_gates._texdef_table on all
        # 353 GfxWorld materials of genuine raid (_texdef_cursor_adjudicate2).
        # Event tuple shape/rt/omap unchanged — label emits only when a
        # consumer arms alloc_events.TRACE.
        c.seg(tc * 16, AL_TEXTABLE, 'mat.textable')
        for i in range(tc):
            if c.u32(defs + i * 16 + 12) in PTRS:
                _image(c)
    if ctp in PTRS:
        c.seg(cc * 32, AL_CONSTS)
    if sbp in PTRS:
        c.seg(sbc * 8, AL_STATEBITS)
    if th in PTRS:
        _material(c, include_techset)


def _vshader(c):
    """Load_MaterialVertexShaderPtr @0x021b6954: ALLOC(3) + Load_Stream(true,
    12) -- NO TEMP bracket -- then name + Load_WiiuVertexShaderPtr (ALLOC(3),
    308-byte GX2 struct, ALLOC(0xff) microcode, FOLLOW record tables)."""
    import rt_events_mts as MTS
    MTS._shader_ref_events(c, 'vs')


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

def gfxworld_console_walk(d, off, log=None):
    """(end_abs, events) for a console GfxWorld body at `off`."""
    c = _W(d, off, log)
    g = c.g
    c.seg(GFXWORLD_ROOT, 4)                        # root -> TEMP (root_size)
    c.mark('body')

    # --- name / baseName (aliases on every measured zone) ---
    for fo in (0, 4):
        if g(fo) in PTRS:
            c.cstr()
    c.mark('names')

    # --- streamInfo (embedded @+0x14) ---
    if g(24) in PTRS:
        c.seg(g(20) * 48, 16)                      # ALLOC(0xf)
    if g(32) in PTRS:
        c.seg(g(28) * 4, 4)
    c.mark('streamInfo')

    if g(36) in PTRS:
        c.cstr()                                   # skyBoxModel
    c.mark('skyBoxModel')

    if g(256) in PTRS:
        c.seg(GFXLIGHT, 16)                        # sunLight  ALLOC(0xf)
        sb = c.o - GFXLIGHT
        if c.u32(sb + 0x100) in PTRS:              # GfxLightDef ptr (rare)
            raise NotImplementedError('inline GfxLightDef in sunLight')
    c.mark('sunLight')

    for (co, po, sz) in _VOL:
        if g(po) in PTRS and g(co):
            c.seg(g(co) * sz, 4)
    c.mark('volumes')

    # --- dpvsPlanes (embedded @+0x174) ---
    if g(376) in PTRS:
        c.seg(g(8) * 20, 4)                        # planes  ALLOC(3)
    if g(380) in PTRS:
        c.seg(g(12) * 2, 2)                        # nodes   ALLOC(0x1)
    c.mark('dpvsPlanes')
    # sceneEntCellBits (field 384) is PUSH(1): no file bytes, no block-5

    # --- cells ---
    cell_count = g(372)
    if g(392) in PTRS:
        cb = c.o
        c.seg(cell_count * CELL, 4)
        for i in range(cell_count):
            co = cb + i * CELL
            atc, atp = c.u32(co + 24), c.u32(co + 28)
            pcnt, pp = c.u32(co + 32), c.u32(co + 36)
            rc, rp = c.d[co + 40], c.u32(co + 44)
            if atp in PTRS:
                ab = c.o
                c.seg(atc * AABBTREE, 4)
                for j in range(atc):
                    ao = ab + j * AABBTREE
                    if c.u32(ao + 32) in PTRS:
                        c.seg(c.u16(ao + 30) * 2, 2)      # ALLOC(0x1)
            if pp in PTRS:
                pb = c.o
                c.seg(pcnt * PORTAL, 4)
                for j in range(pcnt):
                    po2 = pb + j * PORTAL
                    if c.u32(po2 + 36) in PTRS:
                        c.seg(c.d[po2 + 40] * 12, 4)
            if rp in PTRS:
                c.seg(rc, 1)                              # no ALLOC -> align 1
    c.mark('cells')

    # --- draw ---
    rpc = g(DRAW + 0)
    if g(DRAW + 4) in PTRS:
        rb = c.o
        c.seg(rpc * 76, 4)
        for i in range(rpc):
            ro = rb + i * 76
            if c.u32(ro + 60) in PTRS:
                _image(c)
            if c.u32(ro + 64) in PTRS:
                c.seg(c.u32(ro + 68) * 96, 4)
    c.mark('draw.reflectionProbes')
    lmc = g(DRAW + 12)
    if g(DRAW + 16) in PTRS:
        lb = c.o
        c.seg(lmc * 8, 4)
        for i in range(lmc):
            for k in (0, 4):
                if c.u32(lb + i * 8 + k) in PTRS:
                    _image(c)
    c.mark('draw.lightmaps')
    if g(DRAW + 40) in PTRS:
        c.seg(g(DRAW + 32), 128)                   # vd0   ALLOC(0x7f)
    c.mark('draw.vd0')
    if g(DRAW + 72) in PTRS:
        c.seg(g(DRAW + 64), 128)                   # vd1   ALLOC(0x7f)
    c.mark('draw.vd1')
    if g(DRAW + 100) in PTRS:
        c.seg(g(DRAW + 96) * 2, 2)                 # indices  ALLOC(0x1)
    c.mark('draw.indices')

    # --- lightGrid ---
    ra = g(LG + 20)
    mins = [c.u16(off + LG + 4 + 2 * k) for k in range(3)]
    maxs = [c.u16(off + LG + 10 + 2 * k) for k in range(3)]
    if g(LG + 28) in PTRS:
        c.seg((maxs[ra] - mins[ra] + 1) * 2, 2)    # rowDataStart ALLOC(0x1)
    if g(LG + 36) in PTRS:
        c.seg(g(LG + 32), 4)                       # rawRowData
    if g(LG + 44) in PTRS:
        c.seg(g(LG + 40) * 4, 4)                   # entries
    if g(LG + 52) in PTRS:
        c.seg(g(LG + 48) * 168, 4)                 # colors
    if g(LG + 60) in PTRS:
        c.seg(g(LG + 56) * 54, 4)                  # coeffs
    if g(LG + 68) in PTRS:
        c.seg(g(LG + 64) * 40, 4)                  # skyGridVolumes
    c.mark('lightGrid')

    if g(588) in PTRS:
        c.seg(g(584) * BRUSHMODEL, 16)             # models  ALLOC(0xf)
    c.mark('models')

    if g(624) in PTRS:                             # materialMemory
        mb = c.o
        c.seg(g(620) * 8, 4)
        for i in range(g(620)):
            if c.u32(mb + i * 8) in PTRS:
                _material(c)
    c.mark('materialMemory')

    for so in (632, 636):                          # sun sprite/flare materials
        if g(so) in PTRS:
            _material(c)
    c.mark('sunflare')

    if g(788) in PTRS:
        _image(c)                                  # outdoorImage
    c.mark('outdoorImage')

    # fields 792..812 are PUSH(1) runtime arrays: nothing in the file/block 5
    if g(820) in PTRS and g(816):
        raise NotImplementedError('inline SSkinInstance array')

    plc = g(264)                                   # primaryLightCount
    if g(824) in PTRS:
        sb = c.o
        c.seg(plc * 12, 4)
        for i in range(plc):
            so = sb + i * 12
            sc_, mc_ = c.u16(so), c.u16(so + 2)
            if c.u32(so + 4) in PTRS:
                c.seg(sc_ * 2, 4)
            if c.u32(so + 8) in PTRS:
                c.seg(mc_ * 2, 4)
    c.mark('shadowGeom')
    if g(828) in PTRS:
        rb = c.o
        c.seg(plc * 8, 4)
        for i in range(plc):
            hc = c.u32(rb + i * 8)
            if c.u32(rb + i * 8 + 4) in PTRS:
                hb = c.o
                c.seg(hc * 80, 4)
                for j in range(hc):
                    ho = hb + j * 80
                    if c.u32(ho + 76) in PTRS:
                        c.seg(c.u32(ho + 72) * 20, 4)
    c.mark('lightRegion')

    # --- dpvsStatic (the 11 visData arrays are PUSH(1): invisible here) ---
    if g(DPVS + 108) in PTRS:
        c.seg(g(DPVS + 40), 128)                   # smodelCastsShadow 0x7f
    c.mark('dpvs.smodelCastsShadow')
    if g(DPVS + 80) in PTRS:
        c.seg(g(DPVS + 4) * 2, 2)                  # sortedSurfIndex ALLOC(0x1)
    c.mark('dpvs.sortedSurfIndex')
    smc = g(DPVS + 0)
    if g(DPVS + 84) in PTRS:
        c.seg(smc * SMODEL_INST, 4)
    c.mark('dpvs.smodelInsts')
    if g(DPVS + 88) in PTRS:
        c.seg(g(16) * SURFACE, 16)                 # surfaces ALLOC(0xf)
    c.mark('dpvs.surfaces')
    if g(DPVS + 92) in PTRS:
        db = c.o
        c.seg(smc * SMODEL_DRAWINST, 4)
        for i in range(smc):
            ib = db + i * SMODEL_DRAWINST
            if c.u32(ib + 32) in PTRS:
                raise NotImplementedError('inline XModel in smodelDrawInst')
            for e in range(4):
                lo = ib + 80 + e * LMAPINFO
                if c.u32(lo) in PTRS:
                    c.seg(c.u16(lo + 24) * 4, 4)
    c.mark('dpvs.smodelDrawInsts')
    # dpvsDynamic (948): every allocation is PUSH(1) -> nothing here

    for wi in range(2):                            # waterBuffers[2] @1004
        wb = 1004 + wi * 8
        if g(wb + 4) in PTRS:
            c.seg(g(wb), 4)
    c.mark('waterBuffers')
    for mo in (1020, 1024, 1028, 1032):            # water/corona/rope/lut mtl
        if g(mo) in PTRS:
            _material(c)
    c.mark('tail materials')
    if g(1040) in PTRS:
        c.seg(g(1036) * 68, 4)                     # occluders
    if g(1048) in PTRS:
        c.seg(g(1044) * 24, 4)                     # outdoorBounds
    if g(1060) in PTRS:
        c.seg(g(1052) * 56, 4)                     # heroLights
    if g(1064) in PTRS:
        c.seg(g(1056) * 32, 4)                     # heroLightTree
    c.mark('tail arrays')

    for k in range(4):                             # gpuskin[1-4]bone shaders
        if g(1076 + k * 4) in PTRS:
            _vshader(c)
    c.mark('vshaderTail')
    return c.o, c.events


SHORTFALL = {}          # off -> (structural_end, reference_end) when they differ


def gfxworld_console_events(d, off, strict=False):
    """loader_sim CONSOLE_EVENTS entry.

    SPAN SAFETY: the reference span walker (gfxworld_console_span.
    parse_gfxworld_console) stays authoritative, so the zone-level span list is
    bit-identical to the baseline on every map.  When the structural walk ends
    short of it the remainder is covered by ONE trailing align-1 'seg' (exactly
    the old linear model for that tail) and the gap is recorded in SHORTFALL.
    zm_nuked, mp_raid_authored and the GENUINE mp_raid_genuine.zone all walk to
    delta 0, so the shortfall path is inert there.

    NOTE (mp_skate_gfxtail46, 2026-07-26): the structural end lands 17,265,800 B
    BEFORE the reference end, and `_gameworld_body_at` is True at the structural
    end -- i.e. the REFERENCE span looks 17.3 MB too long on that lineage (the
    gfxworld_probe2 walk overshoots and the signature scan then re-syncs late).
    Not changed here: re-spanning skate is a different lane.  Pass strict=True
    to use the structural end instead.
    """
    import gfxworld_console_span as GCS
    end, ev = gfxworld_console_walk(d, off)
    if strict:
        return end, ev
    try:
        ref = GCS.parse_gfxworld_console(d, off)
    except Exception as ex:
        # the reference walker itself fails on some lineages (mp_skate_gfxtail46:
        # shader_probe.Fail inside gfxworld_probe2's inline-material walk) and
        # loader_sim then BREAKs the whole zone walk at GFXWORLD.  The structural
        # walk survives there and its end IS corroborated by the following
        # GameWorldMp body signature -- but adopting it silently would change a
        # live map's span list, so behaviour is preserved and the finding is only
        # recorded.  Pass strict=True to take the structural end.
        SHORTFALL[off] = (end, 'reference walker failed: %s' % str(ex)[:60],
                          GCS._gameworld_body_at(d, end))
        raise
    if end != ref:
        SHORTFALL[off] = (end, ref)
        if ref > end:
            ev = list(ev) + [('seg', end - off, ref - end, 1)]
        else:                       # structural overran: cannot be repaired here
            raise RuntimeError('GfxWorld structural walk overran span end by %d'
                               % (end - ref))
    return ref, ev


EXTRA = {
    'GfxWorld': (gfxworld_console_events, GFXWORLD_ROOT),
}


def _selfcheck(zone='zm_nuked_authored.zone'):
    """Gate: the structural walk must land exactly on the asset span end."""
    import loader_sim as LS
    em, spans, CO = LS.simulate(zone, verbose=False, policy=dict(gfx_skip=0))
    gw = [(s, e) for (i, nm, root, s, e) in spans
          if root == 'GfxWorld' and e > s][0]
    log = []
    try:
        end, ev = gfxworld_console_walk(CO, gw[0], log)
    except Exception as ex:
        end, ev = None, []
        print('WALK RAISED: %s' % ex)
    for (lbl, rel) in log:
        print('  %-28s end=%d' % (lbl, rel))
    print('span %d..%d len=%d' % (gw[0], gw[1], gw[1] - gw[0]))
    if end is not None:
        print('walk end rel=%d  (span len %d)  delta=%+d'
              % (end - gw[0], gw[1] - gw[0], end - gw[1]))
        segs = sum(e[2] for e in ev if e[0] == 'seg')
        temps = sum(e[2] for e in ev if e[0] == 'temp')
        print('%d events, seg=%d temp=%d' % (len(ev), segs, temps))


if __name__ == '__main__':
    _selfcheck(sys.argv[1] if len(sys.argv) > 1 else 'zm_nuked_authored.zone')
