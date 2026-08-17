#!/usr/bin/env python3
"""
Extract genuine Wii U MaterialTechniqueSet assets as SELF-CONTAINED byte blobs
for substitution into a PC->WiiU converted zone (OAT_TECHSET_DIR hook in
ContentWriterT6, mirroring the OAT_IMAGE_DIR pattern).

Why: the PC->WiiU write path cannot transcode D3D11 shaders to GX2, so it
emits techsets with null shader subtrees — the prime suspect for the silent
DB-load freeze. The genuine zone for the same map carries all 229 techsets
(174 inline with real GX2 shaders + 55 as ,refs into common_mp).

Genuine techsets alias shared subobjects (block-offset pointers to earlier
stream data): techniques shared across techsets, shader refs, vertex decls,
literal-const vec4s, name strings. A raw splice would dangle those, so this
extractor RESOLVES every alias via a fitted VIRTUAL-block-offset -> file
-offset delta map and re-serializes each techset with the target INLINED
(alias -> FOLLOW + bytes), recursively.

Output: <dir>/<safe name>.techset  = bytes to write verbatim as the asset
stream (136-byte body + name + techniques...), 4-aligned start assumed.
Refs are emitted verbatim as the genuine 136-byte zeroed body + ",name".

Usage:
  python techset_extract.py <genuine zone> <out dir> [names.txt]
Self-check: every emitted blob is re-parsed with shader_probe (must consume
exactly len(blob)) and must contain no alias pointers.
"""
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shader_probe as SP

FOLLOW = 0xFFFFFFFF


def be32(v):
    return struct.pack('>I', v)


