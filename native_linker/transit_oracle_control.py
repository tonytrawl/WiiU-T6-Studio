#!/usr/bin/env python3
"""
TRANSIT-ORACLE CONTROL — the second (ZOMBIES) byte-exact parity oracle,
mirroring raid_oracle_control.py for zm_transit (genuine WiiU zone vs PC zone).

Modes:
  python transit_oracle_control.py            # gate: assemble PC -> diff vs genuine
  python transit_oracle_control.py survey     # walk-pair survey: CO vs PC spans,
                                              # per-class size/byte relationship
                                              # (no assemble; mines rules per
                                              # HANDOFF_zm_transit_rosetta §3d)

Both sides' walks are PROVEN end-to-end (2026-07-14):
  console: loader_sim.simulate walks all 3,266 assets to EOF byte-exact —
           needed the ZM weapon/vehicle next-asset resync (body_relayout
           WEAPON_NEXT_HINT machinery), the fx_probe FxTrailDef offset fix,
           and strict start-validators; see FINDINGS_transit_oracle.md.
  pc:      pc_walk.walk_pc_zone / produce_nobackbone.walk_pc_bodies (with the
           wk.asset_span hooks) walk all 3,254 assets to EOF.

Console-only rows (12): 3 GLASSES @1..3, 1 relocated XMODEL @1349,
8 LOCALIZE_ENTRY @2779..2786 (the english localize block).
"""
import sys, os, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from collections import defaultdict, Counter
import difflib

import wiiu_zone, pc_zone
import walker as W

CO_PATH = '../wiiu_ref/zm_transit_original.zone'
PC_PATH = '../PC ff/zm_transit.zone'


def type_pairs(CO, PC):
    """(ci, pi) type alignment of the two asset lists via difflib (handles
    console-only inserts and the relocated XMODEL row)."""
    r = pc_zone.PCZoneReader(PC); r.read_string_table(); r.read_asset_list()
    rc = wiiu_zone.ZoneReader(CO); rc.read_string_table(); rc.read_asset_list()
    co_rows = [rc.assets[i][2] for i in range(len(rc.assets))]
    pc_rows = [r.assets[i][1] for i in range(len(r.assets))]
    sm = difflib.SequenceMatcher(None, co_rows, pc_rows, autojunk=False)
    pairs, inserts = [], []
    for (tag, a1, a2, b1, b2) in sm.get_opcodes():
        if tag == 'equal':
            pairs.extend((a1 + k, b1 + k) for k in range(a2 - a1))
        elif tag in ('delete', 'replace'):
            inserts.extend((a1 + k, co_rows[a1 + k]) for k in range(a2 - a1))
    return pairs, inserts


def walk_both():
    """Both spans lists + the pair map + weapon-size priors already applied."""
    import loader_sim as LS
    import pc_walk
    CO = open(CO_PATH, 'rb').read()
    PC = open(PC_PATH, 'rb').read()
    pcspans = []
    pc_walk.walk_pc_zone(PC_PATH, spans=pcspans)
    pairs, inserts = type_pairs(CO, PC)
    pcs = {i: (nm, s, e) for (i, nm, s, e) in pcspans}
    wps = {ci: pcs[pi][2] - pcs[pi][1] for (ci, pi) in pairs
           if pcs.get(pi) and pcs[pi][0] == 'WEAPON' and pcs[pi][2] is not None}
    em, spans, CO = LS.simulate(CO, policy=dict(weapon_pc_size=wps))
    if spans[-1][4] != len(CO) or len(spans) != 3266:
        raise SystemExit('console walk regressed: %d spans, end %d/%d' %
                         (len(spans), spans[-1][4], len(CO)))
    return CO, PC, spans, pcspans, pairs, inserts


def _bswap32(b):
    n = len(b) & ~3
    out = bytearray(b)
    out[:n] = struct.pack('>%dI' % (n // 4), *struct.unpack('<%dI' % (n // 4), b[:n]))
    return bytes(out)


def survey():
    """Per-class CO<->PC relationship: byte-exact, word-mirror (pure endian
    swap), same-size, or structural. This is the §3d rule-mining pass."""
    CO, PC, spans, pcspans, pairs, inserts = walk_both()
    cos = {i: (nm, root, s, e) for (i, nm, root, s, e) in spans}
    pcs = {i: (nm, s, e) for (i, nm, s, e) in pcspans}
    per = defaultdict(Counter)
    ex = {}
    for (ci, pi) in pairs:
        c = cos.get(ci); p = pcs.get(pi)
        if not c or not p or c[3] is None or p[2] is None:
            continue
        root = c[1] or c[0]
        csz = c[3] - c[2]; psz = p[2] - p[1]
        if csz == 0 and psz == 0:
            per[root]['aliased-both'] += 1
            continue
        if csz == 0 or psz == 0:
            per[root]['aliased-one-side'] += 1
            continue
        cb = CO[c[2]:c[3]]; pb = PC[p[1]:p[2]]
        if cb == pb:
            per[root]['byte-exact'] += 1
        elif csz == psz and _bswap32(pb) == cb:
            per[root]['word-mirror'] += 1
        elif csz == psz:
            n = sum(1 for a, b in zip(cb, _bswap32(pb)) if a != b)
            per[root]['same-size'] += 1
            per[root]['same-size-diffbytes'] += n
        else:
            per[root]['structural'] += 1
            key = (root, 'delta')
            per[root]['delta-sum'] += csz - psz
        if root not in ex:
            ex[root] = (c[0], csz, psz)
    print('=== TRANSIT SURVEY (console vs PC, %d pairs) ===' % len(pairs))
    for root in sorted(per):
        print('  %-22s %s' % (root, dict(per[root])))
    print('console-only rows: %s' % (inserts,))
    return 0


def main():
    """Full assemble-based gate, the raid_oracle_control contract."""
    import raid_oracle_control as RC
    import loader_sim as LS
    import produce_nobackbone as PN
    # transit runtime policies are NOT yet baked (gfx_skip etc.) — start from
    # the walk-only policy; pointer-resolution classes will be noisy until the
    # transit constants are measured (raid recipe: SndBank anchors + sweeps).
    CO, PC, spans, pcspans, pairs, inserts = walk_both()
    print('walks green: co %d spans to EOF, pc %d spans' %
          (len(spans), len(pcspans)))
    print('NOTE: assemble-gate policies unbaked — run `survey` for the '
          'walk-level parity report; the full RC.main-style gate lands once '
          'the transit runtime constants are measured.')
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'survey':
        raise SystemExit(survey())
    raise SystemExit(main())
