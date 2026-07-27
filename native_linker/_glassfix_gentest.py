"""Generality tests for glassdef_alias_fix — the shapes no real sample exercises.

_glassfix_validate.py scores the module against the three real skate zones, but all of
them carry the SAME shape: inline asset name, one inline GlassDef owned by glass 0, one
shared alias. This file covers what those samples cannot reach:

  1. two DISTINCT block-5 aliases      -> must Refuse, never collapse (the 2026-07-20 hole)
  2. gbase == 56, name NOT inline      -> the untested else-branch of glasses_layout
  3. unterminated inline name          -> bounded scan must Refuse, not run off the asset
  4. span_end bound vs. a next-asset NUL -> must not adopt a NUL past the body
  5. uniform alias                     -> still fixed (guard is not over-tight)
  6. already-correct + idempotence     -> no-op, no refusal

Synthetic bodies, no zone files, no answer key. Run: python _glassfix_gentest.py
"""
import struct
import glassdef_alias_fix as GF

AE = 1000            # pretend assets_end
BODY = 4096          # pretend Glasses body file offset
NAME = b'maps/mp_synth.d3dbsp\x00'


def rt(dom):
    """Identity runtime map: rt(file_off - assets_end) -> that same domain value."""
    return dom


def build(num, inline_name=True, terminate=True, tail=b'', fill=0):
    """Emit a minimal Glasses body: header, [name], glass array, one inline GlassDef.

    `fill` is the payload filler. It must be NON-ZERO for the unterminated-name tests:
    with the default zero fill the very next byte after a name missing its NUL is itself
    a NUL, so the scan terminates immediately and the bound is never reached."""
    hdr = bytearray(56)
    struct.pack_into('>I', hdr, 0, GF.FOLLOW if inline_name else 0)
    struct.pack_into('>I', hdr, 4, num)              # numGlasses
    struct.pack_into('>I', hdr, 8, GF.FOLLOW)        # glasses array present
    nm = b''
    if inline_name:
        nm = NAME if terminate else NAME.replace(b'\x00', b'X')
    arr = bytearray(bytes([fill]) * (num * GF.GLASS_STRIDE))
    gd = bytearray(bytes([fill]) * 64)
    struct.pack_into('>I', gd, 0, GF.FOLLOW)         # GlassDef record header
    zone = bytearray(BODY) + hdr + nm + arr + gd + tail
    gbase = BODY + 56 + len(nm)
    return zone, gbase, BODY + 56 + len(nm) + len(arr) + len(gd)


def setw(zone, gbase, i, v):
    struct.pack_into('>I', zone, gbase + i * GF.GLASS_STRIDE + GF.GLASSDEF_OFF, v)


def getw(zone, gbase, i):
    return struct.unpack_from('>I', zone, gbase + i * GF.GLASS_STRIDE + GF.GLASSDEF_OFF)[0]


def want_for(gbase, num):
    return 0xA0000000 + rt(gbase + num * GF.GLASS_STRIDE - AE) + 1


def expect_refuse(label, fn, needle=None):
    try:
        fn()
    except GF.Refuse as ex:
        ok = needle is None or needle in str(ex)
        print('  %-46s %s' % (label, 'PASS' if ok else 'FAIL (message: %s)' % ex))
        return ok
    print('  %-46s FAIL (no refusal)' % label)
    return False


def expect(label, cond, detail=''):
    print('  %-46s %s%s' % (label, 'PASS' if cond else 'FAIL', ' ' + detail if detail else ''))
    return bool(cond)


ok = []
print('1. two DISTINCT b5 aliases must refuse, not collapse')
NUM = 8
zone, gbase, _ = build(NUM)
w = want_for(gbase, NUM)
setw(zone, gbase, 0, GF.FOLLOW)                      # glass 0 owns the inline record
for i in range(1, NUM):
    setw(zone, gbase, i, w - 19)                     # the familiar 19-byte drift
