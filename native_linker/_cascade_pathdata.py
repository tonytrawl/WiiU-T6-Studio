"""Boot-3 crash = Path_NodesInCylinder_r (pathnode tree) during SP_actor in G_InitGame.
r7=r22=0xFFFFFFFE (INSERT marker leaked into runtime data), r3/r30=0x003880c4 (bad ptr).
Suspect: PathData/pathnode_t (memory: struct_layout under-reports pathnode_t 80 vs real 144).
Diff EVERY tail span of the deployed build vs the playable answer key to see what still
differs after the clipMap fix."""
import struct, sys, pickle
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
P = open('mp_skate_clipfix.zone', 'rb').read()
K = open('mp_skate_gfxtail46.zone', 'rb').read()
sim = pickle.load(open('_skate2_simmap.pkl', 'rb'))
ae = sim['assets_end']
DRIFT = 11089   # post-GfxWorld growth; simmap is pre-growth
n = min(len(P), len(K))
print('zone lens: pipe %d key %d' % (len(P), len(K)))
rows = []
for sp in sim['spans']:
    i, tn, root, s, e = sp
    fs, fe = s + ae + DRIFT, min(e + ae + DRIFT, n)
    if fs >= n:
        continue
    d = sum(1 for x in range(fs, fe) if P[x] != K[x])
    if d:
        rows.append((d, i, tn, fs, fe))
rows.sort(reverse=True)
print('\nspans still differing vs answer key (top 25):')
for d, i, tn, fs, fe in rows[:25]:
    print('  %-22s span[%3d] file 0x%08X..0x%08X  diffs=%d' % (tn, i, fs, fe, d))
print('\ntotal differing spans: %d' % len(rows))

# focus: any PATHDATA / pathnode span?
print('\n--- PATHDATA / path-related spans ---')
for sp in sim['spans']:
    i, tn, root, s, e = sp
    if 'PATH' in str(tn).upper() or 'PATH' in str(root).upper():
        fs, fe = s + ae + DRIFT, min(e + ae + DRIFT, n)
        d = sum(1 for x in range(fs, fe) if P[x] != K[x]) if fs < n else -1
        print('  %-22s span[%3d] root=%s file 0x%08X..0x%08X len=%d diffs=%d'
              % (tn, i, root, fs, fe, fe - fs, d))
