#!/usr/bin/env python3
"""veh_string_mint.py -- b93 ROUTE A: mint the VehicleDef empty-char* column as
ADDRESS-aliases to ONE already-loaded empty C-string, exactly the shape retail
ships.  PURE: `build()` reads three inputs and returns {struct_offset: new_word}.
It writes nothing, opens nothing, and REFUSES (raises, with the losing datum)
rather than guess.

────────────────────────────────────────────────────────────────── WHY IT EXISTS
b92 broke the weapon wall and the boot now aborts the MAP LOAD in
    Com_Error(1, "Could not load %s file [%s]", "graph", va("vehicle/%s", p))
with p = VehicleDef +1184 `steerGraph`.  MEASURED on b92: 69 of the 79 declared
CSPFT_STRING cells all hold the SAME word 0xA394FE43, a block-5 handle whose
payload rt 60,096,066 has ZERO registrants -- `rt_of_file` calls it
`interp+14402` -- and lands 14 KB inside an XMODEL.  The console really reads
17 bytes of float data there; the b91 dump shows
    guest 0x3AC7FE42 : c6 9b 41 a0 6b e8 41 fc 77 0e 91 ab 40 b1 fa fd 80 00
That binary blob is what the engine pastes into "vehicle/%s".

────────────────────────────────────────────────────────── WHAT RETAIL ACTUALLY DOES
GENUINE zm_transit, its one VehicleDef (file 42,807,121):
    steerGraph +1184 AND accelGraph +1192 BOTH hold 0xA025D541
    -> block 5, rt 2,479,424 (mod4 = 0, EXACTLY ONE registrant)
    -> file 2,535,843, owner WEAPON, walked object ('cstr', 2535843, 2535844)
       i.e. a LENGTH-1 EMPTY STRING; first byte 0x00, byte-before 0x00.
An ADDRESS-alias installs a char* to those "" bytes.  A DEREF-alias would
install the word AT the target, 0x00A00066, which is not a string pointer.
⇒ THE KIND IS ADDRESS.  Retail points 70 cells at ONE "" -- one shared target.

──────────────────────────────────────────── THE LAW THIS MODULE EXISTS TO ENFORCE
⛔⛔ EXACT-REGISTERED ANCHORS ONLY, NEVER AN INTERPOLATED rt.  The obvious
in-asset candidate (b92's own inline "" at turretWeapon +364, whose rt_of_file
is `interp+2628`) reads `c4 3d 52 5f` in real guest memory -- float data.  An
alias minted there would be a FRESH instance of the very defect b93 repairs, and
a purely STRUCTURAL check would have shipped it.  Only the CONTENT read caught
it.  So `build()` will not emit a single word until an anchor oracle certifies
the target, and the pre-flight proves the target's BYTE in guest memory.

⛔ ENDIANNESS: the CONSOLE zone is BIG-endian.  Callers hand `cells` already
decoded as BE words; this module never touches bytes.  A wrong-endian block-5
handle lands back INSIDE the handle range and every consistency check still
passes -- only an absolute count exposes it, which is why `build()` takes an
`expect` count and refuses when the population moves.

⭐ Direction, measured rather than assumed: retail's weapon column ships 781
FORWARD block-5 aliases out of 5,455, and retail's OWN steerGraph target is
itself reached FORWARD (+3,557 B) by three cells at file 2,532,282..2,532,290.
A forward ADDRESS-alias is retail behaviour.  (It has to be: an ADDRESS-alias is
`blockBase5 + rt`, pure arithmetic, no read of the target at fixup time.  Only a
DEREF-alias needs its target already written, and those must run backward.)
"""
import os
import sys

BLOCK_VIRTUAL = 5
HANDLE_LO, HANDLE_HI = 0xA0000001, 0xBFFFFFFF
FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
CSPFT_STRING = 0

__all__ = ['MintRefusal', 'build', 'encode_block_handle', 'decode_block_handle',
           'classify_word', 'string_field_offsets', 'PinnedAnchor', 'LiveAnchor',
           'PIN_B93', 'RETAIL_ORACLE']


class MintRefusal(RuntimeError):
    """Raised instead of guessing.  The message always carries the datum."""


