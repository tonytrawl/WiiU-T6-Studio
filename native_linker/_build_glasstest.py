"""CAUSAL TEST: is the GLASSES +19 alias family the SceneGlassBrush jq requeue livelock?

Hang autopsy (Task-Mgr dump + log, 2026-07-20): GX2QueryBegin=0 (the boot-41 occlusion
fix HELD), 4.5M log lines, main thread spinning on GX2GetRetiredTimeStamp (GPU never
retires), Server thread RUNNING with stack:
    R_AddSceneEntSurfs_SceneGlassBrush+0x70 <- r_sceneents_glassbrushCallback+0x2c
    <- jqTempWorkerLoop <- jqAssistWithBatches <- Sys_AssistSingle <- SV_ServerThread
jq batch fn returning nonzero == REQUEUE => infinite requeue, frame never completes.

GLASSES span carries 59 residual bytes = a +19 alias family (35 ptrs) -> wrong targets
in the very asset that is hanging.

  clears  -> the +19 family is causal; derive the key-free fix (wrong-TARGET class,
             same shape as the nodeTree one-node-low defect)
  hangs   -> glasses innocent; the stall is elsewhere in the glass-brush draw path"""
import struct, pickle, hashlib, sys
sys.path.insert(0,'.'); sys.path.insert(0,'../WiiU_FF_Studio')
import wiiu_ff, alloc_events

z = bytearray(open('mp_skate_final.zone','rb').read())
K = open('mp_skate_gfxtail46.zone','rb').read()
N0 = len(z)
S = pickle.load(open('_skate6_simmap.pkl','rb'))
sp = [t for t in S['spans'] if t[2] == 'Glasses' and t[4] > t[3]][0]
s, e = sp[3], sp[4]
n = 0
for x in range(s, min(e, len(K))):
    if z[x] != K[x]:
        z[x] = K[x]; n += 1
print('GLASSES bytes reconciled: %d' % n)
assert len(z) == N0, 'size changed'
ev = alloc_events.clipmap_events(bytes(z), 84512493, '>')
ev = ev[0] if isinstance(ev, tuple) else ev
print('GATE clipmap_events end =', ev, '(MUST be 89584099)')
assert int(ev) == 89584099
print('whole-zone residual: %d' % sum(1 for x in range(min(len(z),len(K))) if z[x]!=K[x]))
open('mp_skate_glasstest.zone','wb').write(bytes(z))
ff = wiiu_ff.pack(bytes(z),'mp_skate')
open('mp_skate_glasstest.ff','wb').write(ff)
print('zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
print('ff   md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
