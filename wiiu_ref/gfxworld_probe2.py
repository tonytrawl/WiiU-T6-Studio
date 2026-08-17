#!/usr/bin/env python3
"""
Task #28: GfxWorld dynamic stream — parallel PC/WiiU walker with per-section
content validation. PC (layout fully known) is the oracle; the Wii U walk mirrors
it and console divergences are solved at the first point of mismatch.

Usage: python gfxworld_probe2.py pc|wiiu [stop_section]
"""
import struct
import sys

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

CFG = {
    'pc': dict(
        path='../PC ff/mp_raid.zone', endian='<', body=0x3f34930, bodysize=1028,
        draw=396, drawmap=dict(rpc=0, probes=4, lightmapCount=12, lightmaps=16,
                               vertexCount=28, size0=32, vd0data=36+0,  # vd0 @36 {data,vb}
                               size1=44, vd1data=48, indexCount=56, indices=60),
        lightgrid=464, model_off=536, matmem_off=572, sun_off=580,
        outdoorImage=728+12,  # PC: outdoorLookupMatrix@664+64=728 -> outdoorImage@728? computed below
        shadowGeom=776, lightRegion=780, dpvs=784, dpvsdyn=900,
        entry_size=4, coeff_size=54, gfxlight=352, fogmodvol=48,
        surf_size=80, surf_mat=48, sminst_size=36, smdi_size=152,
        smdi_model=56, smdi_lmap=104, lmapinfo_size=12, lmap_cnt_off=8,
        occl_off=992, numoccl_off=988, water_off=956,   # occl_off = occluders PTR offset
    ),
    'wiiu': dict(
        path='mp_raid_genuine.zone', endian='>', body=0x2b7029d, bodysize=1076,
        draw=396, drawmap=dict(rpc=4, probes=8, lightmapCount=16, lightmaps=20,
                               vertexCount=32, size0=36, vd0data=44,
                               size1=68, vd1data=76, indexCount=100, indices=104),
        lightgrid=512, model_off=584, matmem_off=620, sun_off=628,
        outdoorImage=788, shadowGeom=824, lightRegion=828, dpvs=832, dpvsdyn=948,
        gfxlight=372, fogmodvol=66,
        entry_size=4, coeff_size=54,
        surf_size=80, surf_mat=48, sminst_size=36, smdi_size=208,
        smdi_model=32, smdi_lmap=80, lmapinfo_size=32, lmap_cnt_off=24,
        occl_off=1040, numoccl_off=1036, water_off=1004,
    ),
}


class W:
    def __init__(self, cfg):
        self.c = cfg
        self.d = open(cfg['path'], 'rb').read()
        self.e = cfg['endian']
        self.b = cfg['body']
        self.o = self.b + cfg['bodysize']

    def u32(self, o):
        return struct.unpack(self.e+'I', self.d[o:o+4])[0]

    def u16(self, o):
        return struct.unpack(self.e+'H', self.d[o:o+2])[0]

    def f32(self, o):
        return struct.unpack(self.e+'f', self.d[o:o+4])[0]

    def g(self, off):
        return self.u32(self.b + off)

    def mark(self, label, note=''):
        print('  %-46s cur=0x%08x %s' % (label, self.o, note))

    def alias(self, v):
        return 0xa0000000 <= v < 0xc0000000

    def ptr_or_alias(self, v):
        return v in PTRS or v == 0 or self.alias(v)

    def cstr(self):
        e = self.d.index(b'\x00', self.o)
        s = self.d[self.o:e]
        self.o = e + 1
        return s.decode('latin-1', 'replace')

    def xyz_ok(self, o, lim=2e5):
        v = [self.f32(o+4*k) for k in range(3)]
        return all(x == x and abs(x) < lim for x in v)


def consume_image(p):
    if p.e == '<':                     # PC: body 80 + name + loadDef(12+resourceSize)
        ib = p.o
        p.o += 80
        if p.u32(ib+72) in PTRS:
            p.cstr()
        if p.u32(ib) in PTRS:          # texture.loadDef
            rs = p.u32(p.o+8)
            p.o += 12 + rs
    else:                              # console: solved 328-byte GX2 image
        ib = p.o
        p.o += 328
        if p.u32(ib+320) in PTRS:
            p.cstr()
        if p.u32(ib+176) in PTRS and p.d[ib+171] == 0:
            p.o += p.u32(ib+160)