# ─────────────────────────────────────────────────────────────── handle codec
def encode_block_handle(rt, block=BLOCK_VIRTUAL):
    """runtime offset -> the zone word.  `payload = (v-1) & 0x1FFFFFFF`, block
    index = top 3 bits of (v-1)  ⇒  v = (block<<29 | rt) + 1."""
    if not isinstance(rt, int) or isinstance(rt, bool):
        raise MintRefusal('runtime offset must be an int, got %r' % (rt,))
    if not (0 <= rt <= 0x1FFFFFFF):
        raise MintRefusal('runtime offset %d does not fit the 29-bit payload '
                          '(max %d)' % (rt, 0x1FFFFFFF))
    if not (0 <= block <= 7):
        raise MintRefusal('block index %r is not 0..7' % (block,))
    v = ((block << 29) | rt) + 1
    if not (HANDLE_LO <= v <= HANDLE_HI):
        raise MintRefusal('encoded word 0x%08X for block %d rt %d falls OUTSIDE '
                          'the legal handle range 0x%08X..0x%08X'
                          % (v, block, rt, HANDLE_LO, HANDLE_HI))
    b2, rt2 = decode_block_handle(v)
    if (b2, rt2) != (block, rt):
        raise MintRefusal('codec round-trip FAILED: encode(%d, %d) = 0x%08X '
                          'decodes back to (%d, %d)' % (block, rt, v, b2, rt2))
    return v


def decode_block_handle(v):
    w = (v - 1) & 0xFFFFFFFF
    return (w >> 29), (w & 0x1FFFFFFF)


def classify_word(v):
    """What KIND of thing sits in a pointer cell.  Never a value scan."""
    if v == 0:
        return 'NULL'
    if v == FOLLOW:
        return 'FOLLOW'
    if v == INSERT:
        return 'INSERT'
    if HANDLE_LO <= v <= HANDLE_HI:
        return 'ALIAS%d' % decode_block_handle(v)[0]
    return 'RAW'


def string_field_offsets(rows):
    """[(offset, name)] for every declared CSPFT_STRING field, ascending.
    `rows` is the ENGINE cspField table: [(name, offset, type)]."""
    t0 = sorted((o, n) for (n, o, t) in rows if t == CSPFT_STRING)
    offs = [o for o, _n in t0]
    if len(offs) != len(set(offs)):
        dup = sorted(o for o in set(offs) if offs.count(o) > 1)
        raise MintRefusal('DUPLICATE type-0 offsets %r -- the offset key is not '
                          'unique and the mint would be ambiguous' % dup)
    return t0


# ──────────────────────────────────────────────────────────── anchor oracles
class LiveAnchor(object):
    """Certifies a runtime offset against a REAL loader-sim omap (an `Ev`).

    An rt qualifies only if the reverse map resolves it, the FORWARD map calls
    that file offset `exact` (never `interp+N`), and exactly ONE file offset is
    registered there -- a runtime offset with two registrants can give opposite
    verdicts and is AMBIGUOUS, never a PASS."""

    kind = 'LIVE'

    def __init__(self, ev, require_registrants=1):
        self.ev = ev
        self.require_registrants = require_registrants

    def certify(self, rt):
        ev = self.ev
        fo = ev.C.file_of_rt(rt)
        if fo is None:
            raise MintRefusal('rt %d has NO file registrant in the %s omap -- '
                              'there is no exact anchor to alias to'
                              % (rt, getattr(ev, 'key', '?')))
        back, how = ev.C.rt_of_file(fo, align=1)
        if how != 'exact':
            raise MintRefusal('rt %d resolves to file %d but the forward map '
                              'calls that offset %r, not "exact". INTERPOLATED '
                              'ANCHORS ARE FORBIDDEN (the b92 defect is exactly '
                              'this: rt 60096066 = interp+14402).' % (rt, fo, how))
        if back != rt:
            raise MintRefusal('rt %d -> file %d -> rt %d: the maps disagree'
                              % (rt, fo, back))
        n = ev.registrant_count(rt)
        if n != self.require_registrants:
            raise MintRefusal('rt %d has %d registrants (%r), not %d -- a verdict '
                              'resting on more than one registrant is AMBIGUOUS, '
                              'never a PASS'
                              % (rt, n, ev.registrants(rt)[:4],
                                 self.require_registrants))
        if fo >= len(ev.Z):
            raise MintRefusal('rt %d -> file %d is past the end of the zone (%d)'
                              % (rt, fo, len(ev.Z)))
        first = ev.Z[fo]
        if first != 0:
            raise MintRefusal('rt %d -> file %d holds first byte 0x%02X, not NUL '
                              '-- that is not an empty string' % (rt, fo, first))
        return dict(kind='LIVE', rt=rt, fo=fo, how=how, registrants=n,
                    first_byte=first, owner=ev.owner(fo), name=ev.name_at(fo))


