"""EXACT runtime-allocation events for console FxEffectDef (the missing band).

WHY THIS FILE EXISTS
--------------------
rt_events_exact._BANDS has always listed mts / xmodel / weapon / gfxworld and
all_events() merges whatever is present, so a missing band is silently
tolerated: the type keeps the OLD LINEAR (verbatim-region) model.  FX was that
missing band, which is exactly why every FX interior pointer this project ever
derived arithmetically was unreliable -- the linear model has no idea that an
inline Material/GfxImage root consumes file bytes and NO block-5 space, nor
that an inline image's pixels are 8192-ALIGNED.

PROVENANCE: this module is DISASSEMBLED, NOT FITTED.  Every size, order, gate
and alignment below was read out of the deployed RPL
(`C:\\Users\\...\\mlc01\\usr\\title\\0005000e\\1010cf00\\code\\t6mp_cafef_rpl.rpl`,
guest = file vaddr + 0x2000) with `_rpl_dis_robust.py`.  Nothing here was tuned
against a residual.  The functions read, with their guest addresses:

  Load_FxEffectDef__Fb          0x021cb1d4   Load_FxElemDef__Fb        0x021caf1c
  Load_FxElemDefVisuals__Fb     0x021ca938   Load_FxElemVisuals__Fb    0x021ca728
  Load_FxElemExtendedDefPtr__Fb 0x021cadd0   Load_FxTrailDef__Fb       0x021cad04
  Load_MaterialHandle__Fb       0x021ba9d0   Load_Material__Fb         0x021baa9c
  Load_GfxImagePtr__Fb          0x021b1504   Load_GfxImage__Fb         0x021b124c
  Load_XModelPtr__Fb            0x021c2d78   Load_GfxLightDefPtr__Fb   0x021bc678
  Load_GfxLightImage__Fb        0x021bbbc0   Load_XString__Fb          0x0219fbd8
  Load_Stream__FbPCvi           0x0223fd1c   DB_AllocStreamPos__Fi     0x0223fc34
  DB_PushStreamPos__FUi         0x0223fb3c   DB_PopStreamPos__Fv       0x0223fbb0

THE THREE PRIMITIVES (0x0223fc34 / 0x0223fb3c / 0x0223fd1c, read instruction
by instruction):
  * DB_AllocStreamPos(mask):  pos = (pos + mask) & ~mask   -- align == mask+1.
    This is the ONLY thing that pads; the FILE stays packed.  mask 0 => align 1
    (a no-op), mask 3 => 4, mask 7 => 8, mask 0xf => 16, mask 0x1fff => 8192.
  * DB_PushStreamPos(blk)/Pop: per-BLOCK save/restore of that single cursor, so
    anything loaded between Push(0) and Pop consumes TEMP and leaves the
    block-5 cursor exactly where it was.  EVERY `Load_<T>Ptr`/`Load_...Handle`
    wrapper does Push(0) -> [root] -> the type's own Push(5) -> [followers].
    That is the whole reason an inline sub-asset ROOT is `temp` and its
    interior is `seg`.
  * Load_Stream(atStart, ptr, size): `if (!atStart || !size) return 1;`  -- an
    atStart=0 call allocates NOTHING.  All the per-element Load_Stream(0,...)
    calls in the FX chain are therefore pure no-ops (the parent array already
    streamed those bytes); modelling them as allocations would double-count.

GATES ARE `!= 0`, NOT `== FOLLOW` (this matters):
  fx->elemDefs (+28), elem->velSamples (+188), elem->visSamples (+192),
  *visuals (+196) and elem->extended (+256) are tested with a plain
  `cmpwi r0,0; beq` -- there is no -1 test and no InsertPointer call on those
  paths.  Non-zero means "stream it".  Modelled as written.  By contrast the
  handle/pointer paths (Load_MaterialHandle, Load_GfxImagePtr, Load_XModelPtr,
  Load_GfxLightDefPtr, Load_XString, and Material's textureTable/constantTable/
  stateBits) DO test 0 / -1 / -2 explicitly, and an ALIAS value falls through
  to DB_ConvertOffsetToAlias/Pointer -- no allocation at all.  Those are gated
  on the exact constant the RPL compares against, so an alias (our 23 seagull
  slots among them) costs zero bytes, which is what makes the FX interior
  addressable at all.

ALIGNMENT TABLE ACTUALLY EMITTED BY THIS BAND (all from AllocStreamPos sites,
cross-checked against _rpl_align_table.json):
  FxElemDef array            4     (Load_FxEffectDef +0x58, raw 3)
  velSamples / visSamples    4     (Load_FxElemDef +0x44 / +0x84, raw 3)
  visuals array / markArray  4     (Load_FxElemDefVisuals +0x3c / +0x2a8, raw 3)
  FxTrailDef + its verts     4     (Load_FxElemExtendedDefPtr +0x3c;
                                    Load_FxTrailDef +0x3c, raw 3)
  FxTrailDef indices         2     (Load_FxTrailDef +0x78, raw 1)
  FxSpotLightDef (12 B)      4     (Load_FxElemExtendedDefPtr +0x80, raw 3)
  extended, generic 1 byte   1     (Load_FxElemExtendedDefPtr +0xc4, raw 0)
  every string               1     (Load_XString +0x44, raw 0)
  Material textureTable      4     (Load_Material +0x9c, raw 3)
  Material constantTable    16     (Load_Material +0x2e8, raw 15)
  Material stateBits         8     (Load_Material +0x33c, raw 7)
  inline GfxImage pixels  8192     (Load_GfxImage +0x5c, raw 0x1fff)

TEMP ROOTS REACHED FROM FX (file bytes, zero block-5 space):
  FxEffectDef 76 (declared via the EXTRA tuple), Material 104, GfxImage 328,
  XModel 244, GfxLightDef 16, MaterialTechniqueSet 136.

WHAT IS **NOT** MEASURED HERE (labelled guesses -- read this before trusting a
pointer that lands in one of these):
  G1  inline XModel INTERIOR.  Reached only through an elemType 7 (MODEL)
      visual whose FxElemVisuals word is FOLLOW.  It does not occur in
      mp_skate cm35 (0 sites; all 48 MODEL visuals are aliases, zeros, or
      FOLLOW *arrays* whose entries are aliases), so it is UNEXERCISED here.
      Delegation to rt_events_xmodel is available behind XMODEL_INTERIOR but
      defaults OFF, matching rt_events_weapon's measured finding that that
      band's img_pix->TEMP rule makes inline models worse.  With it off the
      interior is one packed remainder seg = the OLD linear model, i.e. a
      known-approximate region, and _selfcheck() reports the site count so a
      zone that does exercise it cannot pass silently.
  G2  inline MaterialTechniqueSet interior (a Material whose techSet word is
      FOLLOW).  0 sites in cm35.  Delegated to rt_events_mts._technique_events
      when it happens, which is itself dump-verified -- but that composition
      has never been exercised from FX, so treat any address inside one as
      unproven.
  G3  elemType 9 (SPOT_LIGHT) GfxLightDef visuals and the 12-byte
      FxSpotLightDef.  0 sites in cm35.  Model is RPL-read, not zone-exercised.
  G4  the generic `extended` branch (elemType not 5 and not 9 with a non-zero
      extended word): the RPL literally streams ONE byte at align 1
      (`li r3,1; mr r5,r3`).  0 sites in cm35.  RPL-read, not exercised.
Everything else in this file is exercised by mp_skate cm35 and byte-exact
against fx_probe.parse_fx (see _selfcheck).

Wire-up: rt_events_exact._BANDS gained 'rt_events_fx'; all_events() merges
EXTRA below.  Key is 'FxEffectDef' -- loader_sim dispatches CONSOLE_EVENTS on
`walker.ASSET_ROOT[name]`, and ASSET_ROOT['FX'] == 'FxEffectDef'.  (A key of
'FX' would never dispatch; a dead alias would be worse than useless, so it is
not exported.)

=====================================================================
MEASURED RESULT (2026-07-26)
=====================================================================
GATE 1 -- structural, byte-exact span ends vs wiiu_ref/fx_probe.parse_fx AND
vs loader_sim's own span chain, on six zones:
    mp_skate cm35        79/79      mp_skate_gfxtail46 (KEY)   79/79
    mp_raid_genuine     164/164     zm_transit_original       157/157
    mp_dockside_wiiu     81/81      zm_nuked_authored         242/242
  = 802/802.  Four of those are GENUINE retail console zones, so the walk is
  not merely self-consistent with our own linker.

GATE 2 -- the three live-handle anchors on mp_skate cm35.  Each is a real
0xAxxxxxxx literal occurring EXACTLY ONCE in the zone (and identically in the
KEY), so (v-1)&0x1FFFFFFF is ground truth for its target's runtime offset:
    file 405111 -> rt 424480 (handle 0xA0067A21 @405403)
    file 497462 -> rt 514732 (handle 0xA007DAAD @499408)
    file 552768 -> rt 587520 (handle 0xA008F701 @27890434)
  With this band installed the model reproduces all three under ONE constant
  offset of exactly -16384, and that constant is fully explained (below).  With
  the -16384 removed the residual is 0 / 0 / 0 BYTES.

  !! ANCHOR 3 WAS MIS-INVERTED IN THE PREVIOUS (REJECTED) WORK.  It was carried
  as `rt 587520 <-> file 569761`, which is what produced the "the rt delta is
  NOT constant: 19,369 / 17,270 / 17,759" reading that the 8 invented handles
  were built on.  The true target is file 552768.  PROOF, independent of any
  runtime map: the consumer word 0xA008F701 sits at file 27890434, i.e. +12 in
  a 16-byte console MaterialTextureDef whose first 12 bytes are
  `a0 ab 10 41 63 70 ea 02 00 00 00 00`.  The texdef at 552768-12 is
  BYTE-IDENTICAL (`... 63 70 ea 02 ...`), belongs to material
  `gfx_fxt_debris_wind_ash_z90_gr` and inlines image `,fxt_debris_wind_ash`;
  the consumer material is `gfx_fxt_debris_wind_ash_z10_gr` -- the same image,
  the same family, exactly what an image-dedup back-reference is.  The old
  candidate at 569761 has a DIFFERENT texdef header (`... 63 70 f4 02 ...`) and
  belongs to `gfx_fxt_fx_distortion_water`, an unrelated material inlining an
  unrelated image.  So the FX rt delta IS constant; the old "non-constant
  delta" argument against a clean model was an artifact of the coarse map.

GATE 3 -- self-verifying anchor corpus (scratch: fx_corpus.py).  For every
modelled FX slot of a known dedup-TARGET class, synthesise the handle the
loader would have to bake under a constant offset S, look for that literal in
the zone, and require an INDEPENDENT structural agreement at the consumer
(byte-identical texdef header for texdef+12; consumer word itself at
elem_base + k*292 + 196 for visuals).  Result:
    S = 16384  -> 26 confirmed anchors, 4 distinct targets, file 405111..552768
    S = 16380 / 16388 / 8192 / 24576 -> 0 anchors each
A wrong S cannot pass the structural test, so the constant is pinned.

THE -16384 IS NOT THIS BAND.  It is ONE inline GfxImage with resident pixels
(file 281743, pixel word FOLLOW, baseSize 8192) inside XModel asset 7, which
rt_events_xmodel.RULES modelled as `'img_pix': ('t', 0)` -- a TEMP copy that
consumes no block-5 space and skips the 8192 align.  That is defect D1.

D1 LANDED 2026-07-27: rt_events_xmodel.RULES['img_pix'] = ('v', 8192).

=====================================================================
2026-07-27 -- WHAT MOVED, AND THE ORACLE THAT MADE IT MEASURABLE
=====================================================================
NEW EXACT DUMP-FREE ORACLE: the decrypted zone header is
`u32 size, u32 externalSize, u32 blockSize[8]`, so blockSize[5] IS the retail
linker's own total block-5 allocation.  The sim's final block-5 cursor must
equal it byte for byte, per zone, with no dump and nothing to fit.  Residuals:

                       shipped 07-26      D1 only        D1 + RPL aligns
  mp_raid_genuine       -3,875,881         +7,127            +7,127
  mp_dockside_wiiu      -2,501,545         -1,961            +6,231
  zm_transit_original   -1,830,814       -241,566          -233,630

SCORE on the byte-exact FX-alias metric (invert the model on (v-1)&0x1FFFFFFF
for every Material-class FxElemDef+196 alias; it must land EXACTLY on a
backward Material-pointer-holder slot), GENUINE retail zones only:

                     07-26 shipped   +D1        +D1 +RPL aligns
  mp_raid_genuine       18/198       93/198        140/198
  mp_dockside_wiiu     122/271      151/271        239/271
  zm_transit_original    0/583        0/583          0/583
  TOTAL                140/1052     244/1052       379/1052  (36.0%)
  +-4 B sharpness ctl    0 / 0        0 / 0          0 / 9

The four localised cm35 defects that motivated this pass (+768 @25,540,367,
-32 @25,848,605, -32 @25,855,326, -1024 @29,816,317) are ALL ZERO now, and the
whole 26-point body-domain live-handle corpus on cm35 is 26/26 byte-exact.
None of them was an FX-band defect: they were XModel-band mis-alignments
(collSurfs 16->4, PhysGeomInfo[] 4->16, BrushWrapper 4->16, Material
constantTable 4->16, stateBits 4->8) whose few bytes per asset get quantised to
multiples of 256 by the first 0x100 microcode pad of the next
MaterialTechniqueSet (see the phase-lock note in rt_events_mts).  That
quantisation is why they presented as +768 / -32 / -1024 instead of as the
handful of bytes they really are.

STILL OPEN (transit): zm_transit_original is 0/583 because it carries 375
TOP-LEVEL MATERIAL assets (raid and dockside have 2) holding 681 inline
GfxImages, 37 of them resident, and there is NO exact band for the Material
asset type -- the linear model keeps every 328-byte inline GfxImage root in
block 5 and skips the 8192-byte pixel align entirely.  Measured: at transit's
first FX asset the model is exactly -49,152 = 6 * 8192 low, and the "structural"
neighbour spacings -47,120 / -47,412 / -47,996 / -48,288 are just that value
plus multiples of 292 = sizeof(FxElemDef), i.e. adjacent elem slots, not
allocator structure.  Wiring _material below as a top-level 'Material' band
(root emitted as a seg so loader_sim's root_size makes it TEMP) collapses that
local error to 0 (modal delta 0, 203 rows) and takes transit 0/583 -> 221/583,
TOTAL 379 -> 600/1052 (57.0%), raid and dockside unchanged.  NOT landed here:
it needs a new band module plus an entry in rt_events_exact._BANDS, outside
this pass's edit scope.

NO REGRESSION from D1 or the align corrections: _xmodel_rt_gate 59/59 (that
gate emits zero img_pix, so it is blind to D1 -- it proves no regression, not
D1 itself); _rt_acceptance composed 4/4 with the comma slot exact and the
dump-free weapon drift improving -440,756 -> +16,812; FX span-exact still
802/802 across six zones; XModel span-exact 440/440 raid, 491/491 dockside,
766/766 transit, 465/465 cm35, 664/664 zm_nuked_authored.
"""
import struct

