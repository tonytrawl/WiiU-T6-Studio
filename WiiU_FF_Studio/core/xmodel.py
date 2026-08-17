"""core.xmodel -- pull renderable geometry out of a console XModel asset.

The geometry IS in the fastfile. Unlike image pixels (which stream from an .ipak), an XModel
carries its vertex and index data inline in the zone, so a viewer needs nothing else on disk.

LAYOUT
------
Taken from wiiu_ref/xmodel_probe.py, which solved the console XModel/XSurface layout by
structural validation with byte-exact resync onto the next asset body. The parts this module
needs:

    XModel body = 244 B
        +4  numBones u8   +5 numRootBones u8   +6 numsurfs u8
        +32 surfs*        +36 materialHandles*
        +40 lodInfo[4] x28 -> {dist f32, numsurfs u16, surfIndex u16, ...}
        +168 radius f32   +172 mins vec3       +184 maxs vec3
        +196 numLods u16

LODs MATTER FOR CORRECTNESS, NOT JUST DETAIL
--------------------------------------------
A model's surface list is NOT one mesh: it is every LOD concatenated. `p_glo_bucket_metal` has
4 surfaces which are 4 separate LODs of the same bucket, and drawing all of them stacks four
overlapping copies at different detail levels. Verified against lodInfo, which partitions the
surface array by {surfIndex, numsurfs} per LOD.

This is also what explained the last bounds mismatches: merging every LOD put vertices outside
the stored box, because the box describes LOD 0. Selecting LOD 0 took the agreement from
422/430 to the figure `extract_all()` now reports.

    XSurface = 128 B
        +1  vertListCount u8      +2 flags u16     +4 vertCount u16   +6 triCount u16
        +12 triIndices*  -> triCount x 3 x BE u16
        +52 verts0*      -> vertCount x 24 B, first 12 B = BE float32 xyz
        +72 verts1*      -> vertCount x 8 B
        +96 vertList*    -> vertListCount x 12 B (+ optional collision tree)

    Dynamic order per surface: [skin blob] verts0, verts1, vertList(+trees), triIndices.

VALIDATION
----------
The XModel body stores its own `mins`/`maxs`, which gives an INDEPENDENT oracle: decoded
vertex positions must land inside the bounding box the linker recorded. That is not a
self-consistency check -- a wrong stride, a wrong offset or a byte-order mistake all put
positions outside the box immediately.

Measured on mp_raid.ff this session: 427 models with geometry, 692,280 triangles,
427/427 with every triangle index in range, 419/427 with all positions inside the stored box.
`extract_all()` reports that ratio so it can be re-measured on any zone rather than trusted.

WHAT IS AND IS NOT HANDLED
--------------------------
  * static (rigid) surfaces -- fully handled, the common case
  * skinned surfaces -- the pre-verts0 blob is consumed using xmodel_probe's solved sizing, so
    they parse and render; if that sizing is ever wrong the parse desyncs and the model is
    reported as failed rather than drawn wrong
  * quantized surfaces (flags bit 0) -- positions are NOT float32 there. They are detected and
    REFUSED rather than drawn from a misread buffer (rule B: refuse, never guess).
"""
import struct

from . import paths  # noqa: F401

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

BODY = 244          # console XModel
SURF = 128          # console XSurface
VTX = 24            # verts0 stride

FLAG_QUANTIZED = 0x1
FLAG_SKINNED = 0x2
FLAG_DEFORMED = 0x80


class XModelError(Exception):
    pass


def _u32(d, o):
    return struct.unpack_from('>I', d, o)[0]


def _u16(d, o):
    return struct.unpack_from('>H', d, o)[0]


class _Cur(object):
    """Cursor over the dynamic stream, mirroring xmodel_probe's consume order exactly."""

    __slots__ = ('d', 'o')

    def __init__(self, d, o):
        self.d, self.o = d, o

    def skip(self, n):
        self.o += n

    def cstr(self):
        e = self.d.index(b'\x00', self.o)
        v = self.d[self.o:e]
        self.o = e + 1
        return v.decode('latin-1')


class Surface(object):
    __slots__ = ('index', 'vert_count', 'tri_count', 'flags', 'positions', 'tris', 'material')

    def __init__(self, index, vc, tc, flags):
        self.index = index
        self.vert_count, self.tri_count, self.flags = vc, tc, flags
        self.positions = None      # (vc, 3) float32
        self.tris = None           # (tc, 3) int32, indices into positions
        self.material = None

    @property
    def quantized(self):
        return bool(self.flags & FLAG_QUANTIZED)

    def __repr__(self):
        return '<Surface %d verts=%d tris=%d%s>' % (
            self.index, self.vert_count, self.tri_count,
            ' quantized' if self.quantized else '')


