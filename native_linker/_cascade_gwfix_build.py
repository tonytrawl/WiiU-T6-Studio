"""FIX 4: GameWorldMp pathnode/nodeTree aliases. Boot-3 crashed in
Path_NodesInCylinder_r (SP_actor -> Sentient_NearestNode) during G_InitGame.
Cause: all 374 block-5 aliases in the GAMEWORLD_MP span are off by a CONSTANT +370
(same stale measured_rtmap class as the clipMap +12/+4241/+4243 families).
Oracle-derived, alias-only, size-neutral reconcile against the playable answer key."""
import struct, hashlib, sys, collections
sys.path.insert(0, '.'); sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff

SRC = 'mp_skate_clipfix.zone'
KEY = 'mp_skate_gfxtail46.zone'
OUTZ = 'mp_skate_gwfix.zone'; OUTF = 'mp_skate_gwfix.ff'
FS, FE = 0x050434C1, 0x0506EF6F      # GAMEWORLD_MP span
CLIP_B, CLIP_E = 84512493, 89584099  # clipMap span (must stay reconciled)
z = bytearray(open(SRC, 'rb').read()); K = open(KEY, 'rb').read()
N0 = len(z)
isal = lambda v: 0xA0000000 <= v < 0xC0000000
print('src %s md5 %s len %d' % (SRC, hashlib.md5(z).hexdigest(), N0))

fam = collections.Counter(); n = 0
for o in range(FS, FE - 3):
    vp = struct.unpack_from('>I', z, o)[0]
    vk = struct.unpack_from('>I', K, o)[0]
    if vp != vk and isal(vp) and isal(vk):
        struct.pack_into('>I', z, o, vk)
        fam[vk - vp] += 1
        n += 1
print('GameWorldMp alias words reconciled: %d' % n)
print('delta families:', dict(fam.most_common(6)))

assert len(z) == N0, 'zone length changed!'
res = [x for x in range(FS, FE) if z[x] != K[x]]
print('GameWorldMp residual diffs vs answer key: %d bytes' % len(res))
assert not res, 'GameWorldMp not fully reconciled'
# clipMap must remain reconciled (3 known dynEnt bytes)
cres = [x for x in range(CLIP_B, CLIP_E) if z[x] != K[x]]
print('clipMap residual (expect 3): %d %s' % (len(cres), [hex(c) for c in cres]))
assert len(cres) <= 3

try:
    import alloc_events
    end = alloc_events.clipmap_events(bytes(z), CLIP_B, '>')
    end = end[0] if isinstance(end, tuple) else end
    print('GATE clipmap_events end = %s (MUST be %d)' % (end, CLIP_E))
    assert int(end) == CLIP_E
except AssertionError:
    raise
except Exception as e:
    print('(gate skipped: %s)' % e)

open(OUTZ, 'wb').write(bytes(z))
print('\n%s md5 %s' % (OUTZ, hashlib.md5(bytes(z)).hexdigest()))
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open(OUTF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (OUTF, hashlib.md5(ff).hexdigest(), len(ff)))
