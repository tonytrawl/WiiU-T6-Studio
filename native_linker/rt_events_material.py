"""EXACT runtime-allocation events for the TOP-LEVEL `Material` and `GfxImage`
asset types (the two bands rt_events_exact._BANDS was still missing).

WHY THIS FILE EXISTS
--------------------
loader_sim dispatches an exact event model per ASSET ROOT; anything without a
band keeps the old verbatim/linear model, which charges every byte of the asset
span to block 5 minus one PC-sized root and emits no interior alignment at all.
For these two types the linear model is wrong in three ways at once:

  * the 328-byte console GfxImage root and the 104-byte console Material root
    load into TEMP, not block 5 (their `*Ptr`/`*Handle` wrappers push block 0);
  * resident pixels are 8192-ALIGNED in block 5 (Load_GfxImage +0x5c);
  * the generic walker uses the PC root size for GfxImage (64, not 328), so a
    top-level IMAGE asset was over-charged by 264 bytes AND missed its pad.

zm_transit carries 375 top-level MATERIAL assets (raid 2, dockside 2), so
without the Material band 55% of the FX-alias corpus was unreachable: every
inline GfxImage root stayed in block 5 and the 8192 pixel pad was never paid.

PROVENANCE -- DISASSEMBLED, NOT FITTED.  Both types are loaded by the SAME
functions rt_events_fx already models, so this module is a thin root wrapper
over rt_events_fx._material / ._image rather than a second transcription
(one source of truth; a divergence between the inline and top-level walks
would be undetectable).  The two sites that this band adds over the linear
model, read out of the deployed RPL (guest = file vaddr + 0x2000):

    Load_GfxImage       0x021b124c
        +0x18  li   r5, 0x148          -> root 328 bytes, Load_Stream(1,...)
        +0x28  li   r3, 5 ; bl DB_PushStreamPos   -> followers in block 5
        +0x44  bl   Load_XString(img+0x140)       -> name, align 1
        +0x4c  lwz  r0, 0xb0(r12) ; cmpwi r0,0 ; beq   -> PLAIN != 0 gate
        +0x58  li   r3, 0x1fff ; bl DB_AllocStreamPos  -> ALIGN 8192
        +0x6c  lwz  r5, 0xa0(r12) ; bl Load_Stream     -> baseSize bytes
    Load_GfxImagePtr    0x021b1504
        +0x2c  li   r3, 0 ; bl DB_PushStreamPos        -> the root is TEMP
        +0x54  li   r3, 3 ; bl DB_AllocStreamPos       -> align 4 INSIDE temp,
               so it does not pad block 5
    Load_Material       0x021baa9c   (sizes/aligns already cited in
        rt_events_fx._material: root 0x68, textureTable align 4 / 16 B rows,
        constantTable align 16, stateBits align 8)

EVIDENCE (dump-free; header blockSize[5] is the retail linker's own total
block-5 figure, so it is ground truth per zone).  Adding the two bands makes
the residual an EXACT MULTIPLE OF 8192 on all three genuine zones at once:

    zone                 before             after
    mp_raid_genuine        +7,127   ->     +16,384  = +2 * 8192
    mp_dockside_wiiu       +6,231   ->     +24,576  = +3 * 8192
    zm_transit_original  -233,630   ->    -221,184  = -27 * 8192

That is the falsifiable part.  A residual that is an exact multiple of the
pixel alignment means the model has ZERO sub-8192 error anywhere in the walk:
after the last align(8192) both the model cursor and the retail cursor are
multiples of 8192 and the remaining allocations are packed align-1 file bytes,
so residual == (model cursor - retail cursor) at that align, and every
remaining defect is a whole 8192 pad.  Hitting that on three independent zones
simultaneously is ~1 in 5e11 by chance.  Before the bands, none of the three
was a multiple; raid's own residue mod 8192 was 7,127.  NOTE HONESTLY: on raid
and dockside the SIGNED residual gets numerically larger, because the missing
pixel pad had been cancelling an unrelated over-allocation of the same size
class; the sub-quantum part of raid's error (7,127) is what disappeared.

The FX-alias byte-exact score (rt_events_fx's metric) goes 379/1052 -> 600/1052
(36.0% -> 57.0%), entirely from zm_transit 0/583 -> 221/583, with raid and
dockside byte-identical and the +/-4 sharpness control unchanged.

CONTROL that pins the 8192: forcing the pixel align to 1 or 256 inside this
band (everything else untouched) collapses transit 221 -> 0 while leaving the
b5 residual plausible-looking.  The alignment is load-bearing and measured.
REFUTED en route: making the inline GfxImage root a block-5 allocation instead
of TEMP improves transit's b5 to -14,680 and leaves the alias score at 221 --
a better-looking number that the RPL contradicts (Load_GfxImagePtr +0x2c
pushes block 0).  It is a FIT and is NOT taken.
"""
import rt_events_fx as RF
from alloc_events import Ev


def material_events(z, b, e='>'):
    """Top-level MATERIAL asset: Load_Material with the asset root as root."""
    c = Ev(z, b, e)
    RF._material(c, root=True)
    return c.o, c.events


def image_events(z, b, e='>'):
    """Top-level IMAGE asset: Load_GfxImage with the asset root as root."""
    c = Ev(z, b, e)
    RF._image(c, root=True)
    return c.o, c.events


EXTRA = {
    'Material': (lambda z, o: material_events(z, o, '>'), RF.MAT_ROOT),
    'GfxImage': (lambda z, o: image_events(z, o, '>'), RF.IMG_ROOT),
}


def _selfcheck(zones=('mp_raid_genuine.zone', 'mp_dockside_wiiu.zone',
                      'zm_transit_original.zone')):
    """STRUCTURAL gate: for every top-level Material / GfxImage asset the event
    walk must end exactly on the asset span end (otherwise loader_sim silently
    falls back to the verbatim model and the band is a no-op for that asset),
    and the events must TILE [start,end) once -- strictly increasing, no gaps,
    no overlaps.  An overlap is what a genuine double-counted allocation would
    look like, so this is also the double-count assertion."""
    import os
    import loader_sim as LS
    import rt_events_exact as RTX
    here = os.path.dirname(os.path.abspath(__file__))
    ref = os.path.join(here, '..', 'wiiu_ref')
    bad = tot = 0
    for zn in zones:
        p = zn if os.path.exists(zn) else os.path.join(ref, zn)
        CO = open(p, 'rb').read()
        em, spans, _ = LS.simulate(CO, verbose=False,
                                   policy=RTX.policy(gfx_skip=0))
        fn = {'Material': material_events, 'GfxImage': image_events}
        n = nb = 0
        for (i, nm, root, s, e) in spans:
            if root not in fn or e <= s:
                continue
            n += 1
            tot += 1
            try:
                end, evs = fn[root](CO, s, '>')
            except Exception:
                nb += 1
                continue
            cur = 0
            ok = end == e
            for ev in evs:
                if ev[0] == 'seg':
                    _, rel, size, al = ev
                elif ev[0] == 'temp':
                    _, rel, size = ev
                else:
                    ok = False
                    break
                if rel != cur:
                    ok = False
                    break
                cur += size
            if not ok or s + cur != end:
                nb += 1
            bad += 0 if ok else 1
        print('%-26s %4d assets  %d span/tile failures' % (zn, n, nb))
    print('TOTAL %d assets, %d failures -> %s'
          % (tot, bad, 'PASS' if bad == 0 else 'FAIL'))
    return bad == 0


if __name__ == '__main__':
    _selfcheck()
