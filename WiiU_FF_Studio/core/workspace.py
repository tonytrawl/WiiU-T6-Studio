"""core.workspace -- let a tool UI live EITHER in the unified shell OR in its own window.

WHY THIS EXISTS
---------------
`ff_ide.IDE`, `ipak_ide.IpakIDE` and `sab_ide.SabIDE` were each written as `tk.Tk` subclasses --
i.e. each one IS a top-level window. That is exactly what stops three tools becoming one app: a
Tk instance cannot be packed inside another window, and there can only be one per process.

The tempting fix is to rewrite the three UIs against a new shell. That would be ~2,600 lines of
re-implementation of code whose correctness was paid for in real bugs (the Tcl tilde crash in the
save dialog, SND_FILENAME|SND_ASYNC playback, the count-not-length read-only asset types...).
Rewriting is how you lose those quietly.

So instead the base class changes from `tk.Tk` to a FRAME, and this module supplies the handful
of window-only methods the tool code calls on `self` (`title`, `geometry`, `protocol`, ...). Each
tool needed a four-line change; every widget, handler and workflow inside it is untouched.

Standalone entry points keep working via `run_standalone()`, so `python sab_ide.py file.sabs`
behaves exactly as before and the existing per-tool EXEs stay buildable.
"""
import tkinter as tk

from . import theme


