"""EXACT runtime-allocation events for console XModel (pipeline hardening,
2026-07-26; template = rt_events_exact.xanim_events).

Layout source: wiiu_ref/xmodel_probe.py (console XModel root = 244 B,
XSurface = 128 B; end byte-exact on 664/664 zm_nuked_authored spans).

Dump-verified interior alignment rules (needle curves vs boot-16,
Cemu.exe.12984.dmp; pad == (-rt_cursor) mod N).  All of them are pinned by a
HARD GATE: 59 dump-measured absolute runtime b5 addresses across two fully
annotated assets (idx35 @0x26055d, 9 surfaces + 2 inline Materials + 4 inline
GfxImages + collSurfs + boneInfo; idx56 @0x3a4289, 9 surfaces, no inline
materials).  The rules below reproduce 59/59 exactly:
  * XSurface.verts0        -> align 256 (runtime verts0 addresses end 0x00)
  * XSurface.verts1        -> align 64
  * XSurface.triIndices    -> align 16
  * XSurfaceCollisionNodes -> align 16; leafs packed right after (align 1)
  * XRigidVertList (12) + XSurfaceCollisionTree (40) -> 4-packed
  * XModelCollSurf_s array -> align 16; boneInfo 4-packed after collSurfs
  * inline Material roots (104) / inline GfxImage roots (328) reached via
    materialHandles FOLLOW load into TEMP; their name strings, textureTable
    (16 B/entry), stateBits, image names stay virtual packed (align <= 4)
  * XModel root (244) loads into TEMP (root_size in EXTRA)

2026-07-27 -- EVERY ALIGNMENT BELOW IS NOW RPL-READ, NOT FITTED.  The masks
were taken instruction by instruction out of the deployed RPL with
_rpl_dis_robust.py / _rpl_loader_steps.py (guest = file vaddr + 0x2000;
DB_AllocStreamPos(mask) => align == mask + 1).  Sites:
    Load_XModel               0x021c21d8  +0x060 boneNames        ALLOC(0x1)
      +0x250 parentList 0 | +0x2a8 quats 1 | +0x304 trans 3 | +0x360 partClass 0
      +0x3b0 baseMat 3 | +0x3fc SURFACES 0xf | +0x500 materialHandles 3
      +0x604 collSurfs 3 | +0x738 boneInfo 3 | +0x774 himip 3
      +0x7c8 collmaps 3 | +0x844 PhysGeomList 3 | +0x880 PhysGeomInfo[] 0xf
    Load_XSurface             0x021b0490  +0x0a8 verts1 0x3f | +0x12c vertList 3
                                          +0x248 triIndices 0xf
    Load_GfxPackedVertex0Union 0x021b01f0 +0x044 verts0 0xff (vc * 0x18)
    Load_XSurfaceVertexInfo   0x021af1bc  see the skin note in RULES
    Load_XRigidVertList       0x021aeafc  +0x044 collisionTree 3
    Load_XSurfaceCollisionTree 0x021ae850 +0x03c nodes 0xf | +0x078 leafs 1
    Load_Material             0x021baa9c  +0x09c textureTable 3
                                          +0x2e8 constantTable 0xf
                                          +0x33c stateBits 7
    Load_PhysGeomInfo         0x021c1650  +0x044 BrushWrapper 0xf
    Load_BrushWrapper         0x021c1264  sides/verts/planes all ALLOC(3)
    Load_cbrushside_t         0x021c0a70  +0x044 cplane 3 (0x14 B)
    Load_GfxImage             0x021b124c  +0x05c pixels 0x1fff
This replaced the previous FITTED skinned-blob calibration (skin2 -> 32,
skin3 -> 64), which was a least-squares stand-in for an allocation the walker
did not emit at all: XSurfaceVertexInfo+0x20.  Corrections landed together:
surfaces 4->16, collSurfs 16->4, PhysGeomInfo[] 4->16, BrushWrapper 4->16,
collision leafs 1->2, constantTable 4->16, stateBits 4->8, vertsBlend 2->64,
skin1 2->64, skin2 32->2, skin3 64->2, plus the missing +0x20 array.
Scored on the byte-exact FX-alias metric over three GENUINE retail zones
(mp_raid_genuine / mp_dockside_wiiu / zm_transit_original):
    244/1052 -> 379/1052   (raid 93->140, dockside 151->239, transit 0->0)
with the +-4 B sharpness control collapsing to 0 (dockside keeps 9 at +4).
Structural gates unchanged: XModel span-exact 440/440, 491/491, 766/766,
465/465 (cm35) and 664/664 (zm_nuked_authored); _xmodel_rt_gate 59/59.

MEASUREMENT CONVENTION (why the band's zero-delta count is capped).  The
ground-truth _zmnuked_realmap.pkl value for an asset is NOT the loader's cursor
at the asset start: measure_band.cmd_measure stores
    real[s-64] = rt(needle) - (needle - s)
i.e. the LINEAR back-projection of the first content-interesting 24-byte window
that matches uniquely in the dump window.  Since the XModel head is followed by
a 256-aligned verts0, that needle usually sits AFTER a large pad, so
    real[s-64] = C + g,   g = pads(s..needle) - temp(s..needle)
and g swings over hundreds of bytes per asset (XAnimParts has no such pads,
which is why that band reaches 27% exact).  The harness delta therefore equals
    dV + g_i - g_{i+1}
even for a perfect model.  Verified: modelling the needle (first candidate that
lies wholly inside one contiguous POINTER-FREE virtual run -- pointer words are
relocated at load, TEMP regions live in another block) reproduces g exactly on
both gate assets (-173 and -182).  Removing that convention term leaves the
per-asset size error dV with median 0 and mean -6.7 over 315 measurable
consecutive-XModel gaps (was mean -58.7 before the skinned fix).
The sim's omap value at an asset start is deliberately left as the true cursor
C (not C+g): the [s, s+244) window is TEMP-root bytes that no block-5 pointer
can target, and shifting the registration would make omap non-monotonic against
the previous asset's tail.

Every allocation is dispatched through RULES[kind] = ('v', align) virtual or
('t', 0) TEMP, so the calibrator (scratchpad/xm_calib.py) can coordinate-
descend the whole rule set against measured gaps. TRACE (if set to a list)
collects (abs_off, size, kind) per emission for gap-local replay.

Usage:
    import rt_events_xmodel
    policy = dict(extra_events={**rt_events_exact.EXTRA_EVENTS,
                                **rt_events_xmodel.EXTRA})
"""
import struct

