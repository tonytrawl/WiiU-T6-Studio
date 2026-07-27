import struct, sys, pickle
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from _dumplib import Dump
NEW  = r'C:\CemuDumps\Cemu.exe.30528.dmp'
WORK = r'C:\Users\TONY-M~1\AppData\Local\Temp\Cemu (11).DMP'
ANCHOR = b'fx_decal_burnt_paper_lod0'; ANCHOR_G = 0x1170F654
B5 = 0x3C5A6000

def sstr(d, B, g, n=40):
    s = d.read(B + g, n) or b''
    z = s.find(b'\x00'); t = s[:z if z >= 0 else n]
    return t.decode('latin1') if t and all(32 <= c < 127 for c in t) else '<'+s[:24].hex()+'>'

# WORKING zone* string + resolved value
dw = Dump(WORK); BW = dw.scan(ANCHOR, limit=1)[0] - ANCHOR_G
wz = struct.unpack_from('>I', dw.read(BW + 0x1088B428 + 0x1264, 4), 0)[0]
print('WORKING load.zone* = 0x%08X -> "%s"' % (wz, sstr(dw, BW, wz)))
print('WORKING mp_skate.all fname buf = "%s"' % sstr(dw, BW, 0x1088B428 + 0x840, 64))

# CRASH: where does the mount string actually live?
dn = Dump(NEW); BN = dn.scan(ANCHOR, limit=1)[0] - ANCHOR_G
for needle in (b'mpl_skate\x00all\x00', b'mpl_skate\x00all', b'mpl_skate.all\x00'):
    hits = dn.scan(needle, limit=6)
    print('CRASH scan %-22r -> guest %s' % (needle, [hex(h - BN) for h in hits]))
    if hits and needle == b'mpl_skate\x00all\x00':
        real = hits[0] - BN
        print('   -> correct zp payload = 0x%X  correct zp alias = 0x%08X' % (real - B5, 0xA0000000 + (real - B5) + 1))

# recompute the rebake bake() internals on the pipeline zone
import re
z = open('mp_skate_sndstreamfix.zone', 'rb').read()
BODY = 4756; FOLLOW = 0xFFFFFFFF
b = None
for m in re.finditer(re.escape(b'mpl_skate.all\x00'), z):
    c = m.start() - BODY
    if c >= 0 and struct.unpack_from('>I', z, c)[0] == FOLLOW:
        b = c; break
u32 = lambda o: struct.unpack_from('>I', z, b + o)[0]
cec, cds = u32(0x1270), u32(0x1278)
name_off = b + BODY
data_start = name_off + 14 + cec * 20
data_end = data_start + cds
ae = pickle.load(open('_skate2_simmap.pkl', 'rb'))['assets_end']
print('\nbake internals: b=0x%X cec=%d cds=0x%X data_start=0x%X data_end=0x%X ae=0x%X'
      % (b, cec, cds, data_start, data_end, ae))
print('  planted string @ file 0x%X: %r' % (data_end - 16, z[data_end-16:data_end-2]))
print('  (data_end-16 - ae) = 0x%X' % (data_end - 16 - ae))
from measured_rtmap import MeasuredRuntimeMap
rtm = MeasuredRuntimeMap('_skate2_simmap.pkl', '_skate2_realmap.pkl')
rtv = int(rtm.rt(data_end - 16 - ae))
print('  rtm.rt(...) = 0x%X -> baked zp = 0x%08X (resolves to guest 0x%X)'
      % (rtv, 0xA0000000 + rtv + 1, B5 + rtv))
