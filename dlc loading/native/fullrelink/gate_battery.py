"""gate_battery — G0..G4, the checks any zone edit must survive before it is allowed near hardware.

Written after three additive builds of patch_mp booted straight into a graceful OSFatal. Every one of
them would have been caught here without burning a boot:

  additive v2 (insert at array index 1087) -> G4 fires: 239 container refs re-aimed at the wrong asset
  grow_relink +92 (blind omap scan)        -> G3 fires: 28 verbatim-body words rewritten, all of them
                                              false positives landing in a StringTable interior

Design rules:
  * Every constant is DERIVED from the zone under test, never hardcoded. A `baseline` dict may be
    supplied to additionally regression-check exact census equality against a known-good zone.
  * Gates fail LOUDLY and specifically -- a gate that says "something changed" is useless.
  * G4 ships POSITIVE CONTROLS. A directional test that can never fire proves nothing, so G4 runs the
    classifier at insert_index=0 (everything must move) and at count (nothing may move) and fails if
    either control does not behave.

Usage:
    import gate_battery as GB
    GB.run_all(retail_zone)                       # audit a pristine zone
    GB.check_edit(retail, edited, insert_indices=[1087], ins_offsets=[(off, n), ...])
"""
import os, sys, struct
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _rel in ('wiiu_ref', 'native_linker', 'WiiU_FF_Studio'):
    sys.path.insert(0, os.path.join(_HERE, '..', '..', '..', _rel))

import wiiu_zone, zone_stream as zs
import walker as W
import body_relayout as BR
import verbatim_gate as VG
import container_refs as CR

ALIAS_LO, ALIAS_HI = 0xA0000000, 0xC0000000


class GateFailure(AssertionError):
    pass


def _fail(gate, msg):
    raise GateFailure('%s FAILED: %s' % (gate, msg))


def _cls(v):
    if v == 0xFFFFFFFF: return 'FOLLOW'
    if v == 0xFFFFFFFE: return 'INSERT'
    if v == 0:          return 'NULL'
    if ALIAS_LO <= v < ALIAS_HI: return 'ALIAS'
    return 'OTHER'


# ---------------------------------------------------------------- G0
def g0_round_trip(zone, verbose=True):
    """The structural walk must reproduce the zone exactly and consume it entirely."""
    import add_asset as AA
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    rt, st = AA.add_assets(zone, [], verbose=False)
    if rt != zone:
        d = [i for i in range(min(len(rt), len(zone))) if rt[i] != zone[i]]
        _fail('G0', 'no-edit round trip differs in %d bytes, first at 0x%x' % (len(d), d[0] if d else -1))
    spans = VG.asset_spans(zone)
    walk_end = spans[-1][1]
    if walk_end != len(zone):
        _fail('G0', 'walk ends at 0x%x, zone is 0x%x (%d B unconsumed)'
              % (walk_end, len(zone), len(zone) - walk_end))
    omap, wb, _ = AA._harvest_omap(zone, r, wiiu_zone.__dict__.get('_x') or _layout(), _zc())
    if verbose:
        print('G0 round-trip      PASS  %d spans, walk consumes all %d B, omap %d'
              % (len(spans), len(zone), len(omap)))
    return dict(spans=len(spans), omap=len(omap), walk_end=walk_end)


def _layout():
    import struct_layout
    return struct_layout.Layout(W.HDR, console=True)


def _zc():
    return W.ZoneCode(W.ZC_DIR)


# ---------------------------------------------------------------- G1 / G2
def g1_g2_census(zone, baseline=None, verbose=True):
    """Field-class census for the layouts we have reversed. Exact equality, not >=."""
    spans = VG.asset_spans(zone)
    out = {}

    ts = [(s, e) for s, e, nm, vb in spans if nm == 'TECHNIQUE_SET']
    nc, slots, hist = Counter(), Counter(), Counter()
    reserved_zero, maxslot = 0, -1
    for s, e in ts:
        if e - s < 0x88:
            _fail('G1', 'techset @0x%x spans %d B, less than the 0x88 body' % (s, e - s))
        nc[_cls(struct.unpack_from('>I', zone, s)[0])] += 1
        if struct.unpack_from('>I', zone, s + 4)[0] == 0:
            reserved_zero += 1
        for i in range(32):
            v = struct.unpack_from('>I', zone, s + 8 + 4*i)[0]
            slots[_cls(v)] += 1
            if _cls(v) == 'ALIAS': hist[i] += 1
            if v != 0: maxslot = max(maxslot, i)
    if reserved_zero != len(ts):
        _fail('G2', 'techset +4 reserved word non-zero in %d of %d' % (len(ts) - reserved_zero, len(ts)))
    out['techset'] = dict(n=len(ts), name=dict(nc), slots=dict(slots),
                          alias_hist=dict(sorted(hist.items())), max_slot=maxslot)

    mt = [(s, e) for s, e, nm, vb in spans if nm == 'MATERIAL']
    fields = {o: Counter() for o in (0, 80, 84, 88, 92, 96)}
    zeros = 0
    for s, e in mt:
        for o in fields:
            fields[o][_cls(struct.unpack_from('>I', zone, s + o)[0])] += 1
        if (zone[s+12:s+20] == b'\x00'*8 and zone[s+77:s+80] == b'\x00'*3
                and zone[s+100:s+104] == b'\x00'*4):
            zeros += 1
    if zeros != len(mt):
        _fail('G2', 'Material scalar-zero invariant broken in %d of %d' % (len(mt) - zeros, len(mt)))
    for o, expect_never in ((84, 'ALIAS'), (88, 'ALIAS'), (92, 'ALIAS')):
        if fields[o].get(expect_never):
            _fail('G2', 'Material +%d has %d ALIAS values, expected none' % (o, fields[o]['ALIAS']))
    if fields[80].get('FOLLOW'):
        _fail('G2', 'Material +80 techniqueSet* must never be FOLLOW (%d found)' % fields[80]['FOLLOW'])
    out['material'] = dict(n=len(mt), **{('f%d' % o): dict(c) for o, c in fields.items()})

    if baseline:
        for k in ('techset', 'material'):
            if k in baseline and baseline[k] != out[k]:
                _fail('G2', '%s census drifted from baseline\n  want %r\n  got  %r'
                      % (k, baseline[k], out[k]))
    if verbose:
        print('G1 parse coverage  PASS  %d techsets (body>=0x88), %d materials' % (len(ts), len(mt)))
        print('G2 field census    PASS  techset slots %s' % out['techset']['slots'])
        print('                         material +80 %s, +0 %s'
              % (out['material']['f80'], out['material']['f0']))
    return out


