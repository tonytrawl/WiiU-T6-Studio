#!/usr/bin/env python3
"""Track C diag: region-aligned diff of our converted FxEffectDef bodies vs genuine.

Walks the GENUINE console body with a real console-side span (incl. inline GfxImage
328-B body + name + baseSize pixels) in lockstep with the PC body, and compares our
converter's OUTPUT for each region against the genuine bytes."""
import sys, os, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
import loader_sim as LS, raid_oracle_control as RC, fx_convert as FXC, pc_walk
import material_convert as MC, walker as W

PTRS = FXC.PTRS
ED = 292


def co_image_span(co, off):
    """Span of one inline console GfxImage stream @off: 328-B body + name + pixels."""
    c = off + 328
    if struct.unpack_from('>I', co, off + 320)[0] in PTRS:
        c = co.index(b'\x00', c) + 1
    if struct.unpack_from('>I', co, off + 176)[0] in PTRS:          # pixels FOLLOW
        c += struct.unpack_from('>I', co, off + 160)[0]             # baseSize
    return c


def co_material_span(co, off):
    """Span of one genuine console material @off (104-B body + dynamic tail)."""
    be32 = lambda o: struct.unpack_from('>I', co, o)[0]
    texc, constc, sbc = co[off + 72], co[off + 73], co[off + 74]
    ts, tt, ct, sbt, th = (be32(off + 80), be32(off + 84), be32(off + 88),
                           be32(off + 92), be32(off + 96))
    src = off + 104
    if be32(off) in PTRS:
        src = co.index(b'\x00', src) + 1
    if ts in PTRS:
        raise RuntimeError('inline techset in genuine FX material @0x%x' % off)
    imgs = []
    if tt in PTRS:
        for i in range(texc):
            if be32(src + i * 16 + 12) in PTRS:
                imgs.append(i)
        src += texc * 16
        for _ in imgs:
            src = co_image_span(co, src)
    if ct in PTRS:
        src += constc * 32
    if sbt in PTRS:
        src += sbc * 8
    if th in PTRS:
        src = co_material_span(co, src)
    return src


def pc_material_span(pc, off):
    return MC.convert_material(pc, off)[1]


def dual_regions(pcbuf, poff, cobuf, goff):
    """Lockstep region walk (PC LE / console BE). Returns list of
    (label, pc_start, pc_end, co_start, co_end)."""
    pu32 = lambda o: struct.unpack_from('<I', pcbuf, o)[0]
    gu32 = lambda o: struct.unpack_from('>I', cobuf, o)[0]
    regs = []
    pc_c = poff + 76
    co_c = goff + 76

    def both(label, pfn, gfn):
        nonlocal pc_c, co_c
        ps, gs = pc_c, co_c
        pc_c = pfn(pc_c)
        co_c = gfn(co_c)
        regs.append((label, ps, pc_c, gs, co_c))

    def cstr_p(c): return pcbuf.index(b'\x00', c) + 1
    def cstr_g(c): return cobuf.index(b'\x00', c) + 1

    if pu32(poff) in PTRS:
        both('name', cstr_p, cstr_g)
    n = sum(struct.unpack_from('<h', pcbuf, poff + o)[0] for o in (8, 10, 12))
    if pu32(poff + 28) not in PTRS:
        return regs
    pbase, gbase = pc_c, co_c
    pc_c += n * ED; co_c += n * ED
    regs.append(('elems x%d' % n, pbase, pc_c, gbase, co_c))
    for i in range(n):
        eb = pbase + i * ED
        etype = pcbuf[eb + 184]; vcount = pcbuf[eb + 185]
        vic = pcbuf[eb + 186]; vsc = pcbuf[eb + 187]
        if pu32(eb + 188) in PTRS:
            sz = (vic + 1) * 96
            both('e%d.vel' % i, lambda c, s=sz: c + s, lambda c, s=sz: c + s)
        if pu32(eb + 192) in PTRS:
            sz = (vsc + 1) * 2 * 24
            both('e%d.vis' % i, lambda c, s=sz: c + s, lambda c, s=sz: c + s)
        vis = pu32(eb + 196)
        if etype == 11:
            if vis in PTRS:
                pmb, gmb = pc_c, co_c
                pc_c += vcount * 8; co_c += vcount * 8
                regs.append(('e%d.marks' % i, pmb, pc_c, gmb, co_c))
                for j in range(vcount):
                    for kk in (0, 4):
                        if pu32(pmb + j * 8 + kk) in PTRS:
                            both('e%d.mark%d.%d.mat' % (i, j, kk),
                                 lambda c: pc_material_span(pcbuf, c),
                                 lambda c: co_material_span(cobuf, c))
        elif vcount > 1:
            if vis in PTRS:
                pab, gab = pc_c, co_c
                pc_c += vcount * 4; co_c += vcount * 4
                regs.append(('e%d.visptrs' % i, pab, pc_c, gab, co_c))
                for j in range(vcount):
                    if pu32(pab + j * 4) in PTRS:
                        if etype in (10, 12):
                            both('e%d.v%d.snd' % (i, j), cstr_p, cstr_g)
                        elif etype <= 6:
                            both('e%d.v%d.mat' % (i, j),
                                 lambda c: pc_material_span(pcbuf, c),
                                 lambda c: co_material_span(cobuf, c))
        else:
            if vis in PTRS:
                if etype in (10, 12):
                    both('e%d.v.snd' % i, cstr_p, cstr_g)
                elif etype <= 6:
                    both('e%d.v.mat(t%d)' % (i, etype),
                         lambda c: pc_material_span(pcbuf, c),
                         lambda c: co_material_span(cobuf, c))
        for off2 in (224, 228, 232, 252):
            if pu32(eb + off2) in PTRS:
                both('e%d.s%d' % (i, off2), cstr_p, cstr_g)
        if pu32(eb + 256) in PTRS:
            if etype == 5:
                def trail(c, buf, E):
                    u = lambda o: struct.unpack_from(E + 'I', buf, o)[0]
                    tb = c; vc_ = u(tb + 12); ic_ = u(tb + 20)
                    c = tb + 28
                    if u(tb + 16) in PTRS: c += vc_ * 20
                    if u(tb + 24) in PTRS: c += ic_ * 2
                    return c
                both('e%d.ext(trail)' % i,
                     lambda c: trail(c, pcbuf, '<'), lambda c: trail(c, cobuf, '>'))
            elif etype == 9:
                both('e%d.ext(spot)' % i, lambda c: c + 12, lambda c: c + 12)
            else:
                both('e%d.ext(u)' % i, lambda c: c + 1, lambda c: c + 1)
        if pu32(eb + 280) in PTRS:
            both('e%d.spawnSnd' % i, cstr_p, cstr_g)
    return regs


