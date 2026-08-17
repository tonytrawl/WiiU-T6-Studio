#!/usr/bin/env python3
"""FIX 30 (raid boot-28, VERTEX EXPLOSION): re-mint EVERY XSurface buffer
dedup alias against the genuine owner-buffer-identity law.

THE DISEASE — the FIFTH instance of the backref-registry debt
-------------------------------------------------------------
A dedup'd XSurface does not re-emit its geometry; it back-references an earlier
surface's buffers. MEASURED on the boot-28 zone (1,773 surfaces both zones,
ordinal pairing vs mp_raid_genuine, ZERO kind mismatches):

        verts0      212/212 alias values DIFFER from genuine
        verts1      212/212
        triIndices  212/212
        vertList    212/212
                  = **848/848 WRONG**

`verts0` IS the vertex buffer (vc * 24 B) and `triIndices` the index buffer
(tc * 6 B). Pointed at a drifted address, the GPU fetches whatever bytes are
there as positions ⇒ **VERTEX EXPLOSION**, which is the boot-27/28 symptom.
Bone arrays (FIX 29) only drive skinned/DObj models and were never a plausible
cause of a WORLD-WIDE explosion — this is.

⚠ WHY NOTHING CAUGHT IT: gate G6 `xsurface-dedup` PASSES on this zone and even
reports `dedup-surfs=212`. It COUNTS the dedup'd surfaces and checks the
skinned-surface null rule; it never checks where the aliases POINT. Third
application of rule (CG) in one session — **a detector is not a census.**

THE LAW
-------
    handle == 0xA0000000 + rt(owner surface's FOLLOW buffer, SAME field) + 1

Ordinal surface pairing (never by name), structural registry (never a value
scan — PHANTOM LAW), `+256` LAW carried.

Field sizes, mirroring xmodel_probe.parse_surface_dyn EXACTLY (that walker is
the pin; any divergence here silently mis-registers every owner):
    +52 verts0      vertCount * 24            +72 verts1     vertCount * 8
    +96 vertList    vlc * 12 (+ collision      +12 triIndices triCount * 6
                    trees, consumed but not
                    registered)
Skinned surfaces additionally consume a pre-verts0 blob; it is consumed, never
registered (genuine ships no dedup'd skinned surface).

Hard gates (raise, never degrade): surface counts equal; every field's KIND
equal; every genuine alias must resolve to an owner buffer exact-or-+256 and to
the SAME field; post-verify re-reads every minted cell.
Value-only: zone length unchanged by construction.
"""
import struct

PTRS = (0xFFFFFFFF, 0xFFFFFFFE)
VTX = 24
SURF = 128
FIELDS = [(12, 'triIndices'), (52, 'verts0'), (72, 'verts1'), (96, 'vertList')]


def _surfaces(path):
    """Return (Z, RuntimeMap, [(surf_off, {field: (kind, val_or_fileoff)})]).

    Walks every XModel span's surface array and reproduces
    xmodel_probe.parse_surface_dyn's cursor, recording each FOLLOW buffer's
    FILE offset instead of merely skipping it.
    """
    import os
    import sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(HERE, '..', 'wiiu_ref'))
    import loader_sim as LS
    import rt_events_exact as RTX
    import xmodel_probe as XP

    Z = open(path, 'rb').read()
    em, spans, _ = LS.simulate(path, verbose=False, policy=RTX.policy())
    rm = LS.RuntimeMap(em.omap)
    u32 = lambda o: struct.unpack_from('>I', Z, o)[0]
    u16 = lambda o: struct.unpack_from('>H', Z, o)[0]
    out = []
    for row in spans:
        (_i, _nm, root, s, e) = tuple(row)[:5]
        if root != 'XModel' or e <= s:
            continue
        nb, nrb, ns = Z[s + 4], Z[s + 5], Z[s + 6]
        c = XP.Cur(Z, s + XP.BODY)
        if u32(s) in PTRS:
            c.cstr()
        for (fo, sz) in ((8, 2 * nb), (12, nb - nrb), (16, 8 * (nb - nrb)),
                         (20, 16 * (nb - nrb)), (24, nb), (28, 32 * nb)):
            if u32(s + fo) in PTRS:
                c.skip(sz)
        if u32(s + 32) not in PTRS:
            continue
        sb = c.o
        c.skip(ns * SURF)
        for k in range(ns):
            b = sb + k * SURF
            vc, tc = u16(b + 4), u16(b + 6)
            rec = {}

            def note(fo, name, size):
                v = u32(b + fo)
                if v in PTRS:
                    rec[name] = ('FOLLOW', c.o, size)
                    c.skip(size)
                    return True
                rec[name] = ('ALIAS' if 0xA0000000 <= v < 0xC0000000
                             else 'OTHER', v, size)
                return False

            # skinned pre-blob: consumed, never registered (parse_surface_dyn)
            if (u32(b + 24) in PTRS or u32(b + 32) in PTRS
                    or u32(b + 36) in PTRS or u32(b + 44) in PTRS):
                vi = [struct.unpack_from('>h', Z, b + 16 + j * 2)[0]
                      for j in range(4)]
                s28, s40 = u32(b + 28), u32(b + 40)
                c.skip((vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]) * 2
                       + 2 * (s28 & 0xffff) + 2 * (s28 >> 16) + 2 * s40)
            note(52, 'verts0', vc * VTX)
            note(72, 'verts1', vc * 8)
            if note(96, 'vertList', Z[b + 1] * 12):
                # collision trees follow the vertList entries — consumed only
                base = rec['vertList'][1]
                for j in range(Z[b + 1]):
                    if u32(base + j * 12 + 8) in PTRS:
                        tb = c.o
                        c.skip(40)
                        nc_, lc_ = u32(tb + 24), u32(tb + 32)
                        if u32(tb + 28) in PTRS:
                            c.skip(nc_ * 16)
                        if u32(tb + 36) in PTRS:
                            c.skip(lc_ * 2)
            note(12, 'triIndices', tc * 6)
            out.append((b, rec))
    return Z, rm, out


