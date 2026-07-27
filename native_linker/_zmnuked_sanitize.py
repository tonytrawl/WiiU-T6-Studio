"""zm_nuked STALE-HEAP SANITIZER (boots 10-18 passes, consolidated 2026-07-26).

WHY THIS EXISTS: the PC source zone is a Plutonium MEMORY DUMP. Every pointer
field the dumper did not re-link holds a stale heap word, which our converter
faithfully relocates into a garbage alias. No runtime-layout model can fix that
(it is bad SOURCE DATA, not bad addressing), so these passes must be re-applied
after any rebuild. They were originally run as ad-hoc inline snippets and were
LOST on the first rebuild — hence this file.

Runs AFTER: _zmnuked_camofix -> _zmnuked_camosets_null2 ->
            _zmnuked_camo_interior_null -> _zmnuked_weapon_strfix

  PASS B  weapDef pointer fields that are NULL -> the zero-record alias. The
          engine derefs several of these without a null check (boot-15 crash).
  PASS A  RAW garbage sweep: any weapon-family field whose value is not in
          {FOLLOW, INSERT, 0, EMPTY, ZEROREC, low-rt alias} is rot — strings to
          "", pointers to the zero record. Earlier passes only filtered values
          with the 0xA alias tag and missed RAW stale words (boot-18: an 0x00BFE4E0
          in szDualWieldWeaponName). Applied with an ITERATIVE MINIMAL REVERT:
          apply everything, re-walk under the LAYOUT-DRIVEN gate, and binary-
          search any drifting weapon's plan for the maximal safe subset.
          ⚠ BOOT-34 BUG (fixed): this used to (a) gate with a bare
          LS.simulate(), whose signature-scanning weapon delimiter moves when
          VALUES change, and (b) revert the whole weapon on drift. Together
          they stranded all 123 rot words of rpd_upgraded_zm — including
          weapDef+0, the "standing WeapDef" name — and boot 33 died on it.
          Under the layout-driven gate every write lands and nothing reverts.
  PASS C  dangling attachment altWeapon names: inline FOLLOW strings naming
          weapons that do not exist in this zone (sf_/gl_ select-fire and
          grenade-launcher variants that live in PC weapon FILES, not as zone
          assets). A FOLLOW string is STRUCTURAL — it cannot be blanked or
          shortened without desyncing the walk — so substitute a SAME-LENGTH
          registered weapon name in place.

Anchors (dump-measured, still valid while the zone LAYOUT is unchanged — every
pass here is value-only and rewalk-gated):
  EMPTY   0xA001BD32  the NUL of StringTable's inline 'defaultweapon_mp'
  ZEROREC 0xA77D18F5  defaultweapon_mp's all-zero attachments[63] array
"""
import struct, sys, hashlib, os, re

sys.path.insert(0, '.')
sys.path.insert(0, os.path.join('..', 'wiiu_ref'))
import loader_sim as LS
import struct_layout, walker as W

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
EMPTY, ZEROREC = 0xA001BD32, 0xA77D18F5
ZONE = 'zm_nuked_authored.zone'

HDR = W.HDR if os.path.isabs(W.HDR) else os.path.normpath(
    os.path.join('..', 'wiiu_ref', W.HDR))
L = struct_layout.Layout(HDR, console=False)
WD_STR = [f['offset'] for f in L.get('WeaponDef')['fields']
          if 'error' not in f and f['is_ptr'] and f.get('base') == 'char']
WD_PTR = [f['offset'] for f in L.get('WeaponDef')['fields']
          if 'error' not in f and f['is_ptr'] and f.get('base') != 'char']
V_STR = [f['offset'] for f in L.get('WeaponVariantDef')['fields']
         if 'error' not in f and f['is_ptr'] and f.get('base') == 'char']
V_SIG = [0x24, 0x28, 0x2C]                       # hideTags, attach*Model
UNIQ_STR = [20, 28, 60, 64] + list(range(256, 312, 4))
UNIQ_PTR = [36, 40, 44, 48, 52, 56, 164, 196, 200, 232, 248, 316, 320, 324, 328]

