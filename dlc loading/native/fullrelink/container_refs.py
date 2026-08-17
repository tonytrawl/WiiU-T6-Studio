"""container_refs — classify and relink CONTAINER REFERENCES: pointers that target an entry in
the XAssetList array rather than an asset body.

THE LAW (measured on patch_mp, reproduced independently)
--------------------------------------------------------
`Material +80 techniqueSet*` does not point at a techset BODY. It points at the **headerPtr field of
that techset's XAssetList array entry**:

    target_file_offset = AO + 8*i + 4          (AO = assets_off, i = array index)
    raw = value & 0x1FFFFFFF
    raw + K == target_file_offset              for an unknown K in [56, 63]

Sweeping K over the 446 Material+80 aliases: K in [56,63] puts 446/446 on an entry with
type == TECHNIQUE_SET; K in [64,71] puts 446/446 one entry LATER (type MATERIAL). So **K is only
determined modulo 8** -- eight consecutive K values name the same entry.

⇒ NEVER reconstruct a container ref by computing an absolute offset from an assumed K: that would
bake in the wrong low 3 bits. Relink by the K-FREE difference instead:

    new_raw = raw + (AO_new - AO_old) + 8*(i_new - i_old)

Both correction terms are multiples of 8 (array entries are 8 B), so the low 3 bits -- whatever they
encode -- are carried through untouched. This is exact, not heuristic.

WHY THIS MATTERS
----------------
Inserting an entry at array index i shifts every later entry by 8 bytes, silently re-aiming every
container ref that targets an entry >= i at the WRONG ASSET. Measured against three failed additive
builds of patch_mp:

    insert at index 1087  ->  239 refs broken  (DESTRUCTIBLEDEF 180, MATERIAL 20, FX 20,
                                                XMODEL 10, WEAPON 8, TRACER 1)   => boot FAILED
    insert at index 1510  ->    0 refs broken                                     (failed for another reason)
    insert at index 1533  ->    0 refs broken                                     (failed for another reason)

789 container refs exist zone-wide, from 10 source types, targeting entry indices 2..1496.

DETECTION
---------
Two tiers, and they are NOT interchangeable:

  EXACT  -- a struct field whose role is known (currently `Material +80`, plus `+96 thermalMaterial*`
            when it is an ALIAS). No guessing; these are ground truth and are also used to calibrate K.
  SCAN   -- byte-granular search for alias words that land on the calibrated residue AND on an entry
            whose type id is a real asset type. Console records are byte-granular, so a 4-aligned scan
            misses most of them. This tier has a measured false-positive floor (~10 per 450 for
            MATERIAL); off-residue words scatter uniformly, making residue+type an ~8x discriminator.

⚠ A K-sweep is USELESS as a discriminator. Every alias word landing anywhere in the array range
matches the residue for exactly one K in any run of 8, so sweeping reports pure noise. Calibrate K
once from the EXACT tier, then hold it fixed.
"""
import os, sys, struct, bisect
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _rel in ('wiiu_ref', 'native_linker', 'WiiU_FF_Studio'):
    sys.path.insert(0, os.path.join(_HERE, '..', '..', '..', _rel))

import wiiu_zone
import verbatim_gate as VG

ALIAS_LO, ALIAS_HI = 0xA0000000, 0xC0000000
K_RANGE = range(48, 80)

# console type ids seen in T6 Wii U zones; a container ref must land on one of these
KNOWN_TYPES = None          # filled per-zone from the array itself


class Zone:
    """Cached per-zone facts the classifier needs."""
    def __init__(self, data):
        self.z = data
        r = wiiu_zone.ZoneReader(data); r.read_string_table(); r.read_asset_list()
        self.r = r
        self.ao = r.assets_off
        self.n = r.asset_count
        self.types = [struct.unpack_from('>I', data, self.ao + i*8)[0] for i in range(self.n)]
        self.typename = {i: r.assets[i][2] for i in range(self.n)}
        self.spans = VG.asset_spans(data)

    def in_array(self, fo):
        return self.ao <= fo < self.ao + self.n * 8

    def index_of(self, fo):
        return (fo - self.ao) // 8


def exact_refs(Z):
    """Ground-truth container refs from struct fields whose role is known."""
    out = []
    for s, e, nm, vb in Z.spans:
        if nm != 'MATERIAL':
            continue
        for off, role in ((80, 'Material.techniqueSet'), (96, 'Material.thermalMaterial')):
            v = struct.unpack_from('>I', Z.z, s + off)[0]
            if ALIAS_LO <= v < ALIAS_HI:
                out.append((s + off, v, role))
    return out


