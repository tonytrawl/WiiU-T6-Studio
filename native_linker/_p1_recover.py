#!/usr/bin/env python3
"""PHASE 1 step 2/3: recover the NAME STRINGS behind the missing + present hashes.

Hash fn CONFIRMED by _p1_hashfn.py (31/31 real T6 constant names in mp_skate_final.zone):
    h = 0 ; for c in name.lower(): h = (h*33) ^ c        [djb2-xor, init 0, lowercase]

Two independent recovery routes, both used as cross-checks:
  (A) DICTIONARY  - harvest every constdef name[12] and every ascii identifier from the
      PC source + console zones, hash them all, look the target up.
  (B) EXACT REVERSE - 33 is odd => invertible mod 2^32. h_prev = ((h ^ c) * inv33) mod 2^32.
      DFS backwards from the target; accept when h_prev == 0. Constrained by the texdef
      nameStart/nameEnd bytes when available.
"""
import struct, sys, re, itertools
from collections import defaultdict

be32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
INV33 = pow(33, -1, 1 << 32)
M = 0xFFFFFFFF


def H(s):
    v = 0
    for ch in s.lower():
        v = ((v * 33) ^ ord(ch)) & M
    return v


IDENT = re.compile(rb'[A-Za-z_][A-Za-z0-9_]{2,31}')
CONSTNAME = re.compile(rb'^[A-Za-z_][A-Za-z0-9_]*$')


def harvest_constdef_names(Z):
    """{name: hash} from 32B constdef {u32 hash, char name[12], vec4}, byte granularity."""
    out = {}
    n = len(Z)
    for i in range(n - 32):
        blk = Z[i + 4:i + 16]
        z = blk.find(b'\x00')
        if z <= 2 or blk[z:] != b'\x00' * (12 - z):
            continue
        nm = blk[:z]
        if not CONSTNAME.match(nm):
            continue
        h = be32(Z, i)
        if h in (0, 0xFFFFFFFF):
            continue
        s = nm.decode('latin1')
        if H(s) == h:
            out[s] = h
    return out


def harvest_idents(Z):
    return set(m.group(0).decode('latin1') for m in IDENT.finditer(Z))


# ---------------- exact reverse ----------------
CHARS = [ord(c) for c in 'abcdefghijklmnopqrstuvwxyz0123456789_']


def reverse(target, maxlen=24, first=None, last=None, prefixes=None):
    """all strings s (lowercase alphabet) with H(s)==target, len<=maxlen.
    prefixes: optional dict {hashvalue: name} of KNOWN prefix hashes for early accept."""
    res = []
    pref = prefixes or {}

    def dfs(h, tail, depth):
        if len(res) > 200:
            return
        if h == 0 and tail:
            if first is None or tail[0] == first:
                res.append(tail)
            return
        if depth >= maxlen:
            return
        if h in pref and (first is None or pref[h][0] == first):
            res.append(pref[h] + tail)
            # keep going too - other decompositions may exist
        for c in CHARS:
            hp = ((h ^ c) * INV33) & M
            dfs(hp, chr(c) + tail, depth + 1)

    # seed: last char constrained?
    if last is not None:
        c = ord(last)
        dfs(((target ^ c) * INV33) & M, last, 1)
    else:
        dfs(target, '', 0)
    return res


def reverse_by_prefix(target, prefix_hashes, maxsuffix=4):
    """find name = KNOWNPREFIX + suffix (suffix up to maxsuffix chars) hashing to target.
    prefix_hashes: {hash: name}. Also allows the empty suffix."""
    hits = []
    for L in range(0, maxsuffix + 1):
        for combo in itertools.product(CHARS, repeat=L):
            h = target
            # peel L chars off the end
            for c in reversed(combo):
                h = ((h ^ c) * INV33) & M
            if h in prefix_hashes:
                hits.append(prefix_hashes[h] + ''.join(chr(c) for c in combo))
            if h == 0 and L > 0:
                hits.append(''.join(chr(c) for c in combo))
    return sorted(set(hits))


def harvest_texdefs(Z):
    """{hash: set((nameStart,nameEnd))} from 16B texdef {u32 hash, u8 s, u8 e, u8 ss, u8 sem, ...}"""
    out = defaultdict(set)
    n = len(Z)
    for i in range(0, n - 16):
        h = be32(Z, i)
        a, b = Z[i + 4], Z[i + 5]
        if 0x20 <= a < 0x7f and 0x20 <= b < 0x7f:
            out[h].add((chr(a), chr(b)))
    return out
