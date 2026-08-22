"""core.uiutil -- small shared Tk behaviours, so each window does not reinvent them.

`attach_sorting` gives a Treeview click-to-sort headings with an ascending/descending toggle and
an arrow marker. It sorts the MODEL, not the display strings: a column of byte counts formatted
with thousands separators must order 9,999 before 10,000, and "9,999" > "10,000" as text. The
caller supplies a key function per column for exactly that reason.
"""
import os

UP, DOWN = ' ▲', ' ▼'


def attach_sorting(tree, headings, refill, state, default=None):
    """Make `headings` clickable sort controls for a flat Treeview.

    headings  {column_id: (label, keyfunc)} -- column_id '#0' is the tree column.
              keyfunc takes the caller's row object and returns a sort key.
    refill    callable() that re-populates the tree; it must read `state` for order.
    state     dict the caller also reads: {'sort': column_id or None, 'desc': bool}
    default   column to sort by initially, or None to leave insertion order.

    The caller keeps ownership of the data and of drawing rows -- this only manages which
    column and direction are active, and repaints the heading labels.
    """
    state.setdefault('sort', default)
    state.setdefault('desc', False)
    state['_headings'] = headings

    def paint():
        for col, (label, _k) in headings.items():
            mark = ''
            if state.get('sort') == col:
                mark = DOWN if state.get('desc') else UP
            tree.heading(col, text=label + mark)

    def clicked(col):
        if state.get('sort') == col:
            state['desc'] = not state.get('desc')     # same column -> flip direction
        else:
            state['sort'] = col
            state['desc'] = False                      # new column -> ascending first
        paint()
        refill()

    for col, (label, _k) in headings.items():
        tree.heading(col, text=label, command=lambda c=col: clicked(c))
    paint()
    return paint


def sort_rows(rows, state):
    """Apply the active sort in `state` to a list of row objects. Returns a new list."""
    col = state.get('sort')
    headings = state.get('_headings') or {}
    if not col or col not in headings:
        return list(rows)
    keyf = headings[col][1]

    def safe(r):
        try:
            v = keyf(r)
        except Exception:
            v = None
        if v is None:
            # Keep unknowns together at the end of an ascending sort rather than crashing on a
            # str/int comparison, which is what a mixed column would otherwise do.
            return (1, '')
        return (0, v.lower() if isinstance(v, str) else v)

    return sorted(rows, key=safe, reverse=bool(state.get('desc')))


# --- drag and drop of files onto a window ------------------------------------------------------
# Tk has NO native file drop. The usual answer is tkinterdnd2, which means shipping a native tkdnd
# binary inside the exe -- a real failure surface for one affordance. Windows can do it with
# nothing but ctypes: register the window with DragAcceptFiles and subclass its window procedure to
# catch WM_DROPFILES.
#
# ⚠ EVERY FAILURE PATH HERE IS SILENT AND NON-FATAL. This subclasses a live Tk window procedure; if
# anything about that goes wrong the correct outcome is "no drag and drop", never a crashed editor.
# The caller keeps its Add files.../Add folder... buttons, so losing this loses nothing but
# convenience -- and on macOS and Linux it is simply never installed.
_DROP_KEEPALIVE = []

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4