class PinnedAnchor(object):
    """Certifies ONE pre-proven runtime offset and REFUSES every other value.

    ⚠ WHY THIS EXISTS AND WHAT IT DOES NOT PROVE.  The emitter runs during
    conversion, BEFORE `loader_sim` has produced an omap -- it has no anchor
    oracle to consult, and inventing one from the artefact under construction
    would be a tautology.  So the emitter carries a PIN whose proof was produced
    OUT OF BAND by `_b93_preflight.py` (an exact-anchor check against the omap
    PLUS a byte read of real guest memory) and re-checked AFTER the bake.  This
    object refuses anything but that pinned value, so a drifting target cannot
    be minted silently -- but it certifies PROVENANCE, not the live zone.  The
    post-bake gate is what closes the loop."""

    kind = 'PINNED'

    def __init__(self, pin):
        for k in ('rt', 'file_offset', 'zone', 'receipt'):
            if k not in pin:
                raise MintRefusal('pin record is missing %r: %r' % (k, pin))
        self.pin = dict(pin)

    def certify(self, rt):
        if rt != self.pin['rt']:
            raise MintRefusal('rt %d is NOT the pinned, pre-proven anchor %d '
                              '(pin proved against zone %r by %r). A PinnedAnchor '
                              'certifies provenance only and REFUSES to vouch for '
                              'an unproven target.'
                              % (rt, self.pin['rt'], self.pin['zone'],
                                 self.pin['receipt']))
        return dict(self.pin, kind='PINNED')


# ────────────────────────────────────────────────── (HG) bake/patch-time derivation
def derive_target_rt(ev, consumer_file_off, require_mod4=0, verbose=False):
    """RE-DERIVE the mint target from the artefact's OWN omap. Never a stored rt.

    ⛔ WHY. A pinned literal is fatal: a sweep of 41 historic bakes found SEVEN
    where the byte at the pinned absolute file offset is `0xA0`, not NUL, while
    every arm-file guard matched bit-for-bit (b71..b77 would have shipped
    `vehicle/\\xa0`). Rule (HG): a pinned anchor is re-derived at bake time in the
    LIVE coordinate system. This is that derivation.

    THE SELECTION RULE, fixed in code so it cannot be chosen after seeing the
    answer. Candidates are objects the ZONE WALK calls a **length-1 `cstr`** --
    i.e. the object IS the empty string. ⛔ NOT "an object whose first byte is
    NUL": that admits struct interiors and is precisely the b92 defect class, in
    which a char* was installed into XMODEL float data that happened to read as
    "". Then: owner WEAPON · the forward map calls it `exact` (never `interp`) ·
    exactly ONE registrant (two give opposite verdicts and are AMBIGUOUS) ·
    `rt % require_mod4 == 0`. Among survivors:
        1. the CLOSEST BACKWARD candidate -- retail's own measured shape
           (genuine reaches its "" backward by 40,271,278 B);
        2. else the CLOSEST FORWARD candidate, minimising the forward distance,
           which is the one property §3.4 flags as argued-but-unobserved.

    -> (rt, info). Raises MintRefusal if nothing qualifies: a mint with no anchor
    must NOT be armed.
    """
    led = dict(cstr1=0, byte_nul=0, weapon=0, placeable=0, exact=0, mod4=0, sole=0)
    cands = []
    for (k, b, e, _d, _w) in ev.objs:
        if k != 'cstr' or e - b != 1:
            continue
        led['cstr1'] += 1
        if ev.Z[b] != 0:
            continue
        led['byte_nul'] += 1
        if ev.owner(b) != 'WEAPON':
            continue
        led['weapon'] += 1
        try:
            rt, how = ev.C.rt_of_file(b, align=1)
        except Exception:
            continue                       # below the omap floor: not placeable
        led['placeable'] += 1
        if how != 'exact':
            continue
        led['exact'] += 1
        if require_mod4 is not None and rt % 4 != require_mod4:
            continue
        led['mod4'] += 1
        if ev.registrant_count(rt) != 1:
            continue
        led['sole'] += 1
        cands.append((rt, b))
    cands.sort()
    consumer_rt, _how = ev.C.rt_of_file(consumer_file_off, align=1)
    back = [c for c in cands if c[0] < consumer_rt]
    fwd = [c for c in cands if c[0] > consumer_rt]
    if back:
        rt, fo = back[-1]
        direction = 'BACKWARD (retail shape)'
    elif fwd:
        rt, fo = fwd[0]
        direction = 'FORWARD (no backward candidate exists in this zone)'
    else:
        raise MintRefusal(
            'NO qualifying empty-cstr anchor in %r: ledger %r. The mint has no '
            'target and MUST NOT be armed -- there is no approximation of an '
            'anchor that is not a guess.' % (getattr(ev, 'key', '?'), led))
    info = dict(rt=rt, file_offset=fo, direction=direction, ledger=led,
                qualifying=len(cands), backward=len(back), forward=len(fwd),
                consumer_rt=consumer_rt, distance=rt - consumer_rt,
                zone=getattr(ev, 'key', '?'))
    if verbose:
        print('[derive] ledger %r' % led)
        print('[derive] %d qualifying (%d back / %d fwd) -> rt %d file %d  %s'
              % (len(cands), len(back), len(fwd), rt, fo, direction))
    return rt, info


