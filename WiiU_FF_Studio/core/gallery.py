"""core.gallery -- a phone-style thumbnail grid that swaps in place of a list view.

ONE WIDGET, EVERY VIEWER. The ipak browser and the in-zone texture browser both want this, and
this project has now paid twice for the same format being implemented in two places (the zone
index name field, and the format-9 blob layout in reader vs writer). So the grid lives here and
the viewers only supply: how to enumerate items, how to make a thumbnail, and what to do on click.

⚠ THUMBNAILS ARE DECODED ON A WORKER, ONE AT A TIME, AND ONLY WHEN VISIBLE.
A pak holds thousands of entries and a console texture is not cheap to decode -- decoding them all
up front would freeze the window for minutes with nothing on screen, which is indistinguishable
from a hang. So: only tiles scrolled into view are queued, results arrive through a queue the UI
thread drains, and a scroll that moves away cancels nothing but simply stops asking. Re-selecting
the same item never re-decodes: the caller's own cache is consulted first.

⚠ Tk DROPS AN IMAGE WITH NO LIVE REFERENCE. Every PhotoImage is kept in `self._photos`, keyed by
item id. Losing that reference does not error -- the tile just renders blank, which reads as
"decode failed" and sends you looking in the wrong place entirely.
"""
import queue
import threading
import tkinter as tk

from . import theme

THUMB = 128                 # px, the tile's image box
QUEUE_PER_SWEEP = 48        # tiles requested per pass; see _queue_visible
#: A tile has three states and they must LOOK different: waiting, done, and cannot-be-done.
#: Static text, no animation -- hundreds of animating tiles is motion nobody asked for, and it
#: competes for attention with the decoding it is supposed to be reporting. Plain ASCII, because
#: a decorative glyph renders as a missing-character box on whatever font Windows falls back to.
LOADING_TEXT = 'loading...'
NO_PREVIEW_TEXT = 'no preview'
PAD = 10
LABEL_H = 16


