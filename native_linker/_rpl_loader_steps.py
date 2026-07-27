#!/usr/bin/env python3
"""Symbolic-lite extraction of the loader step sequence from a Load_* function.

Tracks: immediates, struct-base registers (loaded as *varGlobal), and field
offsets (addi rD, rBase, IMM), so every call site can be labelled with the
struct field it is walking.
"""
import re
import sys
import json

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _rpl_dis_robust import Code  # noqa

HELP = {
    0x223fd1c: 'Load_Stream', 0x223fb3c: 'PUSH', 0x223fbb0: 'POP',
    0x223fc34: 'ALLOC', 0x223fc4c: 'INC', 0x223fc60: 'InsertPointer',
    0x223fecc: 'Off2Ptr', 0x223fea4: 'Off2Alias',
}
MEM = re.compile(r'(-?0x[0-9a-f]+|-?\d+)\((r\d+)\)')
VOL = ['r0'] + ['r%d' % i for i in range(3, 13)]


def steps(c, start, size):
    lines = c.lines(start, size)
    tgts = {}
    for (a, m, o, w) in lines:
        if m.startswith('b') and '0x' in o:
            try:
                t = int(o.split(',')[-1].strip(), 16)
                if start <= t < start + size:
                    tgts[t] = 'L_%03x' % (t - start)
            except ValueError:
                pass
    imm = {}       # reg -> int
    fld = {}       # reg -> field offset (relative to a struct base)
    base = set()   # regs holding a struct base pointer
    hi = {}        # reg -> lis value
    out = []
    curfield = None
    lastset = [None]
    for (a, m, o, w) in lines:
        rel = a - start
        if a in tgts:
            out.append((rel, 'LABEL', tgts[a]))
        ops = [x.strip() for x in o.split(',')]
        if m == 'li':
            imm[ops[0]] = int(ops[1], 0); fld.pop(ops[0], None); base.discard(ops[0])
        elif m == 'lis':
            hi[ops[0]] = int(ops[1], 0) << 16
            imm.pop(ops[0], None); fld.pop(ops[0], None); base.discard(ops[0])
        elif m == 'addi' and len(ops) == 3:
            d, s = ops[0], ops[1]
            v = int(ops[2], 0)
            if s in base:
                fld[d] = v; base.discard(d); imm.pop(d, None)
            elif s in fld:
                fld[d] = fld[s] + v; base.discard(d)
            elif s in hi:
                hi[d] = hi[s] + v; fld.pop(d, None); base.discard(d)
            else:
                fld.pop(d, None); base.discard(d); imm.pop(d, None)
        elif m == 'mr' and len(ops) == 2:
            d, s = ops
            for D in (imm, fld, hi):
                D.pop(d, None)
            base.discard(d)
            if s in imm: imm[d] = imm[s]
            if s in fld: fld[d] = fld[s]
            if s in hi: hi[d] = hi[s]
            if s in base: base.add(d)
        elif m in ('lwz', 'lwzu'):
            mm = MEM.match(ops[1])
            for D in (imm, fld, hi):
                D.pop(ops[0], None)
            base.discard(ops[0])
            if mm:
                off, src = int(mm.group(1), 0), mm.group(2)
                addr = hi.get(src, None)
                if m == 'lwzu' and addr is not None:
                    hi[src] = addr + off
                if addr is not None and (addr + off) in (0x103c1ae8,) or (addr is not None and 0x103c0000 <= addr + off < 0x103d0000):
                    base.add(ops[0])     # *varGlobal -> struct base
                elif src in base:
                    out.append((rel, 'FIELD', off))
                    curfield = off
                    if off == 0:
                        pass
                elif src in fld:
                    out.append((rel, 'FIELD', fld[src] + off))
                    curfield = fld[src] + off
        elif m == 'bl':
            t = int(ops[-1], 16)
            nm = HELP.get(t) or c.sz.sym(t).split('__')[0]
            args = {r: imm[r] for r in ('r3', 'r4', 'r5') if r in imm}
            f = None
            for r in ('r3', 'r4', 'r5'):
                if r in fld:
                    f = fld[r]
            if f is None:
                f = lastset[0]
            out.append((rel, 'CALL', nm, args, f, curfield))
            lastset[0] = None
            for r in VOL:
                imm.pop(r, None); fld.pop(r, None); hi.pop(r, None); base.discard(r)
        elif m in ('stw', 'stwu'):
            mm = MEM.match(ops[1])
            if mm:
                off, dst = int(mm.group(1), 0), mm.group(2)
                addr = hi.get(dst)
                if m == 'stwu' and addr is not None:
                    hi[dst] = addr + off
                src = ops[0]
                if src in fld:
                    lastset[0] = fld[src]
                    out.append((rel, 'SETVAR', hex((addr or 0) + off), fld[src]))
                elif src in base:
                    lastset[0] = 0
                    out.append((rel, 'SETVAR', hex((addr or 0) + off), 0))
        elif m.startswith('b') and m != 'bl' and '0x' in o:
            try:
                t = int(ops[-1], 16)
                out.append((rel, 'BR', m, tgts.get(t, hex(t))))
            except ValueError:
                pass
        elif m in ('cmpwi', 'cmplwi'):
            out.append((rel, 'CMP', o))
        elif m == 'blr':
            out.append((rel, 'BLR'))
    return out


def show(out, fh=sys.stdout, terse=True):
    for s in out:
        rel, kind = s[0], s[1]
        if terse and kind in ('LABEL', 'BR', 'CMP', 'SETVAR', 'FIELD', 'BLR'):
            if kind == 'FIELD':
                fh.write('  +%-5x FIELD  +0x%x (%d)\n' % (rel, s[2], s[2]))
            elif kind == 'CMP':
                fh.write('  +%-5x CMP    %s\n' % (rel, s[2]))
            elif kind == 'LABEL':
                fh.write('%s:\n' % s[2])
            elif kind == 'BR':
                fh.write('  +%-5x %s -> %s\n' % (rel, s[2], s[3]))
            elif kind == 'BLR':
                fh.write('  +%-5x BLR\n' % rel)
            continue
        if kind == 'CALL':
            _, _, nm, args, f, cf = s
            fh.write('  +%-5x CALL %-34s %-28s fldarg=%s\n' % (
                rel, nm, ' '.join('%s=0x%x' % kv for kv in sorted(args.items())),
                ('+0x%x' % f) if f is not None else '-'))


if __name__ == '__main__':
    c = Code()
    g, n = int(sys.argv[1], 16), int(sys.argv[2])
    o = steps(c, g, n)
    outp = sys.argv[3] if len(sys.argv) > 3 else None
    if outp:
        with open(outp, 'w') as fh:
            show(o, fh)
        print('wrote', outp)
    else:
        show(o)
