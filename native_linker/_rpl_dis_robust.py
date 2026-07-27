#!/usr/bin/env python3
"""Robust PPC disassembler over the deployed RPL, tolerant of capstone gaps."""
import struct
import sys
import zlib
import os

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capstone import CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN, Cs
from rpl_symbolize import DEFAULT_RPL, RELOC, Symbolizer


class Code:
    def __init__(self, rpl_path=DEFAULT_RPL, reloc=RELOC):
        self.reloc = reloc
        d = open(rpl_path, 'rb').read()
        assert d[:4] == b'\x7fELF'
        e_shoff = struct.unpack_from('>I', d, 0x20)[0]
        e_shnum = struct.unpack_from('>H', d, 0x30)[0]
        e_shent = struct.unpack_from('>H', d, 0x2e)[0]
        self.secs = []
        for i in range(e_shnum):
            nm, typ, flags, addr, off, size, link, info, align, ent = struct.unpack_from(
                '>10I', d, e_shoff + i * e_shent)
            if typ != 1 or not addr:
                continue
            raw = d[off:off + size]
            if flags & 0x08000000:
                raw = zlib.decompress(raw[4:])
            self.secs.append((addr, len(raw), raw, flags))
        self.secs.sort()
        self.md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
        self.sz = Symbolizer(rpl_path, reloc)

    def read(self, guest, n):
        v = guest - self.reloc
        for (a, z, raw, fl) in self.secs:
            if a <= v < a + z:
                return raw[v - a:v - a + n]
        return None

    def lines(self, guest, nbytes):
        """Yield (addr, mnem, ops, raw_word). Skips 4 bytes on capstone failure."""
        data = self.read(guest, nbytes)
        out = []
        off = 0
        while off < len(data):
            got = False
            for ins in self.md.disasm(data[off:], guest + off):
                out.append((ins.address, ins.mnemonic, ins.op_str,
                            struct.unpack_from('>I', data, ins.address - guest)[0]))
                off = ins.address - guest + ins.size
                got = True
            if not got:
                w = struct.unpack_from('>I', data, off)[0]
                out.append((guest + off, '.long', '0x%08x' % w, w))
                off += 4
            elif off < len(data):
                # capstone stopped mid-stream on a bad opcode
                w = struct.unpack_from('>I', data, off)[0]
                out.append((guest + off, '.long', '0x%08x' % w, w))
                off += 4
        return out

    def dump(self, guest, nbytes, fh=sys.stdout):
        base = guest
        for (a, m, o, w) in self.lines(guest, nbytes):
            tgt = ''
            if m.startswith('b') and '0x' in o:
                try:
                    t = int(o.split(',')[-1].strip(), 16)
                    if 0x02000000 <= t < 0x02b60000:
                        tgt = '   ; ' + self.sz.sym(t)
                except ValueError:
                    pass
            fh.write('  +%-5x 0x%08x  %-10s %s%s\n' % (a - base, a, m, o, tgt))


if __name__ == '__main__':
    c = Code()
    g = int(sys.argv[1], 16)
    n = int(sys.argv[2])
    outp = sys.argv[3] if len(sys.argv) > 3 else None
    if outp:
        with open(outp, 'w') as f:
            c.dump(g, n, f)
        print('wrote', outp)
    else:
        c.dump(g, n)
