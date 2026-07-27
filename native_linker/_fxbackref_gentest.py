"""Synthetic generality battery for fx_backref_fix (FIX 6).

No zone files, no answer key, identity rt. Builds minimal FxEffectDef bodies and
asserts the refusal contract and the alignment predicate on shapes that the two
real maps (skate/raid) do not exercise.

  python _fxbackref_gentest.py
"""
import struct
import sys

sys.path.insert(0, '.')
import fx_backref_fix as FBF

FOLLOW = 0xFFFFFFFF
ED, HDR = 292, 76
ok = []


def check(label, cond, detail=''):
    print('  [%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))
    ok.append(bool(cond))


def expect_refuse(label, fn, needle):
    try:
        fn()
    except FBF.Refuse as ex:
        hit = needle.lower() in str(ex).lower()
        check(label, hit, '' if hit else '(message lacked %r: %s)' % (needle, ex))
        return
    except Exception as ex:
        check(label, False, '(wrong exception %s: %s)' % (type(ex).__name__, ex))
        return
    check(label, False, '(no refusal)')


def build(elems, name=b'fx/test', inline_name=True):
    """elems: [(etype, vcount, visuals_word)] -> (zone, [(body, len)]).

    Every elem gets velSamples/visSamples NULL and, when visuals is FOLLOW and
    etype<=6, a minimal 104-B inline Material with a FOLLOW name.
    """
    pad = b'\x00' * 64                      # leading pad so body != 0
    b = bytearray()
    b += struct.pack('>I', FOLLOW if inline_name else 0xA0001001)   # name
    b += b'\x00' * 4                        # flags/prio
    b += struct.pack('>hhh', len(elems), 0, 0)
    b += b'\x00' * 2
    b += b'\x00' * 12                       # totalSize/loop/nonloop
    b += struct.pack('>I', FOLLOW)          # elemDefs
    b += b'\x00' * (HDR - len(b))
    assert len(b) == HDR
    if inline_name:
        b += name + b'\x00'
    tails = []
    for (et, vc, vis) in elems:
        e = bytearray(b'\x00' * ED)
        e[184] = et
        e[185] = vc
        struct.pack_into('>I', e, 196, vis)
        b += e
        if vis == FOLLOW and et <= 6 and vc <= 1:
            m = bytearray(b'\x00' * 104)
            struct.pack_into('>I', m, 0, FOLLOW)        # name -> inline string
            tails.append(bytes(m) + b'mtl\x00')
        else:
            tails.append(b'')
    for t in tails:
        b += t
    zone = bytearray(pad + bytes(b))
    return zone, [(len(pad), len(b))]


ident = lambda d: d
AL = lambda off: 0xA0000000 + off + 1       # identity rt => payload == off

print('=== fx_backref_fix synthetic battery ===')

# 1. aligned pointer alias -> no violation
z, fx = build([(0, 1, FOLLOW), (0, 1, AL(400))])
r = FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_AUDIT, verbose=False)
check('1. aligned pointer alias is not a violation', r['misaligned'] == 0)

# 2. MISALIGNED pointer alias -> violation
z, fx = build([(0, 1, FOLLOW), (0, 1, AL(401))])
r = FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_AUDIT, verbose=False)
check('2. misaligned pointer alias IS a violation', r['misaligned'] == 1)

# 3. AUDIT never writes
z, fx = build([(0, 1, FOLLOW), (0, 1, AL(401))])
snap = bytes(z)
FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_AUDIT, verbose=False)
check('3. AUDIT leaves the zone byte-identical', bytes(z) == snap)

# 4. elemType 10/12 are const char* -> ANY alignment is legal
z, fx = build([(0, 1, FOLLOW), (12, 1, AL(401)), (10, 1, AL(403))])
r = FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_AUDIT, verbose=False)
check('4. SOUND/RUNNER string visuals are exempt from alignment',
      r['misaligned'] == 0, '(%d string visuals seen)' % r['aliases'])

# 5. FOLLOW / NULL words are untouched and uncounted
z, fx = build([(0, 1, FOLLOW), (0, 1, 0)])
r = FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_AUDIT, verbose=False)
check('5. FOLLOW and NULL are not aliases', r['aliases'] == 0)

# 6. a word that is neither sentinel nor block-5 alias -> Refuse
z, fx = build([(0, 1, FOLLOW), (0, 1, 0x12345678)])
expect_refuse('6. non-sentinel non-alias word refuses',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, ident, 0,
                                          mode=FBF.MODE_AUDIT, verbose=False),
              'neither a sentinel')

# 7. refusal leaves the zone byte-identical
z, fx = build([(0, 1, FOLLOW), (0, 1, 0x12345678)])
snap = bytes(z)
try:
    FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_AUDIT, verbose=False)
except FBF.Refuse:
    pass
check('7. zone untouched after a refusal', bytes(z) == snap)