from alloc_events import Ev

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

# ---- console struct sizes (Load_Stream r5 immediates in the RPL) ----------
FX_ROOT = 0x4c              # 76   Load_FxEffectDef      +0x18  li r5,0x4c
ELEM = 0x124                # 292  Load_FxElemDef        +0x20  li r5,0x124
VEL_SAMPLE = 0x60           # 96   Load_FxElemDef        +0x64  mulli r5,r0,0x60
VIS_SAMPLE = 0x30           # 48   Load_FxElemDef        +0xa4  mulli r5,r0,0x30
MARK_VISUALS = 8            #      Load_FxElemDefVisuals +0x60  slwi r5,r31,3
VISUALS_PTR = 4             #      Load_FxElemDefVisuals +0x2c8 slwi r5,r31,2
TRAIL_DEF = 0x1c            # 28   Load_FxTrailDef       +0x18  li r5,0x1c
TRAIL_VERT = 0x14           # 20   Load_FxTrailDef       +0x54  mulli r5,r0,0x14
SPOT_LIGHT_DEF = 0xc        # 12   Load_FxElemExtendedDefPtr +0x8c li r5,0xc
MAT_ROOT = 0x68             # 104  Load_Material         +0x18  li r5,0x68
IMG_ROOT = 0x148            # 328  Load_GfxImage         +0x18  li r5,0x148
XMODEL_ROOT = 244           #      (body_relayout NAME_AT / rt_events_xmodel)
LIGHTDEF_ROOT = 0x10        # 16   Load_GfxLightDefPtr   +0x94  li r5,0x10
TECHSET_ROOT = 0x88         # 136  Load_MaterialTechniqueSetPtr +0x88 li r5,0x88

