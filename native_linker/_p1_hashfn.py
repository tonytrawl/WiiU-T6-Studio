#!/usr/bin/env python3
"""PHASE 1 step 1: CONFIRM the T6 shader-arg name hash function from ground truth
already present in the zone.

Console Material constantTable entry = 32B { u32 nameHash, char name[12], vec4 literal }.
That is a name->hash oracle sitting in the file. Harvest it at BYTE granularity, then
ask which candidate hash fn reproduces the pairs. No assumption about the fn up front.
"""
import struct, sys, re
from collections import Counter, defaultdict

be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]

IDENT = re.compile(rb'^[A-Za-z_][A-Za-z0-9_]*$')


def harvest_constdefs(Z, limit=None):
    """byte-granularity scan for {u32 hash, char name[12], vec4}. Returns [(hash, name)]."""
    n = len(Z)
    out = []
    seen = set()
    i = 0
    while i + 32 <= n:
        # name field = Z[i+4:i+16]
        blk = Z[i + 4:i + 16]
        z = blk.find(b'\x00')
        if z <= 0:
            i += 1
            continue
        nm = blk[:z]
        if len(nm) < 3 or not IDENT.match(nm):
            i += 1
            continue
        # rest of the 12 must be NUL padding
        if blk[z:] != b'\x00' * (12 - z):
            i += 1
            continue
        h = be32(Z, i)
        if h == 0 or h == 0xFFFFFFFF:
            i += 1
            continue
        key = (h, nm)
        if key not in seen:
            seen.add(key)
            out.append((h, nm.decode('latin1'), i))
        i += 1
        if limit and len(out) >= limit:
            break
    return out


def candidates():
    out = {}

    def mk(mult, init, low, sub=False):
        def h(s):
            v = init
            for ch in s:
                c = ord(ch.lower()) if low else ord(ch)
                if sub:
                    v = (v * mult - c) & 0xFFFFFFFF
                else:
                    v = (v * mult + c) & 0xFFFFFFFF
            return v
        return h

    for mult in (31, 33, 37, 131, 65599, 16777619):
        for init in (0, 5381, 0x1505, 2166136261):
            for low in (False, True):
                for sub in (False, True):
                    out['mul%d_i%d%s%s' % (mult, init, '_lc' if low else '',
                                           '_sub' if sub else '')] = mk(mult, init, low, sub)

    def fnv1a(s, low=False):
        v = 2166136261
        for ch in s:
            c = ord(ch.lower()) if low else ord(ch)
            v = ((v ^ c) * 16777619) & 0xFFFFFFFF
        return v
    out['fnv1a'] = fnv1a
    out['fnv1a_lc'] = lambda s: fnv1a(s, True)

    def fnv1(s, low=False):
        v = 2166136261
        for ch in s:
            c = ord(ch.lower()) if low else ord(ch)
            v = ((v * 16777619) ^ c) & 0xFFFFFFFF
        return v
    out['fnv1'] = fnv1
    out['fnv1_lc'] = lambda s: fnv1(s, True)

    def sdbm(s, low=False):
        v = 0
        for ch in s:
            c = ord(ch.lower()) if low else ord(ch)
            v = (c + (v << 6) + (v << 16) - v) & 0xFFFFFFFF
        return v
    out['sdbm'] = sdbm
    out['sdbm_lc'] = lambda s: sdbm(s, True)

    # IW/T6 R_HashString variant: h = h*33 ^ c  (a.k.a. djb2-xor)
    def djb2x(s, init, low=False):
        v = init
        for ch in s:
            c = ord(ch.lower()) if low else ord(ch)
            v = ((v * 33) ^ c) & 0xFFFFFFFF
        return v
    for init in (0, 5381):
        out['djb2xor_i%d' % init] = (lambda i: (lambda s: djb2x(s, i)))(init)
        out['djb2xor_i%d_lc' % init] = (lambda i: (lambda s: djb2x(s, i, True)))(init)
    return out


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'mp_skate_final.zone'
    Z = open(path, 'rb').read()
    print('zone %s  %d bytes' % (path, len(Z)))
    pairs = harvest_constdefs(Z)
    print('harvested candidate constdef (hash,name) pairs: %d' % len(pairs))
    nm_counts = Counter(p[1] for p in pairs)
    print('distinct names: %d   top: %s' % (len(nm_counts), nm_counts.most_common(15)))

    fns = candidates()
    best = []
    for k, f in fns.items():
        ok = 0
        for (h, nm, off) in pairs:
            try:
                if f(nm) == h:
                    ok += 1
            except Exception:
                pass
        if ok:
            best.append((ok, k))
    best.sort(reverse=True)
    print('\ncandidate hash fns matching harvested pairs (top 10):')
    for ok, k in best[:10]:
        print('   %-28s %6d / %d   (%.1f%%)' % (k, ok, len(pairs), 100.0 * ok / len(pairs)))
    if not best:
        print('   NONE matched.')
