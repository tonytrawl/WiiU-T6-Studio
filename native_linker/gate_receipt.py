#!/usr/bin/env python3
"""PROOF-OF-EXECUTION for gates -- the (EC) corollary, banked 2026-08-15.

WHY THIS EXISTS. `weapon_graph_gate.run(ours, ref)` had `ref` as a REQUIRED
parameter while `produce_container` called `run(zone)` with one argument. Every
bake raised TypeError inside the caller's `except`, printed the (EC) shout, and
moved on. The gate had NEVER graded a bake -- it only ever ran when I typed it by
hand -- and without that shout the build log would have read as clean.

⛔ THE CLASS: a gate that does not run looks exactly like a gate that found
nothing. Signature drift, an import error, a renamed file, a swallowed
exception -- all of them present as silence, and silence is what a PASS looks
like. Loud logging is not enough on its own, because nothing FAILS when the
shout is missed.

THE RULE: a gate must leave a RECEIPT, and the build must REQUIRE the receipts it
expects. A missing receipt is a build failure, not a log line.

A receipt records the gate's POPULATION as well as its verdict, because
"PASS over 0 items" is the other way this fails silently -- see the boot-73
lesson that a detector is not a census (rule CG).

USAGE
    import gate_receipt
    ...
    gate_receipt.record('weapon-graph', ok, population=len(weapons),
                        detail='tier1 %d / tier2 %d' % (a, b))
and in the build, after the gates:
    gate_receipt.require(['weapon-graph', 'weapon-v3', ...])

⭐ NAMED SKIPS -- added 2026-08-17 (PM ruling, mirage first-boot readiness lane).
A map can legitimately have NOTHING for a gate to grade: mp_mirage ships
WEAPON 0 and GLASSES 0 (measured from the SUBJECT census,
CENSUS_dlc1mp_phase1.json:58 `maps.mp_mirage.classes` -- no WEAPON key at all --
and :83/:84 `weapon_class {}` / `glasses 0`). Under the old rule that map could
not satisfy a required weapon-gate row at all: `record(..., population=0)` fails,
and dropping the row from the require() list re-opens the exact hole this file
exists to close.

    gate_receipt.record_census_skip('weapon-graph', 'mp_mirage', 'WEAPON')

records a receipt whose ZERO population is WITNESSED BY THE SUBJECT, and
`require()` accepts it. Everything else still fails:

  * a MANUAL skip -- `record(name, True, population=0)` -- is REFUSED. There is
    no parameter on `record()` that can forge a witness; a skip can only be
    minted by reading the census.
  * a skip claimed on a map that DOES have that class (mp_downhill ships
    WEAPON x1, census :128) is REFUSED -- run the gate.
  * a missing census, an unknown map, or a census that contradicts itself
    (classes[WEAPON] vs weapon_class disagreeing) is REFUSED. An
    unverifiable witness is not a witness (GX).

A named skip is NOT a pass, and it never prints as one: `require()` renders it
`SKIP(census)` with the witness beside it.

SELFTEST
    python gate_receipt.py --selftest
proves the mechanism FAILS when a gate does not run, and that each of the three
skip arms lands the way it must. A check that cannot fail is not a check --
calibrate both arms, always. A SKIPPED arm is NOT a pass.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# the fleet subject census (phase-1, per-map asset class counts). Overridable so
# a future census can witness maps this one does not carry.
CENSUS_PATH = os.path.join(_HERE, '..', 'CENSUS_dlc1mp_phase1.json')

_RECEIPTS = {}


class GateDidNotRun(RuntimeError):
    pass


class UnbackedSkip(RuntimeError):
    """A skip nobody can check is a skip nobody should trust."""


def _census_population(map_name, asset_class, census_path=None):
    """-> (population:int, witness:dict). REFUSES rather than guessing.

    The census is the SUBJECT: `maps.<map>.classes` is a per-class count dict in
    which an ABSENT key means zero (mp_mirage carries no WEAPON key at all).
    Two classes have a second, independent field in the same record and we
    cross-check them, because a census that contradicts itself cannot witness
    anything: WEAPON <-> `weapon_class` (a dict of the weapon rows) and
    GLASSES <-> `glasses` (an int).
    """
    path = os.path.abspath(census_path or CENSUS_PATH)
    if not os.path.isfile(path):
        raise UnbackedSkip('census not found: %r -- cannot witness a zero '
                           'population for %s/%s' % (path, map_name, asset_class))
    try:
        with open(path) as f:
            doc = json.load(f)
    except (ValueError, OSError) as exc:
        raise UnbackedSkip('census unreadable (%r): %s' % (path, exc))
    maps = doc.get('maps') or {}
    if map_name not in maps:
        raise UnbackedSkip('map %r is not in the census %r (it carries %r) -- '
                           'no witness, so no skip'
                           % (map_name, path, sorted(maps)))
    rec = maps[map_name]
    classes = rec.get('classes') or {}
    key = asset_class.upper()
    pop = classes.get(key, 0)
    cross = None
    if key == 'WEAPON':
        wc = rec.get('weapon_class')
        if isinstance(wc, dict):
            cross = wc.get('WEAPON', 0) if wc else 0
    elif key == 'GLASSES':
        g = rec.get('glasses')
        if isinstance(g, int):
            cross = g
    if cross is not None and cross != pop:
        raise UnbackedSkip(
            'census CONTRADICTS ITSELF for %s/%s: classes=%r but the '
            'independent field says %r. A census that disagrees with itself '
            'cannot witness a skip -- fix the census.'
            % (map_name, key, pop, cross))
    return pop, {'census': path, 'map': map_name, 'class': key,
                 'population': pop, 'cross_check': cross,
                 'map_assets': rec.get('assets'),
                 'key_present': key in classes}


def record_census_skip(name, map_name, asset_class, census_path=None,
                       detail=''):
    """Record a NAMED SKIP whose zero population is witnessed by the SUBJECT.

    Raises UnbackedSkip if the census cannot witness it, or if the map actually
    HAS that class (then there is something to grade -- run the gate).
    """
    pop, witness = _census_population(map_name, asset_class, census_path)
    if pop:
        raise UnbackedSkip(
            'REFUSING a skip: the census says %s ships %d %s asset(s) '
            '(%s). There is a population to grade -- run the gate.'
            % (map_name, pop, asset_class.upper(), witness['census']))
    _RECEIPTS[name] = {
        'ok': True, 'population': 0,
        'detail': detail or ('named skip: %s ships 0 %s (subject census)'
                             % (map_name, asset_class.upper())),
        'skip': witness,
    }
    return witness


def reset():
    _RECEIPTS.clear()


def record(name, ok, population=None, detail=''):
    """Called by a gate at the END of its own run, after it has a verdict."""
    _RECEIPTS[name] = {'ok': bool(ok), 'population': population,
                       'detail': detail}
    return ok


def get(name):
    return _RECEIPTS.get(name)


def require(names, empty_population_is_failure=True, verbose=True):
    """REFUSE unless every named gate actually graded something.

    Raises GateDidNotRun listing what is missing. Also fails a gate that
    recorded a population of 0 -- a gate with nothing to look at has not
    validated anything, however green it printed.
    """
    missing = [n for n in names if n not in _RECEIPTS]
    # A population of 0 fails UNLESS the receipt carries a census witness minted
    # by record_census_skip(). `record()` cannot produce one, so a hand-written
    # "skipped, nothing to do" receipt still fails here -- which is the point.
    empty = [n for n in names
             if n in _RECEIPTS and empty_population_is_failure
             and _RECEIPTS[n]['population'] == 0
             and not _RECEIPTS[n].get('skip')]
    if verbose:
        for n in names:
            r = _RECEIPTS.get(n)
            if r is None:
                print('  GATE RECEIPT %-22s *** ABSENT -- DID NOT RUN ***' % n)
            elif r.get('skip'):
                w = r['skip']
                print('  GATE RECEIPT %-22s SKIP(census) population=0  '
                      '%s ships 0 %s of %s assets [%s]'
                      % (n, w['map'], w['class'], w['map_assets'],
                         os.path.basename(w['census'])))
            else:
                print('  GATE RECEIPT %-22s %-4s population=%s %s'
                      % (n, 'PASS' if r['ok'] else 'FAIL',
                         r['population'], r['detail']))
    if missing or empty:
        raise GateDidNotRun(
            'gates with no proof of execution: %r; gates that graded an EMPTY '
            'population: %r. A gate that did not run is indistinguishable from '
            'a gate that found nothing -- REFUSING to treat this build as '
            'checked.' % (missing, empty))
    return True


def _selftest():
    """Both arms. A receipt mechanism that only ever passes proves nothing."""
    fails = 0

    reset()
    record('a', True, population=10)
    try:
        require(['a'], verbose=False)
        print('PASS arm: a recorded gate is accepted')
    except GateDidNotRun as e:
        print('PASS arm FAILED: %s' % e)
        fails += 1

    reset()
    record('a', True, population=10)
    try:
        require(['a', 'b'], verbose=False)
        print('FAIL arm FAILED: a missing gate was accepted')
        fails += 1
    except GateDidNotRun:
        print('FAIL arm: a gate that did not run is rejected')

    reset()
    record('a', True, population=0)
    try:
        require(['a'], verbose=False)
        print('EMPTY arm FAILED: a gate over 0 items was accepted')
        fails += 1
    except GateDidNotRun:
        print('EMPTY arm: a gate that graded 0 items is rejected')

    # the real regression: the exact defect this file exists for -- a gate whose
    # signature no longer matches its caller
    reset()

    def gate_with_required_arg(zone, ref):
        record('sig', True, population=1)

    try:
        gate_with_required_arg('z')          # the produce_container call shape
    except TypeError:
        pass                                 # swallowed, exactly as the build did
    try:
        require(['sig'], verbose=False)
        print('SIGNATURE arm FAILED: the TypeError-skip was accepted')
        fails += 1
    except GateDidNotRun:
        print('SIGNATURE arm: a TypeError-skipped gate is caught')

    # ---- NAMED SKIP arms (2026-08-17). Three outcomes, all calibrated.
    # A skipped arm is NOT a pass: if the census is absent these arms FAIL.
    reset()
    try:
        w = record_census_skip('weapon-graph', 'mp_mirage', 'WEAPON')
        require(['weapon-graph'], verbose=False)
        print('SKIP arm 1: census-backed skip ACCEPTED '
              '(mp_mirage WEAPON=0 of %s assets, key_present=%s)'
              % (w['map_assets'], w['key_present']))
    except (UnbackedSkip, GateDidNotRun) as e:
        print('SKIP arm 1 FAILED: a census-backed skip was rejected: %s' % e)
        fails += 1

    reset()
    record('weapon-graph', True, population=0, detail='nothing to do, skipped')
    try:
        require(['weapon-graph'], verbose=False)
        print('SKIP arm 2 FAILED: a MANUAL unbacked skip was accepted')
        fails += 1
    except GateDidNotRun:
        print('SKIP arm 2: a manual/unbacked skip is REFUSED')

    reset()
    try:
        # mp_downhill ships WEAPON x1 (census maps.mp_downhill.classes / :128)
        record_census_skip('weapon-graph', 'mp_downhill', 'WEAPON')
        print('SKIP arm 3 FAILED: skip accepted on a map that HAS weapons')
        fails += 1
    except UnbackedSkip as e:
        print('SKIP arm 3: skip on a weapons-bearing map REFUSED -> %s'
              % str(e).split(' (')[0])

    reset()
    for label, kw in (('unknown map', dict(map_name='mp_nosuchmap')),
                      ('missing census', dict(census_path='Z:/no/census.json'))):
        try:
            record_census_skip('x', kw.get('map_name', 'mp_mirage'), 'WEAPON',
                               census_path=kw.get('census_path'))
            print('SKIP control %-15s FAILED (did not refuse)' % label)
            fails += 1
        except UnbackedSkip:
            print('SKIP control %-15s: REFUSED' % label)

    print('gate_receipt selftest: %s' % ('PASS' if not fails else
                                         'FAIL (%d)' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(_selftest() if '--selftest' in sys.argv else _selftest())