# ───────────────────────────────────────────────────────────────── the pins
# The b93 shared target.  Every field here was MEASURED, and every one of them
# is re-checked by `_b93_preflight.py` before a bake and by the post-bake gate
# after one.  ⛔ If the bake stops being a same-size word rewrite, THIS RECORD
# IS VOID -- the runtime offset would move and the pin would name float data.
PIN_B93 = dict(
    rt=126306996,                     # block-5 runtime offset
    file_offset=125115662,            # b92/b91 file offset of the same object
    zone='b92 (md5 93742df987437db50d79c1352a7d6fc8)',
    owner='WEAPON',
    object="('cstr', 125115662, 125115663) -- a LENGTH-1 EMPTY STRING",
    how='exact',                      # rt_of_file, NOT interp
    registrants=1,
    mod4=0,
    guest_addr=0x3EBA4AB4,            # blockBase5 0x37330000 + rt, b91 boot
    guest_first_byte=0x00,
    receipt='_b93_preflight.py',
)

# An INDEPENDENT retail pair used by the selftest: the runtime offset comes from
# decoding the genuine zone's word, the word comes from the genuine zone itself.
# encode() is the thing under test, so this is an oracle and not a tautology.
RETAIL_ORACLE = dict(zone='zm_transit_original (retail Wii U)',
                     field='steerGraph +1184 / accelGraph +1192',
                     rt=2479424, word=0xA025D541, block=5)


