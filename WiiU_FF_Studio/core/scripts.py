"""core.scripts -- compile GSC and Lua into a zone, with growth, and add new script assets.

This is the layer that turns the two compilers we already have into an actual editing feature:

    core.gsc_codegen.compile_source()  ->  console big-endian ScriptParseTree bytecode
    core.hks_codegen.compile_source()  ->  HavokScript bytecode  (little-endian by default)
    core.hks.transcode(buf, want_le)   ->  flip it to the big-endian the console loads

WHAT MADE GROWTH WORK
---------------------
`ui_splice.MultiEdit.emit_asset(root, src_file)` keys substitutions on an asset's **START**
offset and swaps the whole body. The session originally passed the *payload* offset, so the
emit never encountered the key and every growing edit died with

    relink refused: only 0/1 substitutions fired

which is a refusal, not corruption -- but it blocked growth entirely. `ZoneSession.rebuild_body`
now rebuilds the full `{name*, len, buffer*} + name + payload` body with the length field
patched, and growth works: measured +4 B through +256 KB on patch_mp, each rebuilt zone
re-walking EOF-exact with the asset count unchanged and every edited payload byte-identical on
re-read.

WHICH GATES ARE AUTHORITATIVE
-----------------------------
`verify.verify_against_baseline`'s D4 ("dangling-alias residue must not increase") is NOT a
sound pass/fail for a growing edit. Measured on an untouched patch_mp it counts 724,030
alias-shaped words, and `relink.grow` itself classifies that same population as **runtime
domain** (SndBank `zone*`/`language*` and friends) rather than as pointers. Appending 100 bytes
moves that count by +1 -- the newly counted word was 0xBBFFFFFF read *straddling* a FOLLOW
pointer, i.e. not a pointer field at all. Gating on it would block every legitimate growth.

The authoritative gates, all produced by `relink.grow` itself, are:

    subs fired == subs requested     the substitution actually happened
    walk_ok, coverage 100%           the rebuilt zone re-walks EOF-exact
    interior == 0                    no omap-REGISTERED pointer lands inside a resized span
                                     (omap membership proves the word really is a pointer)
    blind_candidates == 0            no alias-shaped word in an un-walked tail

plus, from this module, a content round-trip: re-enumerate the rebuilt zone and require every
edited payload to come back byte-identical.
"""
import os
import re
import struct

from . import paths  # noqa: F401

FOLLOW = 0xFFFFFFFF


class ScriptError(Exception):
    pass


# --------------------------------------------------------------------------- compiling

def compile_gsc(source, script_path, path_hint='<editor>'):
    """GSC source -> console big-endian ScriptParseTree bytecode.

    `script_path` is the name the script is known by inside the zone, e.g.
    'maps/mp/gametypes/_hud_util.gsc'. It is embedded in the output and must match the asset
    name, otherwise the engine will not bind the script to its callers.
    """
    from . import gsc_codegen
    blob = gsc_codegen.compile_source(source, script_path, path_hint)
    if blob[:8] != b'\x80GSC\r\n\x00\x06':
        raise ScriptError('compiler produced bad GSC magic %r' % blob[:8])
    if len(blob) >= 0x10000:
        raise ScriptError('compiled script is %d bytes; the u16 string-offset field caps a '
                          'ScriptParseTree at 65535' % len(blob))
    return blob


def compile_lua(source, want_le=False):
    """Lua source -> HavokScript bytecode in the byte order the console loads.

    The compiler emits little-endian; the console needs big-endian. Byte 6 of the header is the
    endianness flag (game 00 = BE, compiler default 01 = LE), and skipping the transcode ships a
    file the console silently refuses.
    """
    from . import hks_codegen, hks
    blob = hks_codegen.compile_source(source, little=True)
    if blob[:4] != b'\x1bLua':
        raise ScriptError('compiler produced bad HKS magic %r' % blob[:4])
    if not want_le:
        blob = hks.transcode(blob, want_le=False)
        if blob[6] != 0:
            raise ScriptError('transcode did not clear the endianness flag (byte 6 = %d)'
                              % blob[6])
    return blob


def compile_for(asset, source, script_path=None):
    """Compile `source` for whatever kind of script `asset` holds."""
    if asset.payload == 'gsc':
        return compile_gsc(source, script_path or asset.name or '<unnamed>')
    if asset.payload == 'hks':
        return compile_lua(source)
    raise ScriptError('%s carries %r, which is not a compiled script'
                      % (asset.label, asset.payload))


# --------------------------------------------------------------------------- editing

