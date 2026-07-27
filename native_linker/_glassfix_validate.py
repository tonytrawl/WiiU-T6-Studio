"""Validate glassdef_alias_fix on BOTH skate builds, key-free; key opened only to score."""
import struct, pickle, sys
sys.path.insert(0,'.')
import glassdef_alias_fix as GF, alloc_events
from measured_rtmap import MeasuredRuntimeMap

S = pickle.load(open('_skate6_simmap.pkl','rb')); AE = S['assets_end']
s,e = [(t[3],t[4]) for t in S['spans'] if t[2]=='Glasses' and t[4]>t[3]][0]
rtm = MeasuredRuntimeMap('_skate6_simmap.pkl','_skate6_realmap.pkl')
fx=[(i,lo,rs) for i,(lo,rs) in enumerate(rtm.interior) if rs-lo==1218310]
assert len(fx)==4
for i,lo,rs in fx: rtm.interior[i]=(lo,lo+1218322)
rtm._meas_lo=None

K = open('mp_skate_gfxtail46.zone','rb').read()      # SCORING ONLY

# ⚠ FIX 5 is BAKED INTO THE PIPELINE, so mp_skate_final/pipecheck are now rebuilt WITH it
# and already carry the corrected A51B8DFD in all 35 sites. Running the module on them
# straight off disk is a no-op (0/35, residual 24 -> 24) — a real regression check, but
# NOT a test of the repair. To keep the positive path (35/35, residual 59 -> 24) alive we
# reconstruct the pre-fix state first: put back the 19-byte-low emit the reloc chain used
# to produce. UNDRIFT=None runs the zone as-is.
WANT, DRIFT = 0xA51B8DFD, 19

def undrift(Z):
    """Re-introduce the drift FIX 5 corrects; returns how many sites were reverted."""
    gbase, num, _ = GF.glasses_layout(Z, s, e)
    n = 0
    for i in range(num):
        off = gbase + i*GF.GLASS_STRIDE + GF.GLASSDEF_OFF
        if struct.unpack_from('>I', Z, off)[0] == WANT:
            struct.pack_into('>I', Z, off, WANT-DRIFT); n += 1
    return n

for label, pre in (('as-built', None), ('drift reconstructed', undrift)):
  print('--- %s ---' % label)
  for src in ('mp_skate_final.zone','mp_skate_pipecheck.zone'):
    Z = bytearray(open(src,'rb').read()); N=len(Z)
    if pre: pre(Z)
    d0 = sum(1 for x in range(s,e) if Z[x]!=K[x])
    n, delta = GF.fix_glassdef_aliases(Z, s, rtm.rt, AE, verbose=True, span_end=e)
    d1 = sum(1 for x in range(s,e) if Z[x]!=K[x])
    ev = alloc_events.clipmap_events(bytes(Z), 84512493, '>')
    ev = ev[0] if isinstance(ev,tuple) else ev
    print('  %-28s size_ok=%s  gate=%s  Glasses residual %d -> %d'
          % (src, len(Z)==N, 'PASS' if int(ev)==89584099 else 'FAIL', d0, d1))
    # idempotence
    n2,_ = GF.fix_glassdef_aliases(Z, s, rtm.rt, AE, verbose=False, span_end=e)
    print('  idempotent re-run fixed=%d (must be 0)' % n2)

# negative control: the fix must be a NO-OP on the key itself
Zk = bytearray(K)
nk,_ = GF.fix_glassdef_aliases(Zk, s, rtm.rt, AE, verbose=False, span_end=e)
print('NEGATIVE CONTROL — fix applied to the key: %d words changed (must be 0)' % nk)

# generality guard: a SECOND distinct b5 alias must refuse, not collapse (2026-07-20)
Zh = bytearray(K); undrift(Zh)
gb, num, _ = GF.glasses_layout(Zh, s, e)
struct.pack_into('>I', Zh, gb + 5*GF.GLASS_STRIDE + GF.GLASSDEF_OFF, 0xA5123456)
snap = bytes(Zh)
try:
    GF.fix_glassdef_aliases(Zh, s, rtm.rt, AE, verbose=False, span_end=e)
    print('GENERALITY GUARD: FAIL — no refusal on 2 distinct aliases')
except GF.Refuse as ex:
    print('GENERALITY GUARD: PASS — refused (%s), zone untouched=%s'
          % (str(ex).split(';')[0], bytes(Zh)==snap))
print('See _glassfix_gentest.py for the synthetic-shape battery (12 checks).')
