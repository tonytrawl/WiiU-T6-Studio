"""Run the PATCHED techset_rebind end-to-end on the PRE-fix pipeline artifact."""
import sys, os, struct
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
import techset_rebind as TR
Z = open('mp_skate_final.zone','rb').read()
PC = open('../mp_skate_pc.zone','rb').read()
out = TR.rebind_matmem_techsets(Z, PC, 'mp_skate', verbose=True)
print('size in=%d out=%d delta=%d' % (len(Z), len(out), len(out)-len(Z)))
d = [i for i in range(len(Z)) if Z[i]!=out[i]]
print('bytes changed: %d  at %d distinct 4-byte handles' % (len(d), len(set(x//4 for x in d))))
open('_p2_skate_pass2.zone','wb').write(out)
