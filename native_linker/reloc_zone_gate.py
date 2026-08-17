#!/usr/bin/env python3
"""reloc_zone_gate -- zone-level acceptance for the block-5 relocation fix (defect D8 / rule (U)).

CANONICAL: native_linker/reloc_zone_gate.py

`reloc_model --selftest` covers the model as arithmetic. This covers it against a REAL zone, which
is where the refuted rule actually did its damage. Read-only: it never writes a zone and never
touches a LIVE file.

  G1  ZERO-CHANGE ROUND-TRIP IS BYTE-EXACT. Substitute each table's body with the bytes already
      there and require the output to equal the input. This exercises the whole emit path with an
      identity model, so it is the regression proof that only the relocation decision changed.
      ⚠ The substituted bytes must be the ORIGINAL bytes, not a re-serialisation: a re-serialiser
      can be length-neutral without being byte-faithful (measured 1,122 differing bytes on
      patch_zm), and including that would test the serialiser instead of the relinker.
  G2  A REAL GROW WITH NO INTERVALS REFUSES. Targets inside a regrown body move by the CELL-ARRAY
      growth, not the body delta, so shifting them by the delta is wrong by hundreds of bytes.
  G3  THE SOUND AND REFUTED MODELS DISAGREE, COUNTED. Blast radius for this zone, and proof the
      fix is not cosmetic.

Usage:  python reloc_zone_gate.py <stock.ff|stock.zone> [asset.csv asset.csv ...]

Defaults to the two tables of the ZM globe edit when none are named. Exit 0 all clear, 1 otherwise.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
for _p in ('native_linker', 'wiiu_ref', 'WiiU_FF_Studio', 'scratch_custommap',
           os.path.join('dlc loading', 'native'),
           os.path.join('dlc loading', 'native', 'fullrelink')):
    _q = os.path.join(ROOT, _p)
    if os.path.isdir(_q) and _q not in sys.path:
        sys.path.insert(0, _q)

import reloc_model as RM

DEFAULT_TABLES = ('zm/mapstable.csv', 'zm/gametypestable.csv')


def _load(path):
    if path.lower().endswith('.zone'):
        return open(path, 'rb').read()
    import wiiu_ff
    return bytes(wiiu_ff.decrypt(open(path, 'rb').read())[1])


def run(path, tables):
    import grow_relink as GR
    import build_zm_custom as B                  # parse_table / serialize for StringTables

    rows, failed = [], 0

    def check(tag, ok, detail):
        rows.append(('PASS' if ok else 'FAIL', tag, detail))
        return 0 if ok else 1

    zone = _load(path)
    print('zone %s  %d B' % (os.path.basename(path), len(zone)))

    parsed = []
    for a in tables:
        try:
            hdr, cols, nrows, cells, end = B.parse_table(zone, a)
        except Exception as ex:
            print('  cannot parse %s: %s -- skipping' % (a, ex))
            continue
        parsed.append((a, hdr, cols, nrows, cells, end))
    if not parsed:
        print('  no named table found in this zone; nothing to gate')
        return 1

    # ---- G1 -------------------------------------------------------------------------------
    subs0 = {hdr: (end, zone[hdr:end]) for _a, hdr, _c, _r, _ce, end in parsed}
    z0, td0, nhp0, bump0 = GR.relink_grow(bytes(zone), subs0, tail_relink=True, verbose=False)
    same = z0 == zone
    ndiff = 0 if same else sum(1 for x, y in zip(z0, zone) if x != y)
    failed += check('G1', same and td0 == 0,
                    'identity substitution byte-identical=%s (delta %+d, %d differing byte(s), '
                    '%d headerPtrs, %d tail bumps)' % (same, td0, ndiff, nhp0, bump0))

    # ---- G2 -------------------------------------------------------------------------------
    subs1 = {}
    for a, hdr, cols, nrows, cells, end in parsed:
        grown = list(cells) + list(cells[:cols])            # one duplicated row
        subs1[hdr] = (end, B.serialize(a, cols, nrows + 1, grown))
    try:
        GR.relink_grow(bytes(zone), subs1, tail_relink=True, verbose=False)
        failed += check('G2', False, 'a real grow with no intervals did NOT refuse')
    except RM.InteriorTarget as ex:
        failed += check('G2', True, 'refused: %s' % str(ex).split('.')[0][:130])
    except RM.RelocError as ex:
        failed += check('G2', True, 'refused (%s): %s' % (type(ex).__name__, str(ex)[:110]))

    # ---- G3 -------------------------------------------------------------------------------
    anchors, stats = RM.anchors_from_stringtables(zone, want_stats=True)
    import wiiu_zone
    rd = wiiu_zone.ZoneReader(zone); rd.read_string_table(); rd.read_asset_list()
    anchors = [RM.container_edge_anchor(rd.assets_end)] + list(anchors)
    targets = RM.stringtable_alias_offsets(zone)
    file_subs = sorted((o, e, len(b) - (e - o)) for o, (e, b) in subs1.items())
    try:
        spans = RM.b5_spans_from_file_subs(file_subs, anchors, target_offsets=targets)
    except RM.BoundaryUnprovable as ex:
        failed += check('G3', False, 'boundary unprovable: %s' % str(ex)[:150])
        spans = None
    if spans is not None:
        sound = RM.Reloc(spans, (), 'sound')
        legacy = RM.LegacyFileBase64Reloc(file_subs, 'legacy')
        agree = differ = interior = 0
        for f, b5 in anchors:
            try:
                n = sound.new_off(b5)
            except RM.InteriorTarget:
                interior += 1
                continue
            if n == legacy.new_off(b5):
                agree += 1
            else:
                differ += 1
        failed += check('G3', (differ + interior) > 0,
                        '%d anchors: agree %d, DISAGREE %d, interior %d (refused by the sound '
                        'model, silently relocated by the refuted one) => %d of %d decided wrongly '
                        'by the old rule' % (len(anchors), agree, differ, interior,
                                             differ + interior, len(anchors)))

    # ---- G4: every declared site CLASSIFIES; nothing is silently unaccounted for -----------
    # PM's named bar for the tail-whitelist landing. A declared pointer site must end up in exactly
    # one bucket: interior-refused, or placeable by the b5-space registered set, or provably outside
    # every regrown body (so it needs no decision). "Unclassified" must be zero -- an unclassified
    # site is one the relinker would skip for a reason nobody stated.
    if spans is not None:
        sound = RM.Reloc(spans, (), 'sound')
        import wiiu_zone as _wz
        rd2 = _wz.ZoneReader(zone); rd2.read_string_table(); rd2.read_asset_list()
        pop = set(RM.stringtable_alias_offsets(zone))
        import struct as _st
        for i in range(rd2.asset_count):
            h = _st.unpack_from('>I', zone, rd2.assets_off + i * 8 + 4)[0]
            if 0xA0000001 <= h <= 0xBFFFFFFF and (((h - 1) >> 29) & 7) == 5:
                pop.add((h - 1) & 0x1FFFFFFF)
        interior = set(RM.preflight_interior(sound, pop))
        reg = RM.b5_registered_set([o for o in pop], anchors)
        unclassified = [o for o in pop
                        if o not in interior and o not in reg and sound.new_off(o) == o
                        and any(s <= o < e for s, e, _d in spans)]
        failed += check('G4', not unclassified and len(interior) > 0,
                        '%d declared site(s): %d interior-REFUSED, %d placeable in b5, '
                        '%d unclassified (must be 0)'
                        % (len(pop), len(interior), len(reg & pop), len(unclassified)))

        # ---- G5: SEEN TO FAIL BOTH WAYS -------------------------------------------------------
        # (a) supply an interval covering one interior site -> that site must STOP being refused.
        #     Proves the classifier reads its input instead of always returning the same answer.
        one = sorted(interior)[0]
        with_iv = RM.Reloc(spans, [(one, one + 1, 999_000_000)], 'sound+1interval')
        after = set(RM.preflight_interior(with_iv, pop))
        moved = one not in after and len(after) == len(interior) - 1
        # (b) the refuted model relocates those same sites SILENTLY instead of refusing.
        legacy2 = RM.LegacyFileBase64Reloc(file_subs, 'legacy')
        silent = sum(1 for o in interior if legacy2.new_off(o) != o)
        failed += check('G5', moved and silent > 0,
                        'mapping one interior site drops it from the refusal set (%s, %d -> %d); '
                        'the refuted model silently relocates %d of the %d interior site(s) '
                        'instead of refusing them' % (moved, len(interior), len(after),
                                                      silent, len(interior)))

    print()
    for st, tag, detail in rows:
        print('  %-4s %-3s %s' % (st, tag, detail))
    print('\n  %d passed, %d failed' % (len(rows) - failed, failed))
    print('  anchors %d from %d table(s); %d ambiguous, %d rejected by the literal re-count'
          % (stats['anchors'], stats['tables'], stats['ambiguous in index'],
             stats['rejected by literal re-count']))
    return 1 if failed else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tables = tuple(sys.argv[2:]) or DEFAULT_TABLES
    return run(sys.argv[1], tables)


if __name__ == '__main__':
    sys.exit(main())
