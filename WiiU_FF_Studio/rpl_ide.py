"""rpl_ide.py -- the engine RPL workspace: apply the patches that make edits loadable.

WHY THIS IS PART OF THE TOOLKIT
-------------------------------
A repacked fastfile carries no valid RSA signature, so an UNPATCHED console rejects it. Shipping
the fastfile editors without the signature patch shipped half a tool: a user with an unpatched
copy had every edit fail -- including a save with no changes -- while the same tool worked on a
machine whose RPLs were already patched. The zone bytes were identical in both cases; only the
signature block differed.

Layout: patches are the primary job, so they get the first tab and the plain language. Sections
and Advanced are for people who already know what they are doing.
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import paths  # noqa: F401,E402
from core import theme  # noqa: E402
from core import rpl_patch as RP  # noqa: E402
from core.workspace import Workspace, run_standalone  # noqa: E402

APP_TITLE = "Wii U RPL Patcher"
APP_VERSION = "1.0"

BG, PANEL, EDIT = theme.BG, theme.SURFACE, theme.EDIT
TEXT, MUTED, BORDER = theme.TEXT, theme.MUTED, theme.BORDER
OK, WARN, INFO = theme.OK, theme.WARN, theme.INFO

#: Offered targets. 480p is the genuine low end (the engine's own 480-class branch); above 720p
#: the internal render target is scaled into a legal TV buffer, which is already the code's shape.
RES_PRESETS = [("640 x 480    (4:3, lowest)", 640, 480),
               ("854 x 480    (480p wide)", 854, 480),
               ("960 x 540    (quarter 1080)", 960, 540),
               ("1024 x 576", 1024, 576),
               ("1152 x 648", 1152, 648),
               ("1280 x 720   (stock)", 1280, 720),
               ("1366 x 768", 1366, 768),
               ("1440 x 810", 1440, 810),
               ("1600 x 900   (boot-proven)", 1600, 900),
               ("1706 x 960", 1706, 960),
               ("1920 x 1080  (heaviest)", 1920, 1080),
               ("Custom...", None, None)]


class RplIDE(Workspace):
    def __init__(self, master=None, initial=None):
        super().__init__(master, bg=BG)
        self.rpl = None
        self.path = None
        self.found = {}
        self._vars = {}

        theme.install(self)
        self._toolbar()
        self._body()
        self._statusbar()
        if initial:
            self.after(120, lambda: self.open_file(initial))

    # ------------------------------------------------------------------ chrome
    def _toolbar(self):
        bar = tk.Frame(self, bg=PANEL, height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ttk.Button(bar, text="Open RPL", style="Ghost.TButton",
                   command=self.open_file).pack(side="left", padx=(10, 4), pady=7)
        self.btn_apply = ttk.Button(bar, text="Apply selected patches",
                                    style="Accent.TButton", command=self._apply,
                                    state="disabled")
        self.btn_apply.pack(side="left", padx=4, pady=7)
        self.btn_restore = ttk.Button(bar, text="Restore stock", style="Ghost.TButton",
                                      command=self._restore, state="disabled")
        self.btn_restore.pack(side="left", padx=4, pady=7)
        self.head = tk.Label(bar, text="no RPL open", bg=PANEL, fg=MUTED,
                             font=theme.FONT_UI)
        self.head.pack(side="right", padx=14)

    def _body(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=8)
        self.tab_patch = tk.Frame(nb, bg=BG)
        self.tab_sec = tk.Frame(nb, bg=BG)
        self.tab_adv = tk.Frame(nb, bg=BG)
        nb.add(self.tab_patch, text="  Patches  ")
        nb.add(self.tab_sec, text="  Sections  ")
        nb.add(self.tab_adv, text="  Advanced  ")
        self._build_patch_tab()
        self._build_sections_tab()
        self._build_advanced_tab()

    def _statusbar(self):
        theme.hairline(self).pack(fill="x")
        bar = tk.Frame(self, bg=PANEL, height=26)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="Open an engine RPL (t6_cafef_rpl.rpl / "
                                         "t6mp_cafef_rpl.rpl)",
                               bg=PANEL, fg=MUTED, font=theme.FONT_UI, anchor="w")
        self.status.pack(side="left", padx=12)

    def say(self, msg, colour=None):
        self.status.config(text=msg, fg=colour or MUTED)

    # ------------------------------------------------------------------ patches tab
    def _build_patch_tab(self):
        tk.Label(self.tab_patch, bg=BG, fg=MUTED, font=theme.FONT_UI, justify="left",
                 anchor="w", wraplength=900,
                 text=("Patches are located by symbol and instruction pattern, so they work on "
                       "any build -- base, MP or update -- without hard-coded addresses.\n"
                       "⚠ Patch BOTH t6_cafef_rpl.rpl and t6mp_cafef_rpl.rpl. They carry "
                       "separate copies of the same engine code, and boot-time render setup "
                       "runs in the base one.")).pack(fill="x", padx=12, pady=(12, 8))

        self.rows = tk.Frame(self.tab_patch, bg=BG)
        self.rows.pack(fill="both", expand=True, padx=12)

    def _render_patches(self):
        for w in self.rows.winfo_children():
            w.destroy()
        self._vars = {}
        if not self.found:
            tk.Label(self.rows, text="Open an RPL to see what can be patched.",
                     bg=BG, fg=MUTED, font=theme.FONT_UI).pack(anchor="w", pady=20)
            return

        for name in ('signature', 'dlc_loadgate', 'resolution'):
            info = self.found.get(name)
            if not info:
                continue
            card = tk.Frame(self.rows, bg=PANEL)
            card.pack(fill="x", pady=4)
            left = tk.Frame(card, bg=PANEL)
            left.pack(side="left", fill="x", expand=True, padx=12, pady=10)

            var = tk.BooleanVar(value=False)
            self._vars[name] = var
            state = "normal" if (info['ok'] and not info.get('already')) else "disabled"
            cb = ttk.Checkbutton(left, text=info['label'], variable=var)
            cb.configure(state=state)
            cb.pack(anchor="w")

            tk.Label(left, text=info['desc'], bg=PANEL, fg=MUTED, font=theme.FONT_UI,
                     justify="left", wraplength=780, anchor="w").pack(anchor="w", pady=(2, 0))

            if info['ok'] and info.get('already'):
                msg, col = "already applied in this file", OK
            elif info['ok']:
                msg, col = "available at %#010x" % info['site'], INFO
            else:
                msg, col = info.get('detail') or "not available", MUTED
            tk.Label(left, text=msg, bg=PANEL, fg=col, font=theme.FONT_UI,
                     anchor="w").pack(anchor="w", pady=(4, 0))

            if name == 'resolution' and info['ok']:
                row = tk.Frame(left, bg=PANEL)
                row.pack(anchor="w", pady=(6, 0))
                tk.Label(row, text="target", bg=PANEL, fg=MUTED,
                         font=theme.FONT_UI).pack(side="left", padx=(0, 6))
                self.res_var = tk.StringVar(value="1600 x 900   (boot-proven)")
                ttk.Combobox(row, textvariable=self.res_var, width=26, state="readonly",
                             values=[p[0] for p in RES_PRESETS]).pack(side="left")
                self.cw_var = tk.StringVar(value=str(info.get('width', 1280)))
                self.ch_var = tk.StringVar(value=str(info.get('height', 720)))
                self.custom_row = tk.Frame(left, bg=PANEL)
                tk.Label(self.custom_row, text="width", bg=PANEL, fg=MUTED,
                         font=theme.FONT_UI).pack(side="left")
                ttk.Entry(self.custom_row, textvariable=self.cw_var,
                          width=7).pack(side="left", padx=(4, 10))
                tk.Label(self.custom_row, text="height", bg=PANEL, fg=MUTED,
                         font=theme.FONT_UI).pack(side="left")
                ttk.Entry(self.custom_row, textvariable=self.ch_var,
                          width=7).pack(side="left", padx=4)
                tk.Label(self.custom_row, text="  any value 1..32767 (the immediates are 16-bit)",
                         bg=PANEL, fg=MUTED, font=theme.FONT_UI).pack(side="left")

                def _res_changed(*_a):
                    if self.res_var.get().startswith("Custom"):
                        self.custom_row.pack(anchor="w", pady=(6, 0))
                    else:
                        self.custom_row.pack_forget()
                self.res_var.trace_add("write", _res_changed)

                tk.Label(row, text="  currently %dx%d  -- boot-time only, not a live toggle"
                                   % (info.get('width', 0), info.get('height', 0)),
                         bg=PANEL, fg=MUTED, font=theme.FONT_UI).pack(side="left", padx=6)

    # ------------------------------------------------------------------ sections tab
    def _build_sections_tab(self):
        cols = ("type", "addr", "size", "flags", "comp")
        self.sec_tree = ttk.Treeview(self.tab_sec, columns=cols, style="Tree.Treeview")
        self.sec_tree.heading("#0", text="#")
        self.sec_tree.column("#0", width=50)
        for c, w in zip(cols, (110, 110, 110, 110, 70)):
            self.sec_tree.heading(c, text=c)
            self.sec_tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(self.tab_sec, orient="vertical", command=self.sec_tree.yview)
        self.sec_tree.configure(yscrollcommand=sb.set)
        self.sec_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        sb.pack(side="left", fill="y", pady=12)

    def _render_sections(self):
        self.sec_tree.delete(*self.sec_tree.get_children())
        if not self.rpl:
            return
        for i, s in enumerate(self.rpl.secs):
            self.sec_tree.insert("", "end", text=str(i),
                                 values=('%#x' % s[1], '%#010x' % s[3], format(s[5], ','),
                                         '%#x' % s[2],
                                         'zlib' if (s[2] & 0x08000000) else '--'))

    # ------------------------------------------------------------------ advanced tab
    def _build_advanced_tab(self):
        """Tools, not an essay.

        The first version of this tab only DESCRIBED what advanced editing involves, which is
        useless -- a tab that explains features it does not have is exactly the smoke-and-mirrors
        this project rejects. These are the operations that are actually safe to expose: symbol
        lookup, reading code, and SAME-SIZE word patching (capability L1, long proven). Growth
        (new sections, .bss) stays out because it needs a boot test per build, not a button.
        """
        top = tk.Frame(self.tab_adv, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(top, text="symbol / address", bg=BG, fg=MUTED,
                 font=theme.FONT_UI).pack(side="left", padx=(0, 6))
        self.adv_q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.adv_q, width=46)
        e.pack(side="left")
        e.bind("<Return>", lambda _ev: self._adv_search())
        ttk.Button(top, text="Find", style="Ghost.TButton",
                   command=self._adv_search).pack(side="left", padx=6)
        tk.Label(top, text="name fragment, or a VA like 0x0297b418", bg=BG, fg=MUTED,
                 font=theme.FONT_UI).pack(side="left", padx=8)

        panes = tk.PanedWindow(self.tab_adv, orient="horizontal", bg=BORDER, sashwidth=4, bd=0)
        panes.pack(fill="both", expand=True, padx=12, pady=6)

        lf = tk.Frame(panes, bg=BG)
        self.adv_tree = ttk.Treeview(lf, columns=("va", "size"), style="Tree.Treeview")
        self.adv_tree.heading("#0", text="symbol")
        self.adv_tree.column("#0", width=340)
        for c, w in (("va", 110), ("size", 80)):
            self.adv_tree.heading(c, text=c)
            self.adv_tree.column(c, width=w, anchor="w")
        asb = ttk.Scrollbar(lf, orient="vertical", command=self.adv_tree.yview)
        self.adv_tree.configure(yscrollcommand=asb.set)
        self.adv_tree.pack(side="left", fill="both", expand=True)
        asb.pack(side="left", fill="y")
        self.adv_tree.bind("<<TreeviewSelect>>", lambda _e: self._adv_show())
        panes.add(lf, minsize=380)

        rf = tk.Frame(panes, bg=BG)
        self.adv_txt = tk.Text(rf, bg=EDIT, fg=TEXT, relief="flat", wrap="none",
                               font=theme.FONT_MONO, height=18)
        self.adv_txt.pack(fill="both", expand=True)
        panes.add(rf, minsize=380)

        poke = tk.Frame(self.tab_adv, bg=PANEL)
        # pinned and reserved ahead of the expanding panes: this card holds the patch entry fields
        # and its Apply button, so losing it to a short window loses the feature entirely
        poke.pack(side="bottom", fill="x", padx=12, pady=(0, 6), before=panes)
        inner = tk.Frame(poke, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=10)
        tk.Label(inner, text="Same-size word patch", bg=PANEL, fg=TEXT,
                 font=theme.FONT_TITLE).pack(anchor="w")
        tk.Label(inner, bg=PANEL, fg=MUTED, font=theme.FONT_UI, justify="left", anchor="w",
                 wraplength=880,
                 text=("Write one 32-bit big-endian word at a virtual address in .text. This is "
                       "the length-neutral edit -- no offsets move, so nothing needs relinking. "
                       "⚠ If anything RELOCATES the instruction you overwrite, the loader masks "
                       "its own displacement into whatever opcode you leave there: prefer "
                       "keeping a branch and neutralising its effect over killing it."))\
            .pack(anchor="w", pady=(2, 6))
        row = tk.Frame(inner, bg=PANEL)
        row.pack(anchor="w")
        tk.Label(row, text="VA", bg=PANEL, fg=MUTED, font=theme.FONT_UI).pack(side="left")
        self.poke_va = tk.StringVar()
        ttk.Entry(row, textvariable=self.poke_va, width=14).pack(side="left", padx=(4, 12))
        tk.Label(row, text="word", bg=PANEL, fg=MUTED, font=theme.FONT_UI).pack(side="left")
        self.poke_w = tk.StringVar()
        ttk.Entry(row, textvariable=self.poke_w, width=14).pack(side="left", padx=4)
        ttk.Button(row, text="Write word", style="Accent.TButton",
                   command=self._adv_poke).pack(side="left", padx=10)
        ttk.Button(row, text="Export section...", style="Ghost.TButton",
                   command=self._adv_export_section).pack(side="left", padx=4)

        tk.Label(self.tab_adv, bg=BG, fg=MUTED, font=theme.FONT_UI, justify="left",
                 anchor="w", wraplength=940,
                 text=("Growth -- new sections, bigger .text/.bss -- is proven but deliberately "
                       "NOT a button here: it needs a boot test per build, FILEINFO must stay "
                       "the last section header, .rela entries accumulate across rebuilds, and "
                       "the code area is not readable on real hardware. rpl_editor/rpl_edit.py "
                       "implements it with a byte-identical round-trip gate."))\
            .pack(fill="x", padx=12, pady=(0, 12))

    def _adv_search(self):
        if not self.rpl:
            return
        q = self.adv_q.get().strip()
        self.adv_tree.delete(*self.adv_tree.get_children())
        if not q:
            return
        if q.lower().startswith('0x'):
            try:
                va = int(q, 16)
            except ValueError:
                return
            self.adv_tree.insert("", "end", text="(address)", values=('%#010x' % va, ''))
            self._adv_dump(va)
            return
        hits = [(nm, v, s) for nm, (v, s) in self.rpl.syms.items() if q.lower() in nm.lower()]
        hits.sort(key=lambda x: x[0])
        for nm, v, s in hits[:400]:
            self.adv_tree.insert("", "end", text=nm[:70],
                                 values=('%#010x' % v, format(s, ',') if s else ''))
        self.say("%d symbol(s) match %r%s" % (len(hits), q,
                                              " (showing 400)" if len(hits) > 400 else ""))

    def _adv_show(self):
        sel = self.adv_tree.selection()
        if not sel:
            return
        va = self.adv_tree.item(sel[0], 'values')[0]
        try:
            self._adv_dump(int(va, 16))
        except (ValueError, IndexError):
            pass

    def _adv_dump(self, va, words=48):
        """Words at a VA, with the branch targets resolved -- enough to find a patch point."""
        self.adv_txt.delete("1.0", "end")
        if not self.rpl:
            return
        lo = self.rpl.taddr
        hi = lo + len(self.rpl.text)
        if not (lo <= va < hi):
            self.adv_txt.insert("end", "%#010x is outside .text (%#010x..%#010x)" % (va, lo, hi))
            return
        for i in range(words):
            a = va + i * 4
            if a + 4 > hi:
                break
            w = self.rpl.word(a)
            note = ''
            tgt = RP._branch_target(w, a)
            if tgt is not None:
                note = '  -> %#010x%s' % (tgt, '  (bl)' if RP._is_bl(w) else '')
            self.adv_txt.insert("end", "%#010x  %08X%s\n" % (a, w, note))

    def _adv_poke(self):
        if not self.rpl or not self.path:
            return
        try:
            va = int(self.poke_va.get().strip(), 0)
            word = int(self.poke_w.get().strip(), 0) & 0xFFFFFFFF
        except ValueError:
            messagebox.showerror(APP_TITLE, "VA and word must be numbers "
                                            "(0x... for hex).")
            return
        try:
            r, _f = RP.survey(self.path)
            old = r.word(va)
            r.set_word(va, word)
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Write %#010x at %#010x?\n\nwas: %08X\nnow: %08X\n\nA stock backup is kept "
                    "as %s." % (word, va, old, word,
                                os.path.basename(RP.backup_path(self.path)))):
                return
            res = RP.write_patched(r, self.path, backup=True)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "%s: %s" % (type(ex).__name__, ex))
            return
        self.say("wrote %08X at %#010x (%s bytes)" % (word, va, format(res['bytes'], ',')), OK)
        self.open_file(self.path)

    def _adv_export_section(self):
        if not self.rpl:
            return
        sel = self.sec_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "Pick a section on the Sections tab first.")
            return
        i = int(self.sec_tree.item(sel[0], 'text'))
        p = filedialog.asksaveasfilename(title="Export section %d" % i,
                                         initialfile="section_%d.bin" % i)
        if not p:
            return
        try:
            import core.rpl_patch as _rp
            body = _rp._sec_bytes(self.rpl.data, self.rpl.secs[i])
            with open(p, 'wb') as f:
                f.write(body)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "%s: %s" % (type(ex).__name__, ex))
            return
        self.say("exported section %d (%s bytes, decompressed)" % (i, format(len(body), ',')), OK)

    def _unused_advanced_doc(self):
        return """ADVANCED RPL EDITING

