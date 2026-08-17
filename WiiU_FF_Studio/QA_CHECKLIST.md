# WII U T6 Studio tester checklist

Work through whichever sections apply to what you use. You don't need to do all of it.

**Before starting, please note:** your Windows version, whether your Desktop is redirected to
OneDrive, and whether your engine RPL was already signature-patched before you installed this.
Several things behave differently depending on those.

**For any failure, the useful details are:** what you clicked, the exact file (name and size),
what you expected, what happened. If the program closes without a message, say so explicitly,
that's a different bug from an error dialog.

⚠️ **Work on copies.** Nothing here should be tested against files you can't replace. The tool
backs up RPLs automatically but not much else.

---

## 0. First run

- [ ] Program starts without a console window appearing behind it
- [ ] Welcome screen lists all four file types with coloured bars on the left
- [ ] Icon appears in the taskbar and title bar
- [ ] `--selftest` runs and reports counts. Note anything that says `FAIL`, and note how many say `SKIP`
- [ ] Startup signature warning: did one appear? If yes, did it name a file path that makes sense for your setup? If your RPL was already patched, you should see nothing at all

---

## 1. Opening files

- [ ] Open `.ff`, `.ipak`, `.sabs`, `.sabl`, `.rpl`. Each shows a different layout
- [ ] Open several at once, mixed types. Tabs appear with a coloured dot per type
- [ ] Switch between tabs. Content changes, nothing bleeds through from the previous tab
- [ ] Close a tab with `Ctrl+W`
- [ ] The `+` button opens another file
- [ ] `Ctrl+S` saves only the tab you're looking at, not all of them
- [ ] Open the same file twice. You should be warned
- [ ] Drag a file onto the window (if supported for that type)
- [ ] **Game folders**. Add your `content` folder, confirm it's remembered after a restart

**Watch for:** fonts or row heights changing in one tab when you open another. That was a real bug; it should be gone.

---

## 2. Texture paks (`.ipak`)

### Browsing
- [ ] Entry list populates; names are readable rather than hex for most entries
- [ ] Click column headers to sort. Sizes and dimensions sort numerically, not as text
- [ ] Filter box narrows the list
- [ ] Select an entry. Preview draws, info panel fills in
- [ ] `alpha` checkbox toggles the alpha view

### Gallery
- [ ] Switch **List → Gallery**. Thumbnails appear in a grid across the full width, not one column
- [ ] Scroll. Thumbnails fill in as you go rather than all at once
- [ ] Tiles that can't be decoded say `no preview` rather than sitting blank forever
- [ ] Size slider resizes tiles and the grid reflows to a sensible column count
- [ ] Click a tile. It loads into the detail panel, same as clicking the list row
- [ ] Switch back to List. Your selection is still there
- [ ] Close and reopen the pak. It comes back in whichever view you were last in

### Editing (on a copy)
- [ ] Export a texture as PNG. Image looks correct
- [ ] Export ALL as PNG to an empty folder
- [ ] Export raw payload
- [ ] Replace a texture with a PNG of the same dimensions
- [ ] Replace with a **DDS** file. This matters, it's what PC mods ship
- [ ] Replace with different dimensions. You should be told it will be resized
- [ ] **Shadowing warning:** replace a texture that also exists in another pak. You should get a warning naming the other paks before the file dialog opens. Try `base_split*` or `lowmip_split*` entries. Those are the common case
- [ ] Add a new texture
- [ ] Delete an entry
- [ ] Save, reopen, confirm your change survived
- [ ] **Check key/crc consistency** from the Tools menu

---

## 3. Fastfiles (`.ff`)

### Browsing
- [ ] Open any `.ff`. The **container facts** populate in the header/status area (asset count, block sizes). This one matters more than it looks: the reader behind it now depends on a module that lives outside the bundle's own folder, so if it were missing from the packaged build this line is where it would show up. A blank or zeroed panel, or an error mentioning `SubstrateDependencyMissing`, is a report-worthy bug
- [ ] Asset tree populates and is grouped by type
- [ ] Select a GSC script. Disassembly appears
- [ ] **Decompile** produces readable output
- [ ] Select a Lua/HKS asset
- [ ] Select a raw text/cfg/csv file. Editable as text
- [ ] Select a model. 3D preview rotates on drag, zooms on wheel, LOD selector works
- [ ] Select something with no editor. Hex view, import/export greyed out

### Zone textures
- [ ] **Textures in this zone…** opens and lists images
- [ ] List view shows all columns; sorting and filter work
- [ ] Gallery view works, multi-column
- [ ] Streamed entries name the pak they come from
- [ ] Export PNG from an inline image
- [ ] Replace an inline image. **The preview should update to show your new image immediately**
- [ ] Replace at a *different* resolution. Inline images allow this
- [ ] **Undo staged** puts it back
- [ ] Save, reopen, confirm it stuck

### Zone sound banks
- [ ] **Sound banks in this zone…** opens
- [ ] Rows list the banks the zone references
- [ ] **Open selected bank file** hands it to the sound editor

### Saving
- [ ] **Save plan (dry run)** describes what will happen
- [ ] **Relink census (dry run)** runs
- [ ] Edit a script so it grows, save, reopen, confirm the change
- [ ] If a save is refused with a message about **placing the substitution boundary in block-5 space**, that is a deliberate new safety check, not a crash. Please still report it, and say which zone and which asset, because each case tells us where the check needs more anchors. What it is preventing: the old code decided where pointers moved using a rule that was measurably wrong, and produced zones that opened fine, passed every check, and then failed silently in game
- [ ] Try `common.ff`. It should **refuse** with a clear explanation about a known limitation, not a raw error
- [ ] Try `ui_mp.ff` and `ui_zm.ff`. These should now save