def main():
    PC = open('../PC ff/mp_raid.zone', 'rb').read()
    em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
    genli = [(s, e) for (i, nm, r, s, e) in gsp if e > s and r == 'FxEffectDef']
    spans = []
    pc_walk.walk_pc_zone('../PC ff/mp_raid.zone', spans=spans)
    fxs = [s for (i, t, s, e) in spans if W.ASSET_ROOT.get(t) == 'FxEffectDef']
    only = [int(a) for a in sys.argv[1:]]
    from collections import Counter
    cls = Counter()
    for k in range(164):
        if only and k not in only:
            continue
        gs, ge = genli[k]
        try:
            regs = dual_regions(PC, fxs[k], CO, gs)
        except Exception as ex:
            print('k=%3d DUAL WALK FAIL: %s' % (k, ex)); continue
        gend = regs[-1][4] if regs else gs
        tail = ge - gend
        hdr = False
        for (lbl, ps, pe, gst, gen_) in regs:
            if '.mat' not in lbl:
                continue
            gbytes = CO[gst:gen_]
            obytes, _nxt = MC.convert_material(PC, ps)
            if len(obytes) == len(gbytes):
                continue
            if not hdr:
                print('=== k=%3d gen=%d walked=%d tailgap=%d' % (k, ge - gs, gend - gs, tail))
                hdr = True
            # genuine texdef/image detail
            be32 = lambda o: struct.unpack_from('>I', CO, o)[0]
            texc = CO[gst + 72]
            src = gst + 104
            if be32(gst) in PTRS:
                e = CO.index(b'\x00', src); nm = CO[src:e].decode('latin1', 'replace'); src = e + 1
            else:
                nm = '<alias>'
            info = []
            imgs = []
            for i in range(texc):
                iv = be32(src + i * 16 + 12)
                imgs.append('F' if iv in PTRS else ('A' if iv else '0'))
            src += texc * 16
            for i in range(texc):
                if imgs[i] != 'F':
                    continue
                strm = CO[src + 171]
                bs = be32(src + 160)
                w, h = struct.unpack_from('>HH', CO, src + 164)
                info.append('img%d(%s %dx%d base=%d strm=%d)' % (i, imgs[i], w, h, bs, strm))
                src = co_image_span(CO, src)
            print('  %-22s %-34s our=%7d gen=%7d d=%+8d tex=%s %s'
                  % (lbl, nm[:34], len(obytes), len(gbytes),
                     len(obytes) - len(gbytes), ''.join(imgs), ' '.join(info)))
            cls[(''.join(imgs), (len(obytes) - len(gbytes)))] += 1
        if tail and hdr:
            print('  TAILGAP %d bytes unwalked in genuine' % tail)
    print(cls.most_common(30))


if __name__ == '__main__':
    main()
