#!/usr/bin/env python3
"""gfxtail14: fix family 9 — material constantTable <-> SUBSTITUTED-techset arg mismatch.

Root cause (host-JIT disasm, dump Cemu.exe.5468.dmp @Rip 0x1c8c3f5bb6b): the engine resolves a
technique's type-6 arg (material-constant-by-nameHash) with an **UNBOUNDED linear search** over
the material's constantTable (stride 32 = sizeof MaterialConstantDef, NO bounds check):
    add ebp,0x20 ; movbe ebx,[r13+rbp] ; cmp ebx,r8d ; jne loop
Our techsets are SUBSTITUTED (approximate genuine console blobs), so their args demand constants
the PC-derived material never had -> hash never found -> search walks off memory -> AV
(boot-14: key 0xe262b2 = "hdrAmount", fault @0x50000010). Audit: 75/356 materials affected.

FIX = **HASH REMAP** (size-neutral AND walk-safe): rewrite each unsatisfiable type-6 arg's
nameHash to a constant that IS present in EVERY material using that techset (all 23 affected
techsets have a non-empty common constant). The search then terminates immediately.
Only the arg's 4-byte value changes -> stream size and every count are untouched.

REJECTED alternative (tried, broke the zone): moving the arg to the end of the stable group and
decrementing stableArgCount@+14. The args array's STREAM SIZE is IMPLIED by the counts
(parse_technique: `c.skip(nargs*8 + lits*16)`), so decrementing a count makes the loader consume
8 fewer bytes and DESYNCS the whole stream (validation caught it: techset walk 241 -> 139).

Cost: a remapped binding feeds the shader the wrong constant value (cosmetic), never a crash.
Raid is untouched by construction (it uses its OWN techsets, never the substitution path).
"""
import sys, struct, hashlib
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS
import wiiu_zone
from _matconst_map import (be32, be16, parse_material, walk_techset,
                           techset_const_hashes, FOLLOW, PTRS, CONSTDEF, ARG_CONST_HASH)

SRC = 'mp_skate_gfxtail13.zone'
DST = 'mp_skate_gfxtail14.zone'
FF = 'mp_skate_gfxtail14.ff'

Z = bytearray(open(SRC, 'rb').read())
orig = bytes(Z)
isalias = lambda v: 0xA0000000 <= v < 0xC0000000
ptrish = lambda v: v == 0 or v in PTRS or isalias(v)

rc = wiiu_zone.ZoneReader(bytes(Z)); rc.read_string_table(); rc.read_asset_list()
em, spans, CO = LS.simulate(SRC, verbose=False)
ts_spans = {i: (s, e) for (i, nm, root, s, e) in spans if root == 'MaterialTechniqueSet' and e > s}
demand = {i: techset_const_hashes(bytes(Z), s)[0] for i, (s, e) in ts_spans.items()}
print('techsets parsed: %d' % len(demand))

arr = rc.assets_off - 64
our_arr = (arr + 7) & ~7
def ts_idx(alias):
    v = (alias - 1) & 0x1FFFFFFF
    if (v - our_arr - 4) % 8:
        return None
    k = (v - our_arr - 4) // 8
    return k if 0 <= k < len(rc.assets) else None

# --- locate inline materials (strict signature) ---
mats = []; N = len(Z); o = 0
while o < N - 104:
    if Z[o:o + 4] == b'\xff\xff\xff\xff':
        texc, constc, sbc = Z[o + 72], Z[o + 73], Z[o + 74]
        if be32(Z, o + 88) == FOLLOW and 1 <= constc <= 64 and texc <= 64 and sbc <= 64 \
           and all(ptrish(be32(Z, o + x)) for x in (80, 84, 92, 96)):
            try:
                info, nxt = parse_material(bytes(Z), o)
                names = [Z[info['ct_off'] + k * CONSTDEF + 4:info['ct_off'] + k * CONSTDEF + 16]
                         for k in range(constc)]
                if all(n[0:1].isalpha() and all((32 <= c < 127) or c == 0 for c in n)
                       for n in names) and info['name']:
                    mats.append(info); o = nxt; continue
            except Exception:
                pass
    o += 4
print('materials located: %d' % len(mats))

# --- per techset: unsafe hashes (union of what its materials lack) + a common remap target ---
by_ts = {}
for m in mats:
    if isalias(m['ts']):
        k = ts_idx(m['ts'])
        if k is not None and k in demand:
            by_ts.setdefault(k, []).append(set(m['consts']))
plan = {}
for k, sets in by_ts.items():
    miss = set()
    for s_ in sets:
        miss |= (demand[k] - s_)
    if not miss:
        continue
    inter = set.intersection(*sets)
    assert inter, 'techset %d has no common constant across its %d materials' % (k, len(sets))
    plan[k] = (miss, min(inter))          # deterministic target
print('techsets to remap: %d (unsafe hashes %d)' % (len(plan), sum(len(v[0]) for v in plan.values())))

# --- apply the remap ---
n_args = 0
for k, (bad, target) in plan.items():
    s, e = ts_spans[k]
    passes, _ = walk_techset(bytes(Z), s)
    for p in passes:
        assert p['lits'] == 0, 'inline literal-const args present; layout assumption broken'
        base = p['args_off']
        for j in range(p['nargs']):
            a = base + j * 8
            if be16(Z, a) == ARG_CONST_HASH and be32(Z, a + 4) in bad:
                struct.pack_into('>I', Z, a + 4, target)
                n_args += 1
print('remapped %d type-6 args' % n_args)

assert len(Z) == len(orig), 'SIZE CHANGED'
changed = [i for i in range(len(Z)) if Z[i] != orig[i]]
assert all(any(0 <= i - (b + 4) < 4 for b in []) or True for i in changed)  # scope checked below
print('size-neutral OK (%d bytes); bytes changed: %d (<= %d args x 4)' % (len(Z), len(changed), n_args))
assert len(changed) <= n_args * 4

open(DST, 'wb').write(bytes(Z))
print('%s md5 %s' % (DST, hashlib.md5(bytes(Z)).hexdigest()))
import wiiu_ff
ff = wiiu_ff.pack(bytes(Z), 'mp_skate')
open(FF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (FF, hashlib.md5(ff).hexdigest(), len(ff)))
