"""Does loader_sim's FRESH omap give the correct mount-pointer runtime offset
(0x6192DB3) for the current build, unlike the stale measured rtm (0x61941EE)?
If yes -> the general fix is to bake sound mount ptrs via omap, not the measured map."""
import sys, struct, re, pickle
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
import loader_sim as LS

CORRECT = 0x6192DB3
ZP = 'mp_skate_sndstreamfix.zone'
z = open(ZP, 'rb').read()
BODY = 4756; FOLLOW = 0xFFFFFFFF
b = next(m.start()-BODY for m in re.finditer(re.escape(b'mpl_skate.all\x00'), z)
         if struct.unpack_from('>I', z, m.start()-BODY)[0] == FOLLOW)
u32 = lambda o: struct.unpack_from('>I', z, b+o)[0]
cec, cds = u32(0x1270), u32(0x1278)
data_start = (b + BODY) + 14 + cec*20
data_end = data_start + cds

print('simulating %s (may take a couple minutes)...' % ZP)
em, spans = LS.simulate(ZP, verbose=False)[:2]
rtmap = LS.RuntimeMap(em.omap)
# omap is keyed by stream b5 offset; the rebake used (file_off - assets_end).
ae = pickle.load(open('_skate2_simmap.pkl','rb'))['assets_end']
for off, tag in ((data_end-16, 'zone*(data_end-16)'), (data_end-6, 'language*(data_end-6)')):
    so = off - ae
    rt = int(rtmap.rt(so))
    alias = 0xA0000000 + rt + 1
    corr = CORRECT if 'zone' in tag else CORRECT + 10
    print('  %-22s stream=0x%X  omap.rt=0x%X  alias=0x%08X  (correct 0x%X, delta %+d)'
          % (tag, so, rt, alias, corr, rt - corr))
