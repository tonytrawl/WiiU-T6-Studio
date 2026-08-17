#!/usr/bin/env python3
"""WII U T6 Studio -- one window for fastfiles, texture paks and sound banks.

WHAT THIS IS
------------
A shell that HOSTS the three existing tools rather than replacing them. `ff_ide.IDE`,
`ipak_ide.IpakIDE` and `sab_ide.SabIDE` are now `core.workspace.Workspace` subclasses (frames
instead of windows), so this file opens a file, decides which workspace understands it, and packs
it into the document area. Every action, dialog and gate inside those tools is the same code that
shipped -- consolidation here is a layout change, not a re-implementation.

THE LAYOUT IS DYNAMIC BY CONSTRUCTION
-------------------------------------
There is no universal editor UI, because the three jobs genuinely differ: a fastfile is a tree of
typed assets, a pak is a flat grid of images, a bank is a list of sounds with a waveform. Forcing
one chrome onto all three would make each worse. So the shell owns only what is genuinely shared
-- open/recent, game folders, document tabs, status -- and swaps the entire working area for the
one that fits the open file. The rail and the accent colour follow the active document's kind, so
"what am I editing" is answerable at a glance without reading the title.

Standalone entry points still work: `python ff_ide.py x.ff` is unchanged.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk                                  # noqa: E402
from tkinter import filedialog, messagebox, ttk       # noqa: E402

from core import paths                                # noqa: E402
from core import theme                                # noqa: E402
from core import settings                             # noqa: E402
from core import uiutil                               # noqa: E402

APP_TITLE = "WII U T6 Studio"
APP_VERSION = "1.0"

#: Shown in the status bar and linked from it.
AUTHOR_URL = "https://github.com/tonytrawl"
AUTHOR_NOTE = "This tool was built by " + AUTHOR_URL

#: extension -> (kind, module, class, human label, one-line description)
REGISTRY = [
    ('zone', ('.ff', '.fastfile'), 'ff_ide', 'IDE', 'Fastfile',
     'Zone assets: scripts, models, materials, raw files'),
    ('ipak', ('.ipak',), 'ipak_ide', 'IpakIDE', 'Texture pak',
     'Streamed image parts: preview, extract, replace'),
    ('sab', ('.sabs', '.sabl'), 'sab_ide', 'SabIDE', 'Sound bank',
     'Sound entries: waveform, playback, extract, replace'),
    ('rpl', ('.rpl', '.rpx'), 'rpl_ide', 'RplIDE', 'Engine RPL',
     'Signature check, DLC load gate, render resolution'),
]

KIND_LABEL = {k: lbl for k, _e, _m, _c, lbl, _d in REGISTRY}
MAX_RECENT = 12


def kind_for(path):
    ext = os.path.splitext(path)[1].lower()
    for kind, exts, _mod, _cls, _lbl, _desc in REGISTRY:
        if ext in exts:
            return kind
    return None


def _load_workspace_class(kind):
    """Import a tool module on demand.

    Deliberately lazy: `ff_ide` drags in the whole walker/relink backend, which is slow to import
    and can fail on a machine missing the OAT reference data. A studio that cannot open sound
    banks because the fastfile backend is unavailable would be a worse tool than the three it
    replaces, so each kind fails independently and only when actually used.
    """
    for k, _exts, mod_name, cls_name, _lbl, _desc in REGISTRY:
        if k == kind:
            mod = __import__(mod_name)
            return getattr(mod, cls_name)
    raise KeyError(kind)


class Document(object):
    """One open file and the workspace showing it."""

    def __init__(self, path, kind, workspace):
        self.path = path
        self.kind = kind
        self.workspace = workspace
        self.tab = None
        self.label = os.path.basename(path) if path else 'Untitled'


class Studio(tk.Tk):
    def __init__(self, initial=None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1400x880")
        self.minsize(1060, 660)
        self.configure(bg=theme.BG)
        theme.install(self)

        # `apply_icon` uses iconbitmap(default=...), which makes this the icon for EVERY window
        # the app opens. Setting it without `default=` (as this did) left every dialog and
        # secondary window showing Tk's feather.
        theme.apply_icon(self)

        #: Marks this toplevel as the shell, so a hosted Workspace routes window commands
        #: (withdraw/iconify/...) to nothing instead of hiding the whole application.
        self._is_studio_shell = True
        self.docs = []
        self.active = None
        # Each tool builds its own menubar; the shell swaps it in with the document, so the menu
        # bar is part of the per-file-type layout rather than a lowest-common-denominator merge.
        self._empty_menu = tk.Menu(self)

        self._command_bar()
        self._tabstrip()
        self._body()
        self._statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Control-o>", lambda _e: self.open_file())
        self.bind_all("<Control-w>", lambda _e: self._close_active())
        self.bind_all("<Control-f>", lambda _e: self.find_asset())

        self._show_welcome()
        if initial:
            self.after(120, lambda: self.open_file(initial))
        # Deferred so the window is up first: this walks folders and parses RPL sections, and
        # doing that before the shell draws would look like a slow start.
        self.after(900, self.check_signature_patch)

    # ------------------------------------------------------------------ chrome
    def _command_bar(self):
        bar = tk.Frame(self, bg=theme.SURFACE, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Brand: an accent bar that re-tints to the active document's kind.
        self.brand_chip = tk.Frame(bar, bg=theme.ACCENT, width=3)
        self.brand_chip.pack(side="left", fill="y", pady=11, padx=(14, 0))
        tk.Label(bar, text=APP_TITLE, bg=theme.SURFACE, fg=theme.TEXT,
                 font=theme.FONT_TITLE).pack(side="left", padx=(10, 18))

        ttk.Button(bar, text="Open file", style="Accent.TButton",
                   command=self.open_file).pack(side="left", padx=(0, 6), pady=10)
        ttk.Button(bar, text="Find asset", style="Tool.TButton",
                   command=self.find_asset).pack(side="left", padx=2, pady=10)
        ttk.Button(bar, text="Bulk replace", style="Tool.TButton",
                   command=self.bulk_replace).pack(side="left", padx=2, pady=10)
        ttk.Button(bar, text="Game folders", style="Tool.TButton",
                   command=self._edit_folders).pack(side="left", padx=2, pady=10)
        ttk.Button(bar, text="?", style="Tool.TButton",
                   command=self.show_about).pack(side="left", padx=2, pady=10)

        # Right side: what kind of document is active, in that kind's colour.
        self.kind_lbl = tk.Label(bar, text="", bg=theme.SURFACE, fg=theme.MUTED,
                                 font=theme.FONT_UI)
        self.kind_lbl.pack(side="right", padx=16)
        theme.hairline(self).pack(fill="x")

    def _tabstrip(self):
        self.tabbar = tk.Frame(self, bg=theme.BG, height=34)
        self.tabbar.pack(fill="x")
        self.tabbar.pack_propagate(False)
        self.tabs_holder = tk.Frame(self.tabbar, bg=theme.BG)
        self.tabs_holder.pack(side="left", padx=8)
        # A "+" beside the open tabs, where a browser puts it. Same action as Open file, but
        # discoverable at the point you are thinking about tabs.
        self.newtab_btn = tk.Label(self.tabbar, text="  +  ", bg=theme.BG, fg=theme.MUTED,
                                   font=("Segoe UI", 12), cursor="hand2")
        self.newtab_btn.pack(side="left")
        self.newtab_btn.bind("<Button-1>", lambda _e: self.open_file())
        self.newtab_btn.bind("<Enter>",
                             lambda _e: self.newtab_btn.configure(fg=theme.TEXT,
                                                                  bg=theme.SURFACE))
        self.newtab_btn.bind("<Leave>",
                             lambda _e: self.newtab_btn.configure(fg=theme.MUTED,
                                                                  bg=theme.BG))

    def _body(self):
        self.body = tk.Frame(self, bg=theme.BG)
        self.body.pack(fill="both", expand=True)

    def _statusbar(self):
        theme.hairline(self).pack(fill="x")
        bar = tk.Frame(self, bg=theme.SURFACE, height=26)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="Ready", bg=theme.SURFACE, fg=theme.MUTED,
                               font=theme.FONT_UI, anchor="w")
        self.status.pack(side="left", padx=14)

        # Credit sits on the far right of the same bar, packed FIRST so it keeps its place when
        # a long path is shown -- the path then truncates against it instead of pushing it off.
        credit = tk.Label(bar, text=AUTHOR_NOTE, bg=theme.SURFACE, fg=theme.FAINT,
                          font=theme.FONT_UI, anchor="e", cursor="hand2")
        credit.pack(side="right", padx=14)
        credit.bind("<Button-1>", lambda _e: self._open_author_url())
        # Underline on hover so it reads as a link without colouring it like one at rest.
        credit.bind("<Enter>", lambda _e: credit.configure(
            fg=theme.ACCENT, font=(theme.FONT_UI[0], theme.FONT_UI[1], "underline")))
        credit.bind("<Leave>", lambda _e: credit.configure(
            fg=theme.FAINT, font=theme.FONT_UI))
        self.credit = credit

        self.path_lbl = tk.Label(bar, text="", bg=theme.SURFACE, fg=theme.FAINT,
                                 font=theme.FONT_UI, anchor="e")
        self.path_lbl.pack(side="right", padx=14)

    def _open_author_url(self):
        """Open the author's GitHub in the user's browser.

        webbrowser is stdlib and picks the platform's default handler, so this works on Windows,
        macOS and Linux without a shell-out. A failure is reported in the status bar rather than
        raised -- a headless or locked-down machine must not take the app down over a credit link.
        """
        import webbrowser
        try:
            if webbrowser.open_new_tab(AUTHOR_URL):
                self.say("Opened %s" % AUTHOR_URL)
                return
        except Exception:
            pass
        self.say("Could not open a browser -- %s" % AUTHOR_URL, theme.WARN)

    def say(self, text, colour=None):
        self.status.config(text=text, fg=colour or theme.MUTED)

    # ------------------------------------------------------------------ welcome
    def _show_welcome(self):
        """The screen when nothing is open. Also the discoverability surface for the merge:
        three tools became one, so the one window has to say what it can open."""
        self.welcome = tk.Frame(self.body, bg=theme.BG)
        self.welcome.pack(fill="both", expand=True)

        wrap = tk.Frame(self.welcome, bg=theme.BG)
        wrap.place(relx=0.5, rely=0.44, anchor="center")

        tk.Label(wrap, text=APP_TITLE, bg=theme.BG, fg=theme.TEXT,
                 font=("Segoe UI Light", 26)).pack(anchor="w")
        tk.Label(wrap, text="Fastfiles, texture paks and sound banks in one place.",
                 bg=theme.BG, fg=theme.MUTED, font=theme.FONT_UI_MED).pack(anchor="w",
                                                                          pady=(4, 22))

        for kind, exts, _m, _c, label, desc in REGISTRY:
            row = tk.Frame(wrap, bg=theme.SURFACE)
            row.pack(fill="x", pady=3)
            tk.Frame(row, bg=theme.KIND_ACCENT[kind], width=3).pack(side="left", fill="y")
            inner = tk.Frame(row, bg=theme.SURFACE)
            inner.pack(side="left", fill="x", expand=True, padx=14, pady=9)
            tk.Label(inner, text="%s   %s" % (label, "  ".join(exts)), bg=theme.SURFACE,
                     fg=theme.TEXT, font=theme.FONT_UI_MED).pack(anchor="w")
            tk.Label(inner, text=desc, bg=theme.SURFACE, fg=theme.MUTED,
                     font=theme.FONT_UI).pack(anchor="w")

        btns = tk.Frame(wrap, bg=theme.BG)
        btns.pack(anchor="w", pady=(20, 0))
        ttk.Button(btns, text="Open a file...", style="Accent.TButton",
                   command=self.open_file).pack(side="left")

        recents = self._recent()
        if recents:
            tk.Label(wrap, text="Recent", bg=theme.BG, fg=theme.FAINT,
                     font=theme.FONT_UI).pack(anchor="w", pady=(22, 4))
            for p in recents[:6]:
                k = kind_for(p) or 'zone'
                row = tk.Frame(wrap, bg=theme.BG)
                row.pack(fill="x", anchor="w")
                dot = tk.Label(row, text="\u25cf", bg=theme.BG,
                               fg=theme.KIND_ACCENT.get(k, theme.MUTED), font=("Segoe UI", 8))
                dot.pack(side="left", padx=(0, 7))
                lnk = tk.Label(row, text=os.path.basename(p), bg=theme.BG, fg=theme.MUTED,
                               font=theme.FONT_UI, cursor="hand2")
                lnk.pack(side="left")
                dim = tk.Label(row, text="  " + os.path.dirname(p), bg=theme.BG,
                               fg=theme.FAINT, font=theme.FONT_UI)
                dim.pack(side="left")
                for w in (lnk, dim):
                    w.bind("<Button-1>", lambda _e, path=p: self.open_file(path))
                    w.bind("<Enter>", lambda _e, l=lnk: l.config(fg=theme.ACCENT))
                    w.bind("<Leave>", lambda _e, l=lnk: l.config(fg=theme.MUTED))

    def _hide_welcome(self):
        if getattr(self, 'welcome', None) is not None:
            self.welcome.destroy()
            self.welcome = None

    # ------------------------------------------------------------------ recents
    def _recent(self):
        cfg = settings.load()
        return [p for p in (cfg.get('recent') or []) if os.path.exists(p)]

    def _note_recent(self, path):
        cfg = settings.load()
        rec = [p for p in (cfg.get('recent') or []) if os.path.abspath(p) != os.path.abspath(path)]
        rec.insert(0, os.path.abspath(path))
        cfg['recent'] = rec[:MAX_RECENT]
        try:
            settings.save(cfg)
        except Exception:
            pass                      # a read-only config location is not worth an error dialog

    # ------------------------------------------------------------------ opening
    def open_file(self, path=None):
        if path is None:
            pats = []
            for _k, exts, _m, _c, label, _d in REGISTRY:
                pats.append((label, " ".join("*" + e for e in exts)))
            allexts = " ".join("*" + e for _k, exts, _m, _c, _l, _d in REGISTRY for e in exts)
            path = filedialog.askopenfilename(
                title="Open",
                filetypes=[("All supported", allexts)] + pats + [("All files", "*.*")])
        if not path:
            return
        kind = kind_for(path)
        if kind is None:
            messagebox.showinfo(
                APP_TITLE,
                "Nothing here understands %s.\n\nThis window opens fastfiles (.ff), texture "
                "paks (.ipak) and sound banks (.sabs/.sabl)." % os.path.basename(path))
            return

        # Any number of documents may be open at once, of the same kind or different kinds --
        # two zones side by side, a zone and the pak it streams from, three banks. The only
        # case worth a question is the SAME FILE twice: both views save the whole file, so the
        # later save silently discards the other's edits. Default to focusing what is already
        # open, and make a second view only on request.
        dupe = next((d for d in self.docs
                     if d.path and os.path.abspath(d.path) == os.path.abspath(path)), None)
        if dupe is not None:
            if not messagebox.askyesno(
                    APP_TITLE,
                    "%s is already open.\n\nOpen a second view of it?\n\nBoth views write the "
                    "whole file when saved, so whichever you save last overwrites the other "
                    "one's edits." % os.path.basename(path)):
                self._activate(dupe)
                return

        self.say("Opening %s..." % os.path.basename(path))
        self.update_idletasks()
        try:
            cls = _load_workspace_class(kind)
        except Exception as ex:
            messagebox.showerror(
                APP_TITLE,
                "The %s backend could not be loaded:\n\n%s: %s\n\nThe other file types are "
                "unaffected." % (KIND_LABEL.get(kind, kind), type(ex).__name__, ex))
            self.say("Backend unavailable for %s" % kind, theme.WARN)
            return

        try:
            ws = cls(self.body, initial=path)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not open %s:\n\n%s: %s\n\n%s"
                                 % (os.path.basename(path), type(ex).__name__, ex,
                                    traceback.format_exc(limit=3)))
            self.say("Failed to open %s" % os.path.basename(path), theme.WARN)
            return

        doc = Document(path, kind, ws)
        # Distinguish repeat views of one file, or the tabs are indistinguishable.
        views = sum(1 for d in self.docs
                    if d.path and os.path.abspath(d.path) == os.path.abspath(path))
        if views:
            doc.label = "%s  (%d)" % (doc.label, views + 1)
        ws.on_title(lambda _w, text, d=doc: self._on_ws_title(d, text))
        self.docs.append(doc)
        self._note_recent(path)
        self._rebuild_tabs()
        self._activate(doc)
        self.say("Opened %s" % os.path.basename(path), theme.OK)

    def _on_ws_title(self, doc, text):
        """A tool renamed itself (usually 'Tool -- file'); keep the tab honest without
        letting the tool's own product name leak into every tab."""
        if not text:
            return
        tail = text.split("--")[-1].strip()
        if tail and tail != text.strip():
            doc.label = tail
            self._rebuild_tabs()

    # ------------------------------------------------------------------ tabs
    def _rebuild_tabs(self):
        for w in self.tabs_holder.winfo_children():
            w.destroy()
        # The strip stays visible even with nothing open, because the "+" lives there.
        self.tabbar.configure(height=34)
        if not self.docs:
            return
        for d in self.docs:
            on = (d is self.active)
            bg = theme.SURFACE if on else theme.BG
            tab = tk.Frame(self.tabs_holder, bg=bg)
            tab.pack(side="left", padx=(0, 2))
            tk.Frame(tab, bg=theme.KIND_ACCENT.get(d.kind, theme.ACCENT) if on else bg,
                     height=2).pack(fill="x", side="top")
            inner = tk.Frame(tab, bg=bg)
            inner.pack(fill="both", padx=12, pady=(3, 6))
            tk.Label(inner, text="\u25cf", bg=bg, font=("Segoe UI", 7),
                     fg=theme.KIND_ACCENT.get(d.kind, theme.MUTED)).pack(side="left",
                                                                         padx=(0, 6))
            lbl = tk.Label(inner, text=d.label, bg=bg,
                           fg=theme.TEXT if on else theme.MUTED, font=theme.FONT_UI,
                           cursor="hand2")
            lbl.pack(side="left")
            x = tk.Label(inner, text="\u2715", bg=bg, fg=theme.FAINT,
                         font=("Segoe UI", 8), cursor="hand2")
            x.pack(side="left", padx=(10, 0))
            for w in (tab, inner, lbl):
                w.bind("<Button-1>", lambda _e, doc=d: self._activate(doc))
            x.bind("<Button-1>", lambda _e, doc=d: self._close_doc(doc))
            x.bind("<Enter>", lambda _e, w=x: w.config(fg=theme.WARN))
            x.bind("<Leave>", lambda _e, w=x: w.config(fg=theme.FAINT))
            d.tab = tab

    def _activate(self, doc):
        """Swap the working area. THIS is the dynamic layout: each kind brings its own chrome."""
        if self.active is doc:
            return
        self._hide_welcome()
        for d in self.docs:
            if d is not doc:
                d.workspace.pack_forget()
        self.active = doc
        doc.workspace.pack(fill="both", expand=True)
        accent = theme.KIND_ACCENT.get(doc.kind, theme.ACCENT)
        self.brand_chip.configure(bg=accent)
        self.kind_lbl.configure(text=KIND_LABEL.get(doc.kind, ''), fg=accent)
        self.path_lbl.configure(text=doc.path or '')
        self.title("%s -- %s" % (APP_TITLE, doc.label))
        self._apply_menu(doc.workspace.menubar())
        self._rebuild_tabs()

    def _apply_menu(self, menu):
        try:
            self.configure(menu=menu if menu is not None else self._empty_menu)
        except tk.TclError:
            pass                      # a platform without menubars is not a reason to fail

    def _close_active(self):
        if self.active:
            self._close_doc(self.active)

    def _close_doc(self, doc):
        # Let the tool run its own close logic (unsaved-changes prompts live in there).
        if not doc.workspace.close():
            self.say("%s kept open" % doc.label)
            return
        try:
            doc.workspace.destroy()
        except Exception:
            pass
        self.docs.remove(doc)
        if self.active is doc:
            self.active = None
            if self.docs:
                self._activate(self.docs[-1])
            else:
                self.brand_chip.configure(bg=theme.ACCENT)
                self.kind_lbl.configure(text="")
                self.path_lbl.configure(text="")
                self.title(APP_TITLE)
                self._apply_menu(None)
                self._show_welcome()
        self._rebuild_tabs()

    # ------------------------------------------------------------------ find an asset
    def find_asset(self):
        """Search the prebuilt dictionary by name and say WHICH PAK(S) hold each texture.

        The point is to answer "where does this live" without opening paks one at a time. It
        also surfaces the thing that silently wastes an edit: when a part is carried by more
        than one pak, the console binds whichever loads FIRST, so editing a later copy does
        nothing. Those rows are flagged.
        """
        from core import ipak_search as isearch

        win = tk.Toplevel(self)
        win.title("Find asset")
        win.configure(bg=theme.BG)
        win.geometry("1080x620")
        win.transient(self)

        top = tk.Frame(win, bg=theme.BG)
        top.pack(fill="x", padx=14, pady=(14, 8))
        tk.Label(top, text="Texture name contains", bg=theme.BG, fg=theme.MUTED,
                 font=theme.FONT_UI).pack(side="left", padx=(0, 8))
        qvar = tk.StringVar()
        ent = ttk.Entry(top, textvariable=qvar, width=42)
        ent.pack(side="left")
        ent.focus_set()
        tk.Label(top, text="or paste a hash like 0A0FEE98", bg=theme.BG, fg=theme.FAINT,
                 font=theme.FONT_UI).pack(side="left", padx=10)

        cov = tk.Label(win, text="", bg=theme.BG, fg=theme.FAINT, font=theme.FONT_UI,
                       anchor="w", justify="left")
        cov.pack(fill="x", padx=14)

        panes = tk.PanedWindow(win, orient="horizontal", bg=theme.BORDER, sashwidth=4, bd=0)
        panes.pack(fill="both", expand=True, padx=14, pady=10)

        left = tk.Frame(panes, bg=theme.BG)
        # "where" is the whole point of the feature: the answer to "which file do I open to edit
        # this?" belongs in the list, not only in the detail pane after a click.
        cols = ("format", "size", "parts", "where")
        tree = ttk.Treeview(left, columns=cols, style="Tree.Treeview", height=18)
        tree.heading("#0", text="texture")
        tree.column("#0", width=300, anchor="w")
        for c, w in zip(cols, (70, 90, 50, 190)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True)
        panes.add(left, minsize=420)

        right = tk.Frame(panes, bg=theme.BG)
        detail = tk.Text(right, bg=theme.EDIT, fg=theme.TEXT, insertbackground=theme.TEXT,
                         relief="flat", wrap="none", font=theme.FONT_MONO, height=18)
        detail.pack(fill="both", expand=True)
        detail.tag_configure("warn", foreground=theme.INFO)
        detail.tag_configure("pak", foreground=theme.ACCENT)
        panes.add(right, minsize=340)

        btns = tk.Frame(win, bg=theme.BG)
        # side="bottom" + before=panes pins the footer and reserves its height ahead of the
        # expanding body, so the buttons stay reachable at any window size (pack order decides who
        # gets starved, not the side= alone).
        btns.pack(side="bottom", fill="x", padx=14, pady=(0, 14), before=panes)
        open_btn = ttk.Button(btns, text="Open selected file", style="Accent.TButton",
                              state="disabled")
        open_btn.pack(side="left")
        reveal_btn = ttk.Button(btns, text="Show in folder", style="Ghost.TButton",
                                state="disabled")
        reveal_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Add game folder...", style="Ghost.TButton",
                   command=lambda: (win.destroy(), self._edit_folders())).pack(side="left",
                                                                              padx=6)
        zbtn = ttk.Button(btns, text="Index zone images", style="Ghost.TButton")
        zbtn.pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")

        state = {'hits': [], 'owners': None, 'paks': [], 'sel_pak': None}

        def _where(h):
            """One-line answer to 'which file holds this?' -- names the file when there is one.

            Naming the single owner is worth far more than a count: '3' tells you nothing you can
            act on, 'base_split1.ipak' tells you exactly what to open.
            """
            names, zs = [], h.get('zones') or ()
            for p in h['parts']:
                for q in p.get('paks') or ():
                    b = os.path.basename(q)
                    if b not in names:
                        names.append(b)
            for z in zs:
                b = os.path.basename(z[0])
                if b not in names:
                    names.append(b)
            if not names:
                return "not in any indexed file"
            if len(names) == 1:
                return names[0]
            return "%s +%d more" % (names[0], len(names) - 1)

        def set_cov():
            n = len(state['paks'])
            dirs = 0
            try:
                dirs = len(settings.search_dirs())
            except Exception:
                pass
            zn = len(state.get('zimgs') or {})
            msg = ("Index covers %d pak%s across %d folder%s"
                   % (n, '' if n == 1 else 's', dirs, '' if dirs == 1 else 's'))
            msg += (", plus %d inline zone image(s)." % zn if zn else
                    ". Zone images are NOT indexed yet, so textures stored inside a fastfile "
                    "will not appear -- use 'Index zone images'.")
            if n < 30:
                msg += ("  Only what the tool can see is searchable -- add your game 'content' "
                        "folder to cover the whole install.")
            cov.configure(text=msg)

        def do_search(*_a):
            q = qvar.get().strip()
            tree.delete(*tree.get_children())
            detail.delete("1.0", "end")
            open_btn.configure(state="disabled")
            state['sel_pak'] = None
            if len(q) < 2:
                return
            try:
                if state['owners'] is None:
                    state['owners'], state['paks'] = isearch.owner_map()
                    set_cov()
                # Pass None, not {} -- an empty dict reads as "already loaded, and it is empty",
                # which suppresses search()'s own cache-only load and silently drops every
                # inline zone image from the results.
                hits = isearch.search(q, owners=state['owners'],
                                      zone_images=state.get('zimgs') or None)
            except Exception as ex:
                detail.insert("end", "Search failed: %s: %s" % (type(ex).__name__, ex))
                return
            state['hits'] = hits
            for i, h in enumerate(hits):
                p0 = h['parts'][0] if h['parts'] else {}
                zs = h.get('zones') or ()
                dims = ('%sx%s' % (p0.get('width'), p0.get('height'))
                        if p0.get('width')
                        else ('%sx%s' % (zs[0][1], zs[0][2]) if zs else '?'))
                label = ('⚠ ' if h['shadowed'] else '') + (h['name'] or '?')
                tree.insert("", "end", iid=str(i), text=label,
                            values=(h.get('format') or '?', dims,
                                    len(h['parts']) or len(zs), _where(h)))
            if not hits:
                detail.insert("end", "Nothing matched %r.\n\nNames come from the prebuilt "
                                     "dictionary; if it has not indexed your install yet, use "
                                     "Add game folder and rebuild the name dictionary."
                              % q)

        def on_pick(_e=None):
            sel = tree.selection()
            detail.delete("1.0", "end")
            state['sel_pak'] = None
            open_btn.configure(state="disabled")
            reveal_btn.configure(state="disabled")
            if not sel:
                return
            h = state['hits'][int(sel[0])]
            for line in isearch.describe(h):
                tag = ("warn" if line.strip().startswith("⚠")
                       else "pak" if line.strip().lower().endswith(".ipak") else None)
                detail.insert("end", line + "\n", tag or ())
            # Prefer a pak (that is where a streamed texture is edited); fall back to the fastfile
            # carrying it inline, which for 97% of indexed zone images is the ONLY place it lives.
            first = next((q for p in h['parts'] for q in p['paks']), None)
            if first:
                state['sel_pak'] = first
                open_btn.configure(state="normal", text="Open %s" % os.path.basename(first))
                reveal_btn.configure(state="normal")
            else:
                zp = next((p for p in (h.get('zone_files') or ()) if p), None)
                if zp:
                    state['sel_pak'] = zp
                    open_btn.configure(state="normal",
                                       text="Open %s" % os.path.basename(zp))
                    reveal_btn.configure(state="normal")
                else:
                    open_btn.configure(state="disabled", text="Open selected file")
                    reveal_btn.configure(state="disabled")

        def open_sel():
            if state['sel_pak']:
                win.destroy()
                self.open_file(state['sel_pak'])

        def reveal_sel():
            """Open the containing folder with the file selected.

            Cross-platform by design -- this tool is not Windows-only even though most of its
            users are. Each platform gets its native 'reveal', and an unknown one falls back to
            copying the path to the clipboard rather than doing nothing.
            """
            p = state.get('sel_pak')
            if not p:
                return
            import subprocess
            try:
                if sys.platform == 'win32':
                    subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', '-R', p])
                else:
                    subprocess.Popen(['xdg-open', os.path.dirname(p)])
            except Exception:
                win.clipboard_clear()
                win.clipboard_append(p)
                messagebox.showinfo(APP_TITLE, "Could not open a file manager here.\n\n"
                                               "The path is on your clipboard:\n%s" % p,
                                    parent=win)

        reveal_btn.configure(command=reveal_sel)

        open_btn.configure(command=open_sel)
        tree.bind("<<TreeviewSelect>>", on_pick)
        tree.bind("<Double-1>", lambda _e: open_sel())
        ent.bind("<Return>", do_search)
        qvar.trace_add("write", lambda *_a: win.after(250, do_search))

        # ---- zone indexing: worker thread, live progress, cancellable ---------------------
        # This decrypts and censuses EVERY reachable fastfile. Run on the UI thread it would
        # freeze the window for minutes with nothing moving, which is indistinguishable from a
        # hang -- so it runs on a worker, reports zones done/total, and can be stopped.
        prog_row = tk.Frame(win, bg=theme.BG)
        prog = ttk.Progressbar(prog_row, style="FF.Horizontal.TProgressbar", length=260,
                               mode="determinate")
        prog.pack(side="left")
        prog_lbl = tk.Label(prog_row, text="", bg=theme.BG, fg=theme.MUTED,
                            font=theme.FONT_UI, anchor="w")
        prog_lbl.pack(side="left", padx=10)
        cancel_btn = ttk.Button(prog_row, text="Stop", style="Ghost.TButton")
        cancel_btn.pack(side="left")

        def index_zones():
            import threading
            import queue as _queue
            stop = threading.Event()
            q = _queue.Queue()

            def work():
                try:
                    imgs, done = isearch.zone_image_map(
                        progress=lambda i, n, p: q.put(('p', i, n, p)),
                        should_stop=stop.is_set)
                    q.put(('done', imgs, done, stop.is_set()))
                except Exception as ex:                       # noqa: BLE001
                    q.put(('err', ex))

            def drain():
                try:
                    while True:
                        msg = q.get_nowait()
                        if msg[0] == 'p':
                            _t, i, n, p = msg
                            prog.configure(maximum=max(n, 1), value=i)
                            prog_lbl.configure(
                                text=('%d / %d fastfiles%s' % (i, n,
                                      '  -  ' + os.path.basename(p) if p else '')))
                        elif msg[0] == 'err':
                            finish()
                            messagebox.showerror(APP_TITLE, "Zone indexing failed:\n\n%s: %s"
                                                 % (type(msg[1]).__name__, msg[1]))
                            return
                        else:
                            _t, imgs, done, was_cancelled = msg
                            state['zimgs'] = imgs
                            finish()
                            if was_cancelled:
                                self.say("zone indexing stopped after %d fastfile(s) -- "
                                         "partial results kept for this session, nothing "
                                         "cached" % len(done), theme.INFO)
                            else:
                                self.say("indexed inline images from %d fastfile(s)"
                                         % len(done), theme.OK)
                            set_cov()
                            do_search()
                            return
                except _queue.Empty:
                    pass
                win.after(120, drain)

            def finish():
                prog_row.pack_forget()
                zbtn.configure(state="normal")

            cancel_btn.configure(command=lambda: (stop.set(),
                                                  prog_lbl.configure(text="stopping...")))
            zbtn.configure(state="disabled")
            # below the button row and reserved ahead of the body, same reason as `btns`
            prog_row.pack(side="bottom", fill="x", padx=14, pady=(0, 6), before=btns)
            prog.configure(value=0)
            prog_lbl.configure(text="starting...")
            threading.Thread(target=work, daemon=True).start()
            win.after(120, drain)

        zbtn.configure(command=index_zones)

        # Build the ownership index up front so the coverage line is honest before any search.
        # Zone images come from the CACHE only -- building it is minutes of work and must be a
        # deliberate action, not something that happens because a search box opened.
        try:
            state['owners'], state['paks'] = isearch.owner_map()
        except Exception:
            state['owners'], state['paks'] = {}, []
        try:
            state['zimgs'], state['zones'] = isearch.zone_image_map(build=False)
        except Exception:
            state['zimgs'], state['zones'] = {}, []
        set_cov()

    # ------------------------------------------------------------------ about / help
    def bulk_replace(self):
        """Apply a folder of replacement images to every pak and fastfile that carries them.

        This is the payoff of the texture index: a PC mod is a pile of files named after the
        textures they replace, and the expensive part of porting one has always been working out
        WHERE each name lives -- which is exactly the question the index already answers.

        ⚠ THIS WRITES TO GAME FILES. Nothing happens until Replace all is pressed and confirmed,
        and every target is backed up write-once before its first edit.
        """
        from core import bulk_replace as BR

        win = tk.Toplevel(self)
        win.title("Bulk replace textures")
        win.configure(bg=theme.BG)
        win.geometry("1040x660")
        win.transient(self)

        head = tk.Frame(win, bg=theme.BG)
        head.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(head, text="Replacement images", bg=theme.BG, fg=theme.TEXT,
                 font=theme.FONT_TITLE).pack(side="left")
        summary = tk.Label(win, text="Add images named the way the game names them "
                                     "(mytexture.dds, mytexture.png). Every pak and fastfile "
                                     "carrying that name is found for you.",
                           bg=theme.BG, fg=theme.FAINT, font=theme.FONT_UI,
                           anchor="w", justify="left", wraplength=1000)
        summary.pack(fill="x", padx=14)

        pick = tk.Frame(win, bg=theme.BG)
        pick.pack(fill="x", padx=14, pady=8)

        cols = ("name", "status", "where")
        tree = ttk.Treeview(pick.master, columns=cols, style="Tree.Treeview", height=13)
        tree.heading("#0", text="file")
        tree.column("#0", width=300, anchor="w")
        for c, w in zip(cols, (240, 110, 330)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")

        state = {'files': [], 'items': [], 'stop': False, 'busy': False}

        def refill():
            tree.delete(*tree.get_children())
            for i, it in enumerate(state['items']):
                where = ", ".join(sorted(set(os.path.basename(t['path'])
                                             for t in it['targets'])))[:80] or it['detail']
                tree.insert("", "end", iid=str(i),
                            text=os.path.basename(it['src']),
                            values=(it['name'] or '-', it['status'], where))
            summary.configure(text=BR.summarise(state['items']) if state['items'] else
                              "Add images named the way the game names them.")

        def replan():
            if not state['files']:
                state['items'] = []
                refill()
                return
            try:
                state['items'] = BR.plan(state['files'])
            except Exception as ex:
                messagebox.showerror(APP_TITLE, "Could not match those files:\n\n%s: %s"
                                     % (type(ex).__name__, ex), parent=win)
                return
            refill()

        def add_files():
            ps = filedialog.askopenfilenames(
                parent=win, title="Add replacement images",
                filetypes=[("Images", "*.png;*.dds;*.tga;*.bmp;*.jpg;*.jpeg"), ("All", "*.*")])
            if ps:
                state['files'].extend(p for p in ps if p not in state['files'])
                replan()

        def add_folder():
            d = filedialog.askdirectory(parent=win, title="Add every image in a folder")
            if not d:
                return
            found = []
            for dirpath, _dd, fs in os.walk(d):
                for fn in sorted(fs):
                    if fn.lower().endswith(BR.IMAGE_EXTS):
                        p = os.path.join(dirpath, fn)
                        if p not in state['files']:
                            found.append(p)
            if not found:
                messagebox.showinfo(APP_TITLE, "No images found in that folder.", parent=win)
                return
            state['files'].extend(found)
            replan()

        def clear():
            state['files'], state['items'] = [], []
            refill()

        def add_paths(paths):
            """Dropped paths: files are taken directly, folders are walked."""
            found = []
            for p in paths:
                if os.path.isdir(p):
                    for dirpath, _dd, fs in os.walk(p):
                        found.extend(os.path.join(dirpath, f) for f in sorted(fs)
                                     if f.lower().endswith(BR.IMAGE_EXTS))
                elif p.lower().endswith(BR.IMAGE_EXTS):
                    found.append(p)
            new = [p for p in found if p not in state['files']]
            if new:
                state['files'].extend(new)
                replan()
            elif found:
                summary.configure(text="Those files are already in the list.")
            else:
                summary.configure(text="Nothing dropped that looks like an image "
                                       "(%s)." % ", ".join(BR.IMAGE_EXTS))

        dropped = uiutil.enable_file_drop(win, add_paths)

        ttk.Button(pick, text="Add files...", style="Tool.TButton",
                   command=add_files).pack(side="left")
        ttk.Button(pick, text="Add folder...", style="Tool.TButton",
                   command=add_folder).pack(side="left", padx=6)
        ttk.Button(pick, text="Clear", style="Ghost.TButton",
                   command=clear).pack(side="left")
        tk.Label(pick, text=("or drag images and folders onto this window" if dropped
                             else "drag and drop is Windows only -- use the buttons"),
                 bg=theme.BG, fg=theme.FAINT, font=theme.FONT_UI).pack(side="left", padx=12)

        tree.pack(fill="both", expand=True, padx=14)

        log = tk.Text(win, bg=theme.EDIT, fg=theme.TEXT, insertbackground=theme.TEXT,
                      relief="flat", wrap="none", font=theme.FONT_MONO, height=8)
        log.pack(fill="both", expand=True, padx=14, pady=(8, 0))

        prow = tk.Frame(win, bg=theme.BG)
        # Progress row and the button row below it are both pinned and reserved ahead of the two
        # expanding panes (`tree` and `log`), which otherwise starve them on a short window.
        prow.pack(side="bottom", fill="x", padx=14, pady=(6, 0), before=tree)
        prog = ttk.Progressbar(prow, style="FF.Horizontal.TProgressbar", length=300,
                               mode="determinate")
        prog.pack(side="left")
        plbl = tk.Label(prow, text="", bg=theme.BG, fg=theme.MUTED, font=theme.FONT_UI)
        plbl.pack(side="left", padx=10)

        btns = tk.Frame(win, bg=theme.BG)
        btns.pack(side="bottom", fill="x", padx=14, pady=12, before=prow)
        go = ttk.Button(btns, text="Replace all", style="Accent.TButton")
        go.pack(side="left")
        stop_btn = ttk.Button(btns, text="Stop", style="Ghost.TButton", state="disabled")
        stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")

        def run():
            if state['busy']:
                return
            items = [i for i in state['items'] if i['status'] == 'ok']
            if not items:
                messagebox.showinfo(APP_TITLE, "Nothing matched, so there is nothing to replace.",
                                    parent=win)
                return
            jobs = BR.group_by_target(items)
            names = sorted(set(os.path.basename(p) for _k, p in jobs))
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Replace %d texture(s) across %d file(s)?\n\n%s\n\n"
                    "These are your real game files. Each one is backed up write-once before its "
                    "first edit.\n\nRemember: an edit to a pak that loads LATER than another pak "
                    "holding the same texture will have no effect in game."
                    % (len(items), len(jobs),
                       "\n".join("  " + n for n in names[:14])
                       + ("\n  ... and %d more" % (len(names) - 14) if len(names) > 14 else "")),
                    parent=win):
                return

            import queue as _queue
            import threading
            q = _queue.Queue()
            state.update(busy=True, stop=False)
            go.configure(state="disabled")
            stop_btn.configure(state="normal")
            log.delete("1.0", "end")
            prog.configure(maximum=max(1, len(jobs)), value=0)

            def worker():
                try:
                    res = BR.apply(items,
                                   progress=lambda d, t, lb: q.put(('p', d, t, lb)),
                                   should_stop=lambda: state['stop'])
                    q.put(('done', res))
                except Exception as ex:
                    q.put(('err', '%s: %s' % (type(ex).__name__, ex)))

            threading.Thread(target=worker, daemon=True).start()

            def pump():
                try:
                    while True:
                        m = q.get_nowait()
                        if m[0] == 'p':
                            _t, d, t, lb = m
                            prog.configure(maximum=max(1, t), value=d)
                            plbl.configure(text=("%d / %d   %s" % (d, t, lb)) if lb
                                           else "%d / %d" % (d, t))
                        elif m[0] == 'err':
                            log.insert("end", "FAILED: %s\n" % m[1])
                            state['busy'] = False
                            go.configure(state="normal")
                            stop_btn.configure(state="disabled")
                            return
                        else:
                            res = m[1]
                            for line in res['log']:
                                log.insert("end", line + "\n")
                            log.insert("end", "\n%d replacement(s) written across %d file(s)."
                                              % (res['replaced'], len(res['written'])))
                            if res['failed']:
                                log.insert("end", "\n%d file(s) failed:\n" % len(res['failed']))
                                for p, e in res['failed']:
                                    log.insert("end", "   %s -- %s\n" % (os.path.basename(p), e))
                            log.insert("end", "\n\nCold start the game: resolved texture parts "
                                              "stay cached, so a hot reload can serve the old "
                                              "bytes and make a good edit look broken.\n")
                            log.see("end")
                            state['busy'] = False
                            go.configure(state="normal")
                            stop_btn.configure(state="disabled")
                            plbl.configure(text="done")
                            return
                except _queue.Empty:
                    pass
                win.after(80, pump)

            win.after(80, pump)

        go.configure(command=run)
        stop_btn.configure(command=lambda: state.update(stop=True))
        win.bind("<Escape>", lambda _e: win.destroy())

    def check_signature_patch(self):
        """Warn, ONCE, if the engine RPL we can see is not signature-patched.

        ⚠ WHY THIS EXISTS. A fastfile this tool writes carries a ZEROED RSA signature, and an
        unpatched console refuses it -- so every edit fails, including a save with no changes at
        all. That cost one user days before it was diagnosed. The information was always
        available; nothing was asking for it.

        ⚠ AND WHY IT IS CAREFULLY HEDGED. We do NOT know where the user deploys. We can only
        inspect the RPLs in the folders configured here, which is usually the update/patch folder
        -- the live one Cemu loads -- but may not be the copy that ends up on their console. So
        the message NAMES THE EXACT FILE it judged and says plainly that the check does not apply
        if that is not their deploy target. An over-confident warning here would send people
        editing a file they should not touch.
        """
        try:
            from core import rpl_patch as RP
            from core import settings as _st
            cfg = _st.load()
            if cfg.get('sig_warn_dismissed'):
                return
            cands = []
            for d in _st.search_dirs():
                for root, _dd, files in os.walk(d):
                    for fn in files:
                        if fn.lower().endswith(('.rpl', '.rpx')) and 'cafef' in fn.lower():
                            cands.append(os.path.join(root, fn))
                    if root.count(os.sep) - d.count(os.sep) >= 3:
                        _dd[:] = []
            checked = None
            for p in sorted(cands):
                try:
                    _r, found = RP.survey(p)
                except Exception:
                    continue
                sig = found.get('signature') or {}
                if not sig.get('ok'):
                    continue
                checked = (p, bool(sig.get('already')))
                if sig.get('already'):
                    return                      # patched: nothing to say
                break
            if not checked or checked[1]:
                return
            path = checked[0]
            if messagebox.askyesno(
                    APP_TITLE,
                    "The engine RPL this tool can see does NOT have the fastfile signature "
                    "patch:\n\n    %s\n\nThat is the live RPL for that folder -- the copy an "
                    "emulator would load from there.\n\nWhy it matters: any .ff this tool saves "
                    "has a zeroed signature, and an UNPATCHED engine rejects it. Edits then "
                    "fail for no visible reason, including saving a zone with no changes.\n\n"
                    "⚠ This check only looked at the folders configured here. If that is NOT "
                    "the path you actually deploy to, this may not apply to your setup and the "
                    "RPL you deploy could well be patched already.\n\n"
                    "Open it in the RPL tab now?" % path):
                self.open_file(path)
            else:
                cfg['sig_warn_dismissed'] = True
                _st.save(cfg)
        except Exception:
            pass                                # a convenience check must never block startup

    def show_about(self):
        """What these tools do, in plain language, plus the two facts that waste the most time.

        Written for someone who just downloaded this: what each file type is, what can be
        changed, and -- first, because it is the difference between an edit working and not --
        that a repacked fastfile is unsigned and needs a patched RPL to load at all.
        """
        win = tk.Toplevel(self)
        win.title("About " + APP_TITLE)
        win.configure(bg=theme.BG)
        win.geometry("880x680")
        win.transient(self)

        head = tk.Frame(win, bg=theme.BG)
        head.pack(fill="x", padx=20, pady=(20, 6))
        tk.Frame(head, bg=theme.ACCENT, width=4).pack(side="left", fill="y")
        box = tk.Frame(head, bg=theme.BG)
        box.pack(side="left", padx=12)
        tk.Label(box, text=APP_TITLE, bg=theme.BG, fg=theme.TEXT,
                 font=("Segoe UI Light", 22)).pack(anchor="w")
        tk.Label(box, text="Edit Call of Duty: Black Ops II files for Wii U -- version %s"
                          % APP_VERSION, bg=theme.BG, fg=theme.MUTED,
                 font=theme.FONT_UI_MED).pack(anchor="w")

        # ⚠ FOOTER IS PACKED BEFORE THE BODY, AND side="bottom". Tk's packer allocates space in
        # PACK ORDER, so anything packed AFTER an expand=True widget is the first thing clipped when
        # the window is short -- which is why "Open GitHub" and "Close" were off-screen at the
        # default size until the user resized. Packing the footer first reserves its height for good
        # and the scrolling body gives way instead, so the buttons stay visible at any window size
        # and at any scroll position. Opening the window BIGGER would have been the wrong fix: on a
        # small display it just eats the screen.
        row = tk.Frame(win, bg=theme.BG)
        row.pack(side="bottom", fill="x", padx=20, pady=(8, 16))
        ttk.Button(row, text="Open GitHub", style="Ghost.TButton",
                   command=self._open_author_url).pack(side="left")
        ttk.Button(row, text="Close", style="Accent.TButton",
                   command=win.destroy).pack(side="right")

        body = tk.Frame(win, bg=theme.BG)
        body.pack(side="top", fill="both", expand=True, padx=20, pady=8)
        # Scrollbar first for the same reason. It also makes the scrolling DISCOVERABLE: the text
        # always scrolled on the wheel, but nothing on screen said there was more below.
        sb = ttk.Scrollbar(body, orient="vertical")
        sb.pack(side="right", fill="y")
        txt = tk.Text(body, bg=theme.EDIT, fg=theme.TEXT, relief="flat", wrap="word",
                      font=theme.FONT_UI, padx=16, pady=14, spacing1=2, spacing3=6,
                      yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.configure(command=txt.yview)
        # Floor the window at a size that still shows the footer plus a few lines of text, so the
        # layout cannot be dragged into the broken state it used to open in.
        win.minsize(560, 320)
        txt.tag_configure("h", foreground=theme.ACCENT,
                          font=("Segoe UI Semibold", 11), spacing1=10)
        txt.tag_configure("warn", foreground=theme.INFO)
        txt.tag_configure("dim", foreground=theme.MUTED)

        def h(s):
            txt.insert("end", s + "\n", "h")

        def p(s, tag=None):
            txt.insert("end", s + "\n", tag or ())

        p("Open a file and the window changes to suit it. Four kinds are supported, and any "
          "number can be open at once as tabs.")

        h("Fastfiles  (.ff)")
        p("A zone: the packaged assets for a map, the menus, or the shared game data. You can "
          "browse every asset, edit and grow GSC scripts, Lua/HKS, and raw text/cfg/csv files, "
          "preview models in 3D, and add new scripts and raw files. Textures stored inside the "
          "zone can be previewed, exported and replaced from the Textures button; the Sound "
          "banks button shows which sound each id maps to.")

        h("Texture paks  (.ipak)")
        p("Where most textures actually live, streamed in as the game needs them. Preview, "
          "export to PNG, replace with your own image, or add new entries. An image is split "
          "across up to three parts holding different mip levels -- replacing covers every part "
          "in the pak you have open, and the tool tells you if other parts live elsewhere.")

        h("Sound banks  (.sabs / .sabl)")
        p("The audio itself. Browse entries, see the waveform, play them, extract to WAV, and "
          "replace or add sounds. Names come from the fastfiles, so point the tool at your game "
          "folder to see them instead of hex ids.")

        h("Engine RPLs  (.rpl / .rpx)")
        p("The game's code. Three patches are offered, each located by searching for the code "
          "itself rather than a fixed address, so they work on any build. A stock backup is "
          "kept automatically the first time you patch a file.")

        h("Read this before you edit anything")
        p("A fastfile this tool saves has no valid signature -- signing needs a private key "
          "nobody outside the original developers has. An unmodified console REJECTS an "
          "unsigned fastfile, so your edit will not load, even if the edit itself is perfect. "
          "Apply the signature patch from the RPL tab to BOTH t6_cafef_rpl.rpl and "
          "t6mp_cafef_rpl.rpl first. This is the single most common reason an edit 'does "
          "nothing'.", "warn")
        p("Cold start the game after deploying. Textures and banks stay cached, so a quick "
          "reload can keep showing the old ones and make a good edit look broken.")
        p("Keep backups. The tool backs up RPLs automatically; for everything else, copy the "
          "original before you overwrite it.")
        p("Nothing here has been verified on real hardware by the tool itself. Its checks prove "
          "files are well formed and round-trip correctly -- not that the console accepts them.")

        h("Finding things")
        p("Find asset (Ctrl+F) searches every texture name and tells you which pak or fastfile "
          "holds it. If a texture appears in more than one pak it is flagged, because the game "
          "uses whichever loads first -- editing a later copy does nothing at all.")

        h("Replacing a lot of textures at once")
        p("Bulk replace takes a folder of images named the way the game names them -- which is "
          "what a PC texture mod is -- works out every pak and fastfile carrying each one, and "
          "rewrites them all in a single pass. Drag files or folders onto the window, or use the "
          "buttons. Nothing is written until you confirm, everything touched is backed up first, "
          "and anything it cannot match is reported while the rest carries on.")
        p("Images can be .png .dds .tga .bmp .jpg .gif .tif or .webp -- DDS included in full "
          "(DXT1-5, BC4/5/6H/7, DX10), because that is what PC mods ship. Whatever you supply is "
          "re-encoded into the format the zone declares for that entry, so pak entries keep their "
          "exact size and are resized to fit, while inline zone images may change resolution.")

        h("Credits")
        p("Built by ToeKnee -- https://github.com/tonytrawl")
        # ⚠ ATTRIBUTION REQUIRED, NOT DECORATION. Two OpenAssetTools files (GPL-3.0) ship inside
        # this exe and the fastfile walk cannot run without them, so naming them here and shipping
        # both licences is part of meeting that licence, not a courtesy.
        p("Fastfile support uses two files from OpenAssetTools by Laupetin "
          "(github.com/Laupetin/OpenAssetTools): the T6 asset structure header and the ZoneCode "
          "asset definitions. They describe the shape of every asset in a zone. Everything else, "
          "including all Wii U support, is my own work.")
        p("GPL-3.0, same as OpenAssetTools. Both licences ship with this program.")
        p("Not affiliated with, endorsed by, or supported by Activision or Treyarch. For use "
          "with content you already own.", "dim")
        txt.configure(state="disabled")
        # (the footer and the scrolling body are built ABOVE, before the text is filled -- see the
        # pack-order note there. ⛔ NOTHING may be packed into `win` after this point: re-packing
        # `row` here moves it to the END of the pack order and reproduces the original bug exactly.
        # W15 catches that -- verified by doing it: the buttons drop to a 1px allocation.)

    # ------------------------------------------------------------------ shared settings
    def _edit_folders(self):
        """Game folders is genuinely shared: all three name dictionaries read the same search
        path, and an install missing from it is why names and shadow warnings go quiet."""
        win = tk.Toplevel(self)
        win.title("Game folders")
        win.configure(bg=theme.BG)
        win.geometry("820x430")
        win.transient(self)
        tk.Label(win, bg=theme.BG, fg=theme.TEXT, justify="left", wraplength=780,
                 font=theme.FONT_UI,
                 text=("Folders searched for the paks and zones that name entries, supply "
                       "formats and dimensions, and reveal when an image you are editing also "
                       "exists in another pak.\n\n"
                       "Add the 'content' folder of your Black Ops 2 Wii U install -- the one "
                       "holding the .ipak and .ff files. Without it, names go missing AND a "
                       "replace can silently target a pak that something else overrides.\n\n"
                       "The folder a file was opened from is searched automatically."))\
            .pack(fill="x", padx=14, pady=(14, 8))
        lb = tk.Listbox(win, bg=theme.EDIT, fg=theme.TEXT, selectbackground=theme.ACCENT2,
                        relief="flat", highlightthickness=0, font=theme.FONT_MONO)
        lb.pack(fill="both", expand=True, padx=14, pady=6)

        def refill():
            lb.delete(0, "end")
            cfg = settings.load()
            for d in cfg['content_dirs']:
                lb.insert("end", "[configured]  " + d)
            for d in settings.search_dirs():
                if d not in cfg['content_dirs']:
                    lb.insert("end", "[auto]        " + d)
        refill()

        def add():
            d = filedialog.askdirectory(title="Select your Black Ops 2 'content' folder")
            if d:
                settings.add_content_dir(d)
                refill()
                self.say("Added %s" % d, theme.OK)

        def remove():
            sel = lb.curselection()
            if not sel:
                return
            row = lb.get(sel[0])
            if row.startswith("[configured]"):
                settings.remove_content_dir(row.split("  ", 1)[1].strip())
                refill()

        row = tk.Frame(win, bg=theme.BG)
        # pinned and reserved ahead of the expanding list, so the buttons survive a short window
        row.pack(side="bottom", fill="x", padx=14, pady=(0, 14), before=lb)
        ttk.Button(row, text="Add folder...", style="Accent.TButton",
                   command=add).pack(side="left")
        ttk.Button(row, text="Remove", style="Ghost.TButton",
                   command=remove).pack(side="left", padx=6)
        ttk.Button(row, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")

    def _on_close(self):
        for d in list(self.docs):
            if not d.workspace.close():
                return               # a tool refused (unsaved work); abort the whole shutdown
            self.docs.remove(d)
        self.destroy()


# ---------------------------------------------------------------------- selftest
def _find_samples(per_kind=1):
    """Real files per kind, from the user's own search path. Nothing hard-coded.

    Returns {kind: [path, ...]} with at most `per_kind` entries each -- W5 needs TWO of one
    kind to prove several documents of the same type can be open together.

    Depth-capped and first-hit-wins so the battery stays quick; a kind with no sample is a SKIP,
    which the report is careful never to count as a pass.
    """
    found = {}
    roots = []
    try:
        roots.extend(settings.search_dirs())
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    roots.extend([here, os.path.dirname(here)])

    want = {}
    for kind, exts, _m, _c, _l, _d in REGISTRY:
        for e in exts:
            want[e] = kind

    def done():
        return all(len(found.get(k, ())) >= per_kind for k, *_ in REGISTRY)

    for root in roots:
        if not os.path.isdir(root) or done():
            continue
        base = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(root):
            if dirpath.rstrip(os.sep).count(os.sep) - base >= 3:
                dirnames[:] = []
            for fn in sorted(files):
                kind = want.get(os.path.splitext(fn)[1].lower())
                if not kind:
                    continue
                bucket = found.setdefault(kind, [])
                p = os.path.join(dirpath, fn)
                if len(bucket) < per_kind and p not in bucket:
                    bucket.append(p)
            if done():
                break
    return found


def _cancel_after(w):
    """Cancel every pending after() callback before destroying a widget tree.

    ⚠ WHY THIS IS NEEDED, AND WHY IT WAS INVISIBLE. destroy() deletes the Tcl commands a widget
    owns but leaves that widget's TIMERS queued. They fire later against commands that no longer
    exist, and Tcl prints `invalid command name "..._drain"`. Every gate that builds a Studio has
    been leaking them all along; nothing serviced the timer queue, so nothing ever fired. W15 is
    the first gate to call a full update() -- update_idletasks() runs idle tasks only, NOT timers --
    and 21 of these errors appeared in the battery output at once. They do not change any verdict,
    and a verification log that a tester reads must not be full of Tcl errors either.

    ⛔ USE THE RAW TCL CALL, NOT Tkinter's after_cancel(). after_cancel also does
    deletecommand(script), which removes the Tcl command a LIVE widget still references; the widget
    then fails its own teardown with "can't delete Tcl command", and W4/W5/W9 all broke that way.
    Cancelling the timer is the whole job -- the command is not ours to delete.
    """
    try:
        for aid in w.tk.eval('after info').split():
            try:
                w.tk.call('after', 'cancel', aid)
            except Exception:
                pass
    except Exception:
        pass


def _run_selftest(quiet=False):
    """Gate the SHELL, not the tools -- they have their own batteries.

    What can actually break here and nothing else would catch: the three tools no longer being
    embeddable (the whole point of the consolidation), extension routing, and the theme failing
    to install. Each check is written so it can FAIL: a tool that is still a tk.Tk subclass, a
    routing table that does not cover its own registry, and a real widget build in a hidden root.
    """
    rows = []

    def add(gate, ok, detail):
        rows.append(('PASS' if ok else 'FAIL', gate, detail))

    # W1 every registered tool is an embeddable Workspace, not a window
    from core.workspace import Workspace
    for kind, _e, mod_name, cls_name, label, _d in REGISTRY:
        try:
            cls = _load_workspace_class(kind)
            ok = issubclass(cls, Workspace) and not issubclass(cls, tk.Tk)
            add('W1 %-5s' % kind, ok,
                '%s.%s is %s' % (mod_name, cls_name,
                                 'an embeddable Workspace' if ok else 'NOT embeddable'))
        except Exception as ex:
            add('W1 %-5s' % kind, False, 'import failed: %s: %s' % (type(ex).__name__, ex))

    # W2 routing covers every extension the registry claims, and rejects what it should
    routed = all(kind_for('x' + e) == k for k, exts, _m, _c, _l, _d in REGISTRY for e in exts)
    add('W2', routed and kind_for('x.txt') is None,
        'every registered extension routes to its own kind; an unknown one routes to None')

    # W3 the shell actually builds, with a document hosted in it
    try:
        root = Studio()
        root.withdraw()
        theme.install(root)
        built = root.winfo_exists() and root.welcome is not None
        add('W3', bool(built), 'shell builds with the welcome screen up')
        _cancel_after(root); root.destroy()
    except Exception as ex:
        add('W3', False, '%s: %s' % (type(ex).__name__, ex))

    # W4 a REAL file of each kind actually opens INSIDE the shell and gets packed.
    # W1-W3 only prove the classes are the right shape and the chrome builds; the risk this
    # refactor actually carries is a tool that breaks when it is a frame instead of a window
    # (a self.title/protocol/geometry call landing somewhere fatal). Only opening real files
    # catches that. A kind with no sample on this machine is reported SKIP, never a pass.
    samples = _find_samples()
    try:
        shell = Studio()
        shell.withdraw()
        for kind, _e, _m, _c, label, _d in REGISTRY:
            path = (samples.get(kind) or [None])[0]
            if not path:
                rows.append(('SKIP', 'W4 %-5s' % kind,
                             'no %s file on this machine to test with' % label.lower()))
                continue
            try:
                shell.open_file(path)
                doc = next((d for d in shell.docs
                            if d.path and os.path.abspath(d.path) == os.path.abspath(path)),
                           None)
                ok = (doc is not None and doc.workspace.winfo_exists()
                      and doc.workspace.winfo_manager() == 'pack')
                add('W4 %-5s' % kind, ok,
                    '%s hosted and packed%s' % (os.path.basename(path),
                                                '' if ok else ' -- FAILED')
                    if doc else 'open_file produced no document for %s'
                    % os.path.basename(path))
            except Exception as ex:
                add('W4 %-5s' % kind, False,
                    '%s: %s: %s' % (os.path.basename(path), type(ex).__name__, ex))
        _cancel_after(shell); shell.destroy()
    except Exception as ex:
        add('W4', False, 'shell could not host documents: %s: %s' % (type(ex).__name__, ex))

    # W5 SEVERAL DOCUMENTS AT ONCE -- two of the SAME kind plus one of another. W4 only ever
    # proved one document at a time, so nothing until now exercised the case the tabs exist
    # for. It is also what surfaced the bind_all defect: three hosted instances all answered a
    # single Ctrl+S, because bind_all binds application-wide rather than per document.
    multi = _find_samples(per_kind=2)
    pair_kind = next((k for k, v in multi.items() if len(v) >= 2), None)
    other_kind = next((k for k, v in multi.items() if k != pair_kind and v), None)
    if pair_kind is None:
        rows.append(('SKIP', 'W5', 'need two files of one kind on this machine to test with'))
    else:
        try:
            shell = Studio()
            shell.withdraw()
            want = list(multi[pair_kind][:2])
            if other_kind:
                want.append(multi[other_kind][0])
            for p in want:
                shell.open_file(p)
            all_open = len(shell.docs) == len(want)
            mapped = [d for d in shell.docs if d.workspace.winfo_manager() == 'pack']
            one_shown = len(mapped) == 1 and mapped[0] is shell.active
            shell._activate(shell.docs[0])          # switching back must re-pack the first
            switched = (shell.active is shell.docs[0]
                        and shell.docs[0].workspace.winfo_manager() == 'pack'
                        and sum(1 for d in shell.docs
                                if d.workspace.winfo_manager() == 'pack') == 1)
            add('W5', all_open and one_shown and switched,
                '%d documents open together (%s x2%s); exactly one visible; switching works'
                % (len(shell.docs), pair_kind,
                   ' + ' + other_kind if other_kind else '')
                if (all_open and one_shown and switched)
                else 'open=%d/%d visible=%d switched=%s'
                     % (len(shell.docs), len(want), len(mapped), switched))
            _cancel_after(shell); shell.destroy()
        except Exception as ex:
            add('W5', False, '%s: %s' % (type(ex).__name__, ex))

    # W6 folder discovery must NOT advertise the development checkout. It used to climb to the
    # parent directory, which matched this repo root because it happens to hold .ipak files and
    # zone subfolders -- a path that will never exist on an end user's machine.
    try:
        disc = [os.path.abspath(d) for d in settings.discovered_dirs()]
        checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        leaked = [d for d in disc if d == checkout]
        add('W6', not leaked,
            'discovery offers %d folder(s), none of them the source checkout' % len(disc)
            if not leaked else 'LEAKED the checkout root: %s' % leaked)
    except Exception as ex:
        add('W6', False, '%s: %s' % (type(ex).__name__, ex))

    # W7 the author credit is actually present, carries the real URL, and is clickable. A label
    # that silently lost its binding still LOOKS right, so check the binding, not the pixels.
    try:
        shell = Studio()
        shell.withdraw()
        txt = shell.credit.cget('text')
        clickable = bool(shell.credit.bind('<Button-1>'))
        ok = (txt == AUTHOR_NOTE) and (AUTHOR_URL in txt) and clickable
        add('W7', ok,
            'status bar credits %s, label is clickable' % AUTHOR_URL if ok
            else 'text=%r clickable=%s' % (txt, clickable))
        _cancel_after(shell); shell.destroy()
    except Exception as ex:
        add('W7', False, '%s: %s' % (type(ex).__name__, ex))

    # W8 asset search: a name fragment must return real textures AND name the pak holding each.
    # Written so it can fail: it takes a texture name straight out of the ownership index (so a
    # hit is guaranteed to exist), searches for a FRAGMENT of it, and requires both that the
    # texture comes back and that at least one hit reports a pak. A search that finds names but
    # cannot say where they live would pass a weaker check and be useless.
    try:
        from core import ipak_search as isearch
        from core import ipak_names as NM
        owners, scanned = isearch.owner_map()
        idx = NM.load_shared()
        # Pick a name that the ownership index definitely covers.
        owned_hashes = {k[0] for k in owners}
        sample = next(((nh, (v[0] if isinstance(v, (tuple, list)) else v))
                       for nh, v in (idx.by_name_hash or {}).items()
                       if nh in owned_hashes and v), (None, None))
        if not sample[1]:
            rows.append(('SKIP', 'W8', 'no named texture on this machine is covered by the '
                                       'pak index, so search cannot be exercised'))
        else:
            nh, name = sample
            frag = name[2:12] if len(name) > 12 else name
            hits = isearch.search(frag, index=idx, owners=owners)
            got = next((h for h in hits if h['name_hash'] == nh), None)
            located = sum(1 for h in hits for p in h['parts'] if p['paks'])
            nonsense = isearch.search('zzz_no_such_texture_zzz', index=idx, owners=owners)
            add('W8', bool(got) and located > 0 and not nonsense,
                "'%s' -> %d texture(s), %d part(s) located in %d indexed pak(s); a nonsense "
                "query returns nothing" % (frag, len(hits), located, len(scanned))
                if (got and located and not nonsense)
                else 'found=%s located=%d nonsense=%d' % (bool(got), located, len(nonsense)))
    except Exception as ex:
        add('W8', False, '%s: %s' % (type(ex).__name__, ex))

    # W9 the window icon must be FOUND and applied as the APPLICATION DEFAULT, or secondary
    # windows show Tk's feather. Two things can fail here and both have: the .ico not being
    # bundled into the frozen build (same class as the name caches shipping missing), and
    # iconbitmap being called without `default=` so only the main window got it.
    try:
        ipath = theme.icon_file()
        root = Studio()
        root.withdraw()
        applied = theme.apply_icon(root)
        child = tk.Toplevel(root)          # a dialog must not raise on the inherited default
        _cancel_after(child); child.destroy()
        _cancel_after(root); root.destroy()
        add('W9', bool(ipath) and applied,
            'icon %s applied as the application default'
            % (os.path.basename(ipath) if ipath else '(none found)')
            if (ipath and applied) else
            'icon_file=%r applied=%s -- secondary windows would show the default feather'
            % (ipath, applied))
    except Exception as ex:
        add('W9', False, '%s: %s' % (type(ex).__name__, ex))

    # W10 zone indexing must be STOPPABLE and must never persist a cancelled sweep. Caching a
    # partial run under the full zone-set stamp would read as complete on every later load and
    # silently hide everything that was not scanned -- worse than not indexing at all.
    try:
        from core import ipak_search as isearch2
        from core import paths as _p
        r, w = _p.cache_file('_zone_images.cache')
        existed = {p: os.path.exists(p) for p in {r, w}}
        # ⚠ rebuild=True IS REQUIRED FOR THIS GATE TO TEST ANYTHING. Without it, a valid cache
        # short-circuits the function and it returns the cached zone list without ever entering
        # the cancellable loop -- which the gate then read as "a cancelled run scanned 198
        # zones" and failed. The gate was conflating zones RETURNED with zones SCANNED, and it
        # only showed up once a real index existed. Forcing the rebuild path makes the stop
        # flag the thing under test.
        imgs, done = isearch2.zone_image_map(should_stop=lambda: True, rebuild=True)
        wrote_new = any(os.path.exists(p) and not existed[p] for p in {r, w})
        add('W10', (not done) and not wrote_new,
            'a cancelled sweep stops immediately (%d zones) and writes no cache'
            % len(done) if (not done and not wrote_new) else
            'zones=%d wrote_cache=%s -- a cancelled run must do neither'
            % (len(done), wrote_new))
    except Exception as ex:
        add('W10', False, '%s: %s' % (type(ex).__name__, ex))

    # W11 the RPL patch engine: patches must be LOCATED on a real engine RPL, and the stock
    # backup must be WRITE-ONCE. The backup rule is the one with teeth -- patching is
    # incremental, so a backup that rewrote itself would replace the stock copy with an
    # already-patched one on the second run and the original would be gone for good.
    try:
        from core import rpl_patch as RPP
        # ⚠ PICK AN RPL THAT IS ACTUALLY A T6 ENGINE RPL. Any .rpl can be first in the search
        # path, and a non-engine one legitimately locates nothing -- which the frozen run hit
        # (located=[]) while the source run passed, because their search paths differ. A gate
        # whose subject is chosen by directory order tests whatever it happens to find.
        cands = _find_samples(per_kind=6).get('rpl') or []
        rpl_path, found = None, {}
        for c in cands:
            try:
                _r, f = RPP.survey(c)
            except Exception:
                continue
            if any(i['ok'] for i in f.values()):
                rpl_path, found = c, f
                break
        if not rpl_path:
            rows.append(('SKIP', 'W11', 'no T6 engine RPL among %d .rpl file(s) here'
                         % len(cands)))
        else:
            located = [n for n, i in found.items() if i['ok']]
            import shutil
            import tempfile
            tmp = tempfile.mkdtemp(prefix='rplgate_')
            try:
                work = os.path.join(tmp, os.path.basename(rpl_path))
                shutil.copy2(rpl_path, work)
                bp1, made1 = RPP.ensure_backup(work)
                with open(work, 'ab') as f:          # pretend a patch changed the file
                    f.write(b'\x00')
                bp2, made2 = RPP.ensure_backup(work)
                same = os.path.getsize(bp1) == os.path.getsize(rpl_path)
                add('W11', bool(located) and made1 and not made2 and same,
                    '%s: located %s; backup is write-once (created=%s, second call=%s, '
                    'still stock size)' % (os.path.basename(rpl_path), ','.join(located),
                                           made1, made2)
                    if (located and made1 and not made2 and same) else
                    'located=%s made1=%s made2=%s stock_size_kept=%s'
                    % (located, made1, made2, same))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as ex:
        add('W11', False, '%s: %s' % (type(ex).__name__, ex))

    # W12 bulk replace is the only feature that writes to MANY game files from one click, so it
    # gets the strictest gate: it must resolve a real texture to a real file, must actually change
    # the bytes of a COPY, must leave a write-once backup -- and an unmatchable name must be
    # REPORTED rather than aborting the batch, because a bulk run that dies halfway leaves the
    # user unable to tell what landed.
    try:
        import shutil
        import tempfile
        from core import bulk_replace as BRG
        from core import ipak_search as ISG
        zi, _zl = ISG.zone_image_map(build=False)
        # Deliberately the SMALLEST reachable zone, not the first: this gate decrypts, edits and
        # re-saves whatever it picks, and a gate that takes minutes is a gate that gets skipped.
        cand = {}
        for _nh, recs in (zi or {}).items():
            r = ISG._zrec(recs[0])
            if not r[6]:
                continue
            full = ISG.resolve_zone(r[0])
            if full and r[0] not in cand:
                try:
                    cand[r[0]] = (os.path.getsize(full), r[6], full)
                except OSError:
                    pass
        pick = None
        if cand:
            _sz, nm, full = min(cand.values())
            pick = (nm, full)
        if not pick:
            rows.append(('SKIP', 'W12', 'no named inline zone image reachable on this machine'))
        else:
            name, zpath = pick
            tmp = tempfile.mkdtemp(prefix='bulkgate_')
            try:
                os.makedirs(os.path.join(tmp, 'content'), exist_ok=True)
                work = os.path.join(tmp, 'content', os.path.basename(zpath))
                shutil.copy2(zpath, work)
                before = open(work, 'rb').read()
                from PIL import Image as _Im
                srcs = []
                for nm, colour in ((name, (255, 0, 255, 255)),
                                   ('zzz_no_such_texture_gate', (0, 0, 0, 255))):
                    sp = os.path.join(tmp, nm.lstrip('~-$') + '.dds')
                    _Im.new('RGBA', (64, 64), colour).save(sp, format='DDS',
                                                           pixel_format='DXT5')
                    srcs.append(sp)
                items = BRG.plan(srcs, dirs=[os.path.join(tmp, 'content')])
                ok = [i for i in items if i['status'] == 'ok']
                bad = [i for i in items if i['status'] == 'unmatched']
                res = BRG.apply(items)
                after = open(work, 'rb').read()
                changed = before != after
                backed = os.path.exists(work + '.orig')
                good = (len(ok) == 1 and len(bad) == 1 and changed and backed
                        and res['replaced'] >= 1 and not res['failed'])
                add('W12', good,
                    ('%s -> %s: %d replaced, bytes changed, .orig kept; 1 unmatched name '
                     'reported and the run continued'
                     % (name, os.path.basename(zpath), res['replaced'])) if good else
                    'matched=%d unmatched=%d changed=%s backup=%s replaced=%d failed=%s'
                    % (len(ok), len(bad), changed, backed, res['replaced'], res['failed']))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as ex:
        add('W12', False, '%s: %s' % (type(ex).__name__, ex))

    # W13 a duplicated module must be a RE-EXPORT, never a second implementation.
    # ⚠ WHY THIS IS A BUILD GATE AND NOT JUST A PIPELINE SCRIPT. stage1_roundtrip.py shipped a
    # container-parsing defect that corrupted every zone it round-tripped, and the model it got
    # wrong was ALREADY CORRECT in wiiu_ref/wiiu_zone.py. Three copies of that file existed and
    # two had silently drifted before anyone looked. This studio BUNDLES native_linker, so a
    # divergent copy is something we can ship. native_linker/canonical_gate.py owns the check and
    # still runs standalone (exit 0/1) for lanes that do not want the app.
    # ⚠ IT CANNOT RUN FROM A PACKAGED BINARY, AND SAYS SO RATHER THAN PASSING. The check reads the
    # SOURCE TREE; inside a PyInstaller bundle __file__ points into the _MEIxxxx temp dir and there
    # is no tree to inspect. The load-bearing run is therefore the pre-package source battery --
    # which is exactly when a divergent copy would still be caught before it ships.
    try:
        if getattr(sys, 'frozen', False):
            rows.append(('SKIP', 'W13', 'canonical-copy check needs the source tree; not present '
                                        'in a packaged build (it ran in the pre-package battery)'))
        else:
            import canonical_gate as CG
            cg_rows, cg_failed = CG.check()
            detail = '%d guarded copy check(s), %d failed' % (len(cg_rows), cg_failed)
            if cg_failed:
                detail += ' | ' + '; '.join(d for s, _n, d in cg_rows if s == 'FAIL')
            else:
                detail += ' (no non-canonical copy defines its own code)'
            add('W13', cg_failed == 0, detail)
    except Exception as ex:
        add('W13', False, '%s: %s' % (type(ex).__name__, ex))

    # W14 the relocation model that decides where pointers move after a length-changing save.
    # ⚠ WHY THIS IS A SHELL GATE. Saving a grown asset rewrites pointers, and the rule for "did this
    # target move" used to be `b5 + 64` compared against a FILE range -- measured wrong at 483 of 483
    # content-confirmed targets on a real zone, producing zones that open fine, pass every check and
    # then die in game with no error. The model is now native_linker/reloc_model.py and it carries
    # its own unit battery; running it here means a packaged build cannot ship with that model broken
    # or missing from the bundle (it needs --hidden-import reloc_model). UNLIKE W13 this one DOES run
    # frozen: it is pure arithmetic over in-memory values and reads no source tree.
    try:
        import io as _io
        import contextlib as _cl
        import reloc_model as _RM
        _buf = _io.StringIO()
        with _cl.redirect_stdout(_buf):
            _rc = _RM.selftest()
        _last = [l for l in _buf.getvalue().splitlines() if 'passed' in l]
        add('W14', _rc == 0,
            (_last[-1].strip() if _last else 'selftest returned %d' % _rc)
            + ' -- block-5 relocation model (boundary semantics, interior refusal, interval remap, '
              'b5-space registered set)')
    except Exception as ex:
        add('W14', False, '%s: %s' % (type(ex).__name__, ex))

    # W15 a dialog's action buttons must be REACHABLE at the smallest size the dialog allows.
    # ⚠ THIS IS A BEHAVIOURAL CHECK, AND IT HAD TO BE. The bug it guards was reported from use:
    # the About window opened too short to show "Open GitHub" and "Close", and they only appeared
    # if you resized the window. Cause: Tk's packer allocates space in PACK ORDER, so a footer
    # packed after an expand=True body is the first thing starved -- and NO amount of reading the
    # side= arguments reveals it, because side="bottom" alone does not help. Static analysis of
    # pack order also cannot settle it (it cannot tell two different frames sharing a variable
    # name apart, which produced a false positive on the welcome screen). So this measures the
    # rendered geometry instead: shrink to the minimum and ask whether the button is inside it.
    try:
        app = Studio()
        app.withdraw()
        theme.install(app)
        app.show_about()
        dlg = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        mw, mh = dlg.minsize()
        dlg.geometry('%dx%d' % (mw, mh))
        # ⚠ THE DIALOG MUST BE MAPPED FOR ITS GEOMETRY TO BE REAL, and TWO things stopped it:
        # a Toplevel under a withdrawn master reports 1x1 with ismapped()==False, and show_about
        # makes it TRANSIENT, so the window manager keeps a transient withdrawn whenever its master
        # is. Both had to go or this gate measures nothing and fails a correct layout (it did,
        # twice). Detach the transient relationship and map it fully transparent, so the geometry
        # manager runs for real without a window flashing through the battery.
        try:
            dlg.transient('')               # stop tracking the withdrawn master
        except tk.TclError:
            pass
        for w in (app, dlg):
            try:
                w.attributes('-alpha', 0.0)
            except tk.TclError:
                pass                        # platform without alpha: it will flash briefly
        dlg.deiconify()
        dlg.update()

        def _btns(w, out):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button):
                    out.append(c)
                _btns(c, out)
            return out

        found = _btns(dlg, [])
        h = dlg.winfo_height()
        bottoms = [(b.winfo_rooty() - dlg.winfo_rooty() + b.winfo_height()) for b in found]
        clipped = [b for b, bot in zip(found, bottoms) if bot > h or not b.winfo_ismapped()]
        add('W15', bool(found) and not clipped,
            '%d action button(s) in About; window %dx%d at its minimum, lowest button bottom edge '
            'at y=%d, %d outside' % (len(found), dlg.winfo_width(), h,
                                     max(bottoms) if bottoms else -1, len(clipped)))
        _cancel_after(app); app.destroy()
    except Exception as ex:
        add('W15', False, '%s: %s' % (type(ex).__name__, ex))

    failed = sum(1 for s, _g, _d in rows if s == 'FAIL')
    text = '%s %s shell gate battery\nfrozen: %s\n\n' % (
        APP_TITLE, APP_VERSION, bool(getattr(sys, 'frozen', False)))
    for s, g, d in rows:
        text += '  %-6s %-10s %s\n' % (s, g, d)
    text += '\n  %d passed, %d failed\n' % (len(rows) - failed, failed)

    try:
        log = os.path.join(os.path.dirname(os.path.abspath(sys.executable
                                                           if getattr(sys, 'frozen', False)
                                                           else __file__)),
                           'studio_selftest.log')
        with open(log, 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        log = '(could not write a log file)'
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass
    if not quiet:
        try:
            r = tk.Tk()
            r.withdraw()
            (messagebox.showinfo if not failed else messagebox.showerror)(
                APP_TITLE, '%s\n\nWritten to:\n%s' % (text.strip()[-1500:], log))
            r.destroy()
        except Exception:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    _args = sys.argv[1:]
    if '--selftest' in _args:
        sys.exit(_run_selftest(quiet='--quiet' in _args))
    _args = [a for a in _args if not a.startswith('--')]
    Studio(_args[0] if _args else None).mainloop()