# ---- alignments (DB_AllocStreamPos mask + 1) -----------------------------
AL_ELEMS = 4
AL_SAMPLES = 4
AL_VISUALS = 4
AL_TRAIL = 4
AL_TRAIL_IND = 2
AL_SPOT = 4
AL_EXT_GENERIC = 1
AL_TEXTABLE = 4
AL_CONSTTABLE = 16
AL_STATEBITS = 8
AL_PIXELS = 8192

# ---- FxElemType (Load_FxElemVisuals / Load_FxElemDefVisuals dispatch) ----
T_TRAIL = 5
T_MODEL = 7
T_OMNI_LIGHT = 8            # Load_FxElemVisuals: `cmpwi r9,8; beq <ret>` (no-op)
T_SPOT_LIGHT = 9
T_SOUND = 10
T_DECAL = 11
T_RUNNER = 12

# ---- field offsets -------------------------------------------------------
FX_NAME = 0
FX_COUNTS = 8               # i16 x3 at +8/+10/+12, summed -> elem count
FX_ELEMDEFS = 0x1c          # 28
E_TYPE = 0xb8               # 184
E_VCOUNT = 0xb9             # 185
E_VELCOUNT = 0xba           # 186
E_VISCOUNT = 0xbb           # 187
E_VELSAMPLES = 0xbc         # 188
E_VISSAMPLES = 0xc0         # 192
E_VISUALS = 0xc4            # 196  <- the seagull slot
E_FXREFS = (0xe0, 0xe4, 0xe8, 0xfc)     # 224/228/232/252
E_EXTENDED = 0x100          # 256
E_SPAWNSOUND = 0x118        # 280

