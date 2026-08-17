"""EXACT runtime-allocation events for console MaterialTechniqueSet
(pipeline hardening band, 2026-07-26; template = rt_events_exact.xanim_events).

Measured model (re-verified 2026-07-26 against the boot-16 needle curves with
THIS module's own event stream: 1004/1013 points byte-exact -- asset 6 279/280,
asset 8 725/733): the runtime cursor 4-aligns at EVERY object allocation
while the FILE stays packed, and each GX2 shader MICROCODE segment inside a
technique body is 0x100-ALIGNED (raw GPU bytecode blob; runtime addresses end
0x00). Strings (technique/shader/var names) are packed char allocations
(align 1) -- the small +2..+5 drifts are the 4-align of the NEXT object after
an odd-length name.

Console stream layout provenance: shader_probe.py (verified mp_raid 172/172
techsets, zm_transit 176/176):
  root = 136 B (name ptr @+0, u16 wvf/flags @+4, 32 technique slots @+8);
  inline name chars ONLY when name ptr FOLLOWs; techniques in slot order for
  slots == FOLLOW.
  MaterialTechnique = 8 + passCount*24, then per-pass dynamics in order
  vs-subtree, vertexDecl(92), ps-subtree, args(nargs*8 [+16/inline literal]);
  technique name chars written LAST.
  Shader ref = 12 B; name chars if FOLLOW; inline GX2VertexShader = 308 B /
  GX2PixelShader = 232 B (BE), programSize @regs(208/164), microcode is the
  first dynamic datum, then count/ptr tables (records + FOLLOW record names).

Alias techset rows (zero-length spans) never reach the generator (loader_sim
dispatches events only for FOLLOW asset rows); inline techsets inside GfxWorld
matmem are walked by the GfxWorld path, not here.

DUMP VERIFICATION (2026-07-26, Cemu.exe.30616.dmp = boot-20 of the current
zm_nuked_authored.zone; needle curves at modeled allocation starts):
  * 3 techsets (b5 129550 / 993751 / 14524598), 172 interior points, 134
    microcode 0x100-pads crossed between points: ZERO error steps -- the
    interior model is byte-exact (incl. the two realmap-outlier techsets, so
    their realmap span-start values are measurement artifacts, not drift).
  * adjacent-techset boundary pairs: real gap == model gap with the 136-B
    root in TEMP (diff 0/+1 on pre-microcode boundaries) -- the root does NOT
    consume virtual space, and there is no extra boundary allocation.
  * phase-lock property: the first 0x100 pad inside a techset absorbs any
    entering-cursor error mod 256; adjacent techsets are then in exact
    lockstep (useful: techset chains self-correct upstream band drift mod 256).
MODEL SELECTION (each variant re-scored against the 1013 dump points, so the
band metric never picked the model):
  obj align 4 + microcode 0x100  -> 1004/1013   (shipped)
  obj align 8 + microcode 0x100  ->  994/1013   (scores a "better" band median
                                     -46, but the dump refutes it: fitting the
                                     biased ground truth, not the loader)
  obj align 4 + microcode 0x40   ->   97/1013
  vertexDecl into TEMP           -> refuted (band n drops 115->107 too)

BAND SCORE (harness on zm_nuked_authored.zone / _zmnuked_realmap.pkl):
  n=115  median=-221  zeros=0/115   (baseline linear consumption: 71 / -767 / 0)
  the n rise 71->115 is real: 44 more anchors now land inside the +-5000
  attribution gate. Pure techset->techset deltas: n=49, median +8.

RESIDUAL vs _zmnuked_realmap.pkl (why band zeros are STRUCTURALLY 0): the
ground truth is built by measure_band.cmd_measure as
    real[s-64] = dump_hit - (cand - s)
i.e. it back-projects a needle found at file offset `cand` to the span start
ASSUMING A LINEAR INTERIOR. Techset needles never land at `cand == s` (the
root is FF/00 slot words and fails the 12-distinct-bytes filter), so cand sits
6.5K..27K into the body and real[s] is inflated by exactly the interior pad
accumulated before cand -- measured 411..1810 B here, DIFFERENT per span. A
byte-exact interior model therefore still yields a per-span constant offset
that varies span to span, so consecutive deltas can never be 0. De-biasing by
the model's own pad-before-cand collapses 26/132 consecutive MTS deltas to
exactly 0, the rest being upstream (XModel/Weapon) band drift between anchors.
Re-measuring MTS span starts with early needles (rel ~136, before the first
pad) would de-bias the ground truth; NOT done here because _zmnuked_realmap.pkl
is shared state that the other bands are scored against.

Wire via loader_sim policy seam:
    EX = dict(rt_events_exact.EXTRA_EVENTS); EX.update(rt_events_mts.EXTRA)
    simulate(zone, policy=dict(extra_events=EX, ...))
"""
import struct

from alloc_events import Ev

FOLLOW = 0xFFFFFFFF

MTS_ROOT = 136           # console: 8 B header + 32 technique ptr slots
VS_GX2_SIZE = 308
PS_GX2_SIZE = 232
VS_REGS = 208            # offset of programSize in the GX2 struct
PS_REGS = 164
VD_SIZE = 92
MICROCODE_ALIGN = 0x100  # GX2 shader program blobs are 256-aligned at runtime
VD_MODE = 'seg'          # 'seg' = virtual alloc; 'temp' = registry copy (no virtual)
OBJ_ALIGN = 4            # per-object allocation alignment
# (recordSize, offset-of-name-ptr-or-None) per count/ptr table, in order
VS_TABLES = ((12, 0), (20, 0), (20, None), (8, None), (12, 0), (16, 0))
PS_TABLES = VS_TABLES[:5]


