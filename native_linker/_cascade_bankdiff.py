"""Precise, complete diff of the mpl_skate.all bank body (full 0x1294 struct)
between pipeline (crashes) and playable (boots) zones. Confirm the ONLY structural
(FOLLOW-vs-pointer) diffs are +0x20/+0x24; everything else identical or a benign
baked-pointer value delta. This is the go/no-go for the +0x20/+0x24 bake fix."""
import struct, re
BODY = 4756
def find_bank(z):
    for m in re.finditer(re.escape(b'mpl_skate.all\x00'), z):
        c = m.start() - BODY
        if c >= 0 and struct.unpack_from('>I', z, c)[0] == 0xFFFFFFFF:
            return c
    return None
ZP = open('mp_skate_pipecheck.zone', 'rb').read()
ZK = open('mp_skate_gfxtail46.zone', 'rb').read()
bp, bk = find_bank(ZP), find_bank(ZK)
FOLLOW = 0xFFFFFFFF
print('bank body: pipe@0x%X key@0x%X  full struct = 0x%X (4756) bytes\n' % (bp, bk, BODY))
# byte diffs across the WHOLE struct
diffs = [i for i in range(BODY) if ZP[bp+i] != ZK[bk+i]]
# coalesce to word groups
words = sorted({i & ~3 for i in diffs})
print('total differing bytes: %d  in %d words' % (len(diffs), len(words)))
print('%-8s %-11s %-11s  %s' % ('off', 'PIPE', 'KEY', 'nature'))
for w in words:
    vp = struct.unpack_from('>I', ZP, bp+w)[0]
    vk = struct.unpack_from('>I', ZK, bk+w)[0]
    nat = []
    if vp == FOLLOW and vk != FOLLOW:
        nat.append('*** STRUCTURAL: pipe FOLLOW, key POINTER ***')
    elif vk == FOLLOW and vp != FOLLOW:
        nat.append('*** STRUCTURAL: key FOLLOW, pipe POINTER ***')
    elif 0xA0000000 <= vp < 0xC0000000 and 0xA0000000 <= vk < 0xC0000000:
        nat.append('benign baked-ptr delta=%d' % (((vp-1)&0x1FFFFFFF)-((vk-1)&0x1FFFFFFF)))
    else:
        nat.append('other')
    print('+0x%04X  %08X    %08X   %s' % (w, vp, vk, ' '.join(nat)))