# 8. walk-end vs body-len disagreement -> Refuse (the walker/emitter gate).
# Declare the body LONGER than its real content so the walk ends early; a short
# declaration instead trips the overrun guard (also a refusal, different branch).
z, fx = build([(0, 1, FOLLOW)])
z += b'\x00' * 16
bad = [(fx[0][0], fx[0][1] + 8)]
expect_refuse('8. walk end != emitted body end refuses',
              lambda: FBF.fix_fx_backrefs(bytearray(z), bad, ident, 0,
                                          mode=FBF.MODE_AUDIT, verbose=False),
              'walk ended')

# 9. implausible elem count -> Refuse
z, fx = build([(0, 1, FOLLOW)])
struct.pack_into('>h', z, fx[0][0] + 8, 9999)
expect_refuse('9. implausible elemDef count refuses',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, ident, 0,
                                          mode=FBF.MODE_AUDIT, verbose=False),
              'implausible')

# 10. unterminated inline name is bounded by span_end -> Refuse, no overrun.
# Fill the WHOLE post-header body with non-NUL so no NUL exists inside the span,
# and append a NUL AFTER the body so an unbounded scan would find one and drift
# into the next asset (that is the failure the bound exists to prevent).
z, fx = build([(0, 1, 0)], name=b'x' * 12)          # visuals NULL -> no material tail
body, ln = fx[0]
for i in range(body + HDR, body + ln):
    z[i] = 0x41
z += b'\x00' * 8                                    # the "next asset" NUL
expect_refuse('10. unterminated inline name refuses (bounded scan)',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, ident, 0,
                                          mode=FBF.MODE_AUDIT, verbose=False),
              'no NUL before the body end')

# 11. MODE_REFUSE raises on a violation; MODE_AUDIT does not
z, fx = build([(0, 1, FOLLOW), (0, 1, AL(401))])
expect_refuse('11. MODE_REFUSE raises on a misaligned alias',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, ident, 0,
                                          mode=FBF.MODE_REFUSE, verbose=False),
              'MISALIGNED')

# 12. MODE_REPAIR binds to the nearest EARLIER same-type donor
z, fx = build([(0, 1, FOLLOW), (0, 1, AL(401))])
r = FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_REPAIR, verbose=False)
body = fx[0][0]
donor = body + HDR + len(b'fx/test\x00') + 0 * ED + 196
site = body + HDR + len(b'fx/test\x00') + 1 * ED + 196
got = struct.unpack_from('>I', z, site)[0]
check('12. repair bound the site to the earlier same-type donor slot',
      got == AL(donor), '(got %08X want %08X)' % (got, AL(donor)))
check('12b. repair reports 1 correction', r['corrected'] == 1)

# 13. repair is idempotent
before = bytes(z)
r2 = FBF.fix_fx_backrefs(z, fx, ident, 0, mode=FBF.MODE_REPAIR, verbose=False)
check('13. repair is idempotent', r2['corrected'] == 0 and bytes(z) == before)

# 14. repair is size-neutral
check('14. repair is size-neutral', len(z) == len(before))

# 15. NO earlier same-type donor -> Refuse (never bind forward)
z, fx = build([(0, 1, AL(401)), (0, 1, FOLLOW)])
expect_refuse('15. no earlier same-type donor refuses (backward-refs rule)',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, ident, 0,
                                          mode=FBF.MODE_REPAIR, verbose=False),
              'no EARLIER same-type')

# 16. donor of a DIFFERENT elemType is never used
z, fx = build([(4, 1, FOLLOW), (0, 1, AL(401))])
expect_refuse('16. cross-elemType donor refused',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, ident, 0,
                                          mode=FBF.MODE_REPAIR, verbose=False),
              'no inline Material for elemType')

# 17. an rt that breaks alignment -> Refuse rather than emit a bad pointer
z, fx = build([(0, 1, FOLLOW), (0, 1, AL(401))])
expect_refuse('17. rt that misaligns the donor refuses',
              lambda: FBF.fix_fx_backrefs(bytearray(z), fx, lambda d: d + 1, 0,
                                          mode=FBF.MODE_REPAIR, verbose=False),
              'DOES NOT PRESERVE 4-ALIGNMENT')

# 18. bytes input is rejected (pack_into on bytes fails obscurely otherwise)
z, fx = build([(0, 1, FOLLOW)])
try:
    FBF.fix_fx_backrefs(bytes(z), fx, ident, 0, verbose=False)
    check('18. bytes input rejected', False)
except TypeError:
    check('18. bytes input rejected with TypeError', True)

# 19. empty fx list is a SKIP, not a refusal
r = FBF.fix_fx_backrefs(bytearray(b'\x00' * 64), [], ident, 0, verbose=False)
check('19. no FX bodies -> skip', r['status'] == 'no-fx')

# 20. control for check 10: prove the test discriminates -- an UNBOUNDED scan of
# the same buffer really would adopt a NUL from beyond the body.
z, fx = build([(0, 1, 0)], name=b'x' * 12)
body, ln = fx[0]
for i in range(body + HDR, body + ln):
    z[i] = 0x41
z += b'\x00' * 8
unb = bytes(z).find(b'\x00', body + HDR)
check('20. control: an unbounded scan WOULD leave the body (test discriminates)',
      unb >= body + ln, '(unbounded NUL at %d, body ends %d)' % (unb, body + ln))

print('\n%d/%d checks passed' % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
