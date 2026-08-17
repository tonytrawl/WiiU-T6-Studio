#!/usr/bin/env python3
"""
T6 LUI (HavokScript, Lua 5.1 fmt 0x0d) bytecode ENDIAN transcoder + validator.

Format from Deewarz/CoDHVKDecompiler LuaFileT6.cs (T6 = HavokScript, NOT standard Lua 5.1):
  Header: magic[4], luaVer, compilerVer, endian@[6], sizeInt, sizeSizeT, sizeInstr,
          sizeNumber, integralFlag, gameByte, <1 skipped byte>, i32 constantTypeCount,
          then constantTypeCount x { i32 id, i32 strLen, byte[strLen] }.
  Function (recursive):
    header: i32 upvalCount, i32 paramCount, u8 usesVararg, i32 registerCount,
            i32 instructionCount, then PAD to 4-byte alignment (4 - pos%4 if 0<..<4).
    instructions: instructionCount x 4 bytes (packed bitfield -> endian-swapped word).
    constants: i32 count, each u8 type then: 0 nil=-; 1 bool=u8; 3 number=f32;
               4 string=i32 len + len bytes (null-terminated); 13 hash=u64.
    footer: i32 (unknown), f32 (unknown), i32 subFunctionCount.
    subfunctions: subFunctionCount x Function.
Everything multibyte is endian-swapped; strings/bytes pass through. PC=LE, WiiU=BE.

Validate against the 45 matched WiiU/PC pairs in patch_ui_zm (gold: PC->BE == genuine WiiU):
  python "../dlc loading/native/fullrelink/lua_endian.py" validate
"""
import sys, os, struct

MAGIC = b'\x1bLua'


class T6Lua:
    def __init__(self, blob, want_le):
        self.b = blob; self.p = 0
        self.src_le = (blob[6] == 1)
        self.se = 'little' if self.src_le else 'big'
        self.want_le = want_le
        self.out = bytearray()

    def _swap(self, sz):
        raw = self.b[self.p:self.p+sz]; self.p += sz
        self.out += (raw[::-1] if self.src_le != self.want_le else raw)
    def _bytes(self, n):
        self.out += self.b[self.p:self.p+n]; self.p += n
    def _rint(self):
        raw = self.b[self.p:self.p+4]; self.p += 4
        self.out += (raw[::-1] if self.src_le != self.want_le else raw)
        return int.from_bytes(raw, self.se)

    def function(self):
        self._rint()                 # upvalCount
        self._rint()                 # paramCount
        self._bytes(1)               # usesVararg
        self._rint()                 # registerCount
        ic = self._rint()            # instructionCount
        extra = 4 - (self.p % 4)     # align to 4 (pos-based, matches reader)
        if 0 < extra < 4:
            self._bytes(extra)
        for _ in range(ic):          # instructions (4-byte packed words)
            self._swap(4)
        cc = self._rint()            # constant count
        for _ in range(cc):
            t = self.b[self.p]; self.out.append(t); self.p += 1
            if   t == 0:  pass
            elif t == 1:  self._bytes(1)
            elif t == 3:  self._swap(4)
            elif t == 4:  n = self._rint(); self._bytes(n)
            elif t == 13: self._swap(8)
            else: raise ValueError('const type %d @%d' % (t, self.p-1))
        self._rint()                 # footer: unknown int
        self._swap(4)                # footer: unknown float
        sc = self._rint()            # subFunctionCount
        for _ in range(sc):
            self.function()

    def transcode(self):
        hdr = bytearray(self.b[0:14]); hdr[6] = 1 if self.want_le else 0
        self.out += hdr; self.p = 14
        for _ in range(self._rint()):     # constant-type table
            self._rint(); n = self._rint(); self._bytes(n)
        self.function()
        return bytes(self.out), self.p


def transcode(blob, want_le):
    if blob[:4] != MAGIC:
        raise ValueError('not a Lua chunk')
    return T6Lua(blob, want_le).transcode()


def _rawfiles(z, be):
    e = '>' if be else '<'; out = {}; o = 0
    while True:
        i = z.find(MAGIC, o)
        if i < 0: break
        j = i-1
        if z[j] == 0: j -= 1
        st = j
        while st > 0 and 32 <= z[st-1] < 127: st -= 1
        name = (z[st:i-1] if z[i-1] == 0 else z[st:i]).decode('latin1', 'replace')
        H = st-12; ln = struct.unpack_from(e+'I', z, H+4)[0] if H >= 0 else -1
        if 0 < ln < 2_000_000 and z[i:i+4] == MAGIC:
            out[name] = z[i:i+ln]; o = i+ln
        else:
            o = i+4
    return out


def validate():
    sys.path[:0] = [os.path.join(_ROOT, 'native_linker'), os.path.join(_ROOT, 'wiiu_ref'),
                    os.path.join(_ROOT, 'WiiU_FF_Studio'), os.path.join(_ROOT, 'tools')]
    import wiiu_ff, ff_decrypt
    src = 'C:/Users/Tony - Main Rig/AppData/Roaming/Cemu/mlc01/usr/title/0005000e/1010cf00/content/english/'
    _h, wz, _n = wiiu_ff.decrypt(open(src+'patch_ui_zm.ff', 'rb').read()); W = _rawfiles(wz, True)
    raw = open('E:/pluto_t6_full_game/zone/all/patch_ui_zm.ff', 'rb').read()
    e, k, v, l = ff_decrypt.detect_platform(raw); pz = ff_decrypt.decrypt_ff(raw, k, e)[1]; P = _rawfiles(pz, False)

    rt = rtf = 0; gold = goldf = 0; fails = []
    for nm, wb in W.items():
        try:
            le, c = transcode(wb, True); be, _ = transcode(le, False)
            if be == wb and c == len(wb): rt += 1
            else:
                rtf += 1; d = next((x for x in range(min(len(be), len(wb))) if be[x] != wb[x]), 'len')
                fails.append(('rt', nm, len(wb), c, d))
        except Exception as ex:
            rtf += 1; fails.append(('rt-exc', nm, str(ex)[:50]))
        if nm in P:                        # GOLD: PC(LE) -> BE must equal genuine WiiU
            try:
                got, c = transcode(P[nm], False)
                if got == wb: gold += 1
                else:
                    goldf += 1; d = next((x for x in range(min(len(got), len(wb))) if got[x] != wb[x]), 'len')
                    fails.append(('gold', nm, len(P[nm]), len(wb), len(got), d))
            except Exception as ex:
                goldf += 1; fails.append(('gold-exc', nm, str(ex)[:50]))
    print('round-trip BE->LE->BE:  %d OK / %d FAIL' % (rt, rtf))
    print('GOLD PC->BE == genuine: %d OK / %d FAIL (of %d shared)' % (gold, goldf, len(set(W)&set(P))))
    for f in fails[:10]:
        print('  ', f)


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'validate':
        validate()
    else:
        print(__doc__)