from alloc_events import Ev, PTRS

FOLLOW = 0xFFFFFFFF

ROOT = 244          # console XModel body (PC 248 minus `bool bad`+pad)
SURF = 128          # console XSurface (GX2 struct)
MAT_ROOT = 104      # console Material body (Track A)
IMG_ROOT = 328      # console GfxImage body
COLLSURF = 36       # console XModelCollSurf_s (collTris dropped)
BONEINFO = 44
PHYSPRESET = 84

# kind -> ('v', align) virtual alloc | ('t', 0) TEMP (file bytes, no virtual)
RULES = {
    'name':      ('v', 1),
    'bonenames': ('v', 2),
    'parentlist': ('v', 1),
    'quats':     ('v', 2),
    'trans':     ('v', 4),
    'partclass': ('v', 1),
    'basemat':   ('v', 4),
    'surfarr':   ('v', 16),    # RPL Load_XModel +0x3fc  ALLOC(0xf)
    # skinned pre-verts0 blob: was FITTED; now READ OUT OF THE RPL
    # (Load_XSurfaceVertexInfo, guest 0x021af1bc).  The struct is 0x24 B at
    # XSurface+0x10 and the loader allocates, IN THIS ORDER:
    #   +0x08 vertsBlend  ALLOC(0x3f)=64  (v0+3v1+5v2+7v3)*2
    #   +0x20 tension     ALLOC(0x03)= 4  (v0+v1+v2+v3)*4    <- was NOT MODELLED
    #   +0x10             ALLOC(0x3f)=64  u16@+0x0e * 2
    #   +0x14             ALLOC(0x01)= 2  u16@+0x0c * 2
    #   +0x1c             ALLOC(0x01)= 2  u32@+0x18 * 2
    # The old skin2=32 / skin3=64 were a least-squares fit standing in for the
    # missing +0x20 array; they are replaced by the measured masks.
    'vblend':    ('v', 64),
    'skin_ten':  ('v', 4),
    'skin1':     ('v', 64),
    'skin2':     ('v', 2),
    'skin3':     ('v', 2),
    'verts0':    ('v', 256),   # RPL Load_GfxPackedVertex0Union +0x44 ALLOC(0xff)
    'verts1':    ('v', 64),    # RPL Load_XSurface +0xa8 ALLOC(0x3f)
    'vlist':     ('v', 4),     # RPL Load_XSurface +0x12c ALLOC(0x3)
    'ctree':     ('v', 4),     # RPL Load_XRigidVertList +0x44 ALLOC(0x3)
    'cnodes':    ('v', 16),    # RPL Load_XSurfaceCollisionTree +0x3c ALLOC(0xf)
    'cleafs':    ('v', 2),     # RPL Load_XSurfaceCollisionTree +0x78 ALLOC(0x1)
    'tris':      ('v', 16),    # RPL Load_XSurface +0x248 ALLOC(0xf)
    'mharr':     ('v', 4),
    'mat_root':  ('t', 0),
    'mat_name':  ('v', 1),
    # 'techset' rule RETIRED 2026-07-30: inline techsets now delegate to
    # rt_events_fx._techset / rt_events_mts (see _material_events) -- the
    # coarse packed seg lost the 256-aligned microcode pads inside.
    'textable':  ('v', 4),
    'img_root':  ('t', 0),
    'img_name':  ('v', 1),
    # D1 (2026-07-26): inline resident pixels are a BLOCK-5 allocation at align
    # 8192, not a TEMP copy.  Read out of the RPL, not fitted:
    # Load_GfxImage (0x021b124c) does DB_AllocStreamPos(0x1fff) then
    # Load_Stream(1, pixels, baseSize) INSIDE its own DB_PushStreamPos(5) --
    # the Push(0) that makes the 328-byte root TEMP was popped before this.
    # rt_events_weapon._sub_image already had it right; the XModel band did not.
    # EVIDENCE (all dump-free, header blockSize[5] is the retail linker's own
    # total block-5 figure, so this is ground truth per zone):
    #     mp_raid_genuine      -3,875,881  ->     +7,127
    #     mp_dockside_wiiu     -2,501,545  ->     -1,961
    #     zm_transit_original  -1,830,814  ->   -241,566
    # and the dump-measured weapon golden points in _rt_acceptance go from
    # -440,756 to +16,812 while `composed` stays 4/4 and _xmodel_rt_gate stays
    # 59/59 (that gate contains 0 img_pix emissions, so it is blind to this
    # rule -- it cannot confirm D1, but it does prove D1 does not regress it).
    'img_pix':   ('v', 8192),
    'consts':    ('v', 16),    # RPL Load_Material +0x2e8 ALLOC(0xf)
    'sbits':     ('v', 8),     # RPL Load_Material +0x33c ALLOC(0x7)
    'collsurf':  ('v', 4),     # RPL Load_XModel +0x604 ALLOC(0x3)
    'boneinfo':  ('v', 4),     # RPL Load_XModel +0x738 ALLOC(0x3)
    'himip':     ('v', 4),
    'pp_root':   ('t', 0),
    'pp_str':    ('v', 1),
    'cm_arr':    ('v', 4),
    'cm_gl':     ('v', 4),
    'cm_geoms':  ('v', 16),    # RPL Load_XModel +0x880 ALLOC(0xf)
    'cm_bw':     ('v', 16),    # RPL Load_PhysGeomInfo +0x44 ALLOC(0xf)
    'cm_sides':  ('v', 4),
    'cm_plane':  ('v', 4),
    'cm_verts':  ('v', 4),
    'cm_planes': ('v', 4),
}

