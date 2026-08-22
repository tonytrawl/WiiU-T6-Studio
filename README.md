# Wii U T6 Toolkit — PC → Wii U (Black Ops II) map conversion

A shareable snapshot of the tools for converting **PC (Plutonium/T6) Black Ops II** fastfiles into
**Wii U (v148)** fastfiles + ipaks. Code and documentation only — **no game data is included** (see
"What you supply" below).

> This is a code/docs snapshot for collaborators. It intentionally excludes internal working notes,
> build artifacts, and any copyrighted game content (zones, fastfiles, ipaks, RPLs).

## Layout

```
native\_linker/     the from-scratch PC→console converters + zone walker + assembler (Python)
wiiu\_ref/          Wii U format probes, GX2 texture tiling, ipak authoring, techset tools (Python)
                   + \*\_findings.md mechanism notes (how each format works)
tools/
  ff\_decrypt.py    decrypt/decompress any T6 fastfile (PC v147 / WiiU v148 / X360 v146)
  salsa20.py       stream cipher used by the fastfile format
  ref\_oat/src/     the EXTENDED OpenAssetTools source (our Wii U console read/write additions)
WiiU\_FF\_Studio/    the desktop GUI (tkinter) for the flat conversions + console tools
docs/              format / reverse-engineering findings (how the conversion works)
```

## Components

### `native\_linker/` — the native conversion pipeline

Converts PC zone assets to Wii U console layout, byte-validated against genuine console zones as an
oracle. Key modules:

* `pc\_convert\_pipeline.py` — end-to-end orchestrator (unlink → convert → repack → author ipak). Also
what the GUI calls.
* `pc\_zone.py` / `zone\_stream.py` — PC (LE) zone reader / console (BE) block-stream writer.
* `pc\_walk.py` — the asset dispatcher that walks a PC map zone end-to-end (routes each asset type to
its per-type probe/consumer).
* Per-type converters: `material\_convert.py`, `xmodel\_convert.py`, `fx\_convert.py`,
`techset\_translate.py` (genuine-console substitution), `pc\_to\_console.py` (simple/world types),
and the GfxWorld set (`gfxworld\_\*.py` — body/dynamics/geometry).
* `\*\_pc.py` — PC-side span/extent parsers used by the walker.
* `validate\_\*.py` — matched-pair oracle harnesses (convert PC body, diff vs genuine console).

### `wiiu\_ref/` — Wii U format primitives

* `gx2\_texture.py` — full Latte GX2 tile/detile (2D + cube faces).
* `ipak.py` / `ipak\_stream.py` — read/author Wii U ipaks; pull PC image sources by name-hash.
* `pc\_image\_enum.py` — enumerate a PC zone's GfxImages.
* `gfxworld\_probe2.py`, `xmodel\_probe.py`, `shader\_probe.py`, etc. — both-platform structure probes.
* `rpl\_sigpatch.py` — patch a T6 engine RPL to load custom (zeroed-signature) fastfiles.
* `\*\_findings.md` — the mechanism notes for each area (read these to understand the formats).

### `WiiU\_FF\_Studio/` — desktop GUI

tkinter front-end (pure stdlib, freezes to a single EXE). Pages: fastfile↔zone, batch convert,
PC-fastfile → Wii U + ipak pipeline, zone inspect/validate/edit, RPL signature patch. See its
`README.md` / `USAGE.md`. All the tools are not baked in yet as they are still being worked on.

## Requirements

* **Python 3.10+** for the tools (`native\_linker`, `wiiu\_ref`, `tools`, the GUI). The GUI is
stdlib-only; some `wiiu\_ref` RE helpers use `capstone` (only where noted).
* **A C++ toolchain** (the OAT upstream build setup) if you want to build `ref\_oat`.

## Quick start

```bash
# decrypt a PC fastfile to its raw zone
python tools/ff\_decrypt.py <map.ff> <out.zone>

# end-to-end: PC fastfile -> Wii U fastfile + ipak  (see the module header for options)
python native\_linker/pc\_convert\_pipeline.py <pc\_map.ff> <out\_dir> \[--console-ref <wiiu .ff/.zone>]

# author a Wii U ipak from a PC map's images
python wiiu\_ref/pc\_image\_enum.py <pc\_map.zone> <meta\_dir>
python wiiu\_ref/ipak\_stream.py prepare <meta\_dir> <out\_dir> --ipak <map>.ipak --pc-ipaks <base.ipak> <mp.ipak> <dlcN.ipak>

# or launch the GUI
python WiiU\_FF\_Studio/wiiu\_ff\_studio.py
```

## How it works (docs)

Start with `docs/WIIU\_MAP\_CONVERSION.md`, then the format findings in `docs/` and the `\*\_findings.md`
notes under `wiiu\_ref/`. Each converter's module header documents its layout map and validation.

## Current state — honest, per component

Status legend: ✅ built \& validated · 🟡 partial / WIP · ⬜ not built. "Byte-exact" = output matches a
genuine console asset byte-for-byte (matched-pair oracle). "HW-confirmed" = loaded/rendered correctly
on Cemu.

### Fastfile I/O \& console tooling

|Tool|State|Truth|
|-|-|-|
|`tools/ff\_decrypt.py`|✅|Decrypts PC v147 / WiiU v148 / X360 v146. Solid.|
|`WiiU\_FF\_Studio` pack/decrypt|✅|WiiU v148 pack is **boot-confirmed**; flat conversions (ff↔zone, batch) work.|
|`wiiu\_ref/rpl\_sigpatch.py`|✅|Reproduces the installed working RPLs byte-exact; custom (zeroed-sig) ffs load.|
|`WiiU\_FF\_Studio` pipeline page|🟡|Wraps `native\_linker` pipeline — inherits its state below (bootable only with a console backbone).|

