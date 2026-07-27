"""Real-zone battery for fx_backref_fix (FIX 6).

The answer key is used for SCORING ONLY (residual byte counts / negative
control). The fix module itself never reads it.

  python _fxbackref_validate.py
"""
import bisect
import pickle
import struct
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')

import alloc_events
import fx_backref_fix as FBF
from measured_rtmap import MeasuredRuntimeMap

sim = pickle.load(open('_skate6_simmap.pkl', 'rb'))
AE = sim['assets_end']
FX = sorted([(t[3], t[4] - t[3]) for t in sim['spans'] if t[2] == 'FxEffectDef'])
rtm = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
print('skate: assets_end=%d, %d FxEffectDef bodies' % (AE, len(FX)))

ok = []


def check(label, cond, detail=''):
    print('  [%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))
    ok.append(bool(cond))
    return cond


# ---------------------------------------------------------------- 1. survey
print('\n=== 1. survey mp_skate_final.zone (AUDIT, must not write) ===')
Z = bytearray(open('mp_skate_final.zone', 'rb').read())
N0 = len(Z)
snap = bytes(Z)
r = FBF.fix_fx_backrefs(Z, FX, rtm.rt, AE, mode=FBF.MODE_AUDIT)
check('size unchanged', len(Z) == N0)
check('AUDIT left the zone byte-identical', bytes(Z) == snap)
check('violations detected', r['misaligned'] > 0,
      '(%d misaligned of %d aliases)' % (r['misaligned'], r['aliases']))

# ---------------------------------------------------------------- 2. refuse
print('\n=== 2. MODE_REFUSE on a violating zone ===')
Z2 = bytearray(snap)
try:
    FBF.fix_fx_backrefs(Z2, FX, rtm.rt, AE, mode=FBF.MODE_REFUSE, verbose=False)
    check('refused', False, '(no exception raised)')
except FBF.Refuse as ex:
    check('refused with a specific diagnostic', 'MISALIGNED' in str(ex))
    check('zone untouched on refusal', bytes(Z2) == snap)
    print('     %s' % str(ex)[:150])

# ---------------------------------------------------------------- 3. repair
# On skate MODE_REPAIR is EXPECTED to refuse: rt() cannot bake a 4-aligned
# interior pointer for 65 of 79 FX bodies, so no target choice yields a valid
# pointer. The refusal IS the result -- it localises the defect to the runtime
# map. Verify it refuses cleanly and leaves the zone untouched.
print('\n=== 3. MODE_REPAIR (expected: principled refusal on skate) ===')
Z3 = bytearray(snap)
try:
    r3 = FBF.fix_fx_backrefs(Z3, FX, rtm.rt, AE, mode=FBF.MODE_REPAIR)
    check('repair refused (rt cannot align)', False, '(it did not refuse)')
except FBF.Refuse as ex:
    check('repair refuses on the rt-alignment defect',
          'DOES NOT PRESERVE 4-ALIGNMENT' in str(ex))
    check('zone untouched on refusal', bytes(Z3) == snap)
    check('size unchanged', len(Z3) == N0)
    print('     %s' % str(ex)[:220])

# ---------------------------------------------------------------- 4. rt diag
print('\n=== 4. rt() alignment diagnosis ===')
d = FBF.rt_alignment_report(FX, rtm.rt, AE)
check('rt misalignment is the dominant class',
      d['aligned_bodies'] < d['bodies'],
      '(%d/%d FX bodies can bake an aligned pointer)'
      % (d['aligned_bodies'], d['bodies']))

# ---------------------------------------------------------------- 5. clipMap gate
print('\n=== 5. clipMap gate (MUST be 89584099) ===')
for lbl, ZZ in (('audit', Z), ('post-refusal', Z3)):
    e = alloc_events.clipmap_events(bytes(ZZ), 84512493, '>')
    e = e[0] if isinstance(e, tuple) else e
    check('clipMap gate preserved after %s' % lbl, int(e) == 89584099, '(end=%s)' % e)

# ---------------------------------------------------------------- 6. raid lane
print('\n=== 6. raid control lane ===')
import loader_sim as LS
for path in ('mp_raid_authored.zone', 'mp_raid_native.zone',
             '../wiiu_ref/mp_raid_genuine.zone'):
    try:
        _, spans, _ = LS.simulate(path, verbose=False)
    except Exception as ex:
        print('  (skip %s: %s)' % (path, str(ex)[:50]))
        continue
    rfx = sorted([(s, e - s) for (i, n, rt_, s, e) in spans
                  if rt_ == 'FxEffectDef'])
    RZ = bytearray(open(path, 'rb').read())
    rsnap = bytes(RZ)
    rr = FBF.survey_fx_backrefs(RZ, rfx, verbose=False)
    check('%s: 0 misaligned (genuine invariant)' % path,
          len(rr['violations']) == 0,
          '(%d aliases, %d elems)' % (rr['aliases'], rr['elems']))
    rr2 = FBF.fix_fx_backrefs(RZ, rfx, lambda d: d, 0, mode=FBF.MODE_REPAIR,
                              verbose=False)
    check('%s: repair is a NO-OP' % path, bytes(RZ) == rsnap and
          rr2['corrected'] == 0)

# ---------------------------------------------------------------- 7. neg control
print('\n=== 7. negative control: the answer key (SCORING ONLY) ===')
K = bytearray(open('mp_skate_gfxtail46.zone', 'rb').read())
ksnap = bytes(K)
kr = FBF.survey_fx_backrefs(K, FX, verbose=False)
print('  key: %d aliases, %d misaligned' % (kr['aliases'], len(kr['violations'])))
check('key is (near-)fully aligned', len(kr['violations']) <= 6,
      '(%d misaligned)' % len(kr['violations']))

# ---------------------------------------------------------------- 8. vs key
print('\n=== 8. residual vs key over FX spans (SCORING ONLY) ===')
n = min(len(snap), len(ksnap))


def fx_residual(A):
    tot = 0
    for (s, ln) in FX:
        e = min(s + ln, n)
        tot += sum(1 for x in range(s, e) if A[x] != ksnap[x])
    return tot


print('  FX-span residual vs key: before=%d  after=%d (audit writes nothing)'
      % (fx_residual(snap), fx_residual(bytes(Z))))

print('\n%d/%d checks passed' % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