---

## 4. Sound banks (`.sabs` / `.sabl`)

⚠️ **A crash was reported here and is unconfirmed.** Please be thorough and specific.

- [ ] Open a `.sabs`. Does it open at all?
- [ ] Open a `.sabl`. Does it open at all?
- [ ] If either crashes: which file, what size, and did the window appear first or never at all?
- [ ] Entry list populates
- [ ] Select an entry. Waveform draws
- [ ] Select several in a row, including a long one
- [ ] Select a **stereo** entry specifically
- [ ] **Play**. Does it sound right? Correct pitch and speed, not chipmunk or slowed
- [ ] **Stop** interrupts immediately
- [ ] Play a stereo entry. Both channels present, not noise
- [ ] Extract to WAV. Opens in another player, sounds the same
- [ ] Extract all
- [ ] Replace an entry with a WAV, save, reopen, play it back
- [ ] Add an entry
- [ ] Delete an entry
- [ ] Filter box and column sorting

**Please note:** whether any sound plays at the wrong speed, and if so whether it was mono or stereo. Sound bank layout was reworked recently and stereo handling changed.

---

## 5. Engine RPLs (`.rpl` / `.rpx`)

Test on a **copy** first.

- [ ] Open `t6_cafef_rpl.rpl`. Patches list appears with current state
- [ ] It correctly reports which patches are already applied
- [ ] Apply the signature patch, reopen, confirm it now reads as applied
- [ ] `<name>.stock` appears next to the file
- [ ] Apply a second patch. The `.stock` backup is **not** overwritten (check the timestamp)
- [ ] **Restore stock** puts the original back
- [ ] Resolution: pick a preset, apply, reopen. It reads back correctly
- [ ] Resolution: **custom** value. Apply, reopen. *Known issue: it may read back as "not present". The patch still applied. Confirm whether you see this*
- [ ] DLC load gate applies
- [ ] Sections tab lists sections
- [ ] Advanced: symbol search finds something like `R_GetWndParms`
- [ ] Advanced: code view shows disassembly with branch targets
- [ ] Advanced: export a section
- [ ] Open a **non-engine** `.rpl`. It should say what it can't find rather than misbehaving

---

## 6. Find asset

- [ ] `Ctrl+F` or the toolbar button opens it
- [ ] Search a partial texture name. Results appear
- [ ] The **where** column names an actual file
- [ ] Search a texture that lives inside a zone rather than a pak. It should still be found
- [ ] Paste a raw hash like `0A0FEE98`
- [ ] Nonsense query returns nothing rather than an error
- [ ] Select a result. Details show every part and which paks hold them
- [ ] Rows marked ⚠ indicate more than one pak has a part
- [ ] **Open** loads the file. Works for both paks and fastfiles
- [ ] **Show in folder** opens Explorer with the file selected
- [ ] Coverage line at the top reports a sensible number of paks

---

## 7. Bulk replace

Use copies. This writes to multiple files at once.

- [ ] Opens from the toolbar
- [ ] **Add files…** with a multi-selection
- [ ] **Add folder…** walks subfolders
- [ ] **Drag files onto the window**. Should work
- [ ] **Drag a folder onto the window**. Should also work
- [ ] Table shows matched / unmatched per file
- [ ] Name matching is lenient. `gme_wall_col.dds` should match the game's decorated name
- [ ] A file named for a raw hash matches
- [ ] Deliberately include a file that matches nothing. It's reported, the rest still proceed
- [ ] **Replace all** asks for confirmation and lists the files it will touch
- [ ] Progress bar moves; **Stop** works
- [ ] Log at the end says what landed and what didn't
- [ ] `.orig` backups appear next to modified fastfiles
- [ ] Reopen a modified file and confirm the change

---

## 8. General

- [ ] Resize the window. Panels behave, nothing gets clipped or stuck
- [ ] Maximise and restore
- [ ] Leave a long operation running and cancel it partway
- [ ] Status bar credit link opens the GitHub page
- [ ] **?** About panel opens and reads sensibly
- [ ] Settings survive a restart: game folders, last view per file type
- [ ] Nothing writes to your game folders unless you explicitly saved there

---

## 9. In-game verification

The only test that really counts. Everything above proves files are *well-formed*, not that the
console accepts them.

- [ ] Signature-patched RPL deployed
- [ ] Cold start. Fully closed, not a reload
- [ ] Edited texture appears in game
- [ ] Edited sound plays, at the right speed
- [ ] Edited script behaves
- [ ] Resolution patch takes effect
- [ ] Game doesn't crash on the map you touched

If a texture edit doesn't show, check **Find asset** for that texture before reporting it, a
pak that loads earlier will win, and that's expected behaviour rather than a bug.

---

## Known issues, no need to report these

- Custom RPL resolution reads back as "not present" after applying. The patch works; the read-back doesn't recognise it.
- `common.ff` can't be saved (VehicleDef record the walker can't pass). Roughly 17 of 155 stock zones are affected.
- Sound playback is Windows-only.
- Emblem textures (`em_*`, A8L8 format) have an all-zero alpha channel. They're shown opaque on purpose; that's how the game stores them.
- Some pak entries have no format info and can't be previewed or replaced, only extracted raw.
- 3D model preview has no textures or materials.
