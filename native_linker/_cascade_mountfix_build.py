"""FIX 2: the sound mount pointers (zone*/language*) are baked +5179 too high (the
rebake's measured rtm is stale — the 11.4MB sound buffer moved between the measured
build and now). Dump-proven: the mount string 'mpl_skate\\0all\\0' is at runtime
0x42738DB3 (b5 base 0x3C5A6000), so correct zp=0xA6192DB4, lp=0xA6192DBE. Bake the
correct values into all 6 mount-pointer words of the mpl_skate.all bank."""
import struct, re, hashlib, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff

B5 = 0x3C5A6000
STR_ADDR = 0x42738DB3               # dump-proven runtime addr of "mpl_skate\0all\0"
ZP = 0xA0000000 + (STR_ADDR - B5) + 1                # zone*  (data_end-16)
LP = 0xA0000000 + (STR_ADDR + 10 - B5) + 1           # language* (data_end-6)
print('correct zp=0x%08X lp=0x%08X' % (ZP, LP))

SRC = 'mp_skate_sndstreamfix.zone'
OUTZ = 'mp_skate_mountfix.zone'; OUTF = 'mp_skate_mountfix.ff'
z = bytearray(open(SRC, 'rb').read())
BODY = 4756; FOLLOW = 0xFFFFFFFF
b = next(m.start()-BODY for m in re.finditer(re.escape(b'mpl_skate.all\x00'), bytes(z))
         if struct.unpack_from('>I', z, m.start()-BODY)[0] == FOLLOW)
u32 = lambda o: struct.unpack_from('>I', z, b+o)[0]
print('bank @0x%X  BEFORE: ' % b + ' '.join('+0x%X=0x%08X' % (o, u32(o))
      for o in (0x20, 0x24, 0x942, 0x946, 0x1264, 0x1268)))
# all six mount-pointer words currently hold the +5179-stale value; set correct.
for o in (0x20, 0x942, 0x1264): struct.pack_into('>I', z, b+o, ZP)
for o in (0x24, 0x946, 0x1268): struct.pack_into('>I', z, b+o, LP)
print('bank @0x%X  AFTER : ' % b + ' '.join('+0x%X=0x%08X' % (o, u32(o))
      for o in (0x20, 0x24, 0x942, 0x946, 0x1264, 0x1268)))

# sanity: all changed bytes lie within the 6 mount-pointer field ranges
src = open(SRC, 'rb').read()
changed = [i for i in range(len(z)) if z[i] != src[i]]
fields = [(b+o, b+o+4) for o in (0x20, 0x24, 0x942, 0x946, 0x1264, 0x1268)]
assert all(any(s <= i < e for s, e in fields) for i in changed), 'stray edit outside mount fields'
assert all(u32(o) == (ZP if o in (0x20, 0x942, 0x1264) else LP)
           for o in (0x20, 0x24, 0x942, 0x946, 0x1264, 0x1268)), 'field not set'
print('changed %d bytes, all within the 6 mount-pointer fields' % len(changed))

open(OUTZ, 'wb').write(bytes(z))
print('%s md5 %s' % (OUTZ, hashlib.md5(bytes(z)).hexdigest()))
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open(OUTF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (OUTF, hashlib.md5(ff).hexdigest(), len(ff)))
