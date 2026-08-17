#!/usr/bin/env python3
"""Whole-zone GfxImage RECORD census — structure-driven, offset-range-free.

Rule (AT): a texture audit must be driven by structure, never by an offset window —
the old 199-slot audit scoped to 78.0-78.5 MB missed every XModel-inline material
record.  This census walks the WHOLE zone at BYTE granularity (records are NOT
4-aligned) and finds every 328-byte GfxImage body regardless of who owns it
(top-level asset, XModel-inline material, FX-inline, GfxWorld.materialMemory).

Rule (AU): the record is MIXED-ENDIAN.  GX2 surface header at body+0 is
LITTLE-endian; the T6 tail is BIG-endian; +316 streamedPartCount is LITTLE-endian
like the header.  +36 GX2 imagePtr is runtime-only (always 0 in-file) — pixel
ownership is +176 (0 = streamed/none, 0xFFFFFFFF = FOLLOW inline).

328-byte body layout (all offsets from body start B):
  LE: +0 dim  +4 width  +8 height  +12 depth  +16 numMips  +20 GX2 format
      +24 aa  +28 use  +32 imageSize  +36 GX2 imagePtr(=0)  +316 streamedPartCount
  BE: +156 mapType  +157 semantic  +158 category  +159 delayLoadPixels
      +160 baseSize(u32) +164/+166 w/h(u16) +168 depth  +170 levelCount
      +171 streaming  +176 pixels ptr  +180 T6 format enum(u32)
      +184+44k streamedPartInfo (packed word +0, dataHash +4)  +320 name ptr
      +324 nameHash(u32)
  name ptr FOLLOW (0xFFFFFFFF): NUL-terminated name chars start at +328; an
  inline record's pixels stream right after the name NUL.
  name ptr block-5 alias (0xA0-family handle): the name string was deduped to an
  earlier occurrence — NO inline name.  These records are invisible to a
  name-anchored scan and must be validated structurally.

Record classes emitted:
  owned        nameHash != 0.  Subclasses:
                 hash_ok=True   inline name matches nameHash (r_hash_string)
                 hash_ok=False  inline name present but DISAGREES with nameHash
                                ("name drift", the gfxtail43 defect family)
                 hash_ok=None   alias-named — no inline name to check
  extern       comma-prefixed reference (",plastic_n"): nameHash=0, t6=0, spc=0.
               Two shapes exist in the corpus: fully-zeroed stub (mp_raid /
               zm_transit style) and 1x1 RGBA8 streamed stub (mp_skate style).

Measured design points (probe run 2026-07-30 on mp_raid_genuine /
mp_skate_gfxtail46 / zm_transit_original — see scratchpad probe_gfximage_fields
/ probe2_bounds_and_alias / probe3_delta):
  * `00 00 00 00 01 00 00 00` at +24 (aa=0 LE, use=1 LE) holds for 100% of
    owned records in all three zones -> pass-2 anchor (rule AU's needle).
  * streaming byte takes value 2 on 28 GENUINE zm_transit records — the valid
    set is {0,1,2}, not {0,1}.
  * genuine zones DO carry alias-named records (mp_raid 1, zm_transit 48, every
    one's nameHash resolves to a real earlier name string) — the alias arm is
    load-bearing, not a converted-zone-only feature.
  * image names may start with '*' (*reflection_probe0..N).  The prior art
    (ipak_stream.scan_genuine_bodies) misses these; its charset lacked '*'.
    That is the whole 1029-vs-1050 mp_skate delta.
  * dual encoding: an inline record's tail w/h (BE u16 +164/+166) equals the GX2
    header w/h (LE +4/+8); a streamed record's tail is 1x1.  100% in corpus.
  * owned field bounds (corpus-unanimous): dim 1..7 (1=2D, 3=cube), w/h
    1..16384, depth {1,6}, numMips 1..12, GX2 fmt {0x01,0x07,0x1a,0x31,0x32,
    0x33,0x35}, mapType {3,5}, imageSize >= 1, spc 0..3 with bytes +317..+319
    zero (LE), pixels ptr in {0, FOLLOW}.

T6 format enums (rule B table): RGBA8=5, BC1=6, BC2=9, BC3=0x0A, BC5=0x14;
valid observed set {1,2,3,4,5,6,9,10,20}.  A defective converted zone can leak
raw DXGI codes (71/77/83/28...) or enum 0 into +180 — the census REPORTS these
(that detection is its point) and only asserts cleanliness when told the zone
is genuine (genuine zones measured 0 violations).

API:
  census(zone_bytes)      -> list of record dicts (see RECORD KEYS below)
  summarize(records)      -> dict of summary counts
  main: python gfximage_census.py <zone> [<zone>...] [--assert-genuine] [--json F]

RECORD KEYS: off, name (str|None), name_hash, owned (bool), hash_ok
(True|False|None), streaming, part_count, part_hashes, t6_format, gx2_format,
formats_agree (True|False|None), map_type, alias_named (bool), name_ptr,
pixel_class ('streamed'|'inline'|'none'), width, height, depth, mips, dim,
semantic, category, image_size, base_size, extent (end offset incl. inline
name + inline pixels).

This file is standalone on purpose (census gets wired into the gate battery
separately); r_hash_string is duplicated from wiiu_ref/ipak.py verbatim.
"""

