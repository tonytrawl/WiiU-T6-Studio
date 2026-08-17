#!/usr/bin/env python3
"""Dump a T6 StringTable asset (e.g. zm/mapstable.csv) as rows x cols, resolving
cells via the dedup model: FOLLOW cells store their string inline in a pool after
the cell array; alias cells reuse the inline string of the same hash."""
import sys, os, struct
sys.path.insert(0, r'C:\Users\Tony - Main Rig\Downloads\Testing enviroment\tools')
import ff_decrypt

FOLLOW = 0xFFFFFFFF


def load_zone(path):
    raw = open(path, 'rb').read()
    e, k, v, l = ff_decrypt.detect_platform(raw)
    _h, z, _n = ff_decrypt.decrypt_ff(raw, k, e)
    return z


def _decode_ptr(v):
    v = (v - 1) & 0xFFFFFFFF
    return (v >> 29), (v & 0x1FFFFFFF)


def dump_stringtable(z, body, le=True, base5=None):
    """body = offset of the StringTable struct (name* field). Returns (name, rows, cols, table)."""
    u = '<I' if le else '>I'
    pp = '<2I' if le else '>2I'
    cols = struct.unpack_from(u, z, body + 4)[0]
    rows = struct.unpack_from(u, z, body + 8)[0]
    n = rows * cols
    name_off = body + 20
    name_end = z.index(b'\x00', name_off)
    name = z[name_off:name_end].decode('latin1')
    cells0 = name_end + 1
    # inline strings (FOLLOW cells), in order, form the local pool
    byhash = {}
    o = cells0 + n * 8
    inline = {}
    for k in range(n):
        p, h = struct.unpack_from(pp, z, cells0 + k * 8)
        if p == FOLLOW:
            if h not in byhash:
                byhash[h] = o
            se = z.index(b'\x00', o)
            inline[k] = z[o:se].decode('latin1')
            o = se + 1
    # calibrate block-5 base: for a hash that is FOLLOW here (known file offset)
    # AND alias elsewhere, base = inline_offset - decode_offset(alias)
    if base5 is None:
        votes = {}
        for k in range(n):
            p, h = struct.unpack_from(pp, z, cells0 + k * 8)
            if 0xA0000001 <= p <= 0xBFFFFFFF and h in byhash:
                blk, off = _decode_ptr(p)
                if blk == 5:
                    b = byhash[h] - off
                    votes[b] = votes.get(b, 0) + 1
        base5 = max(votes, key=votes.get) if votes else None

    def rstr_off(off):
        se = z.index(b'\x00', off)
        return z[off:se].decode('latin1', 'replace')

    def cell(k):
        p, h = struct.unpack_from(pp, z, cells0 + k * 8)
        if p == 0:
            return ''
        if p == FOLLOW:
            return inline.get(k, '')
        if h in byhash:
            return rstr_off(byhash[h])
        if base5 is not None and 0xA0000001 <= p <= 0xBFFFFFFF:
            blk, off = _decode_ptr(p)
            if blk == 5 and 0 <= base5 + off < len(z):
                return rstr_off(base5 + off)
        return '<a:%08x>' % p
    table = [[cell(r * cols + c) for c in range(cols)] for r in range(rows)]
    return name, rows, cols, table, base5


if __name__ == '__main__':
    z = load_zone(r'E:\pluto_t6_full_game\zone\all\patch_zm.ff')
    name, rows, cols, table, base5 = dump_stringtable(z, 3428509, le=True)
    print('asset=%s  rows=%d cols=%d  base5=%s' % (name, rows, cols, base5))
    for r in range(rows):
        print('--- ROW %d ---' % r)
        for c in range(cols):
            v = table[r][c]
            if v:
                v = v.replace('\r\n', '\\n').replace('\t', '\\t')
                print('  [%2d] %s' % (c, v[:120]))
