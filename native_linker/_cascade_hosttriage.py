"""Robust minidump triage: distinguish HOST (Cemu.exe) crash from GUEST (JIT) fault.
The old _r2boot_triage.py mis-parses some dumps (bad nparams). Reads the exception
record defensively and reports code/address/RIP + module context."""
import struct, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump

path = sys.argv[1]
d = Dump(path)
print('dump:', path)
print('streams present:', sorted(d.streams.keys()))

CODES = {0xC0000005: 'ACCESS_VIOLATION', 0xC0000409: 'STACK_BUFFER_OVERRUN/__fastfail',
         0xC000001D: 'ILLEGAL_INSTRUCTION', 0xC0000094: 'INT_DIVIDE_BY_ZERO',
         0xC0000374: 'HEAP_CORRUPTION', 0x80000003: 'BREAKPOINT',
         0xC00000FD: 'STACK_OVERFLOW', 0xE06D7363: 'C++ EH exception'}

if 6 in d.streams:
    s, l = d.streams[6]
    d.f.seek(l); b = d.f.read(s)
    tid = struct.unpack_from('<I', b, 0)[0]
    code, flags = struct.unpack_from('<II', b, 8)
    rec, addr = struct.unpack_from('<QQ', b, 16)
    npar = struct.unpack_from('<I', b, 32)[0]
    print('\nthread id      : %d' % tid)
    print('exception code : 0x%08X  (%s)' % (code, CODES.get(code, '?')))
    print('flags          : 0x%X' % flags)
    print('exception addr : 0x%X' % addr)
    print('num params     : %d' % npar)
    if 0 < npar <= 15:
        pars = struct.unpack_from('<%dQ' % npar, b, 40)
        print('params         :', ['0x%X' % p for p in pars])
        if code == 0xC0000005 and npar >= 2:
            kind = {0: 'READ', 1: 'WRITE', 8: 'EXECUTE'}.get(pars[0], str(pars[0]))
            print('  -> AV %s of address 0x%X' % (kind, pars[1]))
    # context
    ctx_sz, ctx_rva = struct.unpack_from('<II', b, 160)
    if ctx_sz and ctx_rva:
        d.f.seek(ctx_rva); C = d.f.read(ctx_sz)
        names = [('rax',0x78),('rcx',0x80),('rdx',0x88),('rbx',0x90),('rsp',0x98),
                 ('rbp',0xA0),('rsi',0xA8),('rdi',0xB0),('r8',0xB8),('r9',0xC0),
                 ('r10',0xC8),('r11',0xD0),('r12',0xD8),('r13',0xE0),('r14',0xE8),
                 ('r15',0xF0),('rip',0xF8)]
        print('\nregisters:')
        for nm, off in names:
            if off + 8 <= len(C):
                print('  %-4s 0x%016X' % (nm, struct.unpack_from('<Q', C, off)[0]))
        rip = struct.unpack_from('<Q', C, 0xF8)[0] if 0xF8 + 8 <= len(C) else 0
        r13 = struct.unpack_from('<Q', C, 0xE0)[0] if 0xE0 + 8 <= len(C) else 0
        print('\nInterpretation:')
        if 0x7FF000000000 <= rip < 0x800000000000:
            print('  RIP is in HOST module space (Cemu.exe / a DLL) -> HOST-side crash,')
            print('  NOT a guest PPC fault. The guest was likely running fine.')
        else:
            print('  RIP 0x%X is in JIT/other space -> possible guest-code fault.' % rip)
        # is r13 a plausible guest membase?
        if 0x10000000000 <= r13 < 0x1000000000000:
            print('  r13=0x%X looks like a guest membase (JIT thread).' % r13)
else:
    print('\nNO exception stream (6) -> not a crash dump; likely a MANUAL/Task-Mgr dump')
    print('(i.e. the process was dumped while alive — consistent with a hang or a')
    print(' user-initiated dump rather than a fault).')

# module list: what module owns RIP?
if 4 in d.streams:
    s, l = d.streams[4]
    d.f.seek(l)
    n = struct.unpack('<I', d.f.read(4))[0]
    mods = []
    for i in range(n):
        m = d.f.read(108)
        if len(m) < 108: break
        base, sz = struct.unpack_from('<QI', m, 0)
        nrva = struct.unpack_from('<I', m, 0x38)[0]
        mods.append((base, sz, nrva))
    print('\nmodules loaded: %d' % len(mods))