def fix_xsurface_buffers(zone, genuine_path, verbose=True, max_unresolved=0):
    """Return (patched_zone, n_patched, n_plus256). Raises on any surprise."""
    import os
    import tempfile
    HERE = os.path.dirname(os.path.abspath(__file__))

    z = bytes(zone)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix='.zone', dir=HERE)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(z)
        _ZA, rmA, SA = _surfaces(tmp)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    _ZG, rmG, SG = _surfaces(genuine_path)

    if len(SA) != len(SG):
        raise RuntimeError('xsurface_buffer_remint: surface count %d vs '
                           'genuine %d' % (len(SA), len(SG)))

    fwd = {}
    for k, (_b, rec) in enumerate(SG):
        for f, t in rec.items():
            if t[0] == 'FOLLOW':
                fwd[rmG.rt(t[1] - 64)] = (k, f)

    zb = bytearray(z)
    expected = {}
    patched = plus256 = 0
    unresolved = []
    off = {f: o for (o, f) in FIELDS}
    for k, ((ba, ra), (bg, rg)) in enumerate(zip(SA, SG)):
        for f, tg in rg.items():
            ta = ra.get(f)
            if ta is None or ta[0] != tg[0]:
                raise RuntimeError('xsurface_buffer_remint: surface #%d field '
                                   '%s kind %s vs genuine %s'
                                   % (k, f, ta and ta[0], tg[0]))
            if tg[0] != 'ALIAS':
                continue
            b5 = (tg[1] - 1) & 0x1FFFFFFF
            plus = 0
            t = fwd.get(b5)
            if t is None:
                t = fwd.get(b5 - 256)
                if t is None:
                    unresolved.append((k, f, tg[1]))
                    continue
                plus = 256
                plus256 += 1
            tk, tf = t
            if tf != f:
                raise RuntimeError('xsurface_buffer_remint: surface #%d field '
                                   '%s aliases a %s buffer — cross-field '
                                   'geometry dedup is not modelled; measure it '
                                   'before allowing it' % (k, f, tf))
            oa = SA[tk][1][tf]
            if oa[0] != 'FOLLOW':
                raise RuntimeError('xsurface_buffer_remint: owner surface #%d '
                                   'field %s is %s in OUR zone' % (tk, tf, oa[0]))
            exp = (0xA0000000 + rmA.rt(oa[1] - 64) + plus + 1) & 0xFFFFFFFF
            cell = ba + off[f]
            expected[cell] = exp
            if struct.unpack_from('>I', zb, cell)[0] != exp:
                struct.pack_into('>I', zb, cell, exp)
                patched += 1

    if len(unresolved) > max_unresolved:
        raise RuntimeError('xsurface_buffer_remint: %d genuine aliases resolve '
                           'to no owner buffer, bound %d; first: %r'
                           % (len(unresolved), max_unresolved, unresolved[:3]))
    for fo, exp in expected.items():
        if struct.unpack_from('>I', zb, fo)[0] != exp:
            raise RuntimeError('xsurface_buffer_remint: post-verify failed @%d'
                               % fo)
    if verbose:
        print('xsurface_buffer_remint: %d/%d XSurface buffer aliases re-minted '
              'to the genuine owner-buffer law (%d +256, %d unresolved)'
              % (patched, len(expected), plus256, len(unresolved)))
    return bytes(zb), patched, plus256


if __name__ == '__main__':
    import os
    import sys
    zp, gp = sys.argv[1], sys.argv[2]
    Z = open(zp, 'rb').read()
    out, n, a = fix_xsurface_buffers(
        Z, gp, max_unresolved=int(os.environ.get('XSB_MAX_UNRESOLVED', '0')))
    print('would patch %d cells (%d +256); length %d -> %d'
          % (n, a, len(Z), len(out)))