class Workspace(tk.Frame):
    """A tool UI that is a Frame, but answers the window API its code already uses.

    Hosted:      the shell packs it and reads `wants_title` / calls `close()`.
    Standalone:  `run_standalone()` gives it a real Toplevel and forwards the window calls on.
    """

    #: Short label the shell shows on the document tab. Subclasses may override.
    kind = 'file'

    def __init__(self, master=None, **kw):
        kw.setdefault('bg', theme.BG)
        super().__init__(master, **kw)
        self._ws_title = None
        self._ws_close = None
        self._ws_title_cb = None       # shell hook: called when the tool sets a title
        self._ws_doc_cb = None         # shell hook: called when the tool opens a DIFFERENT file
        self._ws_menu = None           # the tool's own menubar, if it built one
        self._ws_menu_cb = None        # shell hook: called when the tool sets a menubar
        self._ws_dirty = False

    # ---- window API the hosted tool code still calls on `self` ------------------
    def configure(self, cnf=None, **kw):
        """Intercept `menu=`, which only a toplevel accepts.

        ⚠ THIS IS NOT COSMETIC. `ff_ide._menu()` and `ipak_ide._menu()` both end in
        `self.config(menu=m)`; against a Frame that raises `TclError: unknown option "-menu"`
        DURING __init__, so the tool never finishes constructing and the document silently fails
        to open. Measured: both the zone and ipak gates failed exactly here while sab (which has
        no menubar) passed.

        Dropping the menubar would have been the easy fix and the wrong one -- it is real
        functionality. So the menu is kept and handed to whoever owns the window: the real
        toplevel when standalone, or the shell, which swaps it in as the document is activated.
        That makes the menu bar itself part of the per-file-type layout.
        """
        menu = None
        if isinstance(cnf, dict) and 'menu' in cnf:
            cnf = dict(cnf)
            menu = cnf.pop('menu')
        if 'menu' in kw:
            menu = kw.pop('menu')
        if menu is not None:
            self._ws_menu = menu
            if getattr(self, '_ws_owns_window', False):
                try:
                    self.winfo_toplevel().configure(menu=menu)
                except tk.TclError:
                    pass
            elif self._ws_menu_cb:
                try:
                    self._ws_menu_cb(self, menu)
                except Exception:
                    pass
        if cnf is None and not kw:
            return None
        return super().configure(cnf, **kw)

    config = configure

    #: Shortcuts the SHELL owns. A hosted tool must not also bind these: the keystroke would
    #: fire twice (the tool's own Open dialog and the shell's), and the tool's meaning is wrong
    #: in a tabbed shell anyway -- Ctrl+O should open a NEW TAB, not replace this tool's file.
    SHELL_RESERVED = ("<Control-o>", "<Control-O>", "<Control-w>", "<Control-W>")

    def bind_all(self, sequence=None, func=None, add=None):
        """Scope an application-wide binding to THIS workspace.

        ⚠ REQUIRED ONCE MORE THAN ONE DOCUMENT CAN BE OPEN. `bind_all` binds on the "all"
        bindtag -- the whole application -- so with three zone tabs open, one Ctrl+S ran all
        three hosted instances' save handlers. Three files written from one keystroke.

        Binding on the frame instead does NOT fix it: a key event is delivered to the focused
        widget and travels ITS bindtags, which do not include an ancestor Frame, so the
        shortcut would simply stop working. So the binding stays application-wide and is GATED
        on this workspace being the mapped (visible) one -- and since the shell packs only the
        active document, exactly one tab reacts.
        """
        if sequence is None or func is None:
            return super().bind_all(sequence, func, add)
        if self._hosted_in_shell() and sequence in self.SHELL_RESERVED:
            return None                     # the shell provides this one

        def _scoped(event, _f=func):
            try:
                if not self.winfo_ismapped():
                    return None
            except tk.TclError:
                return None
            return _f(event)
        return super().bind_all(sequence, _scoped, add)

    def menubar(self):
        """The menu this tool built, if any -- the shell attaches it to the window."""
        return getattr(self, '_ws_menu', None)

    def on_menu(self, callback):
        self._ws_menu_cb = callback

    def title(self, text=None):
        """Tool code calls self.title("App -- file.ff"). Route it, don't crash."""
        if text is None:
            return self._ws_title
        self._ws_title = text
        top = self.winfo_toplevel()
        if isinstance(top, (tk.Tk, tk.Toplevel)) and getattr(self, '_ws_owns_window', False):
            top.title(text)
        if self._ws_title_cb:
            try:
                self._ws_title_cb(self, text)
            except Exception:
                pass                   # a shell display hiccup must not break the tool
        return None

    def geometry(self, spec=None):
        if spec and getattr(self, '_ws_owns_window', False):
            self.winfo_toplevel().geometry(spec)

    def minsize(self, *a):
        if getattr(self, '_ws_owns_window', False):
            self.winfo_toplevel().minsize(*a)

    def maxsize(self, *a):
        if getattr(self, '_ws_owns_window', False):
            self.winfo_toplevel().maxsize(*a)

    def resizable(self, *a):
        if getattr(self, '_ws_owns_window', False):
            self.winfo_toplevel().resizable(*a)

    def iconbitmap(self, *a, **kw):
        if getattr(self, '_ws_owns_window', False):
            try:
                self.winfo_toplevel().iconbitmap(*a, **kw)
            except tk.TclError:
                pass                   # a missing .ico is never worth failing a launch over

    def _hosted_in_shell(self):
        """True when a studio shell owns the window, so window commands must not reach it.

        Duck-typed rather than imported, to keep core.workspace free of a dependency on the
        shell (which imports it).
        """
        return bool(getattr(self.winfo_toplevel(), '_is_studio_shell', False))

    def owns_global_styles(self):
        """True when this tool may configure the SHARED ttk style database.

        ⚠ ttk's style database is per-INTERPRETER, not per-widget. A tool that reconfigures a
        style NAME the shell also uses -- Tree.Treeview, Accent.TButton, Ghost.TButton -- is not
        making a local override: it silently restyles every other tab already on screen, and the
        winner is simply whichever tab was constructed last. Measured before this existed: an
        asset tree's font flipped between Consolas 9 and Segoe UI 9, and its row height between
        20 and 22, purely on the order tabs were opened.

        Standalone the tool owns its window and must style itself. Hosted, core.theme owns the
        look and the tool must keep its hands off.
        """
        return not self._hosted_in_shell()

    def _window_cmd(self, name, *a, **kw):
        """Run a window-manager command on the toplevel, unless a shell owns it.

        ⚠ REQUIRED, not defensive. Gate B2 of the GSC assembler battery does
        `ff_ide.IDE(); app.withdraw()` to build the UI headlessly — with a Frame base that is
        an AttributeError, and it FAILED the battery (11 passed, 1 failed) even though the tool
        itself was fine. Test and script code legitimately drives these; only a shell-hosted
        workspace must be stopped from, say, withdrawing the entire application window.
        """
        if self._hosted_in_shell():
            return None
        try:
            return getattr(self.winfo_toplevel(), name)(*a, **kw)
        except tk.TclError:
            return None

    def withdraw(self, *a, **kw):
        return self._window_cmd('withdraw', *a, **kw)

    def deiconify(self, *a, **kw):
        return self._window_cmd('deiconify', *a, **kw)

    def iconify(self, *a, **kw):
        return self._window_cmd('iconify', *a, **kw)

    def state(self, *a, **kw):
        return self._window_cmd('state', *a, **kw)

    def attributes(self, *a, **kw):
        return self._window_cmd('attributes', *a, **kw)

    def protocol(self, name, func=None):
        """Capture WM_DELETE_WINDOW so the shell can run the tool's own close logic on a tab close."""
        if name == "WM_DELETE_WINDOW":
            self._ws_close = func
            if getattr(self, '_ws_owns_window', False):
                self.winfo_toplevel().protocol(name, func)
        return None

    def mainloop(self, n=0):
        """Standalone tools end with `.mainloop()`; hosted ones must ignore it."""
        if getattr(self, '_ws_owns_window', False):
            self.winfo_toplevel().mainloop(n)

    # ---- shell-facing API -------------------------------------------------------
    def close(self):
        """Ask the tool to shut down cleanly, running whatever it registered for window close.

        Returns False if the tool refused (e.g. unsaved changes and the user said cancel) --
        the shell must then leave the document open.
        """
        cb = self._ws_close
        if cb is None:
            return True
        try:
            # The tools' handlers call self.destroy() themselves on an accepted close, so a
            # still-existing widget afterwards means the tool refused.
            cb()
        except Exception:
            return True
        return not bool(self.winfo_exists())

    def on_title(self, callback):
        self._ws_title_cb = callback

    def on_document(self, callback):
        self._ws_doc_cb = callback

    def set_document(self, path):
        """Tell the shell this workspace now holds a DIFFERENT file.

        ⚠ WITHOUT THIS THE TAB LIES. The shell names a tab from the path it opened, so a tool
        that opens another file through its OWN Open button leaves the tab, the window title,
        the recent list and the status line all describing the previous file -- and the
        already-open check keys off the stale path, so re-opening the file it actually holds
        offers to open a "second view" of something that is not on screen.

        Tools must call this after a successful internal open. It is a no-op when the tool is
        running standalone rather than hosted.
        """
        if not path or not self._ws_doc_cb:
            return
        try:
            self._ws_doc_cb(self, path)
        except Exception:
            pass                       # a shell display hiccup must not break the tool