def _pc_techset_span(d, off):
    """Consume an inline PC MaterialTechniqueSet at `off` -> end. Delegates to the validated
    Track B parser (native_linker/techset_pc). Reached when a Material's techniqueSet ptr is
    FOLLOWING or INSERT (both load the techset inline per OAT LoadPtr_MaterialTechniqueSet)."""
    import os
    import sys
    nl = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'native_linker')
    if nl not in sys.path:
        sys.path.insert(0, nl)
    import techset_pc
    return techset_pc.parse_techset_pc(d, off)


def consume_material(p):
    """Inline Material asset: PC 112 / console 104 (solved layouts)."""
    if p.e == '<':
        b = p.o
        p.o += 112
        tc, cc, sbc = p.d[b+84], p.d[b+85], p.d[b+86]
        tsp, ttp = p.u32(b+92), p.u32(b+96)
        ctp, sbp, thermal = p.u32(b+100), p.u32(b+104), p.u32(b+108)
        if p.u32(b) in PTRS:
            p.cstr()
        if tsp in PTRS:                       # inline MaterialTechniqueSet: FOLLOWING *and* INSERT
            p.o = _pc_techset_span(p.d, p.o)  # both load the techset inline (OAT LoadPtr, -1 & -2)
        if ttp in PTRS:
            defs = p.o
            p.o += tc * 16
            for i in range(tc):
                if p.u32(defs + i*16 + 12) in PTRS:
                    consume_image(p)
        if ctp in PTRS:
            p.o += cc * 32
        if sbp in PTRS:
            p.o += sbc * 20        # PC GfxStateBits = 20 (3 D3D ptrs kept)
        if thermal in PTRS:
            consume_material(p)
    else:
        import xmodel_probe

        class _C:
            pass
        cc = _C()
        cc.d = p.d
        cc.o = p.o
        cc.skip = lambda n: setattr(cc, 'o', cc.o + n)
        def _cstr(maxlen=160):
            e = p.d.index(bytes([0]), cc.o)
            s2 = p.d[cc.o:e]
            cc.o = e + 1
            return s2.decode('latin-1', 'replace')
        cc.cstr = _cstr
        xmodel_probe.consume_material(p.d, cc)
        p.o = cc.o