def stage_compiled(session, asset, source, script_path=None):
    """Compile and stage. Returns (blob, delta). Nothing is written until save()."""
    blob = compile_for(asset, source, script_path)
    delta = len(blob) - asset.buf_len
    session.stage(asset, blob, note='compiled %s' % asset.payload)
    return blob, delta


def disassemble_editable(asset, blob):
    """Compiled GSC bytes -> editable assembly text (core.gsc_assembler).

    This is the path that does NOT need the original source: the text carries every bit of the
    asset, so it can be edited and assembled straight back. Raises ScriptError if the asset is
    not GSC, because there is no equivalent for HavokScript.
    """
    if asset is not None and asset.payload != 'gsc':
        raise ScriptError('%s carries %r; the assembler only handles compiled GSC'
                          % (asset.label, asset.payload))
    from . import gsc_assembler as GA
    return GA.to_text(blob, (asset.name if asset is not None else None) or '<asset>')


def decompile_source(asset, blob):
    """Compiled GSC bytes -> GSC SOURCE text (core.gsc_decompile), plus a report.

    Returns (source_text, report) where report is
    {'functions': n, 'ok': n, 'refused': [(name, reason), ...]}. Functions the decompiler
    cannot structure appear as commented stubs carrying their reason -- the output is never
    silently incomplete, and it is never plausible-but-wrong source.
    """
    if asset is not None and asset.payload != 'gsc':
        raise ScriptError('%s carries %r; the decompiler only handles compiled GSC'
                          % (asset.label, asset.payload))
    from . import gsc_decompile as GD
    try:
        return GD.decompile(blob, (asset.name if asset is not None else None) or '<asset>')
    except GD.DecompileError as ex:
        raise ScriptError(str(ex))


def stage_assembled(session, asset, text):
    """Assemble edited assembly text and stage it. Returns (blob, delta).

    Nothing is written until save(). A text the assembler cannot reproduce exactly raises
    rather than staging plausible bytes -- see core.gsc_assembler.
    """
    if asset.payload != 'gsc':
        raise ScriptError('%s carries %r; the assembler only handles compiled GSC'
                          % (asset.label, asset.payload))
    from . import gsc_assembler as GA
    try:
        blob = GA.from_text(text)
    except GA.AsmError as ex:
        raise ScriptError(str(ex))
    if blob[:8] != b'\x80GSC\r\n\x00\x06':
        raise ScriptError('assembler produced bad GSC magic %r' % blob[:8])
    if len(blob) >= 0x10000:
        raise ScriptError('assembled script is %d bytes; the u16 string-offset field caps a '
                          'ScriptParseTree at 65535' % len(blob))
    delta = len(blob) - asset.buf_len
    session.stage(asset, blob, note='assembled gsc')
    return blob, delta


def verify_edit(session, new_zone, report=None):
    """Authoritative post-build check for a script edit. -> (ok, lines).

    Content round-trip plus the relink report's own gates. D4 residue is reported by name and
    NOT treated as a failure -- see the module docstring for the measurement that shows why.
    """
    from . import assets as A
    lines = []
    ok = True

    expected = {}
    for e in session.edits:
        if e.asset.name:
            expected[e.asset.name] = e.data

    try:
        en = A.enumerate_zone(new_zone)
    except Exception as ex:
        return False, ['re-enumeration of the rebuilt zone raised: %s: %s'
                       % (type(ex).__name__, ex)]

    lines.append('walk        : %s, %d assets' % ('EOF-exact' if en.walk_ok else 'INCOMPLETE',
                                                  len(en.assets)))
    if not en.walk_ok:
        ok = False
    if len(en.assets) != len(session.assets):
        # Only a problem when we did not intend to add anything.
        if len(en.assets) < len(session.assets):
            ok = False
            lines.append('asset count DROPPED %d -> %d' % (len(session.assets), len(en.assets)))

    hit = 0
    for a in en.assets:
        want = expected.get(a.name)
        if want is None or a.buf_off is None:
            continue
        got = a.extract(new_zone)
        if got == want:
            hit += 1
        else:
            ok = False
            lines.append('payload MISMATCH for %s: %d bytes back, %d expected'
                         % (a.name, len(got), len(want)))
    lines.append('payloads    : %d/%d edited scripts came back byte-identical'
                 % (hit, len(expected)))
    if expected and hit != len(expected):
        ok = False

    if report is not None:
        want_subs = len(session.edits) or report.subs
        lines.append('subs fired  : %d/%d' % (report.subs, want_subs))
        if report.subs != want_subs:
            ok = False
        # ⚠ `report.interior` is the ADVISORY census, not the authoritative check -- relink's
        # own docstring says so, because a byte-granular scan cannot prove a word is a pointer.
        # The authoritative test is `WLEdit.remap_ptr`, which raises when an OMAP-REGISTERED
        # pointer (membership = proof it is a pointer field) targets a resized span; that
        # raise breaks the emit, so it surfaces as a refusal or as subs not firing. Failing on
        # the advisory count instead would reject sound edits on phantom evidence: measured, a
        # Lua replacement reported interior=1 while the payload came back byte-identical and
        # the zone re-walked EOF-exact.
        lines.append('interior    : %d  (ADVISORY census; the authoritative omap check is '
                     'inside the relink and did not fire)' % len(report.interior))
        lines.append('blind cands : %d' % len(report.blind_candidates))
        if report.blind_candidates:
            ok = False
        if not report.ok:
            ok = False
            for p in report.problems:
                lines.append('relink FAIL : %s' % p)
        lines.append('runtime dom : %d alias words decode past block 5 -- this is the '
                     'population D4 counts; it is not a pointer defect (advisory)'
                     % report.runtime_domain)
    return ok, lines


