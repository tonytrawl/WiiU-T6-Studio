"""Measure ACTUAL per-block consumption of the sndcap4 authoring vs the hardcoded
header constants (BLOCK0/1/2_MP). Suspect: block2 (runtime) understated -> engine
arena at 0x103efe00 too small -> loader writes past it into the OSThread pool
(+0x3817ce/e2 family). Solo run."""
import sys
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
import loader_sim as LS
import produce_container as PC
import smalls_convert as SM
from measured_rtmap import MeasuredRuntimeMap

pcp = LS.derive_pc_policy('../mp_skate_pc.zone', verbose=False)
rtm = MeasuredRuntimeMap('_skate4_simmap.pkl', '_skate4_realmap.pkl')
PC.MATMEM_STREAM_ALL = True
SM.SNDBANK_EMPTY_ALIASES = {'mpl_skate.all'}
SM.SNDBANK_LOADEDASSETS_ORACLE = (1024, 14000128)
SM.SNDBANK_HEAD_SANITIZE = {'mpl_skate.all'}
zone, info = PC.author_zone('../mp_skate_pc.zone', 'mp_skate', verbose=False,
                            pc_policy=pcp, our_policy=None, override_rtmap=rtm,
                            image_ipak=[r'E:/pluto_t6_full_game/zone/all/dlc1.ipak',
                                        '../skate_artifact/mp_skate.ipak'])
om = info['omap']
print('omap.block_size:', list(om.block_size))
print('header constants: B0=%d B1=%d B2=%d' % (PC.BLOCK0_MP, PC.BLOCK1_MP, PC.BLOCK2_MP))
for i, v in enumerate(om.block_size):
    const = {0: PC.BLOCK0_MP, 1: PC.BLOCK1_MP, 2: PC.BLOCK2_MP}.get(i)
    if const is not None:
        print('block%d actual=%s const=%s  DEFICIT=%s' % (i, format(v, ','),
              format(const, ','), format(v - const, ',')))
