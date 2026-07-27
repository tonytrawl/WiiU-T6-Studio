#!/usr/bin/env python3
"""gfxtail10: fix skate console GfxWorld body name/baseName stale aliases (boot-10 hang).

Boot 10 (gfxtail9) hang: '[OSConsole] Could not load default asset '' for asset type
'gfx_map'' then DB wait forever. Dump (Temp\\Cemu.DMP, BASE 0x1ee7b370000): GfxWorld body
(file 0x03a69ac9, = gfx band start 61,250,249) has
  +0 name     = 0xa24ec9d2 -> 0x3ea929d1 = garbage '\\x87\\xf6\\x9d' (stale by 88,847;
               true string 'maps/mp/mp_skate.d3dbsp' @0x3eaa84e0, owned by ComWorld;
               correct alias = clipMap's 0xa25024e1)
  +4 baseName = 0xa0005abc -> 0x3c5ababb = 'skate' (3 bytes past 'mp_skate'; -3)
Only these 2 blk5 aliases exist in the 1076-B console body. Size-neutral.
"""
import struct, hashlib, sys, os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
GB = 0x03a69ac9
z = bytearray(open('mp_skate_gfxtail9.zone', 'rb').read())

name, base = struct.unpack_from('>2I', z, GB)
assert (name, base) == (0xa24ec9d2, 0xa0005abc), (hex(name), hex(base))
struct.pack_into('>2I', z, GB, 0xa25024e1, 0xa0005ab9)
open('mp_skate_gfxtail10.zone', 'wb').write(z)
print('mp_skate_gfxtail10.zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())

sys.path.insert(0, '../WiiU_FF_Studio')
import wiiu_ff
ff = wiiu_ff.pack(bytes(z), 'mp_skate')
open('mp_skate_gfxtail10.ff', 'wb').write(ff)
print('mp_skate_gfxtail10.ff md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
