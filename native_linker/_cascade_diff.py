"""CASCADE step 0 (offline): exact word-diff between the PIPELINE build
(mp_skate_pipecheck.zone, stomps) and the PLAYABLE build
(mp_skate_gfxtail46.zone, boots). The handoff says ~14 diff words; if true we can
pin the SndBank-stomp culprit by feature WITHOUT four full rebuilds -- patch the
pipeline zone's diff-words back to playable per feature, repack (fast), boot once.

Classifies each diff word by region/feature and decodes alias handles, flagging
any that resolve into the stomped bank-table region 0x1087E7CC..0x1088FE78."""
import struct, hashlib, sys
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref')

PIPE = 'mp_skate_pipecheck.zone'
KEY  = 'mp_skate_gfxtail46.zone'
P = open(PIPE, 'rb').read()
K = open(KEY, 'rb').read()

print('pipecheck md5 %s  len %d  (handoff: b154886c..., 101,404,242)'
      % (hashlib.md5(P).hexdigest(), len(P)))
print('gfxtail46 md5 %s  len %d  (handoff: e4d844b4..., 101,404,242)'
      % (hashlib.md5(K).hexdigest(), len(K)))

if len(P) != len(K):
    print('!! LENGTH MISMATCH -> not a pure word diff; region-diff needed')

n = min(len(P), len(K))
# byte-diff first (catch sub-word), then coalesce into 4-byte words
byte_diffs = [i for i in range(n) if P[i] != K[i]]
print('\nraw differing BYTES: %d' % len(byte_diffs))

# coalesce to 4-aligned words touched
words = sorted({(i & ~3) for i in byte_diffs})
print('differing 4-byte-aligned WORDS: %d' % len(words))

be = lambda d, o: struct.unpack_from('>I', d, o)[0]

STOMP_LO, STOMP_HI = 0x1087E7CC, 0x1088FE78   # global bank-table records region


def decode(v):
    """Interpret a 32-bit word as a possible alias handle / pointer; report region."""
    tags = []
    if 0xA0000000 <= v < 0xC0000000:
        pay = (v - 1) & 0x1FFFFFFF
        tags.append('alias pay=0x%X' % pay)
    blk = v >> 29
    if v >= 0x02000000:
        tags.append('blk%d' % blk)
    # does it (or its payload) fall in the stomped bank region?
    for cand in (v, (v - 1) & 0x1FFFFFFF):
        if STOMP_LO <= cand <= STOMP_HI:
            tags.append('*** IN STOMP REGION ***')
    if v in (0xFFFFFFFF,):
        tags.append('FOLLOW')
    if v in (0xFFFFFFFE,):
        tags.append('INSERT')
    return ' '.join(tags) if tags else 'small/plain'


# known landmark: dynEnt physPreset words the rebake nulls (handoff @0x5562B54/58)
DYNENT = {0x5562B54, 0x5562B58}

print('\n%-12s %-11s %-11s | %s' % ('off', 'PIPE', 'KEY', 'decode(pipe -> key)'))
print('-' * 92)
groups = {}
for w in words:
    vp, vk = be(P, w), be(K, w)
    tag = 'dynEnt' if w in DYNENT else None
    # region bucket by offset megabyte
    line = '0x%08X  %08X    %08X   | P:[%s]  K:[%s]' % (w, vp, vk, decode(vp), decode(vk))
    if tag == 'dynEnt':
        line += '  <== dynEnt-null'
    print(line)
    key = 'dynEnt' if w in DYNENT else 'other'
    groups.setdefault(key, []).append(w)

print('\ngroups:', {k: len(v) for k, v in groups.items()})
print('\nstomp region for reference: 0x%08X..0x%08X' % (STOMP_LO, STOMP_HI))
