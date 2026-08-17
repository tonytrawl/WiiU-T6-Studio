#!/usr/bin/env python3
"""
GSC endian-swapper for the assemble pipeline (CHASE Task 1).

Console (Wii U, BE) compiled GSC = PC (LE) GSC with header words, table
fields, multi-byte cseg operands, and export checksums byte-swapped.
The opcode-level transcoder lives in wiiu_ref/gsc_diff.py (verified
byte-exact); this module wraps it at the ScriptParseTree ASSET-BODY level
so produce_nobackbone can call one function per SPT asset.

ScriptParseTree body (scriptparsetree_probe.py finding — PC-identical
layout, only endianness differs):
    +0  name ptr   (FOLLOW 0xFFFFFFFF -> name cstr inline after body)
    +4  int len    (buffer byte length, NOT counting serialized trailing NUL)
    +8  buffer ptr (FOLLOW -> len+1 bytes inline after the name)

convert_spt_body(pc_body) -> console body bytes, same length.

Validation (this file's main()):
    python gsc_swap.py [console_zone pc_zone]
pairs every SPT by script name across the two zones, converts the PC body,
and requires byte-exact equality with the genuine console body.
Validated: mp_raid_genuine 13/13, mp_dockside 17/17 (2026-07-09).
"""
import os, struct, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_WIIU_REF = os.path.join(os.path.dirname(_HERE), 'wiiu_ref')
if _WIIU_REF not in sys.path:
    sys.path.insert(0, _WIIU_REF)

from gsc_diff import pc_gsc_to_console, console_gsc_to_pc, OpErr  # noqa: F401
from scriptparsetree_probe import find_spt, parse_spt, detect_endian  # noqa

FOLLOW = b'\xff\xff\xff\xff'


def convert_spt_body(pc_body):
    """PC ScriptParseTree asset body (LE) -> console body (BE), byte-exact
    vs genuine. Input must start at the 12-byte SPT struct."""
    d = bytes(pc_body)
    if d[0:4] != FOLLOW or d[8:12] != FOLLOW:
        raise OpErr('SPT body pointers are not FOLLOW (aliased SPT?)')
    ln = struct.unpack_from('<I', d, 4)[0]
    nul = d.index(b'\x00', 12)
    name = d[12:nul + 1]                      # includes NUL
    buf = d[nul + 1:nul + 1 + ln]
    if len(buf) != ln:
        raise OpErr('SPT buffer truncated: have %d want %d' % (len(buf), ln))
    tail = d[nul + 1 + ln:nul + 2 + ln]       # serialized trailing NUL
    out = FOLLOW + struct.pack('>I', ln) + FOLLOW + name
    out += pc_gsc_to_console(buf) + tail
    return out


def spt_body_end(pc_body_region):
    """Length one SPT body consumes from the stream (12 + name + len + 1)."""
    end, _, _ = parse_spt(bytes(pc_body_region), 0, '<')
    return end


def _load_bodies(zone_path):
    d = open(zone_path, 'rb').read()
    e = detect_endian(d)
    out = {}
    for b, name, ln, buf in find_spt(d, e):
        end, _, _ = parse_spt(d, b, e)
        out[name] = d[b:end]
    return out, e


def main(argv):
    co = argv[1] if len(argv) > 1 else os.path.join(_WIIU_REF,
                                                    'mp_raid_genuine.zone')
    pc = argv[2] if len(argv) > 2 else os.path.join(
        os.path.dirname(_HERE), 'PC ff', 'mp_raid.zone')
    cob, ce = _load_bodies(co)
    pcb, pe = _load_bodies(pc)
    assert ce == '>' and pe == '<', 'expected console-BE + PC-LE zone pair'
    names = sorted(set(cob) & set(pcb))
    print('paired SPT bodies: %d (console-only=%d pc-only=%d)'
          % (len(names), len(set(cob) - set(pcb)), len(set(pcb) - set(cob))))
    ok = 0
    for n in names:
        try:
            got = convert_spt_body(pcb[n])
        except OpErr as ex:
            print('  FAIL %-52s %s' % (n, ex))
            continue
        if got == cob[n]:
            ok += 1
        else:
            bad = [i for i in range(min(len(got), len(cob[n])))
                   if got[i] != cob[n][i]]
            print('  DIFF %-52s len %d vs %d, %d bytes, first@0x%x'
                  % (n, len(got), len(cob[n]), len(bad),
                     bad[0] if bad else -1))
    print('body byte-exact: %d / %d' % (ok, len(names)))
    return 0 if ok == len(names) else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
