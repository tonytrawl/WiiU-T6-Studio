import struct, pickle
DMP = r'C:/CemuFullDumps/Cemu.exe.33920.dmp'
GUEST_BASE = 0x00000138e02d0000
Z = open('mp_skate_measured.zone','rb').read()
f = open(DMP,'rb')
f.seek(8); ns, rva = struct.unpack('<II', f.read(8)); f.seek(rva); dr = f.read(ns*12); stt={}
for i in range(ns):
    t,s,l = struct.unpack_from('<III', dr, i*12); stt[t]=(s,l)
s,l = stt[9]; f.seek(l); nn,brva = struct.unpack('<QQ', f.read(16)); f.seek(l+16)
ranges=[]; off=brva
for i in range(nn):
    a,z = struct.unpack('<QQ', f.read(16)); ranges.append((a,z,off)); off+=z
sc = struct.unpack_from('>I', Z, 40)[0]; o = 64+sc*4
anc = Z[o+200:o+240]; anc_b5=(o+200)-64
ra=rd=None
for (a,z,fo) in sorted(ranges,key=lambda t:-t[1]):
    if z < 0x1000000: continue
    f.seek(fo); d=f.read(z); i=d.find(anc)
    if i>=0: ra,ri,rd=a,i,d; break
base_host=(ra+ri)-anc_b5           # host addr of block5 rt offset 0
b5_guest = base_host - GUEST_BASE
print('block5 guest base = 0x%08x' % b5_guest)
R8 = 0x1bd44eb4
rt = R8 - b5_guest
print('R8 rt offset = %d' % rt)
# invert measured map: find anchors bracketing rt
R = pickle.load(open('_skate_realmap.pkl','rb'))['real']
pts = sorted((v,k) for k,v in R.items())
lo = [p for p in pts if p[0] <= rt][-3:]
hi = [p for p in pts if p[0] > rt][:3]
for v,k in lo+hi: print('  anchor rt=%d zone_b5=%d delta=%d' % (v,k,v-k))
if lo:
    v,k = lo[-1]
    zguess = rt - (v-k)
    print('zone_b5 guess = %d (disk %d)' % (zguess, zguess+64))
# what's in the zone there
S = pickle.load(open('_skate_simmap.pkl','rb'))
spans = [sp for sp in S['spans'] if sp[1]!='SNDANCHOR']
for sp in spans:
    if sp[3] <= zguess+64 < sp[4]:
        print('span:', sp)
# search wild value as BE bytes in zone near there
import re
wild = struct.pack('>I', 0x92b3432a)
idxs=[]; i=Z.find(wild)
while i>=0 and len(idxs)<10:
    idxs.append(i); i=Z.find(wild,i+1)
print('wild-value BE occurrences in zone:', idxs)
