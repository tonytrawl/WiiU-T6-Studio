"""Measure many GfxWorld-tail points in a skate dump and add them ALL as anchors to
_skate_simmap.pkl + _skate_realmap.pkl, giving a piecewise-accurate tail runtime map
(the +1,252,389 -> +1,079,067 -> +1,081,088 divergence has transitions the single
SndBank anchor missed). Then the caller re-runs _rebake_skate.py."""
import struct, sys, pickle
DMP = sys.argv[1] if len(sys.argv) > 1 else r'C:/CemuFullDumps/Cemu.exe.39080.dmp'
Z = open('mp_skate_measured.zone', 'rb').read()
f = open(DMP, 'rb')
f.seek(8); ns, rva = struct.unpack('<II', f.read(8)); f.seek(rva); dr = f.read(ns*12); stt = {}
for i in range(ns):
    t, s, l = struct.unpack_from('<III', dr, i*12); stt[t] = (s, l)
s, l = stt[9]; f.seek(l); nn, brva = struct.unpack('<QQ', f.read(16)); f.seek(l+16)
ranges = []; off = brva
for i in range(nn):
    a, z = struct.unpack('<QQ', f.read(16)); ranges.append((a, z, off)); off += z
sc = struct.unpack_from('>I', Z, 40)[0]; o = 64 + sc*4
anc = Z[o+200:o+240]; anc_b5 = (o+200)-64
ra = rd = None
for (a, z, fo) in sorted(ranges, key=lambda t: -t[1]):
    if z < 0x1000000: continue
    f.seek(fo); d = f.read(z); i = d.find(anc)
    if i >= 0: ra, ri, rd = a, i, d; break
base = (ra+ri)-anc_b5

GFX = 59696031; END = len(Z); STEP = (END-GFX)//600  # 5x denser
pts = []
for zoff in range(GFX, END-24, STEP):
    nd = None
    for c in range(zoff, min(zoff+STEP, END-20)):
        w = Z[c:c+20]
        if sum(1 for x in w if 32 <= x < 127) >= 18 and len(set(w)) >= 10:
            nd = (c, w); break
    if nd is None: continue
    c, w = nd; i = rd.find(w)
    if i >= 0 and rd.find(w, i+1) < 0:
        pts.append((c-64, (ra+i)-base))          # (zone_b5, runtime_b5)
print('measured %d unique tail points' % len(pts))

S = pickle.load(open('_skate_simmap.pkl', 'rb'))
R = pickle.load(open('_skate_realmap.pkl', 'rb'))
spans = [sp for sp in S['spans'] if sp[1] != 'SNDANCHOR']    # drop prior single anchors
real = R['real']
pts.sort()
for k, (zb5, rt) in enumerate(pts):
    disk = zb5 + 64
    nxt = (pts[k+1][0] + 64) if k+1 < len(pts) else END      # span to next anchor
    spans.append((1000+k, 'SNDANCHOR', 'SndBank', disk, nxt))
    real[zb5] = rt
S['spans'] = spans; R['real'] = real
pickle.dump(S, open('_skate_simmap.pkl', 'wb'))
pickle.dump(R, open('_skate_realmap.pkl', 'wb'))
print('added %d tail anchors; realmap now %d entries' % (len(pts), len(real)))
from measured_rtmap import MeasuredRuntimeMap
rtm = MeasuredRuntimeMap('_skate_simmap.pkl', '_skate_realmap.pkl')
ae = S['assets_end']
for zb5, rt in pts[::5]:
    print('  verify zone_b5=%d rt=%d got=%d' % (zb5, rt, rtm.rt(zb5+64-ae)))
