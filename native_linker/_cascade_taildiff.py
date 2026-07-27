"""Where exactly does the deployed pipeline build (mountfix, sound-fixed) differ from
the playable answer key? Block-level diff distribution + span classification, so we
see if the clipMap/mapEnts/tail assets diverge (BSP cause) or if all diffs are in the
material/placeholder region (BSP is a load cascade)."""
import struct, sys, pickle
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
P = open('mp_skate_mountfix.zone', 'rb').read()
K = open('mp_skate_gfxtail46.zone', 'rb').read()
n = min(len(P), len(K))
BLK = 0x40000
buckets = {}
for i in range(n):
    if P[i] != K[i]:
        buckets[i // BLK] = buckets.get(i // BLK, 0) + 1
print('total diff bytes: %d in %d blocks of 0x%X' % (sum(buckets.values()), len(buckets), BLK))
# map file offset -> asset span via simmap
sim = pickle.load(open('_skate2_simmap.pkl', 'rb'))
ae = sim['assets_end']
spans = sim['spans']   # (i, TYPE, root, start, end) in stream offsets (file = +ae)
def span_at(fo):
    so = fo - ae
    for (i, tn, root, s, e) in spans:
        if s <= so < e:
            return '%s(%d)' % (tn, i)
    return '?'
print('\nblocks with diffs (file range | count | asset there):')
for blk in sorted(buckets):
    lo = blk * BLK
    print('  0x%08X-0x%08X  %6d  %s' % (lo, lo+BLK, buckets[blk], span_at(lo)))

# specifically: is the tail (clipMap/GameWorldMp/MapEnts/pathdata) clean?
print('\n--- tail spans (last 20) and their diff status ---')
for (i, tn, root, s, e) in spans[-20:]:
    fs, fe = s + ae, e + ae
    d = sum(1 for x in range(fs, min(fe, n)) if P[x] != K[x])
    print('  span[%d] %-22s file 0x%X..0x%X  diffs=%d%s' % (i, tn, fs, fe, d, '  <<<' if d else ''))
