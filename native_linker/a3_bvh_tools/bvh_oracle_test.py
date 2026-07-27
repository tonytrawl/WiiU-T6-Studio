"""Rebuild collision trees from genuine console geometry and compare vs the
genuine trees byte-for-byte."""
import struct, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from a3_build import build_tree

SCR = os.path.dirname(os.path.abspath(__file__))
FOLLOW = 0xFFFFFFFF; INSERT = 0xFFFFFFFE; PTRS = (FOLLOW, INSERT)
BODY = 244; SURF = 128

def u32(d, x): return struct.unpack('>I', d[x:x+4])[0]
def u16(d, x): return struct.unpack('>H', d[x:x+2])[0]
def f32(d, x): return struct.unpack('>f', d[x:x+4])[0]

def walk(d):
    o = 0
    nb, nrb, ns = d[o+4], d[o+5], d[o+6]
    c = o + BODY
    if u32(d, o) in PTRS:
        e = d.index(b'\x00', c); name = d[c:e].decode('latin-1'); c = e+1
    else:
        name = '<alias>'
    for k, sz in ((8, 2*nb), (12, nb-nrb), (16, 8*(nb-nrb)),
                  (20, 16*(nb-nrb)), (24, nb), (28, 32*nb)):
        if u32(d, o+k) in PTRS: c += sz
    surfs = []
    sb = c; c += ns * SURF
    for i in range(ns):
        b = sb + i*SURF
        vc, tc = u16(d, b+4), u16(d, b+6)
        vi = [struct.unpack('>h', d[b+16+j*2:b+18+j*2])[0] for j in range(4)]
        if any(u32(d, b+k) in PTRS for k in (24, 32, 36, 44)):
            s28, s40 = u32(d, b+28), u32(d, b+40)
            c += (vi[0]+3*vi[1]+5*vi[2]+7*vi[3])*2 + 2*(s28 & 0xffff) + 2*(s28 >> 16) + 2*s40
        v0 = None
        if u32(d, b+52) in PTRS: v0 = c; c += vc*24
        if u32(d, b+72) in PTRS: c += vc*8
        trees = []
        vls = []
        if u32(d, b+96) in PTRS:
            vlc = d[b+1]; base = c; c += vlc*12
            for k in range(vlc):
                vl = base + k*12
                vls.append((tuple(u16(d, vl+j*2) for j in range(4)),
                            u32(d, vl+8) in PTRS))
            for (vlt, has) in vls:
                if not has: trees.append(None); continue
                tb = c; c += 40
                nc_, lc_ = u32(d, tb+24), u32(d, tb+32)
                nodes = d[c:c+nc_*16]; c += nc_*16
                leafs = d[c:c+lc_*2]; c += lc_*2
                trees.append(dict(vl=vlt, ts=[f32(d, tb+j*4) for j in range(6)],
                                  nc=nc_, lc=lc_, nodes=nodes, leafs=leafs))
        tris = None
        if u32(d, b+12) in PTRS:
            tris = c; c += tc*6
        surfs.append(dict(i=i, vc=vc, tc=tc, v0=v0, tris=tris, trees=trees))
    return name, surfs

def tri_coords(d, s, t0, tcnt):
    """Console verts0 positions (BE f32 xyz @ stride 24) for tris [t0, t0+tcnt)."""
    out = []
    for t in range(t0, t0 + tcnt):
        tri = []
        for j in range(3):
            vi_ = u16(d, s['tris'] + (t*3 + j)*2)
            p = s['v0'] + vi_*24
            tri.append(np.array([f32(d, p), f32(d, p+4), f32(d, p+8)],
                                dtype=np.float32))
        out.append(tri)
    return out

def main():
    tot = ok = 0
    for idx in (210, 598, 394, 470, 183, 258, 232, 253):
        g = open(os.path.join(SCR, 'idx%d_gen.bin' % idx), 'rb').read()
        gn, surfs = walk(g)
        for s in surfs:
            for k, tg in enumerate(s['trees']):
                if tg is None: continue
                (boneOff, vcnt, triOff, tcnt) = tg['vl']
                bt = build_tree(tri_coords(g, s, triOff, tcnt), tri_offset=triOff)
                our_nodes = b''.join(struct.pack('>8H', *n) for n in bt['nodes'])
                our_leafs = struct.pack('>%dH' % len(bt['leafs']), *bt['leafs'])
                our_ts = [float(x) for x in list(bt['trans']) + list(bt['scale'])]
                tot += 1
                msgs = []
                if len(bt['nodes']) != tg['nc']:
                    msgs.append('nc %d vs %d' % (len(bt['nodes']), tg['nc']))
                if len(bt['leafs']) != tg['lc']:
                    msgs.append('lc %d vs %d' % (len(bt['leafs']), tg['lc']))
                if not msgs and our_nodes != tg['nodes']:
                    fd = next(j for j in range(len(our_nodes)) if our_nodes[j] != tg['nodes'][j])
                    msgs.append('nodes@%d: our %s gen %s' %
                                (fd, our_nodes[fd&~15:(fd&~15)+16].hex(), tg['nodes'][fd&~15:(fd&~15)+16].hex()))
                if not msgs and our_leafs != tg['leafs']:
                    msgs.append('leafs differ')
                if any(abs(a-b) > 1e-30 and (b == 0 or abs(a/b-1) > 1e-6)
                       for a, b in zip(our_ts, tg['ts'])):
                    msgs.append('ts %s vs %s' % (our_ts, tg['ts']))
                if msgs:
                    print('idx%d s%d t%d (tc=%d): %s' % (idx, s['i'], k, tcnt, '; '.join(msgs)))
                else:
                    ok += 1
    print('trees byte-exact: %d / %d' % (ok, tot))

if __name__ == '__main__':
    main()
