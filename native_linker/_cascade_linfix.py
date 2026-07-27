"""Hypothesis: rtm drifts by interpolating INSIDE the 11.4MB sound data buffer, but
is correct at the buffer START (an asset/anchor boundary). Inside a contiguous
buffer, file->runtime is 1:1, so:
   correct_rt(off) = rtm.rt(data_start - ae) + (off - data_start)
Test that this reproduces the dump-proven correct value 0x6192DB3 for data_end-16,
and check linearity at a few interior points."""
import sys, pickle, struct, re
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')
from measured_rtmap import MeasuredRuntimeMap

CORRECT_ZP = 0x6192DB3          # dump-proven b5 offset of the mount string
CORRECT_LP = CORRECT_ZP + 10    # language* = zone*+10 (data_end-6)
z = open('mp_skate_sndstreamfix.zone', 'rb').read()
BODY = 4756; FOLLOW = 0xFFFFFFFF
b = next(m.start()-BODY for m in re.finditer(re.escape(b'mpl_skate.all\x00'), z)
         if struct.unpack_from('>I', z, m.start()-BODY)[0] == FOLLOW)
u32 = lambda o: struct.unpack_from('>I', z, b+o)[0]
cec, cds = u32(0x1270), u32(0x1278)
name_off = b + BODY
data_start = name_off + 14 + cec*20
data_end = data_start + cds
ae = pickle.load(open('_skate2_simmap.pkl','rb'))['assets_end']
rtm = MeasuredRuntimeMap('_skate2_simmap.pkl', '_skate2_realmap.pkl')

def rt(fo): return int(rtm.rt(fo - ae))

anchor = rt(data_start)
print('data_start=0x%X rt=0x%X  data_end=0x%X' % (data_start, anchor, data_end))
print('direct   rt(data_end-16) = 0x%X (delta %+d vs correct)' % (rt(data_end-16), rt(data_end-16)-CORRECT_ZP))
lin = anchor + (data_end-16 - data_start)
print('linear   anchor+(off-ds) = 0x%X (delta %+d vs correct)' % (lin, lin-CORRECT_ZP))

# Where does the direct map first diverge from linear inside the buffer? (find the anchor gap)
print('\nsampling rt vs linear across the buffer:')
for frac in (0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0):
    off = data_start + int((cds-16) * frac)
    d = rt(off); l = anchor + (off - data_start)
    print('  frac %.2f off=0x%08X  rt=0x%08X linear=0x%08X  diff=%+d' % (frac, off, d, l, d-l))

# Also: is data_start itself correctly anchored? Check the previous asset boundary.
# find the SOUND asset span in sim spans
sim = pickle.load(open('_skate2_simmap.pkl','rb'))
for (i, tnm, root, s, e) in sim['spans']:
    if s <= (data_start-ae) < e or (root and 'SND' in str(root).upper()):
        if abs(s - (data_start-ae)) < 0x20000 or abs(e-(data_end-ae)) < 0x200000:
            print('  span[%d] %s %s sim[0x%X..0x%X] (file 0x%X..0x%X)' % (i, tnm, root, s, e, s+ae, e+ae))
