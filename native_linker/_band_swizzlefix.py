"""Extract the stomp band from the fresh crash dump (swizzle-fix boot) and
characterize its content."""
import struct, sys

DMP = r'C:/Users/Tony - Main Rig/AppData/Roaming/Cemu/crashdump/crash_20260713_1261511.dmp'
Z = open('mp_skate_measured.zone', 'rb').read()

f = open(DMP, 'rb')
f.seek(8); ns, rva = struct.unpack('<II', f.read(8))
f.seek(rva); dr = f.read(ns * 12)
stt = {}
for i in range(ns):
    t, s, l = struct.unpack_from('<III', dr, i * 12)
    stt[t] = (s, l)
s, l = stt[9]
f.seek(l); nn, brva = struct.unpack('<QQ', f.read(16))
f.seek(l + 16)
ranges = []
off = brva
for i in range(nn):
    a, z = struct.unpack_from('<QQ', f.read(16))
    ranges.append((a, z, off)); off += z
print('mem ranges:', len(ranges), 'largest:',
      ['0x%x+0x%x' % (a, z) for (a, z, o) in sorted(ranges, key=lambda t: -t[1])[:3]])

# guest base via zone block5 anchor
sc = struct.unpack_from('>I', Z, 40)[0]; o = 64 + sc * 4
anc = Z[o + 200:o + 240]; anc_b5 = (o + 200) - 64
ra = rd = None
for (a, z, fo) in sorted(ranges, key=lambda t: -t[1]):
    if z < 0x10000000: continue
    f.seek(fo); d = f.read(z)
    i = d.find(anc)
    if i >= 0:
        ra, ri, rd, rfo, rz = a, i, d, fo, z
        break
assert rd is not None, 'anchor not found'
# block5 guest base known from prior sessions' rtmap; derive guest base:
# host_addr_of_anchor = ra+ri ; guest block5 base guest addr = ?
# We don't know block5 guest addr directly; instead locate guest 0x10000000
# region: guest VA X maps to host GUEST_BASE+X within this range.
# Find GUEST_BASE: assume anchor's guest addr g satisfies host = ra+ri, and
# guest bases are aligned 0x10000; scan for the MEM1-style band directly:
# try candidate GUEST_BASE = ra (range base maps guest 0?) -- verify by
# checking PPC thread names visible near 0x10ef1400.
CAND = [ra]  # host base of the big range = guest 0 mapping (Cemu maps guest space in one range)
GB = ra
def guest(g, n):
    return rd[g:g + n]
band_lo, band_hi = 0x100f0000, 0x10f01400
band = guest(band_lo, band_hi - band_lo)
open('_band_swizzlefix.bin', 'wb').write(band)
print('band extracted: %d bytes' % len(band))
# first non-zero / structure probes around claimed starts
for probe in (0x10100000, 0x10103000, 0x103f0000, 0x10ef1400 - 0x40):
    d = guest(probe, 64)
    print('0x%08x: %s' % (probe, d.hex()))
# scan for readable ascii (thread names?) near top
top = guest(0x10ef0000, 0x2000)
import re
for m in re.finditer(rb'[ -~]{8,}', top):
    print('ascii @0x%x: %s' % (0x10ef0000 + m.start(), m.group()[:60]))
