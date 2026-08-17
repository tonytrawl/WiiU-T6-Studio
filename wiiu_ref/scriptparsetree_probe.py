#!/usr/bin/env python3
"""
SCRIPTPARSETREE console (Wii U v148) layout probe. Byte-exact.

FINDING: console ScriptParseTree is PC-IDENTICAL.
  body (12 bytes, all u32 big-endian on Wii U, little-endian on PC):
    +0  const char* name   (FOLLOW 0xFFFFFFFF -> name chars inline after body)
    +4  int len            (byte length of the compiled GSC buffer, NOT counting
                            the +1 trailing NUL byte that IS serialized)
    +8  byte* buffer       (FOLLOW -> len+1 bytes inline after the name)
  stream consumption per asset = 12 + strlen(name)+1 + (len + 1).
  The +1 comes from ZoneCode 'set count buffer len + 1' and holds on genuine
  bytes: buffer[len] == 0 and the next SPT body starts exactly at
  buffer_start + len + 1 for every consecutive pair in both zones.
  No alignment between assets. No dropped or added members. Alias never seen
  (every genuine SPT has both pointers FOLLOW).

Usage: python scriptparsetree_probe.py [zone ...]
  PC zones (little-endian) are auto-detected by the header block sizes.
"""
import struct, re, sys, os
from collections import Counter

FOLLOW = 0xFFFFFFFF
NAME_RE = re.compile(rb'[\w/.\-]+\.(gsc|csc|lua)$')
GSC_MAGIC = b'\x80GSC'


def detect_endian(d):
    """Zone header: u32 size at 0. BE zones have small first byte, PC (LE) has
    small last byte of that word. Use the internal size field vs file size."""
    be = struct.unpack('>I', d[0:4])[0]
    le = struct.unpack('<I', d[0:4])[0]
    return '>' if abs(be - len(d)) < abs(le - len(d)) else '<'


def find_spt(d, e):
    """All ScriptParseTree bodies: FOLLOW, len, FOLLOW, name(cstr matching a
    script path), buffer starting with 0x80 GSC and NUL-terminated at len."""
    out = []
    ff = b'\xff\xff\xff\xff'
    pos = 0
    while True:
        pos = d.find(ff, pos)
        if pos < 0:
            break
        b = pos
        pos += 1
        if d[b+8:b+12] != ff:
            continue
        ln = struct.unpack(e + 'I', d[b+4:b+8])[0]
        if not (8 <= ln < 0x400000):
            continue
        nul = d.find(b'\x00', b + 12, b + 12 + 128)
        if nul < 0:
            continue
        name = d[b+12:nul]
        if not NAME_RE.fullmatch(name):
            continue
        buf = nul + 1
        if not d[buf:buf+4] == GSC_MAGIC:
            continue
        out.append((b, name.decode(), ln, buf))
    return out


def parse_spt(d, b, e):
    """Parse one SPT at body offset b. Returns (end, name, buffer_bytes)."""
    assert d[b:b+4] == b'\xff'*4 and d[b+8:b+12] == b'\xff'*4
    ln = struct.unpack(e + 'I', d[b+4:b+8])[0]
    nul = d.index(b'\x00', b + 12)
    name = d[b+12:nul].decode()
    buf = nul + 1
    end = buf + ln + 1                    # 'count buffer len + 1'
    return end, name, d[buf:buf+ln]


def main():
    zones = sys.argv[1:] or ['mp_raid_genuine.zone', 'zm_transit_original.zone',
                             '../PC ff/mp_raid.zone']
    for zp in zones:
        d = open(zp, 'rb').read()
        e = detect_endian(d)
        spts = find_spt(d, e)
        starts = {b for b, *_ in spts}
        chained = loose = bad = 0
        for b, name, ln, buf in spts:
            end, _, body = parse_spt(d, b, e)
            if d[buf + ln] != 0:
                bad += 1
                print('  BAD trailing byte:', name)
                continue
            if end in starts:
                chained += 1                    # lands exactly on next SPT body
            else:
                # last SPT of a run: next asset body must start with a valid
                # marker word (FOLLOW/alias/0) -- weak check, still note it
                nx = struct.unpack(e + 'I', d[end:end+4])[0]
                loose += 1
                if not (nx == FOLLOW or nx == 0 or (nx & 0xE0000000)):
                    print('  suspicious end for', name, hex(end), hex(nx))
        print('%s [%s]: SPT=%d chained=%d run-ends=%d bad=%d' %
              (os.path.basename(zp), 'BE' if e == '>' else 'LE',
               len(spts), chained, loose, bad))
        for b, name, ln, buf in spts:
            print('    0x%08x len=%-7d %s' % (b, ln, name))


if __name__ == '__main__':
    main()