import bisect
import json
import struct
import sys

FOLLOW = 0xFFFFFFFF
BODY = 328
NEEDLE24 = b'\x00\x00\x00\x00\x01\x00\x00\x00'   # aa=0 LE, use=1 LE at body+24

# name charset: prior art's set + '*' (reflection probes) + ',' (extern prefix)
NAME_CHARS = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_/~$#&+.-,*")
NAME_MAX = 256

# rule (B) measured T6 table; valid enum set for GENUINE zones
T6_VALID = frozenset((1, 2, 3, 4, 5, 6, 9, 10, 20))
# t6 -> acceptable GX2 formats (engine maps T6 4/5 -> 0x1a RGBA8; T6 2 is the
# luminance path -> GX2 0x01 R8, measured on '$outdoor' in all three corpus
# zones — mapping 2 -> 0x1a would flag genuine retail data)
T6_TO_GX2 = {1: {0x01}, 2: {0x01}, 3: {0x07}, 4: {0x1a}, 5: {0x1a},
             6: {0x31}, 9: {0x32}, 10: {0x33}, 20: {0x35}}
# sane GX2 formats for structural candidate validation (observed + BC4)
SANE_GX2 = frozenset((0x01, 0x07, 0x1a, 0x31, 0x32, 0x33, 0x34, 0x35))
# raw DXGI codes known to leak into +180 on defective converted zones
LEAKED_DXGI = frozenset((28, 71, 72, 74, 75, 77, 78, 80, 81, 83, 84, 87, 91, 98))


def r_hash_string(name, h=0):
    """T6 R_HashString: case-folding djb-xor. == GfxImage.hash for the name.
    (verbatim from wiiu_ref/ipak.py)"""
    for c in name.encode('latin-1'):
        h = (33 * h & 0xFFFFFFFF) ^ (c | 0x20)
    return h


def _be32(d, o):
    return struct.unpack_from('>I', d, o)[0]


def _le32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def _be16(d, o):
    return struct.unpack_from('>H', d, o)[0]


def _is_b5_handle(v):
    """block-5 asset-pointer alias handle (0xA0-family): (v-1)>>29 & 7 == 5."""
    return v not in (0, FOLLOW) and ((v - 1) >> 29) & 7 == 5


def _read_name(d, B):
    """inline name after a FOLLOW name ptr -> (name, nul_off) or (None, None)."""
    e = d.find(b'\x00', B + BODY, B + BODY + NAME_MAX)
    if e <= B + BODY:
        return None, None
    nm = d[B + BODY:e]
    if len(nm) < 2 or not all(c in NAME_CHARS for c in nm):
        return None, None
    return nm.decode('latin-1'), e


def _struct_battery(d, B, need_needle=True):
    """Structural plausibility of a 328-byte body at B.  Bounds are the measured
    corpus-unanimous ones (module docstring), deliberately a notch wider so a
    defective-but-real record still lands.  -> (ok, failed_gate_name)."""
    if need_needle and d[B + 24:B + 32] != NEEDLE24:
        return False, 'needle24'
    dim = _le32(d, B + 0)
    if not 1 <= dim <= 7:
        return False, 'dim'
    w, h = _le32(d, B + 4), _le32(d, B + 8)
    if not (1 <= w <= 16384 and 1 <= h <= 16384):
        return False, 'width_height'
    if not 1 <= _le32(d, B + 12) <= 256:
        return False, 'depth'
    if not 1 <= _le32(d, B + 16) <= 15:
        return False, 'mips'
    if _le32(d, B + 20) not in SANE_GX2:
        return False, 'gx2_format'
    if not 1 <= _le32(d, B + 32) <= 0x4000000:
        return False, 'image_size'
    if d[B + 156] not in (1, 2, 3, 4, 5):
        return False, 'map_type'
    strm = d[B + 171]
    if strm not in (0, 1, 2):
        return False, 'streaming'
    t6 = _be32(d, B + 180)
    if t6 > 0xFF and t6 not in LEAKED_DXGI:
        return False, 't6_format'
    spc = d[B + 316]
    if spc > 8 or d[B + 317:B + 320] != b'\x00\x00\x00':
        return False, 'part_count'
    pix = _be32(d, B + 176)
    if pix not in (0, FOLLOW):
        return False, 'pixels_ptr'
    tw, th = _be16(d, B + 164), _be16(d, B + 166)
    if strm in (1, 2):
        # streamed: pixels elsewhere, >=1 part, tail dims collapsed to 1x1
        if not (pix == 0 and 1 <= spc <= 8 and tw == 1 and th == 1):
            return False, 'streamed_consistency'
    else:
        # non-streamed: inline FOLLOW pixels, no parts, tail dims == header dims
        if not (pix == FOLLOW and spc == 0 and tw == w and th == h):
            return False, 'inline_consistency'
    return True, None


