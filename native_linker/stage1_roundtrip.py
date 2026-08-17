#!/usr/bin/env python3
"""
Stage 1 round-trip driver. Parses a genuine console zone's CONTAINER (XFile header,
XAssetList, script-string table, asset-list array) into a neutral in-memory form,
then re-emits it through the native ZoneWriter and asserts byte-identity against the
original. This validates the write engine's header emission, block-5 offset math,
FOLLOW/null sentinel handling, string serialization and the asset-array layout
independently of any per-asset body knowledge (that is the next increment).

Usage: python stage1_roundtrip.py [genuine.zone]
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ⚠ REAL DEPENDENCY, DECLARED HERE. parse_container asks wiiu_ref/wiiu_zone.py for the container
# model rather than restating it (see the note in parse_container), so wiiu_ref must be importable.
# Callers that already put it on sys.path are unaffected; this only makes the module runnable on
# its own, which is what its self-test needs.
_WIIU_REF = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          '..', 'wiiu_ref'))
if os.path.isdir(_WIIU_REF) and _WIIU_REF not in sys.path:
    sys.path.insert(0, _WIIU_REF)
import zone_stream as zs

HEADER_LEN = 40
XASSETLIST_LEN = 24
BLOCK5_STREAM_BASE = HEADER_LEN + XASSETLIST_LEN   # 64


def parse_container(d):
    """Neutral parse of the container region. Returns a dict of the fields the
    writer needs, plus the byte length of the container (up to first asset body)."""
    size, ext = struct.unpack_from('>II', d, 0)
    block_sizes = list(struct.unpack_from('>8I', d, 8))
    string_count, str_ptr, depend_count, dep_ptr, asset_count, asset_ptr = \
        struct.unpack_from('>6I', d, 40)

    o = 64
    # script-string pointer pattern (FOLLOW / null), then inline string bytes
    str_ptrs = list(struct.unpack_from('>%dI' % string_count, d, o))
    o += string_count * 4
    strings = []
    for p in str_ptrs:
        if p == zs.FOLLOW:
            end = d.index(b'\x00', o)
            strings.append(d[o:end])
            o = end + 1
        else:
            strings.append(None)          # null slot: no inline bytes
    strings_end = o

    # ---- dependency list + where the asset array actually starts ------------------------------
    # ⚠ TWO DEFECTS LIVED HERE, AND wiiu_zone.py HAD ALREADY SOLVED BOTH.
    #
    #   1. The depends region was never consumed. XAssetList is
    #      {stringCount, strings*, dependCount, depends*, assetCount, assets*} and the depends
    #      region (dependCount pointer words, then inline names) sits BETWEEN the script strings
    #      and the asset array. `depend_count` was parsed and then ignored, so on any zone with
    #      dependCount > 0 the asset array was located short by the whole region -- measured 8 B
    #      on the live patch_zm (DC=1, name "zo_").
    #   2. There was an `align(4)` here. The T6 stream does NOT pad before the asset array;
    #      wiiu_zone.py:118 says so outright. mp_raid happens to land aligned, which is why the
    #      default subject of main() never caught it.
    #
    # Measured across the 15 live update-title zones: the old code put the asset array at the
    # wrong offset on SIX of them (patch_zm -7, patch/patch_mp +3, zm_nuked_patch +2,
    # zm_transit_patch +1) and every asset entry parsed out of those was garbage.
    #
    # ⚠ THE OFFSET IS NOT RE-DERIVED HERE. wiiu_zone.ZoneReader owns the container model,
    # including the batched/interleaved depends layouts and the rule-(W) `depends* == NULL`
    # guard. Re-implementing it in a second place is exactly how these two files diverged in the
    # first place, so this ASKS the reader rather than restating it.
    from wiiu_zone import ZoneReader
    _r = ZoneReader(bytes(d))
    _r.read_string_table()
    assets_file = _r.assets_off
    # ⚠ EMITTED VERBATIM, NOT RE-SERIALISED. Keeping the region's raw bytes makes the round trip
    # byte-exact whichever layout the source used, and means this file needs no opinion about
    # batched vs interleaved at all. (They are identical for DC <= 1, which is every zone we
    # ship, but "identical today" is not a thing to build on.)
    dep_raw = bytes(d[strings_end:assets_file])
    if depend_count and not dep_raw:
        raise ValueError('dependCount=%d but the depends region is empty (strings end 0x%X == '
                         'asset array 0x%X)' % (depend_count, strings_end, assets_file))
    assets = []                            # (console_type, header_ptr)
    for i in range(asset_count):
        t, hp = struct.unpack_from('>II', d, assets_file + i * 8)   # NOT `o` -- see above
        assets.append((t, hp))
    container_end = assets_file + asset_count * 8
    return dict(size=size, ext=ext, block_sizes=block_sizes,
                string_count=string_count, str_ptr=str_ptr,
                depend_count=depend_count, dep_ptr=dep_ptr,
                asset_count=asset_count, asset_ptr=asset_ptr,
                str_ptrs=str_ptrs, strings=strings, assets=assets,
                strings_end=strings_end, dep_raw=dep_raw,
                assets_file=assets_file, container_end=container_end)


def emit_container(c):
    """Re-emit the container through the native writer."""
    w = zs.ZoneWriter()

    # XAssetList (24 bytes) sits raw at stream offset 40, before block 5.
    xlist = struct.pack('>6I', c['string_count'], c['str_ptr'],
                        c['depend_count'], c['dep_ptr'],
                        c['asset_count'], c['asset_ptr'])
    w.buf += xlist                         # raw prefix; not part of any block

    # Block 5 begins here (block-5 offset 0 == stream offset 64).
    w.push_block(zs.BLOCK_VIRTUAL)

    # script-string pointer array
    for p in c['str_ptrs']:
        w.write_u32(zs.FOLLOW if p == zs.FOLLOW else 0)
    # inline string bytes for FOLLOW slots, in order
    for p, s in zip(c['str_ptrs'], c['strings']):
        if p == zs.FOLLOW:
            w.write_cstr(s)

    # Dependency region, verbatim. NO align(4) here -- the asset array immediately follows the
    # inline strings/depends (wiiu_zone.py:118). See parse_container for what the old align cost.
    if c['dep_raw']:
        w.write_bytes(c['dep_raw'])

    # asset-list array: (type, header.data). We recompute the header pointer as a
    # FOLLOW sentinel where the original had one; alias entries are carried through
    # (Stage 1 doesn't re-lay-out bodies yet, so their targets are unchanged).
    for t, hp in c['assets']:
        w.write_u32(t)
        w.write_u32(hp)

    w.pop_block()

    # Stage 1 only re-emits the container; keep the writer's declared block sizes
    # identical to the source so the header matches (body re-layout comes later).
    w.block_size = list(c['block_sizes'])
    w.external_size = c['ext']
    return w.emit(total_size=c['size'])   # carry whole-zone size (bodies not re-laid-out yet)


#: MANDATORY SUBJECTS. Both must round-trip byte-exactly; a failure on either is a failure.
#:
#: ⚠⚠ mp_raid_genuine CANNOT CATCH THE CONTAINER-HEAD CLASS, AND WAS THE ONLY SUBJECT FOR MONTHS.
#: It has dependCount = 0 AND its asset array happens to land 4-aligned, so the two defects that
#: shipped here (an unconsumed depends region, and a spurious align(4) before the asset array)
#: EXACTLY CANCEL on it. Law, banked by the PM: a self-test whose only subject is the one file
#: where two bugs cancel is worse than no self-test.
#:
#: patch_zm from the LIVE UPDATE TITLE is the subject that does catch it: dependCount = 1
#: (dep name "zo_") and an UNALIGNED asset array at 0xBDF.
#: ⛔ THE DISC COPY WILL NOT REPRODUCE IT. E:\Wii U Black ops 2\content\english\patch_zm.ff
#: (md5 329b4443…, 2,654,272 B) has dependCount = 0. Anyone re-checking against that file will
#: conclude the bug is gone. Use the update-title copy (md5 570e46ac…, 4,456,448 B).
SUBJECTS = [
    ('mp_raid_genuine.zone (DC=0, 4-aligned -- CANNOT catch a container-head defect)',
     os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'wiiu_ref',
                  'mp_raid_genuine.zone')),
    ('patch_zm.ff update-title (DC=1 "zo_", asset array UNALIGNED at 0xBDF)',
     os.path.join(os.environ.get('APPDATA', ''), 'Cemu', 'mlc01', 'usr', 'title', '0005000e',
                  '1010cf00', 'content', 'english', 'patch_zm.ff')),
]


def _load(path):
    """Read a subject. .ff needs decrypt+inflate; a raw .zone is already the stream."""
    if path.lower().endswith('.ff'):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import zone_facts as ZF
        return bytes(ZF.load_ff(path))
    return open(path, 'rb').read()


def roundtrip(d):
    """-> (ok, detail). Byte-compares the re-emitted container against the source."""
    c = parse_container(d)
    out = emit_container(c)
    n = c['container_end']
    orig, reemit = d[:n], out[:n]
    head = 'DC=%d strings=%d assets=%d assets_file=0x%X container_end=0x%X' % (
        c['depend_count'], c['string_count'], c['asset_count'], c['assets_file'], n)
    if len(reemit) != n:
        return False, '%s | SHORT EMIT: %d bytes where %d are needed' % (head, len(reemit), n)
    if orig == reemit:
        return True, '%s | byte-identical' % head
    for i in range(n):
        if orig[i] != reemit[i]:
            return False, '%s | DIVERGENCE at 0x%X: orig=%s reemit=%s' % (
                head, i, orig[max(0, i - 4):i + 8].hex(), reemit[max(0, i - 4):i + 8].hex())
    return False, '%s | differs but no byte located (length %d vs %d)' % (head, len(orig), len(reemit))


def main():
    if len(sys.argv) > 1:
        subjects = [('argv', p) for p in sys.argv[1:]]
    else:
        subjects = SUBJECTS

    rows, failed, skipped = [], 0, 0
    for label, path in subjects:
        if not os.path.exists(path):
            # ⚠ A MISSING MANDATORY SUBJECT IS NOT A PASS. It is reported as SKIP and counted, so
            # a green line total can never be mistaken for coverage that did not run.
            rows.append(('SKIP', label, 'not on this machine: %s' % path))
            skipped += 1
            continue
        try:
            ok, detail = roundtrip(_load(path))
        except Exception as ex:
            ok, detail = False, '%s: %s' % (type(ex).__name__, ex)
        rows.append(('OK' if ok else 'FAIL', label, detail))
        failed += (not ok)

    for status, label, detail in rows:
        print('  %-4s %s' % (status, label))
        print('       %s' % detail)
    print()
    print('  %d subject(s): %d ok, %d failed, %d skipped'
          % (len(rows), len(rows) - failed - skipped, failed, skipped))
    if skipped:
        print('  (a SKIPPED subject is NOT a pass -- its file was not present)')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
