"""head_window — the ONLY relink an asset-count change actually needs.

ROOT CAUSE (derived from the RPL + 18 genuine zones, no dump)
-------------------------------------------------------------
The XAsset array is allocated out of the same monotonically-advancing block-5 cursor as every asset
body, so `assetCount += N` moves `arrayEnd_rt` by `8*N` and shifts the block-5 data that follows it.

But the shift does NOT propagate through the zone. `DB_AllocStreamPos` @0x0223FC34 is:

    pos = (pos + MASK) & ~MASK          (r3 is a MASK, not a size)

For two layouts differing only in stream history, immediately after an `ALLOC(mask)` BOTH cursors are
congruent to 0 (mod mask+1), so their difference is too. **A delta of 8 cannot survive a single
ALLOC(15).** patch_mp has 474 RawFiles (ALLOC 15), 274 ScriptParseTrees (ALLOC 31), techset microcode
(ALLOC 255) and image pixels (ALLOC 8191) — the delta collapses to 0 at the first alignment wall.

⇒ Only a short HEAD WINDOW immediately after the array is displaced. Everything downstream is already
at its correct runtime offset and MUST NOT be touched. Empirically confirmed: v13 grew +92 mid-zone,
bumped ZERO downstream aliases, and boots with ~49k StringTableCell.string aliases all resolving.

⛔ The prescription "increment every alias word whose target >= arrayEnd by 8" (~216,554 words on
patch_mp) is arithmetically impossible and would corrupt a currently-correct zone.

THE FATAL WORD
--------------
patch_mp asset #0 is a PhysPreset whose 84-byte root goes to TEMP, so its name string "default\\0" is
the FIRST block-5 body datum, sitting exactly at `arrayEnd_rt`. Asset #5 is the MaterialTechniqueSet
literally named "default" — and it is the ONLY 1 of 66 techsets whose `name` field is an ALIAS rather
than FOLLOW. `g_defaultAssetName == "default"` and `DB_FindXAssetDefaultHeaderInternal` is a pure name
lookup, so the entire techset-default safety net hangs on that single alias resolving to arrayEnd_rt.

Add one asset without fixing it and rt(arrayEnd) becomes 16812 while the alias still says 16804 —
which now lands on XAsset entry #1533 = `00000009 ffffffff`, i.e. the empty string. The techset
registers under "" , the default lookup returns NULL, and the first comma-ref that misses raises
    Could not load default asset 'default' for asset type 'techset'.
That is the exact failure of all four additive builds.

Measured: 22 alias words on patch_mp target the damage window for N=1.
"""
import os, sys, struct

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _rel in ('wiiu_ref', 'native_linker', 'WiiU_FF_Studio'):
    sys.path.insert(0, os.path.join(_HERE, '..', '..', '..', _rel))

import wiiu_zone

ALIAS_LO, ALIAS_HI = 0xA0000000, 0xC0000000
B5_BASE = 64


def array_rt(zone, r=None):
    """(array_start_rt, array_end_rt). The array is 4-ALIGNED in runtime space.
    ⚠ loader_sim.simulate() uses ((arr+7)&~7) -- align 8 -- which is wrong and makes every
    simulated rt on patch_mp +4 off."""
    if r is None:
        r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    arr = r.assets_off - B5_BASE
    start = (arr + 3) & ~3
    return start, start + 8 * r.asset_count


def find_head_aliases(zone, window_end=None, r=None, verbose=True):
    """Every alias word whose target lands in the displaced head window.

    Byte-granular: console records are unaligned, so a 4-aligned scan misses most pointer fields.
    window_end defaults to the first ALLOC(255) wall past the array (conservative: 0x400 past it,
    which covered all 22 measured words with room to spare)."""
    if r is None:
        r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    lo, hi = array_rt(zone, r)
    if window_end is None:
        window_end = hi + 0x400
    hits = []
    for fo in range(0, len(zone) - 3):
        v = struct.unpack_from('>I', zone, fo)[0]
        if not (ALIAS_LO <= v < ALIAS_HI):
            continue
        w = (v - 1) & 0xFFFFFFFF
        if (w >> 29) != 5:
            continue
        raw = w & 0x1FFFFFFF
        if hi <= raw < window_end or raw == hi:
            hits.append((fo, v, raw))
    if verbose:
        print('   head window: arrayEnd_rt=%d (0x%x), scan to %d -> %d alias words'
              % (hi, hi, window_end, len(hits)))
    return hits, lo, hi


def relink_head(out, n_added, hits=None, zone=None, verbose=True):
    """Bump every head-window alias by 8*n_added. `out` is a bytearray of the NEW zone; `hits` must
    have been harvested from the OLD zone at OLD file offsets, so pass the OLD zone's offsets only
    when the head has not moved (array insertions are at/after the array, so head bodies shift by
    8*n_added -- supply hits already offset-corrected by the caller)."""
    d = 8 * n_added
    if d == 0:
        return 0
    n = 0
    for fo, v, raw in hits:
        struct.pack_into('>I', out, fo, (v & ~0x1FFFFFFF) | ((raw + d) & 0x1FFFFFFF))
        n += 1
    if verbose:
        print('   head-window relink: %d alias words bumped by +%d' % (n, d))
    return n


def audit(zone, verbose=True):
    """Report the head window and what points into it. Diagnostic entry point."""
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    hits, lo, hi = find_head_aliases(zone, r=r, verbose=False)
    if verbose:
        print('assets=%d  array_rt=[%d,%d)  head aliases=%d' % (r.asset_count, lo, hi, len(hits)))
        for fo, v, raw in hits[:30]:
            print('   file 0x%08x  %08X -> rt %d%s' % (fo, v, raw, '   <== arrayEnd' if raw == hi else ''))
    return hits, lo, hi