def _record(d, B, name, name_end, alias_named, hash_ok, klass):
    strm = d[B + 171]
    pix = _be32(d, B + 176)
    spc = _le32(d, B + 316)          # rule (AU): +316 is LITTLE-endian
    base = _be32(d, B + 160)
    name_hash = _be32(d, B + 324)
    if strm in (1, 2):
        pixel_class = 'streamed'
    elif pix == FOLLOW:
        pixel_class = 'inline'
    else:
        pixel_class = 'none'
    # extent: body [+ name] [+ inline pixels streaming right after]
    extent = (name_end + 1) if name_end is not None else (B + BODY)
    if pixel_class == 'inline':
        extent += base
    t6 = _be32(d, B + 180)
    gx2 = _le32(d, B + 20)
    agree = (gx2 in T6_TO_GX2[t6]) if t6 in T6_TO_GX2 else (None if klass == 'extern' else False)
    return {
        'off': B,
        'name': name,
        'name_hash': name_hash,
        'owned': name_hash != 0,
        'hash_ok': hash_ok,
        'alias_named': alias_named,
        'name_ptr': _be32(d, B + 320),
        'klass': klass,                      # 'owned' | 'extern'
        'streaming': strm,
        'part_count': spc,
        # streamedPartInfo is a MAX-3 in-body array (+184 + 3*44 == +316, which
        # is exactly why the count lives at +316) — never read past part 2
        'part_hashes': [_be32(d, B + 184 + 44 * k + 4)
                        for k in range(min(spc, 3))] if strm in (1, 2) else [],
        'pixel_class': pixel_class,
        't6_format': t6,
        'gx2_format': gx2,
        'formats_agree': agree,
        'map_type': d[B + 156],
        'semantic': d[B + 157],
        'category': d[B + 158],
        'width': _le32(d, B + 4),
        'height': _le32(d, B + 8),
        'depth': _le32(d, B + 12),
        'dim': _le32(d, B + 0),
        'mips': _le32(d, B + 16),
        'image_size': _le32(d, B + 32),
        'base_size': base,
        'extent': extent,
    }


