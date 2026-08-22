"""Wii U FF IDE -- the asset browser + editor, driven entirely by `core`.

Supersedes `wiiu_ff_editor.py`. The difference that matters is the file list: the old editor
regex-scanned for `FFFFFFFF|len|FFFFFFFF` and could therefore only ever show ScriptParseTrees
and RawFiles. This one drives `core.assets.enumerate_zone()`, which is backed by the structural
walk that is 63/63 zones EOF-exact, so it shows EVERY asset with an exact span.

    python ff_ide.py [file.ff]

Panes
  left    asset tree, grouped by type; span, size and payload kind per asset
  right   editor (text) / bytecode info / hex view, depending on payload kind
  bottom  status + save plan + byte budget

Save goes through `ZoneSession.save()`, which plans, verifies and only then writes. A save that
would need a relink says so; a save that cannot be done soundly is refused with the reason.
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from core import ZoneSession          # noqa: E402
from core import relink as _relink    # noqa: E402,F401

from core.workspace import Workspace, run_standalone   # noqa: E402

APP_TITLE = "Wii U FF IDE"
APP_VERSION = "2.0"

# Kept in step with core/theme.py -- see the note there. Brighter text ramp and lifted
# hairlines for readability; the teal and the overall tone are unchanged.
BG, PANEL, EDIT = "#0f151d", "#171f2a", "#0d1219"
GUTTER, HEAD = "#111823", "#111823"
ACCENT, ACCENT2 = "#18b4a8", "#0e8f86"
TEXT, MUTED, BORDER = "#eef3f8", "#a8b4c4", "#2c3849"
OK, WARN, INFO = "#46cf88", "#f0736e", "#e8b552"

PAYLOAD_ICON = {'gsc': '{}', 'hks': '[]', 'text': 'Tx', 'binary': '..', 'opaque': '::'}


def hexdump(buf, base=0, width=16, maxlen=64 * 1024):
    out = []
    n = min(len(buf), maxlen)
    for i in range(0, n, width):
        chunk = buf[i:i + width]
        hexs = ' '.join('%02x' % c for c in chunk)
        text = ''.join(chr(c) if 0x20 <= c < 0x7f else '.' for c in chunk)
        out.append('%08X  %-*s  %s' % (base + i, width * 3 - 1, hexs, text))
    if len(buf) > n:
        out.append('... (%d more bytes)' % (len(buf) - n))
    return '\n'.join(out)


class IDE(Workspace):
    """The fastfile asset browser/editor. Hosted in the studio shell, or standalone."""

    kind = 'zone'

    def __init__(self, master=None, initial=None):
        super().__init__(master)
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.configure(bg=BG)

        self.session = None
        self.cur = None                 # current Asset
        self.node_asset = {}            # tree iid -> Asset
        self._q = queue.Queue()
        self._busy = False
        self._filter = tk.StringVar()

        self._style()
        self._menu()
        self._toolbar()
        self._body()
        self._statusbar()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain)
        self.bind_all("<Control-o>", lambda e: self._open())
        self.bind_all("<Control-s>", lambda e: self._save())
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
        fm.add_command(label="Open FF/zone...\tCtrl+O", command=self._open)
        fm.add_command(label="Save\tCtrl+S", command=self._save)
        fm.add_command(label="Save As...", command=lambda: self._save(save_as=True))
        fm.add_separator()
        fm.add_command(label="Export asset...", command=self._export)
        fm.add_command(label="Import into asset...", command=self._import)
        fm.add_command(label="Add file to zone...", command=self._add_script)
        fm.add_separator()
        fm.add_command(label="Exit", command=self._on_close)
        m.add_cascade(label="File", menu=fm)

        tm = tk.Menu(m, tearoff=0)
        tm.add_command(label="Textures in this zone...", command=self._zone_textures)
        tm.add_command(label="Sound banks in this zone...", command=self._zone_sounds)
        tm.add_separator()
        tm.add_command(label="Zone summary", command=self._show_summary)
        tm.add_command(label="Save plan (dry run)", command=self._show_plan)
        tm.add_command(label="Verify current zone", command=self._verify)
        tm.add_command(label="Relink census (dry run)", command=self._census)
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
        self.btn_export = ttk.Button(bar, text="Export", style="Ghost.TButton",
                                     command=self._export)
        self.btn_export.pack(side="left", padx=4, pady=7)
        self.btn_import = ttk.Button(bar, text="Import", style="Ghost.TButton",
                                     command=self._import)
        self.btn_import.pack(side="left", padx=4, pady=7)
        self.btn_add = ttk.Button(bar, text="Add file", style="Ghost.TButton",
                                  command=self._add_script)
        self.btn_add.pack(side="left", padx=4, pady=7)
        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8, padx=8)
        # These two browse whole categories that the asset tree cannot show -- images are
        # embedded in materials/FX rather than listed, and a SOUND asset is an alias table.
        # They were buried in the Tools menu, which is not where you look for a browser.
        self.btn_textures = ttk.Button(bar, text="Textures", style="Ghost.TButton",
                                       command=self._zone_textures)
        self.btn_textures.pack(side="left", padx=4, pady=7)
        self.btn_sounds = ttk.Button(bar, text="Sound banks", style="Ghost.TButton",
                                     command=self._zone_sounds)
        self.btn_sounds.pack(side="left", padx=4, pady=7)
        tk.Label(bar, text="  filter:", bg=HEAD, fg=MUTED).pack(side="left")
        ent = ttk.Entry(bar, textvariable=self._filter, width=22)
        ent.pack(side="left", padx=4)
        ent.bind("<KeyRelease>", lambda e: self._refill())
        self.title_lbl = tk.Label(bar, text="no fastfile open", bg=HEAD, fg=MUTED)
        self.title_lbl.pack(side="left", padx=14)
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

    def _body(self):
        pan = tk.PanedWindow(self, orient="horizontal", bg=BORDER, sashwidth=4, bd=0)
        pan.pack(fill="both", expand=True)

        left = tk.Frame(pan, bg=PANEL)
        self.count_lbl = tk.Label(left, text="  assets", bg=PANEL, fg=MUTED,
                                  font=("Segoe UI", 9), anchor="w")
        self.count_lbl.pack(fill="x", pady=(8, 4))
        tw = tk.Frame(left, bg=BORDER)
        tw.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.tree = ttk.Treeview(tw, style="Tree.Treeview", columns=("span", "size", "kind"),
                                 displaycolumns=("span", "size", "kind"), show="tree headings")
        self.tree.heading("#0", text="asset")
        self.tree.heading("span", text="span")
        self.tree.heading("size", text="bytes")
        self.tree.heading("kind", text="kind")
        self.tree.column("#0", width=290, stretch=True)
        self.tree.column("span", width=104, anchor="e", stretch=False)
        self.tree.column("size", width=76, anchor="e", stretch=False)
        self.tree.column("kind", width=48, anchor="center", stretch=False)
        sb = ttk.Scrollbar(tw, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        pan.add(left, minsize=300, width=530)

        right = tk.Frame(pan, bg=EDIT)
        self.path_lbl = tk.Label(right, text="select an asset", bg=EDIT, fg=MUTED,
                                 anchor="w", font=("Consolas", 9), justify="left")
        self.path_lbl.pack(fill="x", padx=8, pady=(6, 2))
        self.note_lbl = tk.Label(right, text="", bg=EDIT, fg=INFO, anchor="w",
                                 font=("Segoe UI", 9), wraplength=680, justify="left")
        self.note_lbl.pack(fill="x", padx=8, pady=(0, 4))
        self._build_script_bar(right)
        ew = tk.Frame(right, bg=EDIT)
        ew.pack(fill="both", expand=True)
        self.ew = ew
        self._build_model_pane(right)
        self.gutter = tk.Text(ew, width=6, bg=GUTTER, fg=MUTED, relief="flat", bd=0,
                              font=("Consolas", 10), padx=6, takefocus=0, state="disabled",
                              highlightthickness=0)
        self.gutter.pack(side="left", fill="y")
        self.txt = tk.Text(ew, bg=EDIT, fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0,
                           wrap="none", undo=True, font=("Consolas", 10), padx=8,
                           highlightthickness=0)
        vsb = ttk.Scrollbar(ew, command=self._yview)
        self.txt.configure(yscrollcommand=self._on_yscroll)
        vsb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.bind("<<Modified>>", self._on_modified)
        self.txt.bind("<KeyRelease>", self._on_key)
        pan.add(right)

    # ------------------------------------------------------------- script editing
    def _build_script_bar(self, parent):
        """Toolbar shown for compiled-script assets (GSC and Lua/HKS).

        There is a DISASSEMBLER but no decompiler, so the Source view starts empty: you paste or
        load source, compile it, and the compiled bytecode replaces the asset's payload. The
        Disassembly view is what the asset currently holds, and stays available for reference.
        """
        self.script_bar = tk.Frame(parent, bg=HEAD, height=34)
        self._script_mode = tk.StringVar(value="disasm")
        # BYTECODE is what the asset holds now (there is a disassembler but NO decompiler, so
        # it is the only truthful view of an existing script). SOURCE is new code you are
        # writing to replace it with, and starts empty for exactly that reason.
        self._mode_btns = {}
        for label, val in (("Bytecode (in the file)", "disasm"),
                           ("Assembly (editable)", "asm"),
                           ("Source to compile", "source")):
            rb = tk.Radiobutton(self.script_bar, text=label, value=val,
                                variable=self._script_mode,
                                command=self._script_mode_changed, bg=HEAD, fg=TEXT,
                                selectcolor=EDIT, activebackground=HEAD,
                                activeforeground=ACCENT,
                                font=("Segoe UI", 9), bd=0, highlightthickness=0)
            rb.pack(side="left", padx=2, pady=5)
            self._mode_btns[val] = rb
        # No "load source" button here: the main toolbar's Import takes a .gsc/.lua SOURCE file
        # for a script asset and compiles it, so there is one obvious way to bring a file in
        # rather than two that look alike.
        # Assemble & stage is the path that needs NO original source: the Assembly view is a
        # lossless rendering of the bytecode already in the file, so it can be edited and put
        # straight back (core.gsc_assembler; round-trip gate in core.gsc_asm_selftest).
        self.btn_assemble = ttk.Button(self.script_bar, text="Assemble && stage",
                                       style="Accent.TButton", command=self._assemble_stage)
        self.btn_assemble.pack(side="left", padx=(12, 3), pady=4)
        self.btn_compile = ttk.Button(self.script_bar, text="Compile && stage",
                                      style="Accent.TButton", command=self._compile_stage)
        self.btn_compile.pack(side="left", padx=(3, 3), pady=4)
        # Decompile fills the Source view FROM THE BYTECODE, so a script with no original
        # source can still be read and edited as GSC (core.gsc_decompile). Anything it
        # cannot structure comes back as a commented stub with the reason, never as source
        # that merely looks right.
        self.btn_decompile = ttk.Button(self.script_bar, text="Decompile",
                                        command=self._decompile_to_source)
        self.btn_decompile.pack(side="left", padx=(3, 3), pady=4)
        self.script_lbl = tk.Label(self.script_bar, text="", bg=HEAD, fg=MUTED,
                                   font=("Consolas", 9))
        self.script_lbl.pack(side="right", padx=10)
        self._src_cache = {}          # asset label -> source text being edited
        self._asm_cache = {}          # asset label -> assembly text being edited

    # ---------------------------------------------------------------- zone media
    def _zone_textures(self):
        """Browse, export and replace the images whose pixels live INSIDE this zone.

        These are not in the asset tree: a zone lists a handful of top-level IMAGE assets while
        carrying hundreds of image records embedded in Materials, FX and XModel-inline
        materials, so they are located structurally instead.
        """
        if not self.session:
            messagebox.showinfo(APP_TITLE, "Open a fastfile first.")
            return
        from core import zone_images as ZI
        from PIL import Image, ImageTk

        try:
            images = ZI.list_images(self.session.zone)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not scan this zone for images:\n\n%s: %s"
                                 % (type(ex).__name__, ex))
            return
        total, inline, streamed, other = ZI.summarise(images)

        win = tk.Toplevel(self)
        win.title("Textures in %s" % os.path.basename(self.session.ff_path or 'zone'))
        win.configure(bg=BG)
        win.geometry("1120x660")

        tk.Label(win, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 text=("%d image records: %d carry their pixels INLINE in this zone and can be "
                       "previewed and replaced here; %d are streamed stubs whose bytes live in "
                       "an .ipak (edit those in a texture pak); %d carry no pixels."
                       % (total, inline, streamed, other))).pack(fill="x", padx=12, pady=(12, 6))

        filt = tk.Frame(win, bg=BG)
        filt.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(filt, text="filter", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        fvar = tk.StringVar()
        ttk.Entry(filt, textvariable=fvar, width=34).pack(side="left")
        count_lbl = tk.Label(filt, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        count_lbl.pack(side="left", padx=10)

        # ⚠ ACTION BAR RESERVED BEFORE THE BODY, for the same pack-order reason as the scrollbar
        # below: whatever is packed after an expand=True widget is the first thing clipped when the
        # window is short. The frame is empty at this point, which is fine -- Tk recomputes its
        # requested height when the buttons are added further down, and because it is EARLIER in
        # pack order it keeps that height at any window size and any scroll position.
        bar = tk.Frame(win, bg=BG)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 12))

        body = tk.Frame(win, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=12, pady=6)
        win.minsize(720, 420)
        # ⚠ BUILD THE HOLDERS FIRST so the tree is a genuine child of `listw`. Creating it under
        # `body` and packing with in_=listw leaves it a sibling of `left` in the hierarchy, drawn
        # BEHIND it -- the list showed as an empty panel while every measurement said it was
        # 714x506 and mapped. Parent correctly rather than packing across the hierarchy.
        from core import gallery as GAL
        left = tk.Frame(body, bg=BG)
        listw = tk.Frame(left, bg=BG)
        cols = ("dims", "format", "mips", "bytes", "where")
        tv = ttk.Treeview(listw, columns=cols, style="Tree.Treeview", height=20)
        tv.column("#0", width=330)
        for c, w in zip(cols, (90, 70, 50, 90, 80)):
            tv.column(c, width=w, anchor="w")
        tvsb = ttk.Scrollbar(listw, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=tvsb.set)
        # ⚠ SCROLLBAR FIRST, AND side="right". Tk allocates space in PACK ORDER, so a fixed-width
        # scrollbar packed AFTER an expand=True Treeview is the first thing clipped when the window
        # is narrow -- the list scrolled but the bar was invisible until the user enlarged the
        # window. Packing it first reserves its width permanently and the tree gives way instead.
        tvsb.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=BG, width=380)
        right.pack(side="right", fill="y")
        left.pack(side="left", fill="both", expand=True)
        right.pack_propagate(False)
        canvas = tk.Label(right, bg=EDIT)
        canvas.pack(fill="both", expand=True, pady=(0, 6))
        info = tk.Label(right, bg=BG, fg=MUTED, font=("Consolas", 8), justify="left",
                        anchor="w", wraplength=360)
        # Pinned under the preview and reserved before it, so the dimensions/format line cannot be
        # squeezed out by the image when the window is short.
        info.pack(side="bottom", fill="x", before=canvas)

        from core import uiutil
        rows = {}
        state = {'cur': None, 'photo': None, 'owners': None, 'staged': {}}
        gal = None                 # built below; refill() must tolerate its absence
        from core import ipak_image as _II_disp     # emblem alpha-0 display rule, shared

        def _thumb(im):
            """⚠ WORKER THREAD -- no Tk here. Undecodable formats return None, never a fake tile."""
            try:
                from PIL import Image as _Im
                rgba, _lvl, _dims = ZI.decode(self.session.zone, im, max_side=128)
                from core import ipak_image as _II
                rgba, _n = _II.for_display(rgba)
                t = _Im.fromarray(rgba, 'RGBA')
                t.thumbnail((128, 128), _Im.LANCZOS)
                bg = _Im.new('RGBA', (128, 128), (18, 24, 32, 255))
                bg.paste(t, ((128 - t.width) // 2, (128 - t.height) // 2), t)
                return bg.convert('RGB')
            except Exception:
                return None

        def _gal_pick(im):
            state['cur'] = im
            for iid, row in rows.items():
                if row is im:
                    tv.selection_set(iid)
                    tv.see(iid)
                    break
            show()
        # Built once for the window: it is what turns "streamed" into the name of the pak the
        # pixels actually come from. Cached on disk, so this is fast after the first time.
        try:
            from core import ipak_search as _isearch
            state['owners'], _paks = _isearch.owner_map()
        except Exception:
            state['owners'] = {}

        def _where(im):
            if im.inline:
                return 'inline'
            try:
                srcs = ZI.streamed_sources(im, owners=state['owners'])
            except Exception:
                srcs = []
            names = []
            for _part, paks in srcs:
                for q in paks:
                    b = os.path.basename(q)
                    if b not in names:
                        names.append(b)
            if not names:
                return im.pixel_class
            return names[0] if len(names) == 1 else '%s +%d' % (names[0], len(names) - 1)

        def refill():
            tv.delete(*tv.get_children())
            rows.clear()
            q = fvar.get().strip().lower()
            shown = [im for im in images if not q or q in (im.label or '').lower()]
            shown = uiutil.sort_rows(shown, state)
            for i, im in enumerate(shown):
                rows[str(i)] = im
                tv.insert("", "end", iid=str(i), text=im.label[:60],
                          values=(im.dims, 'gx2 %#x' % (im.gx2_format or 0), im.levels,
                                  format(im.pixel_len or 0, ','), _where(im)))
            count_lbl.configure(text='%d of %d shown' % (len(shown), len(images)))
            if gal is not None:
                gal.set_items(shown)      # same filter, same order, one selection

        # Sort keys work on the MODEL, not the rendered text: 'bytes' is formatted with
        # separators, and sorting those as strings puts 9,999 after 10,000.
        uiutil.attach_sorting(tv, {
            '#0': ('image', lambda im: (im.label or '').lower()),
            'dims': ('dims', lambda im: im.width * im.height),
            'format': ('format', lambda im: im.gx2_format or 0),
            'mips': ('mips', lambda im: im.levels or 0),
            'bytes': ('bytes', lambda im: im.pixel_len or 0),
            'where': ('where', lambda im: _where(im) or ''),
        }, refill, state)
        fvar.trace_add("write", lambda *_a: refill())
        refill()

        def show(_e=None):
            sel = tv.selection()
            if not sel:
                return
            im = rows[sel[0]]
            state['cur'] = im
            info.configure(text='%s\n%s  levels=%s  %s\npixels @0x%X  %s bytes'
                                % (im.label, im.dims, im.levels,
                                   'inline' if im.inline else im.pixel_class,
                                   im.pixel_off or 0, format(im.pixel_len or 0, ',')))
            canvas.configure(image='', text='')
            state['photo'] = None
            if not im.inline:
                srcs = []
                try:
                    srcs = ZI.streamed_sources(im, owners=state.get('owners'))
                except Exception:
                    srcs = []
                if srcs:
                    txt = ['streamed - the pixels live in a texture pak:']
                    for part, paks in srcs:
                        if paks:
                            for q in paks:
                                txt.append('  part %d   %s' % (part, os.path.basename(q)))
                        else:
                            txt.append('  part %d   (no pak the tool can see carries it)' % part)
                    if any(len(p) > 1 for _i, p in srcs):
                        txt.append('')
                        txt.append('more than one pak has a part: the earliest-loaded wins')
                    info.configure(text=info.cget('text') + '\n' + '\n'.join(txt))
                canvas.configure(text="streamed\n(open that .ipak to edit it)",
                                 fg=MUTED, font=("Segoe UI", 9))
                return
            try:
                # ⚠ PREVIEW THE STAGED BYTES, NOT THE FILE. A replace is held in session._raw
                # until Save, so decoding straight from `zone` showed the ORIGINAL image and made
                # a successful replace look like it did nothing. The patched view is built only
                # when something is staged, and cached so repeated clicks do not re-copy the zone.
                zbytes = self.session.zone
                staged = getattr(self.session, '_raw', None) or {}
                if staged:
                    if state.get('_pv_gen') != len(staged):
                        zb = bytearray(self.session.zone)
                        for off, data in staged.items():
                            zb[off:off + len(data)] = data
                        state['_pv'] = bytes(zb)
                        state['_pv_gen'] = len(staged)
                    zbytes = state['_pv']
                rgba, lvl, dims = ZI.decode(zbytes, im, max_side=340)
                rgba, _dn = _II_disp.for_display(rgba)
                pim = Image.fromarray(rgba, 'RGBA')
                state['photo'] = ImageTk.PhotoImage(pim, master=win)
                canvas.configure(image=state['photo'])
                info.configure(text=info.cget('text') + '\npreviewing level %d (%dx%d)'
                               % (lvl, dims[0], dims[1]))
            except Exception as ex:
                canvas.configure(text="could not decode:\n%s: %s" % (type(ex).__name__, ex),
                                 fg=WARN, font=("Segoe UI", 9))

        def export():
            im = state['cur']
            if not im or not im.inline:
                return
            p = filedialog.asksaveasfilename(
                title="Export texture", defaultextension=".png",
                initialfile=self._safe_name(im.label) + ".png",
                filetypes=[("PNG", "*.png")])
            if not p:
                return
            try:
                ZI.export_png(self.session.zone, im, p)
                self._say("exported %s" % os.path.basename(p), OK)
            except Exception as ex:
                messagebox.showerror(APP_TITLE, "Export failed:\n\n%s: %s"
                                     % (type(ex).__name__, ex))

        def replace():
            im = state['cur']
            if not im or not im.inline:
                return
            p = filedialog.askopenfilename(
                title="Replace with image...",
                filetypes=[("Images", "*.png;*.dds;*.tga;*.bmp;*.jpg;*.jpeg;*.gif;*.tif;*.tiff;*.webp"), ("All", "*.*")])
            if not p:
                return
            try:
                import numpy as _np
                src = Image.open(p).convert("RGBA")
                arr = _np.asarray(src, _np.uint8)
                # What sizes can this picture actually be stored at? The enhanced one is
                # whatever the SOURCE supports, capped at 2x, and preferentially one that fits
                # the existing allocation -- see core.zone_images.resize_choices.
                choices = ZI.resize_choices(im, src.width, src.height)
                pick = choices[0]
                if len(choices) > 1:
                    pick = self._ask_texture_size(im, src.width, src.height, choices)
                    if pick is None:
                        return
                if pick.width == im.width and pick.height == im.height:
                    ZI.replace(self.session, im, arr)
                else:
                    ZI.stage_resize(self.session, im, arr, pick.width, pick.height)
            except Exception as ex:
                messagebox.showerror(APP_TITLE, "Replace refused:\n\n%s: %s"
                                     % (type(ex).__name__, ex))
                return
            self._say("staged %s -- not written until you Save" % im.label, OK)
            # ⚠ SHOW THE NEW PIXELS, like the ipak viewer does. The staged bytes live in the
            # session, not in `zone`, so the preview decoded straight from `zone` kept showing the
            # ORIGINAL -- which reads as "the replace silently failed" when it actually worked.
            state['staged'][im.off] = True
            undo_btn.configure(state="normal")
            state['_pv'] = None
            state['_pv_gen'] = None
            show()
            grew = (pick.width, pick.height) != (im.width, im.height)
            messagebox.showinfo(
                APP_TITLE,
                "Staged a replacement for %s.\n\n%s\n\nSave the fastfile to write it. Note that "
                "a length-neutral image patch CANNOT be combined with a growing script edit -- "
                "save this first, then reopen for any edit that changes a size."
                % (im.label,
                   ("Stored at %dx%d instead of %s -- %.2fx larger, in the SAME %s bytes the "
                    "texture already occupied, so the zone's layout is unchanged."
                    % (pick.width, pick.height, im.dims, pick.factor,
                       format(im.pixel_len, ',')))
                   if grew else
                   ("Re-encoded to the image's existing format and dimensions (%s), occupying "
                    "exactly the same %s bytes, so the zone's layout is unchanged."
                    % (im.dims, format(im.pixel_len, ',')))))
            show()

        # `bar` itself is created and packed ABOVE, before the body, so it can never be clipped.
        ttk.Button(bar, text="Export PNG", style="Ghost.TButton", command=export)\
            .pack(side="left")
        ttk.Button(bar, text="Replace...", style="Accent.TButton", command=replace)\
            .pack(side="left", padx=6)

        def undo_staged():
            """Take back staged replacements. Nothing has been written, so this is a true undo."""
            n = self.session.unstage_raw()
            state['staged'].clear()
            state['_pv'] = None
            state['_pv_gen'] = None
            undo_btn.configure(state="disabled")
            self._say("undid %d staged image replacement(s)" % n, OK)
            show()

        undo_btn = ttk.Button(bar, text="Undo staged", style="Ghost.TButton",
                              state="normal" if getattr(self.session, '_raw', None) else "disabled",
                              command=undo_staged)
        undo_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")
        tv.bind("<<TreeviewSelect>>", show)

        gal = GAL.Gallery(left, key=lambda im: im.off, label=lambda im: im.label or '?',
                          thumbnail=_thumb, on_select=_gal_pick, on_activate=_gal_pick)
        from core import settings as _st
        ctl = tk.Frame(left, bg=BG)
        ctl.pack(side="bottom", fill="x", pady=(4, 0))
        view = GAL.ViewSwitch(ctl, listw, gal,
                              on_change=lambda m: _st.set_view('zone', m))
        view.pack(side="left", anchor="w")
        tk.Label(ctl, text="size", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")
        tpx = tk.IntVar(value=128)
        tk.Scale(ctl, from_=64, to=256, resolution=32, orient="horizontal", showvalue=False,
                 variable=tpx, length=110, bg=BG, fg=MUTED, highlightthickness=0,
                 troughcolor=EDIT, bd=0,
                 command=lambda _v: gal.set_thumb_px(tpx.get())).pack(side="right", padx=(0, 8))
        listw.pack(fill="both", expand=True)
        refill()                      # populate BOTH views now that the gallery exists
        try:
            view.show(_st.get_view('zone', 'list'))
        except Exception:
            pass

    def _zone_sounds(self):
        """Show the SndBank alias tables carried by this zone.

        ⚠ There is no audio in a fastfile. A SOUND asset is a ~4.8 KB SndBank: the alias table
        pairing each entry id to its source filename, plus the bank's name. The waveforms are in
        the .sabs/.sabl, which the sound bank editor opens. This view exists because that table
        is the ONLY source of entry names -- a bank file stores ids and nothing else.
        """
        if not self.session:
            messagebox.showinfo(APP_TITLE, "Open a fastfile first.")
            return
        from core import zone_sounds as ZS
        banks = ZS.list_banks(self.session.zone)

        win = tk.Toplevel(self)
        win.title("Sound banks in %s" % os.path.basename(self.session.ff_path or 'zone'))
        win.configure(bg=BG)
        win.geometry("980x600")
        tk.Label(win, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=940,
                 text=("%d sound bank(s) in this zone. A fastfile carries NO audio -- this is "
                       "the alias table that gives each sound entry its name. The waveforms "
                       "live in the matching .sabs/.sabl, which you can open from here."
                       % len(banks))).pack(fill="x", padx=12, pady=(12, 6))

        sfilt = tk.Frame(win, bg=BG)
        sfilt.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(sfilt, text="filter", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        sfvar = tk.StringVar()
        ttk.Entry(sfilt, textvariable=sfvar, width=34).pack(side="left")
        scount = tk.Label(sfilt, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        scount.pack(side="left", padx=10)

        sbody = tk.Frame(win, bg=BG)
        sbody.pack(fill="both", expand=True, padx=12, pady=6)
        tv = ttk.Treeview(sbody, columns=("id", "file"), style="Tree.Treeview", height=22)
        tv.heading("#0", text="bank")
        tv.column("#0", width=280)
        tv.heading("id", text="assetId")
        tv.column("id", width=110)
        tv.heading("file", text="source filename")
        tv.column("file", width=520)
        ssb = ttk.Scrollbar(sbody, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")     # reserved BEFORE the tree -- see the note in
        tv.pack(side="left", fill="both", expand=True)   # _zone_textures on pack order

        def sfill():
            tv.delete(*tv.get_children())
            q = sfvar.get().strip().lower()
            total = shown = 0
            for bnk in banks:
                pairs = [(a, f) for a, f in bnk.aliases
                         if not q or q in (f or '').lower() or q in ('%08x' % a)]
                total += len(bnk.aliases)
                shown += len(pairs)
                if q and not pairs:
                    continue
                top = tv.insert("", "end", values=("", ""), open=bool(q),
                                text='%s  (%d aliases)' % (bnk.label, len(bnk.aliases)))
                for aid, fname in pairs[:4000]:
                    tv.insert(top, "end", text="", values=('%08X' % aid, fname))
                for p in ZS.bank_files(bnk):
                    cov = ZS.coverage(bnk, p)
                    tv.insert(top, "end", text="  bank file",
                              values=('%d/%d named' % cov if cov else '', p))
            scount.configure(text='%d of %d aliases shown' % (shown, total))

        sfvar.trace_add("write", lambda *_a: sfill())
        sfill()

        def open_bank():
            sel = tv.selection()
            if not sel:
                return
            vals = tv.item(sel[0], 'values')
            p = vals[1] if len(vals) > 1 else ''
            if p and os.path.isfile(p):
                win.destroy()
                shell = self.winfo_toplevel()
                if hasattr(shell, 'open_file'):
                    shell.open_file(p)        # in the studio: opens as its own tab
                else:
                    messagebox.showinfo(APP_TITLE, "Open this in the sound bank editor:\n\n%s"
                                        % p)

        bar = tk.Frame(win, bg=BG)
        # `before=sbody` puts this EARLIER in the pack order without moving the code. Tk allocates
        # space in pack order, so a footer packed after an expand=True body is starved first and the
        # buttons disappear until the window is enlarged.
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 12), before=sbody)
        ttk.Button(bar, text="Open selected bank file", style="Accent.TButton",
                   command=open_bank).pack(side="left")
        ttk.Button(bar, text="Close", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")

    def _update_actions(self):
        """Enable only the actions that can actually work on the current selection.

        A button that is clickable but always errors is worse than one that is greyed out: the
        user cannot tell "not supported here" from "broken". Import is refused for any asset with
        no modelled payload range (every `opaque` type, and the read-only ones), because we
        cannot say where its bytes begin and end.
        """
        has_session = self.session is not None
        a = self.cur
        state = lambda ok: ("normal" if ok else "disabled")           # noqa: E731

        self.btn_add.configure(state=state(has_session))
        for b in ('btn_textures', 'btn_sounds'):
            if hasattr(self, b):
                getattr(self, b).configure(state=state(has_session))
        self.btn_export.configure(state=state(has_session and a is not None))
        self.btn_import.configure(state=state(has_session and a is not None and a.editable))

        is_script = bool(a) and a.payload in ('gsc', 'hks')
        is_gsc = bool(a) and a.payload == 'gsc'
        if hasattr(self, 'btn_decompile'):
            self.btn_decompile.configure(state=state(bool(a) and a.payload == 'gsc'))
        if hasattr(self, 'btn_compile'):
            self.btn_compile.configure(
                state=state(is_script and self._script_mode.get() == 'source'))
        if hasattr(self, 'btn_assemble'):
            self.btn_assemble.configure(
                state=state(is_gsc and self._script_mode.get() == 'asm'))
        # There is no HavokScript assembler, so the Assembly view is offered only for GSC
        # rather than being offered and then failing.
        if hasattr(self, '_mode_btns'):
            self._mode_btns['asm'].configure(state=state(is_gsc))
            if not is_gsc and self._script_mode.get() == 'asm':
                self._script_mode.set('disasm')

        # Keep the File menu honest too, so the same rule holds wherever the action is reached.
        try:
            fm = self.nametowidget(self.cget('menu')).nametowidget('!menu')
        except Exception:
            fm = None
        if fm is not None:
            for label, ok in (("Export asset...", has_session and a is not None),
                              ("Import into asset...", has_session and a is not None
                               and a.editable),
                              ("Add file to zone...", has_session)):
                try:
                    fm.entryconfigure(label, state=state(ok))
                except Exception:
                    pass

    def _show_script_bar(self, on):
        if on:
            self.script_bar.pack(fill="x", before=self.ew)
        else:
            self.script_bar.pack_forget()

    def _script_mode_changed(self):
        if self.cur is not None:
            self._on_pick()
        self._update_actions()

    def _load_source(self):
        if not self.cur:
            return
        kind = self.cur.payload
        types = ([("GSC source", "*.gsc"), ("All files", "*.*")] if kind == 'gsc'
                 else [("Lua source", "*.lua"), ("All files", "*.*")])
        p = filedialog.askopenfilename(title="Load source", filetypes=types)
        if not p:
            return
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        self._src_cache[self.cur.label] = src
        self._script_mode.set("source")
        self._on_pick()
        self.status.config(text="Loaded %s (%d chars). Compile & stage to apply."
                                % (os.path.basename(p), len(src)), fg=MUTED)

    def _assemble_stage(self):
        """Assemble the edited Assembly view and stage it. No original source involved."""
        if not self.cur or not self.session:
            return
        a = self.cur
        if a.payload != 'gsc':
            messagebox.showinfo(APP_TITLE, "%s is not compiled GSC. The assembler only "
                                           "handles GSC." % a.label)
            return
        if self._script_mode.get() != "asm":
            self._script_mode.set("asm")
            self._on_pick()
        txt = self.txt.get("1.0", "end-1c")
        self._asm_cache[a.label] = txt
        try:
            from core import scripts as SC
            blob, delta = SC.stage_assembled(self.session, a, txt)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Assemble failed:\n\n%s" % ex)
            self.script_lbl.config(text="assemble failed", fg=WARN)
            return
        self.script_lbl.config(text="staged %s B (%+d)" % (format(len(blob), ","), delta),
                               fg=OK)
        self._mark_dirty()
        self.status.config(
            text="Assembled %s -> %s bytes (%+d). %s Save to write."
                 % (a.name, format(len(blob), ","), delta,
                    "Length-neutral." if delta == 0 else "Growth: the zone will be relinked."),
            fg=OK)

    def _decompile_to_source(self):
        """Reconstruct GSC source from the asset's bytecode and show it in the Source view."""
        if not self.cur or not self.session:
            return
        a = self.cur
        if a.payload != 'gsc':
            messagebox.showinfo(APP_TITLE, "%s is not compiled GSC." % a.label)
            return
        from core import scripts as SC
        try:
            src, rep = SC.decompile_source(a, a.extract(self.session.zone))
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Decompile failed:\n\n%s: %s"
                                            % (type(ex).__name__, ex))
            self.script_lbl.config(text="decompile failed", fg=WARN)
            return
        self._src_cache[a.label] = src
        self._script_mode.set("source")
        self._on_pick()
        n, ok = rep['functions'], rep['ok']
        refused = n - ok
        self.script_lbl.config(
            text="%d/%d functions" % (ok, n), fg=OK if not refused else WARN)
        self.status.config(
            text="Decompiled %s: %d of %d functions reconstructed%s"
                 % (a.name, ok, n,
                    "" if not refused
                    else "; %d refused and left as commented stubs (reason inline)" % refused),
            fg=OK if not refused else WARN)

    def _compile_stage(self):
        if not self.cur or not self.session:
            return
        a = self.cur
        if a.payload not in ('gsc', 'hks'):
            messagebox.showinfo(APP_TITLE, "%s is not a compiled script." % a.label)
            return
        if self._script_mode.get() != "source":
            self._script_mode.set("source")
            self._on_pick()
        src = self.txt.get("1.0", "end-1c")
        if not src.strip():
            messagebox.showinfo(APP_TITLE,
                                "The Source view is empty.\n\nThere is no decompiler: paste or "
                                "Load source, then Compile & stage.")
            return
        self._src_cache[a.label] = src
        try:
            from core import scripts as SC
            blob, delta = SC.stage_compiled(self.session, a, src)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Compile failed:\n\n%s: %s" % (type(ex).__name__, ex))
            self.script_lbl.config(text="compile failed", fg=WARN)
            return
        self.script_lbl.config(text="staged %s B (%+d)" % (format(len(blob), ","), delta),
                               fg=OK)
        self._mark_dirty()
        self.status.config(
            text="Compiled %s -> %s bytes (%+d). %s Save to write."
                 % (a.name, format(len(blob), ","), delta,
                    "Length-neutral." if delta == 0 else "Growth: the zone will be relinked."),
            fg=OK)

    def _add_script(self):
        """Add ANY file to the zone: .gsc/.csc and .lua are compiled, everything else is stored
        verbatim as a RawFile (.cfg, .csv, .txt, .bin, ...)."""
        if not self.session:
            return
        from core import scripts as SC
        okay, why = SC.can_add(self.session)
        if not okay:
            messagebox.showerror(APP_TITLE, "Cannot add an asset to this zone:\n\n%s" % why)
            return
        p = filedialog.askopenfilename(
            title="Add a file to this zone",
            filetypes=[("Scripts and data", "*.gsc *.csc *.lua *.cfg *.csv *.txt *.bin"),
                       ("GSC", "*.gsc *.csc"), ("Lua", "*.lua"),
                       ("Config / data", "*.cfg *.csv *.txt"), ("All files", "*.*")])
        if not p:
            return
        ext = os.path.splitext(p)[1].lower()
        base = os.path.basename(p)
        if ext in ('.gsc', '.csc'):
            default, kindtxt = 'maps/mp/gametypes/%s' % base, 'GSC (compiled)'
        elif ext == '.lua':
            default, kindtxt = 'ui/t6/%s' % base, 'Lua (compiled to HKS)'
        else:
            default, kindtxt = base, 'RawFile (stored verbatim)'
        name = simpledialog.askstring(
            APP_TITLE,
            "Asset name for the new file.  [%s]\n\n"
            "This is the name the zone will know it by, and what callers must reference.\n"
            "A newly added asset is INERT until something reads it." % kindtxt,
            initialvalue=default, parent=self)
        if not name:
            return
        with open(p, "rb") as f:
            data = f.read()
        try:
            add = SC.add_any(self.session, name, data, path_hint=p)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not add:\n\n%s: %s" % (type(ex).__name__, ex))
            return
        self._mark_dirty()
        self.status.config(text="Queued %s as %r (%s bytes). Nothing reads it yet. Save to write."
                                % (kindtxt, name, format(len(add.payload), ",")), fg=OK)

    # ------------------------------------------------------------- 3D model pane
    def _build_model_pane(self, parent):
        """The XModel preview: a rotatable software render.

        Geometry for an XModel is inline in the zone (unlike image pixels, which stream from an
        .ipak), so this needs nothing on disk beyond the open fastfile.
        """
        self.m3d = tk.Frame(parent, bg=EDIT)          # packed on demand, in place of `ew`

        bar = tk.Frame(self.m3d, bg=HEAD, height=32)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._m3d_mode = tk.StringVar(value="solid")
        for label, val in (("Solid", "solid"), ("Wire", "wire"), ("Points", "points")):
            tk.Radiobutton(bar, text=label, value=val, variable=self._m3d_mode,
                           command=lambda: self._draw3d(), bg=HEAD, fg=TEXT,
                           selectcolor=EDIT, activebackground=HEAD, activeforeground=ACCENT,
                           font=("Segoe UI", 9), bd=0, highlightthickness=0)\
                .pack(side="left", padx=2, pady=4)
        tk.Label(bar, text="  LOD", bg=HEAD, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self._m3d_lod = tk.StringVar(value="0")
        self.lod_box = ttk.Combobox(bar, textvariable=self._m3d_lod, width=4, state="readonly",
                                    values=["0"])
        self.lod_box.pack(side="left", padx=4)
        self.lod_box.bind("<<ComboboxSelected>>", lambda e: self._draw3d(reframe=True))
        ttk.Button(bar, text="Reset view", style="Ghost.TButton",
                   command=self._reset_view3d).pack(side="left", padx=6)
        self.m3d_info = tk.Label(bar, text="", bg=HEAD, fg=MUTED, font=("Consolas", 9))
        self.m3d_info.pack(side="right", padx=10)

        self.canvas3d = tk.Canvas(self.m3d, bg=EDIT, highlightthickness=0, bd=0)
        self.canvas3d.pack(fill="both", expand=True)
        self.canvas3d.bind("<Configure>", lambda e: self._draw3d())
        self.canvas3d.bind("<ButtonPress-1>", self._m3d_down)
        self.canvas3d.bind("<B1-Motion>", self._m3d_drag)
        self.canvas3d.bind("<ButtonRelease-1>", self._m3d_up)
        self.canvas3d.bind("<ButtonPress-3>", self._m3d_down)
        self.canvas3d.bind("<B3-Motion>", self._m3d_pan)
        self.canvas3d.bind("<ButtonRelease-3>", self._m3d_up)
        self.canvas3d.bind("<MouseWheel>", self._m3d_wheel)

        from core import render3d as R3
        self.view3d = R3.View()
        self.model3d = None
        self._m3d_photo = None
        self._m3d_last = (0, 0)
        self._m3d_interacting = False

    def _show_model_pane(self, on):
        if on:
            self.ew.pack_forget()
            self.m3d.pack(fill="both", expand=True)
        else:
            self.m3d.pack_forget()
            self.ew.pack(fill="both", expand=True)

    def _reset_view3d(self):
        self.view3d.reset()
        self._draw3d()

    def _m3d_down(self, e):
        self._m3d_last = (e.x, e.y)
        self._m3d_interacting = True

    def _m3d_up(self, _e):
        self._m3d_interacting = False
        self._draw3d()                     # final full-detail frame

    def _m3d_drag(self, e):
        dx, dy = e.x - self._m3d_last[0], e.y - self._m3d_last[1]
        self._m3d_last = (e.x, e.y)
        self.view3d.orbit(dx * 0.5, -dy * 0.5)
        self._draw3d()

    def _m3d_pan(self, e):
        dx, dy = e.x - self._m3d_last[0], e.y - self._m3d_last[1]
        self._m3d_last = (e.x, e.y)
        self.view3d.pan(dx, dy)
        self._draw3d()

    def _m3d_wheel(self, e):
        self.view3d.dolly(1.12 if e.delta > 0 else 1 / 1.12)
        self._draw3d()

    def _load_model(self, asset):
        """Parse the selected XModel. Returns True when there is something to draw."""
        from core import xmodel as XM
        try:
            m = XM.parse(self.session.zone, asset.start)
        except Exception as ex:
            self.model3d = None
            self._m3d_error = '%s: %s' % (type(ex).__name__, ex)
            return False
        if m.name is None:
            m.name = asset.name
        self.model3d = m
        self._m3d_error = None
        n = max(1, m.lod_count)
        self.lod_box.configure(values=[str(i) for i in range(n)])
        self._m3d_lod.set("0")
        self.view3d.reset()
        self._m3d_centre = self._m3d_radius = None
        return m.renderable

    def _draw3d(self, reframe=False):
        if not getattr(self, 'canvas3d', None) or self.model3d is None:
            return
        from core import render3d as R3
        from PIL import ImageTk
        import numpy as np

        W = max(64, self.canvas3d.winfo_width())
        H = max(64, self.canvas3d.winfo_height())
        try:
            lod = int(self._m3d_lod.get())
        except ValueError:
            lod = 0
        P, T = self.model3d.geometry(lod)
        self.canvas3d.delete("all")
        if len(T) == 0:
            msg = self._m3d_error or "this XModel carries no drawable geometry"
            for note in self.model3d.notes[:2]:
                msg += "\n" + note
            self.canvas3d.create_text(W // 2, H // 2, fill=MUTED, width=W - 60,
                                      justify="center", font=("Segoe UI", 10), text=msg)
            self.m3d_info.config(text="")
            return

        # Frame on LOD 0 so switching LOD does not make the model jump around.
        if reframe or self._m3d_centre is None:
            P0 = self.model3d.geometry(0)[0]
            ref = P0 if len(P0) else P
            self._m3d_centre = (ref.min(0) + ref.max(0)) * 0.5
            self._m3d_radius = float(np.abs(ref - self._m3d_centre).max()) or 1.0

        # Heavy models get a reduced triangle budget WHILE dragging, full detail on release.
        budget = 6000 if (self._m3d_interacting and len(T) > 6000) else None
        img = R3.render(P, T, self.view3d, W, H, mode=self._m3d_mode.get(),
                        budget=budget, centre=self._m3d_centre, radius=self._m3d_radius)
        self._m3d_photo = ImageTk.PhotoImage(img)
        self.canvas3d.create_image(W // 2, H // 2, image=self._m3d_photo)

        m = self.model3d
        size = (np.asarray(m.maxs) - np.asarray(m.mins)) if m.maxs else None
        txt = "%s verts  %s tris  LOD %d/%d" % (format(len(P), ","), format(len(T), ","),
                                                lod, max(1, m.lod_count))
        if size is not None:
            txt += "   %.0f x %.0f x %.0f" % tuple(size)
        if budget:
            txt += "   (reduced while dragging)"
        self.m3d_info.config(text=txt)

    def _statusbar(self):
        bar = tk.Frame(self, bg=HEAD, height=26)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="Open a .ff or .zone to begin", bg=HEAD, fg=MUTED,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=10)
        self.budget = tk.Label(bar, text="", bg=HEAD, fg=MUTED, font=("Consolas", 9))
        self.budget.pack(side="right", padx=10)
        st = ttk.Style(self)
        st.configure("FF.Horizontal.TProgressbar", background=ACCENT, troughcolor=HEAD,
                     borderwidth=0)
        self.pbar = ttk.Progressbar(bar, style="FF.Horizontal.TProgressbar", length=170,
                                    mode="determinate", maximum=1000)
        self.pbar.pack(side="right", padx=10, pady=4)

    def _ask_texture_size(self, im, src_w, src_h, choices):
        """Store the picture at the texture's own size, or at the larger size it supports?

        Returns the chosen SizeChoice, or None if the user cancelled.

        ⚠ Buttons are packed side="bottom" BEFORE the body. Tk allocates space in PACK ORDER,
        so anything packed after an expanding sibling is starved first and the buttons vanish
        off the bottom at small window sizes -- the exact defect fixed in the About window.
        """
        orig = choices[0]
        big = choices[-1]
        win = tk.Toplevel(self)
        win.title("Replacement size")
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        win.configure(bg=BG)
        result = {'pick': None}

        def choose(c):
            result['pick'] = c
            win.destroy()

        btns = tk.Frame(win, bg=BG)
        btns.pack(side="bottom", fill="x", padx=14, pady=(4, 12))
        ttk.Button(btns, text="Cancel", style="Ghost.TButton",
                   command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Keep %s" % orig.dims, style="Ghost.TButton",
                   command=lambda: choose(orig)).pack(side="right", padx=6)
        ttk.Button(btns, text="Use %s" % big.dims, style="Accent.TButton",
                   command=lambda: choose(big)).pack(side="right")

        body = tk.Frame(win, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=14, pady=(14, 6))
        tk.Label(body, text=im.label, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 11),
                 anchor="w").pack(fill="x")
        tk.Label(body, text="Your picture is %dx%d; the texture is %s."
                            % (src_w, src_h, orig.dims),
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(2, 10))

        cost = ("no extra bytes -- it fits the space the texture already occupies"
                if big.delta == 0 else
                "%s extra bytes, which needs the zone to grow" % format(big.delta, ','))
        for head, txt in (
            ("Keep %s" % orig.dims,
             "Downscale the picture to the texture's current size."),
            ("Use %s  (%.2fx)" % (big.dims, big.factor),
             "Store it at the larger size -- %s." % cost),
        ):
            tk.Label(body, text=head, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 9),
                     anchor="w").pack(fill="x", pady=(6, 0))
            tk.Label(body, text=txt, bg=BG, fg=MUTED, font=("Segoe UI", 9),
                     anchor="w", justify="left", wraplength=380).pack(fill="x")

        win.update_idletasks()
        win.minsize(420, win.winfo_reqheight())
        try:
            r = self.winfo_toplevel()
            win.geometry('+%d+%d' % (r.winfo_rootx() + 90, r.winfo_rooty() + 90))
        except Exception:
            pass
        win.grab_set()
        win.wait_window()
        return result['pick']

    def _say(self, msg, colour=MUTED):
        """Status-bar line. Same signature as ipak_ide._say, deliberately.

        ⚠ THIS WAS CALLED IN THREE PLACES AND NEVER DEFINED. The zone-image window used
        `self._say(...)` (export, staged replace, undo staged) while the rest of this class writes
        `self.status.config(...)`, so every one of those three raised AttributeError. Two of them
        were worse than they looked:
          * export: the call sits INSIDE the try, so export_png had already written the PNG. The
            AttributeError was then caught and reported to the user as "Export failed", which is
            the opposite of what happened.
          * replace: the call sits AFTER the try, so the bytes were staged but the exception
            escaped before `undo_btn` was enabled and the preview refreshed, leaving a staged edit
            the window did not admit to.
        Defining the method once is the fix; rewriting three call sites would leave the next
        `_say` to fail the same way.
        """
        self.status.config(text=msg, fg=colour)
        self.update_idletasks()

    # ------------------------------------------------------------------ plumbing
    def _yview(self, *a):
        self.txt.yview(*a)
        self.gutter.yview(*a)

    def _on_yscroll(self, lo, hi):
        self.gutter.yview_moveto(lo)
        return lo, hi

    def _refresh_gutter(self):
        n = int(self.txt.index("end-1c").split(".")[0])
        self.gutter.config(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, n + 1)))
        self.gutter.config(state="disabled")
        self.gutter.yview_moveto(self.txt.yview()[0])

    def _on_modified(self, _e=None):
        if self.txt.edit_modified():
            self.txt.edit_modified(False)
            self._refresh_gutter()

    def _on_key(self, _e=None):
        a = self.cur
        if a is None or not self.session or a.payload not in ('text', 'gsc', 'hks'):
            return
        if a.payload != 'text':
            return
        data = self.txt.get("1.0", "end-1c").encode("latin1", "replace")
        try:
            self.session.stage(a, data)
        except ValueError:
            return
        d = len(data) - a.buf_len
        self.budget.config(
            text="%d / %d bytes  (%+d)" % (len(data), a.buf_len, d),
            fg=MUTED if d == 0 else INFO)
        self._mark_dirty()

    def _mark_dirty(self):
        if not self.session:
            return
        base = os.path.basename(self.session.ff_path or self.session.name)
        dot = "* " if self.session.dirty else ""
        self.title_lbl.config(text="%s%s   delta %+d" % (dot, base, self.session.total_delta))

    # ------------------------------------------------------------------ open
    def _open(self, path=None):
        if self._busy:
            return
        if self.session and self.session.dirty and \
                not messagebox.askyesno(APP_TITLE, "Discard unsaved edits?"):
            return
        p = path or filedialog.askopenfilename(
            filetypes=[("Fastfile / zone", "*.ff *.zone"), ("All files", "*.*")])
        if not p:
            return
        self._busy = True
        self._phase = None
        self._busy_path = p
        self.pbar["value"] = 0
        self.status.config(text="Opening %s ..." % os.path.basename(p))
        threading.Thread(target=self._worker_open, args=(p,), daemon=True).start()

    def _worker_open(self, p):
        try:
            s = ZoneSession.open(
                p, progress=lambda f, label: self._q.put(("progress", f, label)))
            self._q.put(("opened", s))
        except Exception as ex:
            self._q.put(("err", "%s: %s" % (type(ex).__name__, ex)))

    def _apply_open(self, s):
        self.session = s
        self.cur = None
        # ⚠ TELL THE SHELL FIRST. This method used to swap the session and refill the list
        # without announcing anything, so the tab caption, the window title, the recent list and
        # the shell's status line all kept naming the PREVIOUS file while this one was on screen.
        self.set_document(getattr(s, 'ff_path', None))
        self.title("%s -- %s" % (APP_TITLE, os.path.basename(getattr(s, 'ff_path', '') or s.name)))
        self._refill()
        e = s.enumeration
        bits = ["%d assets" % len(s.assets), "%s B" % format(len(s.zone), ",")]
        if e.walk_ok:
            bits.append("walk EOF-exact")
        else:
            bits.append("WALK INCOMPLETE: %s" % e.walk_verdict)
        if e.fallback:
            bits.append("REGEX FALLBACK IN USE -- list is a subset")
        self.status.config(text="  |  ".join(bits))
        self._mark_dirty()

    def _refill(self):
        self.tree.delete(*self.tree.get_children())
        self.node_asset.clear()
        if not self.session:
            return
        needle = self._filter.get().strip().lower()
        groups = {}
        shown = 0
        for a in self.session.assets:
            if needle and needle not in (a.label or '').lower() \
                    and needle not in a.type_name.lower():
                continue
            groups.setdefault(a.type_name, []).append(a)
            shown += 1
        for tname in sorted(groups):
            lst = groups[tname]
            gid = self.tree.insert("", "end", text="%s  (%d)" % (tname, len(lst)),
                                   values=("", "", ""), open=bool(needle))
            for a in lst:
                label = a.label
                if a.name_probable:
                    label += "  ?"
                if a.readonly:
                    label += "   [read-only]"
                iid = self.tree.insert(gid, "end", text=label,
                                       values=("%06X" % a.start, format(a.span_len, ","),
                                               PAYLOAD_ICON.get(a.payload, '?')))
                self.node_asset[iid] = a
        self.count_lbl.config(text="  %d assets in %d types%s"
                              % (shown, len(groups), "  (filtered)" if needle else ""))

    # ------------------------------------------------------------------ select
    def _on_pick(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        a = self.node_asset.get(sel[0])
        if a is None:
            return
        self.cur = a
        z = self.session.zone
        head = ("%s   type=%s   span 0x%06X..0x%06X (%s B)"
                % (a.label, a.type_name, a.start, a.end, format(a.span_len, ",")))
        if a.buf_off is not None:
            head += "   payload 0x%06X +%s" % (a.buf_off, format(a.buf_len, ","))
        head += "   [%s]" % a.payload
        if a.source != 'walk':
            head += "   via %s" % a.source
        self.path_lbl.config(text=head)
        self.note_lbl.config(text=a.reason or "")
        self._update_actions()

        # XModel geometry lives inline in the zone, so it can be drawn rather than hex-dumped.
        if a.type_name == 'XMODEL':
            self._show_model_pane(True)
            drawable = self._load_model(a)
            self._draw3d(reframe=True)
            m = self.model3d
            if drawable and m is not None:
                self.note_lbl.config(
                    text="drag to rotate, right-drag to pan, wheel to zoom"
                         + ("   |  skinned" if m.skinned else "")
                         + ("   |  " + m.notes[0] if m.notes else ""))
                self.budget.config(text="%s tris" % format(m.tri_count, ","), fg=MUTED)
            else:
                self.budget.config(text="", fg=MUTED)
            return
        self._show_model_pane(False)

        # Compiled scripts get a source view + compiler; other assets keep the old behaviour.
        is_script = a.payload in ('gsc', 'hks')
        self._show_script_bar(is_script)
        if a.payload == 'gsc' and self._script_mode.get() == "asm":
            # The lossless assembly view: what the asset ACTUALLY holds, editable, no original
            # source required. Assembling it back is byte-identical unless you change it.
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            txt = self._asm_cache.get(a.label)
            if txt is None:
                from core import scripts as SC
                try:
                    txt = SC.disassemble_editable(a, a.extract(z))
                except Exception as ex:
                    txt = ("; the assembler REFUSED this script rather than guess at it:\n"
                           ";   %s: %s\n;\n"
                           "; Nothing here can be edited until that is understood.\n"
                           % (type(ex).__name__, ex))
            self.txt.insert("1.0", txt)
            self.txt.edit_modified(False)
            self._refresh_gutter()
            self.script_lbl.config(text="editable assembly", fg=MUTED)
            self.budget.config(text="%s bytes" % format(a.buf_len, ","), fg=MUTED)
            return
        if is_script and self._script_mode.get() == "source":
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            src = self._src_cache.get(a.label)
            if src is None:
                lang = "GSC" if a.payload == 'gsc' else "Lua"
                if a.payload == 'gsc':
                    src = ("// %s source for %s\n"
                           "//\n"
                           "// Press DECOMPILE to reconstruct this from the bytecode -- no\n"
                           "// original source needed. Or paste your own.\n"
                           "//\n"
                           "// Compiling replaces this asset's payload. Growth is fine --\n"
                           "// the zone is relinked on save.\n" % (lang, a.name or a.label))
                else:
                    src = ("// %s source for %s\n"
                           "//\n"
                           "// There is no HavokScript decompiler, so this view starts empty:\n"
                           "// paste or Load source, then Compile & stage.\n"
                           "//\n"
                           "// Compiling replaces this asset's payload. Growth is fine --\n"
                           "// the zone is relinked on save.\n" % (lang, a.name or a.label))
                if a.payload == 'hks':
                    src = src.replace("//", "--")
            self.txt.insert("1.0", src)
            self.txt.edit_modified(False)
            self._refresh_gutter()
            n = len(self.session.edits)
            self.script_lbl.config(text="%d edit(s) staged" % n if n else "", fg=MUTED)
            self.budget.config(text="source", fg=MUTED)
            return

        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        if a.payload == 'text' and not a.readonly:
            self.txt.insert("1.0", a.extract(z).decode("latin1"))
            self.budget.config(text="%s / %s bytes" % (format(a.buf_len, ","),
                                                       format(a.buf_len, ",")), fg=MUTED)
        else:
            body = a.extract(z)
            info = []
            show_hex = True
            if a.payload == 'gsc':
                info.append(self._gsc_info(body, a.label))
                show_hex = False
            elif a.payload == 'hks':
                info.append(self._hks_info(body, a.label))
                show_hex = False
            elif a.readonly:
                info.append("READ-ONLY: %s" % a.reason)
            else:
                info.append("%s -- %s bytes. Hex view:" % (a.payload, format(len(body), ",")))
            if show_hex:
                info.append("")
                info.append(hexdump(body, base=a.buf_off if a.buf_off is not None
                                    else a.start))
            self.txt.insert("1.0", "\n".join(info))
            self.budget.config(text="%s bytes" % format(len(body), ","), fg=MUTED)
            self.txt.config(state="disabled")
        self.txt.edit_modified(False)
        self._refresh_gutter()

    def _hks_info(self, body, label='<asset>'):
        """Full native HavokScript disassembly, via core.hks (no external toolchain).

        Lua assets get the same treatment as GSC: the Disassembly view shows what the asset
        actually holds, function by function, rather than a hex dump. The old placeholder here
        predated the HKS disassembler and was never updated.
        """
        try:
            from core import hks as H
            from core import hks_dis as HD
            f = H.parse(body)
            head = []
            if len(body) > 6 and body[6] != 0:
                head.append("!! this chunk is LITTLE-ENDIAN; the console loads big-endian "
                            "(header byte 6 = 0). Recompiling from Source fixes it.\n")
            # `HksFile.listing()` stops at protos and constants; `hks_dis.listing()` is the
            # actual disassembler and prints the instruction stream, which is what makes this
            # the equivalent of the GSC view rather than a summary.
            head.append(f.listing())
            head.append("")
            head.append("; ---- instructions ----")
            head.append(HD.listing(body))
            return "\n".join(head)
        except Exception as ex:
            return ("HavokScript bytecode, %s bytes.  (disassembly unavailable: %s: %s)\n\n%s"
                    % (format(len(body), ","), type(ex).__name__, ex,
                       hexdump(body, base=0)))

    def _gsc_info(self, body, label='<asset>'):
        """Full native disassembly, via core.gsc (no gsc-tool)."""
        try:
            from core import gsc as G
            g = G.GscScript(body, label)
            head = []
            if g.source_endian == 'pc':
                head.append("!! this script is PC LITTLE-ENDIAN, not console big-endian "
                            "-- transcoded for reading\n")
            head.append(g.listing(max_functions=60))
            return "\n".join(head)
        except Exception as ex:
            return ("Compiled GSC bytecode, %s bytes.  (disassembly unavailable: %s: %s)"
                    % (format(len(body), ","), type(ex).__name__, ex))

    # ------------------------------------------------------------------ tools
    def _show_summary(self):
        if not self.session:
            return
        import json
        self._popup("Zone summary", json.dumps(self.session.summary(), indent=2))

    def _show_plan(self):
        if not self.session:
            return
        p = self.session.plan()
        txt = ["strategy : %s" % p.strategy,
               "delta    : %+d bytes" % p.total_delta,
               "edits    : %d" % len(p.edits), ""]
        txt += ["- " + r for r in p.reasons]
        if p.edits:
            txt.append("")
            for e in p.edits:
                txt.append("  %-44s %+d" % (e.asset.label[:44], e.delta))
        self._popup("Save plan (dry run)", "\n".join(txt))

    def _verify(self):
        if not self.session:
            return
        from core import verify as V
        r = V.verify_zone(self.session.zone, expect_assets=len(self.session.assets))
        self._popup("Verification", r.report())

    def _census(self):
        if not self.session:
            return
        if not self.session.dirty:
            self._popup("Relink census", "No edits staged -- nothing to analyse.")
            return
        subs = {}
        for e in self.session.edits:
            a = e.asset
            subs[a.buf_off] = (a.buf_off + a.buf_len, e.data)
        self.status.config(text="Running pointer census (byte-granular)...")
        self.update_idletasks()
        rep = _relink.census(self.session.zone, subs)
        self._popup("Relink census (dry run)", rep.report())
        self.status.config(text="Census done")

    def _popup(self, title, body):
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=PANEL)
        win.geometry("860x560")
        t = tk.Text(win, bg=EDIT, fg=TEXT, relief="flat", bd=0, font=("Consolas", 9),
                    wrap="none", padx=10, pady=8)
        sb = ttk.Scrollbar(win, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.insert("1.0", body)
        t.config(state="disabled")

    # ------------------------------------------------------------------ import/export
    @staticmethod
    def _safe_name(s):
        """A filename Tk's save dialog will accept.

        ⚠ Tk applies TILDE SUBSTITUTION to `-initialfile`, so a leading '~' raises
        `TclError: user "..." doesn't exist` BEFORE the dialog appears -- and in a --windowed
        build that exception has nowhere to surface, so Export just looks dead. That is exactly
        how the ipak viewer's Export was broken; the same hazard applies here.
        """
        s = os.path.basename(s or 'asset').replace('/', '_').replace('\\', '_')
        return s.lstrip('~') or 'asset'

    def _export(self):
        if not self.cur or not self.session:
            return
        a = self.cur
        out = filedialog.asksaveasfilename(title="Export %s" % a.label,
                                           initialfile=self._safe_name(a.label))
        if not out:
            return
        try:
            with open(out, "wb") as fh:
                fh.write(a.extract(self.session.zone))
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Export failed:\n\n%s: %s" % (type(ex).__name__, ex))
            return
        self.status.config(text="Exported %s -> %s" % (a.label, out), fg=OK)

    def _import(self):
        """Replace the selected asset's payload from a file.

        For a compiled-script asset this accepts SOURCE as well as bytecode: a .gsc or .lua text
        file is compiled, anything already carrying the compiled magic is taken verbatim. That
        is why there is no separate 'load source' action -- Import is the one way to put a file
        into the selected asset.
        """
        if not self.cur or not self.session:
            return
        a = self.cur
        if not a.editable:
            messagebox.showerror(
                APP_TITLE,
                "%s cannot be replaced.\n\n%s" % (a.label,
                                                  a.reason or "This asset type has no modelled "
                                                              "payload, so we cannot say where "
                                                              "its bytes begin and end."))
            return
        types = [("All files", "*.*")]
        if a.payload == 'gsc':
            types = [("GSC source or compiled", "*.gsc *.csc"), ("All files", "*.*")]
        elif a.payload == 'hks':
            types = [("Lua source or compiled", "*.lua *.hks"), ("All files", "*.*")]
        src = filedialog.askopenfilename(title="Import into %s" % a.label, filetypes=types)
        if not src:
            return
        data = open(src, "rb").read()

        compiled_from_source = False
        if a.payload in ('gsc', 'hks'):
            already = (data[:4] == b'\x80GSC' if a.payload == 'gsc' else data[:4] == b'\x1bLua')
            if not already:
                try:
                    from core import scripts as SC
                    text = data.decode('utf-8', 'replace')
                    data = SC.compile_for(a, text, a.name)
                    compiled_from_source = True
                except Exception as ex:
                    messagebox.showerror(APP_TITLE, "Could not compile %s:\n\n%s: %s"
                                         % (os.path.basename(src), type(ex).__name__, ex))
                    return
        try:
            self.session.stage(a, data)
        except ValueError as ex:
            messagebox.showerror(APP_TITLE, str(ex))
            return
        self._mark_dirty()
        self.status.config(
            text="%s %s (%s bytes, %+d). Save to apply."
                 % ("Compiled and staged" if compiled_from_source else "Staged",
                    a.label, format(len(data), ","), len(data) - a.buf_len), fg=OK)

    # ------------------------------------------------------------------ save
    def _save(self, *_, save_as=False):
        if not self.session or self._busy:
            return
        plan = self.session.plan()
        if not plan.ok:
            messagebox.showerror(APP_TITLE, "Cannot save:\n\n" + "\n\n".join(plan.reasons))
            return
        if plan.strategy == 'grow':
            if not messagebox.askyesno(
                    APP_TITLE,
                    "This save changes the zone size by %+d bytes and needs a relink.\n\n%s\n\n"
                    "The relink verifies before writing and refuses if it cannot be done "
                    "soundly. Continue?" % (plan.total_delta, "\n".join(plan.reasons))):
                return
        out = self.session.ff_path
        if save_as or not out:
            out = filedialog.asksaveasfilename(
                defaultextension=".ff",
                initialfile=os.path.basename(out or (self.session.name + ".ff")),
                filetypes=[("Wii U fastfile", "*.ff"), ("Raw zone", "*.zone")])
            if not out:
                return
        self._busy = True
        self._phase = None
        self._busy_path = out
        self.pbar["value"] = 0
        self.status.config(text="Building + verifying...")
        threading.Thread(target=self._worker_save, args=(out,), daemon=True).start()

    def _worker_save(self, out):
        try:
            res = self.session.save(
                out, progress=lambda f, label: self._q.put(("progress", f, label)))
            self._q.put(("saved", res))
        except Exception as ex:
            self._q.put(("err", "%s: %s" % (type(ex).__name__, ex)))

    # ------------------------------------------------------------------ pump
    def _drain(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self.pbar["value"] = int(item[1] * 1000)
                    # ⚠ SAY WHICH PHASE. A bar that fills during decompression and then sits
                    # still through the asset walk reads as a hang; naming the phase makes the
                    # quiet stretch legible instead of alarming.
                    label = item[2] if len(item) > 2 else None
                    if label and label != getattr(self, '_phase', None):
                        self._phase = label
                        what = os.path.basename(getattr(self, '_busy_path', '') or '')
                        self.status.config(text=("%s %s ..." % (label.capitalize(), what)).strip()
                                           if what else "%s ..." % label.capitalize())
                    continue
                self._busy = False
                self._phase = None
                self.pbar["value"] = 0
                if kind == "err":
                    self.status.config(text="Error: " + item[1])
                    messagebox.showerror(APP_TITLE, item[1])
                elif kind == "opened":
                    self._apply_open(item[1])
                elif kind == "saved":
                    r = item[1]
                    self._refill()
                    self._mark_dirty()
                    self.status.config(
                        text="Saved %s  (%s, %s B zone, md5 %s)"
                             % (os.path.basename(r['path']), r['strategy'],
                                format(r['zone_bytes'], ","), r['zone_md5'][:8]))
        except queue.Empty:
            pass
        self.after(60, self._drain)

    def _on_close(self):
        if self.session and self.session.dirty and \
                not messagebox.askyesno(APP_TITLE, "Discard unsaved edits and quit?"):
            return
        self.destroy()


def _run_selftest(quiet=False):
    """`WiiU_FF_IDE.exe --selftest` -- prove THIS binary works, without Python installed.

    A windowed build has no stdout, so the result also goes to a log beside the exe and into a
    dialog. This is what verifies the frozen wiring: the asset walk needs OAT's T6_Assets.h and
    ZoneCode directory, which exist as source files from a checkout but are bundled into the
    archive here (see core.paths._install_frozen_data). If that were wrong the walk would go
    PARTIAL AND SILENT -- 754 assets instead of 1527, with no exception -- so it is checked.
    """
    import io
    import contextlib

    buf = io.StringIO()
    failed = 0
    with contextlib.redirect_stdout(buf):
        print('%s %s gate battery' % (APP_TITLE, APP_VERSION))
        print('frozen: %s' % getattr(sys, 'frozen', False))
        from core import paths as _p
        print('T6_Assets.h: %s' % os.environ.get('OAT_T6_ASSETS_H', '(source-relative)'))
        print('backend trees present: %s' % ('none (frozen)' if _p.FROZEN
                                             else len(_p.TREES) - len(_p.missing())))
        print()
        # The walk itself is the thing most likely to break when frozen.
        try:
            from core import ZoneSession
            here = os.path.dirname(sys.executable if getattr(sys, 'frozen', False)
                                   else os.path.abspath(__file__))
            cand = [os.path.join(d, f) for d in (here, os.getcwd(),
                                                 os.path.dirname(os.path.dirname(here)))
                    for f in ('mp_raid.ff', 'patch_mp.ff')]
            zp = next((p for p in cand if os.path.exists(p)), None)
            if zp:
                s = ZoneSession.open(zp)
                n = len(s.assets)
                print('  %-6s %s' % ('PASS' if n > 100 else 'FAIL',
                                     'asset walk: %s -> %d assets' % (os.path.basename(zp), n)))
                failed += int(n <= 100)
            else:
                print('  %-6s %s' % ('SKIP', 'no fastfile next to the binary to walk'))
        except Exception as ex:
            print('  %-6s asset walk: %s: %s' % ('FAIL', type(ex).__name__, ex))
            failed += 1
        print()
        from core import xmodel_selftest
        failed += xmodel_selftest.run()
        print()
        from core import scripts_selftest
        failed += scripts_selftest.run()
        print()
        # The general assembler: the path that edits a script with NO original source. It has
        # its own bundling risk (it imports gsc_diff / gsc_spt from the backend trees), which
        # is exactly the class of fault a frozen build hides, so it runs here too.
        print('GSC assembler gate battery')
        from core import gsc_asm_selftest
        failed += gsc_asm_selftest.run()
    text = buf.getvalue()

    where = os.path.dirname(sys.executable if getattr(sys, 'frozen', False)
                            else os.path.abspath(__file__))
    log = os.path.join(where, 'ff_ide_selftest.log')
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


if __name__ == "__main__":
    _args = sys.argv[1:]
    if '--selftest' in _args:
        sys.exit(_run_selftest(quiet='--quiet' in _args))
    _args = [a for a in _args if not a.startswith('--')]
    run_standalone(IDE, initial=_args[0] if _args else None, title=APP_TITLE)
