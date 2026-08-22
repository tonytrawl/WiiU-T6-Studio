"""Wii U IPAK Viewer -- browse, preview, extract and edit T6 texture packs.

Same shape as ff_ide.py: list on the left, preview on the right, one toolbar across the top.

    python ipak_ide.py [file.ipak]

Panes
  left    every entry in the pak: name, part, format, dimensions, stored size
  right   the decoded texture, plus its metadata and provenance
  bottom  status + pak summary

Speed
  Nothing is decoded until you select it, decoded images are cached, and a preview of a large
  texture decodes a small mip level rather than the full surface. See core/ipak_image.py for
  why "preview the stock format without converting" is not literally possible and what is done
  instead.

Editing
  Import can REPLACE the selected texture (re-encoded into that entry's existing format, which
  is what keeps the console reading it correctly) or ADD a new one. Replacing preserves the
  entry's key so the .ff zone's existing reference still resolves; adding mints a new key that
  nothing references yet. The status bar says which happened, every time.
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import numpy as np                                    # noqa: E402
from PIL import Image, ImageTk                        # noqa: E402

from core.ipak import IpakSession, IpakError          # noqa: E402
from core import ipak_image as II                     # noqa: E402
from core import ipak_names as NM                     # noqa: E402

from core.workspace import Workspace, run_standalone   # noqa: E402

APP_TITLE = "Wii U IPAK Viewer"
APP_VERSION = "1.0"

# Kept in step with core/theme.py -- see the note there.
BG, PANEL, EDIT = "#0f151d", "#171f2a", "#0d1219"
GUTTER, HEAD = "#111823", "#111823"
ACCENT, ACCENT2 = "#18b4a8", "#0e8f86"
TEXT, MUTED, BORDER = "#eef3f8", "#a8b4c4", "#2c3849"
OK, WARN, INFO = "#46cf88", "#f0736e", "#e8b552"

PREVIEW_MAX = 512          # decode bound; the canvas scales what it gets
GALLERY_THUMB = 128        # gallery tile box; decoding to this is what keeps scrolling smooth
CACHE_LIMIT = 48           # decoded previews held in memory


class IpakIDE(Workspace):
    """The texture pak viewer/editor. Hosted in the studio shell, or standalone."""

    kind = 'ipak'

    def __init__(self, master=None, initial=None):
        super().__init__(master)
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.configure(bg=BG)

        self.session = None
        self.cur = None
        self.node_item = {}
        self._q = queue.Queue()
        self._filter = tk.StringVar()
        self._sort = {}                  # click-to-sort state, see core.uiutil
        self._show_alpha = tk.BooleanVar(value=False)
        self._cache = {}
        self._cache_order = []
        self._photo = None
        self._pending = 0

        self._style()
        self._menu()
        self._toolbar()
        self._body()
        self._statusbar()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain)
        self.bind_all("<Control-o>", lambda e: self._open())
        self.bind_all("<Control-s>", lambda e: self._save())
        self.bind_all("<Control-e>", lambda e: self._export())
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
        st.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("TCheckbutton", background=HEAD, foreground=MUTED)
        st.map("TCheckbutton", background=[("active", HEAD)])
        st.configure("Accent.TButton", background=ACCENT, foreground="#03110f",
                     font=("Segoe UI Semibold", 10), borderwidth=0, padding=(13, 6))
        st.map("Accent.TButton", background=[("active", ACCENT2), ("disabled", BORDER)])
        st.configure("Ghost.TButton", background=PANEL, foreground=TEXT,
                     borderwidth=1, padding=(10, 5))
        st.map("Ghost.TButton", background=[("active", BORDER)])
        st.configure("Tree.Treeview", background=EDIT, fieldbackground=EDIT, foreground=TEXT,
                     borderwidth=0, rowheight=20, font=("Consolas", 9))
        st.map("Tree.Treeview", background=[("selected", ACCENT2)],
               foreground=[("selected", "#03110f")])
        st.configure("Tree.Treeview.Heading", background=HEAD, foreground=MUTED,
                     borderwidth=0, font=("Segoe UI", 9))

    def _menu(self):
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Open ipak...\tCtrl+O", command=self._open)
        fm.add_command(label="Save\tCtrl+S", command=self._save)
        fm.add_command(label="Save As...", command=lambda: self._save(save_as=True))
        fm.add_separator()
        fm.add_command(label="Export texture as PNG...\tCtrl+E", command=self._export)
        fm.add_command(label="Export raw payload...", command=self._export_raw)
        fm.add_command(label="Export ALL as PNG...", command=self._export_all)
        fm.add_separator()
        fm.add_command(label="Replace selected texture...", command=self._import_replace)
        fm.add_command(label="Add new texture...", command=self._import_add)
        fm.add_command(label="Delete selected entry", command=self._delete)
        fm.add_separator()
        fm.add_command(label="Exit", command=self._on_close)
        m.add_cascade(label="File", menu=fm)

        tm = tk.Menu(m, tearoff=0)
        tm.add_command(label="Pak summary", command=self._show_summary)
        tm.add_command(label="Check key/crc consistency", command=self._show_hashes)
        tm.add_command(label="Game folders...", command=self._edit_folders)
        tm.add_command(label="Rebuild name dictionary", command=self._rebuild_meta)
        m.add_cascade(label="Tools", menu=tm)
        self.config(menu=m)

    def _toolbar(self):
        bar = tk.Frame(self, bg=HEAD, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ttk.Button(bar, text="Open", style="Ghost.TButton", command=self._open)\
            .pack(side="left", padx=(10, 4), pady=7)
        ttk.Button(bar, text="Save", style="Accent.TButton", command=self._save)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Export", style="Ghost.TButton", command=self._export)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Replace", style="Ghost.TButton", command=self._import_replace)\
            .pack(side="left", padx=4, pady=7)
        ttk.Button(bar, text="Add", style="Ghost.TButton", command=self._import_add)\
            .pack(side="left", padx=4, pady=7)
        tk.Label(bar, text="  filter:", bg=HEAD, fg=MUTED).pack(side="left")
        ent = ttk.Entry(bar, textvariable=self._filter, width=22)
        ent.pack(side="left", padx=4)
        ent.bind("<KeyRelease>", lambda e: self._refill())
        ttk.Checkbutton(bar, text="alpha", variable=self._show_alpha,
                        command=self._redraw).pack(side="left", padx=8)
        self.title_lbl = tk.Label(bar, text="no ipak open", bg=HEAD, fg=MUTED)
        self.title_lbl.pack(side="left", padx=14)
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

    def _body(self):
        pan = tk.PanedWindow(self, orient="horizontal", bg=BORDER, sashwidth=4, bd=0)
        pan.pack(fill="both", expand=True)

        # ---- left: the entry list ----
        tw = tk.Frame(pan, bg=EDIT)
        # ⚠ THE HOLDERS ARE BUILT FIRST SO THE TREE IS A REAL CHILD OF `listw`.
        # An earlier attempt created the tree under `tw` and packed it with in_=listw. Tk allows
        # that, but the widget stays a CHILD OF ITS MASTER in the hierarchy, so it is drawn behind
        # its sibling `side` -- the list rendered as an empty black panel while winfo_width() and
        # winfo_ismapped() both reported it healthy. Numbers cannot see occlusion; parent
        # correctly instead of packing across the hierarchy.
        from core import gallery as GAL
        side = tk.Frame(tw, bg=EDIT)
        listw = tk.Frame(side, bg=EDIT)
        self.tree = ttk.Treeview(listw, style="Tree.Treeview",
                                 columns=("part", "fmt", "dims", "size"),
                                 show="tree headings", selectmode="browse")
        # Click a heading to sort by it; click again to reverse. Keys read the MODEL, so
        # `bytes` orders numerically rather than by its formatted text.
        from core import uiutil
        uiutil.attach_sorting(self.tree, {
            '#0': ('image', lambda i: (i.label or '').lower()),
            'part': ('part', lambda i: i.part_index),
            'fmt': ('format', lambda i: (i.format_string or '').lower()),
            'dims': ('dims', lambda i: (i.dims[0] * i.dims[1]) if i.dims else -1),
            'size': ('bytes', lambda i: i.stored_size),
        }, self._refill, self._sort)
        self.tree.column("#0", width=330, anchor="w")
        self.tree.column("part", width=42, anchor="center")
        self.tree.column("fmt", width=64, anchor="center")
        self.tree.column("dims", width=88, anchor="center")
        self.tree.column("size", width=84, anchor="e")
        # List and gallery are SIBLINGS in one holder, so switching keeps the selection and
        # neither side is rebuilt -- the point is flipping back and forth freely.
        sb = ttk.Scrollbar(listw, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        self.tree.tag_configure("edited", foreground=INFO)
        self.tree.tag_configure("added", foreground=OK)
        self.tree.tag_configure("nometa", foreground=MUTED)

        self.gallery = GAL.Gallery(
            side,
            key=lambda i: (i.name_hash, i.data_hash),
            label=lambda i: i.label or '?',
            thumbnail=self._thumb_for,
            on_select=self._on_gallery_pick,
            on_activate=self._on_gallery_pick)

        # ⚠ PACK THE SWITCH FIRST. An expanding sibling packed earlier takes all the space and
        # the toggle never appears -- it is present, sized zero, and looks like it was never added.
        ctl = tk.Frame(tw, bg=HEAD)
        ctl.pack(side="bottom", fill="x")
        from core import settings as _st
        self.view = GAL.ViewSwitch(
            ctl, listw, self.gallery,
            on_change=lambda m: _st.set_view('ipak', m))   # remembered per kind, see settings
        self.view.pack(side="left", anchor="w", padx=6, pady=4)

        tk.Label(ctl, text="size", bg=HEAD, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")
        self._thumb_px = tk.IntVar(value=GALLERY_THUMB)
        tk.Scale(ctl, from_=64, to=256, resolution=32, orient="horizontal", showvalue=False,
                 variable=self._thumb_px, length=110, bg=HEAD, fg=MUTED, highlightthickness=0,
                 troughcolor=EDIT, bd=0,
                 command=lambda _v: self.gallery.set_thumb_px(self._thumb_px.get())
                 ).pack(side="right", padx=(0, 8))
        side.pack(fill="both", expand=True)
        listw.pack(fill="both", expand=True)
        pan.add(tw, minsize=380, width=620)

        # ---- right: preview + info ----
        rw = tk.Frame(pan, bg=PANEL)
        self.canvas = tk.Canvas(rw, bg=EDIT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.info = tk.Text(rw, height=9, bg=EDIT, fg=TEXT, bd=0, relief="flat",
                            font=("Consolas", 9), wrap="word", padx=10, pady=8,
                            insertbackground=TEXT)
        self.info.pack(fill="x", padx=8, pady=(0, 8))
        self.info.configure(state="disabled")
        pan.add(rw, minsize=360)

    def _statusbar(self):
        bar = tk.Frame(self, bg=HEAD, height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="ready", bg=HEAD, fg=MUTED, anchor="w")
        self.status.pack(side="left", padx=10)
        self.summary = tk.Label(bar, text="", bg=HEAD, fg=MUTED, anchor="e")
        self.summary.pack(side="right", padx=10)

    def _say(self, msg, colour=MUTED):
        self.status.config(text=msg, fg=colour)
        self.update_idletasks()

    # -------------------------------------------------------------------- open
    def _open(self, path=None):
        if not path:
            path = filedialog.askopenfilename(
                title="Open ipak",
                filetypes=[("T6 image packs", "*.ipak"), ("All files", "*.*")])
        if not path:
            return
        if self.session and self.session.dirty and not messagebox.askyesno(
                APP_TITLE, "The open pak has unsaved changes. Discard them?"):
            return
        self._say("opening %s ..." % os.path.basename(path))
        try:
            from core import settings as _settings
            _settings.note_opened(path)      # harvest this folder's siblings for names
            self.session = IpakSession.open(path)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not open:\n\n%s: %s"
                                 % (type(ex).__name__, ex))
            self._say("open failed", WARN)
            return
        self._cache.clear()
        self._cache_order.clear()
        self.cur = None
        self.title_lbl.config(text=os.path.basename(path))
        self.title("%s -- %s" % (APP_TITLE, os.path.basename(path)))
        self.set_document(path)          # keeps doc.path honest, not just the tab caption
        self._refill()
        st = self.session.stats()
        self.summary.config(
            text="%d entries | %d named | %d previewable | %s | %.1f MB"
                 % (st['entries'], st['named'], st['previewable'], st['endian'],
                    st['bytes'] / 1048576.0))
        unnamed = st['entries'] - st['named']
        if unnamed:
            self._say("opened. %d entr%s could not be named -- Tools > Rebuild name dictionary "
                      "may help if your content folder has more paks"
                      % (unnamed, "y" if unnamed == 1 else "ies"), INFO)
        else:
            self._say("opened %d entries, all named" % st['entries'], OK)

    def _refill(self):
        self.tree.delete(*self.tree.get_children())
        self.node_item.clear()
        if not self.session:
            return
        needle = self._filter.get().strip().lower()
        shown = 0
        visible = []
        from core import uiutil
        if self._sort.get('sort'):
            ordered = uiutil.sort_rows(self.session.items, self._sort)
        else:
            ordered = sorted(self.session.items, key=lambda i: (i.label.lower(), i.part_index))
        for it in ordered:
            if needle and needle not in it.label.lower() and needle not in (
                    it.format_string or '').lower():
                continue
            tags = []
            if it.status == 'added':
                tags.append('added')
            elif it.status == 'replaced':
                tags.append('edited')
            elif not it.name:
                tags.append('nometa')
            dims = '%dx%d' % it.dims if it.dims else '--'
            iid = self.tree.insert('', 'end', text=it.label,
                                   values=(it.part_index, it.format_string or '--',
                                           dims, '%d' % it.stored_size), tags=tags)
            self.node_item[iid] = it
            visible.append(it)
            shown += 1
        # The gallery shows exactly what the list shows -- same filter, same sort order. Two views
        # of one selection, not two different datasets.
        if getattr(self, 'gallery', None) is not None:
            self.gallery.set_items(visible)
            # Restore the view the user last chose FOR THIS KIND, once, after there is content
            # to show -- switching to an empty gallery looks like the switch is broken.
            if visible and not getattr(self, '_view_restored', False):
                self._view_restored = True
                try:
                    from core import settings as _st
                    self.view.show(_st.get_view('ipak', 'list'))
                except Exception:
                    pass
        if needle:
            self._say("%d of %d entries match %r" % (shown, len(self.session.items), needle))

    # ----------------------------------------------------------------- gallery
    def _thumb_for(self, it):
        """item -> a small PIL image for the grid. ⚠ RUNS ON A WORKER: no Tk calls in here.

        Decodes the SMALLEST mip that covers the tile rather than the full surface -- a 2048x2048
        BC3 face costs milliseconds this way and would otherwise make scrolling unusable.
        Undecodable entries (no metadata, or a format the decoder refuses, e.g. A8L8) return None
        and the tile says 'no preview' -- an honest blank, never a fabricated one.
        """
        try:
            from PIL import Image
            rgba, _lvl, _dims = self.session.preview(it, max_side=GALLERY_THUMB)
            rgba, _note = II.for_display(rgba)   # emblems store art in luminance, alpha 0
            im = Image.fromarray(rgba, 'RGBA')
            im.thumbnail((GALLERY_THUMB, GALLERY_THUMB), Image.LANCZOS)
            bg = Image.new('RGBA', (GALLERY_THUMB, GALLERY_THUMB), (18, 24, 32, 255))
            bg.paste(im, ((GALLERY_THUMB - im.width) // 2,
                          (GALLERY_THUMB - im.height) // 2), im)
            return bg.convert('RGB')
        except Exception:
            return None

    def _on_gallery_pick(self, it):
        """A tile click must land on the SAME record the list view would select."""
        self.cur = it
        self._show_info(it)
        self._reselect(it)
        self._on_pick()

    # ----------------------------------------------------------------- preview
    def _on_pick(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        it = self.node_item.get(sel[0])
        if it is None:
            return
        self.cur = it
        self._show_info(it)
        key = (it.name_hash, it.data_hash, it.status)
        if key in self._cache:
            self._redraw()
            return
        self._photo = None
        self.canvas.delete("all")
        self._say("decoding %s ..." % it.label)
        self._pending += 1
        token = self._pending
        threading.Thread(target=self._decode_worker, args=(it, token), daemon=True).start()

    def _decode_worker(self, it, token):
        try:
            rgba, lvl, dims = self.session.preview(it, max_side=PREVIEW_MAX)
            self._q.put(('img', token, it, rgba, lvl, dims))
        except Exception as ex:
            self._q.put(('err', token, it, '%s' % ex, None, None))

    def _drain(self):
        try:
            while True:
                kind, token, it, a, b, c = self._q.get_nowait()
                if token != self._pending or it is not self.cur:
                    continue                    # a stale decode for a row we already left
                if kind == 'img':
                    self._cache_put((it.name_hash, it.data_hash, it.status), (a, b, c))
                    self._redraw()
                    self._say("%s -- decoded level %d at %dx%d" % (it.label, b, c[0], c[1]), OK)
                else:
                    self.canvas.delete("all")
                    self._draw_message(a)
                    self._say(a, INFO)
        except queue.Empty:
            pass
        self.after(60, self._drain)

    def _cache_put(self, key, val):
        self._cache[key] = val
        self._cache_order.append(key)
        while len(self._cache_order) > CACHE_LIMIT:
            self._cache.pop(self._cache_order.pop(0), None)

    def _redraw(self):
        self.canvas.delete("all")
        if not self.cur:
            return
        hit = self._cache.get((self.cur.name_hash, self.cur.data_hash, self.cur.status))
        if not hit:
            return
        rgba, _lvl, _dims = hit
        # An all-zero alpha plane composites to nothing over the checkerboard below. Emblems are
        # stored exactly that way (art in luminance), so show them opaque and SAY SO rather than
        # present an empty square. The stored bytes are untouched -- see ipak_image.for_display.
        rgba, _disp_note = II.for_display(rgba)
        if _disp_note:
            self._say(_disp_note, INFO)
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        h, w = rgba.shape[0], rgba.shape[1]

        if self._show_alpha.get():
            a = rgba[:, :, 3]
            arr = np.dstack([a, a, a])
            im = Image.fromarray(arr, 'RGB')
        else:
            im = Image.fromarray(rgba, 'RGBA')
            # Composite over a checkerboard so transparency reads as transparency rather than
            # as black pixels that look like a broken texture.
            bgc = Image.new('RGBA', (w, h), (52, 58, 70, 255))
            sq = 8
            yy, xx = np.mgrid[0:h, 0:w]
            mask = (((yy // sq) + (xx // sq)) % 2).astype(bool)
            base = np.array(bgc)
            base[mask] = (68, 76, 92, 255)
            im = Image.alpha_composite(Image.fromarray(base, 'RGBA'), im).convert('RGB')

        scale = min(cw / float(w), ch / float(h))
        # Integer-multiply small textures so single pixels stay legible; shrink large ones.
        if scale >= 1:
            scale = max(1, int(scale))
            resample = Image.NEAREST
        else:
            resample = Image.LANCZOS
        dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
        self._photo = ImageTk.PhotoImage(im.resize((dw, dh), resample))
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)
        self.canvas.create_text(8, ch - 10, anchor="w", fill=MUTED,
                                font=("Consolas", 8),
                                text="%dx%d shown at %d%%" % (w, h, int(scale * 100)))

    def _draw_message(self, msg):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.canvas.create_text(cw // 2, ch // 2, fill=MUTED, width=cw - 60,
                                justify="center", font=("Segoe UI", 10), text=msg)

    def _show_info(self, it):
        lines = []
        lines.append("%s" % it.label)
        lines.append("  key        nameHash %08X   dataHash %08X   part %d"
                     % (it.name_hash, it.data_hash, it.part_index))
        if it.record:
            r = it.record
            lines.append("  format     %s        levels %s   mip %s"
                         % (r.get('format', '?'), r.get('levels', '?'), r.get('mip', '?')))
            lines.append("  dimensions %s x %s" % (r.get('width', '?'), r.get('height', '?')))
            lines.append("  source     %s" % r.get('iwi', '?'))
        else:
            lines.append("  metadata   NONE -- no section-3 record for this key, and the")
            lines.append("             cross-pak dictionary did not have it either.")
            if it.name:
                lines.append("             (name resolved by nameHash only, not exact)")
        lines.append("  stored     %d bytes in the data section" % it.stored_size)
        lines.append("  state      %s%s" % (it.status,
                                            "" if it.exact_meta else "   [metadata inexact]"))
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", "\n".join(lines))
        self.info.configure(state="disabled")

    # ------------------------------------------------------------------ export
    def _need(self):
        if not self.session:
            messagebox.showinfo(APP_TITLE, "Open an ipak first.")
            return False
        if not self.cur:
            messagebox.showinfo(APP_TITLE, "Select an entry first.")
            return False
        return True

    def _export(self):
        if not self._need():
            return
        it = self.cur
        try:
            rgba, _lvl, _d = self.session.preview(it, max_side=None)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Cannot decode this entry:\n\n%s" % ex)
            return
        p = filedialog.asksaveasfilename(defaultextension=".png",
                                         initialfile="%s.png" % _safe(it.label),
                                         filetypes=[("PNG", "*.png")])
        if not p:
            return
        Image.fromarray(rgba, 'RGBA').save(p)
        self._say("exported %s (%dx%d)" % (os.path.basename(p), rgba.shape[1], rgba.shape[0]),
                  OK)

    def _export_raw(self):
        if not self._need():
            return
        it = self.cur
        p = filedialog.asksaveasfilename(defaultextension=".bin",
                                         initialfile="%s.part%d.bin" % (_safe(it.label),
                                                                        it.part_index))
        if not p:
            return
        with open(p, 'wb') as f:
            f.write(self.session.payload(it))
        self._say("exported raw GX2-tiled payload: %s" % os.path.basename(p), OK)

    def _export_all(self):
        if not self.session:
            messagebox.showinfo(APP_TITLE, "Open an ipak first.")
            return
        d = filedialog.askdirectory(title="Export every previewable texture as PNG into...")
        if not d:
            return
        todo = [i for i in self.session.items if i.previewable]
        if not messagebox.askyesno(
                APP_TITLE, "Export %d textures as PNG to\n%s?\n\n%d of %d entries cannot be "
                           "decoded and will be skipped." % (len(todo), d,
                                                             len(self.session.items) - len(todo),
                                                             len(self.session.items))):
            return
        done = fail = 0
        for n, it in enumerate(todo):
            if n % 25 == 0:
                self._say("exporting %d/%d ..." % (n, len(todo)))
            try:
                rgba, _l, _dd = self.session.preview(it, max_side=None)
                Image.fromarray(rgba, 'RGBA').save(
                    os.path.join(d, "%s.part%d.png" % (_safe(it.label), it.part_index)))
                done += 1
            except Exception:
                fail += 1
        self._say("exported %d PNGs, %d failed" % (done, fail), OK if not fail else INFO)

    # ------------------------------------------------------------------ import
    def _shadow_check(self, it):
        """Warn if an EARLIER-LOADING pak also holds this image. -> True to proceed.

        ⚠ THIS IS THE MOST EXPENSIVE SILENT FAILURE IN THE WHOLE TOOL. IPak_BuildAdjacencyInfo
        compares the 64-bit (nameHash, dataHash) key exactly and SKIPS any part already bound, so
        the console reads whichever pak loaded FIRST and never revisits it. Edit a later one and
        the save succeeds, the bytes are correct, and the game shows the original -- with nothing
        anywhere reporting a problem. That is exactly how three edited posters on a ported map
        were lost: base_split1 / lowmip_split1 / mp_express held byte-identical keys and load
        before the map's own pak.

        Find asset already computes this; the replace path never asked. Now it does.
        """
        try:
            from core import ipak_search as isearch
            own, _paks = isearch.owner_map()
        except Exception:
            return True                      # no index -> stay out of the way
        mine = os.path.normcase(os.path.abspath(self.session.path))
        others = []
        for p in self.session.parts_of(it):
            for q in own.get((p.name_hash, p.data_hash), ()):
                if os.path.normcase(os.path.abspath(q)) != mine:
                    b = os.path.basename(q)
                    if b not in others:
                        others.append(b)
        if not others:
            return True
        return messagebox.askyesno(
            APP_TITLE,
            "%s also lives in:\n\n  %s\n\nThe console binds each part from the FIRST pak it "
            "finds it in and never looks again, so if any of those load before %s your edit "
            "will not be read -- the save will succeed and the game will still show the "
            "original.\n\nBase-game paks (base_split*, lowmip_split*) load before a map's own "
            "pak.\n\nReplace here anyway?"
            % (it.label, "\n  ".join(others[:8]), os.path.basename(self.session.path)),
            icon='warning')

    def _import_replace(self):
        if not self._need():
            return
        it = self.cur
        if not it.previewable:
            messagebox.showerror(
                APP_TITLE,
                "This entry has no known format or dimensions, so there is no way to know how "
                "the console expects the replacement to be encoded.\n\nReplacing it would "
                "almost certainly produce a corrupt texture, so it is refused.")
            return
        if not self._shadow_check(it):
            return
        p = filedialog.askopenfilename(
            title="Replace %s with..." % it.label,
            filetypes=[("Images", "*.png;*.dds;*.tga;*.bmp;*.jpg;*.jpeg;*.gif;*.tif;*.tiff;*.webp"), ("All", "*.*")])
        if not p:
            return
        try:
            im = Image.open(p).convert('RGBA')
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not read that image:\n\n%s" % ex)
            return
        if im.size != (it.width, it.height):
            if not messagebox.askyesno(
                    APP_TITLE,
                    "That image is %dx%d but this entry is %dx%d.\n\nThe dimensions are fixed "
                    "by the GfxImage record in the .ff zone, so the image will be resized to "
                    "fit. Continue?" % (im.size[0], im.size[1], it.width, it.height)):
                return
            im = im.resize((it.width, it.height), Image.LANCZOS)
        # Replace EVERY part of this image, not just the selected entry. A streamed image is
        # split across up to three parts, each holding a different slice of the mip chain, so
        # replacing one leaves the other levels serving the original pixels -- which is exactly
        # the "I replaced it and the stock texture still shows" symptom.
        try:
            rep = self.session.replace_image_all(it, np.asarray(im, np.uint8))
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Replace failed:\n\n%s" % ex)
            return
        for p in self.session.parts_of(it):
            self._cache.pop((p.name_hash, p.data_hash, 'original'), None)
        self._refill()
        self._reselect(it)

        if not rep['replaced']:
            messagebox.showerror(APP_TITLE, "Nothing was replaced:\n\n" +
                                 "\n".join("part %d: %s" % s for s in rep['skipped']))
            return
        msg = "replaced %s: part%s %s re-encoded as %s -- not written until you Save" % (
            rep['name'], "s" if len(rep['replaced']) > 1 else "",
            ", ".join(str(i) for i in rep['replaced']), it.format_string)
        self._say(msg, OK)

        notes = []
        if rep['skipped']:
            notes.append("These parts were NOT replaced and will keep showing the original "
                         "pixels at those mip levels:\n" +
                         "\n".join("   part %d -- %s" % s for s in rep['skipped']))
        if rep['elsewhere']:
            notes.append("This image has further parts in other paks:\n" +
                         "\n".join("   part %d in %s" % s for s in rep['elsewhere']) +
                         "\n\nThose mip levels come from those files, so they will keep serving "
                         "the original pixels until you replace them there too.\n"
                         "Part 0 is the low-detail tail (lowmip_split*); parts 1 and 2 are the "
                         "levels you see up close.")
        notes.append("After deploying, COLD-START the game: resolved parts stay cached, so a "
                     "hot reload can keep serving the old bytes.")
        messagebox.showinfo(APP_TITLE, "\n\n".join(notes))

    def _import_add(self):
        if not self.session:
            messagebox.showinfo(APP_TITLE, "Open an ipak first.")
            return
        p = filedialog.askopenfilename(
            title="Add a new texture from...",
            filetypes=[("Images", "*.png;*.dds;*.tga;*.bmp;*.jpg;*.jpeg;*.gif;*.tif;*.tiff;*.webp"), ("All", "*.*")])
        if not p:
            return
        try:
            im = Image.open(p).convert('RGBA')
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not read that image:\n\n%s" % ex)
            return
        dlg = _AddDialog(self, os.path.splitext(os.path.basename(p))[0], im.size)
        self.wait_window(dlg)
        if not dlg.result:
            return
        name, fmt, levels = dlg.result
        try:
            it = self.session.add_image(name, np.asarray(im, np.uint8),
                                        format_string=fmt, levels=levels)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Add failed:\n\n%s" % ex)
            return
        self._refill()
        self._reselect(it)
        messagebox.showinfo(
            APP_TITLE,
            "Added %r as %s.\n\nkey: nameHash %08X  dataHash %08X\n\n"
            "Note this only puts the pixels in the pak. Nothing references them until a "
            "GfxImage record in a .ff zone points at that key, so the texture will not appear "
            "in game on its own." % (name, fmt, it.name_hash, it.data_hash))
        self._say("added %s -- not written until you Save" % name, OK)

    def _delete(self):
        if not self._need():
            return
        it = self.cur
        if not messagebox.askyesno(
                APP_TITLE, "Delete %s (part %d) from the pak?\n\nIf a zone still references "
                           "this key, that image will fail to load." % (it.label,
                                                                        it.part_index)):
            return
        self.session.delete(it)
        self.cur = None
        self._refill()
        self._say("deleted %s -- not written until you Save" % it.label, INFO)

    def _reselect(self, it):
        for iid, cand in self.node_item.items():
            if cand is it:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self._on_pick()
                return

    # -------------------------------------------------------------------- save
    def _save(self, save_as=False):
        if not self.session:
            return
        path = self.session.path
        if save_as:
            path = filedialog.asksaveasfilename(defaultextension=".ipak",
                                                initialfile=os.path.basename(path),
                                                filetypes=[("T6 image pack", "*.ipak")])
            if not path:
                return
        if not self.session.dirty and not save_as:
            self._say("nothing changed -- nothing written", INFO)
            return
        self._say("saving ...")
        try:
            self.session.save(path)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Save FAILED and the file was not changed:\n\n"
                                            "%s: %s" % (type(ex).__name__, ex))
            self._say("save failed", WARN)
            return
        mism = self.session.verify_hashes()
        note = ""
        if mism:
            note = ("  (%d entr%s carry a preserved key whose crc no longer matches -- that is "
                    "what makes a drop-in swap work)" % (len(mism),
                                                         "y" if len(mism) == 1 else "ies"))
        self._say("saved %s%s" % (os.path.basename(path), note), OK)
        # Re-open so what is on screen is what is on disk. `cur` belonged to the previous
        # session's item list, so it must be dropped or the preview would show a stale object.
        self.session = IpakSession.open(path)
        self.cur = None
        self._photo = None
        self.canvas.delete("all")
        self._cache.clear()
        self._cache_order.clear()
        self._refill()

    # ------------------------------------------------------------------- tools
    def _show_summary(self):
        if not self.session:
            return
        st = self.session.stats()
        by_fmt = {}
        for i in self.session.items:
            by_fmt[i.format_string or '(unknown)'] = by_fmt.get(i.format_string
                                                                or '(unknown)', 0) + 1
        lines = ["%s" % self.session.path, ""]
        for k, v in sorted(st.items()):
            lines.append("  %-14s %s" % (k, v))
        lines.append("")
        lines.append("  formats:")
        for k, v in sorted(by_fmt.items(), key=lambda t: -t[1]):
            lines.append("    %-12s %d" % (k, v))
        _TextWindow(self, "Pak summary", "\n".join(lines))

    def _show_hashes(self):
        if not self.session:
            return
        self._say("checking every payload crc against its key ...")
        mism = self.session.verify_hashes()
        if not mism:
            _TextWindow(self, "Key / crc consistency",
                        "Every entry's payload crc matches its stored dataHash.\n\n"
                        "This is the state a retail pak ships in.")
        else:
            lines = ["%d entr%s whose stored dataHash does not match the payload crc.\n"
                     % (len(mism), "y" if len(mism) == 1 else "ies"),
                     "This is EXPECTED after replacing a texture: the key is deliberately",
                     "preserved so the .ff zone's existing reference still resolves. It is",
                     "only a problem if you did not intend it.\n"]
            for it, stored, actual in mism[:200]:
                lines.append("  %-46s stored %08X  actual %08X" % (it.label, stored, actual))
            _TextWindow(self, "Key / crc consistency", "\n".join(lines))
        self._say("ready")

    def _edit_folders(self):
        """Let the user point the tool at THEIR game install.

        The dictionary was originally harvested from two hard-coded paths, which works on exactly
        one machine. A pak opened from anywhere else scores no names -- measured on a
        project-built base_split8: 43 entries, 0 named, because nothing on disk describes those
        keys. The search path has to be the user's, not ours.
        """
        from core import settings
        win = tk.Toplevel(self)
        win.title("Game folders")
        win.configure(bg=BG)
        win.geometry("780x400")
        tk.Label(win, bg=BG, fg=TEXT, justify="left", wraplength=740, font=("Segoe UI", 9),
                 text=("Folders searched for names, formats and dimensions.\n\n"
                       "Add the 'content' folder of your Black Ops 2 Wii U install -- the one "
                       "holding the .ipak and .ff files. The .ff zones matter as much as the "
                       "paks: they are what names entries in paks that carry no metadata of "
                       "their own.\n\n"
                       "The folder a file was opened from is searched automatically, so a pak "
                       "sitting next to its own zones usually resolves with no configuration."))\
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
                self._say("added %s -- run Tools > Rebuild name dictionary to index it" % d,
                          INFO)

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
        ttk.Button(row, text="Rebuild dictionary now", style="Ghost.TButton",
                   command=lambda: (win.destroy(), self._rebuild_meta())).pack(side="left",
                                                                               padx=6)
        ttk.Button(row, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")

    def _rebuild_meta(self):
        dirs = NM.content_dirs()
        if not dirs:
            messagebox.showinfo(
                APP_TITLE,
                "No game content folder is configured, so there is nothing to harvest names "
                "from.\n\nUse Tools > Game folders... and point it at the 'content' folder of "
                "your Black Ops 2 Wii U install.")
            return
        # ⛔ THIS USED TO RUN ON THE UI THREAD. `load_shared(rebuild=True)` walks every pak and
        # every zone under the content folders -- minutes of work -- inside the button handler,
        # so Tk could not repaint and Windows titled the window "Not Responding". It looked
        # exactly like a crash, and there was no progress and no way to stop it.
        self._say("harvesting metadata from %s ..." % dirs[0])
        from core import uiutil as UI
        try:
            idx = UI.run_with_progress(
                self, "Rebuilding the name dictionary",
                lambda report: NM.load_shared(rebuild=True, progress=report),
                app_title=APP_TITLE)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Harvest failed:\n\n%s: %s"
                                 % (type(ex).__name__, ex))
            self._say("harvest failed", WARN)
            return
        if idx is None:                       # cancelled: leave the old dictionary in place
            self._say("dictionary rebuild cancelled -- the previous one is still in use", WARN)
            return
        messagebox.showinfo(APP_TITLE, "Dictionary rebuilt: %d keys, %d distinct image names."
                                       % (len(idx), len(idx.by_name_hash)))
        if self.session:
            self._open(self.session.path)

    def _on_close(self):
        if self.session and self.session.dirty and not messagebox.askyesno(
                APP_TITLE, "There are unsaved changes. Quit anyway?"):
            return
        self.destroy()


class _AddDialog(tk.Toplevel):
    """Name / format / mip choice for a newly added texture."""

    def __init__(self, parent, default_name, size):
        super().__init__(parent)
        self.title("Add texture")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.result = None
        self.transient(parent)
        self.grab_set()

        tk.Label(self, text="Image is %dx%d" % size, bg=BG, fg=MUTED)\
            .grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 4), sticky="w")
        tk.Label(self, text="name", bg=BG, fg=TEXT).grid(row=1, column=0, padx=12, sticky="e")
        self.name = tk.StringVar(value=default_name)
        ttk.Entry(self, textvariable=self.name, width=38)\
            .grid(row=1, column=1, padx=12, pady=4)

        tk.Label(self, text="format", bg=BG, fg=TEXT).grid(row=2, column=0, padx=12, sticky="e")
        self.fmt = tk.StringVar(value="DXT5")
        ttk.Combobox(self, textvariable=self.fmt, width=35, state="readonly",
                     values=["DXT1", "DXT3", "DXT5", "DXN", "A8R8G8B8", "L8"])\
            .grid(row=2, column=1, padx=12, pady=4)

        tk.Label(self, text="mip levels", bg=BG, fg=TEXT)\
            .grid(row=3, column=0, padx=12, sticky="e")
        self.levels = tk.StringVar(value="1")
        ttk.Entry(self, textvariable=self.levels, width=38)\
            .grid(row=3, column=1, padx=12, pady=4)

        tk.Label(self, text="A new entry is not referenced by any zone yet.",
                 bg=BG, fg=MUTED, wraplength=340, justify="left")\
            .grid(row=4, column=0, columnspan=2, padx=12, pady=(8, 4), sticky="w")

        bf = tk.Frame(self, bg=BG)
        bf.grid(row=5, column=0, columnspan=2, pady=(4, 12))
        ttk.Button(bf, text="Add", style="Accent.TButton", command=self._ok)\
            .pack(side="left", padx=6)
        ttk.Button(bf, text="Cancel", style="Ghost.TButton", command=self.destroy)\
            .pack(side="left", padx=6)

    def _ok(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showerror(APP_TITLE, "A name is required -- it is what the nameHash "
                                            "key is derived from.")
            return
        try:
            levels = max(1, int(self.levels.get()))
        except ValueError:
            messagebox.showerror(APP_TITLE, "Mip levels must be a whole number.")
            return
        self.result = (name, self.fmt.get(), levels)
        self.destroy()


class _TextWindow(tk.Toplevel):
    def __init__(self, parent, title, body):
        super().__init__(parent)
        self.title(title)
        self.geometry("820x560")
        self.configure(bg=BG)
        t = tk.Text(self, bg=EDIT, fg=TEXT, bd=0, font=("Consolas", 9),
                    wrap="none", padx=10, pady=8)
        sb = ttk.Scrollbar(self, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        # Scrollbar reserved FIRST: Tk allocates space in pack order, so a fixed-width bar packed
        # after an expand=True text widget is clipped away entirely on a narrow window.
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        t.insert("1.0", body)
        t.configure(state="disabled")


def _safe(name):
    """Filesystem-safe name for an export.

    ⚠ `~` MUST NOT SURVIVE. It is not a filesystem problem -- it is a Tcl one. 65.1% of retail
    ipak entries are T6 generated-image names beginning with `~` (e.g. `~-gbox_curio_wooden_col`),
    and Tk 8.6 applies TILDE SUBSTITUTION to a Save dialog's `-initialfile`. On Windows `~foo`
    names no user, so `tk_getSaveFile` raises

        TclError: user "-gbox_curio_wooden_col.png" doesn't exist

    BEFORE the dialog is ever displayed. In a --windowed build that exception had nowhere to
    surface, so Export looked like it silently did nothing -- which is exactly what was reported.
    """
    s = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)[:120]
    return s.lstrip('~_') or 'export'


def _run_selftest(quiet=False):
    """`WiiU_IPAK_Viewer.exe --selftest` -- prove THIS binary works, on a machine with no Python.

    A windowed build has no stdout, so the result is written to a log file next to the exe and
    also shown in a dialog. This is what verifies the frozen import path: the container and
    tiling backends live in ../wiiu_ref as source, and are compiled into the archive when
    packaged. If that wiring were wrong, this exits non-zero instead of failing later in
    someone's hands.
    """
    import io
    import contextlib
    from core import ipak_selftest

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print('%s %s gate battery' % (APP_TITLE, APP_VERSION))
        print('frozen: %s' % getattr(sys, 'frozen', False))
        print()
        failed = ipak_selftest.run()
    text = buf.getvalue()

    where = os.path.dirname(sys.executable if getattr(sys, 'frozen', False)
                            else os.path.abspath(__file__))
    log = os.path.join(where, 'ipak_selftest.log')
    try:
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
            root = tk.Tk()
            root.withdraw()
            (messagebox.showinfo if not failed else messagebox.showerror)(
                APP_TITLE, '%s\n\nWritten to:\n%s' % (text.strip()[-1500:], log))
            root.destroy()
        except Exception:
            pass
    return 1 if failed else 0


if __name__ == '__main__':
    args = [a for a in sys.argv[1:]]
    if '--selftest' in args:
        sys.exit(_run_selftest(quiet='--quiet' in args))
    args = [a for a in args if not a.startswith('--')]
    run_standalone(IpakIDE, initial=args[0] if args else None, title=APP_TITLE)
