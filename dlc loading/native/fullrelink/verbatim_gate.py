"""verbatim_gate — refuse to let the omap value-scan corrupt VERBATIM asset bodies.

THE DEFECT
----------
`body_relayout.emit_verbatim` relinks a verbatim asset's body with a blind 4-aligned scan that
rewrites any word whose decoded block-5 offset happens to be a REGISTERED omap target. Inside a
verbatim body there is no field-role information, so this cannot tell a pointer from data.

Measured on patch_mp for the shipped +92 growth: EVERY word it rewrote inside a verbatim body is a
FALSE POSITIVE. All of them target a **STRINGTABLE interior** at arbitrary binary offsets, and the
bytes there are shader/vertex garbage, not names or struct headers:

    0x09e9381 TECHNIQUE_SET 0xA0ADACF5 -> target 0x0ADAD34 inside STRINGTABLE  'EM_BACK_jama'
    0x0c3251b XMODEL        0xA0AD0782 -> target 0x0AD07C1 inside STRINGTABLE  '..-e.......0'
    0x0dd47b6 MATERIAL      0xA0805F0A -> target 0x0805F49 inside STRINGTABLE  '337.global_z'
    ... (~25 sites across MATERIAL / XMODEL / FX / TECHNIQUE_SET)

A Material/XModel/FX/TechniqueSet pointing into a StringTable's *interior* is structurally
impossible. STRINGTABLE is emitted STRUCTURALLY and registers a very large number of interior
sub-allocations (671,282 B of cells across 38 tables), so it dominates omap density and soaks up
random alias-range garbage. omap hit-rate is ~1 per 195 bytes of zone, so a few thousand
alias-looking data words will produce tens of coincidental hits.

⇒ The omap whitelist's problem in verbatim bodies is FALSE POSITIVES (data corrupted), not false
  negatives. This is the true origin of the "+92 technique alias" regression documented in
  WARNING_patchmp_growth_alias_bump.md. That doc's REPAIR (restore the retail word) is CORRECT;
  only its mechanism story (a MaterialTechnique with passCount==0) is wrong -- the word was never a
  technique pointer, and its target is not a technique record.

  ⚠ The doc's gate repairs exactly ONE of the ~25 sites. The rest are still corrupted in every
  grown build shipped so far.

THE RULE THIS GATE ENFORCES
---------------------------
    No word inside a VERBATIM asset body may differ from its retail value.

Verbatim bodies are copied byte-for-byte; the only legitimate change is the body's POSITION. Any
value change is by definition the blind scan misfiring. Genuine cross-asset pointers out of these
types are FOLLOW (0xFFFFFFFF) or are handled structurally, so nothing legitimate is lost.

If field-level relinkers are added later (see the true-relink plan), register those exact field
offsets via `allow` so the gate permits them and keeps rejecting everything else.
"""
import struct
import wiiu_zone, struct_layout, zone_stream as zs
import walker as W
import body_relayout as BR
import ui_splice as US


def asset_spans(zone):
    """[(start, end, type_name, is_verbatim)] in stream order, from the structural walk."""
    r = wiiu_zone.ZoneReader(zone); r.read_string_table(); r.read_asset_list()
    L = struct_layout.Layout(W.HDR, console=True); zc = W.ZoneCode(W.ZC_DIR)
    w = zs.ZoneWriter(); w.push_block(zs.BLOCK_VIRTUAL)
    w.block_size[zs.BLOCK_VIRTUAL] = r.assets_end - BR.B5_BASE
    em = US.MultiEdit(zone, L, zc, w, {}); em.delta = 0
    cur, out = r.assets_end, []
    for (cid, pc_, nm) in r.assets:
        root = W.ASSET_ROOT.get(nm)
        if root is None or root not in L.structs:
            continue
        s = cur
        try:
            cur = em.emit_asset(root, cur)
        except Exception:
            break
        out.append((s, cur, nm, root in BR.DELIMITERS))
    return out


def find_violations(retail, grown, insertion, delta, spans=None, allow=()):
    """Every 4-aligned word inside a VERBATIM body whose value differs from retail.

    `insertion`/`delta` describe the growth so a body's shifted position is accounted for:
    a body starting at/after `insertion` sits `delta` bytes later in `grown`.
    `allow` = set of retail file offsets that a field-level relinker is entitled to change.
    """
    spans = spans if spans is not None else asset_spans(retail)
    bad = []
    for s, e, nm, vb in spans:
        # ⚠ Do NOT skip spans that END before the insertion point. A verbatim body sitting BEFORE the
        # insertion can still hold an alias word whose TARGET is after it, and the blind omap scan
        # bumps that word in place. Measured: patch_mp TECHNIQUE_SET span 0x85B5..0x20800 has such a
        # word at 0x1A4D1 (0xA0880882 -> 0xA08808DE, target 0x8808C1 > insertion 0x328B28). An
        # earlier version of this gate skipped pre-insertion spans and let that one corruption ship
        # in every grown build. The freeze rule is positional-independent: NO verbatim word may
        # differ from retail, wherever the body sits.
        if not vb:
            continue
        sh = delta if s >= insertion else 0
        for fo in range(s, e - 3, 4):
            if fo in allow:
                continue
            a = struct.unpack_from('>I', retail, fo)[0]
            b = struct.unpack_from('>I', grown, fo + sh)[0]
            if a != b:
                bad.append((fo, fo + sh, nm, a, b))
    return bad


def enforce(retail, grown, insertion, delta, spans=None, allow=(), verbose=True):
    """Restore every violating word to its retail value. Returns (new_grown, restored_list)."""
    out = bytearray(grown)
    bad = find_violations(retail, out, insertion, delta, spans, allow)
    for fo, go, nm, a, b in bad:
        struct.pack_into('>I', out, go, a)
    if verbose:
        by = {}
        for fo, go, nm, a, b in bad:
            by[nm] = by.get(nm, 0) + 1
        print('   verbatim gate: restored %d word(s) %s'
              % (len(bad), ' '.join('%s=%d' % kv for kv in sorted(by.items()))))
    left = find_violations(retail, out, insertion, delta, spans, allow)
    assert not left, 'verbatim gate failed to converge: %d left' % len(left)
    return bytes(out), bad