def walk(p, stop=None):
    c = p.c
    g = p.g

    # ---- streamInfo / sunLight / volumes / dpvsPlanes (verified previously) ----
    if g(24) in PTRS:
        p.o += g(20) * 48
        p.mark('streamInfo.aabbTrees x%d' % g(20))
    if g(32) in PTRS:
        p.o += g(28) * 4
        p.mark('streamInfo.leafRefs x%d' % g(28))
    if g(36) in PTRS:                       # skyBoxModel XString — inline ONLY when FOLLOWING
        # streamInfo's serialized extent can exceed the counts by a small
        # variable amount (dockside: +20; raid: +0 — the CAVEATS "-16/-20"
        # quirk). The inline sky string is the first hard re-sync point:
        # scan up to 48 bytes for the printable NUL-terminated name start.
        # (Un-resynced this desynced the whole dockside zone walk @758.)
        for adj in range(48):
            q = p.o + adj
            e2 = p.d.index(b'\x00', q)
            if e2 > q and all(0x20 <= b_ < 0x7f for b_ in p.d[q:e2]) \
                    and e2 - q >= 4 and (q == p.o or p.d[q - 1] not in
                                         range(0x20, 0x7f)):
                p.o = q
                break
        s = p.cstr()                         # (alias/null => 0 bytes, e.g. raid). Loaded by the
        p.mark('skyBoxModel', repr(s))       # engine right after streamInfo, before sunLight.
    if g(256) in PTRS:
        p.o += c['gfxlight']
        p.mark('sunLight (%d)' % c['gfxlight'])
    for co, po, sz, l in [(268, 272, 32, 'coronas'), (276, 280, 16, 'shadowMapVol'),
                          (284, 288, 16, 'smVolPlanes'), (292, 296, 24, 'exposureVol'),
                          (300, 304, 16, 'expVolPlanes'), (308, 312, 100, 'fogVol'),
                          (316, 320, 16, 'fogVolPlanes'), (324, 328, c['fogmodvol'], 'fogModVol'),
                          (332, 336, 16, 'fogModPlanes'), (340, 344, 36, 'lutVol'),
                          (348, 352, 16, 'lutVolPlanes')]:
        if g(po) in PTRS and g(co):
            p.o += g(co) * sz
            p.mark('%s x%d' % (l, g(co)))
    cellCount = g(372)
    if g(376) in PTRS:
        p.o += g(8) * 20
        p.mark('dpvsPlanes.planes x%d' % g(8))
    if g(380) in PTRS:
        p.o += g(12) * 2
        p.mark('dpvsPlanes.nodes x%d' % g(12))

    # ---- cells ----
    if g(392) in PTRS:
        # RESYNC: the region chain from streamInfo to here can drift by small
        # map-specific amounts (dockside serializes planeCount-1 planes; the
        # streamInfo counts understate by 0/20). Cells carry verifiable world
        # bounds — align on the offset where the first cells' bounds parse.
        def _cells_score(base):
            n = min(cellCount, 4)
            return sum(1 for i in range(n)
                       if p.xyz_ok(base + i * 48) and p.xyz_ok(base + i * 48 + 12))
        if _cells_score(p.o) < min(cellCount, 4):
            best = (p.o, _cells_score(p.o))
            for dlt in range(-64, 68, 4):
                sc = _cells_score(p.o + dlt)
                if sc > best[1]:
                    best = (p.o + dlt, sc)
            p.o = best[0]
        cb = p.o
        p.o += cellCount * 48
        bad = 0
        trees = portals = pverts = smi = probeidx = 0
        for i in range(cellCount):
            co = cb + i*48
            if not (p.xyz_ok(co) and p.xyz_ok(co+12)):
                bad += 1
            atc, atp = p.u32(co+24), p.u32(co+28)
            pc_, pp_ = p.u32(co+32), p.u32(co+36)
            rc_ = p.u32(co+40) & 0xff if p.e == '<' else p.d[co+40]
            rp_ = p.u32(co+44)
            if atp in PTRS:
                ab = p.o
                p.o += atc * 40
                trees += atc
                for j in range(atc):
                    ao = ab + j*40
                    sic = p.u16(ao+30)
                    if p.u32(ao+32) in PTRS:
                        p.o += sic * 2
                        smi += sic
            if pp_ in PTRS:
                pb = p.o
                p.o += pc_ * 92
                portals += pc_
                for j in range(pc_):
                    po2 = pb + j*92
                    vcnt = p.d[po2+40]
                    if p.u32(po2+36) in PTRS:
                        p.o += vcnt * 12
                        pverts += vcnt
            if rp_ in PTRS:
                p.o += rc_
                probeidx += rc_
        p.mark('cells x%d' % cellCount,
               'badBounds=%d trees=%d smIdx=%d portals=%d pverts=%d probeIdx=%d'
               % (bad, trees, smi, portals, pverts, probeidx))
    if stop == 'cells':
        return p

    # ---- draw ----
    dm = c['drawmap']
    D = c['draw']
    rpc = g(D + dm['rpc'])
    if g(D + dm['probes']) in PTRS:
        rb = p.o
        p.o += rpc * 76
        # validate: origins finite
        badp = sum(0 if p.xyz_ok(rb + i*76) else 1 for i in range(rpc))
        inl_img = inl_vol = 0
        for i in range(rpc):
            ro = rb + i*76
            img, pv, pvc = p.u32(ro+60), p.u32(ro+64), p.u32(ro+68)
            if img in PTRS:
                inl_img += 1
                consume_image(p)
            if pv in PTRS:
                p.o += pvc * 96      # GfxReflectionProbeVolumeData
                inl_vol += pvc
        p.mark('draw.reflectionProbes x%d' % rpc,
               'badOrigin=%d inlineVol=%d' % (badp, inl_vol))
    lmc = g(D + dm['lightmapCount'])
    if g(D + dm['lightmaps']) in PTRS:
        lb = p.o
        p.o += lmc * 8
        note = []
        for i in range(lmc):
            for k in (0, 4):
                v = p.u32(lb + i*8 + k)
                note.append('F' if v in PTRS else ('a' if p.alias(v) else '0'))
                if v in PTRS:
                    consume_image(p)
        p.mark('draw.lightmaps x%d [%s]' % (lmc, ''.join(note)))
    if g(D + dm['vd0data']) in PTRS:
        p.o += g(D + dm['size0'])
        p.mark('draw.vd0.data %d' % g(D + dm['size0']))
    if g(D + dm['vd1data']) in PTRS:
        p.o += g(D + dm['size1'])
        p.mark('draw.vd1.data %d' % g(D + dm['size1']))
    ic = g(D + dm['indexCount'])
    if g(D + dm['indices']) in PTRS:
        ib = p.o
        p.o += ic * 2
        vc0 = g(D + dm['vertexCount'])
        mx = max(p.u16(ib + 2*k) for k in range(0, min(ic, 3000)))
        p.mark('draw.indices x%d' % ic, 'maxIdx(sample)=%d vertexCount=%d' % (mx, vc0))
    if stop == 'draw':
        return p

    # ---- lightGrid ----
    L = c['lightgrid']
    mins = [p.u16(p.b+L+4+2*k) for k in range(3)]
    maxs = [p.u16(p.b+L+10+2*k) for k in range(3)]
    ra = g(L+20)
    if g(L+28) in PTRS:
        cnt = maxs[ra] - mins[ra] + 1
        rb = p.o
        p.o += cnt * 2
        p.mark('lightGrid.rowDataStart x%d' % cnt,
               'first,last=%d,%d' % (p.u16(rb), p.u16(rb+2*(cnt-1))))
    if g(L+36) in PTRS:
        p.o += g(L+32)
        p.mark('lightGrid.rawRowData %d' % g(L+32))
    ec = g(L+40)
    if g(L+44) in PTRS:
        es = c['entry_size']
        eb = p.o
        p.o += ec * es
        p.mark('lightGrid.entries x%d @%d' % (ec, es))
    cc = g(L+48)
    if g(L+52) in PTRS:
        p.o += cc * 168
        p.mark('lightGrid.colors x%d' % cc)
    qc = g(L+56)
    if g(L+60) in PTRS:
        p.o += qc * c['coeff_size']
        p.mark('lightGrid.coeffs x%d @%d' % (qc, c['coeff_size']))
    if g(L+68) in PTRS:
        p.o += g(L+64) * 40
        p.mark('lightGrid.skyGridVolumes x%d' % g(L+64))
    if stop == 'lightgrid':
        return p

    # ---- models / materialMemory / sun / outdoorImage ----
    mc = g(c['model_off'])
    if g(c['model_off']+4) in PTRS:
        mb = p.o
        p.o += mc * 64
        ok = sum(1 for i in range(mc)
                 if p.xyz_ok(mb+i*64+32) and p.xyz_ok(mb+i*64+44)
                 and p.u32(mb+i*64+56) < 20000)
        p.mark('models x%d' % mc, 'boundsOK=%d/%d' % (ok, mc))
    mmc = g(c['matmem_off'])
    if g(c['matmem_off']+4) in PTRS:
        ab = p.o
        p.o += mmc * 8
        inl = 0
        for i in range(mmc):
            if p.u32(ab + i*8) in PTRS:
                consume_material(p)
                inl += 1
        p.mark('materialMemory x%d' % mmc, 'inlineMaterials=%d' % inl)
    # sun (embedded): spriteMaterial/flareMaterial asset refs
    for so in (c['sun_off']+4, c['sun_off']+8):
        if g(so) in PTRS:
            consume_material(p)
            p.mark('sunflare material inline')
    if g(c['outdoorImage']) in PTRS:
        consume_image(p)
        p.mark('outdoorImage inline')
    if stop == 'models':
        return p

    # ---- shadowGeom / lightRegion ----
    plc = g(264)
    if g(c['shadowGeom']) in PTRS:
        sb = p.o
        p.o += plc * 12
        for i in range(plc):
            so = sb + i*12
            surfC, smodC = p.u16(so), p.u16(so+2)
            if p.u32(so+4) in PTRS:
                p.o += surfC * 2
            if p.u32(so+8) in PTRS:
                p.o += smodC * 2
        p.mark('shadowGeom x%d' % plc)
    if g(c['lightRegion']) in PTRS:
        rb = p.o
        p.o += plc * 8
        for i in range(plc):
            hc = p.u32(rb + i*8)
            if p.u32(rb + i*8 + 4) in PTRS:
                hb = p.o
                p.o += hc * 80
                for j in range(hc):
                    ho = hb + j*80
                    if p.u32(ho+76) in PTRS:
                        p.o += p.u32(ho+72) * 20
        p.mark('lightRegion x%d' % plc)
    if stop == 'lightregion':
        return p

    # ---- dpvs (reorder: smodelCastsShadow, sortedSurfIndex, smodelInsts,
    #      surfaces, smodelDrawInsts; RUNTIME members = 0) ----
    dp = c['dpvs']
    smodelCount = g(dp)
    staticSurfaceCount = g(dp+4)
    smVis = g(dp+40)
    sfVis = g(dp+44)
    surfaceCount = g(16)
    if g(dp+108) in PTRS:                 # smodelCastsShadow
        p.o += smVis
        p.mark('dpvs.smodelCastsShadow %d bytes' % smVis)
    if g(dp+80) in PTRS:                  # sortedSurfIndex
        sb = p.o
        p.o += staticSurfaceCount * 2
        mx = max(p.u16(sb+2*k) for k in range(min(staticSurfaceCount, 3000)))
        p.mark('dpvs.sortedSurfIndex x%d' % staticSurfaceCount,
               'maxIdx(sample)=%d (surfaceCount=%d)' % (mx, surfaceCount))
    if g(dp+84) in PTRS:                  # smodelInsts
        sb = p.o
        sz = c['sminst_size']
        p.o += smodelCount * sz
        ok = sum(1 for i in range(0, smodelCount, 37)
                 if p.xyz_ok(sb+i*sz) and p.xyz_ok(sb+i*sz+12))
        p.mark('dpvs.smodelInsts x%d @%d' % (smodelCount, sz),
               'boundsOK=%d/%d(sampled)' % (ok, (smodelCount+36)//37))
    if g(dp+88) in PTRS:                  # surfaces
        sb = p.o
        sz = c['surf_size']
        p.o += surfaceCount * sz
        ok = sum(1 for i in range(0, surfaceCount, 41)
                 if p.alias(p.u32(sb+i*sz+c['surf_mat'])))
        p.mark('dpvs.surfaces x%d @%d' % (surfaceCount, sz),
               'matAliasOK=%d/%d(sampled)' % (ok, (surfaceCount+40)//41))
    if g(dp+92) in PTRS:                  # smodelDrawInsts
        sb = p.o
        sz = c['smdi_size']
        p.o += smodelCount * sz
        ok = sum(1 for i in range(0, smodelCount, 43)
                 if p.alias(p.u32(sb+i*sz+c['smdi_model'])))
        # lmapVertexInfo[4] per inst: lmapVertexColors FOLLOW -> numLmapVertexColors*4
        lmap = 0
        for i in range(smodelCount):
            io = sb + i*sz + c['smdi_lmap']
            for k in range(4):
                lo = io + k*c['lmapinfo_size']
                if p.u32(lo) in PTRS:
                    n = p.u16(lo + c['lmap_cnt_off'])
                    p.o += n * 4
                    lmap += n
        p.mark('dpvs.smodelDrawInsts x%d @%d' % (smodelCount, sz),
               'modelAliasOK=%d/%d(sampled) lmapColors=%d' % (ok, (smodelCount+42)//43, lmap))
    if stop == 'dpvs':
        return p

    # ---- tail (OAT load order): waterBuffers[2], {water,corona,rope,lut}Material,
    #      occluders, outdoorBounds, heroLights, heroLightTree ----
    OCC = c['occl_off']                     # occluders PTR offset (materials precede it)
    for wi in range(2):                     # waterBuffers[2] inline: buffer data if FOLLOWING
        wb = c['water_off'] + wi * 8
        if g(wb + 4) in PTRS:
            p.o += g(wb)                    # bufferSize bytes (= bufferSize/16 vec4_t)
            p.mark('waterBuffer[%d] %d bytes' % (wi, g(wb)))
    # materials: waterMaterial/coronaMaterial/ropeMaterial/lutMaterial. Material asset ptrs load
    # inline for BOTH FOLLOWING and INSERT (OAT LoadPtr_Material, -1 & -2); INSERT also registers
    # the material as a listed asset. lutMaterial is INSERT on raid/nuke/skate -> inline material.
    for moff in range(OCC - 20, OCC - 4, 4):
        if g(moff) in PTRS:
            consume_material(p)
            p.mark('tail material inline (body+%d)' % moff)

    noc = g(c['numoccl_off'])
    if g(OCC) in PTRS:
        p.o += noc * 68
        p.mark('occluders x%d' % noc)
    noo = g(OCC + 4)                         # outdoorBounds: numOutdoorBounds x GfxOutdoorBounds(24)
    if g(OCC + 8) in PTRS:
        p.o += noo * 24
        p.mark('outdoorBounds x%d' % noo)
    # heroLights (56 PC-identical) / heroLightTree (32: mins,maxs,left,right)
    hlc, htc = g(OCC + 12), g(OCC + 16)
    if g(OCC + 20) in PTRS:
        p.o += hlc * 56
        p.mark('heroLights x%d' % hlc)
    if g(OCC + 24) in PTRS:
        p.o += htc * 32
        p.mark('heroLightTree x%d' % htc)
    p.mark('GFXWORLD END')
    return p


def main():
    plat = sys.argv[1] if len(sys.argv) > 1 else 'pc'
    stop = sys.argv[2] if len(sys.argv) > 2 else None
    cfg = dict(CFG[plat])
    if len(sys.argv) > 3:
        cfg['path'] = sys.argv[3]
        cfg['body'] = int(sys.argv[4], 0)
    p = W(cfg)
    print('%s body=0x%x' % (plat, p.b))
    walk(p, stop)


if __name__ == '__main__':
    main()
