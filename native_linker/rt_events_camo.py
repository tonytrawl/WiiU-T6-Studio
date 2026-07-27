"""EXACT runtime-allocation events for the `WeaponCamo` asset type.

WHY THIS FILE EXISTS
--------------------
A WeaponCamo is a bag of GfxImage POINTERS: two base images plus two per
camoSet.  Every one of them goes through Load_GfxImagePtr -> Load_GfxImage,
whose resident pixels are 8192-ALIGNED in block 5.  Without a band the type was
walked verbatim (bytes charged linearly, no interior alignment at all), so
every camo image silently lost its pixel pad.

zm_transit carries 29 WeaponCamo assets with 25 resident 8192-byte camo images
in one contiguous run; that run was the single largest defect left in the
model.  Adding this band moves zm_transit's blockSize[5] residual

        -221,184  (= -27 * 8192)   ->   -32,768  (= -4 * 8192)

i.e. 23 of the 27 remaining whole-8192 defects on that zone, with mp_raid and
mp_dockside byte-identical (neither carries a WeaponCamo asset) and the FX
alias score and the +/-4 sharpness control unchanged.  The residual stays an
exact multiple of 8192, which is the invariant that says the model has no
sub-alignment error left (see rt_events_material's header).

PROVENANCE -- DISASSEMBLED.  Load_WeaponCamo__Fb, guest 0x021fb0ec
(guest = RPL file vaddr + 0x2000), read instruction by instruction:

    +0x018  li    r5, 0x1c ; bl Load_Stream       root 28 B; the caller
                                                  (Load_WeaponCamoPtr 0x021fb7a8
                                                  / the XAsset dispatch) pushed
                                                  block 0, so the root is TEMP
    +0x02c  li    r3, 5    ; bl DB_PushStreamPos  followers -> block 5
    +0x040                   bl Load_XString      camo+0x00 name, align 1
    +0x058                   bl Load_GfxImagePtr  camo+0x04 solidBaseImage
    +0x06c                   bl Load_GfxImagePtr  camo+0x08 patternBaseImage
    +0x074  lwz   r0, 0xc(r10) ; cmpwi r0,0       camoSets gate (plain != 0)
    +0x084  li    r3, 3    ; bl DB_AllocStreamPos ALIGN 4
    +0x09c  mulli r5, r31, 0x14 ; bl Load_Stream  numCamoSets(+0x10) * 20
            per set, fully unrolled: Load_Stream(0,...,0x14) [no-op] then
            2 x Load_GfxImagePtr  (set+0x00 solid, set+0x04 pattern)
    +0x338  lwz   r4, 0x14(r10) ; cmpwi r4,0      camoMaterialSets gate
    +0x344  li    r3, 3    ; bl DB_AllocStreamPos ALIGN 4
    +0x368  li    r5, 8    ; bl Load_Stream       numCamoMaterials(+0x18) * 8
            per set:  lwz r6, 4(r5) gate (materials ptr)
    +0x3b4  li    r3, 3    ; bl DB_AllocStreamPos ALIGN 4
            mulli r5, r30, 0x2c ; bl Load_Stream  numMaterials * 44
            per material: numBaseMaterials = u16 @+2; each of baseMaterials
            (+4) and camoMaterials (+8) is a Material** array, ALIGN 4
            (Load_WeaponCamoMaterial +0x40 and +0x140, both raw 3), whose
            entries go through Load_MaterialHandle.

Cross-checked against _rpl_align_table.json: EVERY DB_AllocStreamPos on the
WeaponCamo path is raw 3 => align 4 (Load_WeaponCamo +0x84/+0x344/+0x3b4,
Load_WeaponCamoSet* +0x50/+0x6c, Load_WeaponCamoMaterialSet +0x3c,
Load_WeaponCamoMaterial +0x40/+0x140).  The ONLY 8192 on this path comes from
Load_GfxImage +0x5c -- which is precisely what the verbatim model was missing.

The image and material sub-walks are rt_events_fx._image / ._material, the same
already-validated transcriptions the FX and Material bands use (one source of
truth: an inline-vs-camo divergence would otherwise be undetectable).
"""
import struct