# ───────────────────────────────────────────────────────────────────── build
def build(rows, oat, cells, target_rt, anchor_oracle,
          require_mod4=0, allowed_cell_classes=('ALIAS5',), expect=None,
          require_old_words=None, verbose=False):
    """-> {struct_offset: new_word} for ONE VehicleDef.  Pure.

    rows          [(name, offset, type)]   the ENGINE cspField table
    oat           {name: value}            the OAT VEHICLEFILE InfoString
                                           (keys/values str or bytes)
    cells         {offset: word}           the CURRENT console (BE) struct words
    target_rt     int                      the ONE shared "" runtime offset
    anchor_oracle LiveAnchor | PinnedAnchor -- REQUIRED.  There is no default and
                  no bypass: a mint with no anchor oracle is a guess.

    SELECTION RULE (the ratified spec): a cell is minted iff its declared type is
    CSPFT_STRING **and** its OAT InfoString value is the empty string **and** its
    current word is not FOLLOW/INSERT (an inline string is already correct and
    nulling or re-pointing it would DESYNC the stream).
    """
    if anchor_oracle is None:
        raise MintRefusal('anchor_oracle is REQUIRED. EXACT-REGISTERED ANCHORS '
                          'ONLY, NEVER AN INTERPOLATED rt -- a mint with nothing '
                          'to certify the target is exactly the defect b93 repairs.')
    cert = anchor_oracle.certify(target_rt)          # raises with the datum
    if require_mod4 is not None and target_rt % 4 != require_mod4:
        raise MintRefusal('target rt %d has rt %% 4 == %d, required %d. Retail '
                          'ships its VehicleDef "" target at rt 2479424 (mod4=0); '
                          'pass require_mod4=None to accept another alignment '
                          'deliberately.'
                          % (target_rt, target_rt % 4, require_mod4))
    word = encode_block_handle(target_rt, BLOCK_VIRTUAL)

    t0 = string_field_offsets(rows)
    oat_n = _normalise_oat(oat)

    out, skipped, seen_classes = {}, [], {}
    for off, name in t0:
        if name not in oat_n:
            raise MintRefusal('field %r (+%d) is DECLARED CSPFT_STRING by the '
                              'engine table but has NO value in the OAT '
                              'InfoString. REFUSING to guess whether it is empty.'
                              % (name, off))
        if off not in cells:
            raise MintRefusal('field %r (+%d) is declared but the caller supplied '
                              'no cell word for that offset (cells has %d entries, '
                              'range %d..%d)'
                              % (name, off, len(cells),
                                 min(cells) if cells else -1,
                                 max(cells) if cells else -1))
        cur = cells[off]
        cls = classify_word(cur)
        seen_classes[cls] = seen_classes.get(cls, 0) + 1
        if oat_n[name] != b'':
            skipped.append((off, name, 'OAT-NON-EMPTY', cur))
            continue
        if cls in ('FOLLOW', 'INSERT'):
            # an inline string: already correct, and nulling a FOLLOW element
            # DESYNCS the stream.  Never a mint target.
            skipped.append((off, name, 'INLINE-' + cls, cur))
            continue
        if cls not in allowed_cell_classes:
            raise MintRefusal('field %r (+%d) is OAT-empty and not inline, but its '
                              'current word 0x%08X classifies as %r, which is not '
                              'in the calibrated set %r. This is a CELL CLASS THE '
                              'MINT HAS NEVER BEEN CALIBRATED ON -- refusing.'
                              % (name, off, cur, cls, tuple(allowed_cell_classes)))
        if cur == word:
            skipped.append((off, name, 'ALREADY-MINTED', cur))
            continue
        if require_old_words is not None and cur not in require_old_words:
            raise MintRefusal(
                'field %r (+%d) currently holds 0x%08X, which is not in the '
                'calibrated old-word set %r. The emitter runs BEFORE loader_sim '
                'exists, so the ONLY thing that can tell it "this is the zone the '
                'pin was proven against" is the exact word the relocator produced. '
                'A different word means a different bake -- REFUSING.'
                % (name, off, cur,
                   sorted('0x%08X' % w for w in require_old_words)))
        out[off] = word

    if expect is not None and len(out) != expect:
        raise MintRefusal('mint selected %d cells, caller expected %d. An ABSOLUTE '
                          'COUNT is the only thing that exposes a silently moved '
                          'population (or a wrong-endian read, which stays inside '
                          'the handle range and passes every consistency check). '
                          'Selected offsets: %r' % (len(out), expect, sorted(out)))
    if verbose:
        print('[mint] target rt %d -> word 0x%08X (anchor %s: %r)'
              % (target_rt, word, cert['kind'], cert))
        print('[mint] declared type-0 fields %d ; minted %d ; left alone %d'
              % (len(t0), len(out), len(skipped)))
        print('[mint] cell-class histogram over all declared fields: %r'
              % sorted(seen_classes.items()))
        for row in skipped:
            print('[mint]   untouched +%-6d %-26s %-16s 0x%08X'
                  % (row[0], row[1], row[2], row[3]))
    build.last = dict(target_rt=target_rt, word=word, cert=cert, declared=len(t0),
                      minted=len(out), skipped=skipped, classes=seen_classes)
    return out


def _normalise_oat(oat):
    out = {}
    for k, v in oat.items():
        k = k.decode('latin1') if isinstance(k, bytes) else k
        v = v.encode('latin1') if isinstance(v, str) else v
        if k in out and out[k] != v:
            raise MintRefusal('OAT key %r appears twice with different values '
                              '%r / %r' % (k, out[k], v))
        out[k] = v
    return out


# ──────────────────────────────────────────────── the ARM FILE (bake wiring)
# ⭐ WHY AN ARM FILE AND NOT A CONSTANT IN THE EMITTER.  The emitter runs during
# conversion, long before `loader_sim` produces an omap, so it cannot derive the
# target itself and cannot re-derive the engine table or the OAT oracle without
# dragging an RPL parse and an Unlinker subprocess into every bake.  So the
# pre-flight -- which DOES have the omap and the dump -- freezes exactly the
# three inputs `build()` takes, together with the provenance of each, and the
# emitter consumes that.  ⭐ STAMP EFFECTIVE PARAMETERS, NOT JUST FILES: the arm
# file carries the rpl md5, the oracle md5, the zone md5 and the receipt path, so
# a bake log can prove WHICH mint ran, and `--exact`-class silent drops (b83) are
# impossible here because an absent arm file means the mint DID NOT RUN and says so.
ARM_ENV = 'T6_VEH_STRING_MINT'
ARM_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '_b93_veh_mint_arm.json')
ARM_SCHEMA = 1


