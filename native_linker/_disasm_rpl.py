#!/usr/bin/env python3
"""
_disasm_rpl.py — disassemble GUEST code straight out of the DEPLOYED RPL.

The RPL is the same static code Cemu runs, so we don't need a 3.5GB dump to read
instructions (dumps are still needed for DATA / register state). Mapping is the one
rpl_symbolize.py already proves: guest_addr - 0x2000 = RPL vaddr.

Usage:
  python _disasm_rpl.py 0x02988ddc 120          # disassemble 120 instrs from a guest addr
  python _disasm_rpl.py --func 0x02988ddc       # disassemble until blr/end-of-function
Import:
  from _disasm_rpl import Code
  c = Code(); c.dis(0x02988ddc, 80)
"""
import struct
import sys
import zlib

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
            if typ != 1 or not addr:                 # SHT_PROGBITS with an address
                continue
            raw = d[off:off + size]
            if flags & 0x08000000:                   # SHF_RPL_ZLIB
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

    def dis(self, guest, count=64, stop_at_blr=False, out=print):
        data = self.read(guest, count * 4)
        if not data:
            out('no section covers 0x%08x' % guest)
            return
        out('=== 0x%08x  %s ===' % (guest, self.sz.sym(guest)))
        for ins in self.md.disasm(data, guest):
            tgt = ''
            # annotate branch targets with symbols
            if ins.mnemonic.startswith('b') and ins.op_str.startswith('0x'):
                try:
                    t = int(ins.op_str.split(',')[-1].strip(), 16)
                    if 0x02000000 <= t < 0x02b60000:
                        tgt = '   ; ' + self.sz.sym(t)
                except ValueError:
                    pass
            out('  0x%08x  %-8s %s%s' % (ins.address, ins.mnemonic, ins.op_str, tgt))
            if stop_at_blr and ins.mnemonic == 'blr':
                break


def main():
    a = sys.argv[1:]
    c = Code()
    if a and a[0] == '--func':
        c.dis(int(a[1], 16), 400, stop_at_blr=True)
    else:
        c.dis(int(a[0], 16), int(a[1]) if len(a) > 1 else 64)


if __name__ == '__main__':
    main()