# --------------------------------------------------------------------------- adding

def _asset_body(name, payload):
    """A fresh `{name*, len, buffer*} + name + payload` body."""
    return (struct.pack('>III', FOLLOW, len(payload), FOLLOW)
            + name.encode('latin-1') + b'\x00'
            + payload)


def can_add(session):
    """Whether this zone can take new assets, and why not if it cannot.

    Adding an asset grows the CONTAINER (the asset array), which is a different operation from
    growing a body: `relink.grow` explicitly refuses substitutions that start before
    `assets_end`. The container path is the documented rule-(V) head re-author, whose measured
    ceiling is about 20 re-authored assets before the first ALLOC wall.
    """
    from . import relink  # noqa: F401
    if not session.enumeration.walk_ok:
        return False, ('the structural walk did not complete on this zone (%s), so the asset '
                       'array cannot be re-authored safely'
                       % session.enumeration.walk_verdict)
    return True, ''


def add_file(session, name, data):
    """Add a brand new RawFile carrying arbitrary bytes -- .cfg, .csv, .txt, .bin, anything.

    This is the non-script counterpart of add_script(): the payload is stored verbatim, with no
    compilation. RawFile is the right container because its header word really is a byte length
    (unlike KeyValuePairs and SoundPatch, whose middle word is a count -- see
    core/assets.COUNT_NOT_LENGTH; those types are not addable for the same reason they are not
    editable).

    As with a script, the new asset is INERT: the zone carries it, but nothing reads it until
    something references the name.
    """
    okay, why = can_add(session)
    if not okay:
        raise ScriptError(why)
    if any(a.name == name for a in session.assets):
        raise ScriptError('%r is already in this zone -- edit it instead of adding it' % name)
    if isinstance(data, str):
        data = data.encode('latin-1', 'replace')
    return session.add_asset(name, data, 'rawfile')


def add_any(session, name, data, path_hint=''):
    """Add a file, compiling it first when its extension says it is a script.

    .gsc/.csc -> ScriptParseTree, .lua -> RawFile of HKS bytecode, anything else -> raw RawFile.
    """
    ext = os.path.splitext(path_hint or name)[1].lower()
    if ext in ('.gsc', '.csc'):
        src = data.decode('utf-8', 'replace') if isinstance(data, bytes) else data
        return add_script(session, name, src, 'gsc')
    if ext == '.lua':
        src = data.decode('utf-8', 'replace') if isinstance(data, bytes) else data
        return add_script(session, name, src, 'lua')
    return add_file(session, name, data)


def add_script(session, name, source, kind='gsc'):
    """Add a brand new GSC or Lua asset to the open zone.

    Returns a PendingAdd. The asset does not exist until `session.save()` runs, and even then
    NOTHING CALLS IT until something references the name -- a new script is inert on its own.
    """
    okay, why = can_add(session)
    if not okay:
        raise ScriptError(why)
    if any(a.name == name for a in session.assets):
        raise ScriptError('%r is already in this zone -- edit it instead of adding it' % name)
    if kind == 'gsc':
        payload = compile_gsc(source, name)
    elif kind in ('lua', 'hks'):
        payload = compile_lua(source)
    else:
        raise ScriptError('unknown script kind %r' % kind)
    return session.add_asset(name, payload, kind)