# G1: delegate an inline XModel's interior to the XModel band.  OFF by default
# (see the docstring); flip only with a measurement in hand.
XMODEL_INTERIOR = False

# instrumentation: set to a collections.Counter to tally which branches fire
BRANCH = None
# set to a list to collect (abs_off, size, kind) per emission
TRACE = None


def _hit(kind, n=1):
    if BRANCH is not None:
        BRANCH[kind] += n


def _seg(c, size, align, kind):
    if TRACE is not None and size > 0:
        TRACE.append((c.o, size, kind))
    c.seg(size, align)


def _temp(c, size, kind):
    if TRACE is not None and size > 0:
        TRACE.append((c.o, size, 'TEMP:' + kind))
    c.temp(size)


def _cstr(c, kind):
    """Load_XString's string body: DB_AllocStreamPos(0) => align 1, packed."""
    if TRACE is not None:
        TRACE.append((c.o, c.d.index(b'\x00', c.o) + 1 - c.o, kind))
    c.cstr()


def _xstring(c, v, kind):
    """Load_XString(ptr): 0 -> nothing; -1 -> the chars follow (align 1);
    anything else (including -2) -> DB_ConvertOffsetToPointer, no bytes."""
    if v == FOLLOW:
        _cstr(c, kind)
        _hit(kind)