class Extractor:
    def __init__(self, zone):
        self.d = zone
        self.techsets = {}      # name -> (body_off, end_off)
        self.refsets = {}       # name -> bytes (verbatim 136 + ",name\0")
        self.tech_offs = []     # technique body file offsets
        self.decl_offs = []     # vertexDecl file offsets
        self.sref_offs = []     # shader-ref (12B) file offsets
        self.sref_kind = {}     # sref file offset -> 'vs'/'ps'
        self.gx2_offs = {'vs': [], 'ps': []}   # inline GX2 body file offsets
        self.amap = {}          # virtual block-5 offset -> file offset (solved)
        self.anchors = []       # sorted (virtual, file) pairs from amap
        self._catalogue()
        self._tech_set = set(self.tech_offs)
        self._decl_set = set(self.decl_offs)
        self._sref_set = set(self.sref_offs)
        self._sref_kset = {k: {o for o in self._sref_set
                               if self.sref_kind[o] == k}
                           for k in ('vs', 'ps')}
        print('catalogued %d techsets, %d tech / %d decl / %d sref objs'
              % (len(self.techsets), len(self.tech_offs),
                 len(self.decl_offs), len(self.sref_offs)))
        self._solve_aliases()

    # ---------------- catalogue pass -------------------------------------
    def u32(self, o):
        return struct.unpack('>I', self.d[o:o + 4])[0]

    def u16(self, o):
        return struct.unpack('>H', self.d[o:o + 2])[0]

    def _catalogue(self):
        d = self.d
        cands = SP.find_techsets(d)
        seen = []
        for o, nm in sorted(cands):
            if any(a <= o < b for a, b in seen):
                continue
            try:
                end, _ = SP.parse_techset(d, o)
            except SP.Fail:
                continue
            seen.append((o, end))
            self.techsets[nm] = (o, end)
            # walk again recording object offsets
            slots = [self.u32(o + 8 + i * 4) for i in range(32)]
            c = SP.Cur(d, o + 136)
            c.cstr(160)
            for v in slots:
                if v != FOLLOW:
                    continue
                self.tech_offs.append(c.o)
                c.o = self._walk_technique(c.o)
        # ref-encoded techsets: 136-byte body + ",name"
        i = -1
        while True:
            i = d.find(b'\x00,', i + 1)
            if i < 0:
                break
            e = d.find(b'\x00', i + 2)
            if e < 0 or e - i - 2 > 120:
                continue
            nm = d[i + 2:e]
            if not nm or not all(ch in SP.NAME_CHARS for ch in nm):
                continue
            body = i + 1 - 136
            if body >= 0:
                self.refsets.setdefault(nm.decode('latin-1'),
                                        d[body:e + 1])

    def _walk_technique(self, t):
        d = self.d
        pc = self.u16(t + 6)
        c = SP.Cur(d, t + 8 + pc * 24)
        for i in range(pc):
            po = t + 8 + i * 24
            vd, vs, ps = self.u32(po), self.u32(po + 4), self.u32(po + 8)
            args_p = self.u32(po + 20)
            nargs = d[po + 12] + d[po + 13] + d[po + 14]
            if vs == FOLLOW:
                self._rec_sref(c, 'vs')
            if vd == FOLLOW:
                self.decl_offs.append(c.o)
                c.skip(SP.VD_SIZE)
            if ps == FOLLOW:
                self._rec_sref(c, 'ps')
            if args_p == FOLLOW:
                base = c.o
                lits = 0
                for j in range(nargs):
                    atype = self.u16(base + j * 8)
                    if self.u32(base + j * 8 + 4) == FOLLOW and atype in (1, 4):
                        lits += 1
                c.skip(nargs * 8 + lits * 16)
        if self.u32(t) == FOLLOW:
            nm = SP.name_run(d, c.o, minlen=1)
            c.skip(len(nm.encode()) + 1)
        return c.o

    def _rec_sref(self, c, kind):
        """Record an inline shader ref (and its inline GX2 body) then parse
        past it, advancing c."""
        o = c.o
        self.sref_offs.append(o)
        self.sref_kind[o] = kind
        name_p, ld_p = self.u32(o), self.u32(o + 4)
        pos = o + 12
        if name_p == FOLLOW:
            pos = self.d.index(b'\x00', pos) + 1
        if ld_p == FOLLOW:
            self.gx2_offs[kind].append(pos)
        SP.parse_shader_ref(c, kind)

    # ---------------- delta fit ------------------------------------------
    @staticmethod
    def dec(v):
        """alias value -> (block, offset)"""
        return (v - 1) >> 29, (v - 1) & 0x1FFFFFFF

    # delta drift bounds between two virtual points `gap` apart:
    #   virtual runs AHEAD of file via alloc-alignment bumps (delta decreases,
    #   observed ~0.2% of virtual span), file runs ahead via interleaved TEMP
    #   bytes / stream padding (delta increases, bounded small).
    @staticmethod
    def _dn(gap):
        return int(gap * 0.03) + 0x800     # max delta DECREASE over gap

    @staticmethod
    def _up(gap):
        return 0x2000                       # max delta INCREASE over gap

    def _solve_aliases(self):
        """Solve every block-5 vd/slot/vs/ps alias target to its catalogued
        file offset by GLOBAL monotone assignment: the virtual->file map is
        strictly increasing and delta = file - virtual drifts slowly
        (alignment bumps shrink it ~0.2%/span, TEMP/padding grows it a
        little). DP over the sorted distinct targets picks, per site, a
        candidate object of its kind so file offsets strictly increase and
        total delta-drift is minimal — globally consistent, no greedy
        poisoning."""
        import bisect
        cat = {'vd': sorted(set(self.decl_offs)),
               'slot': sorted(set(self.tech_offs)),
               'vs': sorted(o for o in set(self.sref_offs)
                            if self.sref_kind[o] == 'vs'),
               'ps': sorted(o for o in set(self.sref_offs)
                            if self.sref_kind[o] == 'ps'),
               'ldvs': sorted(set(self.gx2_offs['vs'])),
               'ldps': sorted(set(self.gx2_offs['ps']))}
        sites = {}                        # boff -> kind (vs/ps share targets)
        for nm, (o, end) in self.techsets.items():
            for off, kind in self._alias_sites(o):
                if kind not in cat:
                    continue
                blk, boff = self.dec(self.u32(off))
                if blk == 5:
                    sites.setdefault(boff, kind)
        # aliased loadDef pointers inside catalogued shader-ref bodies point
        # at earlier inline GX2 bodies — solve them in the same map
        for so in self.sref_offs:
            ld = self.u32(so + 4)
            if ld in (0, FOLLOW):
                continue
            blk, boff = self.dec(ld)
            if blk == 5:
                sites.setdefault(boff, 'ld' + self.sref_kind[so])
        order = sorted(sites)
        if not order:
            self.amap, self.anchors = {}, []
            return
        # coarse global delta band (delta shrinks with file span)
        band_lo = -(len(self.d) >> 4)     # ~6% of file size
        band_hi = 0x10000
        cand = []
        kept = []
        for v in order:
            arr = cat[sites[v]]
            i0 = bisect.bisect_left(arr, v + band_lo)
            i1 = bisect.bisect_right(arr, v + band_hi)
            c = arr[i0:i1]
            if not c:
                # no catalogued target near this virtual offset (origin object
                # outside the techset corpus) — leave to per-site fallback
                print('  skip unsolvable alias v=%#x (%s)' % (v, sites[v]))
                continue
            kept.append(v)
            cand.append(c)
        order = kept
        BIG = float('inf')
        # dp[j] = min total drift cost ending with cand[i][j]. The ONLY hard
        # constraint is strict file monotonicity; drift is pure cost, so a
        # locally sharp delta change can't make the chain infeasible.
        dp = [0.0] * len(cand[0])
        back = [None]
        prev = 0                          # index of last non-dropped site
        chain = [0]                       # non-dropped site indices in order
        for i in range(1, len(order)):
            prev_f = cand[prev]
            prev_d = [f - order[prev] for f in prev_f]
            # prefix-min over prev candidates (sorted by file offset) makes
            # the transition O(n+m) via a running best
            ndp = [BIG] * len(cand[i])
            nbk = [-1] * len(cand[i])
            gap = order[i] - order[prev]
            dn = self._dn(gap)
            up = self._up(gap)
            for j, f in enumerate(cand[i]):
                dj = f - order[i]
                best = BIG
                bk = -1
                for k, fk in enumerate(prev_f):
                    if fk >= f:
                        break
                    if dp[k] == BIG:
                        continue
                    drift = dj - prev_d[k]
                    cost = dp[k] + abs(drift)
                    # drift beyond the physically plausible bounds (alignment
                    # bumps down / TEMP+padding up) is a soft violation
                    if drift < -dn:
                        cost += (-dn - drift) * 64
                    elif drift > up:
                        cost += (drift - up) * 64
                    if cost < best:
                        best = cost
                        bk = k
                ndp[j] = best
                nbk[j] = bk
            if all(x == BIG for x in ndp):
                # no monotone transition — drop this site (per-site fallback
                # resolves it later) and keep the chain state
                print('  drop chain-infeasible alias v=%#x (%s)'
                      % (order[i], sites[order[i]]))
                continue
            dp = ndp
            back.append(nbk)
            prev = i
            chain.append(i)
        # backtrack best path along the surviving chain
        j = min(range(len(dp)), key=lambda x: dp[x])
        picks = {}
        for ci in range(len(chain) - 1, -1, -1):
            i = chain[ci]
            picks[i] = j
            if ci > 0:
                j = back[ci][j]
        solved = [(order[i], cand[i][picks[i]]) for i in sorted(picks)]
        self.amap = dict(solved)
        self.anchors = solved
        print('alias map: %d block-5 targets solved (DP), delta %+#x..%+#x'
              % (len(solved), solved[0][1] - solved[0][0],
                 solved[-1][1] - solved[-1][0]))

    def _alias_sites(self, o):
        """(file offset of pointer word, kind) for every alias inside the
        techset at o. kinds: slot, vd, vs, ps, args, lit."""
        d = self.d
        out = []
        slots = [self.u32(o + 8 + i * 4) for i in range(32)]
        c = SP.Cur(d, o + 136)
        c.cstr(160)
        for si, v in enumerate(slots):
            if v not in (0, FOLLOW):
                out.append((o + 8 + si * 4, 'slot'))
            if v != FOLLOW:
                continue
            t = c.o
            pc = self.u16(t + 6)
            cc = SP.Cur(d, t + 8 + pc * 24)
            for i in range(pc):
                po = t + 8 + i * 24
                vd, vs, ps = self.u32(po), self.u32(po + 4), self.u32(po + 8)
                args_p = self.u32(po + 20)
                nargs = d[po + 12] + d[po + 13] + d[po + 14]
                if vd not in (0, FOLLOW):
                    out.append((po, 'vd'))
                if vs not in (0, FOLLOW):
                    out.append((po + 4, 'vs'))
                if ps not in (0, FOLLOW):
                    out.append((po + 8, 'ps'))
                if args_p not in (0, FOLLOW):
                    out.append((po + 20, 'args'))
                if vs == FOLLOW:
                    SP.parse_shader_ref(cc, 'vs')
                if vd == FOLLOW:
                    cc.skip(SP.VD_SIZE)
                if ps == FOLLOW:
                    SP.parse_shader_ref(cc, 'ps')
                if args_p == FOLLOW:
                    base = cc.o
                    lits = 0
                    for j in range(nargs):
                        atype = self.u16(base + j * 8)
                        up = self.u32(base + j * 8 + 4)
                        if up not in (0, FOLLOW) and atype in (1, 4):
                            out.append((base + j * 8 + 4, 'lit'))
                        if up == FOLLOW and atype in (1, 4):
                            lits += 1
                    cc.skip(nargs * 8 + lits * 16)
            c.o = self._walk_technique(t)
        return out

    def _interp(self, boff):
        """(low, high) plausible file range for virtual offset boff via the
        solved anchor map (bracketed interpolation + drift slack)."""
        import bisect
        i = bisect.bisect_left(self.anchors, (boff,))
        ests = []
        if i > 0:
            vl, fl = self.anchors[i - 1]
            ests.append(boff + (fl - vl))
        if i < len(self.anchors):
            vr, fr = self.anchors[i]
            ests.append(boff + (fr - vr))
        if not ests:
            return 40, len(self.d)
        # delta shifts monotonically between the bracketing anchors, so the
        # target sits between the two estimates (plus small local slack)
        return (max(40, min(ests) - 0x800),
                min(len(self.d), max(ests) + 0x800))

    def resolve(self, v, validate=None):
        """alias value -> file offset of the target object."""
        blk, boff = self.dec(v)
        if blk != 5:
            raise RuntimeError('alias %#x: unexpected block %d' % (v, blk))
        f = self.amap.get(boff)
        if f is not None:
            return f
        lo, hi = self._interp(boff)
        if validate is None:
            raise RuntimeError('alias %#x: unsolved and no validator' % v)
        hits = [f for f in range(lo, hi + 1) if validate(f)]
        if not hits:
            raise RuntimeError('alias %#x: no target in %#x..%#x'
                               % (v, lo, hi))
        return hits[0]

    def resolve_str(self, v):
        """alias to a NUL-terminated name string -> the string. Search the
        interpolated file range outward from its midpoint for a string start
        (byte before is NUL, chars are name-like)."""
        blk, boff = self.dec(v)
        ilo, ihi = self._interp(boff)
        for widen in (0x200, 0x1000, 0x8000, 0x40000):
            lo = max(ilo - widen, 1)
            hi = min(ihi + widen, len(self.d) - 1)
            mid = (lo + hi) // 2
            for r in range(0, max(mid - lo, hi - mid) + 1):
                for f in (mid - r, mid + r) if r else (mid,):
                    if f < lo or f > hi:
                        continue
                    if self.d[f] not in SP.NAME_CHARS:
                        continue
                    # we may be inside the string: back up to its start
                    s = f
                    while s > 0 and self.d[s - 1] in SP.NAME_CHARS:
                        s -= 1
                    nm = SP.name_run(self.d, s, minlen=3)
                    if nm:
                        return nm
        raise RuntimeError('str alias %#x: no string in %#x..%#x'
                           % (v, ilo, ihi))

    # ---------------- alias-target validators -----------------------------
    def _gx2_ok(self, f):
        """Plausible inline GX2 shader struct start (vs or ps)."""
        if f + SP.VS_GX2_SIZE > len(self.d):
            return False
        for regs in (SP.VS_REGS, SP.PS_REGS):
            size = self.u32(f + regs)
            prog = self.u32(f + regs + 4)
            mode = self.u32(f + regs + 8)
            if prog == FOLLOW and 0 < size <= 0x20000 and mode == 0:
                return True
        return False

    def _table_ok(self, f, cnt, rsz, noff):
        """Plausible count/ptr table start: each record's name ptr valid."""
        if f + cnt * rsz > len(self.d):
            return False
        if noff is None:
            return True
        for i in range(cnt):
            np = self.u32(f + i * rsz + noff)
            if np == 0 or not SP.ptr_ok(np):
                return False
        return True

    def _args_ok(self, f, nargs):
        """Plausible MaterialShaderArgument array (8B recs, sane types)."""
        if f + nargs * 8 > len(self.d):
            return False
        return all(self.u16(f + j * 8) < 0x20 for j in range(nargs))

    # ---------------- self-contained re-serialization --------------------
    def emit_techset(self, name):
        if name in self.techsets:
            o, end = self.techsets[name]
            d = self.d
            out = bytearray()
            slots = [self.u32(o + 8 + i * 4) for i in range(32)]
            out += be32(FOLLOW)                      # name: follows
            out += d[o + 4:o + 8]                    # worldVertFormat etc.
            for v in slots:
                out += be32(FOLLOW if v else 0)
            nm = SP.name_run(d, o + 136, minlen=1)
            out += nm.encode('latin-1') + b'\x00'
            c = SP.Cur(d, o + 136)
            c.cstr(160)
            for v in slots:
                if v == 0:
                    continue
                if v == FOLLOW:
                    t = c.o
                    out += self.emit_technique(t)
                    c.o = self._walk_technique(t)
                else:
                    out += self.emit_technique(self.resolve(v, validate=lambda f: f in self._tech_set))
            return bytes(out)
        if name in self.refsets:
            return self.refsets[name]
        return None

    def emit_technique(self, t):
        d = self.d
        pc = self.u16(t + 6)
        name_p = self.u32(t)
        hdr = bytearray()
        hdr += be32(FOLLOW)                          # name (written last)
        hdr += d[t + 4:t + 8]                        # flags, passCount
        body = bytearray()
        dyn = bytearray()
        c = SP.Cur(d, t + 8 + pc * 24)
        for i in range(pc):
            po = t + 8 + i * 24
            vd, vs, ps = self.u32(po), self.u32(po + 4), self.u32(po + 8)
            args_p = self.u32(po + 20)
            nargs = d[po + 12] + d[po + 13] + d[po + 14]
            pb = bytearray(d[po:po + 24])
            # pointer slots -> FOLLOW where any data exists
            struct.pack_into('>I', pb, 0, FOLLOW if vd else 0)
            struct.pack_into('>I', pb, 4, FOLLOW if vs else 0)
            struct.pack_into('>I', pb, 8, FOLLOW if ps else 0)
            struct.pack_into('>I', pb, 20, FOLLOW if args_p else 0)
            body += pb
            # dynamics in parse order: vs ref, vd, ps ref, args
            if vs:
                src = c if vs == FOLLOW else SP.Cur(d, self.resolve(vs, validate=lambda f: f in self._sref_kset['vs']))
                dyn += self.emit_shader_ref(src, 'vs')
                if vs == FOLLOW:
                    pass                              # c advanced by emit
            if vd:
                src_off = c.o if vd == FOLLOW else self.resolve(vd, validate=lambda f: f in self._decl_set)
                dyn += d[src_off:src_off + SP.VD_SIZE]
                if vd == FOLLOW:
                    c.skip(SP.VD_SIZE)
            if ps:
                src = c if ps == FOLLOW else SP.Cur(d, self.resolve(ps, validate=lambda f: f in self._sref_kset['ps']))
                dyn += self.emit_shader_ref(src, 'ps')
            if args_p:
                base = c.o if args_p == FOLLOW else self.resolve(
                    args_p, validate=lambda f: self._args_ok(f, nargs))
                # arg words are copied VERBATIM: console type-1/4 u-values are
                # small index-like values (0x2..0x12, misaligned), NOT literal
                # pointers — genuine zones never carry FOLLOW inline literals.
                arr = bytearray(d[base:base + nargs * 8])
                lits = bytearray()
                inline_after = base + nargs * 8
                for j in range(nargs):
                    atype = self.u16(base + j * 8)
                    up = self.u32(base + j * 8 + 4)
                    if atype in (1, 4) and up == FOLLOW:
                        lits += d[inline_after:inline_after + 16]
                        inline_after += 16
                dyn += arr + lits
                if args_p == FOLLOW:
                    c.o = inline_after
        # technique name last
        if name_p == FOLLOW:
            nm = SP.name_run(d, c.o, minlen=1)
        else:
            nm = self.resolve_str(name_p)
        dyn += nm.encode('latin-1') + b'\x00'
        return bytes(hdr + body + dyn)

    def emit_shader_ref(self, c, kind):
        """Re-serialize a 12-byte shader ref + subtree from cursor c
        (advancing c when it is the live stream cursor)."""
        d = self.d
        o = c.o
        name_p, ld_p, w3 = self.u32(o), self.u32(o + 4), self.u32(o + 8)
        out = bytearray()
        out += be32(FOLLOW if name_p else 0)
        out += be32(FOLLOW if ld_p else 0)
        out += be32(w3)
        c.skip(12)
        if name_p:
            if name_p == FOLLOW:
                nm = c.cstr(96)
            else:
                nm = self.resolve_str(name_p)
            out += nm.encode('latin-1') + b'\x00'
        if ld_p:
            if ld_p == FOLLOW:
                out += self.emit_gx2(c, kind)
            else:
                out += self.emit_gx2(SP.Cur(d, self.resolve(
                    ld_p, validate=self._gx2_ok)), kind)
        return bytes(out)

    def emit_gx2(self, c, kind):
        """Copy a GX2 shader struct + microcode + tables, inlining any alias
        record-name pointers. kind ('vs'/'ps') comes from the pass slot —
        sniffing it from the struct is unreliable (VS sq_vtx_semantic bytes
        are 0xFF and mimic a FOLLOW at the PS programSize slot)."""
        d = self.d
        o = c.o
        if kind == 'ps':
            regs, total, tables = SP.PS_REGS, SP.PS_GX2_SIZE, SP.PS_TABLES
        else:
            regs, total, tables = SP.VS_REGS, SP.VS_GX2_SIZE, SP.VS_TABLES
        size = self.u32(o + regs)
        counts = [(self.u32(o + regs + 12 + i * 8), self.u32(o + regs + 16 + i * 8))
                  for i in range(len(tables))]
        # decide, per table, whether an alias ptr is a REAL shared block-5
        # table (inline it) or linker memory garbage (counts >64, pointers
        # into runtime blocks — the loader ignores these; copy verbatim)
        inline = []
        for cnt, ptr in counts:
            if ptr in (0, FOLLOW):
                inline.append(False)
                continue
            blk = (ptr - 1) >> 29
            inline.append(blk == 5 and 0 < cnt <= 64)
        hdr = bytearray(d[o:o + total])
        for i, (cnt, ptr) in enumerate(counts):
            if inline[i]:
                struct.pack_into('>I', hdr, regs + 16 + i * 8, FOLLOW)
        out = bytearray(hdr)
        c.skip(total)
        out += d[c.o:c.o + size]
        c.skip(size)
        for ti, ((cnt, ptr), (rsz, noff)) in enumerate(zip(counts, tables)):
            if ptr == 0 or (ptr != FOLLOW and not inline[ti]):
                continue
            if ptr == FOLLOW:
                base = c.o
                consumed_names = []
                if noff is not None:
                    for i in range(cnt):
                        if self.u32(base + i * rsz + noff) == FOLLOW:
                            consumed_names.append(i)
                c.skip(cnt * rsz)
                names = []
                for _ in consumed_names:
                    names.append(c.cstr(96))
                out += self._emit_table(d[base:base + cnt * rsz], cnt, rsz,
                                        noff, names)
            else:
                try:
                    base = self.resolve(ptr, validate=lambda f: self._table_ok(
                        f, cnt, rsz, noff))
                    # records whose name ptr is FOLLOW keep their chars right
                    # after the ORIGINAL table in file order — read them there
                    names = []
                    if noff is not None:
                        sc = SP.Cur(d, base + cnt * rsz)
                        for i in range(cnt):
                            if self.u32(base + i * rsz + noff) == FOLLOW:
                                names.append(sc.cstr(96))
                    out += self._emit_table(d[base:base + cnt * rsz], cnt,
                                            rsz, noff, names)
                except (RuntimeError, SP.Fail, ValueError):
                    # unresolvable share: restore the original word verbatim
                    struct.pack_into('>I', out, regs + 16 + ti * 8, ptr)
        return bytes(out)

    def _emit_table(self, raw, cnt, rsz, noff, follow_names):
        """Emit a count/ptr table with record-name aliases inlined."""
        arr = bytearray(raw)
        names = bytearray()
        fi = 0
        for i in range(cnt):
            if noff is None:
                continue
            np = self.u32_of(arr, i * rsz + noff)
            if np == FOLLOW:
                nm = follow_names[fi] if follow_names else None
                fi += 1
                if nm is None:
                    raise RuntimeError('aliased table with FOLLOW names')
                names += nm.encode('latin-1') + b'\x00'
            elif np != 0:
                nm = self.resolve_str(np)
                struct.pack_into('>I', arr, i * rsz + noff, FOLLOW)
                names += nm.encode('latin-1') + b'\x00'
        return bytes(arr + names)

    @staticmethod
    def u32_of(buf, o):
        return struct.unpack('>I', bytes(buf[o:o + 4]))[0]