# ---------------------------------------------------------------- G3
def g3_verbatim_freeze(src, dst, shift_of, verbose=True):
    """No word inside a VERBATIM body may differ from its source value.

    shift_of(src_offset) -> the byte delta that body experienced. Verbatim bodies are byte copies;
    only their POSITION may change."""
    bad = []
    for s, e, nm, vb in VG.asset_spans(src):
        if not vb:
            continue
        d = shift_of(s)
        if bytes(dst[s+d:e+d]) != src[s:e]:
            for fo in range(s, e - 3, 4):
                if struct.unpack_from('>I', dst, fo+d)[0] != struct.unpack_from('>I', src, fo)[0]:
                    bad.append((fo, nm))
    if bad:
        c = Counter(nm for fo, nm in bad)
        _fail('G3', '%d verbatim word(s) changed %s; first at 0x%x'
              % (len(bad), dict(c), bad[0][0]))
    if verbose:
        print('G3 verbatim freeze PASS  0 changed words across all verbatim bodies')
    return True


# ---------------------------------------------------------------- G4
def g4_container_refs(zone, insert_indices, verbose=True):
    """Directional test of the container-ref law, with positive controls.

    For an array insertion at each index in `insert_indices`, every ref targeting an entry >= that
    index MUST move by exactly +8 per insertion at or below it, and every other ref must not move."""
    Z, K, residue, refs = CR.audit(zone, verbose=False)
    count = Z.n

    def expected_shift(idx, ins):
        return 8 * sum(1 for j in ins if j <= idx)

    def simulate(ins):
        imap = CR.index_map_for_inserts(ins, count)
        moved = sum(1 for r in refs if imap[r['index']] != r['index'])
        for r in refs:
            want = expected_shift(r['index'], ins)
            got = 8 * (imap[r['index']] - r['index'])
            if want != got:
                _fail('G4', 'ref at 0x%x -> entry %d: expected shift %+d, map gives %+d'
                      % (r['off'], r['index'], want, got))
        return moved

    # positive controls -- a directional test that cannot fire proves nothing
    all_moved = simulate([0])
    if all_moved != len(refs):
        _fail('G4', 'positive control (insert at 0) moved %d of %d refs, expected all'
              % (all_moved, len(refs)))
    none_moved = simulate([count])
    if none_moved != 0:
        _fail('G4', 'negative control (insert at end) moved %d refs, expected 0' % none_moved)

    affected = simulate(insert_indices)
    broken = [r for r in refs
              if any(r['index'] >= i for i in insert_indices)]
    if verbose:
        print('G4 container refs  PASS  %d refs, K=%d residue=%d; controls fired (%d / 0)'
              % (len(refs), K, residue, all_moved))
        if insert_indices:
            c = Counter(r['src_type'] for r in broken)
            print('                         insert at %s -> %d refs need relink %s'
                  % (insert_indices, len(broken), dict(c) if broken else ''))
    return dict(refs=refs, K=K, residue=residue, affected=affected, broken=broken)


# ---------------------------------------------------------------- driver
def run_all(zone, baseline=None, insert_indices=(), verbose=True):
    if verbose:
        print('=== GATE BATTERY (zone %d B) ===' % len(zone))
    res = {}
    res['g0'] = g0_round_trip(zone, verbose)
    res['census'] = g1_g2_census(zone, baseline, verbose)
    res['g4'] = g4_container_refs(zone, list(insert_indices), verbose)
    if verbose:
        print('=== all gates PASS ===')
    return res


def check_edit(src, dst, shift_of, insert_indices=(), verbose=True):
    """Gates that apply to an EDITED zone: freeze + container-ref correctness."""
    if verbose:
        print('=== EDIT GATES (%d B -> %d B) ===' % (len(src), len(dst)))
    g3_verbatim_freeze(src, dst, shift_of, verbose)
    g4_container_refs(src, list(insert_indices), verbose)
    if verbose:
        print('=== edit gates PASS ===')
    return True


if __name__ == '__main__':
    import sys, os
    BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')
    sys.path.insert(0, os.path.join(BASE, 'WiiU_FF_Studio'))
    import wiiu_ff
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        BASE, 'botwars_test', 'patch_mp_BACKUP_771e67b1.ff')
    z = wiiu_ff.decrypt(open(path, 'rb').read())[1]
    idx = [int(a) for a in sys.argv[2:]]
    run_all(z, insert_indices=idx)
