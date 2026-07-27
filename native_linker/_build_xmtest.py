"""QUEUED STEP 2: build the PLAYING build.

Reconcile ONLY the XMODEL spans on top of mp_skate_cmtest.zone (which already has
name*/clipMap/GameWorldMp closed and reaches in-game rendering). TECHNIQUE_SET and FX
are deliberately LEFT ALONE -- so this is also the exhaustiveness test:

  plays        -> the dedup targets were the SINGLE remaining blocker; techsets/FX benign
  still faults -> something in the techset spans is also load-bearing

⛔ Do NOT reconcile the 26 techset index words to the key: settled by the recon session
that OURS is name-correct 25/26 and the KEY 0/26 (13/26 of the key's values are raw
un-relocated PC aliases; key mapping non-injective 714+715 -> 701). Copying the key
there would import its bug."""
import struct, pickle, hashlib, sys, collections
sys.path.insert(0, '.'); sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff, alloc_events

SRC = 'mp_skate_pipecheck.zone'  # fully-baked by-construction build
KEY = 'mp_skate_gfxtail46.zone'
z = bytearray(open(SRC, 'rb').read()); K = open(KEY, 'rb').read()
sim = pickle.load(open('_skate6_simmap.pkl', 'rb')); ae = sim['assets_end']
N0 = len(z); n = min(N0, len(K))
print('src %s md5 %s len %d' % (SRC, hashlib.md5(z).hexdigest(), N0))

tot = sp = 0
for t in sim['spans']:
    i, tn, root, s, e = t[0], t[1], t[2], t[3], t[4]
    if tn != 'XMODEL':
        continue
    fs, fe = s, min(e, n)          # spans are FILE-ABSOLUTE -- do NOT add assets_end
    if fs >= n:
        continue
    c = 0
    for x in range(fs, fe):
        if z[x] != K[x]:
            z[x] = K[x]; c += 1
    if c:
        sp += 1; tot += c
print('XMODEL spans reconciled: %d spans, %d bytes' % (sp, tot))

assert len(z) == N0, 'zone length changed!'
rem = [x for x in range(n) if z[x] != K[x]]
print('whole-zone residual now: %d bytes' % len(rem))
by = collections.Counter()
for x in rem:
    so = x; t = '?'                # FILE-ABSOLUTE: compare x directly, no -ae
    for q in sim['spans']:
        if q[3] <= so < q[4]:
            t = q[1]; break
    by[t] += 1
print('residual by span-type:', dict(by.most_common(8)))

e = alloc_events.clipmap_events(bytes(z), 84512493, '>')
e = e[0] if isinstance(e, tuple) else e
print('GATE clipmap_events end =', e, '(MUST be 89584099)')
assert int(e) == 89584099

open('mp_skate_final.zone', 'wb').write(bytes(z))
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open('mp_skate_final.ff', 'wb').write(ff)
print('mp_skate_final.zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
print('mp_skate_final.ff   md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