def load_arm(path=None):
    """-> the frozen arm spec. REFUSES on a missing/short/foreign file."""
    import json
    p = path or ARM_DEFAULT
    if not os.path.exists(p):
        raise MintRefusal('no arm file at %s -- the mint is NOT armed. Generate '
                          'one with `python _b93_preflight.py` (which proves the '
                          'target in guest memory first).' % p)
    spec = json.load(open(p, 'r', encoding='utf-8'))
    if spec.get('schema') != ARM_SCHEMA:
        raise MintRefusal('arm file %s has schema %r, this module speaks %r'
                          % (p, spec.get('schema'), ARM_SCHEMA))
    for k in ('rows', 'oat', 'target_rt', 'expect', 'require_old_words',
              'pc_zone_len', 'pin', 'provenance'):
        if k not in spec:
            raise MintRefusal('arm file %s is missing %r' % (p, k))
    spec['rows'] = [tuple(r) for r in spec['rows']]
    spec['require_old_words'] = set(int(w, 16) if isinstance(w, str) else w
                                    for w in spec['require_old_words'])
    spec['_path'] = p
    return spec


def build_from_arm(spec, cells, pc_zone_len=None, verbose=False):
    """`build()` driven by a frozen arm spec, plus the guards an emitter needs
    because it cannot see the zone it is building."""
    if pc_zone_len is not None and spec.get('pc_zone_len') != pc_zone_len:
        raise MintRefusal(
            'PC SOURCE MISMATCH: this arm file was proven against a PC zone of '
            '%s bytes, the converter was handed one of %s. The pinned runtime '
            'offset means NOTHING in another zone -- REFUSING. (This is the guard '
            'that stops a zm_nuked arm file being applied to the zm_transit bus.)'
            % (spec.get('pc_zone_len'), pc_zone_len))
    # ⛔ b106: require_old_words is RETIRED ON THIS PATH, and the obituary matters.
    # It existed because the b93 mint ran as a POST-BAKE PATCH against a zone it could not
    # otherwise identify: the exact relocated word was the only evidence that "this is the
    # bake the pin was proven against". Emitted at BAKE TIME there is nothing to prove —
    # we ARE the bake, and the words in `cells` are ones this same run just relocated.
    # Keeping it would refuse every future build for the crime of not being b92: it is a
    # pin on a value that legitimately changes whenever the emitter's input changes, and it
    # already refused b105 (0xA3962243 vs the calibrated 0xA394FE43).
    # ⚠ WHAT REPLACES IT IS NOT NOTHING — it is a POST-BAKE CONTENT GATE (produce_container,
    # zm_nuked path): the minted target must resolve to a NUL-bounded EMPTY STRING, the shape
    # genuine's VehicleDef column points at (zm_transit, x74, measured). That gate scores what
    # the value MEANS instead of which build produced it, so a target that drifts onto garbage
    # refuses while a target that merely moves does not.
    # ⛔ `spec['require_old_words']` is deliberately left in the arm file: it is still the right
    # guard for the post-bake patch path, which may be re-run for forensics.
    return build(spec['rows'], spec['oat'], cells, spec['target_rt'],
                 PinnedAnchor(spec['pin']), require_mod4=0,
                 expect=spec['expect'],
                 require_old_words=None, verbose=verbose)


def verify_pin(ev, pin=PIN_B93, require_mod4=0):
    """POST-BAKE gate: re-prove a pin against a freshly-walked zone.
    Returns the certification dict; raises MintRefusal with the datum."""
    cert = LiveAnchor(ev).certify(pin['rt'])
    if cert['fo'] != pin['file_offset']:
        raise MintRefusal('pin rt %d now resolves to file %d, but it was proven '
                          'against file %d. THE ZONE MOVED -- Route A assumed a '
                          'same-size word rewrite and that assumption is now VOID.'
                          % (pin['rt'], cert['fo'], pin['file_offset']))
    if require_mod4 is not None and pin['rt'] % 4 != require_mod4:
        raise MintRefusal('pin rt %d mod4 = %d' % (pin['rt'], pin['rt'] % 4))
    return cert


