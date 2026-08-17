#!/usr/bin/env python3
"""FIX 29 (raid boot-27, VERTEX EXPLOSION in-game + the G-106 alignment fails):
re-mint EVERY XModel bone-array dedup alias against the genuine
owner-array-identity law.

THE DISEASE
-----------
An XModel whose bone arrays are byte-identical to an earlier model's does not
re-emit them — it back-references the earlier model's array. The loader law is
`slot := *(target)`, so the alias must land EXACTLY on the owner model's
FOLLOW array start for that SAME field. Ours land in a drifted domain:

    MEASURED on the boot-27 zone, ordinal model pairing vs mp_raid_genuine
    (440/440 models, bone counts equal, ZERO unresolved, ZERO cross-field):
        boneNames  32/32 WRONG      partClass  32/32 WRONG
        baseMat    32/32 WRONG      trans       2/2  WRONG
        quats       2/2  WRONG      parentList  2/2  WRONG
                                    = 102/102 WRONG

`baseMat` (DObjAnimMat, 8 f32/bone) and `trans` (4 f32/bone) are exactly what
`__CalcSkelNonRootBones` reads to build the skeleton. Pointed at the WRONG
model's block they yield garbage matrices ⇒ **vertex explosion in-game**, which
is the boot-27 user-visible symptom.

⭐⭐ WHY G-106 UNDER-REPORTS THIS BY 60%: G-106 flags 24 FATAL-f32 + 16 sub-word
= 40 of the 102. The other 62 aliases are just as wrong but happen to land on a
naturally-aligned offset, so the alignment probe cannot see them. This is rule
(CA)'s own warning made concrete — **MISALIGNMENT IS ONLY THE DETECTOR, THE
DEFECT IS THE WRONG DEDUP TARGET** (the skate cm46 finding, re-proven here on a
second map). Never size this class from the alignment count.

THE LAW
-------
    handle == 0xA0000000 + rt(owner model's FOLLOW array start, SAME field) + 1

Fields and their natural alignments (from the XModel body walk):
    +8  boneNames  2*nb        align 2      +20 trans      16*(nb-nrb)  align 4
    +12 parentList nb-nrb      align 1      +24 partClass  nb           align 1
    +16 quats      8*(nb-nrb)  align 2      +28 baseMat    32*nb        align 4

Pairing is ORDINAL, never by name (genuine name-dedups break name pairing — the
FIX 23/25/27 recipe), and the owner registry comes from the STRUCTURAL walk,
never a value scan (PHANTOM LAW).

Hard gates (raise, never degrade): span counts equal; per-model bone counts
equal; every field's KIND (FOLLOW/ALIAS/other) equal; every genuine alias must
resolve to an owner array exact-or-+256 AND to the same field; post-verify
re-reads every minted cell; the minted payload must satisfy the field's natural
alignment (rule (CA)) or we raise rather than ship a hardware trap.
Value-only: zone length unchanged by construction.
"""
import struct

PTRS = (0xFFFFFFFF, 0xFFFFFFFE)
# (body offset, name, size(nb, nrb), natural alignment)
FIELDS = [
    (8,  'boneNames',  lambda nb, nrb: 2 * nb,          2),
    (12, 'parentList', lambda nb, nrb: nb - nrb,        1),
    (16, 'quats',      lambda nb, nrb: 8 * (nb - nrb),  2),
    (20, 'trans',      lambda nb, nrb: 16 * (nb - nrb), 4),
    (24, 'partClass',  lambda nb, nrb: nb,              1),
    (28, 'baseMat',    lambda nb, nrb: 32 * nb,         4),
]


def _walk(path):
    """Return (Z, RuntimeMap, [(span_start, nb, nrb, {field: (kind, a, size)})]).

    `kind` is 'FOLLOW' (a = the array's FILE offset), 'ALIAS' (a = the handle)
    or 'OTHER' (a = the raw word). Mirrors fx_oracle.walk_xmodel's prologue
    ordering, which is the loader's.
    """
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', 'wiiu_ref'))
    import loader_sim as LS
    import rt_events_exact as RTX
    import xmodel_probe as XP

    Z = open(path, 'rb').read()
    em, spans, _ = LS.simulate(path, verbose=False, policy=RTX.policy())
    rm = LS.RuntimeMap(em.omap)
    u32 = lambda x: struct.unpack_from('>I', Z, x)[0]
    models = []
    for row in spans:
        (i, nm, root, s, e) = tuple(row)[:5]
        if root != 'XModel' or e <= s:
            continue
        nb, nrb = Z[s + 4], Z[s + 5]
        c = XP.Cur(Z, s + XP.BODY)
        if u32(s) in PTRS:
            c.cstr()
        rec = {}
        for (fo, fname, szf, _al) in FIELDS:
            v = u32(s + fo)
            sz = szf(nb, nrb)
            if v in PTRS:
                rec[fname] = ('FOLLOW', c.o, sz)
                c.skip(sz)
            elif 0xA0000000 <= v < 0xC0000000:
                rec[fname] = ('ALIAS', v, sz)
            else:
                rec[fname] = ('OTHER', v, sz)
        models.append((s, nb, nrb, rec))
    return Z, rm, models