Z = bytearray(open(ZONE, 'rb').read())
before = hashlib.md5(Z).hexdigest()
# BOOT-34: the rewalk GATE must use the LAYOUT-DRIVEN weapon walk. Called bare,
# loader_sim bounds a console WeaponVariantDef with body_relayout's SIGNATURE
# SCANNER (_weapon_end), which hunts for the next weapon-start pattern -- so a
# pure VALUE change inside a weapon can move the detected boundary and the gate
# condemns a patch that never touched the layout. rt_events_weapon's walk is
# driven by the console struct layout (and agrees 98/98 with the scanner on a
# clean zone), so it is value-stable. Under it all of PASS A's writes land.
import rt_events_exact as RTX
GATE_POL = dict(extra_events=RTX.all_events())
em, spans, _ = LS.simulate(ZONE, verbose=False, policy=GATE_POL)
GOOD = [(t[0], t[3], t[4]) for t in spans]
B = bytes(Z)


def r32(o):
    return struct.unpack_from('>I', Z, o)[0]


# exact rt -> file inverse from OUR OWN emitter map. NOT the measured/composed
# map: that one INTERPOLATES between anchors and will happily hand back an
# offset inside a string region (rule (R)).
import bisect
_INV = sorted((rt, so + 64) for so, rt in em.omap.items())
_INVK = [t[0] for t in _INV]


def rt_to_file(rt):
    j = bisect.bisect_right(_INVK, rt) - 1
    if j < 0:
        return None
    r0, fo = _INV[j]
    return fo + (rt - r0) if rt - r0 < 0x8000 else None


def looks_ascii(fo):
    if fo is None or not (64 <= fo < len(Z)):
        return False
    e = bytes(Z).find(b'\x00', fo, fo + 64)
    return e > fo and all(32 <= c < 127 for c in Z[fo:e])


def looks_ptr_array(fo, n=8):
    if fo is None or not (64 <= fo + 4 * n < len(Z)):
        return False
    return all(ptrspace(struct.unpack_from('>I', Z, fo + 4 * j)[0])
               for j in range(n))


LOWBAND = 0x10000


def ok(v, fileoff=None, isstr=None):
    """⚠ BOOT-37 BUG (fixed): this used to whitelist ANY alias into the first
    64 KB of block 5 unconditionally. An ADDRESS-RANGE whitelist cannot tell a
    genuine backward reference from heap rot that happens to land low --
    python_upgraded_zm carried the stale word 0xA000CCC5 (rt 0xCCC4, inside the
    early string table) in weapDef+4, weapDef+916 AND parentWeaponName, and the
    engine walked that "XModel* array" and dereferenced entry[1] = 0x3B.
    The low band is now only PROVISIONALLY accepted: callers pass the site so
    the target can be VALIDATED BY TYPE (rule (R): validate, never whitelist).
    Called without a site it keeps the old, permissive answer, so any caller
    that has not been updated still behaves as before rather than over-writing.
    """
    if v in (FOLLOW, INSERT, 0, EMPTY, ZEROREC):
        return True
    if (v >> 28) != 0xA:
        return False
    rt = (v - 1) & 0x1FFFFFFF
    if rt >= LOWBAND:
        return False
    if fileoff is None:
        return True
    tgt = rt_to_file(rt)
    return looks_ascii(tgt) if isstr else looks_ptr_array(tgt)


def ptrspace(v):
    return v in (FOLLOW, INSERT, 0) or (v >> 28) == 0xA


def pstr(o, mx=48):
    e = B.find(b'\x00', o, o + mx)
    return e > o and all(32 <= c < 127 for c in B[o:e])


def weapon_sites(s, e):
    """(str_sites, ptr_sites) for one weapon body, incl. inline uniques.

    ⚠ BOOT-22 BUG (fixed): V_SIG (+0x24 hideTags u16*, +0x28 attachViewModel
    XModel*, +0x2C attachWorldModel XModel*) are POINTERS, not strings. Sending
    them to the empty-string alias made the engine deref the STRING BYTES —
    the dword just past that NUL is 0x00322061, which is exactly the value the
    boot-22 host crash faulted on. They belong in ptr_sites, and a pointer
    field must never be given a string target."""
    ss = [s + o for o in V_STR]
    ps = [s + o for o in V_SIG]
    if r32(s + 8) == FOLLOW:
        wd = s + 716
        if r32(s) == FOLLOW:
            wd = B.index(b'\x00', wd) + 1
        ss += [wd + o for o in WD_STR]
        ps += [wd + o for o in WD_PTR]
    o = s + 716                                   # STRICT unique signature
    while o < e - 424:
        o = B.find(b'\xff\xff\xff\xff', o, e - 420)
        if o < 0:
            break
        if pstr(o + 424) and all(ptrspace(r32(o + f)) for f in
                                 (20, 28, 40, 44, 48, 52, 56, 164, 196, 200,
                                  316, 320, 324, 328)):
            ss += [o + f for f in UNIQ_STR]
            ps += [o + f for f in UNIQ_PTR]
            o += 424
            continue
        o += 1
    return ss, ps


