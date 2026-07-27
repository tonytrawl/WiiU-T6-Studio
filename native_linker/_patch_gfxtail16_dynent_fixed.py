#!/usr/bin/env python3
"""gfxtail16: redo the dynEnt physPreset null CORRECTLY (gfxtail15 desynced the zone).

+0x38 = DynEntityDef.physPreset* (db+56). alloc_events.clipmap_events consumes an
inline PhysPreset ONLY when db+56 is FOLLOW. gfxtail15 nulled ALL 267 records incl. the
2 FOLLOW ones -> 2 x 84B PhysPresets orphaned = +168 stray bytes -> loader's cursor 168
short -> assets 807+ (the SPTs) read headers from inside clipMap -> name*=NULL ->
AV in DB_LinkXAssetEntry (boots 16/17/18).
Now: null ONLY block-5 aliases (109), leave FOLLOW/INSERT intact.
GATE: clipmap_events end MUST == next asset start (delta 0, exact on the raid oracle).
Built from gfxtail14 (last delta-0 zone).
"""
import sys, struct, hashlib, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import alloc_events as AE, clipmap_console as CC
from gfxworld_dynent_fix import find_dynent_arrays, null_dynent_lightlists, STRIDE

SRC='mp_skate_gfxtail14.zone'; DST='mp_skate_gfxtail16.zone'; FF='mp_skate_gfxtail16.ff'
BB=84512493
z=bytearray(open(SRC,'rb').read()); orig=bytes(z)

def gate(buf, tag):
    m=re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'+b'maps/mp/mp_skate.gsc'), bytes(buf))
    spt=m.start()
    end,_=AE.clipmap_events(bytes(buf), BB, '>', mat_span=CC._mat_span)
    print('  GATE[%s]: clipmap_events end=%d  next asset=%d  delta=%+d' % (tag, end, spt, spt-end))
    return spt-end

assert gate(z,'gfxtail14 in')==0, 'source zone already desynced'
n,nulled = null_dynent_lightlists(z)
d=gate(z,'gfxtail16 out')
assert d==0, 'DESYNC introduced (delta %+d) — refusing to write' % d
assert len(z)==len(orig), 'size changed'
live=sum(1 for s,c in find_dynent_arrays(z) for k in range(c)
         if 0xA0000000<=struct.unpack_from('>I',z,s+k*STRIDE+0x38)[0]<0xC0000000)
print('  live +0x38 block-5 aliases AFTER: %d (want 0)'%live)
assert live==0
changed=sum(1 for i in range(len(z)) if z[i]!=orig[i])
print('  bytes changed: %d (size-neutral)'%changed)
open(DST,'wb').write(bytes(z)); print('%s md5 %s'%(DST,hashlib.md5(bytes(z)).hexdigest()))
import wiiu_ff
ff=wiiu_ff.pack(bytes(z),'mp_skate'); open(FF,'wb').write(ff)
print('%s md5 %s (%d bytes)'%(FF,hashlib.md5(ff).hexdigest(),len(ff)))