# =====================================================================
# inline sub-assets reached through the *Ptr / *Handle wrappers
# =====================================================================
def _image(c, root=False):
    """Load_GfxImagePtr -> Load_GfxImage (0x021b124c).

        Load_Stream(1, img, 0x148)   root 328 -- caller pushed block 0 => TEMP
        DB_PushStreamPos(5)
        Load_XString(img+0x140)      name, align 1
        if (u32(img+0xb0) != 0):     PIXEL WORD, plain != 0 (no -1 test, and
            DB_AllocStreamPos(0x1fff)    NO streaming-flag test either)
            Load_Stream(1, pixels, u32(img+0xa0))
        Load_Stream(0, img, 0x9c)    no-op
        Load_WiiuTexture             no allocation
        DB_PopStreamPos

    root=True is the TOP-LEVEL asset entry (rt_events_material.image_events):
    identical loader body, but the root must be emitted as the FIRST 'seg' so
    loader_sim.replay_events swallows it with temp_next = root_size.  Emitting
    it as 'temp' there would leave temp_next armed for the next allocation
    (replay_events restores tn0 after a 'temp'), so the name would vanish into
    TEMP as well.  Same reason rt_events_exact.xanim_events writes c.seg(104,4).
    """
    b = c.o
    if root:
        _seg(c, IMG_ROOT, 4, 'img.root')
    else:
        _temp(c, IMG_ROOT, 'img.root')
    _hit('img')
    _xstring(c, c.u32(b + 0x140), 'img.name')
    if c.u32(b + 0xb0) != 0:
        _seg(c, c.u32(b + 0xa0), AL_PIXELS, 'img.pixels')
        _hit('img.pixels')


def _techset(c):
    """Inline MaterialTechniqueSet (G2 -- 0 sites in cm35).  Root 136 -> TEMP;
    the interior is the dump-verified rt_events_mts technique walk."""
    import rt_events_mts as RM
    b = c.o
    _temp(c, TECHSET_ROOT, 'ts.root')
    _hit('techset')
    _xstring(c, c.u32(b), 'ts.name')
    for i in range(32):
        if c.u32(b + 8 + i * 4) == FOLLOW:
            RM._technique_events(c)