wspans = [(i, s, e) for (i, nm, root, s, e) in spans
          if root == 'WeaponVariantDef' and e > s]

# ---- PASS B: NULL weapDef pointer fields -> zero record --------------------
n_b = 0
for (i, s, e) in wspans:
    if r32(s + 8) != FOLLOW:
        continue
    wd = s + 716
    if r32(s) == FOLLOW:
        wd = B.index(b'\x00', wd) + 1
    for o in WD_PTR:
        if r32(wd + o) == 0:
            struct.pack_into('>I', Z, wd + o, ZEROREC)
            n_b += 1
print('PASS B: %d NULL weapDef ptr fields -> zero-record' % n_b)

# ---- PASS A: raw-garbage sweep, with iterative revert ----------------------
plans = {}
for (i, s, e) in wspans:
    ws = []
    ss, ps = weapon_sites(s, e)
    for fo in ss:
        v = r32(fo)
        if not ok(v, fo, True):                  # boot-37: validate by TYPE
            ws.append((fo, EMPTY, v))
    for fo in ps:
        v = r32(fo)
        if not ok(v, fo, False):
            ws.append((fo, ZEROREC, v))
    if ws:
        plans[i] = ws
total = sum(len(w) for w in plans.values())
for ws in plans.values():
    for (fo, nv, _) in ws:
        struct.pack_into('>I', Z, fo, nv)
open(ZONE, 'wb').write(bytes(Z))


def walk_ok():
    open(ZONE, 'wb').write(bytes(Z))
    _, sp2, _ = LS.simulate(ZONE, verbose=False, policy=GATE_POL)
    return [(t[0], t[3], t[4]) for t in sp2] == GOOD


def _set(sites, new):
    for (fo, nv, old) in sites:
        struct.pack_into('>I', Z, fo, nv if new else old)


# BOOT-34: the revert must drop the minimal load-bearing SITE, never the whole
# weapon. The old code reverted plans[bad[0]] wholesale, which stranded ALL 123
# rot words of rpd_upgraded_zm -- including weapDef+0, the "standing WeapDef"
# name the engine looks up (fatal Com_Error). Binary-search each failing
# weapon's plan for the maximal safe subset instead.
culprits = []
for _ in range(12):
    if walk_ok():
        break
    _, sp2, _ = LS.simulate(ZONE, verbose=False, policy=GATE_POL)
    bad = [a[0] for a, b in zip(GOOD, [(t[0], t[3], t[4]) for t in sp2]) if a != b]
    if not bad:
        break
    w = bad[0]
    _set(plans[w], False)                      # baseline: this weapon reverted
    assert walk_ok(), 'PASS A: weapon %d untouched still drifts' % w
    keep, pending = [], [list(plans[w])]
    while pending:
        ch = pending.pop()
        _set(ch, True)
        if walk_ok():
            keep += ch
            continue
        _set(ch, False)
        if len(ch) > 1:
            m = len(ch) // 2
            pending += [ch[:m], ch[m:]]
        else:
            culprits.append((w, ch[0][0]))
    _set(keep, True)
    plans[w] = keep
    assert walk_ok(), 'PASS A: minimal-revert search did not converge'
else:
    raise SystemExit('PASS A did not converge')
landed = sum(len(w) for w in plans.values())
print('PASS A: %d/%d writes landed over %d weapons; %d walk-load-bearing sites '
      'reverted %s' % (landed, total, len(plans), len(culprits),
                       ['%d@0x%x' % c for c in culprits]))

# ---- PASS C: dangling inline altWeapon names ------------------------------
B = bytes(Z)
wnames, by_len = set(), {}
for (i, s, e) in wspans:
    ne = B.index(b'\x00', s + 716)
    wnames.add(B[s + 716:ne])
for n in sorted(wnames):
    by_len.setdefault(len(n), []).append(n)
