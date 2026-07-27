"""Content bisection: build mp_raid_authored.zone transplanting GENUINE bodies for
a chosen set of asset ROOTS (our converter used for all others). Boot each variant;
when the crash disappears, the transplanted set contains the culprit converter.
  python _bisect.py ALL              -> transplant every type (should boot = sanity)
  python _bisect.py GfxWorld,clipMap_t
  python _bisect.py NONE             -> pure our-converter (baseline crash)
"""
import sys, struct
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from collections import defaultdict, OrderedDict
import loader_sim as LS, raid_oracle_control as RC
import produce_nobackbone as PN, produce_container as PC

arg = sys.argv[1] if len(sys.argv) > 1 else 'NONE'

em, gsp, CO = LS.simulate(RC.CO_PATH, policy=RC.GEN_POLICY)
gen_by_root = defaultdict(list)
for (i, nm, root, s, e) in gsp:
    if e > s:
        gen_by_root[root].append(CO[s:e])

targets = (set(gen_by_root) if arg == 'ALL'
           else set() if arg == 'NONE'
           else (set(gen_by_root) - set(arg[1:].split(','))) if arg.startswith('~')
           else set(arg.split(',')))

# phase 1: capture emit order {pc_off: root}
PN.BISECT_LOG = OrderedDict()
PC.author_zone('../PC ff/mp_raid.zone', 'mp_raid',
               pc_policy=RC.PC_POLICY, our_policy=RC.GEN_POLICY, verbose=False)
log = PN.BISECT_LOG; PN.BISECT_LOG = None

by_root = defaultdict(list)          # root -> [(s, our_len), ...] in emit order
for s, (root, blen) in log.items():
    by_root[root].append((s, blen))

BMAP = {}
for root in sorted(targets):
    ours = by_root.get(root, []); gen = gen_by_root.get(root, [])
    if len(ours) == len(gen):        # counts match -> positional (validated for XModel etc.)
        for (s, _), g in zip(ours, gen):
            BMAP[s] = g
        n = len(ours)
    else:                            # unequal (e.g. SndBank english insert): match by nearest size
        pool = sorted(range(len(gen)), key=lambda k: len(gen[k]))
        used = set(); n = 0
        for s, blen in ours:
            best = min((k for k in range(len(gen)) if k not in used),
                       key=lambda k: abs(len(gen[k]) - blen), default=None)
            if best is not None:
                BMAP[s] = gen[best]; used.add(best); n += 1
    if targets != set(gen_by_root):
        print('  %-22s ours=%d gen=%d transplanted=%d' % (root, len(ours), len(gen), n))
print('transplanting %d roots, %d bodies' % (len(targets), len(BMAP)))

# phase 2: build with substitution
PN.BISECT_MAP = BMAP
zone, info = PC.author_zone('../PC ff/mp_raid.zone', 'mp_raid',
                            pc_policy=RC.PC_POLICY, our_policy=RC.GEN_POLICY, verbose=False)
PN.BISECT_MAP = None

# HARNESS FIX (2026-07-13, Track 0): aliased-row hp words re-derived from the
# GENUINE hp via the identity-proven relocator — the author's omap.reloc path
# resolves them through converter interior fine-maps, which the resident-image
# tracks legitimately diverged (broke rows 849/883/885/886/888 = FX NULL+0x14).
# ALL also restores genuine's header b5 word (byte-identical acceptance).
import spine_selfpolicy as SPINE
genctx = SPINE.gen_context()
zone, _nfix = SPINE.fix_hp_rows(zone, info, genctx, force_gen_b5=(arg == 'ALL'))
open('mp_raid_authored.zone', 'wb').write(zone)
PC.rewalk_zone(zone, 'bisect[%s]' % arg)
print('zone %.2f MB  genuine %.2f MB' % (len(zone) / 1e6, len(CO) / 1e6))

# pack to .ff (named per the bisect arg so builds don't clobber)
sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
safe = arg.replace('~', 'not').replace(',', '_')
ffname = 'mp_raid_bisect_%s.ff' % safe
open(ffname, 'wb').write(wiiu_ff.pack(zone, 'mp_raid'))
print('wrote %s' % ffname)