def census(zone_bytes):
    """Whole-zone GfxImage record census -> list of record dicts, ascending off.

    Pass 1 (name-anchored): every FOLLOW dword treated as a candidate name ptr
    at body+320; classified by inline name + nameHash:
      hash match           -> owned, hash_ok=True   (collision-proof anchor)
      ','-name + hash==0   -> extern                (+ stub shape: t6=0, spc=0)
      hash mismatch        -> owned drift, hash_ok=False, only if the FULL
                              structural battery passes (else noise, dropped)
    Pass 2 (structure-anchored): every `aa=0,use=1` needle at body+24 whose
    name ptr at +320 is a block-5 alias handle; accepted only on the full
    structural battery.  These are the alias-named records a name scan can
    never see (name=None; resolving the alias needs an rt map).
    Dedup: pass-1 records are accepted first (stronger anchors); a pass-2
    candidate overlapping any accepted record's extent (incl. inline name and
    inline pixels) is discarded, then pass-2 survivors dedup greedily among
    themselves in ascending offset order."""
    d = zone_bytes
    n = len(d)
    accepted = []

    # ---- pass 1: FOLLOW name ptr at +320 --------------------------------
    o = d.find(b'\xff\xff\xff\xff')
    while o != -1:
        B = o - 320
        o = d.find(b'\xff\xff\xff\xff', o + 4)
        if B < 0 or B + BODY > n:
            continue
        name, e = _read_name(d, B)
        if name is None:
            continue
        h = _be32(d, B + 324)
        if h != 0 and r_hash_string(name) == h:
            accepted.append(_record(d, B, name, e, False, True, 'owned'))
        elif name.startswith(',') and h == 0 \
                and _be32(d, B + 180) == 0 and d[B + 316:B + 320] == b'\x00\x00\x00\x00':
            accepted.append(_record(d, B, name, e, False, None, 'extern'))
        elif h != 0 and _struct_battery(d, B)[0]:
            # name drift: real record whose inline name no longer matches its
            # hash (gfxtail43 family). 0 in the current corpus; kept armed.
            accepted.append(_record(d, B, name, e, False, False, 'owned'))
    accepted.sort(key=lambda r: r['off'])

    # ---- pass 2: alias-named records via the structural needle ----------
    # merge pass-1 extents into disjoint intervals; a pass-2 candidate is
    # rejected if its body range intersects any of them
    spans = []
    for r in accepted:
        s, x = r['off'], r['extent']
        if spans and s <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], x))
        else:
            spans.append((s, x))
    starts = [s for s, _ in spans]

    def overlaps(a, b):
        """does [a, b) intersect any merged pass-1 span?"""
        i = bisect.bisect_right(starts, b - 1) - 1
        return i >= 0 and spans[i][1] > a

    alias = []
    o = d.find(NEEDLE24)
    while o != -1:
        B = o - 24
        o = d.find(NEEDLE24, o + 1)
        if B < 0 or B + BODY > n:
            continue
        if not _is_b5_handle(_be32(d, B + 320)):
            continue
        if not _struct_battery(d, B, need_needle=False)[0]:
            continue
        rec = _record(d, B, None, None, True, None, 'owned')
        if _be32(d, B + 324) == 0:
            rec['klass'] = 'extern'          # alias-named extern; 0 in corpus
        alias.append(rec)

    last_end = -1
    for rec in sorted(alias, key=lambda r: r['off']):
        if rec['off'] < last_end:
            continue                          # alias-vs-alias overlap: greedy
        if overlaps(rec['off'], rec['extent']):
            continue                          # inside an accepted record
        accepted.append(rec)
        last_end = rec['extent']

    accepted.sort(key=lambda r: r['off'])
    return accepted


def summarize(records):
    s = {
        'total': len(records),
        'owned': 0, 'extern': 0,
        'alias_named': 0, 'drift': 0,
        'streamed': 0, 'inline': 0, 'none': 0,
        'owned_t6_violations': 0,
        'owned_format_disagree': 0,
        't6_formats': {}, 'violation_offs': [],
    }
    for r in records:
        if r['klass'] == 'extern':
            s['extern'] += 1
            continue
        s['owned'] += 1
        s[r['pixel_class']] += 1
        if r['alias_named']:
            s['alias_named'] += 1
        if r['hash_ok'] is False:
            s['drift'] += 1
        t6 = r['t6_format']
        s['t6_formats'][t6] = s['t6_formats'].get(t6, 0) + 1
        if t6 not in T6_VALID:
            s['owned_t6_violations'] += 1
            s['violation_offs'].append(r['off'])
        if r['formats_agree'] is False:
            s['owned_format_disagree'] += 1
    return s


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    assert_genuine = '--assert-genuine' in argv
    json_out = None
    if '--json' in argv:
        json_out = argv[argv.index('--json') + 1]
        if json_out in args:
            args.remove(json_out)
    if not args:
        print('usage: gfximage_census.py <zone> [<zone>...] [--assert-genuine] [--json out.json]')
        return 2
    rc = 0
    dumps = {}
    for path in args:
        with open(path, 'rb') as f:
            d = f.read()
        recs = census(d)
        s = summarize(recs)
        dumps[path] = recs
        print('=== %s  (%d bytes)' % (path, len(d)))
        print('  owned=%d (streamed=%d inline=%d none=%d) extern=%d '
              'alias_named=%d drift=%d'
              % (s['owned'], s['streamed'], s['inline'], s['none'],
                 s['extern'], s['alias_named'], s['drift']))
        print('  t6 formats: %s' % {k: v for k, v in sorted(s['t6_formats'].items())})
        print('  owned +180 outside %s: %d %s' % (sorted(T6_VALID),
              s['owned_t6_violations'],
              s['violation_offs'][:10] if s['owned_t6_violations'] else ''))
        print('  owned t6<->gx2 table disagreements: %d' % s['owned_format_disagree'])
        if assert_genuine and s['owned_t6_violations']:
            print('  FAIL --assert-genuine: %d owned records with invalid +180 enum'
                  % s['owned_t6_violations'])
            rc = 1
        if assert_genuine and s['drift']:
            print('  FAIL --assert-genuine: %d name-drift records' % s['drift'])
            rc = 1
    if json_out:
        with open(json_out, 'w') as f:
            json.dump(dumps, f, indent=1)
        print('wrote %s' % json_out)
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
