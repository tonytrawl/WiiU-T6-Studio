import sys,re,struct
sys.path.insert(0,'.');sys.path.insert(0,'../wiiu_ref');sys.path.insert(0,'../WiiU_FF_Studio')
import loader_sim as LS
from _matconst_map import be32,be16,walk_techset,PTRS,FOLLOW
from _p1_dump13 import nm,carried,material_at_name
_c=lambda d,o,n=200:(lambda e: d[o:e].decode('latin1','replace') if e>=0 else None)(d.find(b'\x00',o,o+n))
P='../wiiu_ref/mp_raid_genuine.zone'
Z=open(P,'rb').read()
em,spans,_=LS.simulate(P,verbose=False)
for (i,en,root,s,e) in spans:
    if root!='MaterialTechniqueSet': continue
    n_=_c(Z,s+136) if be32(Z,s)==FOLLOW else None
    if not n_ or 'shadowcaster' not in n_: continue
    passes,_x=walk_techset(Z,s); sm=set(); cs=set()
    for p in passes:
        for j in range(p['nargs']):
            a=p['args_off']+j*8; t,v=be16(Z,a),be32(Z,a+4)
            if v in PTRS: continue
            if t==2: sm.add(v)
            elif t in (0,6): cs.add(v)
    print('RAID techset[%d] %-34s sampler-demand: %s'%(i,n_.lstrip(','),' '.join(sorted(nm(h) for h in sm))))
# raid shadowcaster MATERIAL
for m in re.finditer(re.escape(b'shadowcaster\x00'),Z):
    o=m.start(); b=material_at_name(Z,o)
    if b is None or b<0: continue
    if be32(Z,b) not in PTRS: continue
    try: tex,tc,cn,kind,texc,constc=carried(Z,b)
    except Exception: continue
    print('RAID material @%d %-24s texc=%d  tex=%s'%(b,_c(Z,o),texc,' '.join(nm(h) for h in tex)))