An RPL can be grown -- new sections, bigger .text, bigger .bss -- and that capability is
proven (boot-tested builds B2/B3/B8/B12, and end-to-end code injection in D1-D4). It is
also the sharpest edge in this toolkit, so it is exposed here as documentation first and
tooling second.

The rules below were each paid for with a failed boot. Read them before editing .text.

1. A RELOCATION AND THE INSTRUCTION UNDER IT ARE TWO SEPARATE FACTS.
   Killing a relocated `bl` does NOT remove its relocation. The loader masks its own 24-bit
   displacement into whatever opcode now sits there -- a `nop` becomes `ori rX,rY,Z` with
   near-arbitrary register fields. You get an arbitrary register clobbered at an arbitrary
   point, which is far harder to attribute than a restored call.
   PREFER: keep the branch, neutralise the callee's effect. That leaves .rela.text untouched
   and the invariant self-enforcing.

2. HOOK AT A FUNCTION ENTRY. Entries carry no relocation.

3. TO CALL AN IMPORT FROM NEW CODE you must append a .rela.text REL24 entry, or branch to the
   existing import stub (PC-relative, no relocation needed).

4. .rela.text ENTRIES ACCUMULATE ACROSS REBUILDS. Purge the host range BEFORE re-adding, never
   after. A purge count higher than the previous build's site count means you just deleted
   your own relocations.