def sim_loader(blob, phase, trace=None):
    """Simulate the console loader's VIRTUAL-block cursor consuming this
    self-contained techset blob's TAIL (blob[136:] = name + techniques...).
    The loader allocates every object 4-aligned (verified: every solved
    genuine anchor is mod-4 == 0) and strings unaligned. Returns the total
    VIRTUAL bytes consumed for an entry cursor phase (cursor % 4 == phase).
    If trace is a dict, records blob_offset -> cursor for technique/sref/
    decl/gx2 object starts (used to validate against genuine anchors)."""
    d = blob
    va = phase

    def obj(c, size, align=4):
        """Aligned object of `size` bytes at stream cursor c. Objects align
        to 4; GX2 shader microcode aligns to 0x100 (GX2_SHADER_ALIGNMENT) -
        confirmed against genuine anchors."""
        nonlocal va
        va = (va + align - 1) & ~(align - 1)
        if trace is not None:
            trace[c.o] = va
        va += size
        c.skip(size)

    def cstr(c):
        nonlocal va
        s = c.cstr(256)
        va += len(s.encode('latin-1')) + 1
        return s

    slots = [struct.unpack('>I', d[8 + i * 4:12 + i * 4])[0] for i in range(32)]
    c = SP.Cur(d, 136)
    cstr(c)                                   # techset name
    for v in slots:
        if v != FOLLOW:
            continue
        t = c.o
        pc = struct.unpack('>H', d[t + 6:t + 8])[0]
        name_p = struct.unpack('>I', d[t:t + 4])[0]
        obj(c, 8 + pc * 24)                   # technique body + passes
        for i in range(pc):
            po = t + 8 + i * 24
            vd, vs, ps = (struct.unpack('>I', d[po + j:po + j + 4])[0]
                          for j in (0, 4, 8))
            args_p = struct.unpack('>I', d[po + 20:po + 24])[0]
            nargs = d[po + 12] + d[po + 13] + d[po + 14]
            for kind, ptr in (('vs', vs), (None, vd), ('ps', ps)):
                if kind is None:              # vertex decl
                    if ptr == FOLLOW:
                        obj(c, SP.VD_SIZE)
                    continue
                if ptr != FOLLOW:
                    continue
                so = c.o
                snm = struct.unpack('>I', d[so:so + 4])[0]
                sld = struct.unpack('>I', d[so + 4:so + 8])[0]
                obj(c, 12)                    # shader ref
                if snm == FOLLOW:
                    cstr(c)
                if sld == FOLLOW:
                    regs, total, tables = ((SP.VS_REGS, SP.VS_GX2_SIZE, SP.VS_TABLES)
                                           if kind == 'vs' else
                                           (SP.PS_REGS, SP.PS_GX2_SIZE, SP.PS_TABLES))
                    g = c.o
                    size = struct.unpack('>I', d[g + regs:g + regs + 4])[0]
                    counts = [(struct.unpack('>I', d[g + regs + 12 + i2 * 8:g + regs + 16 + i2 * 8])[0],
                               struct.unpack('>I', d[g + regs + 16 + i2 * 8:g + regs + 20 + i2 * 8])[0])
                              for i2 in range(len(tables))]
                    obj(c, total)             # gx2 struct
                    obj(c, size, align=0x100)  # microcode
                    for (cnt, ptr2), (rsz, noff) in zip(counts, tables):
                        if ptr2 != FOLLOW:
                            continue
                        base = c.o
                        nfollow = 0
                        if noff is not None:
                            nfollow = sum(1 for i3 in range(cnt)
                                          if struct.unpack('>I', d[base + i3 * rsz + noff:base + i3 * rsz + noff + 4])[0] == FOLLOW)
                        obj(c, cnt * rsz)     # table records
                        for _ in range(nfollow):
                            cstr(c)
                if False:
                    pass
            if args_p == FOLLOW:
                lits = 0
                base = c.o
                for j in range(nargs):
                    atype = struct.unpack('>H', d[base + j * 8:base + j * 8 + 2])[0]
                    up = struct.unpack('>I', d[base + j * 8 + 4:base + j * 8 + 8])[0]
                    if up == FOLLOW and atype in (1, 4):
                        lits += 1
                obj(c, nargs * 8)             # args array
                for _ in range(lits):
                    obj(c, 16)                # literal vec4
        if name_p == FOLLOW:
            cstr(c)                           # technique name (written last)
    return va - phase, c.o


