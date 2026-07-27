"""Zone-wide sweep of EVERY weapon-NAME-REFERENCE field (bake candidate).

The engine hard-errors on any weapon/attachment name reference it cannot
resolve in the loaded weapon table:
    Com_Error("could not find altWeapon '%s' for attachment '%s' on weapon '%s'")
    Com_Error("could not find alt Dual Wield Weapon '%s' for weapon '%s'")
PC zones reference weapons that live in PC weapon FILES and were never
converted as zone assets (sf_/gl_ select-fire + grenade-launcher variants,
dual-optic attachments, left-hand dual-wield partners).

⚠ THE REASON THIS KEEPS RECURRING: each fix so far covered ONE FIELD.
Boot 17 + 31 did WeaponAttachmentUnique.szAltWeaponName; boot 34 then died on
WeaponDef.szDualWieldWeaponName. There are SIX such fields across the weapon
family -- sweep them ALL, every time.

WHICH FIELDS THE ENGINE ACTUALLY RESOLVES (measured, boot 34):
  * szAltWeaponName / szDualWieldWeaponName  -> RESOLVED, fatal if dangling.
  * parentWeaponName                         -> NOT resolved. 30 zm_nuked
    weapons carry a dangling one (m1911_zm -> 'm1911', etc.) and the engine
    set up every LIVE one of them without complaint. Leave them alone.
⚠⚠ THE LIVE WEAPON TABLE IS A HIGH-WATER MARK, NOT A ROSTER. It is filled in
order as BG_SetupWeaponVariantDef runs, and the CRASHING weapon is always its
LAST entry. Boot 34 ended at index 52 (fivesevendw_zm); boot 35 got exactly one
further, to index 53 (m1911_upgraded_zm) -- the reference I had dismissed as
unreachable BECAUSE it was absent from boot 34's table. Absence from a crash
dump's table means "not reached YET", never "unreachable".
=> Fix EVERY dangling alt/dual-wield reference. Table membership is not a
   filter; it is only useful for reading how far setup got.
Recover it from a dump: variant table 0x11D33308, entry+0x00 = name.

CLOSED 2026-07-26 (boot 36): m1911_upgraded_zm.szDualWieldWeaponName ->
'm1911lh_upgraded_zm'. It could NOT take the empty-string remedy -- that needs
an adjacent inline STRING to absorb the orphaned bytes and this one is
bracketed by binary (mat.statebits before, an f32 array after) -- and no 'lh'
weapon exists in the zone at all. Fixed by SAME-LENGTH substitution with a real
table weapon (19 chars: saiga12_upgraded_zm). Cost: if Mustang & Sally is ever
obtained its left-hand weapon is wrong -- a gameplay-time cosmetic fault on a
Pack-a-Punch weapon, versus a guaranteed load-time Com_Error.
"""
import struct, sys, os, bisect
sys.path.insert(0, '.'); sys.path.insert(0, os.path.join('..', 'wiiu_ref'))
import loader_sim as LS, rt_events_weapon as RW, rt_events_exact as RTX
import struct_layout, walker as W
from measured_rtmap import MeasuredRuntimeMap

FOLLOW, INSERT, EMPTY = 0xFFFFFFFF, 0xFFFFFFFE, 0xA001BD32
ZONE = sys.argv[1] if len(sys.argv) > 1 else 'zm_nuked_authored.zone'
HDR = W.HDR if os.path.isabs(W.HDR) else os.path.normpath(
    os.path.join('..', 'wiiu_ref', W.HDR))
L = struct_layout.Layout(HDR, console=False)

# every char* field in the weapon family whose VALUE is a weapon name
NAMEREF = {
    'variant': [(16, 'szAltWeaponName')],
    'weapDef': [(64, 'parentWeaponName'), (1652, 'szSpawnedGrenadeWeaponName'),
                (1656, 'szDualWieldWeaponName')],
    'uniq':    [(20, 'szAltWeaponName'), (28, 'szDualWieldWeaponName')],
}

Z = open(ZONE, 'rb').read()
_, spans, _ = LS.simulate(ZONE, verbose=False,
                          policy=dict(extra_events=RTX.all_events()))
