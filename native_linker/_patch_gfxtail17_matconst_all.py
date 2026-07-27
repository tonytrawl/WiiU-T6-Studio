#!/usr/bin/env python3
"""gfxtail17: REDO the family-9 constant remap with a BYTE-ALIGNED material scan.

gfxtail14's audit/remap scanned materials with `o += 4`, but console Material bodies are
NOT 4-aligned (they occur at o%4 in {0,1,2,3}) -> it saw ~125 of 551 materials, so most
materials whose techset demands a constant they lack were NEVER remapped -> boot-19 still
AV'd on the hdrAmount (0xe262b2) unbounded constantTable search (Rax=0x31d44144,
Rdx=0x31d45a34, R8=0xe262b2, fault 0x50000010).
Same class of bug as the dynEnt locator: ALWAYS scan zone structures at BYTE granularity.

Fix = same HASH REMAP as gfxtail14 (rewrite an unsatisfiable type-6 arg's nameHash to a
constant common to ALL materials using that techset) but over the FULL material set.
Built from gfxtail16 (SPT desync fixed, clipmap gate delta 0).
"""
import sys, struct, hashlib, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS, wiiu_zone
import alloc_events as AE, clipmap_console as CC
from _matconst_map import (be32, be16, parse_material, walk_techset,
                           techset_const_hashes, FOLLOW, PTRS, CONSTDEF, ARG_CONST_HASH)

SRC='mp_skate_gfxtail16.zone'; DST='mp_skate_gfxtail17.zone'; FF='mp_skate_gfxtail17.ff'
BB=84512493
Z=bytearray(open(SRC,'rb').read()); orig=bytes(Z)
isalias=lambda v: 0xA0000000<=v<0xC0000000
ptrish=lambda v: v==0 or v in PTRS or isalias(v)

def gate(buf,tag):
    m=re.search(re.escape(b'\xff\xff\xff\xff\x00\x00\x09\x64\xff\xff\xff\xff'+b'maps/mp/mp_skate.gsc'),bytes(buf))
    end,_=AE.clipmap_events(bytes(buf),BB,'>',mat_span=CC._mat_span)
    d=m.start()-end; print('  GATE[%s] clipmap delta=%+d'%(tag,d)); return d
assert gate(Z,'in')==0

rc=wiiu_zone.ZoneReader(bytes(Z)); rc.read_string_table(); rc.read_asset_list()
em,spans,CO=LS.simulate(SRC,verbose=False)
ts_spans={i:(s,e) for (i,nm,root,s,e) in spans if root=='MaterialTechniqueSet' and e>s}
demand={i:techset_const_hashes(bytes(Z),s)[0] for i,(s,e) in ts_spans.items()}
n_ts=sum(1 for (c,p,nm) in rc.assets if nm=='TECHNIQUE_SET')
print('techsets: %d in asset list, %d walked (%d beyond sim break -> NOT remappable here)'%(n_ts,len(demand),n_ts-len(demand)))
arr=rc.assets_off-64; our_arr=(arr+7)&~7
def ts_idx(a):
    v=(a-1)&0x1FFFFFFF
    if (v-our_arr-4)%8: return None
    k=(v-our_arr-4)//8
    return k if 0<=k<len(rc.assets) else None

# BYTE-ALIGNED material scan (the fix)
mats=[]; last=-1
for m in re.finditer(re.escape(b'\xff\xff\xff\xff'),bytes(Z)):
    o=m.start()
    if o<last or o+104>len(Z): continue
    texc,constc,sbc=Z[o+72],Z[o+73],Z[o+74]
    if not(be32(Z,o+88)==FOLLOW and 1<=constc<=64 and texc<=64 and sbc<=64
           and all(ptrish(be32(Z,o+x)) for x in (80,84,92,96))): continue
    try:
        info,nxt=parse_material(bytes(Z),o)
        names=[Z[info['ct_off']+k*CONSTDEF+4:info['ct_off']+k*CONSTDEF+16] for k in range(constc)]
        if all(n[0:1].isalpha() and all((32<=c<127) or c==0 for c in n) for n in names) and info['name']:
            mats.append(info); last=o+104
    except Exception: pass
print('materials located (BYTE-aligned): %d'%len(mats))

by_ts={}
for mm in mats:
    if isalias(mm['ts']):
        k=ts_idx(mm['ts'])
        if k is not None and k in demand: by_ts.setdefault(k,[]).append(set(mm['consts']))
plan={}
for k,sets in by_ts.items():
    miss=set()
    for s_ in sets: miss|=(demand[k]-s_)
    if not miss: continue
    inter=set.intersection(*sets)
    if not inter:
        print('  !! techset %d: NO common constant across %d materials -> cannot remap'%(k,len(sets))); continue
    plan[k]=(miss,min(inter))
print('techsets to remap: %d (unsafe hashes %d)'%(len(plan),sum(len(v[0]) for v in plan.values())))

n_args=0
for k,(bad,target) in plan.items():
    s,e=ts_spans[k]
    passes,_=walk_techset(bytes(Z),s)
    for p in passes:
        assert p['lits']==0
        base=p['args_off']
        for j in range(p['nargs']):
            a=base+j*8
            if be16(Z,a)==ARG_CONST_HASH and be32(Z,a+4) in bad:
                struct.pack_into('>I',Z,a+4,target); n_args+=1
print('remapped %d type-6 args'%n_args)
assert len(Z)==len(orig)
assert gate(Z,'out')==0
# verify: zero materials left missing
bad=0
for mm in mats:
    if not isalias(mm['ts']): continue
    k=ts_idx(mm['ts'])
    if k is None or k not in ts_spans: continue
    hs,_=techset_const_hashes(bytes(Z),ts_spans[k][0])
    if hs-set(mm['consts']): bad+=1
print('materials still MISSING a demanded constant: %d / %d'%(bad,len(mats)))
assert bad==0
print('bytes changed: %d (size-neutral)'%sum(1 for i in range(len(Z)) if Z[i]!=orig[i]))
open(DST,'wb').write(bytes(Z)); print('%s md5 %s'%(DST,hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff=wiiu_ff.pack(bytes(Z),'mp_skate'); open(FF,'wb').write(ff)
print('%s md5 %s (%d bytes)'%(FF,hashlib.md5(ff).hexdigest(),len(ff)))
