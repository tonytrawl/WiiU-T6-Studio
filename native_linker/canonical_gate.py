#!/usr/bin/env python3
"""canonical_gate -- a duplicated module must be a RE-EXPORT, never a second implementation.

WHY THIS EXISTS
---------------
`stage1_roundtrip.py` shipped a container-parsing defect that corrupted every zone it round-tripped
(the depends region was never consumed, plus a spurious align(4) before the asset array). The model
it got wrong was ALREADY CORRECT in `wiiu_ref/wiiu_zone.py`, documented in comments. One file had
simply never inherited the fix.

Three copies of that file existed. Two had already diverged from canonical BEFORE the defect was
found (identical to each other, dated 2026-07-06 and 07-12). Syncing them is a patch on a hazard:
the next `cp` re-opens it, and nothing complains until a lane silently imports a stale parser and
writes a broken zone.

So: exactly one copy of each guarded module carries code. Every other copy is a thin shim that
loads the canonical file and re-exports it. This gate FAILS if any non-canonical copy contains a
definition of its own, which is what a re-introduced real copy looks like.

⚠ THIS IS A STRUCTURAL CHECK, NOT A CONTENT DIFF. Comparing the copies byte-for-byte would pass
the moment someone syncs them again and then drift the instant someone edits one. Requiring the
non-canonical copies to hold NO definitions is the property that cannot silently rot.

Run:  python canonical_gate.py          (from native_linker/, or anywhere)
Exit: 0 all clear, 1 a divergent copy exists
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))

#: module basename -> (canonical path, [paths that MUST be re-export shims])
#: Add an entry the moment a module gains a second copy. Keep the canonical FIRST.
GUARDED = {
    'stage1_roundtrip.py': (
        os.path.join(ROOT, 'native_linker', 'stage1_roundtrip.py'),
        [os.path.join(ROOT, 'WiiU_FF_Studio', 'dist', 'native_linker', 'stage1_roundtrip.py'),
         os.path.join(ROOT, 'WiiU_T6_Toolkit', 'native_linker', 'stage1_roundtrip.py')],
    ),
    'grow_relink.py': (
        os.path.join(ROOT, 'dlc loading', 'native', 'fullrelink', 'grow_relink.py'),
        [os.path.join(ROOT, 'WiiU_T6_Toolkit', 'dlc loading', 'native', 'fullrelink',
                      'grow_relink.py')],
    ),
    # Added 2026-08-17 by the substrate lane's request. zone_facts was rewritten to DELEGATE its
    # container model to wiiu_zone (console) / pc_zone (PC); both copies below were the v1 that
    # re-derived it, i.e. a second container parser. The v1 is preserved on purpose at
    # native_linker/substrate_fixtures/zone_facts_v1_rederive.py as the substrate gate's positive
    # control, so shimming these loses nothing.
    'zone_facts.py': (
        os.path.join(ROOT, 'native_linker', 'zone_facts.py'),
        [os.path.join(ROOT, 'WiiU_FF_Studio', 'dist', 'native_linker', 'zone_facts.py'),
         os.path.join(ROOT, 'relink_validation', 'zone_facts.py')],
    ),
    # Added 2026-08-17. Found while correcting the B5_BASE comment in canonical body_relayout: two
    # copies carried a FULL second implementation (28 top-level definitions, byte-identical to each
    # other, neither canonical), so both still stated the refuted `file = b5 + 64` rule as fact
    # after canonical was corrected. Exactly the hazard this gate exists for, previously unguarded.
    'body_relayout.py': (
        os.path.join(ROOT, 'native_linker', 'body_relayout.py'),
        [os.path.join(ROOT, 'WiiU_FF_Studio', 'dist', 'native_linker', 'body_relayout.py'),
         os.path.join(ROOT, 'WiiU_T6_Toolkit', 'native_linker', 'body_relayout.py')],
    ),
}

SHIM_MARKER = 'CANONICAL:'


def definitions(path):
    """Top-level function/class names defined in `path`. A shim has none."""
    try:
        src = io.open(path, encoding='utf-8', errors='replace').read()
        tree = ast.parse(src)
    except Exception as ex:
        return ['<unparseable: %s>' % ex]
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def check():
    rows, failed = [], 0
    for name, (canon, copies) in sorted(GUARDED.items()):
        if not os.path.exists(canon):
            rows.append(('FAIL', name, 'canonical missing: %s' % canon))
            failed += 1
            continue
        cdefs = definitions(canon)
        if not cdefs:
            rows.append(('FAIL', name, 'canonical defines nothing -- is %s itself a shim?' % canon))
            failed += 1
        else:
            rows.append(('ok', name, 'canonical defines %d symbol(s)' % len(cdefs)))
        for p in copies:
            rel = os.path.relpath(p, ROOT)
            if not os.path.exists(p):
                rows.append(('ok', name, '%s absent (fine -- deleted is as good as a shim)' % rel))
                continue
            defs = definitions(p)
            src = io.open(p, encoding='utf-8', errors='replace').read()
            if defs:
                rows.append(('FAIL', name,
                             '%s is a SECOND IMPLEMENTATION -- defines %s. Replace it with a '
                             're-export shim or delete it.' % (rel, ', '.join(defs[:6]))))
                failed += 1
            elif SHIM_MARKER not in src:
                rows.append(('FAIL', name,
                             '%s holds no definitions but does not name its canonical source. '
                             'Add a "%s <path>" line so the next reader knows where to look.'
                             % (rel, SHIM_MARKER)))
                failed += 1
            else:
                rows.append(('ok', name, '%s is a re-export shim' % rel))
    return rows, failed


def main():
    rows, failed = check()
    w = max(len(r[1]) for r in rows) if rows else 4
    for status, name, detail in rows:
        print('  %-4s %-*s %s' % (status.upper(), w, name, detail))
    print()
    print('  %d checked, %d failed' % (len(rows), failed))
    if failed:
        print('  A duplicated module that carries its own code will drift, and nothing else in '
              'this project notices until a lane writes a broken zone.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