PREF = [b'zombie_knuckle_crack', b'zombie_fists_zm', b'defaultweapon_mp',
        b'tazer_knuckles_zm']
pat = re.compile(rb'(?:sf|dw|gl)_[a-z0-9_]{3,40}')
n_c, no_sub = 0, []
for (i, s, e) in wspans:
    for m in pat.finditer(B, s, e):
        st, en = m.start(), m.end()
        if B[en:en + 1] != b'\x00' or m.group(0) in wnames:
            continue
        rep = next((p for p in PREF if len(p) == en - st),
                   (by_len.get(en - st) or [None])[0])
        if rep is None:
            no_sub.append(m.group(0))
            continue
        Z[st:en] = rep
        n_c += 1
print('PASS C: %d dangling altWeapon names substituted (%d without a same-length'
      ' replacement)' % (n_c, len(no_sub)))

# ---- PASS D/E: weapon-level dedup aliases whose TARGET is wrong -----------
# BOOT-21 EVIDENCE: 74 weapons share one attachments[63] alias and one
# attachmentUniques[95] alias. The exact-rt model DID move them (0xA77F9505 ->
# 0xA77F9584) but they still resolve into u16 TRIANGLE-INDEX data: the defect is
# the alias TARGET, not the addressing — i.e. source rot the runtime model can
# never fix (the PC word is a stale heap pointer, and all 74 inherit it).
# The engine null-checks every ARRAY ENTRY (beq skip), so pointing them at
# defaultweapon_mp's genuine all-zero arrays is safe; same for the 2 weapons
# whose weapDef is an alias. Handles are COMPUTED from the model (not the old
# hardcoded dump constants) so this pass survives any relayout.
import pickle
import rt_events_exact as RTX, _rt_acceptance as ACC
from measured_rtmap import MeasuredRuntimeMap
_ae = pickle.load(open('_zmnuked_simmap.pkl', 'rb'))['assets_end']
_anch = RTX.anchor_rt('_zmnuked_simmap.pkl', '_zmnuked_realmap.pkl')
_em, _sp, _ = LS.simulate(ZONE, verbose=False,
                          policy=RTX.policy(gfx_skip=0, anchor_rt=_anch))
# ⚠ locate the target arrays with ACC.sites, which SNAPS to a registered
# allocation. A byte-granular scan finds them ONE BYTE EARLY (the run of zeros
# starts at the preceding string's NUL), and a 4-byte-early target makes the
# engine read the first array entry out of string bytes — a fresh wild pointer.
_st = ACC.sites(bytes(Z), _sp, keys=set(_em.omap))
_ov = MeasuredRuntimeMap('_zmnuked_simmap.pkl', '_zmnuked_realmap.pkl',
                         interior_model=True)
_ov.sim = LS.RuntimeMap(_em.omap); _ov.sim_shift = _ae - 64
H = lambda fo: 0xA0000000 + int(_ov.rt(fo - _ae)) + 1
H_ATT = H(_st['attachments[63]'])
H_UNIQ = H(_st['attachmentUniques[95]'])
H_WD = H(_st['weapDef'])
print('PASS D/E targets (model-computed): attachments 0x%08X  uniques 0x%08X  '
      'weapDef 0x%08X' % (H_ATT, H_UNIQ, H_WD))
n_d = n_e = 0
for (i, s, e) in wspans:
    for off, h in ((0x18, H_ATT), (0x1C, H_UNIQ)):
        v = r32(s + off)
        if v not in (FOLLOW, INSERT, 0) and (v >> 28) == 0xA and v != h:
            struct.pack_into('>I', Z, s + off, h); n_d += 1
    v = r32(s + 8)
    if v not in (FOLLOW, INSERT, 0) and (v >> 28) == 0xA and v != H_WD:
        struct.pack_into('>I', Z, s + 8, H_WD); n_e += 1
print('PASS D: %d attachment-array aliases repointed ; PASS E: %d alias weapDefs'
      % (n_d, n_e))

open(ZONE, 'wb').write(bytes(Z))
print('zone md5 %s -> %s' % (before, hashlib.md5(Z).hexdigest()))
_, sp3, _ = LS.simulate(ZONE, verbose=False)
assert [(t[0], t[3], t[4]) for t in sp3] == GOOD, 'span drift!'
print('rewalk gate: %d spans unchanged, OK' % len(sp3))
