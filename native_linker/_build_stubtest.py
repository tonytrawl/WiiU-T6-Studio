"""CAUSAL TEST: is the matmem GfxImage* stub type-confusion the R_SkinStaticModelsCamera crash?

Ours stubs 2,810 GfxImage* slots to 0xA00272D4, which decodes into a MaterialTechniqueSet
arg table -> a GfxImage read out of techset argument bytes -> garbage texture pointer.
The KEY stubs to 0xA0046111, which is type-correct (an inline image inside an XModel body),
and the key boots and renders.

Swap ours for the key's stub value ONLY. Everything else stays as-is.
  clears crash -> stub type-confusion is causal; then implement the KEY-FREE version
                  (resolve a real in-zone GfxImage body structurally, + fix the
                  rt(earliest-64) domain bug in colormap_rebind.py:148)
  same crash   -> stub is innocent; remaining suspects are FX (862 B) and Glasses (59 B)

Built on xm2 (GameWorldMp + XModel correct, techsets deliberately NOT reconciled since
the tsdiag boot proved TS/unsatisfied-demand innocent)."""
import struct, hashlib, sys
sys.path.insert(0,'.'); sys.path.insert(0,'../WiiU_FF_Studio')
import wiiu_ff, alloc_events

OURS_STUB = 0xA00272D4
KEY_STUB  = 0xA0046111
z = bytearray(open('mp_skate_xm2.zone','rb').read())
K = open('mp_skate_gfxtail46.zone','rb').read()
N0 = len(z)

# byte-granular (rule: the stub is NOT 4-aligned everywhere; a 4-aligned scan undercounts 4x)
old = struct.pack('>I', OURS_STUB); new = struct.pack('>I', KEY_STUB)
n = 0; i = 0
while True:
    i = z.find(old, i)
    if i < 0: break
    z[i:i+4] = new; n += 1; i += 4
print('stub occurrences replaced 0x%08X -> 0x%08X : %d' % (OURS_STUB, KEY_STUB, n))
assert len(z) == N0, 'size changed'

ev = alloc_events.clipmap_events(bytes(z), 84512493, '>')
ev = ev[0] if isinstance(ev, tuple) else ev
print('GATE clipmap_events end =', ev, '(MUST be 89584099)')
assert int(ev) == 89584099
print('whole-zone residual: %d' % sum(1 for x in range(min(len(z),len(K))) if z[x]!=K[x]))

open('mp_skate_stubtest.zone','wb').write(bytes(z))
ff = wiiu_ff.pack(bytes(z),'mp_skate')
open('mp_skate_stubtest.ff','wb').write(ff)
print('zone md5 %s' % hashlib.md5(bytes(z)).hexdigest())
print('ff   md5 %s (%d bytes)' % (hashlib.md5(ff).hexdigest(), len(ff)))