import rt_events_fx as RF
from alloc_events import Ev

FOLLOW = RF.FOLLOW
PTRS = RF.PTRS

CAMO_ROOT = 0x1c            # 28   Load_WeaponCamo          +0x18  li r5,0x1c
SET = 0x14                  # 20   Load_WeaponCamo          +0x9c  mulli 0x14
MATSET = 8                  # 8    Load_WeaponCamo          +0x368 li r5,8
MAT = 0x2c                  # 44   Load_WeaponCamo          +0x3c0 mulli 0x2c
AL_ARRAY = 4                # every DB_AllocStreamPos here is raw 3


def camo_events(z, b, e='>'):
    """Top-level WEAPON_CAMO asset.  Root emitted as the first 'seg' so
    loader_sim.replay_events swallows it with temp_next = root_size (see the
    note in rt_events_fx._image)."""
    c = Ev(z, b, e)
    c.seg(CAMO_ROOT, 4)
    if c.u32(b) == FOLLOW:
        c.cstr()                                   # Load_XString, align 1
    for io in (4, 8):
        if c.u32(b + io) in PTRS:
            RF._image(c)
    if c.u32(b + 12) in PTRS:                      # WeaponCamoSet[numCamoSets]
        n = c.u32(b + 16)
        base = c.o
        c.seg(SET * n, AL_ARRAY)
        for i in range(n):
            for io in (0, 4):
                if c.u32(base + SET * i + io) in PTRS:
                    RF._image(c)
    if c.u32(b + 20) in PTRS:                      # WeaponCamoMaterialSet[n]
        n = c.u32(b + 24)
        base = c.o
        c.seg(MATSET * n, AL_ARRAY)
        for i in range(n):
            if c.u32(base + MATSET * i + 4) in PTRS:
                cnt = c.u32(base + MATSET * i)
                mb = c.o
                c.seg(MAT * cnt, AL_ARRAY)
                for j in range(cnt):
                    nbm = struct.unpack_from(e + 'H', z, mb + MAT * j + 2)[0]
                    for po in (4, 8):
                        if c.u32(mb + MAT * j + po) in PTRS:
                            ab = c.o
                            c.seg(4 * nbm, AL_ARRAY)
                            for k in range(nbm):
                                if c.u32(ab + 4 * k) in PTRS:
                                    RF._material(c)
    return c.o, c.events


EXTRA = {
    'WeaponCamo': (lambda z, o: camo_events(z, o, '>'), CAMO_ROOT),
}


def _selfcheck(zones=('zm_transit_original.zone', 'zm_nuked_authored.zone')):
    """STRUCTURAL gate: the event walk must land exactly on the asset span end
    for every WeaponCamo asset (otherwise loader_sim falls back to the verbatim
    model and the band is a silent no-op)."""
    import os
    import loader_sim as LS
    import rt_events_exact as RTX
    here = os.path.dirname(os.path.abspath(__file__))
    ref = os.path.join(here, '..', 'wiiu_ref')
    bad = tot = 0
    for zn in zones:
        p = zn if os.path.exists(zn) else os.path.join(ref, zn)
        if not os.path.exists(p):
            print('%-26s (absent, skipped)' % zn)
            continue
        CO = open(p, 'rb').read()
        em, spans, _ = LS.simulate(CO, verbose=False,
                                   policy=RTX.policy(gfx_skip=0))
        cam = [s for s in spans if s[2] == 'WeaponCamo' and s[4] > s[3]]
        nb = 0
        for (i, nm, root, s, e) in cam:
            tot += 1
            try:
                end, evs = camo_events(CO, s, '>')
            except Exception:
                nb += 1
                continue
            if end != e:
                nb += 1
        print('%-26s %3d WeaponCamo assets, %d span failures' % (zn, len(cam), nb))
        bad += nb
    print('TOTAL %d assets, %d failures -> %s'
          % (tot, bad, 'PASS' if bad == 0 else 'FAIL'))
    return bad == 0


if __name__ == '__main__':
    _selfcheck()