def _material(c, root=False):
    """Load_MaterialHandle (0x021ba9d0) -> Load_Material (0x021baa9c).

    Handle:  Load_Stream(atStart, ptr, 4); DB_PushStreamPos(0);
             v = *ptr;  0 -> nothing;  -1/-2 -> DB_AllocStreamPos(3) [in TEMP,
             so it does NOT pad block 5] + Load_Material(1, ...);
             anything else -> DB_ConvertOffsetToAlias, NO bytes.
    Material: Load_Stream(1, mat, 0x68) root 104 -> TEMP (handle pushed 0);
             DB_PushStreamPos(5); name; techSet ptr; then, each gated on
             `!= 0` then `== -1` (an alias falls through to
             DB_ConvertOffsetToPointer and costs nothing):
               textureTable  align 4,  u8(+0x48) * 16   (+ GfxImagePtr per row)
               constantTable align 16, u8(+0x49) * 32
               stateBits     align 8,  u8(+0x4a) * 8
             then thermalMaterial (+0x60) through Load_MaterialHandle again.

    root=True is the TOP-LEVEL asset entry (rt_events_material.material_events)
    -- same Load_Material, root emitted as the first 'seg' so replay_events
    swallows it with temp_next (see _image's note).
    """
    b = c.o
    tc, cc, sbc = c.d[b + 0x48], c.d[b + 0x49], c.d[b + 0x4a]
    tsp = c.u32(b + 0x50)
    ttp, ctp, sbp, th = (c.u32(b + 0x54), c.u32(b + 0x58),
                         c.u32(b + 0x5c), c.u32(b + 0x60))
    if root:
        _seg(c, MAT_ROOT, 4, 'mat.root')
    else:
        _temp(c, MAT_ROOT, 'mat.root')
    _hit('material')
    _xstring(c, c.u32(b), 'mat.name')
    if tsp in PTRS:
        _techset(c)
    if ttp == FOLLOW:
        defs = c.o
        _seg(c, tc * 16, AL_TEXTABLE, 'mat.textable')   # console texdef = 16 B
        for i in range(tc):
            if c.u32(defs + i * 16 + 12) in PTRS:
                _image(c)
    if ctp == FOLLOW:
        _seg(c, cc * 32, AL_CONSTTABLE, 'mat.consts')
    if sbp == FOLLOW:
        _seg(c, sbc * 8, AL_STATEBITS, 'mat.statebits')
    if th in PTRS:
        _material(c)


def _xmodel(c):
    """Load_XModelPtr (0x021c2d78): Push(0) -> XModel root 244 = TEMP, then
    Load_XModel pushes 5 for its followers.  G1: interior unexercised here."""
    import xmodel_probe as XP
    s = c.o
    _hit('xmodel')
    if XMODEL_INTERIOR:
        import rt_events_xmodel as RX
        end, evs = RX.xmodel_events(c.d, s, c.e)
        delta = s - c.base
        for k, ev in enumerate(evs):
            if ev[0] == 'seg':
                _, rel, size, align = ev
                if k == 0:                              # root -> TEMP
                    c.events.append(('temp', rel + delta, size))
                else:
                    c.events.append(('seg', rel + delta, size, align))
            elif ev[0] == 'temp':
                c.events.append(('temp', ev[1] + delta, ev[2]))
            else:
                c.events.append(ev)
        c.o = end
        return
    end = XP.parse_xmodel(c.d, s)[0]
    _temp(c, XMODEL_ROOT, 'xm.root')
    _seg(c, end - c.o, 4, 'xm.rest.APPROX')             # LABELLED GUESS (G1)


def _lightdef(c):
    """Load_GfxLightDefPtr (0x021bc678): root 16 -> TEMP; Push(5);
    Load_XString(+0); Load_Stream(0, ld+4, 8) [no-op] -> Load_GfxImagePtr.
    G3: 0 sites in cm35."""
    b = c.o
    _temp(c, LIGHTDEF_ROOT, 'lightdef.root')
    _hit('lightdef')
    _xstring(c, c.u32(b), 'lightdef.name')
    if c.u32(b + 4) in PTRS:
        _image(c)


# =====================================================================
# FxElemVisuals / FxElemDefVisuals
# =====================================================================
def _elem_visuals(c, etype, vptr_off):
    """Load_FxElemVisuals (0x021ca728): pure dispatch on the elem's TYPE.
    Note elemType 8 returns immediately -- it allocates nothing at all."""
    v = c.u32(vptr_off)
    if etype == T_MODEL:
        if v in PTRS:
            _xmodel(c)
    elif etype == T_RUNNER:
        _xstring(c, v, 'runner.ref')            # + Load_FxEffectDefFromName
    elif etype == T_SOUND:
        _xstring(c, v, 'sound.name')
    elif etype == T_SPOT_LIGHT:
        if v in PTRS:
            _lightdef(c)
    elif etype == T_OMNI_LIGHT:
        return                                   # `cmpwi r9,8; beq <ret>`
    else:
        if v in PTRS:                            # sprite/tail/line/cloud/...
            _material(c)


def _elem_def_visuals(c, eb):
    """Load_FxElemDefVisuals (0x021ca938).  DECAL is tested FIRST, before the
    visualCount<=1 shortcut, so a DECAL with visualCount 1 still takes the
    markArray path (visualCount*8, two MaterialHandles per entry)."""
    etype = c.d[eb + E_TYPE]
    vcount = c.d[eb + E_VCOUNT]
    v = c.u32(eb + E_VISUALS)
    if etype == T_DECAL:
        if v != 0:
            base = c.o
            _seg(c, vcount * MARK_VISUALS, AL_VISUALS, 'markarray')
            _hit('markarray')
            for i in range(vcount):
                for k in (0, 4):
                    if c.u32(base + i * MARK_VISUALS + k) in PTRS:
                        _material(c)
    elif vcount > 1:
        if v != 0:
            base = c.o
            _seg(c, vcount * VISUALS_PTR, AL_VISUALS, 'visuals.array')
            _hit('visuals.array')
            for i in range(vcount):
                _elem_visuals(c, etype, base + i * VISUALS_PTR)
    else:
        _elem_visuals(c, etype, eb + E_VISUALS)


