#!/usr/bin/env python3
"""gfxtail19: un-shadow the ONE techset whose remap the engine ignores.

ROOT CAUSE (FINDINGS_family9_name_dedup.md, final section):
'wpc_sw4_3d_unlit_4layer_570jw7w9' is a GENUINE shared techset in patch_mp (proven: the pristine
v128 update already contains it). patch_mp loads BEFORE the map, so it registers first and wins
T6's name-keyed pool:
    Load_MaterialTechniqueSetAsset -> DB_LinkXAssetEntry -> lwz r0,4(r31); stw r0,0(r30)
    (our zone's pointer is OVERWRITTEN with the pooled entry)
Our skate copy (asset 730) -- which gfxtail14 remapped to be satisfiable -- is DISCARDED, so its
materials are served the genuine body, which demands hdrAmount (0xe262b2) that our converted
materials do not carry -> unbounded constantTable walk -> AV. That is why boots 19 and 21 crashed
BYTE-IDENTICALLY across two different deployed builds: the remap never applied.

FIX: rename OUR copy (byte-length-neutral) so it no longer collides. Our aliases then resolve to
our own remapped body. SAFE for by-name lookups (Material_FindTechniqueSet -> DB_FindXAssetHeader):
patch_mp's genuine copy keeps the ORIGINAL name registered, so any name lookup still resolves to
it -- we only change which body OUR OWN asset entries land on.

Scope check (_collide_scan.py): of 241 techsets, 56 names collide and 33 were remapped, but ONLY
asset 730 is BOTH -> exactly one rename. The other 32 remaps are unshadowed and already work
(gfxtail18 cleared boot-20's 0x88befc32).

Constraints: size-neutral, byte-granular, clipMap gate in+out. Built from gfxtail18.
"""
import hashlib
import os
import re
import sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE
import clipmap_console as CC
import loader_sim as LS
import wiiu_ff
from _matconst_map import FOLLOW, be32, techset_const_hashes

SRC = 'mp_skate_gfxtail18.zone'
DST = 'mp_skate_gfxtail19.zone'
FF = 'mp_skate_gfxtail19.ff'
BB = 84512493
TARGET_ASSET = 730
OLD_NAME = b'wpc_sw4_3d_unlit_4layer_570jw7w9'
NEW_NAME = b'wpc_sw4_3d_unlit_4layer_570jw7k9'   # same length (32); 'w'->'k' in the hash suffix
assert len(OLD_NAME) == len(NEW_NAME), 'rename MUST be byte-length-neutral'

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)


def gate(buf, tag):
    m = re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'
                            + b'maps/mp/mp_skate.gsc'), bytes(buf))
    end, _ = AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    d = m.start() - end
    print('  GATE[%s] clipmap delta=%+d' % (tag, d))
    return d


assert gate(Z, 'in') == 0

em, spans, CO = LS.simulate(SRC, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}
s, e = ts_spans[TARGET_ASSET]
assert be32(Z, s) == FOLLOW, 'techset %d name is not inline' % TARGET_ASSET
noff = s + 136
end = Z.find(b'\x00', noff)
cur = bytes(Z[noff:end])
print('asset %d name @%d = %r' % (TARGET_ASSET, noff, cur.decode()))
assert cur == OLD_NAME, 'unexpected name'

# the old name must occur exactly once in the zone (inline, unaliased)
occ = [m.start() for m in re.finditer(re.escape(OLD_NAME + b'\x00'), bytes(Z))]
print('occurrences of the old name in the zone: %d %s' % (len(occ), occ))
assert occ == [noff], 'name is referenced elsewhere — renaming would break those refs'

# the NEW name must not exist anywhere: not in skate, not in any earlier-loading zone
assert bytes(Z).find(NEW_NAME) == -1, 'new name already present in skate zone'
UPD = r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e\1010cf00\content\english'
BASE = r'E:\Wii U Black ops 2\content\english'


def get_zone(p):
    r = wiiu_ff.decrypt(open(p, 'rb').read())
    if isinstance(r, dict):
        return [v for v in r.values() if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    if isinstance(r, tuple):
        return [v for v in r if isinstance(v, (bytes, bytearray)) and len(v) > 4096][0]
    return bytes(r)


for f in ('patch_mp.ff', 'common_mp.ff', 'ui_mp.ff', 'code_post_gfx_mp.ff'):
    for D in (UPD, BASE):
        p = os.path.join(D, f)
        if os.path.exists(p):
            B = get_zone(p)
            assert B.find(NEW_NAME) == -1, 'new name collides in %s' % f
            has_old = B.find(OLD_NAME) != -1
            print('  %-22s old-name present: %-5s new-name collision: no' % (f, has_old))
            break

# --- apply ---
Z[noff:noff + len(NEW_NAME)] = NEW_NAME
print('renamed -> %r' % NEW_NAME.decode())

assert len(Z) == len(orig), 'ZONE GREW — forbidden'
assert gate(Z, 'out') == 0

# our copy must be satisfiable: it is the body our materials now bind to
hs, _ = techset_const_hashes(bytes(Z), s)
print('our asset %d now demands: %s' % (TARGET_ASSET, ['0x%08x' % h for h in sorted(hs)]))
assert 0x00e262b2 not in hs, 'our copy still demands hdrAmount — remap missing!'

changed = sum(1 for i in range(len(Z)) if Z[i] != orig[i])
print('bytes changed: %d (size-neutral)' % changed)
open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