class Gallery(tk.Frame):
    """A scrollable grid of thumbnails.

    items         list of opaque handles the caller understands
    key           item -> a hashable id, stable across refills (used for caching + selection)
    label         item -> the one-line caption under the tile
    thumbnail     item -> PIL.Image or None. CALLED ON A WORKER THREAD; must not touch Tk.
    on_select     item -> None, single click
    on_activate   item -> None, double click (the viewers use this to jump to the full record)
    """

    def __init__(self, master, key, label, thumbnail,
                 on_select=None, on_activate=None, thumb_px=THUMB, **kw):
        tk.Frame.__init__(self, master, bg=theme.EDIT, **kw)
        self._key, self._label, self._thumb = key, label, thumbnail
        self._on_select, self._on_activate = on_select, on_activate
        self._px = thumb_px

        self.canvas = tk.Canvas(self, bg=theme.EDIT, highlightthickness=0, bd=0)
        self.sb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=theme.EDIT)
        # Grid from the TOP-LEFT. Tk centres a grid inside an over-wide master by default, which
        # made a part-filled row float in the middle of the pane instead of reading as a row.
        self.inner.grid_anchor('nw')
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        for w in (self.canvas, self.inner):
            w.bind("<MouseWheel>", self._on_wheel)

        self._items = []
        self._tiles = {}            # key -> (frame, image label, caption)
        self._photos = {}           # key -> PhotoImage   (see the reference warning above)
        self._pending = set()
        self._sel = None
        self._cols = 0
        self._q = queue.Queue()      # worker -> UI  (results only)
        self._todo = []              # UI -> worker (work only), guarded by _lock
        self._lock = threading.Lock()
        self._stop = False
        self._worker = None
        self._frame = 0
        self.after(80, self._drain)

    # ---------------------------------------------------------------- public
    def set_items(self, items):
        """Replace the contents. Cheap: tiles are built empty and filled as they scroll in."""
        self._stop = True                      # abandon the previous sweep's queue
        self._items = list(items)
        self._pending.clear()
        with self._lock:
            self._todo = []
        for f, _i, _c in self._tiles.values():
            f.destroy()
        self._tiles.clear()
        self._photos.clear()
        self._sel = None
        self._layout()
        self._stop = False
        self.after_idle(self._queue_visible)

    def set_thumb_px(self, px):
        """Resize the tiles. Rebuilds the grid and re-requests thumbnails at the new size.

        ⚠ CLEARS THE PHOTO CACHE. A 128 px thumbnail scaled up to 256 is a blurry lie about what
        the texture looks like, which defeats the point of looking at it -- so the images are
        re-decoded at the new box rather than stretched.
        """
        px = max(48, min(320, int(px)))
        if px == self._px:
            return
        self._px = px
        items = list(self._items)
        self.set_items([])
        self.set_items(items)
        # ⚠ RE-LAYOUT WITH THE CURRENT WIDTH. Changing the tile size does not change the CANVAS
        # size, so no <Configure> fires and the column count would keep the value computed for the
        # OLD tile size -- bigger tiles in the same number of columns, or a stale single column.
        # winfo_width() is authoritative here precisely because we are NOT inside a Configure.
        try:
            w = self.canvas.winfo_width()
            if w > 1:
                self._cols = 0                # force _layout to recompute rather than early-out
                self._layout(w)
        except tk.TclError:
            pass
        self.after_idle(self._queue_visible)

    def select(self, item):
        """Highlight `item` without firing on_select (used to mirror the list view's selection)."""
        k = self._key(item) if item is not None else None
        self._paint_selection(k)
        if k in self._tiles:
            self._scroll_into_view(self._tiles[k][0])

    def destroy(self):
        self._stop = True
        tk.Frame.destroy(self)

    # ---------------------------------------------------------------- layout
    def _cell(self):
        return self._px + PAD * 2, self._px + PAD * 2 + LABEL_H

    def _on_resize(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        cw, _ch = self._cell()
        if max(1, e.width // cw) != self._cols:
            self._layout(e.width)                 # pass the NEW width -- see _layout
        self.after_idle(self._queue_visible)

    def _layout(self, width=None):
        """Re-grid the tiles. `width` is the authoritative pane width when one is supplied.

        ⚠ DURING A <Configure> CALLBACK, winfo_width() STILL REPORTS THE OLD SIZE. Only e.width
        carries the new one. Reading winfo_width() here meant the resize handler computed 6
        columns, called this, and this immediately overwrote it with the stale 1 -- so the grid
        stayed a single centred column no matter how wide the window got. Measured twice before
        the cause was visible in a screenshot rather than in the numbers.

        set_items() legitimately has no width (the gallery is still unpacked while the list view
        shows), so it passes None and gets one column until the first real <Configure>.
        """
        cw, _ch = self._cell()
        w = width if width is not None else self.canvas.winfo_width()
        self._cols = max(1, w // cw)
        for i, it in enumerate(self._items):
            k = self._key(it)
            if k not in self._tiles:
                self._tiles[k] = self._make_tile(it, k)
            f, _img, _cap = self._tiles[k]
            f.grid(row=i // self._cols, column=i % self._cols, padx=PAD // 2, pady=PAD // 2)

    def _make_tile(self, item, k):
        f = tk.Frame(self.inner, bg=theme.EDIT, highlightthickness=2,
                     highlightbackground=theme.EDIT, highlightcolor=theme.EDIT)
        # A tile starts in a LOADING state, never blank. An empty square is indistinguishable
        # from a failed decode, so an undecoded tile says so and an undecodable one says
        # 'no preview' -- the two outcomes must never look the same.
        # ⚠ A FIXED-SIZE FRAME HOLDS THE IMAGE, NOT width= ON THE LABEL ITSELF.
        # Tk reads Label width/height in CHARACTERS when the widget carries text, and in pixels
        # only when it carries an image. `width=128` with a 'loading...' caption therefore asked
        # for 128 CHARACTERS -- roughly 900px -- so exactly one tile fitted per row and its image
        # sat centred in a huge empty cell. Every diagnostic reported _cols=6 and a 979px canvas,
        # because the column COUNT was right and the CELLS were enormous. Frame width/height are
        # always pixels, so the box is authoritative and the label just fills it.
        box = tk.Frame(f, bg=theme.HEAD, width=self._px, height=self._px)
        box.pack_propagate(False)
        box.pack()
        img = tk.Label(box, bg=theme.HEAD, bd=0, text=LOADING_TEXT,
                       fg=theme.FAINT, font=theme.FONT_UI, compound='center')
        img.pack(expand=True, fill="both")
        cap = tk.Label(f, text=self._label(item)[:22], bg=theme.EDIT, fg=theme.MUTED,
                       font=theme.FONT_UI, anchor="center", width=int(self._px / 6.5))
        cap.pack(fill="x")
        for w in (f, box, img, cap):
            w.bind("<Button-1>", lambda _e, i=item: self._click(i))
            w.bind("<Double-1>", lambda _e, i=item: self._double(i))
            w.bind("<MouseWheel>", self._on_wheel)
        return f, img, cap

    # ---------------------------------------------------------------- interaction
    def _click(self, item):
        self._paint_selection(self._key(item))
        if self._on_select:
            self._on_select(item)

    def _double(self, item):
        self._paint_selection(self._key(item))
        if self._on_activate:
            self._on_activate(item)

    def _paint_selection(self, k):
        for kk, (f, _i, cap) in self._tiles.items():
            on = (kk == k)
            f.configure(highlightbackground=theme.ACCENT if on else theme.EDIT,
                        highlightcolor=theme.ACCENT if on else theme.EDIT)
            cap.configure(fg=theme.TEXT if on else theme.MUTED)
        self._sel = k

    def _scroll_into_view(self, frame):
        try:
            self.canvas.update_idletasks()
            y = frame.winfo_y()
            h = max(1, self.inner.winfo_height())
            self.canvas.yview_moveto(max(0.0, (y - 40) / float(h)))
        except tk.TclError:
            pass

    def _on_wheel(self, e):
        self.canvas.yview_scroll(-1 * (e.delta // 120), "units")
        self.after_idle(self._queue_visible)
        return "break"

    # ---------------------------------------------------------------- thumbnails
    def _queue_visible(self):
        """Ask for thumbnails of the tiles actually on screen, nearest first."""
        if self._stop or not self._items:
            return
        try:
            top = self.canvas.canvasy(0)
            bot = top + self.canvas.winfo_height()
        except tk.TclError:
            return
        want = []
        for it in self._items:
            k = self._key(it)
            if k in self._photos or k in self._pending:
                continue
            t = self._tiles.get(k)
            if not t:
                continue
            y = t[0].winfo_y()
            _cw, ch = self._cell()
            if y + ch >= top - ch and y <= bot + ch:      # one row of slack either side
                want.append(it)
                # ⚠ HARD CAP PER SWEEP. Before the canvas has a real size, every tile reports as
                # visible -- measured: a 187-entry pak queued all 187 at once. On a 4,000-entry
                # pak that is the whole pak decoded before the first tile appears, which is the
                # freeze this lazy path exists to avoid. The rest arrive on the next sweep.
                if len(want) >= QUEUE_PER_SWEEP:
                    break
        for it in want:
            self._pending.add(self._key(it))
        # ⚠ WORK GOES IN ITS OWN QUEUE, NEVER IN THE RESULT QUEUE. An earlier version pushed
        # follow-up items into `self._q` and let the worker drain it looking for them -- so the
        # worker consumed its OWN ('img', ...) results before the UI thread could, and tiles
        # stayed blank forever while `pending` sat full. One queue per direction.
        with self._lock:
            self._todo.extend(want)
            running = self._worker is not None and self._worker.is_alive()
        if want and not running:
            self._worker = threading.Thread(target=self._work, daemon=True)
            self._worker.start()

    def _work(self):
        while not self._stop:
            with self._lock:
                if not self._todo:
                    return
                it = self._todo.pop(0)
            try:
                im = self._thumb(it)
            except Exception:
                im = None
            self._q.put(('img', self._key(it), im))

    def _drain(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] != 'img':
                    continue
                _t, k, im = msg
                self._pending.discard(k)
                t = self._tiles.get(k)
                if not t:
                    continue
                _f, lbl, _cap = t
                if im is None:
                    lbl.configure(image='', text=NO_PREVIEW_TEXT, fg=theme.FAINT,
                                  font=theme.FONT_UI, compound='center')
                    continue
                try:
                    from PIL import ImageTk
                    ph = ImageTk.PhotoImage(im)
                except Exception:
                    continue
                self._photos[k] = ph               # MUST outlive this scope -- see header
                lbl.configure(image=ph, text='')
        except queue.Empty:
            pass
        # ⚠ SELF-HEAL THE COLUMN COUNT. The grid must not depend on <Configure> arriving at the
        # right moment. It legitimately does not when the view is restored from settings during
        # open -- the gallery is packed before the window has been laid out, so it computes its
        # columns against a 1-pixel canvas and would stay a single column until the user happened
        # to resize. Measured exactly that. Checking the real width on the drain tick costs
        # nothing and makes the layout correct regardless of event order.
        try:
            w = self.canvas.winfo_width()
            if w > 1 and self._items:
                cw, _ch = self._cell()
                if max(1, w // cw) != self._cols:
                    self._layout(w)
                    self.after_idle(self._queue_visible)
        except tk.TclError:
            pass
        if self.winfo_exists():
            self.after(80, self._drain)


class ViewSwitch(tk.Frame):
    """A two-state List / Gallery toggle that swaps which widget is packed.

    The two views are SIBLINGS that share the caller's selection, not separate windows -- the
    point is to flip back and forth while keeping your place, so switching must be instant and
    must not rebuild either side.
    """

    def __init__(self, master, list_widget, gallery_widget, on_change=None, **kw):
        tk.Frame.__init__(self, master, bg=theme.SURFACE, **kw)
        self.list_widget, self.gallery_widget = list_widget, gallery_widget
        self._on_change = on_change
        self.mode = 'list'
        self._btns = {}
        for name, text in (('list', ' List '), ('gallery', ' Gallery ')):
            b = tk.Label(self, text=text, bg=theme.SURFACE, fg=theme.MUTED,
                         font=theme.FONT_UI, padx=8, pady=3, cursor="hand2")
            b.pack(side="left", padx=1, pady=2)
            b.bind("<Button-1>", lambda _e, n=name: self.show(n))
            self._btns[name] = b
        self._paint()

    def show(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        (self.gallery_widget if mode == 'list' else self.list_widget).pack_forget()
        w = self.list_widget if mode == 'list' else self.gallery_widget
        w.pack(fill="both", expand=True)
        self._paint()
        if self._on_change:
            self._on_change(mode)

    def _paint(self):
        for n, b in self._btns.items():
            on = (n == self.mode)
            b.configure(bg=theme.ACCENT if on else theme.SURFACE,
                        fg=theme.ON_ACCENT if on else theme.MUTED)
