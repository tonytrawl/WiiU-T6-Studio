"""core.theme -- one palette and one ttk style set for the whole studio.

WHY THIS EXISTS
---------------
The three tools each carried their OWN copy of the same nine colour constants and their own
`_style()` method. Identical today by luck, not by construction: any change to one drifted the
others, and once they share a window a drift is visible as a seam down the middle of the app.

So the palette lives here, and `install()` configures every ttk style the studio uses -- both the
NEW shell styles and the LEGACY names (`Ghost.TButton`, `Accent.TButton`, `Tree.Treeview`,
`FF.Horizontal.TProgressbar`) that the existing tool code already asks for. That means a hosted
workspace looks right without editing its widget code.

The legacy constant names are re-exported unchanged so `from core.theme import *` is a drop-in
for the old module-level block.
"""
import os
import sys
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------- palette
# Slightly deeper and cooler than the original three-tool palette, with an explicit elevation
# ramp (BG -> SURFACE -> RAISED) so panels read as layered instead of flat-on-flat.
# ⚠ TUNED FOR READABILITY, SAME TONES. The original palette was handsome but dim: secondary
# text at #8b97a8 and tertiary at #5d6878 sat near 4:1 against the panels, which is fine for a
# label you glance at and tiring for the tables this app is mostly made of. Every surface keeps
# its hue and its relative order; the text ramp is brightened and the hairlines lifted so rows,
# headings and separators are actually distinguishable. Nothing here changes the brand teal.
BG = "#0f151d"          # window background, the lowest layer
SURFACE = "#171f2a"     # panels, toolbars, the rail
RAISED = "#212c3a"      # hovered/selected rows, elevated cards
EDIT = "#0d1219"        # text areas, canvases, list bodies
HEAD = "#111823"        # table headings, gutters
BORDER = "#2c3849"      # hairlines

TEXT = "#eef3f8"        # primary text
MUTED = "#a8b4c4"       # secondary text -- was #8b97a8, too dim for table columns
FAINT = "#7f8b9c"       # tertiary / disabled -- was #5d6878, barely legible

# ⚠ ONE ACCENT, ONE MEANING. Amber marks THE THING YOU ARE ACTING ON: the primary button, the
# selected row, the progress bar. Everything else is neutral. The moment amber also means
# "decorative" it stops telling you anything.
ACCENT = "#e8a33d"      # amber
ACCENT2 = "#c9862a"     # pressed/darker amber
ON_ACCENT = "#17130a"   # text on an accent fill

OK = "#46cf88"
WARN = "#f0736e"
INFO = "#e8b552"

# Legacy aliases (the three tools import these names).
PANEL = SURFACE
GUTTER = HEAD

#: Per-document-type accent, so the shell can tint the active document without a second palette.
#: ⚠ THESE ARE IDENTITY, NOT EMPHASIS. The same colour marks a type everywhere it appears -- the
#: welcome list's spine, the tab dot, the brand bar -- so a pak reads amber in every context.
#: RPL is violet rather than green because green already means "ok" in the status bar, and two
#: meanings for one colour is exactly what this palette exists to avoid.
KIND_ACCENT = {
    'zone': "#4d8fd6",      # fastfiles -- blue
    'ipak': "#e8a33d",      # texture paks -- amber, matching the action accent
    'sab': "#7cb342",       # sound banks -- green
    'rpl': "#b07cd6",       # engine RPLs -- violet
}

FONT_UI = ("Segoe UI", 9)
FONT_UI_MED = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI Semibold", 11)
FONT_MONO = ("Consolas", 9)

