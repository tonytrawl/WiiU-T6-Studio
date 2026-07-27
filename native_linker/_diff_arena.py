"""Path A: diff the 0x103efe00 arena + thread-list between a WORKING-map live dump
and the skate crash dump. Reads both MemoryList(type5) and Memory64(type9) dumps."""
import struct, sys

def load(path):
    f = open(path, 'rb')
    f.seek(8); ns, rva = struct.unpack('<II', f.read(8))
    f.seek(rva); dr = f.read(ns * 12); stt = {}
    for i in range(ns):
        t, s, l = struct.unpack_from('<III', dr, i * 12); stt[t] = (s, l)
    ranges = []
    if 9 in stt:
        s, l = stt[9]; f.seek(l); nn, brva = struct.unpack('<QQ', f.read(16)); f.seek(l + 16)
        off = brva
        for i in range(nn):
            a, z = struct.unpack('<QQ', f.read(16)); ranges.append((a, z, off)); off += z
    else:
        s, l = stt[5]; f.seek(l); nn, = struct.unpack('<I', f.read(4)); d = f.read(nn * 16)
        for i in range(nn):
            a, z, r32 = struct.unpack_from('<QII', d, i * 16); ranges.append((a, z, r32))
    big = max(ranges, key=lambda t: t[1])
    f.seek(big[2]); D = f.read(big[1])
    return D  # guest 0x02000000 maps to D[0]

def rd(D, g, ln): return D[g - 0x02000000:g - 0x02000000 + ln]

def ent(D, g):
    b = rd(D, g, 64); return len(set(b)) > 40

def arena_bounds(D, seed=0x103efe00):
    lo = seed
    while ent(D, lo - 64): lo -= 64
    hi = seed
    while ent(D, hi): hi += 64
    return lo, hi

SKATE = r'C:/Users/Tony - Main Rig/AppData/Roaming/Cemu/crashdump/crash_20260713_1261939.dmp'
WORK = r'C:/Users/TONY-M~1/AppData/Local/Temp/Cemu.DMP'

Dk = load(SKATE)
Dw = load(WORK)
print('=== arena bounds ===')
lk, hk = arena_bounds(Dk); print('skate   arena 0x%08x..0x%08x (%.2f MB)' % (lk, hk, (hk-lk)/2**20))
lw, hw = arena_bounds(Dw); print('working arena 0x%08x..0x%08x (%.2f MB)' % (lw, hw, (hw-lw)/2**20))
print('=== the thread region 0x10e87450 (skate corrupt OSThread) ===')
for tag, D in (('skate', Dk), ('working', Dw)):
    b = rd(D, 0x10e87450, 32)
    print('%-8s @0x10e87450: %s' % (tag, b.hex()))
    # OSThread magic is 0x74487244 'tHrD' at +0x320 in T6? scan nearby for tHrD
    reg = rd(D, 0x10e80000, 0x20000)
    js = [0x10e80000 + i for i in range(len(reg)-4) if reg[i:i+4] == b'tHrD']
    print('   tHrD magics in 0x10e80000..0x10ea0000:', [hex(x) for x in js[:8]])
print('=== does the working arena reach 0x10e87450? ===')
print('working arena covers 0x10e87450:', lw <= 0x10e87450 < hw)
print('skate    arena covers 0x10e87450:', lk <= 0x10e87450 < hk)
