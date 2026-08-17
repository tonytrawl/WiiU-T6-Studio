"""core.uiutil -- small shared Tk behaviours, so each window does not reinvent them.

`attach_sorting` gives a Treeview click-to-sort headings with an ascending/descending toggle and
an arrow marker. It sorts the MODEL, not the display strings: a column of byte counts formatted
with thousands separators must order 9,999 before 10,000, and "9,999" > "10,000" as text. The
caller supplies a key function per column for exactly that reason.
"""
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