setw(zone, gbase, 5, 0xA5123456)                     # a SECOND, unrelated target
snapshot = bytes(zone)
ok.append(expect_refuse('two distinct aliases -> Refuse',
                        lambda: GF.fix_glassdef_aliases(zone, BODY, rt, AE, verbose=False),
                        'DISTINCT'))
ok.append(expect('refusal left the zone untouched', bytes(zone) == snapshot))
ok.append(expect('glass 5 NOT collapsed', getw(zone, gbase, 5) == 0xA5123456,
                 '(%08X)' % getw(zone, gbase, 5)))

print('2. gbase == 56 branch (asset name NOT inline)')
zone, gbase, _ = build(NUM, inline_name=False)
ok.append(expect('gbase == body+56', gbase == BODY + 56, '(got body+%d)' % (gbase - BODY)))
g, n, owners = GF.glasses_layout(zone, BODY)
ok.append(expect('layout decodes num/owners', (g, n) == (gbase, NUM)))
w = want_for(gbase, NUM)
setw(zone, gbase, 0, GF.FOLLOW)
for i in range(1, NUM):
    setw(zone, gbase, i, w - 19)
fixed, delta = GF.fix_glassdef_aliases(zone, BODY, rt, AE, verbose=False)
ok.append(expect('non-inline-name body fixes correctly',
                 (fixed, delta) == (NUM - 1, -19) and
                 all(getw(zone, gbase, i) == w for i in range(1, NUM)),
                 '(fixed=%s delta=%s)' % (fixed, delta)))

print('3. unterminated inline name -> bounded scan refuses')
zone, gbase, _ = build(NUM, terminate=False, fill=0x11)
ok.append(expect_refuse('no NUL within MAX_INLINE_NAME -> Refuse',
                        lambda: GF.glasses_layout(zone, BODY), 'no NUL'))

print('4. span_end stops the scan before the next asset')
# Name unterminated inside THIS asset, but a NUL sits just past the body end. Unbounded
# (or MAX_INLINE_NAME-bounded) scanning adopts it; span_end must not.
zone, gbase, end = build(NUM, terminate=False, tail=b'\x00' * 64, fill=0x11)
ok.append(expect_refuse('span_end -> Refuse instead of next asset NUL',
                        lambda: GF.glasses_layout(zone, BODY, span_end=end), 'span'))
# Control: what the ORIGINAL `zone.index(b'\x00', o)` did on this same body — silently
# adopt the foreign NUL and put gbase past the asset. (The MAX_INLINE_NAME fallback also
# refuses here, so this shows the old behaviour directly rather than through the module.)
old_gbase = bytes(zone).index(b'\x00', BODY + 56) + 1
ok.append(expect('old unbounded scan ran past body end',
                 old_gbase > end, '(gbase body+%d vs end body+%d)'
                 % (old_gbase - BODY, end - BODY)))

print('5/6. uniform alias still fixed; then idempotent')
zone, gbase, end = build(NUM)
w = want_for(gbase, NUM)
setw(zone, gbase, 0, GF.FOLLOW)
for i in range(1, NUM):
    setw(zone, gbase, i, w - 19)
size = len(zone)
fixed, delta = GF.fix_glassdef_aliases(zone, BODY, rt, AE, verbose=False, span_end=end)
ok.append(expect('uniform alias fixed', (fixed, delta) == (NUM - 1, -19),
                 '(fixed=%s delta=%s)' % (fixed, delta)))
ok.append(expect('size-neutral', len(zone) == size))
fixed2, delta2 = GF.fix_glassdef_aliases(zone, BODY, rt, AE, verbose=False, span_end=end)
ok.append(expect('idempotent re-run', (fixed2, delta2) == (0, None),
                 '(fixed=%s delta=%s)' % (fixed2, delta2)))

print()
print('%d/%d PASS' % (sum(ok), len(ok)))
raise SystemExit(0 if all(ok) else 1)