TRACE = None        # set to a list to collect (abs_off, size, kind)


def _emit(c, size, kind):
    if TRACE is not None and size > 0:
        TRACE.append((c.o, size, kind))
    mode, align = RULES[kind]
    if mode == 't':
        c.temp(size)
    else:
        c.seg(size, align)


def _emit_cstr(c, kind):
    e = c.d.index(b'\x00', c.o)
    _emit(c, e + 1 - c.o, kind)


def _surface_events(c, sb):
    """One console XSurface's dynamic allocations (mirrors
    xmodel_probe.parse_surface_dyn with per-allocation runtime aligns)."""
    d = c.d
    vc = c.u16(sb + 4)
    tc = c.u16(sb + 6)
    if c.u32(sb + 24) in PTRS or c.u32(sb + 32) in PTRS or \
       c.u32(sb + 36) in PTRS or c.u32(sb + 44) in PTRS or \
       c.u32(sb + 48) in PTRS:
        # XSurfaceVertexInfo (0x24 B at XSurface+0x10), transcribed from
        # Load_XSurfaceVertexInfo (guest 0x021af1bc).  Each branch is
        # `if (fld) { if (fld == -1) { AllocStreamPos(mask); Load_Stream(...) }
        # else Off2Ptr }`, so a 0 or an ALIAS costs nothing.
        vi = [c.i16(sb + 16 + j * 2) for j in range(4)]
        s28 = c.u32(sb + 28)
        s40 = c.u32(sb + 40)
        if c.u32(sb + 24) in PTRS:              # +0x08 vertsBlend
            _emit(c, (vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]) * 2, 'vblend')
        if c.u32(sb + 48) in PTRS:              # +0x20, allocated SECOND
            _emit(c, (vi[0] + vi[1] + vi[2] + vi[3]) * 4, 'skin_ten')
        if c.u32(sb + 32) in PTRS:              # +0x10, u16 @+0x0e
            _emit(c, 2 * (s28 & 0xFFFF), 'skin1')
        if c.u32(sb + 36) in PTRS:              # +0x14, u16 @+0x0c
            _emit(c, 2 * (s28 >> 16), 'skin2')
        if c.u32(sb + 44) in PTRS:              # +0x1c, u32 @+0x18
            _emit(c, 2 * s40, 'skin3')
    if c.u32(sb + 52) in PTRS:
        _emit(c, vc * 24, 'verts0')             # 24 B stride
    if c.u32(sb + 72) in PTRS:
        _emit(c, vc * 8, 'verts1')              # console 2nd stream
    if c.u32(sb + 96) in PTRS:                  # vertList (+collision trees)
        vlc = d[sb + 1]
        base = c.o
        _emit(c, vlc * 12, 'vlist')             # XRigidVertList[]
        for k in range(vlc):
            if c.u32(base + k * 12 + 8) in PTRS:
                tb = c.o
                _emit(c, 40, 'ctree')           # XSurfaceCollisionTree
                nc_ = c.u32(tb + 24)
                lc_ = c.u32(tb + 32)
                if c.u32(tb + 28) in PTRS:
                    _emit(c, nc_ * 16, 'cnodes')
                if c.u32(tb + 36) in PTRS:
                    _emit(c, lc_ * 2, 'cleafs')
    if c.u32(sb + 12) in PTRS:
        _emit(c, tc * 6, 'tris')


