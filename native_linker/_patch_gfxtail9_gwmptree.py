#!/usr/bin/env python3
"""Verify +370 correction for skate gwmp nodeTree aliases against PC target indices,
audit pathnode Links, then patch gfxtail8.zone -> gfxtail9.zone + pack ff."""
import struct, hashlib, os, sys

ROOT = r'C:\Users\Tony - Main Rig\Downloads\Testing enviroment'
os.chdir(os.path.join(ROOT, 'native_linker'))
bz = bytearray(open('mp_skate_gfxtail8.zone', 'rb').read())
pz = open(os.path.join(ROOT, 'mp_skate_pc.zone'), 'rb').read()
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)
NC, VB, SB, TC = 496, 30690, 30752, 375
F_BODY_B, F_BODY_P = 0x503d9ca, 0x6704786
F_TREE_B, F_TREE_P = 0x5067928, 0x672e6e4
TREE_BYTES = 0x5069478 - 0x5067928
TREE_G = 0x4172cfc0            # loaded tree base (dump 11848)
B5V = 0x3C5A5FC0
dec = lambda v: (v & 0x1FFFFFFF) - 1 + 64

def collect(d, ftree, e):
    """walk stream tree; return alias slots [(file_off, dec, kind)] and slot count"""
    i32 = lambda o: struct.unpack_from(e + 'i', d, o)[0]
    u32 = lambda o: struct.unpack_from(e + 'I', d, o)[0]
    o = ftree
    slots = []
    def tnode():
        nonlocal o
        axis = i32(o); o += 8
        if axis < 0:
            cnt = u32(o); o += 4
            p = u32(o)
            if p not in PTRS and p != 0:
                slots.append((o, dec(p), 'leaf'))
            o += 4
            return ('leaf', cnt, p)
        a = u32(o)
        if a not in PTRS and a != 0:
            slots.append((o, dec(a), 'c0'))
        o += 4
        b = u32(o)
        if b not in PTRS and b != 0:
            slots.append((o, dec(b), 'c1'))
        o += 4
        return ('split', a, b)
    def tdyn(info):
        nonlocal o
        if info[0] == 'leaf':
            _, cnt, p = info
            if p in PTRS:
                o += cnt * 2
        else:
            for ch in info[1:]:
                if ch in PTRS:
                    tdyn(tnode())
    infos = [tnode() for _ in range(TC)]
    for inf in infos:
        tdyn(inf)
    return slots, o

bslots, bend = collect(bz, F_TREE_B, '>')
pslots, pend = collect(pz, F_TREE_P, '<')
assert len(bslots) == len(pslots) == 374
assert all(k1 == k2 for (_, _, k1), (_, _, k2) in zip(bslots, pslots))
assert not any(k == 'leaf' for _, _, k in bslots)   # all aliases are child links

# PC: derive stream offset of tree[0]: hypothesis node0.child1 -> tree[1]
# find the first array-node0 child1 slot = 2nd slot overall (slot order: c0 then c1 of node 0)
assert bslots[0][2] == 'c0' and bslots[1][2] == 'c1'
S0_pc = pslots[1][1] - 16
idx_pc = [(sdec - S0_pc) for _, sdec, _ in pslots]
assert all(x % 16 == 0 for x in idx_pc), 'PC targets not 16-aligned under hypothesis'
idx_pc = [x // 16 for x in idx_pc]
n16 = TREE_BYTES // 16
assert all(0 <= x < n16 for x in idx_pc), (min(idx_pc), max(idx_pc), n16)

# built with correction C: resolved = B5V + dec; target = resolved + C; index = (target-TREE_G)/16
# solve C from slot 1 matching idx_pc[1] (=1):
res1 = B5V + bslots[1][1]
C = (TREE_G + idx_pc[1] * 16) - res1
print('derived correction C = %d' % C)
ok = 0
for (foff, sdec, kind), want in zip(bslots, idx_pc):
    tgt = B5V + sdec + C
    assert (tgt - TREE_G) % 16 == 0, (hex(foff), kind)
    assert (tgt - TREE_G) // 16 == want, ('index mismatch', hex(foff), (tgt - TREE_G) // 16, want)
    ok += 1
print('all %d built aliases match PC target indices under C=%d' % (ok, C))
# self-reference sanity: no split node points at itself
for j, ((foff, sdec, kind), want) in enumerate(zip(bslots, idx_pc)):
    src_node = (foff - F_TREE_B) // 16
    assert want != src_node, ('self-ref', j)
print('no self-references; index range %d..%d (n16=%d)' % (min(idx_pc), max(idx_pc), n16))

# ---- pathnode Links audit ----
def links_audit(d, body, e, tag):
    u32 = lambda o: struct.unpack_from(e + 'I', d, o)[0]
    g = lambda off: u32(body + off)
    o = body + 44
    if g(0) in PTRS:
        o = d.index(b'\x00', o) + 1
    assert g(12) in PTRS
    base = o
    cls = {}
    for i in range(NC + 128):
        v = u32(base + i * 144 + 64)
        k = 'FOLLOW' if v == FOLLOW else ('INSERT' if v == INSERT else ('NULL' if v == 0 else 'ALIAS'))
        cls[k] = cls.get(k, 0) + 1
    print('%s pathnode Links classes: %s' % (tag, cls))
links_audit(bz, F_BODY_B, '>', 'built')
links_audit(pz, F_BODY_P, '<', 'pc')

# ---- patch ----
n = 0
for (foff, sdec, kind) in bslots:
    v = struct.unpack_from('>I', bz, foff)[0]
    assert v >> 29 == 5
    nv = v + C
    assert nv >> 29 == 5
    struct.pack_into('>I', bz, foff, nv)
    n += 1
print('patched %d tree aliases (+%d)' % (n, C))
open('mp_skate_gfxtail9.zone', 'wb').write(bz)
print('mp_skate_gfxtail9.zone md5 %s' % hashlib.md5(bytes(bz)).hexdigest())
sys.path.insert(0, os.path.join(ROOT, 'WiiU_FF_Studio'))
import wiiu_ff
ff = wiiu_ff.pack(bytes(bz), 'mp_skate')
open('mp_skate_gfxtail9.ff', 'wb').write(ff)
print('mp_skate_gfxtail9.ff md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