def enable_file_drop(widget, on_files):
    """Call on_files([paths]) when files are dropped on `widget`. -> True if installed.

    Returns False (harmlessly) on any non-Windows platform or if the hook cannot be installed.
    """
    import sys
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        widget.update_idletasks()                 # the HWND does not exist until it is realised
        hwnd = widget.winfo_id()
        if not hwnd:
            return False

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        prototype = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                                       ctypes.c_ulonglong, ctypes.c_longlong)
        setter = getattr(user32, 'SetWindowLongPtrW', None) or user32.SetWindowLongW
        getter = getattr(user32, 'GetWindowLongPtrW', None) or user32.GetWindowLongW
        # ⚠ argtypes ARE REQUIRED, not tidiness. Left unset, ctypes marshals a Python int as a
        # 32-bit C int, so passing a 64-bit window-procedure pointer raises -- and with the
        # blanket except below that surfaces only as "drag and drop silently unavailable".
        getter.restype = ctypes.c_void_p
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        setter.restype = ctypes.c_void_p
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        caller = user32.CallWindowProcW
        caller.restype = ctypes.c_longlong
        caller.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                           ctypes.c_ulonglong, ctypes.c_longlong]

        # ⚠ GET, never Set-with-0. SetWindowLongPtrW(hwnd, GWLP_WNDPROC, 0) does not READ the
        # current procedure -- it INSTALLS a null one, and the window stops handling its own
        # messages from that instant.
        old = ctypes.c_void_p(getter(hwnd, GWLP_WNDPROC))
        if not old:
            return False

        def proc(h, msg, wp, lp):
            if msg == WM_DROPFILES:
                try:
                    n = shell32.DragQueryFileW(wintypes.HWND(wp), 0xFFFFFFFF, None, 0)
                    paths = []
                    for i in range(n):
                        need = shell32.DragQueryFileW(wintypes.HWND(wp), i, None, 0) + 1
                        buf = ctypes.create_unicode_buffer(need)
                        shell32.DragQueryFileW(wintypes.HWND(wp), i, buf, need)
                        paths.append(buf.value)
                    shell32.DragFinish(wintypes.HWND(wp))
                    if paths:
                        widget.after(0, lambda p=paths: on_files(p))
                except Exception:
                    pass
                return 0
            return caller(old, h, msg, wp, lp)

        new = prototype(proc)
        setter(hwnd, GWLP_WNDPROC, ctypes.cast(new, ctypes.c_void_p).value)
        shell32.DragAcceptFiles(hwnd, True)
        # Both the callback and the original proc must outlive this call: if Python collects the
        # WNDPROC, Windows calls into freed memory the next time the window gets a message.
        _DROP_KEEPALIVE.append((new, old, hwnd))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- background work

class Cancelled(Exception):
    """Raised inside a worker to unwind it when the user presses Cancel."""


def run_with_progress(parent, title, job, app_title='WiiU T6 Studio', can_cancel=True):
    """Run `job` on a worker thread behind a modal, cancellable progress dialog.

    `job(report)` is called ON THE WORKER. It should call `report(done, total, label)` from time
    to time; that call RAISES `Cancelled` once the user presses Cancel, which unwinds the job.
    Returns the job's result, or None if it was cancelled.

    ⛔ WHY THIS EXISTS. Long jobs were being run STRAIGHT ON THE UI THREAD -- the name-dictionary
    rebuild walked every pak and zone under the content folders inside the button handler. Tk
    cannot repaint while that runs, so Windows greys the window and titles it "Not Responding",
    and the user reasonably reports a crash. Nothing was wrong except that the work was on the
    wrong thread and had no way to report or stop.

    ⚠ The worker must not touch Tk. `report` only puts a tuple on a queue; the dialog polls it.
    """
    import queue as _queue
    import threading as _threading
    import tkinter as _tk
    from tkinter import ttk as _ttk

    q = _queue.Queue()
    stop = _threading.Event()
    out = {}

    def report(done, total, label=''):
        if stop.is_set():
            raise Cancelled()
        q.put(('p', done, total, str(label)))

    def work():
        try:
            out['result'] = job(report)
        except Cancelled:
            out['cancelled'] = True
        except BaseException as ex:                  # noqa: BLE001 -- surfaced below, not eaten
            out['error'] = ex
        q.put(('done', 0, 0, ''))

    win = _tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    win.protocol('WM_DELETE_WINDOW', lambda: stop.set())

    body = _tk.Frame(win)
    body.pack(fill='both', expand=True, padx=16, pady=14)
    head = _tk.Label(body, text=title, anchor='w')
    head.pack(fill='x')
    detail = _tk.Label(body, text='starting...', anchor='w', width=52)
    detail.pack(fill='x', pady=(4, 8))
    bar = _ttk.Progressbar(body, mode='indeterminate', length=380)
    bar.pack(fill='x')
    bar.start(12)
    if can_cancel:
        _ttk.Button(body, text='Cancel', command=lambda: (stop.set(),
                                                          detail.configure(text='cancelling...'))
                    ).pack(anchor='e', pady=(10, 0))

    _threading.Thread(target=work, daemon=True).start()

    determinate = [False]

    def poll():
        try:
            while True:
                kind, done, total, label = q.get_nowait()
                if kind == 'done':
                    win.destroy()
                    return
                if total and not determinate[0]:
                    bar.stop()
                    bar.configure(mode='determinate', maximum=1000)
                    determinate[0] = True
                if total:
                    bar['value'] = int(1000 * done / total)
                    detail.configure(text='%d / %d   %s' % (done, total, label[-46:]))
                else:
                    detail.configure(text=str(label)[-52:])
        except _queue.Empty:
            pass
        except _tk.TclError:
            return                        # the dialog went away underneath us
        win.after(80, poll)

    win.after(60, poll)
    try:
        win.grab_set()
    except _tk.TclError:
        pass
    parent.wait_window(win)

    if 'error' in out:
        raise out['error']
    return None if out.get('cancelled') else out.get('result')