def _image_events(c):
    """Inline console GfxImage (Load_GfxImage, guest 0x021b124c):
        Load_Stream(1, img, 0x148)      328-B root -> TEMP (the *Ptr wrapper
                                        pushed block 0 before calling in)
        DB_PushStreamPos(5)
        Load_XString(img+0x140)         name, align 1
        if (u32(img+0xb0) != 0) {       <- PLAIN != 0.  No -1 test and no
            DB_AllocStreamPos(0x1fff)      streaming-flag test: transcribed
            Load_Stream(1, px, u32(img+0xa0))  from +0x4c..+0x7c of the RPL.
        }
    The shipped gate was `in PTRS and u8(img+0xab) == 0`; both extra
    conditions are absent from the loader.  Measured neutral on raid /
    dockside / transit (every non-zero pixel word there is FOLLOW with the
    streaming byte clear), so this is a correctness change, not a score fit."""
    b = c.o
    _emit(c, IMG_ROOT, 'img_root')
    if c.u32(b + 320) in PTRS:
        _emit_cstr(c, 'img_name')
    if c.u32(b + 176) != 0:
        _emit(c, c.u32(b + 160), 'img_pix')     # pixels: baseSize, align 8192


def _material_events(c):
    """Inline console Material asset: 104-B root -> TEMP; interior tables
    virtual packed (align <= 4)."""
    d = c.d
    b = c.o
    tc, cc, sbc = d[b + 72], d[b + 73], d[b + 74]
    tsp, ttp, ctp, sbp = (c.u32(b + 80), c.u32(b + 84), c.u32(b + 88),
                          c.u32(b + 92))
    thermal = c.u32(b + 96)
    _emit(c, MAT_ROOT, 'mat_root')
    if c.u32(b) in PTRS:
        _emit_cstr(c, 'mat_name')
    if tsp in PTRS:                             # inline techset ASSET
        # 2026-07-30 (0x2000 pixel-pad campaign): was ONE packed align-1 seg
        # over the whole techset interior ('techset' rule).  That loses every
        # 256-aligned GX2 microcode pad and every TEMP root inside -- the
        # zm_transit oracle bracketed its last missing 8192 to exactly this:
        # the inline XModel @10782383 (weapon @10776857) carries a 133,571-B
        # inline MaterialTechniqueSet whose lost interior pads crystallised
        # to a whole 8192 at the next image pad (string-dedup anchors pin the
        # flip between targets 10,776,749 and 11,405,927).  Delegate to
        # rt_events_fx._techset = root 136 TEMP (Load_MaterialTechniqueSetPtr
        # 0x021ba4b8 pushes block 0) + the dump-verified rt_events_mts
        # technique walk (1004/1013 needle-curve points byte-exact).
        import rt_events_fx as RF
        RF._techset(c)
    if ttp in PTRS:
        defs = c.o
        _emit(c, tc * 16, 'textable')           # 16 B/entry
        for i in range(tc):
            if c.u32(defs + i * 16 + 12) in PTRS:
                _image_events(c)
    if ctp in PTRS:
        _emit(c, cc * 32, 'consts')
    if sbp in PTRS:
        _emit(c, sbc * 8, 'sbits')              # console stateBits 8 B
    if thermal in PTRS:
        _material_events(c)


