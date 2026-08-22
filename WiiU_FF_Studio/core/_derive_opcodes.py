"""Derive the complete HavokScript opcode table from hksc's own `lopcodes.def`.

WHY THIS REPLACES `core/_mine_hks.py`
-------------------------------------
The mined table was built by aligning hksc `-l -l` listings against raw instruction words. That
method is sound -- it produced 55 entries, conflict-free over 781 instructions, and every one of
them is confirmed correct here -- but it can only ever name an opcode that the SPEC CORPUS
happened to emit. It left 22 holes in 0..76, and a hole is not inert: a disassembly or a codegen
that touches one is silently wrong, because `MNEMONIC.get(op, 'OP_%d')` degrades to a label
instead of raising.

`lopcodes.def` is not a listing to be aligned against -- it IS the enum. Opcode numbers are the
positions of the `DEFCODE*` lines in that file, so parsing it yields all 77 entries with no
corpus dependence and no holes. It also carries per-operand modes (`bmode`/`cmode`) that the
listing never exposed, which is what lets `hks_dis` tell a constant-index Bx (LOADK) from a
proto-index Bx (CLOSURE) from opaque VM data (DATA) instead of guessing from the mode alone.

  ⛔ THE CONFIGURATION MATTERS. `lopcodes.def` has two `#ifdef` blocks, and both SHIFT every
  opcode after them:
      LUA_CODT7   inserts 8 shift/bitwise opcodes between LE_BK(59) and CONCAT
      LUA_CODIW6  appends DELETE/DELETE_BK at the end
  With T7 on, CONCAT becomes 68 and DATA becomes 84. So the config is read from `hkscconf.h`
  rather than assumed, and the emitted table records which config produced it. T6 (our target)
  is LUA_CODT6 with both of the above OFF.

USAGE
-----
    python -m core._derive_opcodes --src <path-to-hksc/src>      # print the table
    python -m core._derive_opcodes --src <path> --write          # regenerate _opcodes_hks.py

`--src` may also come from the `HKSC_SRC` environment variable. There is deliberately no default
path: hksc is not shipped with the studio, the derived table is (see `_opcodes_hks.py`), and a
machine-specific path baked into a tool is a defect in its own right.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# "DEFCODE1(NAME, MODE, test, useRA, bmode, cmode, makeR1)" and friends. The macro variant
# decides how many trailing arguments there are, but every variant starts (name, mode, test,
# useRA, bmode, cmode, ...) -- which is all we need.
DEF_RE = re.compile(r'^\s*(DEFCODE(?:_R1V|_R1|1)?)\s*\(\s*([A-Za-z0-9_]+)\s*,'
                    r'\s*(ABC|ABx|AsBx)\s*,\s*(\d)\s*,\s*(\d)\s*,'
                    r'\s*([A-Za-z]+)\s*,\s*([A-Za-z]+)\s*,')

CONFIG_RE = re.compile(r'^\s*#\s*(ifdef|ifndef|if|elif|else|endif|define|undef)\b\s*(.*)$')


class DeriveError(Exception):
    pass


def _strip_block_comments(text):
    return re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)


def read_config(src_dir):
    """Evaluate `hkscconf.h`'s `#if 0` / `#if 1` blocks into {macro: bool}.

    The file is machine-generated from configure and uses only literal `#if 0` / `#if 1`, so a
    tiny evaluator is exact here. Anything else raises rather than guessing -- a mis-read config
    silently renumbers every opcode after LE_BK, which is the worst possible failure mode.
    """
    path = os.path.join(src_dir, 'hkscconf.h')
    if not os.path.exists(path):
        raise DeriveError('no hkscconf.h in %s' % src_dir)
    text = _strip_block_comments(open(path, encoding='utf-8', errors='replace').read())
    cfg = {}
    live = [True]
    for line in text.splitlines():
        m = CONFIG_RE.match(line)
        if not m:
            continue
        kind, rest = m.group(1), m.group(2).strip()
        if kind in ('ifdef', 'ifndef'):
            # Only the `#ifndef hconfig_h` include guard uses these here, and it is live on a
            # first include. Push so the matching #endif balances.
            live.append(live[-1])
        elif kind in ('if', 'elif'):
            # Literal blocks are evaluated exactly. Anything else becomes an UNKNOWN frame: we
            # do not guess it, but we also do not choke on it, because every macro we actually
            # read is inside a literal block. Reading one from an unknown frame raises below.
            if kind == 'elif':
                if len(live) < 2:
                    raise DeriveError('hkscconf.h: #elif without #if')
                live.pop()
            live.append((live[-1] and rest == '1') if rest in ('0', '1') else None)
        elif kind == 'else':
            if len(live) < 2:
                raise DeriveError('hkscconf.h: #else without #if')
            live[-1] = None if live[-1] is None else (live[-2] and not live[-1])
        elif kind == 'endif':
            if len(live) < 2:
                raise DeriveError('hkscconf.h: #endif without #if')
            live.pop()
        else:
            name = rest.split()[0] if rest else ''
            if not name.startswith('LUA_COD'):
                continue
            if live[-1] is None:
                raise DeriveError('hkscconf.h defines %s inside a conditional this evaluator '
                                  'cannot decide; refusing to guess the opcode numbering' % name)
            if live[-1]:
                cfg[name] = (kind == 'define')
    if 'LUA_CODT7' not in cfg or 'LUA_CODIW6' not in cfg:
        raise DeriveError('hkscconf.h never settles LUA_CODT7/LUA_CODIW6; both renumber opcodes')
    return cfg


def derive(src_dir):
    """Parse lopcodes.def under the config in hkscconf.h.

    Returns (entries, config) where entries is [(opcode, name, mode, test, useRA, bmode, cmode)]
    in enum order, indices dense from 0.
    """
    cfg = read_config(src_dir)
    path = os.path.join(src_dir, 'lopcodes.def')
    if not os.path.exists(path):
        raise DeriveError('no lopcodes.def in %s' % src_dir)
    text = _strip_block_comments(open(path, encoding='utf-8', errors='replace').read())

    entries = []
    live = [True]
    for line in text.splitlines():
        m = CONFIG_RE.match(line)
        if m:
            kind, rest = m.group(1), m.group(2).strip()
            if kind in ('ifdef', 'ifndef'):
                # This is the whole point of reading hkscconf.h: `#ifdef LUA_CODT7` inserts 8
                # opcodes in the MIDDLE of the enum, renumbering everything after it.
                name = rest.split()[0] if rest else ''
                on = bool(cfg.get(name, False))
                if kind == 'ifndef':
                    on = not on
                live.append(live[-1] and on)
            elif kind == 'if':
                live.append(live[-1] and rest == '1')
            elif kind == 'else':
                live[-1] = live[-2] and not live[-1]
            elif kind == 'endif':
                if len(live) < 2:
                    raise DeriveError('lopcodes.def: #endif without #if')
                live.pop()
            continue
        if not live[-1]:
            continue
        d = DEF_RE.match(line)
        if d:
            entries.append((len(entries), d.group(2), d.group(3),
                            int(d.group(4)), int(d.group(5)), d.group(6), d.group(7)))
    if not entries:
        raise DeriveError('lopcodes.def yielded no opcodes -- the macro shape must have changed')
    return entries, cfg


def format_of(mode, use_ra):
    """Presentation format, matching how hksc's own listing prints each form.

    JMP is `AsBx` with useRA=0: hksc prints only the offset, so it renders as 'sBx'. FORPREP and
    FORLOOP are `AsBx` with useRA=1 and print both, so they render as 'iAsBx'.
    """
    if mode == 'ABx':
        return 'iABx'
    if mode == 'AsBx':
        return 'iAsBx' if use_ra else 'sBx'
    return 'iABC'


def render(entries, cfg, src_dir):
    on = sorted(k for k, v in cfg.items() if v)
    off = sorted(k for k, v in cfg.items() if not v)
    L = ['"""HavokScript opcode table -- GENERATED by core/_derive_opcodes.py. Do not hand-edit.',
         '',
         'The whole enum, densely, %d opcodes 0..%d with no holes. It supersedes a table that was'
         % (len(entries), entries[-1][0]),
         'assembled from observed instructions only, and so could name nothing the sample corpus',
         'never emitted -- that one had 22 gaps below 76 and stopped there.',
         '',
         'Variant blocks in effect (each one renumbers every opcode after it):',
         '    ON : %s' % (', '.join(on) or '(none)'),
         '    OFF: %s' % (', '.join(off) or '(none)'),
         '',
         'FORMAT is the presentation form. BMODE/CMODE are the operand modes straight from the',
         'definition file and are what callers should test -- `iABx` alone does not tell you',
         'whether Bx is a constant index (LOADK, bmode=K), a proto index (CLOSURE, bmode=U) or',
         'opaque VM data (DATA, bmode=UK), and treating all three alike produces false failures.',
         '',
         'ISBK: HKS gives B only 8 bits, so opcodes that need an RK-mode B come in even/odd pairs',
         'and the low opcode bit carries B\'s 9th bit. For those, the odd `_BK` member always has',
         'a constant B and the even member always has a register B.',
         '"""',
         'MNEMONIC = {']
    for op, name, _m, _t, _u, _b, _c in entries:
        L.append('    %3d: %r,' % (op, name))
    L.append('}')
    L.append('')
    L.append('FORMAT = {')
    for op, _n, mode, _t, use_ra, _b, _c in entries:
        L.append('    %3d: %r,' % (op, format_of(mode, use_ra)))
    L.append('}')
    L.append('')
    L.append('#: operand mode of B / C: R register, K constant, RK either, U unsigned, N unused,')
    L.append('#: UK unsigned-or-opaque. Straight from lopcodes.def.')
    L.append('BMODE = {')
    for op, _n, _m, _t, _u, bmode, _c in entries:
        L.append('    %3d: %r,' % (op, bmode))
    L.append('}')
    L.append('')
    L.append('CMODE = {')
    for op, _n, _m, _t, _u, _b, cmode in entries:
        L.append('    %3d: %r,' % (op, cmode))
    L.append('}')
    L.append('')
    L.append('#: 1 if the opcode is a conditional test (the NEXT instruction is always a jump).')
    L.append('TEST = {')
    for op, _n, _m, test, _u, _b, _c in entries:
        L.append('    %3d: %d,' % (op, test))
    L.append('}')
    L.append('')
    L.append('#: 1 if argument A is used as a register.')
    L.append('USE_RA = {')
    for op, _n, _m, _t, use_ra, _b, _c in entries:
        L.append('    %3d: %d,' % (op, use_ra))
    L.append('}')
    L.append('')
    names = {op: n for op, n, _m, _t, _u, _b, _c in entries}
    isbk = sorted(op for op, n in names.items()
                  if n.endswith('_BK') or names.get(op + 1, '') == n + '_BK')
    L.append('#: opcodes whose low bit is B\'s 9th (RK) bit -- see the module docstring.')
    L.append('ISBK = frozenset(%r)' % (tuple(isbk),))
    L.append('')
    L.append('CONFIG = %r' % ({k: bool(v) for k, v in sorted(cfg.items())},))
    L.append('COUNT = %d' % len(entries))
    L.append('')
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--src', default=os.environ.get('HKSC_SRC'),
                    help='path to the hksc `src` directory (or set HKSC_SRC)')
    ap.add_argument('--write', action='store_true', help='regenerate core/_opcodes_hks.py')
    ap.add_argument('--compare', action='store_true',
                    help='diff the derived table against the checked-in one')
    a = ap.parse_args(argv)
    if not a.src:
        ap.error('no --src and no HKSC_SRC: point this at an hksc source tree')

    entries, cfg = derive(a.src)
    print('%d opcodes derived from %s' % (len(entries), os.path.join(a.src, 'lopcodes.def')))
    print('config: %s' % ', '.join('%s=%d' % (k, v) for k, v in sorted(cfg.items())))

    if a.compare:
        from core import _opcodes_hks as cur
        derived = {op: n for op, n, _m, _t, _u, _b, _c in entries}
        same = diff = added = 0
        for op in sorted(set(derived) | set(cur.MNEMONIC)):
            d, c = derived.get(op), cur.MNEMONIC.get(op)
            if d == c:
                same += 1
            elif c is None:
                added += 1
            else:
                diff += 1
                print('  DIFFERS %3d  checked-in %-22s derived %s' % (op, c, d))
        print('compare: %d identical, %d differ, %d newly named' % (same, diff, added))
        return 1 if diff else 0

    text = render(entries, cfg, a.src)
    if a.write:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_opcodes_hks.py')
        with open(out, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        print('wrote %s' % out)
    else:
        print()
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
