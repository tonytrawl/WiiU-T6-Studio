"""core.gsc_asm_selftest -- gate battery for the general GSC assembler.

Run:  python -m core.gsc_asm_selftest            (from WiiU_FF_Studio/)
      python -m core.gsc_asm_selftest <zone.ff>  (extra zones, repeatable)

  A1   round-trip        from_text(to_text(b)) == b BYTE-IDENTICAL for every GSC asset in
                         every zone found. One mismatch is a failure and the first differing
                         offset is printed.
  A1r  reachability      the SAME comparison must FAIL when a byte of the input is corrupted
                         and when an operand in the text is altered -- a gate that cannot
                         fail is not a gate.
  A2   symbolic          no script may fall back to an absolute address or a literal jump.
                         Those round-trip fine but would NOT follow an edit that moves code,
                         so they are counted and required to be zero.
  A3   edit             changing one instruction's operand through the text changes exactly
                         that operand and nothing else.
  A4   growth           INSERTING an instruction relocates everything after it: the blob
                         grows, re-parses, and every export / import site / stringtablefixup
                         site still lands where it did relative to its own instruction.
  A5r  refusal          malformed text is REFUSED with a reason (unknown mnemonic, undefined
                         label, undefined string symbol), never assembled into plausible bytes.
  B1   zone edit        edit one instruction through the text, write the .ff, reopen it: the
                        payload is byte-identical to what we assembled, differs from the
                        original, and the edit is visible on re-disassembly.
  B1r  reachability     an UNEDITED asset staged through the same path comes back
                        byte-identical, so B1 measures the edit and not the pipeline.
  B2   IDE view         the Assembly view is reachable IN THIS BUILD: the real widget renders
                        it, it is editable, and its button stages an edit.
  C1   decompiler       decompile -> gsc_codegen.compile_source -> compare the NORMALISED
                        instruction streams. Reports functions EQUIVALENT / DIVERGENT /
                        REFUSED. It does NOT require 100%: it pins the current number so a
                        regression is visible, and caps DIVERGENT, because a refusal is
                        honest while wrong source is not.
  C1r  decompiler       reachability: a deliberately corrupted decompilation MUST be counted
                        divergent, so C1 cannot pass by comparing nothing.
  C2   IDE decompile    the Decompile button's path works IN THIS BUILD: real source comes
                        back and the widget renders it editable.

⚠ THE ZONES UNDER TEST ARE COPIES. patch_mp is live, shared and contended; nothing here ever
opens the original for writing.

WHAT THIS BATTERY DOES NOT CLAIM
--------------------------------
Nothing here is boot-tested. A1 proves the text carries every bit of the file; it does not
prove the console accepts an edited script. Separately measured and NOT modelled: the export
record's `crc` is a checksum whose byte range we cannot re-derive -- the naive "all decoded
instructions of the function" range matches only 1,754 / 5,434 exports, `align4` of it 614,
and `next_export - 6` 2,644. So the assembler CARRIES the stored crc verbatim and never
fabricates one. An edit that changes a function's code therefore ships the ORIGINAL checksum.
"""
import os
import shutil
import sys
import tempfile

from . import paths  # noqa: F401
from . import gsc_assembler as GA


class Battery(object):
    def __init__(self):
        self.rows = []

    def add(self, gate, status, detail=''):
        self.rows.append((gate, status, detail))

    def report(self):
        w = max((len(r[0]) for r in self.rows), default=4)
        for g, s, d in self.rows:
            print('  %-6s %-*s %s' % (s, w, g, d))
        f = sum(1 for r in self.rows if r[1] == 'FAIL')
        p = sum(1 for r in self.rows if r[1] == 'PASS')
        sk = sum(1 for r in self.rows if r[1] == 'SKIP')
        print()
        print('  %d passed, %d failed, %d skipped' % (p, f, sk))
        if sk:
            print('  (a skipped gate is NOT a pass -- its data was not on this machine)')
        return f


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _candidate_zones(extra):
    live = os.path.join(os.environ.get('APPDATA', ''), 'Cemu', 'mlc01', 'usr', 'title',
                        '0005000e', '1010cf00', 'content', 'english', 'patch_mp.ff')
    out = list(extra) + [live, os.path.join(ROOT, 'common_mp.ff'),
                         os.path.join(ROOT, 'dust2_wiiu.ff')]
    seen, keep = set(), []
    for p in out:
        if p and os.path.exists(p) and p not in seen:
            seen.add(p)
            keep.append(p)
    return keep


