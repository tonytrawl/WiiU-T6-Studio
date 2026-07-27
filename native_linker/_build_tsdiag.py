"""DIAGNOSTIC ONLY — MUST NOT SHIP. Reconcile TECHNIQUE_SET spans to the key on top
of xm2 (which already has clipMap + root names + GameWorldMp+16 + XModel correct).

⛔ The key's techset arg values are DEGENERATE (77 distinct correct hashes collapsed
onto 15; 4,189 intra-pass duplicate bindings that genuine console output never exhibits).
OURS is name-correct at 99.906%. Copying them is importing a bug.

This is run ONLY to settle causality: the key's degenerate values are uniformly
SATISFIABLE (0 unsatisfied demands) whereas ours has 13 unsatisfied materials, whose
stride-16 hash search may never terminate.

  advances  -> unsatisfied demand is causal => ship the principled DEMAND REBIND
               (each of the 13 has 37-135 in-zone candidates), NOT this.
  same crash-> demand is innocent; look elsewhere."""
import struct, pickle, hashlib, sys, collections
sys.path.insert(0,'.'); sys.path.insert(0,'../WiiU_FF_Studio')
import wiiu_ff, alloc_events

z = bytearray(open('mp_skate_final.zone','rb').read())
K = open('mp_skate_gfxtail46.zone','rb').read()
N0=len(z); n=min(N0,len(K))
S = pickle.load(open('_skate6_simmap.pkl','rb'))
tot=sp=0
for t in S['spans']:
    if t[2] != 'MaterialTechniqueSet': continue
    s,e = t[3], min(t[4], n)
    if s>=n or e<=s: continue
    c=0
    for x in range(s,e):
        if z[x]!=K[x]: z[x]=K[x]; c+=1
    if c: sp+=1; tot+=c
print('TECHNIQUE_SET spans reconciled: %d spans, %d bytes' % (sp,tot))
assert len(z)==N0
rem=sum(1 for x in range(n) if z[x]!=K[x])
print('whole-zone residual now: %d' % rem)
ev=alloc_events.clipmap_events(bytes(z),84512493,'>')
ev=ev[0] if isinstance(ev,tuple) else ev
print('GATE clipmap_events end =',ev,'(MUST be 89584099)')
assert int(ev)==89584099
open('mp_skate_tsdiag2.zone','wb').write(bytes(z))
ff=wiiu_ff.pack(bytes(z),'mp_skate')
open('mp_skate_tsdiag2.ff','wb').write(ff)
print('zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
print('ff   md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
