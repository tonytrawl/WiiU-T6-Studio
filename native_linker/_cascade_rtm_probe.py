"""Is the SIM map correct and only the MEASURED correction stale? Compare, for the
mount-string stream offset, the pure sim rt vs the measured rt vs the CORRECT value
(0x6192DB3, proven from the dump). Decides the general fix."""
import sys, pickle, struct, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from measured_rtmap import MeasuredRuntimeMap

CORRECT = 0x6192DB3   # from dump: string at B5+this
z = open('mp_skate_sndstreamfix.zone', 'rb').read()
BODY = 4756; FOLLOW = 0xFFFFFFFF
b = next(m.start()-BODY for m in re.finditer(re.escape(b'mpl_skate.all\x00'), z)
         if struct.unpack_from('>I', z, m.start()-BODY)[0] == FOLLOW)
u32 = lambda o: struct.unpack_from('>I', z, b+o)[0]
cec, cds = u32(0x1270), u32(0x1278)
data_end = (b + BODY) + 14 + cec*20 + cds
sim = pickle.load(open('_skate2_simmap.pkl','rb'))
ae = sim['assets_end']
so = data_end - 16 - ae   # stream offset input to rt()
print('stream offset (data_end-16-ae) = 0x%X ; CORRECT rt = 0x%X' % (so, CORRECT))
print('sim pkl keys:', list(sim.keys()))

# pure sim rt: the simmap likely stores (sim_off -> ...) pairs; try its own rt if present
def try_rt(label, obj):
    for attr in ('rt', 'lookup', 'map'):
        f = getattr(obj, attr, None)
        if callable(f):
            try:
                print('  %s.%s(0x%X) = 0x%X (delta %d)' % (label, attr, so, int(f(so)), int(f(so))-CORRECT)); return
            except Exception as e:
                print('  %s.%s err %s' % (label, attr, e))

rtm = MeasuredRuntimeMap('_skate2_simmap.pkl', '_skate2_realmap.pkl')
print('measured rtm.rt = 0x%X (delta %+d)' % (int(rtm.rt(so)), int(rtm.rt(so))-CORRECT))
# inspect the sim map structure to compute pure-sim value
for k in sim:
    v = sim[k]
    print('  sim[%r] type=%s %s' % (k, type(v).__name__,
          ('len=%d head=%s' % (len(v), v[:3]) if hasattr(v,'__len__') and not isinstance(v,(str,bytes,dict)) else
           (str(v)[:80]))))
