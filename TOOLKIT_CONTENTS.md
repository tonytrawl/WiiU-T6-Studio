# Toolkit contents — snapshot 2026-07-27

This folder is a **publish target**: a point-in-time copy of the project's own tooling, refreshed from the
live working tree. Nothing here is authoritative — the working tree is. Editing files here does not affect
the project, and this snapshot never overwrites anything outside this folder.

Previous snapshot was 2026-07-12. This refresh: **368 new files, 26 updated, 283 unchanged** — 684 files, 7.3 MB.

## What's here

| folder | what it is |
|---|---|
| `native_linker/` | the PC→Wii U zone converter and linker: asset converters, the loader simulator, the runtime-allocation (`rt`) model, gate batteries, validators |
| `wiiu_ref/` | format libraries and probes — `ipak` / `ipak_stream` (texture paks), `gx2_texture` (tiling, BC decode, PNG), `wiiu_zone`, `walker`, `techset_extract`, plus per-asset probes and the findings notes that go with them |
| `WiiU_FF_Studio/` | fastfile GUI + library: decrypt/pack, asset listing, SAB conversion, RPL signature patching |
| `rpl_editor/` | **new since the last snapshot** — full RPL read/modify/write with byte-identical round-trip and working append-growth |
| `dlc loading/` | DLC infrastructure: load-zone assembly, patch relink, additive-growth work (`add_asset`, `grow_relink`, `verbatim_gate`) |
| `mod_loader_patch/` | the RPL hook + Lua mod-loader menu patch |
| `tools/` | standalone utilities: `ff_decrypt`, `ff_pack`, `zone_info`, `zone_validate`, `gsc_extract`, `stfs_extract`, `salsa20`, `asset_dir`, `explore`, and the `bo2_tool_gui` front-end |
| `docs/` | findings and handoffs, refreshed where a newer copy exists |

**Start with `docs/HANDOFF_pipeline_bake_rules.md`** — it is the distilled ruleset (what breaks, why, where in
the pipeline to fix it, and the assert that catches it), and it carries an explicit evidence standard so you
can tell a boot-proven rule from a structurally-gated one.

## What is deliberately NOT here

- **OAT (Open Asset Tools) and everything derived from it** — `tools/ref_oat` and its build logs.
- **Other people's tools** — `tools/gsc-tool`, `ref_jezuz`, `ref_hydrax`, `ref_py360`.
- **Game assets** — no `.ff`, `.ipak`, `.zone`, `.rpl`, `.sabs`, `.bin`, `.techset`, no dumps. The source
  directories are dominated by these (19 GB of logs in `native_linker`, 4.8 GB of paks under `dlc loading`),
  so this snapshot is a strict source whitelist rather than a mirror.
- **Per-boot forensic probes** — scripts written against one crash dump (`_b41_*`, `_boot21_*`, `_vfy*`,
  `_cm3x_*`, and similar). They reference dumps that no longer exist and are session debris, not tools.
- **Build output** — `__pycache__`, PyInstaller `build/`, `.pyc`.

## Caveats worth knowing before you run anything

- Many scripts carry **absolute paths** to the author's working tree and to game files that are not
  distributed. Expect to edit paths; expect some scripts to need a zone or dump you do not have.
- The `native_linker` scripts prefixed `_` are working instruments, not a stable API. The stable entry points
  are the named modules (`pc_to_console.py`, `produce_container.py`, `material_convert.py`, `loader_sim.py`,
  `zone_gates`-style validators).
- `wiiu_ref/clipmap_console.py` imports `clipmap_probe`, which lives in `wiiu_ref/` — put **both**
  `native_linker/` and `wiiu_ref/` on `sys.path`.
- Nothing here decrypts or redistributes game content. The fastfile crypto is a key the tools use; you supply
  your own files.

## Not refreshed

`README.md` still dates from the 2026-07-12 snapshot and describes that state.
