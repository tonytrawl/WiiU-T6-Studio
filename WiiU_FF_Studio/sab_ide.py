"""Wii U Sound Bank Editor -- browse, play, extract and edit T6 .sabs / .sabl banks.

Same shape as ff_ide.py and ipak_ide.py: list on the left, preview on the right, one toolbar.

    python sab_ide.py [bank.sabs]

Panes
  left    every entry: id, format, channels, duration, stored size, checksum state
  right   the decoded waveform, transport controls and the entry's metadata
  bottom  status + bank summary

Playback uses `winsound` from the standard library on an in-memory WAV, so there is no audio
dependency to install. Playback is asynchronous; Stop cancels it.

Entries have NO NAMES
  A bank identifies an entry only by `id` = SND_HashName(alias). Unlike an .ipak, no metadata
  section carries names, and the alias strings are not in the zones either. Entries are listed
  by id; File > Load names... attaches an optional JSON {id: name} dictionary if you have one.

Editing
  Replace  keeps the entry's id, so aliases that already reference it still bind.
  Add      mints a new entry; nothing references it until an alias is pointed at its id.
  Delete   removes it entirely.
  Save     rebuilds the container. Untouched banks rebuild BYTE-EXACT (verified on 136 banks,
           12,667 entries), and untouched entries keep their original checksum verbatim.
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import numpy as np                                     # noqa: E402
from PIL import Image, ImageTk                         # noqa: E402

from core import sab as S                              # noqa: E402
from core.workspace import Workspace, run_standalone   # noqa: E402

APP_TITLE = "Wii U Sound Bank Editor"
APP_VERSION = "1.0"

# Kept in step with core/theme.py -- see the note there.
BG, PANEL, EDIT = "#0f151d", "#171f2a", "#0d1219"
HEAD = "#111823"
ACCENT, ACCENT2 = "#18b4a8", "#0e8f86"
TEXT, MUTED, BORDER = "#eef3f8", "#a8b4c4", "#2c3849"
OK, WARN, INFO = "#46cf88", "#f0736e", "#e8b552"

PREVIEW_SECONDS = 30        # decode bound for the waveform; export always decodes in full
WAVE_W, WAVE_H = 900, 260


class SabIDE(Workspace):
    """The sound bank editor. A Workspace, so it runs hosted in the studio shell or alone."""

    kind = 'sab'

    def __init__(self, master=None, initial=None):
        super().__init__(master)
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.configure(bg=BG)

        self.bank = None
        self.path = None
        self.cur = None
        self._pcm = None
        self._rate = 0
        self._playing = False
        self._wav_path = None     # temp WAV backing the current async play
        self._play_after = None   # pending 'playback finished' timer
        self._q = queue.Queue()
        self._busy = False
        self._filter = tk.StringVar()
        self._sort = {}                  # click-to-sort state, see core.uiutil
        self._filter.trace_add("write", lambda *_: self._refill())

        self._style()
        self._toolbar()
        self._body()
        self._statusbar()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain)
        if initial:
            self.after(120, lambda: self._open(initial))

    # ------------------------------------------------------------------ chrome
    def _style(self):
        # ⚠ HOSTED: DO NOT TOUCH THE SHARED STYLE DATABASE. ttk styles are per-interpreter, so
        # every name set here (and theme_use itself) applies to EVERY tab already on screen --
        # the last tab constructed silently restyled the others. core.theme owns the look in the
        # shell; this runs only when the tool owns its own window.
        if not self.owns_global_styles():
            return
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Ghost.TButton", background=PANEL, foreground=TEXT, borderwidth=0,
                     focuscolor=PANEL, padding=(10, 5))
        st.map("Ghost.TButton", background=[("active", BORDER)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#04110f", borderwidth=0,
                     focuscolor=ACCENT, padding=(10, 5))
        st.map("Accent.TButton", background=[("active", ACCENT2)])
        st.configure("Tree.Treeview", background=EDIT, fieldbackground=EDIT, foreground=TEXT,
                     borderwidth=0, rowheight=22)
        st.map("Tree.Treeview", background=[("selected", ACCENT2)],
               foreground=[("selected", "#04110f")])
        st.configure("Tree.Treeview.Heading", background=HEAD, foreground=MUTED,
                     borderwidth=0, padding=(6, 4))
        st.configure("FF.Horizontal.TProgressbar", background=ACCENT, troughcolor=HEAD,
                     borderwidth=0)

    def _toolbar(self):
        bar = tk.Frame(self, bg=PANEL, height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ttk.Button(bar, text="Open", style="Ghost.TButton", command=self._open)\
            .pack(side="left", padx=(10, 4), pady=7)
        ttk.Button(bar, text="Save", style="Accent.TButton", command=self._save)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Save As", style="Ghost.TButton",
                   command=lambda: self._save(save_as=True)).pack(side="left", padx=4, pady=7)
        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8, padx=8)
        ttk.Button(bar, text="Extract", style="Ghost.TButton", command=self._extract)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Extract all", style="Ghost.TButton", command=self._extract_all)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Replace", style="Ghost.TButton", command=self._replace)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Add", style="Ghost.TButton", command=self._add)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Delete", style="Ghost.TButton", command=self._delete)\
            .pack(side="left", padx=4, pady=7)
        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8, padx=8)
        ttk.Button(bar, text="Load names", style="Ghost.TButton", command=self._load_names)\
            .pack(side="left", padx=4, pady=7)
        # `_rebuild_names` existed with no way to reach it, and there was no way at all to point
        # the tool at a game install. Measured consequence: the shipped dictionary named 95 of
        # 948 entries (10%) across 44 banks -- 38 banks at exactly 0% -- while the banks whose
        # zones happened to be discoverable scored 100%. The names were never missing because
        # the pairing was wrong; they were missing because nothing could widen the search.
        ttk.Button(bar, text="Rebuild names", style="Ghost.TButton", command=self._rebuild_names)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Game folders", style="Ghost.TButton", command=self._edit_folders)\
            .pack(side="left", padx=4, pady=7)
        tk.Label(bar, text="filter", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))\
            .pack(side="left", padx=(12, 4))
        ttk.Entry(bar, textvariable=self._filter, width=20).pack(side="left", pady=7)

    def _body(self):
        pan = tk.PanedWindow(self, orient="horizontal", bg=BORDER, sashwidth=4, bd=0)
        pan.pack(fill="both", expand=True)

        left = tk.Frame(pan, bg=PANEL)
        self.count_lbl = tk.Label(left, text="  entries", bg=PANEL, fg=MUTED,
                                  font=("Segoe UI", 9), anchor="w")
        self.count_lbl.pack(fill="x", pady=(8, 4))
        tw = tk.Frame(left, bg=BORDER)
        tw.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        cols = ("fmt", "ch", "dur", "size", "state")
        self.tree = ttk.Treeview(tw, style="Tree.Treeview", columns=cols,
                                 displaycolumns=cols, show="tree headings")
        for c, t, w, anc in (("fmt", "format", 86, "w"), ("ch", "ch", 34, "center"),
                             ("dur", "length", 66, "e"), ("size", "bytes", 82, "e"),
                             ("state", "md5", 48, "center")):
            self.tree.column(c, width=w, anchor=anc, stretch=False)
        # Click-to-sort, ascending then descending. Keys read the entry, not the rendered
        # cell: "length" is shown as "1.20s" and "bytes" with separators, and sorting either
        # as text gives the wrong order.
        from core import uiutil
        uiutil.attach_sorting(self.tree, {
            '#0': ('entry', lambda e: (self.bank.name_of(e) or '').lower()),
            'fmt': ('format', lambda e: (e.format_name or '').lower()),
            'ch': ('ch', lambda e: e.channels),
            'dur': ('length', lambda e: e.duration),
            'size': ('bytes', lambda e: e.size),
            'state': ('md5', lambda e: str(self.bank.verify_checksum(e))),
        }, self._refill, self._sort)
        self.tree.column("#0", width=190, stretch=True)
        sb = ttk.Scrollbar(tw, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        self.tree.bind("<Double-1>", lambda e: self._play())
        pan.add(left, minsize=340, width=560)

        right = tk.Frame(pan, bg=EDIT)
        self.path_lbl = tk.Label(right, text="open a .sabs or .sabl to begin", bg=EDIT,
                                 fg=MUTED, anchor="w", font=("Consolas", 9), justify="left")
        self.path_lbl.pack(fill="x", padx=8, pady=(8, 2))

        tbar = tk.Frame(right, bg=EDIT)
        tbar.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Button(tbar, text="  Play  ", style="Accent.TButton", command=self._play)\
            .pack(side="left")
        ttk.Button(tbar, text="Stop", style="Ghost.TButton", command=self._stop)\
            .pack(side="left", padx=6)
        self.play_lbl = tk.Label(tbar, text="", bg=EDIT, fg=MUTED, font=("Segoe UI", 9))
        self.play_lbl.pack(side="left", padx=10)

        self.canvas = tk.Canvas(right, bg=EDIT, highlightthickness=0, bd=0, height=WAVE_H)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.canvas.bind("<Configure>", lambda e: self._draw_wave())

        self.info = tk.Text(right, bg=EDIT, fg=TEXT, relief="flat", bd=0, height=11,
                            font=("Consolas", 9), padx=8, highlightthickness=0)
        self.info.pack(fill="x", padx=8, pady=(0, 8))
        self.info.config(state="disabled")
        pan.add(right)

    def _statusbar(self):
        bar = tk.Frame(self, bg=HEAD, height=26)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="Open a sound bank to begin", bg=HEAD, fg=MUTED,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=10)
        self.summary = tk.Label(bar, text="", bg=HEAD, fg=MUTED, font=("Consolas", 9))
        self.summary.pack(side="right", padx=10)
        self.pbar = ttk.Progressbar(bar, style="FF.Horizontal.TProgressbar", length=170,
                                    mode="indeterminate")

    # ------------------------------------------------------------------ open / list
    def _open(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title="Open sound bank",
                filetypes=[("T6 sound banks", "*.sabs *.sabl"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.bank = S.SoundBank.open(path)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not open %s:\n\n%s: %s"
                                 % (os.path.basename(path), type(ex).__name__, ex))
            return
        self.path = path
        self.title("%s -- %s" % (APP_TITLE, os.path.basename(path)))
        # Remember where this came from: a bank sitting beside its own zones then resolves with
        # no configuration at all, which is the common case for an extracted or project folder.
        try:
            from core import settings as _settings
            _settings.note_opened(path)
        except Exception:
            pass
        # Optional enrichment: names harvested from the fastfiles that reference this bank.
        # A no-op when no .ff is reachable -- entries then keep their hex ids.
        try:
            named, total = self.bank.attach_shared_names()
        except Exception:
            named, total = 0, len(self.bank.entries)
        deps = [d for d in self.bank.dependencies if d]
        self.summary.config(text="v%d  %d entries  %s  deps: %s"
                                 % (self.bank.version, len(self.bank.entries),
                                    _human(len(self.bank.raw)), ", ".join(deps[:2]) or "none"))
        self._refill()
        self.status.config(text="Opened %s -- %d/%d entries named from the fastfiles"
                                % (os.path.basename(path), named, total), fg=MUTED)

    def _refill(self):
        if not self.bank:
            return
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.node_entry = {}
        f = self._filter.get().strip().lower()
        shown = 0
        from core import uiutil
        for e in uiutil.sort_rows(self.bank.entries, self._sort):
            name = self.bank.name_of(e)
            if f and f not in name.lower() and f not in ("%08x" % e.id):
                continue
            ck = self.bank.verify_checksum(e)
            state = "ok" if ck else ("--" if ck is None else "!=")
            iid = self.tree.insert("", "end", text=name,
                                   values=(e.format_name, e.channels,
                                           "%.2fs" % e.duration, format(e.size, ","), state))
            self.node_entry[iid] = e
            shown += 1
        self.count_lbl.config(text="  %s of %s entries"
                                  % (format(shown, ","), format(len(self.bank.entries), ",")))

    # ------------------------------------------------------------------ preview
    def _on_pick(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        e = self.node_entry.get(sel[0])
        if e is None:
            return
        self.cur = e
        self.path_lbl.config(text="%s   %s   %d ch   %.2f s   %s bytes @ 0x%X"
                                  % (self.bank.name_of(e), e.format_name, e.channels,
                                     e.duration, format(e.size, ","), e.offset))
        self._pcm = None
        try:
            self._pcm, self._rate = self.bank.decode(e, seconds=PREVIEW_SECONDS)
        except Exception as ex:
            self._pcm, self._rate = None, 0
            self.status.config(text="decode failed: %s: %s" % (type(ex).__name__, ex), fg=WARN)
        self._draw_wave()
        self._show_info(e)

    def _show_info(self, e):
        ck = self.bank.verify_checksum(e)
        lines = [
            "id              0x%08X   (SND_HashName of the alias; names are not stored in the "
            "bank)" % e.id,
            "format          %s (%d)" % (e.format_name, e.fmt),
            "channels        %d%s" % (e.channels, "   looping" if e.looping else ""),
            "frameCount      %s  at %d Hz  ->  %.3f s"
            % (format(e.frames, ","), e.nominal_rate, e.duration),
            "stored audio    %d Hz  (the console keeps 2/3 of the nominal rate)" % e.stored_rate,
            "size / offset   %s bytes @ 0x%X" % (format(e.size, ","), e.offset),
            "checksum        %s" % ("matches the blob" if ck else
                                    ("no stored checksum (new entry)" if ck is None else
                                     "does NOT match -- a retail anomaly (3.0% of entries); "
                                     "preserved verbatim on save")),
        ]
        if e.note:
            lines.append("edit            %s (not written until you Save)" % e.note)
        if self._pcm is not None and len(self._pcm):
            peak = float(np.abs(self._pcm).max()) / 32768.0
            lines.append("decoded         %s samples, peak %.1f%%%s"
                         % (format(len(self._pcm), ","), peak * 100,
                            "   (first %ds shown)" % PREVIEW_SECONDS
                            if e.duration > PREVIEW_SECONDS else ""))
        self.info.config(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", "\n".join(lines))
        self.info.config(state="disabled")

    def _draw_wave(self):
        self.canvas.delete("all")
        W = max(64, self.canvas.winfo_width())
        H = max(64, self.canvas.winfo_height())
        if self._pcm is None or not len(self._pcm):
            self.canvas.create_text(W // 2, H // 2, fill=MUTED, font=("Segoe UI", 10),
                                    text="select an entry to see its waveform")
            return
        pcm = self._pcm
        nch = pcm.shape[1]
        img = Image.new("RGB", (W, H), (12, 17, 25))
        px = img.load()
        lane = H // nch
        # min/max envelope per column -- the honest way to draw audio at screen resolution
        for ch in range(nch):
            data = pcm[:, ch].astype(np.float32) / 32768.0
            n = len(data)
            idx = np.linspace(0, n, W + 1).astype(np.int64)
            mid = lane * ch + lane // 2
            half = max(2, lane // 2 - 6)
            for x in range(W):
                a, b = idx[x], max(idx[x] + 1, idx[x + 1])
                seg = data[a:b]
                if not len(seg):
                    continue
                lo = int(mid - float(seg.max()) * half)
                hi = int(mid - float(seg.min()) * half)
                if lo > hi:
                    lo, hi = hi, lo
                for y in range(max(0, lo), min(H - 1, hi) + 1):
                    px[x, y] = (24, 180, 168)
            for x in range(0, W, 2):
                if 0 <= mid < H:
                    px[x, mid] = (60, 78, 100)
        self._wave_photo = ImageTk.PhotoImage(img)
        self.canvas.create_image(W // 2, H // 2, image=self._wave_photo)
        dur = len(pcm) / float(self._rate or 1)
        self.canvas.create_text(8, 10, anchor="nw", fill=MUTED, font=("Consolas", 8),
                                text="%.2fs  %d Hz  %dch" % (dur, self._rate, nch))

    # ------------------------------------------------------------------ playback
    def _play(self):
        if not self.cur or self._pcm is None:
            return
        try:
            import winsound
        except ImportError:
            messagebox.showinfo(APP_TITLE, "Audio playback needs winsound (Windows only). "
                                           "Use Extract to save a WAV instead.")
            return
        try:
            wav = self.bank.to_wav(self.cur, seconds=PREVIEW_SECONDS)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not decode: %s: %s" % (type(ex).__name__, ex))
            return
        self._stop()
        self._playing = True
        self.play_lbl.config(text="playing %s..." % self.bank.name_of(self.cur), fg=ACCENT)

        # ⚠ PLAY FROM A TEMP FILE, NOT FROM MEMORY. Measured:
        #   winsound.PlaySound(wav, SND_MEMORY)              -> returns after 5.11s on a 5s clip
        #       i.e. it plays SYNCHRONOUSLY. A worker thread keeps the GUI alive but the sound is
        #       still uninterruptible: SND_PURGE from another thread does not cancel it, which is
        #       exactly why Stop did nothing and a second Play had to wait for the first.
        #   winsound.PlaySound(wav, SND_MEMORY | SND_ASYNC)  -> RuntimeError:
        #       "Cannot play asynchronously from memory" -- Python's winsound REFUSES that
        #       combination outright (the buffer-lifetime hazard), so it is not an option.
        # SND_FILENAME | SND_ASYNC returns immediately and IS cancelled by SND_PURGE. The file
        # must outlive the play, so it is kept until the next play, a stop, or close.
        self._write_temp_wav(wav)
        try:
            winsound.PlaySound(self._wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as ex:
            self._playing = False
            self.play_lbl.config(text="")
            messagebox.showerror(APP_TITLE, "Playback failed: %s: %s" % (type(ex).__name__, ex))
            return
        # There is no completion callback for an async play, so clear the label on a timer.
        secs = 0.0
        if self._pcm is not None and self._rate:
            secs = len(self._pcm) / float(self._rate)
        if self._play_after is not None:
            try:
                self.after_cancel(self._play_after)
            except Exception:
                pass
        self._play_after = self.after(int(secs * 1000) + 250, self._playback_finished)

    def _write_temp_wav(self, wav):
        """Park the WAV on disk so it can be played asynchronously (and therefore stopped)."""
        import tempfile
        if self._wav_path is None:
            fd, self._wav_path = tempfile.mkstemp(prefix='sabplay_', suffix='.wav')
            os.close(fd)
        with open(self._wav_path, 'wb') as f:
            f.write(wav)
        return self._wav_path

    def _playback_finished(self):
        self._play_after = None
        self._playing = False
        self.play_lbl.config(text="")

    def _stop(self):
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        if self._play_after is not None:
            try:
                self.after_cancel(self._play_after)
            except Exception:
                pass
            self._play_after = None
        self._playing = False
        self.play_lbl.config(text="")

    def _cleanup_temp(self):
        if self._wav_path and os.path.exists(self._wav_path):
            try:
                os.remove(self._wav_path)
            except OSError:
                pass                  # still held by the mixer; the OS temp dir will get it
        self._wav_path = None

    # ------------------------------------------------------------------ edit
    def _extract(self):
        if not self.cur:
            return
        name = self.bank.name_of(self.cur).replace("/", "_")
        out = filedialog.asksaveasfilename(title="Extract to WAV", defaultextension=".wav",
                                           initialfile="%s.wav" % name,
                                           filetypes=[("WAV", "*.wav")])
        if not out:
            return
        try:
            self.bank.export(self.cur, out)          # full length, not the preview bound
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "%s: %s" % (type(ex).__name__, ex))
            return
        self.status.config(text="Extracted %s" % os.path.basename(out), fg=OK)

    def _extract_all(self):
        if not self.bank:
            return
        d = filedialog.askdirectory(title="Extract every entry to...")
        if not d:
            return
        self._run_bg("Extracting %d entries" % len(self.bank.entries),
                     lambda: self._do_extract_all(d))

    def _do_extract_all(self, d):
        ok = fail = 0
        for e in list(self.bank.entries):
            try:
                self.bank.export(e, os.path.join(
                    d, "%s.wav" % self.bank.name_of(e).replace("/", "_")))
                ok += 1
            except Exception:
                fail += 1
        return "Extracted %d entries to %s%s" % (ok, d, ", %d failed" % fail if fail else "")

    def _replace(self):
        if not self.cur:
            return
        src = filedialog.askopenfilename(title="Replace with WAV (16-bit PCM)",
                                         filetypes=[("WAV", "*.wav"), ("All files", "*.*")])
        if not src:
            return
        self._run_bg("Encoding %s" % os.path.basename(src), lambda: self._do_replace(src))

    def _do_replace(self, src):
        e = self.cur
        self.bank.replace(e, src)
        return ("Replaced %s -- id kept, %s bytes, %.2fs. Save to write."
                % (self.bank.name_of(e), format(e.size, ","), e.duration))

    def _add(self):
        if not self.bank:
            return
        src = filedialog.askopenfilename(title="Add WAV (16-bit PCM)",
                                         filetypes=[("WAV", "*.wav"), ("All files", "*.*")])
        if not src:
            return
        txt = simpledialog.askstring(
            APP_TITLE,
            "Entry id for the new sound, as hex.\n\n"
            "This is SND_HashName(alias) -- the value an alias in the .ff will look up.\n"
            "Nothing references a new id until you point an alias at it.",
            initialvalue="0x%08X" % (0x5AB00000 + len(self.bank.entries)), parent=self)
        if not txt:
            return
        try:
            eid = int(txt, 16) if txt.lower().startswith("0x") else int(txt, 16)
        except ValueError:
            messagebox.showerror(APP_TITLE, "%r is not a hex id." % txt)
            return
        self._run_bg("Encoding %s" % os.path.basename(src), lambda: self._do_add(eid, src))

    def _do_add(self, eid, src):
        e = self.bank.add(eid, src)
        return ("Added 0x%08X -- %s bytes, %.2fs. Nothing references it yet. Save to write."
                % (e.id, format(e.size, ","), e.duration))

    def _delete(self):
        if not self.cur:
            return
        e = self.cur
        if not messagebox.askyesno(APP_TITLE,
                                   "Delete %s?\n\nAny alias still pointing at this id will "
                                   "fail to resolve." % self.bank.name_of(e)):
            return
        self.bank.delete(e)
        self.cur = None
        self._pcm = None
        self._refill()
        self._draw_wave()
        self.status.config(text="Deleted %s. Save to write." % ("0x%08X" % e.id), fg=INFO)

    def _load_names(self):
        if not self.bank:
            return
        p = filedialog.askopenfilename(title="Load an {id: name} JSON dictionary",
                                       filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not p:
            return
        try:
            n = self.bank.load_names(p)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "%s: %s" % (type(ex).__name__, ex))
            return
        self._refill()
        self.status.config(text="Loaded %d names" % n, fg=OK)

    def _rebuild_names(self):
        """Re-harvest id -> name from every reachable fastfile (cached afterwards).

        Deliberately usable with NO bank open: the natural order is to add a game folder and
        index it, then open a bank and see names. Requiring an open bank first made the action
        a silent no-op at exactly the moment it was wanted.
        """
        self._run_bg("Harvesting names from fastfiles", self._do_rebuild_names)

    def _do_rebuild_names(self):
        if self.bank is not None:
            named, total = self.bank.attach_shared_names(rebuild=True)
            return ("Name dictionary rebuilt: %d/%d entries in this bank now have names"
                    % (named, total))
        from core import sab_names as SN
        names = SN.load_shared(rebuild=True)
        return ("Name dictionary rebuilt: %d ids harvested -- open a bank to see them"
                % len(names or {}))

    def _edit_folders(self):
        """Point the tool at THIS machine's game install.

        A .sabs/.sabl stores no names at all -- only a u32 id per entry -- so every name comes
        from the SndBank asset inside a .ff. If no zone is reachable, entries can only be listed
        by hex id, and no amount of work on the bank itself changes that. This dialog is
        therefore the whole mechanism by which names exist, and until now the sound editor had
        no equivalent of the ipak viewer's version of it.
        """
        from core import settings
        win = tk.Toplevel(self)
        win.title("Game folders")
        win.configure(bg=BG)
        win.geometry("780x400")
        tk.Label(win, bg=BG, fg=TEXT, justify="left", wraplength=740, font=("Segoe UI", 9),
                 text=("Folders searched for the fastfiles that name sound bank entries.\n\n"
                       "Add the 'content' folder of your Black Ops 2 Wii U install -- the one "
                       "holding the .ff zones. A bank keeps only a numeric id per entry; the "
                       "name lives in the SndBank asset inside a zone, so with no zone in reach "
                       "entries can only be shown as hex ids.\n\n"
                       "The folder a bank was opened from is searched automatically. After "
                       "adding a folder, press 'Rebuild names' to index it -- that decrypts "
                       "every zone it finds, so it takes a while, and the result is cached."))\
            .pack(fill="x", padx=12, pady=(12, 6))
        lb = tk.Listbox(win, bg=EDIT, fg=TEXT, selectbackground=ACCENT2, relief="flat",
                        highlightthickness=0, font=("Consolas", 9))
        lb.pack(fill="both", expand=True, padx=12, pady=6)

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
                self.status.config(
                    text="added %s -- press 'Rebuild names' to index it" % d, fg=MUTED)

        def remove():
            sel = lb.curselection()
            if not sel:
                return
            row = lb.get(sel[0])
            if row.startswith("[configured]"):
                settings.remove_content_dir(row.split("  ", 1)[1].strip())
                refill()

        row = tk.Frame(win, bg=BG)
        # pinned and reserved ahead of the expanding list, so the buttons survive a short window
        row.pack(side="bottom", fill="x", padx=12, pady=(0, 12), before=lb)
        ttk.Button(row, text="Add folder...", style="Accent.TButton",
                   command=add).pack(side="left")
        ttk.Button(row, text="Remove", style="Ghost.TButton",
                   command=remove).pack(side="left", padx=6)
        ttk.Button(row, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")

    # ------------------------------------------------------------------ save
    def _save(self, save_as=False):
        if not self.bank or self._busy:
            return
        out = self.path
        if save_as or not out:
            out = filedialog.asksaveasfilename(
                title="Save sound bank", defaultextension=".sabs",
                initialfile=os.path.basename(self.path or "bank.sabs"),
                filetypes=[("T6 sound banks", "*.sabs *.sabl")])
            if not out:
                return
        self._run_bg("Saving %s" % os.path.basename(out), lambda: self._do_save(out))

    def _do_save(self, out):
        res = self.bank.save(out)
        self.path = out
        return ("Saved %s -- %d entries, %s"
                % (os.path.basename(out), res['entries'], _human(res['bytes'])))

    # ------------------------------------------------------------------ plumbing
    def _run_bg(self, label, fn):
        self._busy = True
        self.status.config(text=label + "...", fg=MUTED)
        self.pbar.pack(side="right", padx=10, pady=4)
        self.pbar.start(12)

        def run():
            try:
                self._q.put(("done", fn()))
            except Exception as ex:
                self._q.put(("error", "%s: %s" % (type(ex).__name__, ex)))
        threading.Thread(target=run, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "played":
                    self._playing = False
                    self.play_lbl.config(text="")
                    continue
                self._busy = False
                self.pbar.stop()
                self.pbar.pack_forget()
                if kind == "done":
                    self.status.config(text=payload, fg=OK)
                    self._refill()
                    if self.cur is not None:
                        self._show_info(self.cur)
                else:
                    self.status.config(text=payload, fg=WARN)
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.after(60, self._drain)

    def _on_close(self):
        if self.bank and self.bank.dirty and \
                not messagebox.askyesno(APP_TITLE, "Discard unsaved edits and quit?"):
            return
        self._stop()
        self.destroy()


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


def _run_selftest(quiet=False):
    """`WiiU_SAB_Editor.exe --selftest` -- prove THIS binary works, without Python installed."""
    import io as _io
    import contextlib
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        print("%s %s gate battery" % (APP_TITLE, APP_VERSION))
        print("frozen: %s" % getattr(sys, "frozen", False))
        print()
        from core import sab_selftest
        failed = sab_selftest.run()
    text = buf.getvalue()
    where = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else APP_DIR)
    log = os.path.join(where, "sab_selftest.log")
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        log = "(could not write a log file)"
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass
    if not quiet:
        try:
            root = tk.Tk()
            root.withdraw()
            (messagebox.showinfo if not failed else messagebox.showerror)(
                APP_TITLE, "%s\n\nWritten to:\n%s" % (text.strip()[-1500:], log))
            root.destroy()
        except Exception:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.exit(_run_selftest(quiet="--quiet" in args))
    args = [a for a in args if not a.startswith("--")]
    run_standalone(SabIDE, initial=args[0] if args else None, title=APP_TITLE)
