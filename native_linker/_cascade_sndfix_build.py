"""FIX (surgical, isolation-grade): the ONLY structural defect in the pipeline's
mpl_skate.all bank body vs the playable build is bank+0x20/+0x24 (rtBank_STREAMED
zone*/language*) left as FOLLOW (0xFFFFFFFF) instead of the baked runtime pointers.
Left FOLLOW -> loader consumes stream bytes there -> ~6MB stream desync writes over
the global SndBank struct array @0x1087E7CC -> sound alias lookup walks garbage.

Patch bank+0x20 := zone*(+0x1264), bank+0x24 := language*(+0x1268) on the EXACT
crashing zone, repack. Changes exactly 8 bytes vs the build that stomped -> if it
boots, the fix is proven to be exactly this."""
import struct, re, hashlib, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff

BODY = 4756
SRC = 'mp_skate_pipecheck.zone'
OUTZ = 'mp_skate_sndstreamfix.zone'
OUTF = 'mp_skate_sndstreamfix.ff'
z = bytearray(open(SRC, 'rb').read())
print('src %s md5 %s len %d' % (SRC, hashlib.md5(z).hexdigest(), len(z)))

# locate mpl_skate.all bank body (FOLLOW name marker BODY bytes before the name)
b = None
for m in re.finditer(re.escape(b'mpl_skate.all\x00'), bytes(z)):
    c = m.start() - BODY
    if c >= 0 and struct.unpack_from('>I', z, c)[0] == 0xFFFFFFFF:
        b = c; break
assert b is not None, 'bank body not found'
u32 = lambda o: struct.unpack_from('>I', z, b + o)[0]
zone_p, lang_p = u32(0x1264), u32(0x1268)
print('bank @0x%X  zone*(+0x1264)=0x%08X  language*(+0x1268)=0x%08X' % (b, zone_p, lang_p))
print('BEFORE: +0x20=0x%08X +0x24=0x%08X' % (u32(0x20), u32(0x24)))
assert u32(0x20) == 0xFFFFFFFF and u32(0x24) == 0xFFFFFFFF, 'unexpected: +0x20/+0x24 not FOLLOW'
assert 0xA0000000 <= zone_p < 0xC0000000 and 0xA0000000 <= lang_p < 0xC0000000, \
    'zone*/language* not block-5 aliases -> abort'

struct.pack_into('>I', z, b + 0x20, zone_p)
struct.pack_into('>I', z, b + 0x24, lang_p)
print('AFTER : +0x20=0x%08X +0x24=0x%08X' % (u32(0x20), u32(0x24)))

# sanity: exactly 8 bytes changed vs source
src = open(SRC, 'rb').read()
changed = [i for i in range(len(z)) if z[i] != src[i]]
print('bytes changed vs source: %d (expect 8) @ %s' % (len(changed), [hex(c) for c in changed]))
assert len(changed) == 8

open(OUTZ, 'wb').write(bytes(z))
print('%s md5 %s' % (OUTZ, hashlib.md5(bytes(z)).hexdigest()))
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open(OUTF, 'wb').write(ff)
print('%s md5 %s (%d bytes)' % (OUTF, hashlib.md5(ff).hexdigest(), len(ff)))