def run_standalone(cls, initial=None, title=None, icon=None, size="1280x820",
                   minsize=(1000, 640)):
    """Run a Workspace subclass as its own application window.

    Keeps `python <tool>_ide.py <file>` and the per-tool EXEs working exactly as before, so the
    consolidated shell is an ADDITION rather than a migration anyone is forced through.
    """
    root = tk.Tk()
    root.configure(bg=theme.BG)
    theme.install(root)
    try:                            # same reason as in the shell: no stderr in a windowed build
        from . import uiutil as _ui
        _ui.install_crash_handler(root, title or 'WiiU T6 Studio')
    except Exception:
        pass
    if title:
        root.title(title)
    root.geometry(size)
    if minsize:
        root.minsize(*minsize)
    # Application-wide default, so this tool's dialogs get the icon too rather than Tk's
    # feather. An explicit `icon=` argument still wins, but is applied the same way.
    if icon:
        try:
            root.iconbitmap(default=icon)
        except tk.TclError:
            theme.apply_icon(root)
    else:
        theme.apply_icon(root)

    ws = cls(root, initial=initial)
    ws._ws_owns_window = True
    ws.pack(fill="both", expand=True)

    # Give the tool its window semantics back: its own title text drives the window title, and
    # its WM_DELETE_WINDOW handler (registered during __init__, before this point) is rebound
    # onto the real toplevel.
    if ws._ws_title:
        root.title(ws._ws_title)
    if ws._ws_close:
        root.protocol("WM_DELETE_WINDOW", ws._ws_close)
    if ws.menubar() is not None:
        # Built during __init__, before _ws_owns_window was set, so attach it now.
        try:
            root.configure(menu=ws.menubar())
        except tk.TclError:
            pass
    root.mainloop()
    return ws
