"""Comprehensive anchor map for all 7 stale-alias families (measured_rtmap REVERTED
to linear). For each family pointer we know true_rt (gfxtail11) and buggy_rt
(gfxtail7). Log every rt() call; match by result==buggy_rt to find the anchor
(mlo,mrs) and quantify linear-vs-true gap + which measured span mlo belongs to."""
import sys, struct, pickle, bisect
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import produce_container as PC
import material_convert as MC
import smalls_convert as SM
import measured_rtmap as MR

LOG = []
_orig = MR.MeasuredRuntimeMap.rt
def rt_logged(self, dom):
    res = _orig(self, dom)
    if self._meas_lo is not None:
        j = bisect.bisect_right(self._meas_lo, dom) - 1
        mlo, mrs = self._meas[j] if j >= 0 else (None, None)
    else:
        mlo = mrs = None
    LOG.append((res, dom, mlo, mrs))
    return res
MR.MeasuredRuntimeMap.rt = rt_logged

# span label for a given mlo (measured start dom)
S = pickle.load(open('_skate2_simmap.pkl', 'rb')); AE = S['assets_end']
SPAN_BY_LO = {}
for (i, nm, root, s, e) in S['spans']:
    SPAN_BY_LO[s - AE] = (nm, root, s - AE, e - AE)

pcp = LS.derive_pc_policy('../mp_skate_pc.zone', verbose=False)
rtm = MR.MeasuredRuntimeMap('_skate2_simmap.pkl', '_skate2_realmap.pkl')
PC.MATMEM_STREAM_ALL = True
SM.SNDBANK_EMPTY_ALIASES = {'mpl_skate.all'}
SM.SNDBANK_LOADEDASSETS_ORACLE = None
SM.SNDBANK_HEAD_SANITIZE = {'mpl_skate.all'}
zone, info = PC.author_zone('../mp_skate_pc.zone', 'mp_skate', verbose=False,
                            pc_policy=pcp, our_policy=None, override_rtmap=rtm,
                            image_ipak=[r'E:/pluto_t6_full_game/zone/all/dlc1.ipak',
                                        '../skate_artifact/mp_skate.ipak'])

g7 = open('mp_skate_gfxtail7.zone', 'rb').read()
g11 = open('mp_skate_gfxtail11.zone', 'rb').read()
def rtv(buf, off):
    a = struct.unpack_from('>I', buf, off)[0]
    return (a & 0x1FFFFFFF) - 1 if (a >> 29) == 5 else None

# family sample offsets
BB = 84512493; F_SIDES = 0x509ede9; F_BRUSHES = 0x51df69d; F_BRUSHES_END = 0x527f6bd
u32 = lambda o: struct.unpack_from('>I', g7, o)[0]; u16 = lambda o: struct.unpack_from('>H', g7, o)[0]
NSIDES = u32(BB+24); NBRUSH = u16(BB+64); NSM = u32(BB+84); NNODES = u32(BB+92)
F_NODES = F_BRUSHES_END + NSM*84
fam = {
  'planes@+12':        [BB+12],
  'leafbrushes@+44':   [BB+44],
  'brushside_plane':   [F_SIDES+0*12, F_SIDES+(NSIDES-1)*12],
  'cnode_plane':       [F_NODES+0*8, F_NODES+(NNODES-1)*8],
  'brush_sides':       [F_BRUSHES+0*96+32, F_BRUSHES+(NBRUSH-1)*96+32],
  'brush_verts':       [F_BRUSHES+0*96+88, F_BRUSHES+(NBRUSH-1)*96+88],
}

# index LOG by result
byres = {}
for (res, dom, mlo, mrs) in LOG:
    byres.setdefault(res, []).append((dom, mlo, mrs))

print('total rt calls %d  distinct results %d' % (len(LOG), len(byres)))
for name, offs in fam.items():
    print('\n== %s ==' % name)
    for off in offs:
        bg = rtv(g7, off); tr = rtv(g11, off)
        if bg is None:
            print('  @0x%x not blk5' % off); continue
        hits = byres.get(bg, [])
        anchor = ''
        if hits:
            dom, mlo, mrs = hits[0]
            sp = SPAN_BY_LO.get(mlo, ('?','?',mlo,mlo))
            anchor = 'dom=%d anchor=%s/%s[%d,%d) mrs=%d dom-mlo=%d' % (
                dom, sp[0], sp[1], sp[2], sp[3], mrs, dom-mlo)
        print('  @0x%x buggy_rt=0x%x true_rt=0x%x drift=%+d | %s' % (
            off, bg, tr, tr-bg, anchor if anchor else 'NO rt() call had this result'))
