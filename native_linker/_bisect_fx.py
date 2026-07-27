"""FX-internal bisection: like `_bisect.py ~FxEffectDef`, but only a SUBSET of the 164
FxEffectDef bodies stay OURS — the rest are transplanted genuine too. Binary-search the
culprit effect in ~7 boots.

  python _bisect_fx.py 0:82      -> OUR bodies for FX emit-indices [0,82), genuine rest
  python _bisect_fx.py 82:164
  python _bisect_fx.py 41        -> single index ours

Writes mp_raid_bisectfx_<lo>_<hi>.ff (deploy like the master says).
"""
import sys, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from collections import defaultdict, OrderedDict
import loader_sim as LS, raid_oracle_control as RC
import produce_nobackbone as PN, produce_container as PC

arg = sys.argv[1]
if ':' in arg:
    lo, hi = (int(x) for x in arg.split(':'))
else:
    lo = int(arg); hi = lo + 1

em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
gen_by_root = defaultdict(list)
for (i, nm, root, s, e) in gsp:
    if e > s:
        gen_by_root[root].append(CO[s:e])

# phase 1: capture emit order
PN.BISECT_LOG = OrderedDict()
PC.author_zone('../PC ff/mp_raid.zone', 'mp_raid',
               pc_policy=RC.PC_POLICY, our_policy=RC.GEN_POLICY, verbose=False)
log = PN.BISECT_LOG; PN.BISECT_LOG = None

by_root = defaultdict(list)
for s, (root, blen) in log.items():
    by_root[root].append((s, blen))

BMAP = {}
for root in sorted(gen_by_root):
    ours = by_root.get(root, []); gen = gen_by_root.get(root, [])
    if root == 'FxEffectDef':
        assert len(ours) == len(gen) == 164, (len(ours), len(gen))
        n = 0
        for k, ((s, _), g) in enumerate(zip(ours, gen)):
            if not (lo <= k < hi):
                BMAP[s] = g; n += 1
        print('  FxEffectDef: ours=[%d,%d) (%d bodies), genuine %d' % (lo, hi, hi - lo, n))
        continue
    if len(ours) == len(gen):
        for (s, _), g in zip(ours, gen):
            BMAP[s] = g
    else:
        used = set()
        for s, blen in ours:
            best = min((k for k in range(len(gen)) if k not in used),
                       key=lambda k: abs(len(gen[k]) - blen), default=None)
            if best is not None:
                BMAP[s] = gen[best]; used.add(best)
print('transplanting %d bodies total' % len(BMAP))

PN.BISECT_MAP = BMAP
zone, info = PC.author_zone('../PC ff/mp_raid.zone', 'mp_raid',
                            pc_policy=RC.PC_POLICY, our_policy=RC.GEN_POLICY, verbose=False)
PN.BISECT_MAP = None
open('mp_raid_authored.zone', 'wb').write(zone)
PC.rewalk_zone(zone, 'bisectfx[%d:%d]' % (lo, hi))
print('zone %.2f MB  genuine %.2f MB' % (len(zone) / 1e6, len(CO) / 1e6))

sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
ffname = 'mp_raid_bisectfx_%d_%d.ff' % (lo, hi)
open(ffname, 'wb').write(wiiu_ff.pack(zone, 'mp_raid'))
print('wrote %s' % ffname)
