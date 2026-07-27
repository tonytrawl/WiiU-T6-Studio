"""Family 9 scope audit: for every console Material in the skate zone, collect its
constantTable nameHashes; for every MaterialTechniqueSet collect its passes' type-6
arg hashes (constant-by-hash). Report materials whose techset demands a constant the
material's constantTable lacks -> the unbounded-search crash (AV 0x50000010).

Console Material body (104B): texc@72, constc@73, sbc@74, techniqueSet@80,
textureTable@84, constantTable@88.  MaterialConstantDef = 32B {u32 hash, char[12] name,
float[4] literal}.
"""
import sys, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS

ZONE = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_gfxtail13.zone'
Z = open(ZONE, 'rb').read()
u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
u16 = lambda o: struct.unpack_from('>H', Z, o)[0]

em, spans, CO = LS.simulate(ZONE, verbose=False)
mats = [(i, nm, s, e) for (i, nm, root, s, e) in spans if root == 'Material' and e > s]
tss = [(i, nm, s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s]
print('zone %s: Material assets=%d  MaterialTechniqueSet assets=%d' % (ZONE, len(mats), len(tss)))

def const_hashes(s, e):
    """constantTable hashes of the material body at s. Find the 32B constdef run
    structurally: hash + printable name[12] + 16B literal, constc entries."""
    constc = Z[s + 73]
    if constc == 0:
        return constc, []
    # scan the material span for the first plausible constdef run of length constc
    for o in range(s + 104, min(e, s + 104 + 4096) - constc * 32 + 1):
        ok = True
        for k in range(constc):
            nm = Z[o + k * 32 + 4: o + k * 32 + 16]
            if not (nm[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in nm)):
                ok = False; break
        if ok:
            return constc, [u32(o + k * 32) for k in range(constc)]
    return constc, None      # couldn't locate

tot = 0; located = 0; missing_any = 0
allconst = {}
for (i, nm, s, e) in mats:
    constc, hashes = const_hashes(s, e)
    tot += 1
    if hashes is not None:
        located += 1
        allconst[i] = set(hashes)
print('materials with constantTable located: %d/%d' % (located, tot))

# global picture: which hashes exist as constants anywhere
univ = set()
for v in allconst.values():
    univ |= v
print('distinct constant hashes across all materials: %d' % len(univ))
HDR = 0x00e262b2
print('hdrAmount(0x%08x) present in %d/%d materials' %
      (HDR, sum(1 for v in allconst.values() if HDR in v), len(allconst)))
print('materials with ZERO constants: %d' % sum(1 for v in allconst.values() if not v))
# distribution of constant counts
from collections import Counter
cc = Counter(len(v) for v in allconst.values())
print('constantCount distribution:', dict(sorted(cc.items())[:12]))
