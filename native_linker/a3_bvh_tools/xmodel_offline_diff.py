import sys, os, pickle
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
import loader_sim as LS, raid_oracle_control as RC, produce_nobackbone as PN
import produce_container as PCN
import material_convert as MC, ipak_stream as ISM
from collections import defaultdict

# A1 hooks (gotcha: assemble_zone loops must wire manually)
MC.XMODEL_IMAGE_SOURCE = PCN._make_pc_image_source(list(ISM.DEFAULT_PC_IPAKS))
MC.MATMEM_IMAGE_SOURCE = PCN._make_pc_image_source(list(ISM.DEFAULT_PC_IPAKS))
MC.RESIDENT_IMAGE_TEST = (PCN.make_console_resident_test() or
                          PCN._make_resident_test(list(ISM.DEFAULT_PC_IPAKS)))

ROOT = 'XModel'
em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
gen = defaultdict(list)
for (i, nm, r, s, e) in gsp:
    if e > s and r == ROOT: gen[nm].append(CO[s:e])
stat, out, omap = PN.assemble_zone('../PC ff/mp_raid.zone', verbose=False,
                                   pc_policy=RC.PC_POLICY, our_policy=RC.GEN_POLICY)
occ = defaultdict(int)
diffs = []
SCR = os.path.dirname(os.path.abspath(__file__))
for (i, nm, r, body, why) in out:
    if r != ROOT or body is None: continue
    k = occ[nm]; occ[nm] += 1; gl = gen.get(nm)
    if not gl or k >= len(gl): continue
    g = gl[k]
    if body == g: continue
    d = len(body) - len(g)
    fd = next((j for j in range(min(len(body), len(g))) if body[j] != g[j]),
              min(len(body), len(g)))
    diffs.append((i, nm, d, fd, len(body), len(g)))
    if i in (210, 598, 394, 470, 183, 258, 232, 253):
        open(os.path.join(SCR, 'idx%d_our.bin' % i), 'wb').write(body)
        open(os.path.join(SCR, 'idx%d_gen.bin' % i), 'wb').write(g)
print('total diffs:', len(diffs), ' net:', sum(d for _, _, d, _, _, _ in diffs))
for (i, nm, d, fd, lo, lg) in diffs:
    print('%-4d %-36s our=%d gen=%d delta=%+d firstdiff@0x%x' % (i, nm[:36], lo, lg, d, fd))
