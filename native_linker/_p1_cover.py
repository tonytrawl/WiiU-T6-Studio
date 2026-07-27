import sys,re,struct
sys.path.insert(0,'.');sys.path.insert(0,'../wiiu_ref');sys.path.insert(0,'../WiiU_FF_Studio')
from collections import defaultdict
import loader_sim as LS
from _matconst_map import be32,be16,walk_techset,PTRS,FOLLOW
from _p1_dump13 import nm
_c=lambda d,o,n=200:(lambda e: d[o:e].decode('latin1','replace') if e>=0 else None)(d.find(b'\x00',o,o+n))
Z=open('mp_skate_final.zone','rb').read(); pc=open('../mp_skate_pc.zone','rb').read()
emc,spansc,_=LS.simulate('mp_skate_final.zone',verbose=False)
empc,spanspc,_=LS.simulate_pc(pc,verbose=False)
co={}; 
for (i,en,root,s,e) in spansc:
    if root=='MaterialTechniqueSet':
        n_=_c(Z,s+136) if be32(Z,s)==FOLLOW else None
        if n_: co[(n_).lstrip(',')]=s
pcn={}
for sp in spanspc:
    if sp[2]=='MaterialTechniqueSet':
        n_=_c(pc,sp[3]+152)
        if n_: pcn[n_.lstrip(',')]=sp[3]
print('console techset names: %d   PC techset names: %d'%(len(co),len(pcn)))
both=set(co)&set(pcn); print('names in BOTH: %d   PC-only (absent on console): %d   console-only: %d'%(len(both),len(set(pcn)-set(co)),len(set(co)-set(pcn))))
lit=[n for n in set(pcn)-set(co) if n.startswith('lit_sm_')]
print('PC-only that are lit_sm_ blend techsets: %d'%len(lit))
print('sample PC-only:',sorted(set(pcn)-set(co))[:8])
def dem(Zb,s,walker):
    passes,_=walker(Zb,s); c,sm=set(),set()
    for p in passes:
        for j in range(p['nargs']):
            a=p['args_off']+j*8; t,v=be16(Zb,a),be32(Zb,a+4)
            if v in PTRS: continue
            if t in (0,6): c.add(v)
            elif t==2: sm.add(v)
    return c,sm
print('\n--- console BODY vs PC BODY for shared names (sampler demand) ---')
same=diff=0; ex=[]
for n_ in sorted(both):
    try: _,sc=dem(Z,co[n_],walk_techset)
    except Exception: continue
    try:
        import shader_probe_pc as SPP
    except Exception: SPP=None
    if n_=='wpc_shadowcaster_wj6w5j60' or len(ex)<6:
        ex.append((n_,sorted(nm(h) for h in sc)))
for n_,s in ex[:8]: print('   %-42s %s'%(n_[:42],' '.join(s)))
