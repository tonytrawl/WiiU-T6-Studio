"""CAUSAL TEST (oracle-diagnostic, not the shipped fix): reconcile ONLY the 374
GameWorldMp +16 pathnode pointers to the key. Everything else stays by-construction.

  advances -> the +16 pathnode family is the 'awaiting challenge' server hang
              => derive the principled anchor rebase (carry 1200527 -> 1200543)
  hangs    -> +16 is innocent (memory's note stands); look elsewhere

Size-neutral, clipMap-gated."""
import struct, pickle, hashlib, sys, collections
sys.path.insert(0,'.'); sys.path.insert(0,'../WiiU_FF_Studio')
import wiiu_ff, alloc_events

z = bytearray(open('mp_skate_pipecheck.zone','rb').read())
K = open('mp_skate_gfxtail46.zone','rb').read()
N0 = len(z)
S = pickle.load(open('_skate6_simmap.pkl','rb'))
sp = [t for t in S['spans'] if t[2]=='GameWorldMp'][0]
s,e = sp[3], sp[4]

n = 0
for off in range(s, min(e,len(K))-4):
    zv = struct.unpack_from('>I', z, off)[0]
    kv = struct.unpack_from('>I', K, off)[0]
    if zv==kv: continue
    if (zv>>29)==5 and (kv>>29)==5 and kv-zv==16:
        struct.pack_into('>I', z, off, kv); n += 1
print('GameWorldMp +16 pointers repointed: %d' % n)
assert len(z)==N0, 'size changed'

rem = sum(1 for x in range(min(len(z),len(K))) if z[x]!=K[x])
print('whole-zone residual now: %d' % rem)
gw = sum(1 for x in range(s,min(e,len(K))) if z[x]!=K[x])
print('GameWorldMp residual now: %d (was 398)' % gw)

ev = alloc_events.clipmap_events(bytes(z), 84512493, '>')
ev = ev[0] if isinstance(ev, tuple) else ev
print('GATE clipmap_events end =', ev, '(MUST be 89584099)')
assert int(ev)==89584099

open('mp_skate_gwtest.zone','wb').write(bytes(z))
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open('mp_skate_gwtest.ff','wb').write(ff)
print('zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
print('ff   md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