def fix_xmodel_bonearrays(zone, genuine_path, verbose=True, max_unresolved=0):
    """Return (patched_zone, n_patched, n_plus256). Raises on any surprise.

    max_unresolved: genuine aliases whose target is no top-level XModel FOLLOW
    bone array are LEFT UNTOUCHED and counted. raid and transit measure 0.
    ⚠ genuine mp_dockside_wiiu does NOT (model #269 partClass -> A104FB6F): its
    owner is outside the top-level XModel span set this module walks (FX-inline
    et7 models carry bone arrays too and are not enumerated here). That is a
    REAL coverage gap, not noise — do not raise the bound to hide it.
    """
    import os
    import tempfile
    HERE = os.path.dirname(os.path.abspath(__file__))

    z = bytes(zone)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix='.zone', dir=HERE)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(z)
        _ZA, rmA, MA = _walk(tmp)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    _ZG, rmG, MG = _walk(genuine_path)

    if len(MA) != len(MG):
        raise RuntimeError('xmodel_bonearray_remint: XModel span count %d vs '
                           'genuine %d' % (len(MA), len(MG)))

    align = {f: a for (_o, f, _s, a) in FIELDS}
    off = {f: o for (o, f, _s, _a) in FIELDS}

    # genuine forward registry: rt(FOLLOW array start) -> (model ordinal, field)
    fwd = {}
    for k, (_s, _nb, _nrb, rec) in enumerate(MG):
        for f, (kind, a, _sz) in rec.items():
            if kind == 'FOLLOW':
                fwd[rmG.rt(a - 64)] = (k, f)

    zb = bytearray(z)
    expected = {}
    patched = 0
    plus256 = 0
    crossfield = 0
    unresolved = []
    for k, ((sa, nba, nrba, ra), (sg, nbg, nrbg, rg)) in enumerate(zip(MA, MG)):
        if (nba, nrba) != (nbg, nrbg):
            raise RuntimeError('xmodel_bonearray_remint: model #%d bone counts '
                               '(%d,%d) vs genuine (%d,%d)'
                               % (k, nba, nrba, nbg, nrbg))
        for f, (kind, v, _sz) in rg.items():
            if ra[f][0] != kind:
                raise RuntimeError('xmodel_bonearray_remint: model #%d field %s '
                                   'kind %s vs genuine %s'
                                   % (k, f, ra[f][0], kind))
            if kind != 'ALIAS':
                continue
            b5 = (v - 1) & 0x1FFFFFFF
            plus = 0
            t = fwd.get(b5)
            if t is None:
                t = fwd.get(b5 - 256)          # +256 LAW (boot-21 under-count)
                if t is None:
                    unresolved.append((k, f, v))
                    continue
                plus = 256
                plus256 += 1
            tk, tf = t
            if tf != f:
                # CROSS-FIELD dedup is LEGAL — measured on genuine
                # mp_dockside_wiiu (model #269: boneNames aliases a baseMat
                # array). Two different fields whose bytes coincide dedup onto
                # one array. An earlier version of this module raised here and
                # would have refused a correct genuine zone; raid happens not
                # to contain the class, which is exactly how such an assertion
                # survives unnoticed until another map.
                crossfield += 1
            oa = MA[tk][3][tf]
            if oa[0] != 'FOLLOW':
                raise RuntimeError('xmodel_bonearray_remint: owner model #%d '
                                   'field %s is %s in OUR zone, not FOLLOW'
                                   % (tk, tf, oa[0]))
            rt = rmA.rt(oa[1] - 64) + plus
            if rt % align[f]:
                raise RuntimeError('xmodel_bonearray_remint: minted %s payload '
                                   'rt=%d violates natural alignment %d (rule '
                                   '(CA)) for model #%d — refusing to ship a '
                                   'hardware trap' % (f, rt, align[f], k))
            exp = (0xA0000000 + rt + 1) & 0xFFFFFFFF
            cell = sa + off[f]
            expected[cell] = exp
            if struct.unpack_from('>I', zb, cell)[0] != exp:
                struct.pack_into('>I', zb, cell, exp)
                patched += 1

    if len(unresolved) > max_unresolved:
        raise RuntimeError('xmodel_bonearray_remint: %d genuine aliases resolve '
                           'to no owner array, bound %d; first: %r'
                           % (len(unresolved), max_unresolved, unresolved[:3]))
    for fo, exp in expected.items():
        if struct.unpack_from('>I', zb, fo)[0] != exp:
            raise RuntimeError('xmodel_bonearray_remint: post-verify failed @%d'
                               % fo)
    if verbose:
        print('xmodel_bonearray_remint: %d/%d XModel bone-array aliases '
              're-minted to the genuine owner-array law (%d +256 artifacts, '
              '%d cross-field dedups, %d unresolved left untouched)'
              % (patched, len(expected), plus256, crossfield, len(unresolved)))
    return bytes(zb), patched, plus256


if __name__ == '__main__':
    import sys
    zp, gp = sys.argv[1], sys.argv[2]
    Z = open(zp, 'rb').read()
    import os
    mu = int(os.environ.get('XBR_MAX_UNRESOLVED', '0'))
    out, n, a = fix_xmodel_bonearrays(Z, gp, max_unresolved=mu)
    print('would patch %d cells (%d +256); length %d -> %d'
          % (n, a, len(Z), len(out)))
