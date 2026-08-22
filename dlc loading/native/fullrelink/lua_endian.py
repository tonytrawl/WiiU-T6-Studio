#!/usr/bin/env python3
"""
T6 LUI (HavokScript, Lua 5.1 fmt 0x0d) bytecode ENDIAN transcoder + validator.

MODEL CORRECTED 2026-08-21 -- this file now delegates the container model to
WiiU_FF_Studio/core/hks.py (the proven per-proto model, gated by core/hks_selftest.py).
The previous local walker encoded TWO wrong facts:

  (1) it invented constant type 13 with an 8-byte payload. There is no type 13: the 8-byte
      constant is LUA_TUI64 = type 11, and the per-proto hash is not a constant at all.
  (2) it hardcoded a 12-byte per-proto footer (i32 + f32 + i32 nsubs). The real footer is
      PER-PROTO and VARIABLE:  i32 debug_flag, then a 4-byte hash IFF flag == 1, then
      i32 nsubs. Retail WiiU LUI ships flag 0 (8-byte gap); PC and our compiler emit
      flag 1 (12-byte gap). Assuming 12 parses retail console chunks 4 bytes out of phase
      per proto and truncates SILENTLY (measured: 30,513 B -> 3,534 B on
      ui_mp/t6/zombie/selectstartloczombie.lua) -- and the truncated result re-parses
      "exactly", so every consumed-the-buffer check passed.

The flag-driven walk in core.hks handles both populations; a chunk that cannot be walked to
its exact end RAISES (core.hks.HksError) instead of returning a plausible prefix.

API is unchanged: transcode(blob, want_le) -> (bytes, consumed); consumed == len(blob) on
success, always (partial consumption is now a refusal, never a return).

Validate against the 45 matched WiiU/PC pairs in patch_ui_zm (gold: PC->BE == genuine WiiU):
  python "../dlc loading/native/fullrelink/lua_endian.py" validate
"""
import sys, os, struct

MAGIC = b'\x1bLua'

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

# the container model lives in WiiU_FF_Studio/core/hks.py -- one owner, imported, not ported
_STUDIO = os.path.join(_ROOT, 'WiiU_FF_Studio')
if _STUDIO not in sys.path:
    sys.path.insert(0, _STUDIO)
from core import hks as _hks   # noqa: E402


def transcode(blob, want_le):
    """BE<->LE transcode via the per-proto flag-driven model.

    Returns (out_bytes, consumed). Raises core.hks.HksError on anything that does not walk
    to the exact end of the buffer -- a truncated or trailing-tail chunk is REFUSED, never
    silently shortened.
    """
    if blob[:4] != MAGIC:
        raise ValueError('not a Lua chunk')
    out = _hks.transcode(blob, want_le)
    return out, len(blob)


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


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'validate':
        validate()
    else:
        print(__doc__)