def install(widget):
    """Configure every ttk style the studio uses. Safe to call more than once.

    Takes any widget (styles are per-interpreter, not per-widget) so it works from the shell or
    from a standalone tool window.
    """
    st = ttk.Style(widget)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass                      # a platform without clam still gets the colours below

    # ---- buttons -------------------------------------------------------------
    st.configure("Ghost.TButton", background=SURFACE, foreground=TEXT, borderwidth=0,
                 focuscolor=SURFACE, padding=(10, 5), font=FONT_UI)
    st.map("Ghost.TButton",
           background=[("disabled", SURFACE), ("active", RAISED)],
           foreground=[("disabled", FAINT)])

    st.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT, borderwidth=0,
                 focuscolor=ACCENT, padding=(10, 5), font=FONT_UI)
    st.map("Accent.TButton",
           background=[("disabled", BORDER), ("active", ACCENT2)],
           foreground=[("disabled", FAINT)])

    # Toolbar buttons sit on SURFACE and need no border at all.
    st.configure("Tool.TButton", background=SURFACE, foreground=TEXT, borderwidth=0,
                 focuscolor=SURFACE, padding=(11, 6), font=FONT_UI)
    st.map("Tool.TButton",
           background=[("disabled", SURFACE), ("active", RAISED)],
           foreground=[("disabled", FAINT)])

    # ---- trees / lists -------------------------------------------------------
    # ⚠ MONOSPACED ON PURPOSE, AND OWNED HERE. Nearly every list in this app is tabular data --
    # hashes, byte counts, dimensions, part indices -- which only lines up in a fixed-width font.
    # Three of the four editors used to set exactly this from their own private _style(), and
    # because ttk's style database is per-INTERPRETER rather than per-widget, whichever tab was
    # constructed last silently restyled every tab already on screen (measured: an asset tree
    # flipping between Consolas 9/rowheight 20 and Segoe UI 9/22 depending on tab order).
    # The shared look now lives here, once, and hosted tools do not touch it.
    st.configure("Tree.Treeview", background=EDIT, fieldbackground=EDIT, foreground=TEXT,
                 borderwidth=0, rowheight=20, font=FONT_MONO)
    st.map("Tree.Treeview",
           background=[("selected", ACCENT2)], foreground=[("selected", ON_ACCENT)])
    st.configure("Tree.Treeview.Heading", background=HEAD, foreground=MUTED,
                 borderwidth=0, padding=(6, 4), font=FONT_UI)
    st.map("Tree.Treeview.Heading", background=[("active", RAISED)])

    # ---- misc ---------------------------------------------------------------
    # Checkbuttons were never themed here, so any tool hosting one got clam's light-grey default
    # on a dark card -- most visibly the RPL patcher's three primary controls, which are the
    # whole point of that tab.
    st.configure("TCheckbutton", background=SURFACE, foreground=TEXT, focuscolor=SURFACE,
                 font=FONT_UI, padding=(2, 3))
    st.map("TCheckbutton",
           background=[("active", SURFACE)],
           foreground=[("disabled", FAINT), ("active", TEXT)],
           indicatorcolor=[("selected", ACCENT), ("!selected", EDIT)])
    st.configure("Card.TCheckbutton", background=PANEL, foreground=TEXT, focuscolor=PANEL,
                 font=FONT_UI, padding=(2, 3))
    st.map("Card.TCheckbutton",
           background=[("active", PANEL)],
           foreground=[("disabled", FAINT), ("active", TEXT)],
           indicatorcolor=[("selected", ACCENT), ("!selected", EDIT)])

    st.configure("FF.Horizontal.TProgressbar", background=ACCENT, troughcolor=HEAD,
                 borderwidth=0)
    st.configure("TSeparator", background=BORDER)
    st.configure("TEntry", fieldbackground=EDIT, foreground=TEXT, borderwidth=0,
                 insertcolor=TEXT)
    st.configure("TCombobox", fieldbackground=EDIT, background=SURFACE, foreground=TEXT,
                 borderwidth=0, arrowcolor=MUTED)
    st.configure("Vertical.TScrollbar", background=SURFACE, troughcolor=BG,
                 borderwidth=0, arrowcolor=MUTED)
    st.configure("Horizontal.TScrollbar", background=SURFACE, troughcolor=BG,
                 borderwidth=0, arrowcolor=MUTED)
    return st


_ICON_CACHE = {}


def icon_file():
    """Locate the application icon in both source and frozen layouts, or None."""
    names = ('editor.ico', 'studio.ico', 'ipak.ico')
    bases = []
    mei = getattr(sys, '_MEIPASS', None)
    if mei:
        bases.append(mei)
    if getattr(sys, 'frozen', False):
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
    here = os.path.dirname(os.path.abspath(__file__))          # core/
    bases += [os.path.dirname(here), here]                     # studio dir, then core/
    for b in bases:
        for n in names:
            p = os.path.join(b, n)
            if os.path.isfile(p):
                return p
    return None


def apply_icon(win):
    """Make our icon the DEFAULT for every window the application opens. -> bool

    ⚠ THE `default=` KEYWORD IS THE WHOLE POINT. `win.iconbitmap(path)` sets the icon on THAT
    WINDOW ONLY, so every tk.Toplevel opened later -- Find asset, Game folders, the tools' own
    dialogs -- kept Tk's default feather icon. `iconbitmap(default=path)` registers it as the
    application default, which new toplevels inherit.

    Windows takes .ico here; other platforms reject it, so those fall back to iconphoto with a
    converted image. The PhotoImage must be cached or it is garbage collected and the icon
    silently reverts.
    """
    p = icon_file()
    if not p:
        return False
    try:
        win.iconbitmap(default=p)
        return True
    except tk.TclError:
        pass
    try:
        from PIL import Image, ImageTk
        if 'photo' not in _ICON_CACHE:
            im = Image.open(p)
            im.load()
            _ICON_CACHE['photo'] = ImageTk.PhotoImage(im.convert('RGBA'),
                                                      master=win.winfo_toplevel())
        win.winfo_toplevel().iconphoto(True, _ICON_CACHE['photo'])
        return True
    except Exception:
        return False


def hairline(parent, orient="horizontal"):
    """A 1px divider. Cheaper and more predictable than a ttk.Separator on clam."""
    if orient == "horizontal":
        return tk.Frame(parent, bg=BORDER, height=1)
    return tk.Frame(parent, bg=BORDER, width=1)