# ──────────────────────────────────────────────────────────────── SELFTEST
def _synthetic():
    """A miniature of the MEASURED b92 reality: 79 declared type-0 fields, of
    which 69 hold the interpolated alias 0xA394FE43 and are OAT-empty, 6 are
    FOLLOW (turretWeapon "" plus the 5 real button strings) and 4 hold
    0xA394FE58 against an OAT value of "none"."""
    rows = [('type', 4, 18)]                       # a non-string field, ignored
    oat, cells = {'type': '18'}, {4: 0x00000012}
    off = 100
    for i in range(69):
        nm = 'empty%02d' % i
        rows.append((nm, off, CSPFT_STRING))
        oat[nm] = ''
        cells[off] = 0xA394FE43
        off += 4
    rows.append(('turretWeapon', off, CSPFT_STRING))
    oat['turretWeapon'] = ''
    cells[off] = FOLLOW
    off += 4
    for nm, val in (('gasButton', 'BUTTON_LTRIG'), ('reverseBrakeButton', 'BUTTON_B'),
                    ('handBrakeButton', 'none'), ('attackButton', 'BUTTON_RTRIG'),
                    ('attackSecondaryButton', 'BUTTON_RSHLDR')):
        rows.append((nm, off, CSPFT_STRING))
        oat[nm] = val
        cells[off] = FOLLOW
        off += 4
    for nm in ('boostButton', 'moveUpButton', 'moveDownButton', 'switchSeatButton'):
        rows.append((nm, off, CSPFT_STRING))
        oat[nm] = 'none'
        cells[off] = 0xA394FE58
        off += 4
    return rows, oat, cells