# --------------------------------------------------------------------------- crash reporting

_CRASH_SEEN = set()
_CRASH_SHOWN = [0]
_CRASH_LOG = [None]


def crash_log_path():
    """Where crash reports go. Beside the executable, else %LOCALAPPDATA%/the home dir."""
    if _CRASH_LOG[0]:
        return _CRASH_LOG[0]
    import sys as _sys
    from . import paths as _paths
    base = os.path.dirname(os.path.abspath(
        _sys.executable if getattr(_sys, 'frozen', False) else _paths.STUDIO_DIR))
    cand = os.path.join(base, 'studio_errors.log')
    try:
        with open(cand, 'a'):
            pass
    except OSError:
        home = os.path.expanduser('~')
        alt = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        d = os.path.join(alt, 'WiiU_T6_Studio')
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = home
        cand = os.path.join(d, 'studio_errors.log')
    _CRASH_LOG[0] = cand
    return cand


def install_crash_handler(root, app_title='WiiU T6 Studio', max_dialogs=3):
    """Make an exception in a Tk callback VISIBLE instead of vanishing.

    ⛔ WHY THIS IS LOAD-BEARING. Tk catches whatever a callback raises and hands it to
    `Tk.report_callback_exception`, whose default prints to stderr -- and a PyInstaller
    `--windowed` build HAS NO STDERR. So every exception raised while opening a pak, scrolling a
    list or repainting a preview was discarded in silence. The window stayed up with a dead
    widget or a half-finished operation, which is indistinguishable from a hang or a crash and
    leaves nothing to diagnose from. That is the single biggest reason "it crashed" has been
    unactionable.

    Every fault is now appended to `studio_errors.log` with a timestamp, and the FIRST few
    distinct ones raise a dialog. Repeats of a traceback already seen are logged but not shown:
    an exception inside a repeating `after()` callback fires many times a second, and a dialog
    storm is worse than the original bug.
    """
    import datetime
    import sys as _sys
    import tkinter as _tk
    import traceback as _tb
    from tkinter import messagebox as _mb

    path = crash_log_path()

    def _record(exc, val, tb, where):
        text = ''.join(_tb.format_exception(exc, val, tb))
        sig = ''.join(_tb.format_exception_only(exc, val)) + (
            ''.join(l for l in text.splitlines(True) if l.startswith('  File'))[-400:])
        first = sig not in _CRASH_SEEN
        _CRASH_SEEN.add(sig)
        try:
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write('\n%s  [%s]%s\n%s'
                         % (datetime.datetime.now().isoformat(timespec='seconds'), where,
                            '' if first else '  (repeat)', text))
        except OSError:
            pass
        if first and _CRASH_SHOWN[0] < max_dialogs:
            _CRASH_SHOWN[0] += 1
            try:
                _mb.showerror(
                    app_title,
                    "Something went wrong, but the application is still running.\n\n%s\n"
                    "This has been written to:\n%s\n\nIf it keeps happening, send that file."
                    % (''.join(_tb.format_exception_only(exc, val)).strip()[:300], path))
            except Exception:
                pass                    # never let the reporter raise
        return text

    def _tk_handler(exc, val, tb):
        _record(exc, val, tb, 'tk-callback')

    # bind on the CLASS, not the instance: every Toplevel and every widget routes its callback
    # exceptions through Tk.report_callback_exception, and a per-instance assignment misses
    # dialogs created later.
    _tk.Tk.report_callback_exception = staticmethod(_tk_handler)
    try:
        root.report_callback_exception = _tk_handler
    except Exception:
        pass

    prev = _sys.excepthook

    def _hook(exc, val, tb):
        _record(exc, val, tb, 'main-thread')
        try:
            prev(exc, val, tb)
        except Exception:
            pass

    _sys.excepthook = _hook

    try:                                # worker threads, Python 3.8+
        import threading as _th
        _th.excepthook = lambda a: _record(a.exc_type, a.exc_value, a.exc_traceback, 'thread')
    except Exception:
        pass
    return path