def _gx2_events(c, kind):
    """Inline GX2 shader struct at c.o: struct alloc + 0x100-aligned microcode
    + FOLLOW count/ptr tables (+ packed record name strings)."""
    d = c.d
    body = c.o
    regs = VS_REGS if kind == 'vs' else PS_REGS
    total = VS_GX2_SIZE if kind == 'vs' else PS_GX2_SIZE
    tables = VS_TABLES if kind == 'vs' else PS_TABLES
    size = c.u32(body + regs)
    if size == 0 or size > 0x20000:
        raise ValueError('%s bad progsize 0x%x @0x%x' % (kind, size, body))
    if c.u32(body + regs + 4) != FOLLOW:
        raise ValueError('%s program not FOLLOW @0x%x' % (kind, body))
    counts = [(c.u32(body + regs + 12 + i * 8), c.u32(body + regs + 16 + i * 8))
              for i in range(len(tables))]
    c.seg(total, OBJ_ALIGN)                # GX2 shader struct
    c.seg(size, MICROCODE_ALIGN)           # raw GPU bytecode: 0x100-aligned
    for (cnt, ptr), (rsz, noff) in zip(counts, tables):
        if ptr == FOLLOW:
            if cnt == 0 or cnt > 64:
                raise ValueError('%s bad table count %d' % (kind, cnt))
            base = c.o
            c.seg(cnt * rsz, OBJ_ALIGN)    # record array = one allocation
            if noff is not None:
                for i in range(cnt):
                    if c.u32(base + i * rsz + noff) == FOLLOW:
                        c.cstr()           # record name, packed


def _shader_ref_events(c, kind):
    """MaterialVertexShader/MaterialPixelShader (12 B) + inline name + GX2."""
    sb = c.o
    name_p = c.u32(sb)
    ld_p = c.u32(sb + 4)
    c.seg(12, OBJ_ALIGN)
    if name_p == FOLLOW:
        c.cstr()                           # shader name, packed
    if ld_p == FOLLOW:
        _gx2_events(c, kind)


def _technique_events(c):
    """One MaterialTechnique at c.o (mirrors shader_probe.parse_technique,
    emitting one event per loader allocation)."""
    d = c.d
    t = c.o
    name_p = c.u32(t)
    pc = c.u16(t + 6)
    if not (1 <= pc <= 8) or name_p == 0:
        raise ValueError('bad tech header @0x%x' % t)
    c.seg(8 + pc * 24, OBJ_ALIGN)          # technique struct + pass array
    for i in range(pc):
        po = t + 8 + i * 24
        vd, vs, ps = c.u32(po), c.u32(po + 4), c.u32(po + 8)
        args_p = c.u32(po + 20)
        nargs = d[po + 12] + d[po + 13] + d[po + 14]
        if vs == FOLLOW:
            _shader_ref_events(c, 'vs')
        if vd == FOLLOW:
            if VD_MODE == 'temp':
                c.temp(VD_SIZE)            # decl-registry copy: no virtual
            else:
                c.seg(VD_SIZE, OBJ_ALIGN)  # vertexDecl body (console 92)
        if ps == FOLLOW:
            _shader_ref_events(c, 'ps')
        if args_p == FOLLOW:
            base = c.o
            lits = 0
            for j in range(nargs):
                atype = c.u16(base + j * 8)
                if c.u32(base + j * 8 + 4) == FOLLOW and atype in (1, 4):
                    lits += 1              # inline literal const (never seen)
            c.seg(nargs * 8 + lits * 16, OBJ_ALIGN)
    if name_p == FOLLOW:                   # reorder: name chars written LAST
        c.cstr()


def mts_events(z, o, e='>'):
    """Console MaterialTechniqueSet asset body at `o` -> (end_abs, events)."""
    c = Ev(z, o, e)
    c.seg(MTS_ROOT, 4)                     # root (TEMP via root_size=136)
    if c.u32(o) == FOLLOW:
        c.cstr()                           # techset name inline only on FOLLOW
    for i in range(32):
        if c.u32(o + 8 + i * 4) == FOLLOW:
            _technique_events(c)
    return c.o, c.events


EXTRA = {
    'MaterialTechniqueSet': (lambda z, o: mts_events(z, o, '>'), MTS_ROOT),
}


def _selfcheck(zone='zm_nuked_authored.zone'):
    """Event-walk extent must equal shader_probe.parse_techset for every
    top-level FOLLOW techset row in the zone."""
    import sys, os
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    sys.path.insert(0, os.path.join(here, '..', 'wiiu_ref'))
    import shader_probe as SP
    import loader_sim as LS
    em, spans, CO = LS.simulate(os.path.join(here, zone), verbose=False,
                                policy=dict(gfx_skip=0))
    ok = bad = 0
    first_bad = None
    for (i, nm, root, cs, ce) in spans:
        if root != 'MaterialTechniqueSet' or ce <= cs:
            continue
        try:
            ref, _nt = SP.parse_techset(CO, cs)
            end, ev = mts_events(CO, cs, '>')
        except Exception as ex:
            bad += 1
            first_bad = first_bad or (hex(cs), str(ex)[:60])
            continue
        if end == ref == ce:
            ok += 1
        else:
            bad += 1
            first_bad = first_bad or (hex(cs), hex(ce), hex(ref), hex(end))
    print('mts[%s]: %d OK, %d BAD%s' %
          (zone, ok, bad, '' if not bad else ' first=%s' % (first_bad,)))


if __name__ == '__main__':
    _selfcheck()
