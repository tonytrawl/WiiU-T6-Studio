"""CAUSAL TEST: reconcile ONLY the CONSTANT-DELTA alias families inside GfxWorld
(anchor-carry drift), leaving the intentional R1 placeholder slots untouched.

Rationale: XModel bodies are now byte-exact to the key yet R_SkinStaticModelsCamera
still faults => the bad pointer is NOT XModel content but something pointing INTO it
(smodel instance / dpvs / lighting data), which lives in GfxWorld.

Constant-delta families are anchor-carry drift (many pointers sharing ONE delta).
The placeholder is a constant VALUE (0xA00272D4) producing VARYING deltas -> excluded,
so placeholder INTENT is preserved."""
import struct, pickle, hashlib, sys, collections
sys.path.insert(0,'.'); sys.path.insert(0,'../WiiU_FF_Studio')
import wiiu_ff, alloc_events

PLACEHOLDER = 0xA00272D4
z = bytearray(open('mp_skate_xm2.zone','rb').read())
K = open('mp_skate_gfxtail46.zone','rb').read()
N0 = len(z)
S = pickle.load(open('_skate6_simmap.pkl','rb'))
gw = [t for t in S['spans'] if t[2]=='GfxWorld'][0]
s,e = gw[3], gw[4]
print('GfxWorld span %d..%d' % (s,e))

# census constant-delta families inside GfxWorld, byte-granular
fam = collections.Counter()
for off in range(s, min(e,len(K))-4):
    zv = struct.unpack_from('>I', z, off)[0]
    kv = struct.unpack_from('>I', K, off)[0]
    if zv==kv or zv==PLACEHOLDER: continue
    if (zv>>29)==5 and (kv>>29)==5:
        fam[kv-zv]+=1
BIG = [d for d,c in fam.items() if c >= 25]
print('constant-delta families (>=25 ptrs):', sorted((d,fam[d]) for d in BIG))

n=0; skipped_ph=0
for off in range(s, min(e,len(K))-4):
    zv = struct.unpack_from('>I', z, off)[0]
    kv = struct.unpack_from('>I', K, off)[0]
    if zv==kv: continue
    if zv==PLACEHOLDER: skipped_ph+=1; continue
    if (zv>>29)==5 and (kv>>29)==5 and (kv-zv) in BIG:
        struct.pack_into('>I', z, off, kv); n+=1
print('GfxWorld drift pointers repointed: %d' % n)
print('placeholder slots PRESERVED: %d' % skipped_ph)
assert len(z)==N0, 'size changed'

ev = alloc_events.clipmap_events(bytes(z), 84512493, '>')
ev = ev[0] if isinstance(ev,tuple) else ev
print('GATE clipmap_events end =', ev, '(MUST be 89584099)')
assert int(ev)==89584099
rem = sum(1 for x in range(min(len(z),len(K))) if z[x]!=K[x])
print('whole-zone residual now: %d' % rem)

open('mp_skate_gwa.zone','wb').write(bytes(z))
ff = wiiu_ff.pack(bytes(z),'mp_skate')
open('mp_skate_gwa.ff','wb').write(ff)
print('zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
print('ff   md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