ov = MeasuredRuntimeMap('_zmnuked_simmap.pkl', '_zmnuked_realmap.pkl')
inv = []
for (lo, hi, rs) in ov.spans:
    if rs is not None:
        inv.append((rs, rs + (hi - lo), lo))
for (lo, rt) in ov.interior:
    sp = next(((l, h) for (l, h, r) in ov.spans if l <= lo < h), None)
    if sp:
        inv.append((rt, rt + (sp[1] - lo), lo))
inv.sort()
_k = [t[0] for t in inv]

def rt_to_file(rt):
    j = bisect.bisect_right(_k, rt) - 1
    if j < 0:
        return None
    r0, r1, lo = inv[j]
    return None if rt >= r1 + 0x40000 else lo + (rt - r0) + ov.ae

def read_name(v, follow_at=None):
    """-> (name|None, shape). Never guesses: an unvalidated target is None."""
    if v in (0, EMPTY):
        return '', 'empty'
    if v == FOLLOW:
        if follow_at is None:
            return None, 'FOLLOW(no section)'
        e = Z.find(b'\x00', follow_at, follow_at + 96)
        s = Z[follow_at:e]
        return (s.decode('latin1'), 'FOLLOW') if e > follow_at and all(
            32 <= c < 127 for c in s) else (None, 'FOLLOW(not ascii)')
    if (v >> 28) != 0xA:
        return None, 'raw(%08x)' % v
    fo = rt_to_file((v - 1) & 0x1FFFFFFF)
    if fo is None or not (64 <= fo < len(Z)):
        return None, 'alias(unresolved)'
    e = Z.find(b'\x00', fo, fo + 96)
    s = Z[fo:e]
    return (s.decode('latin1'), 'alias') if e > fo and all(
        32 <= c < 127 for c in s) else (None, 'alias(not ascii)')

# ---- the resolvable set: the weapon names this zone actually emits ----------
weapons, emitted = [], set()
for (i, nm, root, s, e) in spans:
    if root != 'WeaponVariantDef' or e <= s:
        continue
    c = RW.weapon_walk(Z, s, '>')
    sec = {}
    for (lab, a, b, k) in c.sections:
        sec.setdefault(lab, []).append(a)
    weapons.append((i, s, e, sec))
    if 'name' in sec:
        ne = Z.find(b'\x00', sec['name'][0])
        emitted.add(Z[sec['name'][0]:ne].decode('latin1'))
print('zone emits %d weapon bodies, %d distinct names' % (len(weapons), len(emitted)))

rows = []
for (i, s, e, sec) in weapons:
    wname = Z[sec['name'][0]:Z.find(b'\x00', sec['name'][0])].decode('latin1') \
        if 'name' in sec else 'idx%d' % i
    bases = [('variant', s)]
    if 'weapDef' in sec:
        bases.append(('weapDef', sec['weapDef'][0]))
    for (kind, base) in bases:
        for (off, fname) in NAMEREF[kind]:
            v = struct.unpack_from('>I', Z, base + off)[0]
            fa = sec.get('wd.xs%d' % off, [None])[0] if kind == 'weapDef' else None
            if kind == 'variant' and off == 16:
                fa = sec.get('altName', [None])[0]
            nmv, shape = read_name(v, fa)
            rows.append((wname, kind, fname, base + off, v, nmv, shape))

bad = [r for r in rows if r[5] not in (None, '') and r[5] not in emitted]
unk = [r for r in rows if r[5] is None]
print('name-reference fields checked: %d' % len(rows))
from collections import Counter
print('shapes:', dict(Counter(r[6] for r in rows)))
print('DANGLING (name is real ASCII but not an emitted weapon): %d' % len(bad))
for r in bad:
    print('  %-28s %-26s @0x%x val=%08x %-8s -> %r'
          % (r[0], r[2], r[3], r[4], r[6], r[5]))
print('UNRESOLVED (could not read): %d' % len(unk))
for r in unk[:12]:
    print('  %-28s %-26s @0x%x val=%08x %s' % (r[0], r[2], r[3], r[4], r[6]))