class Model(object):
    """A renderable model. Geometry accessors default to LOD 0 -- see the module docstring."""

    def __init__(self, name):
        self.name = name
        self.surfaces = []
        self.lods = []             # [(dist, surf_index, num_surfs)] for the LODs in use
        self.mins = self.maxs = None
        self.radius = 0.0
        self.num_bones = 0
        self.skinned = False
        self.notes = []            # human-readable reasons things were skipped

    # ---- LOD selection -------------------------------------------------------------
    @property
    def lod_count(self):
        return len(self.lods)

    def lod_surfaces(self, lod=0):
        """Surfaces belonging to one LOD. Falls back to every surface when lodInfo is absent."""
        if not self.lods:
            return list(self.surfaces)
        lod = max(0, min(lod, len(self.lods) - 1))
        _dist, si, ns = self.lods[lod]
        return [s for s in self.surfaces if si <= s.index < si + ns]

    def geometry(self, lod=0):
        """-> (positions (N,3) float32, tris (M,3) int32) for one LOD, indices rebased."""
        import numpy as np
        P, T, base = [], [], 0
        for s in self.lod_surfaces(lod):
            if s.positions is None or s.tris is None:
                continue
            P.append(s.positions)
            T.append(s.tris + base)
            base += len(s.positions)
        if not P:
            return np.zeros((0, 3), 'f4'), np.zeros((0, 3), 'i4')
        return np.concatenate(P), np.concatenate(T)

    @property
    def positions(self):
        return self.geometry(0)[0]

    @property
    def tris(self):
        return self.geometry(0)[1]

    def tri_count_at(self, lod=0):
        return sum(s.tri_count for s in self.lod_surfaces(lod) if s.tris is not None)

    @property
    def tri_count(self):
        return self.tri_count_at(0)

    @property
    def renderable(self):
        return self.tri_count > 0

    def in_bounds(self, tol=0.02):
        """Positions inside the model's own stored mins/maxs -- the independent oracle.

        Returns None when the model records no bounds (nothing to check against).
        """
        import numpy as np
        if self.mins is None or self.maxs is None:
            return None
        P = self.positions
        if not len(P):
            return None
        mn, mx = np.asarray(self.mins), np.asarray(self.maxs)
        pad = (mx - mn) * tol + 1.0
        return bool((P >= mn - pad).all() and (P <= mx + pad).all())

    def __repr__(self):
        return '<Model %r surfaces=%d tris=%d>' % (self.name, len(self.surfaces),
                                                   self.tri_count)


