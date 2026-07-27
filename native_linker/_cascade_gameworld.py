"""GAMEWORLD_MP span[801] differs by 748 bytes vs the answer key and holds the pathnode
tree walked by Path_NodesInCylinder_r (boot-3 crash, SP_actor in G_InitGame).
Crash regs: r7=r22=0xFFFFFFFE (INSERT leaked), r3/r30=0x003880c4 (bad ptr).
Classify the diffs: block-5 aliases (stale-rtmap class, same as clipMap) vs INSERT/FOLLOW
markers vs data."""
import struct, sys, collections
sys.path.insert(0, '.')
P = open('mp_skate_clipfix.zone', 'rb').read()
K = open('mp_skate_gfxtail46.zone', 'rb').read()
FS, FE = 0x050434C1, 0x0506EF6F     # GAMEWORLD_MP span (drift-corrected)
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
isal = lambda v: 0xA0000000 <= v < 0xC0000000

diffb = [x for x in range(FS, FE) if P[x] != K[x]]
print('GameWorldMp span 0x%X..0x%X  diff bytes=%d' % (FS, FE, len(diffb)))

# group into contiguous runs
runs = []
for x in diffb:
    if runs and x == runs[-1][1] + 1:
        runs[-1][1] = x
    else:
        runs.append([x, x])
print('contiguous diff runs: %d' % len(runs))

# For each run, look at the enclosing word(s) at every byte alignment and classify.
fam = collections.Counter()
kinds = collections.Counter()
samples = []
for a, b in runs:
    # try the word starts that could cover this run
    best = None
    for o in range(a - 3, a + 1):
        if o < FS or o + 4 > FE:
            continue
        vp = struct.unpack_from('>I', P, o)[0]
        vk = struct.unpack_from('>I', K, o)[0]
        if vp != vk:
            best = (o, vp, vk)
            if isal(vp) and isal(vk):
                break
    if not best:
        kinds['unclassified'] += 1
        continue
    o, vp, vk = best
    if isal(vp) and isal(vk):
        kinds['b5-alias pair'] += 1
        fam[vk - vp] += 1
    elif vp in (INSERT, FOLLOW) or vk in (INSERT, FOLLOW):
        kinds['INSERT/FOLLOW marker'] += 1
        if len(samples) < 12:
            samples.append((o, vp, vk))
    else:
        kinds['data/other'] += 1
        if len(samples) < 12:
            samples.append((o, vp, vk))

print('\nclassification:', dict(kinds))
print('alias delta families:', dict(fam.most_common(10)))
print('\nsamples (offset, pipe, key):')
for o, vp, vk in samples:
    tag = lambda v: ('INSERT' if v == INSERT else 'FOLLOW' if v == FOLLOW else
                     'b5 0x%X' % ((v - 1) & 0x1FFFFFFF) if isal(v) else '0x%08X' % v)
    print('  @0x%08X  pipe=%-14s key=%s' % (o, tag(vp), tag(vk)))

# does the pipeline carry INSERT (0xFFFFFFFE) anywhere in this span where key does not?
pi = sum(1 for o in range(FS, FE - 3) if struct.unpack_from('>I', P, o)[0] == INSERT)
ki = sum(1 for o in range(FS, FE - 3) if struct.unpack_from('>I', K, o)[0] == INSERT)
print('\nINSERT(0xFFFFFFFE) word occurrences in span: pipe=%d key=%d' % (pi, ki))