# =====================================================================
# FxElemExtendedDef
# =====================================================================
def _trail_def(c):
    """Load_FxTrailDef (0x021cad04): 28-byte body; verts (u32@+0xc count,
    ptr@+0x10) align 4 x20 B; indices (u32@+0x14 count, ptr@+0x18) align 2."""
    tb = c.o
    _seg(c, TRAIL_DEF, AL_TRAIL, 'trail.def')
    _hit('trail')
    if c.u32(tb + 0x10) != 0:
        _seg(c, c.u32(tb + 0xc) * TRAIL_VERT, AL_TRAIL, 'trail.verts')
    if c.u32(tb + 0x18) != 0:
        _seg(c, c.u32(tb + 0x14) * 2, AL_TRAIL_IND, 'trail.inds')


def _extended(c, eb):
    """Load_FxElemExtendedDefPtr (0x021cadd0)."""
    etype = c.d[eb + E_TYPE]
    v = c.u32(eb + E_EXTENDED)
    if v == 0:
        return
    if etype == T_TRAIL:
        _trail_def(c)                                    # align 4 comes from
    elif etype == T_SPOT_LIGHT:                          # the trail body seg
        _seg(c, SPOT_LIGHT_DEF, AL_SPOT, 'spotlight')    # G3
        _hit('spotlight')
    else:
        # RPL: DB_AllocStreamPos(0); Load_Stream(1, ptr, 1)  -- literally ONE
        # byte at align 1.  G4: never exercised in cm35.
        _seg(c, 1, AL_EXT_GENERIC, 'ext.generic.APPROX')
        _hit('ext.generic')


# =====================================================================
# FxElemDef / FxEffectDef
# =====================================================================
def _elem(c, eb):
    """Load_FxElemDef (0x021caf1c) with atStart=0: the 292-byte body was
    already streamed by the parent array, so the leading Load_Stream is a
    no-op.  Order is exactly the call order in the function."""
    if c.u32(eb + E_VELSAMPLES) != 0:
        _seg(c, (c.d[eb + E_VELCOUNT] + 1) * VEL_SAMPLE, AL_SAMPLES, 'velsamples')
    if c.u32(eb + E_VISSAMPLES) != 0:
        _seg(c, (c.d[eb + E_VISCOUNT] + 1) * VIS_SAMPLE, AL_SAMPLES, 'vissamples')
    _elem_def_visuals(c, eb)
    for off in E_FXREFS:
        _xstring(c, c.u32(eb + off), 'fx.ref')
    _extended(c, eb)
    _xstring(c, c.u32(eb + E_SPAWNSOUND), 'spawnsound')


def fx_events(z, b, e='>'):
    """Console FxEffectDef asset body at `b` -> (end_abs, events).

    Load_FxEffectDef (0x021cb1d4):
        Load_Stream(atStart, fx, 0x4c)   root 76 -> TEMP (EXTRA root_size)
        DB_PushStreamPos(5)
        Load_XString(fx+0)               name, align 1, ONLY when -1
        if (u32(fx+0x1c) != 0):
            DB_AllocStreamPos(3)         align 4
            n = i16(+8) + i16(+0xa) + i16(+0xc)
            Load_Stream(1, elems, n * 0x124)     ONE allocation for the array
            for k in n: Load_FxElemDef(0, elems + k*0x124)
        DB_PopStreamPos
    """
    c = Ev(z, b, e)
    if TRACE is not None:
        TRACE.append((b, FX_ROOT, 'TEMP:fx.root'))
    c.seg(FX_ROOT, 4)                       # root -> TEMP via EXTRA root_size
    _xstring(c, c.u32(b), 'fx.name')
    if c.u32(b + FX_ELEMDEFS) != 0:
        n = sum(struct.unpack_from(e + '3h', z, b + FX_COUNTS))
        if n < 0:
            raise ValueError('fx @%d: negative elem count %d' % (b, n))
        base = c.o
        _seg(c, n * ELEM, AL_ELEMS, 'elemdefs')
        for k in range(n):
            _elem(c, base + k * ELEM)
    return c.o, c.events


EXTRA = {
    'FxEffectDef': (lambda z, o: fx_events(z, o, '>'), FX_ROOT),
}


# =====================================================================
# self-checks
# =====================================================================
def _spans(zone, limit=None):
    import loader_sim as LS
    import rt_events_exact as RTX
    em, spans, CO = LS.simulate(zone, verbose=False,
                                policy=RTX.policy(gfx_skip=0))
    return em, spans, CO


