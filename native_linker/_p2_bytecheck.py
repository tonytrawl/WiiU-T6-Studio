import sys, json
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
import _p2_inv as INV
A = open('mp_skate_final.zone','rb').read()
B = open('_p2_skate_pass2.zone','rb').read()
inv = INV.build('mp_skate_final.zone', verbose=False)
handle_offs = set(M['off']+80 for M in inv['MATS'])
diff = [i for i in range(len(A)) if A[i]!=B[i]]
words = sorted(set(i - (i % 4) if False else None for i in []))  # placeholder
# map each differing byte to the containing techSet-handle word
covered, orphan = set(), []
for i in diff:
    hit = next((h for h in (i, i-1, i-2, i-3) if h in handle_offs), None)
    if hit is None: orphan.append(i)
    else: covered.add(hit)
print('differing bytes                 : %d' % len(diff))
print('distinct material+80 handles hit: %d' % len(covered))
print('differing bytes OUTSIDE a techSet handle word: %d %s' % (len(orphan), orphan[:8]))