### Geometry \& GfxWorld

|Tool|State|Truth|
|-|-|-|
|GfxWorld geometry (`gfxworld\_dynamics/assemble`)|✅ HW-confirmed|vd0 (group-aware 36B), vd1 (swap2), indices, surfaces + material-ptr relocation render correct on Cemu. Offset = stored `vertexDataOffset0` (no console reorder).|
|GfxWorld body/dynamics|✅ HW-confirmed|Body + all dynamic regions convert; validated vs oracle.|
|Lighting exactness|🟡|Tangent + vd1 second-UV are console-repacked → map renders slightly darker (cosmetic; geometry unaffected).|
|No-backbone region generators (`gfxworld` novel synthesis)|🟡 WIP|The console-only regions for a map with no console counterpart. Breakdown: \~7.2 MB GX2 textures (existing image pipeline, cubemaps verified 6/6), \~4.2 MB PC-sourced bounded conversions, **\~90 KB genuinely-new synthesis** (streamInfo / sortedSurfIndex / smodelCastsShadow). In progress.|

### Per-asset converters (`native\_linker`)

|Tool|State|Truth|
|-|-|-|
|`material\_convert.py`|✅|437/446 byte-exact (the 9 differ by 2 bytes in a sort hash — low-impact, loadable). Console Material = 104 B.|
|`xmodel\_convert.py` (rigid)|✅|body+bones+surfaces(128B)+materialHandles+collSurfs+boneInfo+physPreset; full driver 186/0 clean resync.|
|`xmodel\_convert.py` inline-material **image** emission|🟡 REQUIRED, WIP|Emits material handles but **not the inline GfxImage pixels** → bodies are truncated → **not yet loadable** for maps with inline models (mp\_skate has 466 inline XModels). Wiring the existing GX2 image path into the inline-material branch; not new RE.|
|`xmodel\_convert.py` skinned surfaces|⬜|`vertsBlend` swaps cleanly, but the 3 Latte skin-streams are **not derivable from PC data** (confirmed independently by OAT). Plan: emit rigid (stream-valid, bind-pose) + loader-tolerance test; real synthesis is a later item. Blocks zombies characters/weapons.|
|`xmodel\_convert.py` collmaps chain|⬜|109/465 models; collision, deferrable past a first render.|
|`fx\_convert.py`|🟡|FxEffectDef header 388/388 byte-exact; the FxElemDef body/curve tail is not yet wired.|
|`techset\_translate.py`|✅ (substitution)|Substitutes a genuine console techset per PC techset via **name grammar**; mp\_skate = 0 unresolved (202 exact + 34 struct + 5 prefix). Not byte-identical (it's a valid *substitute* console shader, not a transcode). D3D→GX2 shader recompilation is intentionally NOT done.|
|`pc\_to\_console.py` (simple/world)|✅|StringTable/KVP/RawFile/… + ComWorld/MapEnts/GameWorldMp/clipMap byte-exact.|
|`sndbank\_pc.py`|✅ (span)|PC SndBank is byte-identical to WiiU → byte-copy; walker sizes it correctly.|
|`validate\_\*.py`|✅|Matched-pair oracle harnesses; the discipline behind every "byte-exact" claim above.|

### Walk / images / assemble

|Tool|State|Truth|
|-|-|-|
|`pc\_walk.py` (zone traversal)|✅ / ⬜|Reaches **end-of-zone on mp\_skate (840) + raid**. **WEAPON consumer not built** → blocks any map with inline weapons (nuketown MP + all ZM maps, \~100/zm map).|
|`pc\_image\_enum.py` + `ipak\_stream.py` + `gx2\_texture.py`|✅|Author a WiiU ipak from PC images; **byte-exact vs retail** (mp\_la 287/287). GX2 tiling covers 2D + cube faces.|
|`dlc\_packs.py` (DLC source auto-select)|✅|DLC maps stream from `dlcN`/`dlczmN.ipak`; auto-selects the right pack (mp\_skate skips 397→7).|
|Asset-list authoring (`\_assetlist\_author.py`)|✅ (foundation)|Console order + type remap byte-exact on 2 MP maps; string table reused verbatim; MP console-only inserts characterized.|
|`pc\_convert\_pipeline.py` (with console backbone)|✅|For a map that **exists on Wii U**, produces a bootable ff (backbone splice) + ipak.|
|No-backbone whole-zone assembler (`produce\_nobackbone.py`)|🟡 WIP|The path that authors a complete console zone from PC alone (the goal). Raid-oracle control runs; **not yet producing a bootable no-backbone ff** — gated on the XModel inline-image emission + region generators + assemble wiring above.|

### `tools/ref\_oat/src` — extended OpenAssetTools

||State|Truth|
|-|-|-|
|Console read/write (per-struct)|✅ (byte oracle)|Emits/reads individual console asset structs — useful as a **byte reference** for validating the native converters.|
|Bootable output|⚠️ **never**|OAT has **never produced a bootable Wii U ff** — it leaves dangling cross-asset world pointers on load (this is *why* the native `native\_linker` pipeline exists). Do not treat OAT output as a working target; use it as a per-struct oracle only.|
|Techset write|⬜|Emits **null shader subtrees** (no D3D→GX2 transcode) — real shaders come from genuine-blob substitution.|
|"Siege-skin" work|✅ (transplant)|`ConsoleSiegeSkinTail.h` is the GfxWorld GPU-skinning *shaders* transplanted verbatim — NOT the XModel skinned skin-streams (those remain unsolved, see above).|

## License / attribution

`tools/ref\_oat` derives from OpenAssetTools (see its upstream license). The Wii U additions and the
`native\_linker`/`wiiu\_ref` tooling are this project's work. Game assets are the property of their
respective owners and are not included or redistributed here.