def _selfcheck(zone='mp_skate_gfxtail46.zone'):
    """HARD GATE 1 (structural): the event walk must end byte-exact on the
    independently-validated console probe (wiiu_ref/fx_probe.parse_fx) for
    EVERY FX span, and the span must chain (each asset's start == the previous
    asset's end).  A drift here means loader_sim raises `span drift` and falls
    back to the linear model, i.e. the band silently does nothing."""
    import os, sys
    from collections import Counter
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    sys.path.insert(0, os.path.join(here, '..', 'wiiu_ref'))
    import fx_probe as FP
    global BRANCH
    BRANCH = Counter()
    em, spans, CO = _spans(zone)
    ok = bad = 0
    first_bad = None
    for (i, nm, root, s, e2) in spans:
        if root != 'FxEffectDef' or e2 <= s:
            continue
        try:
            ref = FP.parse_fx(CO, s)[0]
            end, ev = fx_events(CO, s, '>')
        except Exception as ex:
            bad += 1
            first_bad = first_bad or (i, hex(s), str(ex)[:60])
            continue
        if end == ref == e2:
            ok += 1
        else:
            bad += 1
            first_bad = first_bad or (i, hex(s), hex(e2), hex(ref), hex(end))
    print('fx[%s]: span-exact %d/%d%s' %
          (os.path.basename(zone), ok, ok + bad,
           '' if not bad else '  BAD=%d first=%s' % (bad, first_bad)))
    print('  branches: %s' % dict(sorted(BRANCH.items())))
    for g, k in (('G1 inline XModel', 'xmodel'), ('G2 inline techset', 'techset'),
                 ('G3 spot/lightdef', 'lightdef'), ('G3 spotlight ext', 'spotlight'),
                 ('G4 generic ext', 'ext.generic')):
        if BRANCH.get(k):
            print('  !! UNVERIFIED BRANCH EXERCISED: %s x%d  (labelled guess -- '
                  'see the docstring; addresses past it are unproven)'
                  % (g, BRANCH[k]))
    BRANCH = None
    d1_status()
    return bad == 0


def d1_status():
    """Report (never change) the cross-band prerequisite: rt_events_xmodel's
    inline-GfxImage pixel rule.  While it is ('t', 0) every FX address this
    band produces on mp_skate is uniformly 16384 low -- see the docstring."""
    try:
        import rt_events_xmodel as RX
    except ImportError:
        print('  D1: rt_events_xmodel not importable (band absent)')
        return None
    r = RX.RULES.get('img_pix')
    if r == ('v', 8192):
        print("  D1: rt_events_xmodel RULES['img_pix'] = ('v', 8192)  OK")
        return True
    print("  !! D1 OUTSTANDING: rt_events_xmodel RULES['img_pix'] = %r "
          "(want ('v', 8192)). Inline resident pixels are a block-5 seg at "
          "align 8192, not a TEMP copy -- see rt_events_weapon._sub_image. "
          "Until that is set, absolute FX addresses on mp_skate read 16384 low."
          % (r,))
    return False


# LIVE-HANDLE anchors on mp_skate cm35 (each literal occurs exactly once in the
# zone; anchor 3's file offset is the CORRECTED one -- see the docstring).
SKATE_ANCHORS = ((405111, 424480), (497462, 514732), (552768, 587520))


def _anchor_check(zone, anchors=SKATE_ANCHORS):
    """GATE 2: absolute residual in BYTES at each live-handle anchor."""
    import bisect
    import loader_sim as LS
    import rt_events_exact as RTX
    em, spans, CO = LS.simulate(zone, verbose=False, policy=RTX.policy(gfx_skip=0))
    ks = sorted(em.omap); vs = [em.omap[k] for k in ks]

    def rt(f):
        j = bisect.bisect_right(ks, f - 64) - 1
        return None if j < 0 else vs[j] + (f - 64 - ks[j])
    errs = []
    for f, w in anchors:
        g = rt(f)
        errs.append(None if g is None else g - w)
        print('  anchor file %-9d rt %-9s want %-9d  residual %s'
              % (f, g, w, 'n/a' if g is None else '%+d B' % (g - w)))
    if len(set(errs)) == 1 and errs[0] not in (0, None):
        print('  -> residual is a SINGLE CONSTANT %+d B: zero band-local error '
              '(the constant is upstream drift, not this band)' % errs[0])
    d1_status()
    return errs


if __name__ == '__main__':
    import sys
    z = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_gfxtail46.zone'
    _selfcheck(z)
    print('anchors[%s]:' % z)
    _anchor_check(z)
