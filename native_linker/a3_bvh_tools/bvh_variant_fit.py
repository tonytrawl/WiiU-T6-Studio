"""Score pick-heuristic variants by tree byte-exactness across all dumped models."""
import struct, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Tony - Main Rig\Downloads\Testing enviroment\native_linker')
from a3_test import walk, tri_coords
import console_bvh as CB
f32 = np.float32
FLT_MAX = CB.FLT_MAX
SCR = os.path.dirname(os.path.abspath(__file__))
MODELS = [210, 598, 394, 470, 183, 258, 232, 253,
          243, 248, 304, 513, 573, 613, 447, 185, 186, 196, 261, 334, 305, 557, 660]

def make_pick(P):
    def pick(bounds, remap, count):
        gb = CB.Bounds()
        for i in range(count):
            gb.expand_bounds(bounds[remap[i]])
        smallest = 1 if f32(gb.maxs[0]-gb.mins[0]) > f32(gb.maxs[1]-gb.mins[1]) else 0
        if f32(gb.maxs[smallest]-gb.mins[smallest]) > f32(gb.maxs[2]-gb.mins[2]):
            smallest = 2
        if P.get('den_largest'):
            largest = 1 if f32(gb.maxs[0]-gb.mins[0]) < f32(gb.maxs[1]-gb.mins[1]) else 0
            if f32(gb.maxs[largest]-gb.mins[largest]) < f32(gb.maxs[2]-gb.mins[2]):
                largest = 2
            ref = largest
        else:
            ref = smallest
        bias = []
        for i in range(3):
            num = f32(f32(f32(gb.maxs[i]-gb.mins[i]) + f32(1.0)) * f32(P.get('mult', 10.0)))
            den = f32(f32(gb.maxs[ref]-gb.mins[ref]) + f32(1.0))
            b = float(f32(num/den))
            if P.get('bias_floor'):
                bias.append(int(b))
            else:
                bias.append(int(b + 0.4999999990686774))
            if P.get('bias_cap'):
                bias[-1] = min(bias[-1], P['bias_cap'])
        best = -1
        ca = cd = None
        thr = P.get('side_thr', 1)
        for axis in range(3):
            mins_l, maxs_l, cop_l = [], [], []
            for i in range(count):
                b = bounds[remap[i]]
                if b.mins[axis] == b.maxs[axis]:
                    cop_l.append(f32(b.mins[axis]))
                else:
                    mins_l.append(f32(b.mins[axis])); maxs_l.append(f32(b.maxs[axis]))
            mins_l.sort(); maxs_l.sort(); cop_l.sort()
            mm = len(mins_l); cc = len(cop_l)
            sf = 0; sb = count; ss = 0; so = 0
            pm = 0; po = 0
            if cc and mm:
                nd = cop_l[0] if f32(cop_l[0]-mins_l[0]) < 0.0 else mins_l[0]
            elif mm: nd = mins_l[0]
            elif cc: nd = cop_l[0]
            else: continue
            mi = xi = oi = 0
            while nd < FLT_MAX:
                dist = nd; nd = FLT_MAX
                ss += pm; sb -= pm; pm = 0
                while mi < mm and mins_l[mi] == dist: pm += 1; mi += 1
                if mi < mm and mins_l[mi] < nd: nd = mins_l[mi]
                while xi < mm and maxs_l[xi] == dist: sf += 1; ss -= 1; xi += 1
                if xi < mm and nd > maxs_l[xi]: nd = maxs_l[xi]
                sf += po; so -= po; po = 0
                while oi < cc and cop_l[oi] == dist: po += 1; oi += 1
                so += po; sb -= po
                if oi < cc and nd > cop_l[oi]: nd = cop_l[oi]
                if sf > thr and sb > thr:
                    h = bias[axis] + count - P.get('bal', 1)*abs(sf-sb) - so - P.get('pen', 4)*ss
                    bonus_ok = (not so and not ss and not pm)
                    if P.get('bonus') == 'always': bonus_ok = True
                    if P.get('bonus') == 'off': bonus_ok = False
                    if bonus_ok:
                        h += int(float(f32(nd - dist)))
                    better = h >= best if P.get('tie_ge') else h > best
                    if better:
                        best = h; ca = axis
                        if so or ss or pm:
                            cd = f32(dist)
                        else:
                            cd = f32(f32(dist + nd) * f32(0.5))
            # end sweep
        if best == -1:
            return None
        return ca, cd
    return pick

def score(P, detail=False):
    pick = make_pick(P)
    orig = CB._pick_split_plane
    CB._pick_split_plane = pick
    tot = ok = 0
    fails = []
    try:
        for idx in MODELS:
            try:
                g = open(os.path.join(SCR, 'idx%d_gen.bin' % idx), 'rb').read()
            except FileNotFoundError:
                continue
            gn, surfs = walk(g)
            for s in surfs:
                for k, tg in enumerate(s['trees']):
                    if tg is None: continue
                    (_, _, triOff, tcnt) = tg['vl']
                    bt = CB.build_tree(tri_coords(g, s, triOff, tcnt), tri_offset=triOff)
                    on = b''.join(struct.pack('>8H', *n) for n in bt['nodes'])
                    ol = struct.pack('>%dH' % len(bt['leafs']), *bt['leafs'])
                    tot += 1
                    if on == tg['nodes'] and ol == tg['leafs']:
                        ok += 1
                    elif detail:
                        fails.append((idx, s['i'], k))
    finally:
        CB._pick_split_plane = orig
    return ok, tot, fails

if __name__ == '__main__':
    variants = [
        ('base', {}),
        ('bonus_off', {'bonus': 'off'}),
        ('bonus_always', {'bonus': 'always'}),
        ('pen3', {'pen': 3}),
        ('pen5', {'pen': 5}),
        ('bias_floor', {'bias_floor': True}),
        ('den_largest', {'den_largest': True}),
        ('mult8', {'mult': 8.0}),
        ('mult12', {'mult': 12.0}),
        ('tie_ge', {'tie_ge': True}),
        ('bal2', {'bal': 2}),
        ('thr0', {'side_thr': 0}),
        ('cap20', {'bias_cap': 20}),
        ('cap40', {'bias_cap': 40}),
    ]
    for name, P in variants:
        ok, tot, _ = score(P)
        print('%-12s %d/%d' % (name, ok, tot))