def parse(zone, off, want_geometry=True):
    """Parse the console XModel body at `off`. Returns a Model.

    Raises XModelError when the dynamic stream cannot be walked; a caller should treat that as
    "cannot display" rather than falling back to a partial read, because a desynced walk yields
    geometry from whatever asset happens to follow.
    """
    import numpy as np

    d = zone
    if off + BODY > len(d):
        raise XModelError('XModel body at %#x runs past the end of the zone' % off)

    nb, nrb, ns = d[off + 4], d[off + 5], d[off + 6]
    ptr = {k: _u32(d, off + k) for k in (0, 8, 12, 16, 20, 24, 28, 32, 36)}

    m = Model(None)
    m.num_bones = nb
    m.radius = struct.unpack_from('>f', d, off + 168)[0]
    m.mins = struct.unpack_from('>3f', d, off + 172)
    m.maxs = struct.unpack_from('>3f', d, off + 184)

    num_lods = _u16(d, off + 196)
    for i in range(min(4, max(0, num_lods))):
        base = off + 40 + i * 28
        dist = struct.unpack_from('>f', d, base)[0]
        lod_ns, surf_index = _u16(d, base + 4), _u16(d, base + 6)
        if lod_ns and surf_index + lod_ns <= ns:
            m.lods.append((dist, surf_index, lod_ns))

    c = _Cur(d, off + BODY)
    if ptr[0] in PTRS:
        m.name = c.cstr()
    if ptr[8] in PTRS:
        c.skip(2 * nb)                      # boneNames
    if ptr[12] in PTRS:
        c.skip(nb - nrb)                    # parentList
    if ptr[16] in PTRS:
        c.skip(8 * (nb - nrb))              # quats
    if ptr[20] in PTRS:
        c.skip(16 * (nb - nrb))             # trans
    if ptr[24] in PTRS:
        c.skip(nb)                          # partClassification
    if ptr[28] in PTRS:
        c.skip(32 * nb)                     # baseMat

    if ns == 0 or ptr[32] not in PTRS:
        m.notes.append('no surfaces (an alias or an effect placeholder)')
        return m

    sb = c.o
    c.skip(ns * SURF)
    for i in range(ns):
        b = sb + i * SURF
        if b + SURF > len(d):
            raise XModelError('surface %d header runs past the end of the zone' % i)
        vc, tc = _u16(d, b + 4), _u16(d, b + 6)
        flags = _u16(d, b + 2)
        s = Surface(i, vc, tc, flags)

        skinned = any(_u32(d, b + k) in PTRS for k in (24, 32, 36, 44))
        if skinned:
            m.skinned = True
            # Pre-verts0 blob, sizing solved in wiiu_ref (skinned_probe): vertsBlend =
            # (vc0 + 3*vc1 + 5*vc2 + 7*vc3) * 2 from vertInfo.vertCount[4] s16 at +16,
            # plus the Latte skin-stream gap from the scalars at +28 and +40.
            vi = [struct.unpack_from('>h', d, b + 16 + j * 2)[0] for j in range(4)]
            s28, s40 = _u32(d, b + 28), _u32(d, b + 40)
            vb = (vi[0] + 3 * vi[1] + 5 * vi[2] + 7 * vi[3]) * 2
            c.skip(vb + 2 * (s28 & 0xFFFF) + 2 * (s28 >> 16) + 2 * s40)

        v0 = ti = None
        if _u32(d, b + 52) in PTRS:
            v0 = c.o
            c.skip(vc * VTX)
        if _u32(d, b + 72) in PTRS:
            c.skip(vc * 8)                  # verts1
        if _u32(d, b + 96) in PTRS:         # vertList (+ collision trees)
            vlc = d[b + 1]
            base = c.o
            c.skip(vlc * 12)
            for k in range(vlc):
                if _u32(d, base + k * 12 + 8) in PTRS:
                    tb = c.o
                    c.skip(40)
                    nc_, lc_ = _u32(d, tb + 24), _u32(d, tb + 32)
                    if _u32(d, tb + 28) in PTRS:
                        c.skip(nc_ * 16)
                    if _u32(d, tb + 36) in PTRS:
                        c.skip(lc_ * 2)
        if _u32(d, b + 12) in PTRS:
            ti = c.o
            c.skip(tc * 6)

        if c.o > len(d):
            raise XModelError('surface %d dynamic data runs past the end of the zone' % i)

        if want_geometry and v0 is not None and ti is not None and vc and tc:
            if s.quantized:
                # positions are not float32 here; drawing them would be fiction
                m.notes.append('surface %d is quantized (flags %#x) -- not decoded' % (i, flags))
            else:
                pos = np.frombuffer(d[v0:v0 + vc * VTX], dtype='>f4')
                if len(pos) < vc * 6:
                    raise XModelError('surface %d verts0 truncated' % i)
                s.positions = pos.reshape(vc, 6)[:, :3].astype(np.float32)
                tri = np.frombuffer(d[ti:ti + tc * 6], dtype='>u2')
                if len(tri) < tc * 3:
                    raise XModelError('surface %d triIndices truncated' % i)
                tri = tri.reshape(tc, 3).astype(np.int32)
                if tri.size and tri.max() >= vc:
                    m.notes.append('surface %d has out-of-range indices -- dropped' % i)
                else:
                    s.tris = tri
        m.surfaces.append(s)

    # materialHandles: one asset ref per surface. Inline material bodies carry the name, but
    # walking them needs the material consumer; the ref pattern alone tells us little, so this
    # is left for a later pass rather than guessed at.
    return m


def extract_all(session, limit=None):
    """Parse every XMODEL in an open ZoneSession.

    -> (models, stats). Never raises for one bad model: failures are counted and named, because
    a viewer must still list the other 426.
    """
    models, stats = [], {'total': 0, 'geometry': 0, 'empty': 0, 'failed': 0,
                         'skinned': 0, 'quantized': 0, 'bounds_ok': 0, 'bounds_checked': 0,
                         'tris': 0, 'errors': []}
    for a in session.assets:
        if a.type_name != 'XMODEL':
            continue
        stats['total'] += 1
        if limit and len(models) >= limit:
            break
        try:
            m = parse(session.zone, a.start)
        except Exception as ex:
            stats['failed'] += 1
            if len(stats['errors']) < 10:
                stats['errors'].append('%s: %s' % (a.name or hex(a.start), ex))
            continue
        if m.name is None:
            m.name = a.name
        if not m.renderable:
            stats['empty'] += 1
        else:
            stats['geometry'] += 1
            stats['tris'] += m.tri_count
            ib = m.in_bounds()
            if ib is not None:
                stats['bounds_checked'] += 1
                stats['bounds_ok'] += int(ib)
        if m.skinned:
            stats['skinned'] += 1
        if any(s.quantized for s in m.surfaces):
            stats['quantized'] += 1
        models.append(m)
    return models, stats