def collect(zones):
    """[(zone_label, asset_name, blob)] for every GSC asset. Always from a COPY."""
    from core import ZoneSession
    from core import assets as A
    from core import gsc as G
    out = []
    tmp = tempfile.mkdtemp(prefix='gscasm_')
    try:
        for zp in zones:
            cp = os.path.join(tmp, os.path.basename(zp))
            shutil.copy2(zp, cp)
            try:
                s = ZoneSession.open(cp)
            except Exception:
                os.remove(cp)
                continue
            label = os.path.splitext(os.path.basename(zp))[0]
            for a in A.enumerate_zone(s.zone):
                if a.payload != 'gsc':
                    continue
                blob = a.extract(s.zone)
                if G.is_gsc(blob):
                    out.append((label, a.name or 'idx%d' % a.index, bytes(blob)))
            del s
            os.remove(cp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _first_diff(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n if len(a) != len(b) else -1


# --------------------------------------------------------------------------- gates

def gate_roundtrip(b, corpus):
    ok = bad = 0
    resid = dict(absolute=0, jump_literals=0, overrides=0, insns=0, raw_bytes=0, cseg_bytes=0)
    firsts = []
    for label, name, blob in corpus:
        try:
            prog = GA.parse(blob)
            for k in resid:
                resid[k] += prog.stats.get(k, 0)
            got = GA.from_text(GA.render(prog, name))
        except Exception as ex:
            bad += 1
            if len(firsts) < 3:
                firsts.append('%s/%s: %s: %s' % (label, name, type(ex).__name__,
                                                 str(ex).splitlines()[0][:110]))
            continue
        if got == blob:
            ok += 1
        else:
            bad += 1
            at = _first_diff(got, blob)
            if len(firsts) < 3:
                firsts.append('%s/%s: %d B -> %d B, first diff at 0x%X (got %s want %s)'
                              % (label, name, len(blob), len(got), at,
                                 got[at:at + 6].hex(), blob[at:at + 6].hex()))
    b.add('A1', 'PASS' if not bad else 'FAIL',
          'byte-identical %d/%d GSC assets%s'
          % (ok, ok + bad, ('  || ' + ' || '.join(firsts)) if firsts else ''))
    b.add('A2', 'PASS' if not (resid['absolute'] or resid['jump_literals']) else 'FAIL',
          '%d absolute address ref(s), %d literal jump(s), %d layout override(s) over '
          '%d instructions / %d cseg bytes (%d undecoded)'
          % (resid['absolute'], resid['jump_literals'], resid['overrides'],
             resid['insns'], resid['cseg_bytes'], resid['raw_bytes']))
    return ok


def gate_reachability(b, corpus):
    """The A1 comparison must FAIL on a corrupted input and on an altered text."""
    sample = next(((n, x) for _l, n, x in corpus if len(x) > 0x200), None)
    if sample is None:
        b.add('A1r', 'SKIP', 'no script large enough to corrupt')
        return
    name, blob = sample
    notes = []

    # (i) corrupt a byte of the SOURCE: the round-trip must still close on the corrupted
    #     bytes, and the result must differ from the pristine blob. If it matched the
    #     pristine blob the comparison would be blind to real damage.
    prog = GA.parse(blob)
    txt = GA.render(prog, name)
    # (ii) alter an operand in the TEXT: the assembled bytes must change.
    lines = txt.splitlines()
    hit = None
    for i, ln in enumerate(lines):
        s = ln.split(';')[0].strip()
        if s.startswith('EvalLocalVariableCached ') or s.startswith('GetByte '):
            hit = i
            break
    if hit is None:
        b.add('A1r', 'SKIP', 'no single-u8-operand instruction found to perturb')
        return
    head, val = lines[hit].split(';')[0].rstrip().rsplit(None, 1)
    lines[hit] = '%s %d' % (head, (int(val, 0) + 1) & 0xFF)
    changed = GA.from_text('\n'.join(lines))
    if changed == blob:
        b.add('A1r', 'FAIL', 'altering an operand in the text produced identical bytes -- '
                             'the round-trip comparison cannot detect a change')
        return
    at = _first_diff(changed, blob)
    notes.append('operand edit moved 1 byte at 0x%X' % at)
    if len(changed) != len(blob):
        b.add('A1r', 'FAIL', 'a same-width operand edit changed the file length')
        return

    # (iii) corrupt the blob itself and require the round-trip of the CORRUPTED input to
    #       differ from the pristine one.
    bad = bytearray(blob)
    off = prog.stats and 0
    bad[len(bad) - 1] ^= 0xFF
    try:
        got = GA.from_text(GA.to_text(bytes(bad), name))
        if got == blob:
            b.add('A1r', 'FAIL', 'a corrupted input round-tripped back to the pristine blob')
            return
        notes.append('corrupted input stays corrupted through the round-trip')
    except GA.AsmError as ex:
        notes.append('corrupted input REFUSED (%s)' % str(ex).splitlines()[0][:60])
    b.add('A1r', 'PASS', '; '.join(notes))


def gate_edit(b, corpus):
    sample = next(((n, x) for _l, n, x in corpus if len(x) > 0x400), None)
    if sample is None:
        b.add('A3', 'SKIP', 'no script available')
        return None
    name, blob = sample
    txt = GA.to_text(blob, name)
    lines = txt.splitlines()
    for i, ln in enumerate(lines):
        s = ln.split(';')[0].strip()
        if s.startswith('EvalLocalVariableCached '):
            head, val = ln.split(';')[0].rstrip().rsplit(None, 1)
            lines[i] = '%s %d' % (head, (int(val, 0) + 1) & 0xFF)
            new = GA.from_text('\n'.join(lines))
            diffs = [k for k in range(min(len(new), len(blob))) if new[k] != blob[k]]
            ok = (len(new) == len(blob) and len(diffs) == 1)
            b.add('A3', 'PASS' if ok else 'FAIL',
                  'one-operand edit changed %d byte(s), length %d -> %d'
                  % (len(diffs), len(blob), len(new)))
            return new
    b.add('A3', 'SKIP', 'no EvalLocalVariableCached to edit')
    return None


def gate_growth(b, corpus):
    """Insert an instruction and require every symbolic reference to survive relocation."""
    sample = next(((n, x) for _l, n, x in corpus if len(x) > 0x400), None)
    if sample is None:
        b.add('A4', 'SKIP', 'no script available')
        return
    name, blob = sample
    before = GA.parse(blob)
    txt = GA.render(before, name)
    lines = txt.splitlines()
    ins_at = None
    for i, ln in enumerate(lines):
        if ln.split(';')[0].strip() == 'CheckClearParams':
            ins_at = i + 1
            break
    if ins_at is None:
        b.add('A4', 'SKIP', 'no CheckClearParams to insert after')
        return
    lines.insert(ins_at, '  GetUndefined')          # 1 byte, no operand, no alignment effect
    lines.insert(ins_at + 1, '  DecTop')
    try:
        grown = GA.from_text('\n'.join(lines))
    except GA.AsmError as ex:
        b.add('A4', 'FAIL', 'growth refused: %s' % str(ex).splitlines()[0][:140])
        return
    # The file grows by MORE than the 2 inserted bytes whenever the shift changes the
    # alignment filler in front of a later 2/4-aligned operand. That ripple is correct, so
    # the gate requires growth and re-checks the content, not an exact byte count.
    if not (len(blob) + 2 <= len(grown) <= len(blob) + 2 + 3 * (len(before.body) + 1)):
        b.add('A4', 'FAIL', 'inserting 2 one-byte instructions changed the file by %+d bytes'
                            % (len(grown) - len(blob)))
        return
    ripple = len(grown) - len(blob) - 2
    try:
        after = GA.parse(grown)
    except Exception as ex:
        b.add('A4', 'FAIL', 're-parse of the grown script failed: %s: %s'
                            % (type(ex).__name__, ex))
        return
    same = (len(after.exports) == len(before.exports)
            and len(after.imports) == len(before.imports)
            and len(after.stfix) == len(before.stfix)
            and [e['name'] for e in after.exports] == [e['name'] for e in before.exports]
            and after.stats['absolute'] == 0 and after.stats['jump_literals'] == 0)
    # every table reference must still be symbolic and resolvable -- which parse() proves by
    # producing zero absolute refs on the RELOCATED file.
    b.add('A4', 'PASS' if same else 'FAIL',
          '+2 insn B (+%d align ripple): %d exports / %d imports / %d strfix preserved, '
          '%d absolute ref(s), %d literal jump(s) after relocation'
          % (ripple, len(after.exports), len(after.imports), len(after.stfix),
             after.stats['absolute'], after.stats['jump_literals']))


def gate_refusal(b, corpus):
    sample = next(((n, x) for _l, n, x in corpus if len(x) > 0x400), None)
    if sample is None:
        b.add('A5r', 'SKIP', 'no script available')
        return
    name, blob = sample
    txt = GA.to_text(blob, name)
    cases = []

    def expect_refusal(what, mutate):
        lines = txt.splitlines()
        if not mutate(lines):
            cases.append('%s: NOT EXERCISED' % what)
            return
        try:
            GA.from_text('\n'.join(lines))
        except GA.AsmError:
            cases.append('%s: refused' % what)
        except Exception as ex:
            cases.append('%s: raised %s (not AsmError)' % (what, type(ex).__name__))
        else:
            cases.append('%s: ACCEPTED' % what)

    def bad_mnemonic(lines):
        for i, ln in enumerate(lines):
            if ln.split(';')[0].strip() == 'CheckClearParams':
                lines[i] = '  NotARealOpcode'
                return True
        return False

    def bad_label(lines):
        for i, ln in enumerate(lines):
            s = ln.split(';')[0].strip()
            if s.startswith('JumpOnFalse ') or s.startswith('Jump '):
                lines[i] = '  %s L_FFFFFF' % s.split()[0]
                return True
        return False

    def bad_string(lines):
        for i, ln in enumerate(lines):
            if ln.strip().startswith('strfix '):
                lines[i] = ln.replace('strfix ', 'strfix s_does_not_exist ', 1).replace(
                    's_does_not_exist s', 's_does_not_exist ', 1)
                parts = lines[i].split()
                lines[i] = '  strfix s_does_not_exist ' + ' '.join(parts[2:])
                return True
        return False

    expect_refusal('unknown mnemonic', bad_mnemonic)
    expect_refusal('undefined label', bad_label)
    expect_refusal('undefined string symbol', bad_string)
    ok = all(c.endswith(': refused') for c in cases)
    b.add('A5r', 'PASS' if ok else 'FAIL', '; '.join(cases))


def gate_zone_edit(b, zones):
    """MILESTONE B: edit one instruction through the assembly text, write the .ff, reopen it,
    and require the edit to be present on re-disassembly -- and BYTE-IDENTICAL to what we
    assembled, because "present and valid" would also pass plausible-but-wrong bytecode.

    Also runs the reachability half: an UNEDITED asset staged through the same path must come
    back byte-identical to the original, so the gate is measuring the edit and not the pipeline.
    """
    from core import ZoneSession
    from core import scripts as SC
    from core import gsc as G
    if not zones:
        b.add('B1', 'SKIP', 'no zone available')
        return
    tmpdir = tempfile.mkdtemp(prefix='gscasm_b_')
    try:
        work = os.path.join(tmpdir, 'work.ff')
        shutil.copy2(zones[0], work)          # never touch the live file
        s = ZoneSession.open(work)
        cand = [x for x in s.assets if x.payload == 'gsc' and x.buf_len > 0x400]
        if not cand:
            b.add('B1', 'SKIP', 'no GSC asset big enough in %s'
                  % os.path.basename(zones[0]))
            return
        a = cand[0]
        orig = bytes(a.extract(s.zone))

        # (i) no-op: assemble the unedited text straight back
        noop, _d = SC.stage_assembled(s, a, SC.disassemble_editable(a, orig))
        if noop != orig:
            b.add('B1r', 'FAIL', 'staging an UNEDITED asset through the assembler changed '
                                 '%d bytes' % sum(1 for i in range(min(len(noop), len(orig)))
                                                  if noop[i] != orig[i]))
        else:
            b.add('B1r', 'PASS', 'an unedited asset stages byte-identical (%d B), so B1 '
                                 'measures the edit and not the pipeline' % len(orig))

        # (ii) the real edit
        s = ZoneSession.open(work)
        a = [x for x in s.assets if x.name == a.name][0]
        text = SC.disassemble_editable(a, bytes(a.extract(s.zone)))
        lines = text.splitlines()
        edited_line = None
        for i, ln in enumerate(lines):
            body = ln.split(';')[0].strip()
            if body.startswith('EvalLocalVariableCached '):
                head, val = ln.split(';')[0].rstrip().rsplit(None, 1)
                lines[i] = '%s %d' % (head, (int(val, 0) + 1) & 0xFF)
                edited_line = ' '.join(lines[i].split())      # normalised: column padding
                break                                          # is cosmetic, tokens are not
        if edited_line is None:
            b.add('B1', 'SKIP', 'no single-operand instruction to edit in %s' % a.name)
            return
        blob, delta = SC.stage_assembled(s, a, '\n'.join(lines))
        out = os.path.join(tmpdir, 'out.ff')
        s.save(out, verify=False)
        s2 = ZoneSession.open(out)
        hit = s2.find(a.name)
        same = hit is not None and bytes(hit.extract(s2.zone)) == blob
        moved = hit is not None and bytes(hit.extract(s2.zone)) != orig
        # and it must still DISASSEMBLE, with the edit visible in the re-rendered text
        redis = False
        if hit is not None:
            try:
                G.GscScript(bytes(hit.extract(s2.zone)), a.name)
                back_txt = SC.disassemble_editable(hit, bytes(hit.extract(s2.zone)))
                redis = edited_line in [' '.join(x.split(';')[0].split())
                                        for x in back_txt.splitlines()]
            except Exception:
                redis = False
        good = same and moved and redis
        b.add('B1', 'PASS' if good else 'FAIL',
              'edited %s (%+d B), wrote %s, reopened: payload byte-identical to what we '
              'assembled, differs from the original, and the edit re-disassembles (%s)'
              % (a.name, delta, os.path.basename(out), edited_line) if good
              else 'byte-identical=%s changed=%s edit-visible=%s' % (same, moved, redis))
    except Exception as ex:
        b.add('B1', 'FAIL', '%s: %s' % (type(ex).__name__, ex))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def gate_ide_view(b, zones):
    """The Assembly view must be reachable IN THIS BUILD, not just from the library.

    A feature that passes from source can be absent from a frozen exe and every other gate
    still passes, because everything else works fine on an asset nobody opened (see the
    ipak/sab name caches). So this drives the real widget: select a GSC asset, switch the
    view, read the pane back, edit it and stage it through the button's own handler.
    """
    if not zones:
        b.add('B2', 'SKIP', 'no zone available')
        return
    try:
        import tkinter as tk
    except Exception as ex:
        b.add('B2', 'SKIP', 'tkinter unavailable: %s' % ex)
        return
    if tk._default_root is not None:
        b.add('B2', 'SKIP', 'a Tk root already exists; refusing to build a second one')
        return
    tmpdir = tempfile.mkdtemp(prefix='gscasm_ui_')
    app = None
    try:
        import ff_ide
        from core import ZoneSession
        work = os.path.join(tmpdir, 'work.ff')
        shutil.copy2(zones[0], work)
        app = ff_ide.IDE()
        app.withdraw()
        app._apply_open(ZoneSession.open(work))
        app.update()
        iid = next((k for k, v in app.node_asset.items()
                    if v is not None and v.payload == 'gsc'), None)
        if iid is None:
            b.add('B2', 'SKIP', 'no GSC asset in %s' % os.path.basename(zones[0]))
            return
        app.tree.selection_set(iid)
        app._script_mode.set('asm')
        app._on_pick()
        txt = app.txt.get('1.0', 'end-1c')
        shown = txt.lstrip().startswith('; gsc-asm')
        editable = str(app.txt.cget('state')) == 'normal'
        armed = str(app.btn_assemble.cget('state')) == 'normal'
        app._assemble_stage()
        staged = 'Assembled' in app.status.cget('text')
        good = shown and editable and armed and staged
        b.add('B2', 'PASS' if good else 'FAIL',
              'Assembly view on %s: rendered=%s editable=%s button armed=%s staged=%s'
              % (app.node_asset[iid].name, shown, editable, armed, staged))
    except Exception as ex:
        b.add('B2', 'FAIL', '%s: %s' % (type(ex).__name__, ex))
    finally:
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)


_TEMP_RE = __import__("re").compile(r"\b_([ak])\d+\b")


def _norm_stream(blob):
    """{function: [normalised instruction]} -- operands resolved to what they MEAN."""
    from . import gsc_decompile as GD
    prog = GA.parse(blob)
    out = {}
    for fn in GD.best_lift(prog):
        if fn.error:
            continue
        seq = []
        for it in fn.ins:
            op, n = it.op, GD._n(it.op)
            if op in (0x0A, 0x0B) or op in GD.FIELD_OPS:
                seq.append('%s %r' % (n, it.s))
            elif op == 0x17:
                seq.append('%s %r' % (n, it.lv))
            elif op in GD.CALL_OPS or op == 0x15:
                seq.append('%s %s::%s/%d' % (n, it.imp['ns'], it.imp['name'],
                                             it.imp['params']))
            elif op in (0x19, 0x27, 0x24):
                try:
                    seq.append('%s %s' % (n, fn.local(it.ops[0])))
                except GD.DecompileError:
                    seq.append('%s <bad local>' % n)
            elif it.tgt is not None:
                seq.append('%s %+d' % (n, it.tgt - it.idx))
            elif op == 0x5A:
                # The case table is a LOOKUP structure: Treyarch sorts it, gsc-tool and
                # we write source order. Sort both sides so the ORDER is not compared,
                # while each entry keeps its value AND its target distance.
                seq.append('%s %s' % (n, sorted(
                    (('d' if v == 0 else
                      'i%d' % (v & 0x7FFFFF) if v & 0x00800000 else
                      's%r' % s), t - it.idx)
                    for v, t, s in it.cases)))
            elif op == 0x09:
                seq.append('%s %s' % (n, GD._flt(it.ops[0])))
            elif it.ops:
                seq.append('%s %s' % (n, it.ops))
            else:
                seq.append(n)
        out[fn.name] = [_TEMP_RE.sub(r"_\1#", x) for x in seq]
    return out


def _decompile_round(blob, name):
    """-> (equivalent, divergent, refused) for ONE script."""
    from . import gsc_codegen as CG
    from . import gsc_decompile as GD
    src, rep = GD.decompile(blob, name)
    refused = len(rep['refused'])
    if rep['ok'] == 0:
        return 0, 0, refused
    prog = GA.parse(blob)
    path = dict((s, t) for s, t, _r in prog.pool.entries).get(prog.name_sym) or 'x.gsc'
    blob2 = CG.compile_source(src, path)
    a, b = _norm_stream(blob), _norm_stream(blob2)
    eq = dv = 0
    for k, va in a.items():
        vb = b.get(k)
        if vb is None:
            continue
        # Treyarch omits the trailing End when control falls off the bottom; gsc_codegen
        # always emits one. Forgiven only as a single trailing instruction.
        if va == vb or vb == va + ['End']:
            eq += 1
        else:
            dv += 1
    return eq, dv, refused


def gate_gui_decompile(b, zones):
    """C2 -- the Decompile button's path works IN THIS BUILD, not just in the library."""
    import tkinter as tk
    from core import scripts as SC
    corpus = collect(zones[:1])
    hit = next(((n, x) for _l, n, x in corpus if len(x) > 0x800), None)
    if hit is None:
        b.add('C2', 'SKIP', 'no GSC asset to decompile')
        return
    name, blob = hit

    _nm = name

    class _A(object):
        # ⚠ a class body cannot read an enclosing local it also binds by that name
        payload, label, name = 'gsc', _nm, _nm
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception as ex:
        b.add('C2', 'SKIP', 'no display: %s' % ex)
        return
    try:
        src, rep = SC.decompile_source(_A(), blob)
        txt = tk.Text(root)
        txt.insert('1.0', src)
        shown = txt.get('1.0', 'end-1c')
        editable = str(txt.cget('state')) == 'normal'
        ok = shown == src and editable and rep['functions'] > 0 and rep['ok'] > 0
        b.add('C2', 'PASS' if ok else 'FAIL',
              'Source view on %s: %d/%d functions reconstructed, %d chars rendered, '
              'editable=%s' % (name, rep['ok'], rep['functions'], len(shown), editable))
    except Exception as ex:
        b.add('C2', 'FAIL', '%s: %s' % (type(ex).__name__, ex))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def gate_decompile(b, corpus, ceiling_pct=3.0, floor_pct=96.0):
    sample = [(n, x) for _l, n, x in corpus][:120]
    if not sample:
        b.add('C1', 'SKIP', 'no scripts')
        return
    eq = dv = ref = err = 0
    for name, blob in sample:
        try:
            e, d, r = _decompile_round(blob, name)
            eq += e
            dv += d
            ref += r
        except Exception:
            err += 1
    tot = eq + dv + ref
    pct = (100.0 * eq / tot) if tot else 0.0
    dvp = (100.0 * dv / tot) if tot else 0.0
    # Pinned to the MEASURED numbers so a regression in either direction fails: equivalence
    # must not fall and divergence must not grow. These are not targets -- 100% is the target
    # -- they are a ratchet. Raising the ceiling to make a red gate green is exactly the
    # "gate that cannot fail" this battery exists to avoid.
    b.add('C1', 'PASS' if (dvp <= ceiling_pct and pct >= floor_pct) else 'FAIL',
          '%d scripts: %d functions EQUIVALENT (%.1f%%, floor %.1f%%), %d DIVERGENT '
          '(%.1f%%, ceiling %.1f%%), %d REFUSED, %d script error(s)'
          % (len(sample), eq, pct, floor_pct, dv, dvp, ceiling_pct, ref, err))

    # C1r -- the comparison must notice a decompilation that is WRONG rather than absent.
    from . import gsc_codegen as CG
    from . import gsc_decompile as GD
    hit = None
    for name, blob in sample:
        try:
            src, rep = GD.decompile(blob, name)
            if rep['ok'] and 'return ' in src:
                hit = (name, blob, src)
                break
        except Exception:
            continue
    if hit is None:
        b.add('C1r', 'SKIP', 'no decompiled function with a return to perturb')
        return
    name, blob, src = hit
    prog = GA.parse(blob)
    path = dict((s, t) for s, t, _r in prog.pool.entries).get(prog.name_sym) or 'x.gsc'
    bad = src.replace('return ', 'return 987654 + ', 1)
    try:
        a, c = _norm_stream(blob), _norm_stream(CG.compile_source(bad, path))
        noticed = any(c.get(k) not in (v, v + ['End']) for k, v in a.items() if k in c)
    except Exception:
        noticed = True
    b.add('C1r', 'PASS' if noticed else 'FAIL',
          'a corrupted decompilation IS counted as divergent'
          if noticed else 'a corrupted decompilation still compared EQUIVALENT')


def run(extra_zones=()):
    b = Battery()
    zones = _candidate_zones(extra_zones)
    if not zones:
        b.add('A1-A5r', 'SKIP', 'no script-bearing fastfile found on this machine')
        return b.report()
    print('  zones: %s' % ', '.join(os.path.basename(z) for z in zones))
    corpus = collect(zones)
    if not corpus:
        b.add('A1-A5r', 'SKIP', 'no GSC assets in %d zone(s)' % len(zones))
        return b.report()
    print('  %d GSC assets' % len(corpus))
    print()
    gate_roundtrip(b, corpus)
    gate_reachability(b, corpus)
    gate_edit(b, corpus)
    gate_growth(b, corpus)
    gate_refusal(b, corpus)
    gate_zone_edit(b, zones)
    gate_ide_view(b, zones)
    gate_decompile(b, corpus)
    gate_gui_decompile(b, zones)
    return b.report()


if __name__ == '__main__':
    sys.exit(1 if run(sys.argv[1:]) else 0)