def calibrate(Z, verbose=True):
    """Solve K and the entry-field residue from the EXACT tier.

    Returns (K, residue). Picks the middle of the widest contiguous K window in which every exact
    ref lands on an entry whose type matches the role's expected target type."""
    ex = [(fo, v) for fo, v, role in exact_refs(Z) if role == 'Material.techniqueSet']
    assert ex, 'no exact Material.techniqueSet refs to calibrate from'
    ts_type = next(t for i, t in enumerate(Z.types) if Z.typename[i] == 'TECHNIQUE_SET')
    good = []
    for K in K_RANGE:
        ok = True
        for fo, v in ex:
            t = (v & 0x1FFFFFFF) + K
            if not Z.in_array(t) or Z.types[Z.index_of(t)] != ts_type:
                ok = False; break
        if ok:
            good.append(K)
    assert good, 'no K in %r puts every exact ref on a TECHNIQUE_SET entry' % K_RANGE
    # widest contiguous run
    runs, cur = [], [good[0]]
    for k in good[1:]:
        if k == cur[-1] + 1: cur.append(k)
        else: runs.append(cur); cur = [k]
    runs.append(cur)
    run = max(runs, key=len)
    K = run[len(run)//2]
    residue = ((ex[0][1] & 0x1FFFFFFF) + K - Z.ao) % 8
    for fo, v in ex:
        assert ((v & 0x1FFFFFFF) + K - Z.ao) % 8 == residue, 'exact refs disagree on residue'
    if verbose:
        print('   calibrate: K window %d..%d (width %d) -> K=%d, residue=%d, %d exact refs'
              % (run[0], run[-1], len(run), K, residue, len(ex)))
    assert len(run) == 8, 'expected an 8-wide K window (K known mod 8), got %d' % len(run)
    return K, residue


def find_refs(Z, K, residue, verbose=True):
    """All container refs: EXACT tier plus a byte-granular SCAN tier.

    Returns list of dicts {off, value, raw, index, src_type, dst_type, tier, role}."""
    valid_types = set(Z.types)
    exact_off = {fo: role for fo, v, role in exact_refs(Z)}
    refs, seen = [], set()

    def add(fo, v, tier, role=None):
        raw = v & 0x1FFFFFFF
        t = raw + K
        if not Z.in_array(t) or (t - Z.ao) % 8 != residue:
            return False
        i = Z.index_of(t)
        if Z.types[i] not in valid_types:
            return False
        if fo in seen:
            return False
        seen.add(fo)
        refs.append(dict(off=fo, value=v, raw=raw, index=i, dst_type=Z.typename[i],
                         tier=tier, role=role))
        return True

    for fo, v, role in exact_refs(Z):
        add(fo, v, 'EXACT', role)
    for s, e, nm, vb in Z.spans:
        for fo in range(s, e - 3):
            if fo in exact_off:
                continue
            v = struct.unpack_from('>I', Z.z, fo)[0]
            if ALIAS_LO <= v < ALIAS_HI:
                add(fo, v, 'SCAN')
    # annotate source asset type
    starts = sorted((s, nm) for s, e, nm, vb in Z.spans)
    import bisect
    ss = [s for s, nm in starts]
    for rf in refs:
        j = bisect.bisect_right(ss, rf['off']) - 1
        rf['src_type'] = starts[j][1] if j >= 0 else '?'
    refs.sort(key=lambda d: d['off'])
    if verbose:
        by = Counter(r['src_type'] for r in refs)
        tiers = Counter(r['tier'] for r in refs)
        idx = [r['index'] for r in refs]
        print('   container refs: %d  (%s)' % (len(refs), dict(tiers)))
        print('     by source type: %s' % dict(by))
        print('     target entry index range: %d..%d' % (min(idx), max(idx)))
    return refs


def broken_by_insert(refs, insert_index):
    """Refs that an array insertion at `insert_index` would silently re-aim."""
    return [r for r in refs if r['index'] >= insert_index]


def relink(out, refs, index_map, ao_delta=0, verbose=True):
    """Rewrite every container ref in-place in `out` (a bytearray).

    index_map: old array index -> new array index.
    ao_delta : bytes the array START itself moved (0 unless the string table changed).

    Uses the K-FREE difference law, so the unknown low 3 bits are preserved exactly:
        new_raw = raw + ao_delta + 8*(new_index - old_index)
    """
    n = 0
    for r in refs:
        new_i = index_map.get(r['index'], r['index'])
        shift = ao_delta + 8 * (new_i - r['index'])
        if shift == 0:
            continue
        nv = (r['value'] & ~0x1FFFFFFF) | ((r['raw'] + shift) & 0x1FFFFFFF)
        struct.pack_into('>I', out, r['off'] + r.get('off_delta', 0), nv)
        n += 1
    if verbose:
        print('   container refs relinked: %d of %d' % (n, len(refs)))
    return n


def index_map_for_inserts(insert_indices, count):
    """old index -> new index, for new entries occupying `insert_indices` (each a slot the new
    entry will OCCUPY, so existing entries at or after it shift right)."""
    ins = sorted(insert_indices)
    return {k: k + sum(1 for j in ins if j <= k) for k in range(count)}


def audit(zone, verbose=True):
    """One-shot: load, calibrate, classify. Returns (Zone, K, residue, refs)."""
    Z = Zone(zone)
    K, residue = calibrate(Z, verbose)
    refs = find_refs(Z, K, residue, verbose)
    return Z, K, residue, refs
