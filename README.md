<div align="center">

# WII U T6 Studio

**Fastfiles, texture paks, sound banks and the engine itself, in one window.**

Edit Call of Duty: Black Ops II on Wii U without a pile of single-purpose tools.

[![Latest release](https://img.shields.io/github/v/release/tonytrawl/WiiU-T6-Studio?style=for-the-badge&logo=github&logoColor=17130a&label=RELEASE&labelColor=17130a&color=e8a33d)](https://github.com/tonytrawl/WiiU-T6-Studio/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/tonytrawl/WiiU-T6-Studio/total?style=for-the-badge&label=DOWNLOADS&labelColor=17130a&color=4d8fd6)](https://github.com/tonytrawl/WiiU-T6-Studio/releases)
[![Platform](https://img.shields.io/badge/PLATFORM-WINDOWS-7cb342?style=for-the-badge&logo=windows&logoColor=eef3f8&labelColor=17130a)](#-why-windows-only)
[![License](https://img.shields.io/badge/LICENSE-GPL%20v3.0-b07cd6?style=for-the-badge&labelColor=17130a)](LICENSE)

[![Buy me a coffee](https://img.shields.io/badge/SUPPORT%20MY%20WORK-BUY%20ME%20A%20COFFEE%20%E2%98%95-e8a33d?style=for-the-badge&labelColor=17130a&logo=buymeacoffee&logoColor=17130a)](https://buymeacoffee.com/tonytrawl)

</div>

* * *

Black Ops II on Wii U keeps its content in four containers. This tool edits, authors and grows all
of them.

## What you can do with it

* Swap textures in texture paks and inside fastfiles
* Edit GSC and CSC scripts, with a decompiler and an editable assembly view
* Compile Lua and HKS from source
* Edit text, cfg and csv files, and make them bigger than they were
* Add new scripts and raw files to a zone
* Replace sounds, extract them to WAV, and add or delete entries
* Preview models in 3D
* Patch the engine so edited files will actually load
* Change the game's internal render resolution
* Search every pak and zone by texture name to find where something lives
* Replace hundreds of textures in one pass from a folder

It opens `.ff` fastfiles, `.ipak` texture paks, `.sabs` / `.sabl` sound banks and the engine's own
`.rpl` / `.rpx` modules. The window changes to suit whatever you opened, and several can be open at
once as tabs.

* * *

## ⚠️ Read this first

Two things to set up before your first edit. Skip either one and your work will not load, or will
load without names and warnings.

### 1. Patch the signature check

A fastfile written by this tool, or by any tool, has **no valid RSA signature**. Nobody outside
Treyarch has the key. An unpatched console checks that signature and refuses the file, which shows
up as a crash or a hang the moment the zone is requested. It happens even when you save a zone with
no changes at all.

1. Open the **RPL** tab.
2. Open `t6_cafef_rpl.rpl`, apply **Fastfile signature check**.
3. Open `t6mp_cafef_rpl.rpl`, apply the same patch.

Patch **both**. `t6_cafef_rpl` loads first and carries its own copy of the same engine code. The
tool checks on startup and tells you if it looks unpatched. Your original is copied to
`<name>.stock` before the first edit and that backup is never overwritten.

### 2. Point the tool at your game folders

Texture entries store a numeric hash and no name. Readable names, formats and dimensions come from
your own paks and zones, and so does the warning that the texture you are editing also exists in a
pak that loads earlier. Without a game folder you get hex ids and no warnings.

Click **Game folders** in the toolbar and add the `content` folder of your Wii U install, the one
holding the `.ipak` and `.ff` files. It looks like this:

```
content/
  base_split0.ipak … base_split8.ipak
  mp_nuketown_2020.ipak
  english/
    common_patch_mp.ff
    patch_mp.ff
```

**Cemu installs are found automatically.** The tool checks Cemu's own data folder for both title
categories:

| | Path |
|---|---|
| Update | `<Cemu>/mlc01/usr/title/0005000e/1010cf00/content` |
| DLC | `<Cemu>/mlc01/usr/title/0005000c/1010cf00/content` |

`<Cemu>` is `%APPDATA%\Cemu` on Windows, `~/Library/Application Support/Cemu` on macOS, and
`$XDG_DATA_HOME/Cemu` or `~/.cemu` on Linux. A Wine or Proton prefix is checked too.

Three other things are searched without any configuration:

* The folder you opened a file from, and its parent.
* The folder the program itself sits in, or a `content` folder inside it. Drop the exe into your
  game folder and it just works.
* `WIIU_CONTENT_DIR`, if you set it. Separate multiple paths the way your OS separates `PATH`.

The **Game folders** window lists everything currently being searched, marked `[configured]` or
`[auto]`. Settings are stored in `wiiu_ff_studio.json` next to the program.

> The disc image is not searched and should not be edited. Maps belong in the DLC folder, patched
> zones in the update folder.

* * *

## 🗂️ Fastfiles (`.ff`)

Browse every asset in a zone.

**Scripts.** GSC and CSC get a disassembly listing plus an editable assembly view, with or without
the source. The decompiler lands around 98% on retail bytecode. Lua and HKS get the same treatment
and compile from source. Text, cfg and csv files are edited directly.

**Growing a zone.** Scripts and raw files can get bigger. The zone is rebuilt and every pointer
re-pointed, then re-walked. **A file whose walk broke is refused rather than written.**

**Models.** A software preview with geometry and shading, enough to identify something but not to
judge how it looks in game.

**Inline textures.** Zones carry their own textures, often hundreds, embedded in materials and FX
rather than in the asset list. Open them with the **Textures** button to browse, export and
replace. Unlike pak textures these can change resolution, because the record is rewritten with the
pixels.

Feed one a larger image and you are offered the original size or the largest that still fits. That
ceiling is measured per texture against the padding after it, so it is whatever the space allows
rather than a fixed multiple. Sometimes that is double, sometimes 1.6x. Where nothing bigger fits
you get the original size only, and an image too large for the space is refused.

## 🖼️ Texture paks (`.ipak`)

Preview, extract and replace streamed textures. Use the gallery view to browse visually instead of
by name.

Replacement re-encodes into the entry's existing format, because the zone tells the GPU how to read
those bytes. It covers **every part** of an image, not just the one you clicked. A streamed texture
is split across up to three parts holding different mip tiers, and doing one leaves the rest
showing the original.

> The console binds each part from the **first** pak it finds it in and never looks again. If a
> base-game pak holds the same texture and loads earlier, your edit is never read: the save
> succeeds, the game shows the old image, and nothing reports a problem. The tool warns you before
> you spend the edit, and **Find asset** shows every pak holding each part.

## 🔊 Sound banks (`.sabs` / `.sabl`)

Browse entries, see the waveform, play them back, extract to WAV, replace, add and delete.

Untouched entries are written back byte for byte including their original checksums. Retail ships
entries whose stored checksum does not recompute, and "correcting" them is what broke banks in
earlier attempts.

Audio is DSP-ADPCM stored at 2/3 of the nominal rate, 48000 down to 32000, while the frame count
keeps the original figure. Multi-channel payloads are block-interleaved at 64 KB in streamed banks
and flat in loaded ones, with nothing in the file to tell you which. Only the extension.

## ⚙️ Engine RPLs (`.rpl` / `.rpx`)

Three patches: the fastfile signature check, the DLC load gate, and the internal render resolution
with eleven presets from 640x480 up, or type your own.

Everything is located by symbol name and instruction pattern rather than by address. The base, MP
and update builds put the same code in different places. The resolution site sits at `0x0297B418`
in one and `0x027FA17C` in another, so a patcher pinned to an address quietly does nothing on the
wrong file.

* * *

## 🔍 Finding things

**Find asset** (`Ctrl+F`) tells you which pak *or fastfile* holds what you are after, including
textures that live inline in a zone rather than in any pak. Type part of a name or paste a raw
hash. It reads an index rather than decoding anything, so a search across tens of thousands of keys
returns instantly.

**Bulk replace** points the same machinery at a folder:

1. Name your images the way the game names them, which is what a PC texture mod already does.
2. Drag the folder onto the window, or use **Bulk replace**.
3. Review the list. Nothing is written until you confirm.

It finds every pak and zone carrying each image and rewrites them all, grouped by destination file,
so twenty textures living in one pak open and save that pak once. Everything touched is backed up
first, and anything it cannot match is reported while the rest carries on.

Accepts `.png` `.dds` `.tga` `.bmp` `.jpg` `.gif` `.tif` `.webp`. DDS is covered in full (DXT1-5,
BC4/5/6H/7, DX10), because that is what PC mods ship.

* * *

## 📋 Before you edit

1. **Patch the signature check.** Nothing you save will load otherwise.
2. **Back up whatever you touch.** RPL patches back themselves up. Nothing else does.
3. **Cold start the game afterwards.** Texture parts and loaded banks stay cached, so a hot reload
   can serve the old bytes and make a good edit look broken.
4. **Watch for duplicate copies.** If the same file exists somewhere else the game loads from, an
   update folder or a DLC folder, editing one is a coin flip on which gets read.

* * *

## 🧪 Checking a build

Every copy can test itself:

```
WiiU_T6_Studio.exe --selftest
```

Read the counts rather than the colour. A gate reported `SKIP` is **not** a pass. It means the data
it needed was not on that machine, so the check never ran.

* * *

## 🧱 Known limits

**Nothing here is proven on console.** The checks prove files are well-formed and round-trip
correctly, not that the console accepts them. Treat a successful save as structurally sound rather
than known working.

**`common.ff` cannot be saved.** The structural walk stops at a VehicleDef record it cannot resume
past, affecting roughly 17 of the 155 stock zones. Your file is not damaged and same-size image
replacement still works. Only growing the zone is refused.

**Some pak entries carry no format or dimensions** anywhere findable. Those extract raw but cannot
be previewed or replaced. They are shown rather than hidden.

**The GSC compiler does not cover the whole language.** No `%anim` or `#animtree`, no
`waittillmatch`, and vector constants compile to a longer but valid form. Scripts using those are
refused rather than silently mis-compiled. A single compiled script caps at 65,535 bytes.

**`KeyValuePairs` and `SoundPatch` assets are read-only.** The field that looks like a length is
actually a count, so resizing them desynchronises the zone.

**Added assets and textures are inert.** Nothing references them until you wire them up zone-side,
which this does not do for you.

**PC files are not supported.** The pak reader recognises little-endian paks, but the image decoder
is console-specific and the fastfile side is Wii U only.

* * *

## 🪟 Why Windows only

The release is a single Windows executable. Three things tie to it: audio playback, drag and drop,
and shell integration. Everything else, including fastfile parsing, GX2 detiling, the BCn and
DSP-ADPCM codecs and the relinker, is plain Python.

Run it from source on Linux or macOS and it works minus playback and drag and drop. There is no
build for either, because Windows is the only platform I test on.

* * *

## 👥 Project contributors

**DarkexNrkm |-/** Provided core Wii U subsystem and tooling expertise, set the overall project
trajectory, and conducted key research.

**Priception** Established the testing and QA methodology using insights from past work on Halo
projects, implemented HD texture modifications, and led key research on future usage for end users.

**UndeadFrankie** Research and texture consultant. Shared insights from similar Xbox platform
conversion projects, helping analyse cross-platform texture handling to inform the Wii U shader
pipeline.

**ThePsych** Contributed early research work on ipak file conversions.

* * *

## 🧩 OpenAssetTools

Two files ship with this program from [OpenAssetTools](https://github.com/Laupetin/OpenAssetTools)
by Laupetin, at commit `85aa741`:

- `T6_Assets.h`, the layout of every T6 asset structure
- `ZoneCode/Game/T6/XAssets`, how those structures are written into a zone

They are read as reference data and are what makes walking a fastfile possible. No OpenAssetTools
code is compiled into this program.

Everything else, including all Wii U support, is my own work.

* * *

## 📄 Licence

GPL-3.0, full text in [`LICENSE`](LICENSE). This program includes GPL-3.0 files from
OpenAssetTools, whose licence ships at
[`licenses/OpenAssetTools-LICENSE.txt`](licenses/OpenAssetTools-LICENSE.txt).

Source for every release is in this repository at the matching tag.

Extracting the contents of game files does not grant you any rights to them.

* * *

<div align="center">

Built by **[tonytrawl](https://github.com/tonytrawl)**

Not affiliated with, endorsed by, or supported by Activision or Treyarch.<br>
For use with content you already own.

</div>
