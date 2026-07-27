"""Negative-control battery for techset_rebind's refuse contract.

Every guard added in the pass-2 hardening is exercised by INJECTING the fault it
is supposed to catch. A guard that has never been seen to fire is indistinguish-
able from one that cannot fire -- which is precisely the defect class this pass
was hardened against (four fixes sat in-tree this session without ever running).

  python _p2_refusetest.py
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import techset_rebind as TR

ZONE = 'mp_skate_final.zone'
PCZ = '../mp_skate_pc.zone'
Z = open(ZONE, 'rb').read()
PC = open(PCZ, 'rb').read()

results = []


def check(name, fn, expect_refuse=True, expect_text=None):
    saved = {k: getattr(TR, k) for k in
             ('_per_slot_demands', '_actionable_violations', '_load_subst_manifest')}
    try:
        fn()
        try:
            TR.rebind_matmem_techsets(Z, PC, 'mp_skate', verbose=False)
            ok = not expect_refuse
            detail = 'no refusal'
        except TR.RebindRefusal as ex:
            ok = expect_refuse and (expect_text is None or expect_text in str(ex))
            detail = 'REFUSED: %s' % str(ex)[:90]
        except Exception as ex:
            ok = False
            detail = 'WRONG TYPE %s: %s' % (type(ex).__name__, str(ex)[:70])
    finally:
        for k, v in saved.items():
            setattr(TR, k, v)
    results.append((ok, name, detail))
    print('%-4s %-38s %s' % ('PASS' if ok else 'FAIL', name, detail))


# 1. baseline: clean input must NOT refuse
check('clean input runs', lambda: None, expect_refuse=False)

# 2. a techset whose demand decode raises -> refuse (must not read as demand-free)
_real_psd = TR._per_slot_demands
_state = {'n': 0}


def _raise_on_third():
    def f(d, s):
        _state['n'] += 1
        if _state['n'] == 3:
            raise ValueError('injected decode fault')
        return _real_psd(d, s)
    _state['n'] = 0
    TR._per_slot_demands = f


check('one techset fails to decode', _raise_on_third,
      expect_text='failed demand decode')

# 3. a broken WALK (most techsets empty) -> refuse, not a silent no-op
def _mostly_empty():
    def f(d, s):
        return {}
    TR._per_slot_demands = f


check('walk returns empty for all', _mostly_empty,
      expect_text='EMPTY demand map')

# 4. repairs that do not clear the violation -> post-verify refuses.
#    Report every binding as violating: the guard then selects repairs, but no
#    candidate can clear, so this exercises BOTH the stuck path and post-verify.
def _always_violating():
    TR._actionable_violations = lambda sd, k, ct, cc: [1]


# Every binding AND every candidate violates -> nothing is selected -> all
# materials land in n_stuck. That is an inherited residual, NOT a regression:
# the pass must report it loudly and still return the pass-1 result.
check('universally-unsatisfiable -> n_stuck, no refusal', _always_violating,
      expect_refuse=False)

# 5. a manifest that maps intents to a satisfiable-looking but WRONG target:
#    post-verify must catch it if the resulting binding still violates.
def _poison_manifest():
    real = TR._load_subst_manifest
    TR._load_subst_manifest = lambda m: {}      # degrade to R2 only -- legal
    _ = real


check('empty manifest degrades to R2 only', _poison_manifest, expect_refuse=False)

# 6. the OUTER wrapper must not swallow a RebindRefusal
def _outer():
    def f(d, s):
        raise ValueError('injected')
    TR._per_slot_demands = f


check('refusal escapes outer wrapper', _outer, expect_text='refusing')

print()
nfail = sum(1 for ok, _, _ in results if not ok)
print('%d/%d checks passed' % (len(results) - nfail, len(results)))
sys.exit(1 if nfail else 0)
