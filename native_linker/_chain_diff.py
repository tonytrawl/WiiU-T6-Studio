#!/usr/bin/env python3
"""Attribute the gfxtail patch stack: diff consecutive zones along the REAL chain
(per each patch script's SRC), report changed ranges per step. Needed to decide
which patches must be re-applied on the interior-anchored rebake (content/binding)
vs which the fixed rtmap now produces natively (alias families)."""
import sys

CHAIN = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7),          # unknown steps
         (7, 8), (8, 9), (9, 10), (10, 11),                        # alias families
         (11, 13), (13, 14), (14, 16), (16, 17), (17, 18),         # dynent+matconst
         (18, 19), (19, 20), (20, 21), (21, 22)]                   # binding fixes


def ranges(a, b):
    """grouped [start,end) differing ranges (gap-merge 4)"""
    out = []
    i, n = 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i + 1
            while j < n and (a[j] != b[j] or (j + 4 <= n and a[j:j + 4] != b[j:j + 4])):
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


cache = {}


def load(n):
    if n not in cache:
        cache.clear()   # keep at most 1 old + current
        cache[n] = open('mp_skate_gfxtail%d.zone' % n, 'rb').read()
    return cache[n]


prev = load(CHAIN[0][0])
prev_n = CHAIN[0][0]
for (x, y) in CHAIN:
    a = prev if x == prev_n else open('mp_skate_gfxtail%d.zone' % x, 'rb').read()
    b = open('mp_skate_gfxtail%d.zone' % y, 'rb').read()
    rs = ranges(a, b)
    tot = sum(e - s for s, e in rs)
    print('%2d -> %2d : %6d ranges, %8d bytes changed' % (x, y, len(rs), tot))
    if len(rs) <= 12:
        for s, e in rs:
            print('           [%9d..%9d) %4dB  %s -> %s'
                  % (s, e, e - s, a[s:min(e, s + 16)].hex(), b[s:min(e, s + 16)].hex()))
    prev, prev_n = b, y