def _collmap_events(c, ncm):
    """collmaps chain (mirrors xmodel_probe.consume_collmaps)."""
    base = c.o
    _emit(c, 4 * ncm, 'cm_arr')
    for i in range(ncm):
        if c.u32(base + i * 4) not in PTRS:
            continue
        gl = c.o
        _emit(c, 12, 'cm_gl')                   # PhysGeomList
        cnt = c.u32(gl)
        if c.u32(gl + 4) in PTRS:
            gbase = c.o
            _emit(c, 68 * cnt, 'cm_geoms')      # PhysGeomInfo[]
            for g in range(cnt):
                if c.u32(gbase + g * 68) not in PTRS:
                    continue
                bw = c.o
                _emit(c, 96, 'cm_bw')           # BrushWrapper
                nsides = c.u32(bw + 28)
                nverts = c.u32(bw + 84)
                if c.u32(bw + 32) in PTRS:      # sides
                    sbase = c.o
                    _emit(c, 12 * nsides, 'cm_sides')
                    for s in range(nsides):
                        if c.u32(sbase + s * 12) in PTRS:
                            _emit(c, 20, 'cm_plane')   # cplane_s
                if c.u32(bw + 88) in PTRS:
                    _emit(c, 12 * nverts, 'cm_verts')
                if c.u32(bw + 92) in PTRS:
                    _emit(c, 20 * nsides, 'cm_planes')


def xmodel_events(z, b, e='>'):
    """Console XModel at `b` -> (end_abs, events). End byte-exact vs
    xmodel_probe.parse_xmodel (validated 664/664 zm_nuked_authored)."""
    d = z
    c = Ev(d, b, e)
    nb, nrb, ns = d[b + 4], d[b + 5], d[b + 6]
    n = nb - nrb
    p = lambda o: c.u32(b + o)
    ncoll = p(156)
    ncollmaps = d[b + 216]
    if TRACE is not None:
        TRACE.append((c.o, ROOT, 'root'))
    c.seg(ROOT, 4)                              # root -> TEMP via root_size
    if p(0) in PTRS:
        _emit_cstr(c, 'name')
    if p(8) in PTRS:
        _emit(c, 2 * nb, 'bonenames')           # u16[]
    if p(12) in PTRS:
        _emit(c, n, 'parentlist')               # u8[]
    if p(16) in PTRS:
        _emit(c, 8 * n, 'quats')                # s16[4]
    if p(20) in PTRS:
        _emit(c, 16 * n, 'trans')               # f32[4]
    if p(24) in PTRS:
        _emit(c, nb, 'partclass')               # u8[]
    if p(28) in PTRS:
        _emit(c, 32 * nb, 'basemat')            # f32[8]
    if p(32) in PTRS:                           # surfaces
        sb = c.o
        _emit(c, ns * SURF, 'surfarr')
        for i in range(ns):
            _surface_events(c, sb + i * SURF)
    if p(36) in PTRS:                           # materialHandles + inlines
        base = c.o
        _emit(c, 4 * ns, 'mharr')
        for i in range(ns):
            if c.u32(base + i * 4) in PTRS:
                _material_events(c)
    if p(152) in PTRS:
        _emit(c, COLLSURF * ncoll, 'collsurf')
    if p(164) in PTRS:
        _emit(c, BONEINFO * nb, 'boneinfo')
    if p(200) in PTRS:
        _emit(c, 4 * ns, 'himip')               # himipInvSqRadii
    if p(212) in PTRS:                          # inline PhysPreset asset
        pb = c.o
        _emit(c, PHYSPRESET, 'pp_root')
        if c.u32(pb) in PTRS:
            _emit_cstr(c, 'pp_str')
        if c.u32(pb + 28) in PTRS:
            _emit_cstr(c, 'pp_str')
    if p(220) in PTRS and ncollmaps:
        _collmap_events(c, ncollmaps)
    if p(224) in PTRS:
        raise RuntimeError('inline physConstraints (not modeled)')
    return c.o, c.events


EXTRA = {
    'XModel': (lambda z, o: xmodel_events(z, o, '>'), ROOT),
}
