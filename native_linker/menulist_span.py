"""menulist_span — console MenuList span parser (rule BW).

WHY a new parser and not a fix: `body_relayout._menulist_end` is CORRECT in method and merely too
narrow in its follower vocabulary. Its own comment explains the method, and it still stands:

    the WiiU (v148) menu graph -- menuDef_t/itemDef_s/windowDef_t and the ExpressionStatement/
    inline-Material sub-tree -- does NOT match the T6 load_db's console FillStruct offsets (the
    genuine menuDef_t body is 424 B, not 392) ... Re-deriving that whole sub-graph from one mostly
    -empty stub is unreliable, and menus are copied UNCHANGED for the mapsTable relink -- so bound
    the MenuList like the other hard console assets and copy verbatim.

So a non-empty MenuList is bounded at the NEXT ASSET's header. `_menulist_end` recognises exactly
two followers -- a MenuList named '*.txt' and a StringTable named '*.csv' -- which is what patch_mp
happens to use. `en_patch_loc_mp` ends

    2237 MENULIST 'ui_mp/hud.menu' -> 2238 MENULIST 'ui_mp/hud_splitscreen.menu' -> 2239 RAWFILE

so BOTH followers are invisible to it: the MenuList is named '.menu', not '.txt', and RawFile is not
a recognised follower at all. It scanned 373,939 B, matched nothing, and raised.

⚠⚠ THE THING THAT MAKES THIS SAFE -- learned the hard way, see below -- IS `NEXT_ROOTS`.
The parser scans ONLY for the signature of the asset type that actually comes next. It is set by the
caller exactly like `body_relayout.WEAPON_NEXT_HINT` (rule AK): "the upcoming body-bearing (FOLLOW)
asset roots", i.e. rule (AA) applied forward over the XAsset array. With no hint the parser falls
back to the MenuList/StringTable union and NEVER to the RawFile signature.

    MEASURED FAILURE that forced this design: a first cut scanned for the union of all three
    signatures. On `common_patch_mp` entry 5 the true follower is a TECHNIQUE_SET -- a type no
    signature scan can recognise -- so the union scan sailed past it and matched the zone's FINAL
    RawFile 'common_patch_mp' **2,786,758 B** downstream, returning a span 50x too long. Worse, by
    SUCCEEDING it suppressed the rule-(AP) resync that had been getting that entry right (55,376 B),
    and a zone that passed at 3309/3309 started dying at entry 6. A span parser that answers when it
    should raise is more damaging than one that raises: the resync is the correct tool when the
    follower is a type with no header signature, and it only runs if the delimiter FAILS.

## The three follower signatures

    MenuList    {name* ptr, menuCount<=64, menus* ptr} + '<path>.txt' or '<path>.menu'
    StringTable {name* F, cols<64, rows<4096, values* F, cellIndex* F} + '<path>.csv'
    RawFile     {name* F, len, buffer* ptr} + name + buffer, landing EXACTLY on EOF
                -- accepted ONLY when NEXT_ROOTS says RawFile, and only as the zone's last asset.
                The EOF-exact test is self-validating: a wrong offset cannot land on the last byte.

'.menu' is the addition to the MenuList signature. `_menulist_end` deliberately excluded it to avoid
matching menuDef WINDOW names inside the graph -- but a window name is a bare string, not a string
preceded by {ptr, small count, ptr}, so the full 3-word signature is what makes it safe. Measured on
en_patch_loc_mp: ONE hit across the whole 373,939-B tail.

EVIDENCE for the en_patch_loc_mp boundaries -- two INDEPENDENT bounds that agree:
    (a) backward from EOF: the only {name*, len, buffer*} + printable-name body in the last 512 B
        that lands on EOF is 0x9446d 'patch_loc_mp' len=0  (12 + 13 + len + 1 = 26 -> 0x94487 = EOF).
    (b) forward: the MenuList signature matched exactly once, 0x8dc98 menuCount=17
        'ui_mp/hud_splitscreen.menu' -- the sibling of entry 2237's 'ui_mp/hud.menu'.
A single scan hit is not normally evidence; here the guard is the end-to-end EOF check, which only
the true boundaries can satisfy.

⚠ SCOPE: this BOUNDS the span. It does NOT model the menuDef graph -- the body is copied verbatim,
exactly as `_menulist_end` intends. Growing or re-authoring a MenuList still needs the 424-B WiiU
menuDef_t reversed.
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE
PTRS = (FOLLOW, INSERT)

MENULIST_SIZE = 12          # {name*, menuCount, menus*}
RAWFILE_SIZE = 12           # {name*, len, buffer*}

# Set by the caller before each MenuList span, same contract as body_relayout.WEAPON_NEXT_HINT:
# the upcoming body-bearing asset ROOTS in stream order. None => no hint.
NEXT_ROOTS = None


class MenuListSpanError(RuntimeError):
    pass


def _u32(z, o):
    return struct.unpack_from('>I', z, o)[0]


def _name_at(z, o, cap=64):
    """Printable NUL-terminated inline name at o, or None."""
    if o >= len(z):
        return None
    e = z.find(b'\x00', o, o + cap)
    if e < 0 or e == o:
        return None
    s = z[o:e]
    if not all(32 <= ch < 127 for ch in s):
        return None
    return s


def _is_stringtable(z, p):
    """{name* F, cols<64, rows<4096, values* F, cellIndex* F} + '<path>.csv'."""
    if _u32(z, p) != FOLLOW or _u32(z, p + 12) != FOLLOW or _u32(z, p + 16) != FOLLOW:
        return False
    if not (_u32(z, p + 4) < 64 and _u32(z, p + 8) < 4096):
        return False
    nm = _name_at(z, p + 20)
    return nm is not None and len(nm) > 4 and nm.endswith(b'.csv')


#: Upper bound on menuCount for a record to be accepted as a MenuList header.
#
# ⚠⚠ THIS WAS 64 AND IT WAS CALIBRATED ON THE WRONG ZONE. `patch_mp` carries small lists, so 64
# looked generous; `ui_mp` -- the actual UI zone -- carries ONE MenuList whose menuCount is 125.
# The bound rejected the only follower in the zone, the scan then swept the whole remaining
# 36 MB matching nothing, and the walk DIED at entry 308 of 524. The user-visible symptom was
# "saving ui_mp/ui_zm fails", including a save with no edits, because the walk guard correctly
# refuses to write a zone it cannot verify.
#
# MEASURED, not guessed: ui_mp.ff has exactly one MenuList-shaped record, menuCount 125. The
# bound is set to 1024 -- comfortably above the real maximum while still rejecting the random
# 32-bit words a byte-granular scan trips over, which is the only job it has.
# ⭐ A bound like this is a deviation list: it must be RE-CALIBRATED whenever the corpus widens,
# and calibrating it on one small zone is how it came to be wrong.
MAX_MENU_COUNT = 1024


def _is_menulist(z, p):
    """{name* ptr, menuCount<=MAX_MENU_COUNT, menus* ptr} + a PATH-SHAPED inline name.

    ⚠ THE NAME NEED NOT HAVE AN EXTENSION. Requiring '.txt'/'.menu' made this blind to a real
    retail record and cost ui_mp.ff, ui_zm.ff and their localised twins entirely: entry 308
    ('ui_mp/menus.txt') is followed by entry 309 named 'ui_mp/gamesetup_systemlink', which has no
    extension at all. There is exactly ONE '.txt' string in the whole 54 MB zone -- entry 308's
    own name -- so the follower scan was guaranteed to sweep to EOF and raise. Measured after
    relaxing to a path shape: ui_mp 865/865 bodies EOF-exact, ui_zm 524/524, both localised twins
    likewise, and every other zone byte-identical.

    ⚠ THE '/' AND '@' TESTS ARE LOad-BEARING, NOT TIDINESS. Dropping the extension test alone
    admits the zone's '@PLATFORM_*'/'@MENU_*' interior tokens: over ui_mp's 8.3 MB gap a relaxed
    scan returns 21 printable-name hits, 20 of them '@'-prefixed menu-graph keys and exactly one
    -- 0x7fa454 -- the real record. Requiring a path separator and rejecting '@' leaves that one.

    ⚠ AN ALIAS-NAMED MenuList IS STILL NOT MATCHED HERE, DELIBERATELY. The PTRS test above already
    rejects it (its name word is a block-5 dedup handle, not FOLLOW/INSERT), and widening THAT is
    what makes the scan land on a wrong offset -- measured: accepting alias names bounds entry 308
    at 0x225df2 instead of 0x7fa454, a structurally "successful" walk at 4.55% coverage. The
    alias-named follower is bounded by the rule-(AP) resync instead, which is the correct tool and
    already handles it.
    """
    if _u32(z, p) not in PTRS or _u32(z, p + 8) not in PTRS:
        return False
    if _u32(z, p + 4) > MAX_MENU_COUNT:
        return False
    nm = _name_at(z, p + MENULIST_SIZE)
    if nm is None or len(nm) <= 4:
        return False
    if nm.endswith(b'.txt') or nm.endswith(b'.menu'):
        return True
    return b'/' in nm and b'@' not in nm


def _is_final_rawfile(z, p):
    """{name* F, len, buffer* ptr} + name + buffer, landing EXACTLY on EOF.

    T6 Load_RawFile streams len+1 bytes (the terminator); both len and len+1 are accepted because
    either way only the true offset can land on the zone's last byte.
    """
    if _u32(z, p) != FOLLOW or _u32(z, p + 8) not in PTRS:
        return False
    ln = _u32(z, p + 4)
    if ln > len(z):
        return False
    nm = _name_at(z, p + RAWFILE_SIZE)
    if nm is None:
        return False
    base = p + RAWFILE_SIZE + len(nm) + 1 + ln
    return base == len(z) or base + 1 == len(z)


_SIG = {'MenuList': _is_menulist, 'StringTable': _is_stringtable, 'RawFile': _is_final_rawfile}
_SAFE_UNION = ('MenuList', 'StringTable')     # never RawFile without a hint -- see module docstring


def _accepted(next_roots):
    """The signatures this call is allowed to bound on."""
    if next_roots:
        f = next_roots[0]
        if f not in _SIG:
            raise MenuListSpanError(
                'follower %r has no header signature -- refusing to scan. The rule-(AP) resync '
                'knows the follower type and is the correct tool here.' % (f,))
        return [_SIG[f]]
    return [_SIG[k] for k in _SAFE_UNION]


def parse_menulist_console(z, o, next_roots=None):
    """Span of one console MenuList body at `o`. Returns the end offset."""
    if o + MENULIST_SIZE > len(z):
        raise MenuListSpanError('MenuList root runs past EOF at 0x%x' % o)
    name_p, count = _u32(z, o), _u32(z, o + 4)
    c = o + MENULIST_SIZE
    if name_p in PTRS:
        e = z.find(b'\x00', c)
        if e < 0:
            raise MenuListSpanError('unterminated MenuList name at 0x%x' % c)
        c = e + 1
    if count == 0:
        return c                       # empty MenuList: bounded EXACTLY, no scan
    sigs = _accepted(NEXT_ROOTS if next_roots is None else next_roots)
    p = c
    n = len(z)
    while p + 24 <= n:
        for sig in sigs:
            if sig(z, p):
                return p
        p += 1                         # console bodies are not 4-aligned -> byte step
    raise MenuListSpanError('MenuList at 0x%x: no follower header in 0x%x..0x%x (looking for %s)'
                            % (o, c, n, [f.__name__ for f in sigs]))


if __name__ == '__main__':
    import os
    import sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, HERE)
    for _r in ('wiiu_ref', 'native_linker', 'WiiU_FF_Studio'):
        sys.path.insert(0, os.path.join(HERE, '..', _r))
    import zone_facts as ZF
    P = (r'C:\Users\Tony - Main Rig\AppData\Roaming\Cemu\mlc01\usr\title\0005000e'
         r'\1010cf00\content\english\en_patch_loc_mp.ff')
    d = ZF.load_ff(P)
    e = parse_menulist_console(d, 0x38fb9, ['MenuList'])
    print('MenuList @0x38fb9 -> 0x%x (%d B)   follower MenuList' % (e, e - 0x38fb9))
    e2 = parse_menulist_console(d, e, ['RawFile'])
    print('MenuList @0x%x -> 0x%x (%d B)   follower RawFile' % (e, e2, e2 - e))
    print('zone EOF 0x%x' % len(d))
    try:
        parse_menulist_console(d, 0x38fb9, ['MaterialTechniqueSet'])
        print('** techset follower did NOT raise -- BUG')
    except MenuListSpanError as ex:
        print('techset follower correctly RAISES: %s' % str(ex)[:70])
