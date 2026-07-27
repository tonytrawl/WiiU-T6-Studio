"""New crash = opening the skate english streamed bank with a GARBAGE name.
(1) extract the exact garbage filename bytes from the log,
(2) find the mpl_skate.english (and .all) bank bodies in pipeline vs playable zone,
    diff name*/filename fields + the +0x20/+0x24/+0x942/+0x1264 baked ptrs,
(3) see if the garbage bytes appear in either zone (identify the bad-ptr source)."""
import struct, re
LOG = r"C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\log.txt"
BODY = 4756

# ---- (1) exact garbage filename bytes ----
data = open(LOG, 'rb').read()
i = data.rfind(b'/vol/content/english/sound/')
j = data.find(b'.sabs"', i)
raw = data[i:j+5]
print('[1] crash path raw bytes (%d):' % len(raw))
print('   ', raw.hex())
print('    ascii:', ''.join(chr(c) if 32 <= c < 127 else '.' for c in raw))
garbage = raw[len(b'/vol/content/english/sound/'):j+5-i]
print('    garbage name portion hex:', garbage.hex())

# ---- (2) find skate bank bodies in both zones ----
ZP = open('mp_skate_sndstreamfix.zone', 'rb').read()   # the DEPLOYED fixed zone
ZK = open('mp_skate_gfxtail46.zone', 'rb').read()

def banks(z, needle):
    out = []
    for m in re.finditer(re.escape(needle), z):
        c = m.start() - BODY
        if c >= 0 and struct.unpack_from('>I', z, c)[0] == 0xFFFFFFFF:
            out.append(c)
    return out

for needle in (b'mpl_skate.english\x00', b'mpl_skate.all\x00'):
    print('\n[2] bank %r:' % needle)
    bp = banks(ZP, needle); bk = banks(ZK, needle)
    print('    pipe bodies:', [hex(x) for x in bp], ' key bodies:', [hex(x) for x in bk])
    # also: where does the NAME string live, and is it referenced?
    for m in re.finditer(re.escape(needle), ZP):
        print('    name string in PIPE @0x%X context: %s' % (m.start(), ZP[m.start()-4:m.start()+len(needle)+2].hex()))
    if not bp:
        # maybe english bank has no separate body; find name occurrences
        print('    (no FOLLOW-marked body; english may be a name-only/aliased bank)')
        continue
    b0 = bp[0]; k0 = bk[0] if bk else None
    FIELDS = {0x00: 'name*', 0x20: 'strm.zone*', 0x24: 'strm.lang*', 0x840: 'strm.filename',
              0x942: 'zp@0x942', 0x946: 'lp@0x946', 0x1162: 'load.filename',
              0x1264: 'zone*', 0x1268: 'language*', 0x1270: 'entryCount', 0x1278: 'dataSize'}
    for off, nm in sorted(FIELDS.items()):
        vp = struct.unpack_from('>I', ZP, b0+off)[0]
        vk = struct.unpack_from('>I', ZK, k0+off)[0] if k0 is not None else 0
        mark = '' if vp == vk else '  <<< DIFF'
        print('    +0x%04X %-14s pipe=0x%08X key=0x%08X%s' % (off, nm, vp, vk, mark))

# ---- (3) does the garbage appear in the zone? (identify bad-ptr target) ----
print('\n[3] garbage[:16] in zones?')
g16 = garbage[:16]
print('    in PIPE zone:', [hex(m.start()) for m in re.finditer(re.escape(g16), ZP)][:5])
print('    in KEY  zone:', [hex(m.start()) for m in re.finditer(re.escape(g16), ZK)][:5])
