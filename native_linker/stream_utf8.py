#!/usr/bin/env python3
"""stream_utf8.py -- force stdout/stderr to utf-8 at a program's ENTRY POINT.

Rule (AE) closure mechanism (2026-08-13, PM ruling).

WHY THIS EXISTS
Python takes stdout's encoding from the machine's ANSI codepage -- cp1252 on
the box this pipeline was written on. A print() carrying a character that
codepage cannot encode raises UnicodeEncodeError, and when that print sits
inside a try: above real work, the blanket except eats it and the work is
SKIPPED. That is how the vshaderTail rebase was lost: a logged build and an
interactive build baked DIFFERENT ZONES.

THE THREE FIXES, AND WHY THIS IS THE RIGHT ONE
  1. sanitise every string   -- 88 edits on this build path alone, and it has
                                to be redone for every new string forever
  2. demand PYTHONIOENCODING -- an unwritten precondition; forgetting it is
                                silent, and it cannot be shipped to a user
  3. force the stream HERE   -- ~4 lines, covers every string that will ever
                                be printed, on any codepage, on anyone's box
The north star is that any person can pick up this tooling and run it. On
their machine the ANSI codepage is unknowable, so the only closure that
survives shipping is (3).

WHERE TO CALL IT
ENTRY POINTS ONLY -- inside `if __name__ == '__main__':`, before any work.
Never at import time from a library module: reconfiguring streams is a
process-global side effect, and a library has no business imposing it on
whatever imported it.

    if __name__ == '__main__':
        import stream_utf8; stream_utf8.force()
        main()

`errors='backslashreplace'` rather than 'strict': a character that cannot be
represented degrades to \\uXXXX in the log instead of killing the run. That is
already what CPython does for sys.stderr tracebacks, which is exactly why an
uncaught raise carrying non-ASCII survives while a handler's print() does not.

This module is deliberately pure ASCII, and its failure warning is ASCII by
construction -- a warning about encoding must not die of encoding.
"""
import sys

FAILED = []          # populated by force(); inspectable by callers/gates


def force(streams=('stdout', 'stderr')):
    """Reconfigure the named std streams to utf-8/backslashreplace.

    Returns True if every requested stream is now utf-8. On failure it does
    NOT raise -- a build must not die because its logging could not be
    upgraded -- but it SHOUTS (rule EC: a gate that skips must shout), because
    silently continuing on a cp1252 stream is the exact condition that cost us
    the vshaderTail rebase.
    """
    del FAILED[:]
    for name in streams:
        s = getattr(sys, name, None)
        if s is None:                      # pythonw / embedded: no stream
            FAILED.append('%s: absent' % name)
            continue
        enc = (getattr(s, 'encoding', '') or '').lower().replace('-', '')
        if enc in ('utf8', 'utf8sig'):
            continue                       # already safe (PYTHONIOENCODING=utf-8)
        rec = getattr(s, 'reconfigure', None)
        if rec is None:
            # not a TextIOWrapper: a capture harness, a pipe wrapper, or a
            # frozen-exe stub. Nothing to reconfigure -- report, do not guess.
            FAILED.append('%s: no reconfigure (%s, encoding=%s)'
                          % (name, type(s).__name__, enc or '?'))
            continue
        try:
            rec(encoding='utf-8', errors='backslashreplace')
        except Exception as ex:
            FAILED.append('%s: %s' % (name, type(ex).__name__))
    if FAILED:
        try:
            sys.stderr.write(
                '!! stream_utf8: COULD NOT FORCE utf-8 (%s). Non-ASCII output '
                'may kill this run; a print inside a try may silently skip '
                'work. Re-run with PYTHONIOENCODING=utf-8.\n'
                % '; '.join(FAILED))
        except Exception:
            pass                           # nothing left to report through
        return False
    return True


def state():
    """(stdout_encoding, stderr_encoding) -- for gates and diagnostics."""
    return tuple((getattr(getattr(sys, n, None), 'encoding', None))
                 for n in ('stdout', 'stderr'))


def selftest():
    """Prove the forcer works AND that its failure path reports rather than
    lies. Both verdicts observed, per the instrument laws."""
    ok = True
    before = state()
    got = force()
    after = state()
    good = got and all((e or '').lower().replace('-', '') == 'utf8'
                       for e in after)
    ok = ok and good
    print('  before=%s after=%s forced=%s  %s'
          % (before, after, got, 'ok' if good else 'MISMATCH'))

    class Dummy(object):                   # no reconfigure attribute
        encoding = 'cp1252'

    real_out = sys.stdout
    sys.stdout = Dummy()
    try:
        got2 = force(('stdout',))
    finally:
        sys.stdout = real_out
    good2 = (got2 is False) and any('no reconfigure' in f for f in FAILED)
    ok = ok and good2
    print('  unreconfigurable stream -> returned %s, reported %s  %s'
          % (got2, FAILED, 'ok' if good2 else 'MISMATCH'))
    force()                                # restore for the rest of the run
    print('  SELFTEST: %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    print('stream_utf8: encodings before force = %s' % (state(),))
    print('stream_utf8: force() -> %s, after = %s' % (force(), state()))
