"""Console WeaponVariantDef interior walker (probe / feature extraction).

Mirrors Load_WeaponVariantDef + Load_WeaponDef (weapon_convert._WD_STREAM) on the
CONSOLE (BE) side. Returns (end, features) where features counts every inline
sub-asset root by kind, so the runtime-drift residual can be regressed against
structural quantities.
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref'))
import body_relayout as BR
import weapon_convert as WC

F = 0xFFFFFFFF
INS = 0xFFFFFFFE
PTRS = (F, INS)

VARIANT = 716
WEAPONDEF = 2836          # console (PC 2448 + 388 WiiU tail)
ATTACH = 284
ATTACHU = 424
CAMO = 28
FLAME = 484
TRACER = 128
MATERIAL = 104
XMODEL = 244


class Cur:
    def __init__(self, z, o):
        self.z = z
        self.o = o
        self.feat = {}
        self.log = []

    def u(self, o):
        return struct.unpack_from('>I', self.z, o)[0]

    def take(self, n, kind):
        self.log.append((self.o, n, kind))
        self.feat[kind] = self.feat.get(kind, 0) + n
        self.feat['n_' + kind] = self.feat.get('n_' + kind, 0) + 1
        self.o += n

    def cstr(self, kind='str'):
        e = self.z.index(b'\x00', self.o)
        self.take(e + 1 - self.o, kind)


def _xstring_array(c, n, kind='xsarr'):
    base = c.o
    c.take(4 * n, kind)
    for i in range(n):
        if c.u(base + 4 * i) == F:
            c.cstr(kind + '_s')


def _ptr_array(c, n, kind, sub):
    base = c.o
    c.take(4 * n, kind)
    for i in range(n):
        if c.u(base + 4 * i) in PTRS:
            sub(c)


def _material(c):
    e = BR.DELIMITERS['Material'](c.z, c.o)
    c.log.append((c.o, MATERIAL, 'mat_root'))
    c.feat['mat_root'] = c.feat.get('mat_root', 0) + MATERIAL
    c.feat['n_mat_root'] = c.feat.get('n_mat_root', 0) + 1
    c.o += MATERIAL
    c.take(e - c.o, 'mat_body')


def _fx(c):
    e = BR.DELIMITERS['FxEffectDef'](c.z, c.o)
    c.take(e - c.o, 'fx')


def _xmodel(c):
    e = BR.DELIMITERS['XModel'](c.z, c.o)
    c.take(e - c.o, 'xmodel')


def _tracer(c):
    # console TracerDef: 128-B root, name string, then material
    b = c.o
    c.take(TRACER, 'tracer_root')
    if c.u(b) == F:
        c.cstr('tracer_name')
    if c.u(b + 8) in PTRS:
        _material(c)


def _image(c):
    e = BR.DELIMITERS['GfxImage'](c.z, c.o)
    c.take(e - c.o, 'image')


def _camo(c):
    """Load_WeaponCamo: 28-B root {name, solidBaseImage, patternBaseImage,
    camoSets, numCamoSets, camoMaterials, numCamoMaterials} + sets/materials."""
    b = c.o
    c.take(CAMO, 'camo_root')
    if c.u(b) == F:
        c.cstr('camo_name')
    for io in (4, 8):
        if c.u(b + io) in PTRS:
            _image(c)
    if c.u(b + 12) in PTRS:                      # WeaponCamoSet[numCamoSets]
        n = c.u(b + 16)
        base = c.o
        c.take(20 * n, 'camo_sets')
        for i in range(n):
            for io in (0, 4):
                if c.u(base + 20 * i + io) in PTRS:
                    _image(c)
    if c.u(b + 20) in PTRS:                      # WeaponCamoMaterialSet[n]
        n = c.u(b + 24)
        base = c.o
        c.take(8 * n, 'camo_matsets')
        for i in range(n):
            if c.u(base + 8 * i + 4) in PTRS:
                cnt = c.u(base + 8 * i)
                mb = c.o
                c.take(44 * cnt, 'camo_mats')
                for j in range(cnt):
                    nbm = struct.unpack_from('>H', c.z, mb + 44 * j + 2)[0]
                    for po in (4, 8):
                        if c.u(mb + 44 * j + po) in PTRS:
                            ab = c.o
                            c.take(4 * nbm, 'camo_matptrs')
                            for k in range(nbm):
                                if c.u(ab + 4 * k) in PTRS:
                                    _material(c)


def _flame(c):
    b = c.o
    c.take(FLAME, 'flame_root')
    if c.u(b + 432) == F:
        c.cstr('flame_name')
    for mo in (436, 440, 444, 448, 452, 456, 460, 464):
        if c.u(b + mo) in PTRS:
            _material(c)
    for so in (468, 472, 476, 480):
        if c.u(b + so) == F:
            c.cstr('flame_snd')


def _attachment(c):
    b = c.o
    c.take(ATTACH, 'att_root')
    if c.u(b) == F:
        c.cstr('att_name')
    if c.u(b + 4) == F:
        c.cstr('att_name')


def _attachment_unique(c):
    b = c.o
    c.take(ATTACHU, 'attu_root')
    A = lambda o: c.u(b + o)
    if A(0) == F:
        c.cstr('attu_s')
    if A(20) == F:
        c.cstr('attu_s')
    if A(28) == F:
        c.cstr('attu_s')
    if A(36) == F:
        c.take(64, 'attu_hidetags')
    for mo in (40, 44, 48, 52, 56):
        if A(mo) in PTRS:
            _xmodel(c)
    if A(60) == F:
        c.cstr('attu_s')
    if A(64) == F:
        c.cstr('attu_s')
    if A(164) in PTRS:
        _camo(c)
    if A(196) in PTRS:
        _material(c)
    if A(200) in PTRS:
        _material(c)
    if A(232) == F:
        _xstring_array(c, 88, 'attu_xanims')
    if A(248) == F:
        c.take(21 * 4, 'attu_locdmg')
    for so in range(256, 312, 4):
        if A(so) == F:
            c.cstr('attu_s')
    if A(316) in PTRS:
        _fx(c)
    if A(320) in PTRS:
        _fx(c)
    if A(324) in PTRS:
        _tracer(c)
    if A(328) in PTRS:
        _tracer(c)


def _weapondef(c):
    wd = c.o
    W = lambda o: c.u(wd + o)
    c.take(WEAPONDEF, 'wd_root')
    for op in WC._WD_STREAM:
        k, off = op[0], op[1]
        v = W(off)
        if k == 'xs':
            if v == F:
                c.cstr('wd_str')
        elif k == 'u16':
            if v == F:
                c.take(WC.NOTETRACK_COUNT * 2, 'wd_notetrack')
        elif k == 'f32':
            if v == F:
                c.take(op[2] * 4, 'wd_f32')
        elif k == 'vec2':
            if v == F:
                c.take(W(op[2]) * 8, 'wd_vec2')
        elif k == 'xsarr':
            if v == F:
                _xstring_array(c, op[2], 'wd_xsarr')
        elif k == 'xmod':
            if v == F:
                _ptr_array(c, 16, 'wd_xmodarr', _xmodel)
        elif k == 'asset':
            if v in PTRS:
                {'xmodel': _xmodel, 'fx': _fx, 'material': _material,
                 'tracer': _tracer, 'camo': _camo}[op[2]](c)
        elif k == 'flame':
            if v == F:
                _flame(c)


def weapon_walk(z, b):
    c = Cur(z, b)
    P = lambda o: c.u(b + o)
    c.take(VARIANT, 'var_root')
    if P(0) == F:
        c.cstr('var_name')
    if P(8) == F:
        _weapondef(c)
    for o in (12, 16, 20):
        if P(o) == F:
            c.cstr('var_str')
    if P(24) == F:
        _ptr_array(c, 63, 'attachments', _attachment)
    if P(28) == F:
        _ptr_array(c, 95, 'attachmentUniques', _attachment_unique)
    if P(32) == F:
        _xstring_array(c, 88, 'szXAnims')
    if P(36) == F:
        c.take(64, 'hideTags')
    if P(40) == F:
        _ptr_array(c, 8, 'attachViewModel', _xmodel)
    if P(44) == F:
        _ptr_array(c, 8, 'attachWorldModel', _xmodel)
    if P(48) == F:
        _xstring_array(c, 8, 'attachViewModelTag')
    if P(52) == F:
        _xstring_array(c, 8, 'attachWorldModelTag')
    for o in (508, 512, 520):
        if P(o) == F:
            c.cstr('var_str')
    for o in (596, 600, 604):
        if P(o) in PTRS:
            _material(c)
    return c.o, c


if __name__ == '__main__':
    import pickle
    om, spans = pickle.load(open('_wv_sim_base.pkl', 'rb'))
    Z = open('zm_nuked_authored.zone', 'rb').read()
    ok = bad = 0
    for (i, nm, root, s, e) in spans:
        if root != 'WeaponVariantDef' or e <= s:
            continue
        try:
            end, c = weapon_walk(Z, s)
        except Exception as ex:
            bad += 1
            print('ERR  %d %s' % (s, str(ex)[:80]))
            continue
        if end == e:
            ok += 1
        else:
            bad += 1
            print('DRIFT s=%d len=%d walk=%d (%+d)' % (s, e - s, end - s, end - e))
    print('span-exact %d/%d' % (ok, ok + bad))