def selftest(verbose=True):
    ok, fail = 0, []

    def check(label, cond, datum=''):
        nonlocal ok
        if cond:
            ok += 1
            if verbose:
                print('  PASS  %s' % label)
        else:
            fail.append('%s   LOSING DATUM: %s' % (label, datum))
            print('  FAIL  %s   LOSING DATUM: %s' % (label, datum))

    def refuses(label, fn, needle=''):
        nonlocal ok
        try:
            fn()
        except MintRefusal as e:
            if needle and needle.lower() not in str(e).lower():
                fail.append('%s refused but for the wrong reason: %s' % (label, e))
                print('  FAIL  %s   wrong reason: %s' % (label, e))
                return
            ok += 1
            if verbose:
                print('  PASS  %s   (refused: %s)' % (label, str(e)[:88]))
        except Exception as e:                                   # noqa: BLE001
            fail.append('%s raised %s, not MintRefusal: %s'
                        % (label, type(e).__name__, e))
            print('  FAIL  %s raised %s: %s' % (label, type(e).__name__, e))
        else:
            fail.append('%s DID NOT REFUSE' % label)
            print('  FAIL  %s DID NOT REFUSE' % label)

    print('=' * 78)
    print('veh_string_mint SELFTEST')
    print('=' * 78)

    print('\n-- T1 handle codec, round trip')
    bad = [rt for rt in (0, 4, 2479424, 126306996, 0x1FFFFFFC)
           if decode_block_handle(encode_block_handle(rt)) != (5, rt)]
    check('encode/decode round-trips for 5 runtime offsets', not bad, bad)

    print('\n-- T2 RETAIL ORACLE (independent: rt and word both come from the '
          'genuine zone; encode() is what is under test)')
    got = encode_block_handle(RETAIL_ORACLE['rt'], RETAIL_ORACLE['block'])
    check('encode(rt %d) == retail steerGraph word 0x%08X'
          % (RETAIL_ORACLE['rt'], RETAIL_ORACLE['word']),
          got == RETAIL_ORACLE['word'], '0x%08X' % got)
    b, rt = decode_block_handle(RETAIL_ORACLE['word'])
    check('decode(0x%08X) == (block 5, rt %d)'
          % (RETAIL_ORACLE['word'], RETAIL_ORACLE['rt']),
          (b, rt) == (5, RETAIL_ORACLE['rt']), (b, rt))

    print('\n-- T3 the b93 pin encodes to the word the pre-flight will emit')
    # 126,306,996 = 0x07874AB4 ; (5<<29)|0x07874AB4 = 0xA7874AB4 ; +1 -> 0xA7874AB5.
    # Cross-check against the guest address the b91 dump was read at:
    # blockBase5 0x37330000 + 0x07874AB4 = 0x3EBA4AB4 = PIN_B93['guest_addr'].
    w = encode_block_handle(PIN_B93['rt'])
    check('encode(pin rt %d) == 0xA7874AB5' % PIN_B93['rt'], w == 0xA7874AB5,
          '0x%08X' % w)
    check('pin rt + blockBase5 0x37330000 == the guest address it was read at',
          0x37330000 + PIN_B93['rt'] == PIN_B93['guest_addr'],
          '0x%08X' % (0x37330000 + PIN_B93['rt']))

    print('\n-- T4 build() over a synthetic miniature of the measured b92 shape')
    rows, oat, cells = _synthetic()
    anchor = PinnedAnchor(PIN_B93)
    subs = build(rows, oat, cells, PIN_B93['rt'], anchor, expect=69)
    check('79 declared type-0 fields seen', len(string_field_offsets(rows)) == 79,
          len(string_field_offsets(rows)))
    check('exactly 69 cells minted', len(subs) == 69, len(subs))
    check('every minted word is the ONE shared target',
          set(subs.values()) == {w}, sorted('%08X' % x for x in set(subs.values())))
    inline_offs = {o for (o, n, r, _c) in build.last['skipped'] if r.startswith('INLINE')}
    check('exactly 1 cell is skipped as OAT-empty-but-INLINE (turretWeapon)',
          len(inline_offs) == 1, sorted(inline_offs))
    nonempty = {o for (o, n, r, _c) in build.last['skipped'] if r == 'OAT-NON-EMPTY'}
    check('the 9 OAT-non-empty cells are untouched', len(nonempty) == 9,
          sorted(nonempty))
    check('10 declared cells are left alone in total (69 + 10 == 79)',
          len(build.last['skipped']) == 10 and len(subs) + 10 == 79,
          len(build.last['skipped']))
    # by WORD CLASS, not by skip-reason: no cell that currently holds FOLLOW may
    # be minted -- nulling or re-pointing a FOLLOW element DESYNCS the stream.
    follow_offs = {o for o, w2 in cells.items() if w2 in (FOLLOW, INSERT)}
    check('all 6 FOLLOW-word cells are untouched by the mint',
          len(follow_offs) == 6 and not (set(subs) & follow_offs),
          sorted(set(subs) & follow_offs) or len(follow_offs))
    check('build() did not mutate the caller\'s cells',
          cells == _synthetic()[2], 'cells were mutated')
    check('build() is deterministic',
          build(rows, oat, cells, PIN_B93['rt'], anchor, expect=69) == subs,
          'second call differed')

    print('\n-- T5..T11 the refusals (each must raise MintRefusal, with the datum)')
    refuses('no anchor oracle',
            lambda: build(rows, oat, cells, PIN_B93['rt'], None), 'anchor_oracle')
    refuses('an UNPROVEN target rt',
            lambda: build(rows, oat, cells, PIN_B93['rt'] + 4, anchor), 'pinned')
    refuses('a misaligned target rt',
            lambda: build(rows, oat, cells, PIN_B93['rt'] + 1,
                          PinnedAnchor(dict(PIN_B93, rt=PIN_B93['rt'] + 1))),
            'mod4' if False else '% 4')
    bad_oat = dict(oat)
    bad_oat.pop('empty07')
    refuses('a declared field with NO OAT value',
            lambda: build(rows, bad_oat, cells, PIN_B93['rt'], anchor), 'no value')
    bad_cells = dict(cells)
    bad_cells.pop(100 + 4 * 7)
    refuses('a declared field with no cell word',
            lambda: build(rows, oat, bad_cells, PIN_B93['rt'], anchor),
            'no cell word')
    raw_cells = dict(cells)
    raw_cells[100 + 4 * 7] = 0x12345678
    refuses('an OAT-empty cell in an UNCALIBRATED class',
            lambda: build(rows, oat, raw_cells, PIN_B93['rt'], anchor),
            'never been calibrated')
    refuses('a population that moved (expect mismatch)',
            lambda: build(rows, oat, cells, PIN_B93['rt'], anchor, expect=70),
            'absolute count')
    dup_rows = rows + [('shadow', 100, CSPFT_STRING)]
    refuses('duplicate declared type-0 offsets',
            lambda: build(dup_rows, oat, cells, PIN_B93['rt'], anchor), 'duplicate')
    refuses('a runtime offset too big for the 29-bit payload',
            lambda: encode_block_handle(0x20000000), '29-bit')

    print('\n-- T12 NULL is a distinct class and is NOT swept in by default')
    null_cells = dict(cells)
    null_cells[100] = 0
    refuses('an OAT-empty cell holding NULL',
            lambda: build(rows, oat, null_cells, PIN_B93['rt'], anchor),
            'never been calibrated')
    subs2 = build(rows, oat, null_cells, PIN_B93['rt'], anchor,
                  allowed_cell_classes=('ALIAS5', 'NULL'), expect=69)
    check('...but is mintable when the caller OPTS IN explicitly',
          len(subs2) == 69 and subs2[100] == w, len(subs2))

    print('\n' + '=' * 78)
    print('SELFTEST: %d passed, %d failed' % (ok, len(fail)))
    for f in fail:
        print('   FAILED: %s' % f)
    print('=' * 78)
    return not fail


if __name__ == '__main__':
    sys.exit(0 if selftest() else 1)