5. FILEINFO MUST REMAIN THE LAST SECTION HEADER. The loader finds it by position; appending
   past it yields "Invalid FILEINFO magic" and a crash.

6. EVERY SECTION BODY IS CRC'd over DECOMPRESSED bytes in SHT_RPL_CRCS -- including FILEINFO.
   Any hand edit must update it.

7. THE CODE AREA IS NOT READABLE ON HARDWARE. A trampoline with an embedded literal pool
   faults on a real console even where Cemu tolerates it.

TOOLING: rpl_editor/rpl_edit.py implements the above with a byte-identical round-trip gate
(PASS on both retail RPLs). It is intentionally NOT wired to a button here: authoring code is
a scripting job with a boot test attached, not a click.

WHAT THIS TAB DOES GIVE YOU: the Sections tab shows the real section table, and the Patches
tab performs the three edits that are structurally located, verified, and reversible via the
automatic .stock backup.
"""

    # ------------------------------------------------------------------ actions
    def open_file(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title="Open engine RPL",
                filetypes=[("Wii U RPL/RPX", "*.rpl *.rpx"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.rpl, self.found = RP.survey(path)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Could not read this RPL:\n\n%s: %s"
                                 % (type(ex).__name__, ex))
            return
        self.path = path
        try:
            from core import settings as _s
            _s.note_opened(path)
        except Exception:
            pass
        self.title("%s -- %s" % (APP_TITLE, os.path.basename(path)))
        self.head.configure(text="%s   %d symbols   .text %s bytes"
                                 % (os.path.basename(path), len(self.rpl.syms),
                                    format(len(self.rpl.text), ',')))
        self._render_patches()
        self._render_sections()
        self.btn_apply.configure(state="normal")
        bp = RP.backup_path(path)
        self.btn_restore.configure(state="normal" if os.path.exists(bp) else "disabled")
        avail = sum(1 for i in self.found.values() if i['ok'] and not i.get('already'))
        self.say("opened %s -- %d patch(es) available" % (os.path.basename(path), avail), OK)

    def _apply(self):
        if not self.rpl:
            return
        want = [n for n, v in self._vars.items() if v.get()]
        if not want:
            messagebox.showinfo(APP_TITLE, "Tick at least one patch to apply.")
            return
        # Re-open so the edit is applied to the file as it is on disk right now.
        try:
            r, _f = RP.survey(self.path)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "%s: %s" % (type(ex).__name__, ex))
            return
        done, failed = [], []
        for name in want:
            try:
                if name == 'signature':
                    RP.apply_signature(r)
                elif name == 'dlc_loadgate':
                    RP.apply_dlc_loadgate(r)
                elif name == 'resolution':
                    label = getattr(self, 'res_var', None)
                    sel = label.get() if label else ''
                    if sel.startswith('Custom'):
                        try:
                            w, h = int(self.cw_var.get()), int(self.ch_var.get())
                        except ValueError:
                            raise RuntimeError('custom resolution must be two whole numbers')
                    else:
                        w, h = next(((a, b) for t, a, b in RES_PRESETS
                                     if t == sel and a), (1600, 900))
                    RP.apply_resolution(r, w, h)
                done.append(name)
            except Exception as ex:
                failed.append((name, str(ex)))
        if not done:
            messagebox.showerror(APP_TITLE, "Nothing was applied:\n\n"
                                 + "\n".join("%s: %s" % f for f in failed))
            return
        try:
            res = RP.write_patched(r, self.path, backup=True)
        except Exception as ex:
            messagebox.showerror(APP_TITLE, "Write failed:\n\n%s: %s"
                                 % (type(ex).__name__, ex))
            return
        msg = ["Applied: %s" % ", ".join(done)]
        if res['backup']:
            msg.append("Stock backup: %s%s" % (os.path.basename(res['backup']),
                                               "  (created now)" if res['backup_created']
                                               else "  (kept from an earlier patch)"))
        if failed:
            msg.append("\nNot applied:\n" + "\n".join("  %s: %s" % f for f in failed))
        msg.append("\n⚠ Patch the OTHER RPL too -- t6_cafef_rpl and t6mp_cafef_rpl each "
                   "carry their own copy of this code.")
        messagebox.showinfo(APP_TITLE, "\n".join(msg))
        self.say("wrote %s (%s bytes)" % (os.path.basename(self.path),
                                          format(res['bytes'], ',')), OK)
        self.open_file(self.path)

    def _restore(self):
        if not self.path:
            return
        bp = RP.backup_path(self.path)
        if not os.path.exists(bp):
            return
        if not messagebox.askyesno(APP_TITLE, "Restore the stock RPL from\n\n%s\n\nThis "
                                              "discards every patch applied to this file."
                                              % os.path.basename(bp)):
            return
        import shutil
        shutil.copy2(bp, self.path)
        self.say("restored stock from %s" % os.path.basename(bp), OK)
        self.open_file(self.path)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    run_standalone(RplIDE, initial=args[0] if args else None,
                   title=APP_TITLE, size="1100x760", minsize=(900, 600))


if __name__ == "__main__":
    main()
