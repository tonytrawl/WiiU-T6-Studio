"""BSP crash (= gfxtail6/7): clipMap name mis-relocated (block-4 alias instead of a
block-5 alias to 'maps/mp/mp_skate.d3dbsp'). Compare the clipMap body name field
(body[0]) in the pipeline build vs the playable answer key, and locate the bsp
string + clipMap body offset for the fix."""
import struct, re
PIPE = 'mp_skate_mountfix.zone'      # deployed build (has sound fixes)
KEY  = 'mp_skate_gfxtail46.zone'     # playable answer key
ZP = open(PIPE, 'rb').read()
ZK = open(KEY, 'rb').read()
BSP = b'maps/mp/mp_skate.d3dbsp\x00'

for tag, z in (('PIPE', ZP), ('KEY', ZK)):
    hits = [m.start() for m in re.finditer(re.escape(BSP), z)]
    print('[%s] "maps/mp/mp_skate.d3dbsp" @ %s' % (tag, [hex(h) for h in hits]))

def decode(v):
    blk = v >> 29
    if 0xA0000000 <= v < 0xC0000000:
        return 'blk5-alias pay=0x%X' % ((v-1) & 0x1FFFFFFF)
    if 0x80000000 <= v < 0xA0000000:
        return '*** blk4-alias (MISENCODE) pay=0x%X ***' % ((v-1) & 0x1FFFFFFF)
    if v == 0xFFFFFFFF: return 'FOLLOW'
    return 'blk%d/plain 0x%X' % (blk, v)

# clipMap_t: top-level asset; body[0] = name*. Locate by structural signature the
# note gives: isInUse byte, numCBrushSides etc. Simpler: the clipMap body's name
# field is a pointer to the bsp string. Scan for a word that is a blk5/blk4 alias
# whose payload ~ the bsp string's runtime position, near the clipMap region (file
# ~84.5MB per the note). Report candidate name fields around each build's tail.
print('\n--- scanning tail region 0x50A0000..0x5100000 for clipMap-name-like aliases ---')
# Actually locate the clipMap by the KNOWN gfxtail signature: isInUse=0 + big counts.
# Fall back: print the 64 bytes at the note-known offset +/- to compare builds.
# Find clipMap body: it directly precedes a run and its name* points to BSP string.
# Search both zones for a 4-byte alias immediately followed by clipMap-ish fields.
for tag, z in (('PIPE', ZP), ('KEY', ZK)):
    print('\n[%s] candidate clipMap name fields (blk4/blk5 aliases in 0x5000000..0x5200000):' % tag)
    found = 0
    for off in range(0x5000000, min(0x5200000, len(z)-4), 4):
        v = struct.unpack_from('>I', z, off)[0]
        if 0x80000000 <= v < 0xC0000000:
            pay = (v-1) & 0x1FFFFFFF
            # clipMap name payload is ~0x2500000 region (bsp string in b5)
            if 0x2400000 <= pay <= 0x2700000:
                # check next few words look like clipMap (planeCount etc small-ish)
                nxt = struct.unpack_from('>I', z, off+4)[0]
                print('   @0x%X = 0x%08X (%s) next=0x%08X' % (off, v, decode(v), nxt))
                found += 1
                if found > 12: break