def selfcheck(blob):
    """Re-parse an emitted techset blob; must consume exactly len(blob) and
    contain no alias pointers at any site."""
    end, ntech = SP.parse_techset(blob, 0)
    if end != len(blob):
        raise RuntimeError('selfcheck: consumed %d of %d' % (end, len(blob)))
    return ntech


def main():
    zone_path = sys.argv[1]
    out_dir = sys.argv[2]
    names = None
    if len(sys.argv) > 3:
        names = [l.strip().lstrip(',') for l in open(sys.argv[3])
                 if l.strip()]
    d = open(zone_path, 'rb').read()
    ex = Extractor(d)
    print('catalogued %d inline techsets, %d refs' %
          (len(ex.techsets), len(ex.refsets)))
    os.makedirs(out_dir, exist_ok=True)
    want = names if names is not None else sorted(ex.techsets)
    ok = ref = fail = 0
    reasons = Counter()
    for nm in want:
        try:
            blob = ex.emit_techset(nm)
        except Exception as e:  # noqa: BLE001
            reasons[type(e).__name__ + ': ' + str(e)[:40]] += 1
            fail += 1
            continue
        if blob is None:
            reasons['missing'] += 1
            fail += 1
            continue
        if nm in ex.techsets:
            try:
                selfcheck(blob)
            except Exception as e:  # noqa: BLE001
                reasons['selfcheck: ' + str(e)[:40]] += 1
                fail += 1
                continue
            ok += 1
        else:
            ref += 1
        safe = nm.replace('/', '__')
        with open(os.path.join(out_dir, safe + '.techset'), 'wb') as f:
            f.write(blob)
    print('emitted %d self-contained + %d refs, %d failed' % (ok, ref, fail))
    for r, c in reasons.most_common(10):
        print('   fail x%d: %s' % (c, r))


if __name__ == '__main__':
    main()
