<div align="center">

# WII U T6 Studio

**Fastfiles, texture paks, sound banks and the engine itself, in one window.**

[![Latest release](https://img.shields.io/github/v/release/tonytrawl/WiiU-T6-Studio?style=for-the-badge&logo=github&logoColor=17130a&label=RELEASE&labelColor=17130a&color=e8a33d)](https://github.com/tonytrawl/WiiU-T6-Studio/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/tonytrawl/WiiU-T6-Studio/total?style=for-the-badge&label=DOWNLOADS&labelColor=17130a&color=4d8fd6)](https://github.com/tonytrawl/WiiU-T6-Studio/releases)
[![Platform](https://img.shields.io/badge/PLATFORM-WINDOWS-7cb342?style=for-the-badge&logo=windows&logoColor=eef3f8&labelColor=17130a)](#-why-windows-only)
[![License](https://img.shields.io/badge/LICENSE-GPL%20v3.0-b07cd6?style=for-the-badge&labelColor=17130a)](LICENSE)

[![Buy me a coffee](https://img.shields.io/badge/SUPPORT%20MY%20WORK-BUY%20ME%20A%20COFFEE%20%E2%98%95-e8a33d?style=for-the-badge&labelColor=17130a&logo=buymeacoffee&logoColor=17130a)](https://buymeacoffee.com/tonytrawl)

</div>

* * *

Black Ops II on Wii U keeps its content in four different containers. This tool gives you the
ability to edit, author, and grow all those files.

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
* Bulk replace hundreds of textures in one pass from a folder (ideal for bringing plutonium texture mods to wii u)

It opens `.ff` fastfiles, `.ipak` texture paks, `.sabs` / `.sabl` sound banks and the engine's own
`.rpl` / `.rpx` modules. The window changes to suit whatever you opened. You can have several open
at once, such as a map's zone next to the pak its textures stream from.

* * *

## ⚠️ Read this first

A fastfile written by this tool, or by any tool, has **no valid RSA signature**. Nobody outside
Treyarch has the key. An unpatched console checks that signature and refuses the file, which shows
up as a crash or a hang the moment the zone is requested.

Open your engine RPL in the **RPL** tab and apply the signature patch before you edit anything. The
tool checks on startup and tells you if it looks unpatched.

* * *

## 🗂️ Fastfiles (`.ff`)

Browse every asset in a zone.

GSC and CSC scripts get a disassembly listing plus an editable assembly view, which works whether
or not you have the source. There is a decompiler that lands around 98% on retail bytecode. Lua and
HKS get the same treatment and compile from source. Text, cfg and csv files are edited directly.
Models render in a small software preview with geometry and shading only, enough to identify
something but not to judge how it looks in game.

Scripts and raw files can grow. The zone gets rebuilt and every pointer re-pointed. Afterwards the
tool re-walks the result and **refuses to write a file whose walk broke**, which is the main thing
standing between you and a zone that loads to a black screen.

Zones also carry their own inline textures, often hundreds of them, embedded in materials and FX
rather than sitting in the asset list. You can browse, export and replace those. Unlike pak
textures they can change resolution, because the record is rewritten along with the pixels.

## 🖼️ Texture paks (`.ipak`)

Preview, extract and replace streamed textures.

Replacement re-encodes into the entry's existing format, because the zone tells the GPU how to read
those bytes. It covers **every part** of an image rather than just the one you clicked. A streamed
texture is split across up to three parts holding different mip tiers, so doing one leaves the rest
showing the original.

The list has a gallery view if you would rather browse visually than read names.

The console binds each part from the *first* pak it finds it in and never looks again. If a
base-game pak holds the same texture and loads earlier, your edit is never read. The save succeeds,
the game shows the old image, and nothing reports a problem. The tool warns you before you spend
the edit, and **Find asset** shows you every pak holding each part.

## 🔊 Sound banks (`.sabs` / `.sabl`)

Browse entries, see the waveform, play them back, extract to WAV, replace, add and delete.

Untouched entries are written back byte for byte including their original checksums. Retail itself
ships entries whose stored checksum does not match a recomputation, and "correcting" them is what
broke banks in earlier attempts.

The Wii U format took some working out. Audio is DSP-ADPCM stored at 2/3 of the nominal rate (48000
down to 32000) while the frame count keeps the original figure. Multi-channel payloads are
block-interleaved at 64 KB in streamed banks and stored flat in loaded ones, with nothing in the
file to tell you which. Only the extension.

## ⚙️ Engine RPLs (`.rpl` / `.rpx`)

Three patches. The fastfile signature check, the DLC load gate, and the internal render resolution
with eleven presets from 640x480 up, or type your own.

Everything is located by symbol name and instruction pattern rather than by address, because the
base, MP and update builds put the same code in different places. The resolution site sits at
`0x0297B418` in one and `0x027FA17C` in another. A patcher pinned to an address quietly does
nothing on the wrong file.

Your original is copied to `<name>.stock` before the first edit, and that backup is never
overwritten, so it is always the untouched file no matter how many patches you apply later.

> Patch **both** RPLs. `t6_cafef_rpl` loads first and carries its own copy of the same engine code.
> Boot-time render setup runs entirely in there.

* * *

## 🔍 Finding things

Texture entries store a numeric hash and nothing else. There are no names anywhere in the file.
Readable names come from a dictionary harvested out of your zones, which ships prebuilt.

**Find asset** searches that dictionary and tells you which pak *or fastfile* holds what you are
after, including textures that live inline in a zone rather than in any pak. Type part of a name or
paste a raw hash. It reads an index rather than decoding anything, so a search across tens of
thousands of keys comes back instantly.

**Bulk replace** is the same machinery pointed at a folder. Drop in a pile of images named the way
the game names them. It finds every pak and zone carrying each one and rewrites them all. Work is
grouped by destination file, so twenty textures living in one pak open and save that pak once
rather than twenty times. Anything it cannot match is reported and the rest carries on, because a
batch that dies halfway leaves you unable to tell what landed.

DDS is fully supported, which matters because it is what PC texture mods ship. PNG, TGA, BMP, JPG,
GIF, TIFF and WebP work too.

* * *

## 🪟 Why Windows only

The release is a single Windows executable, and Windows is the only platform being supported for
now. Some of that is dependencies and some of it is honesty about testing.

Three things genuinely tie to Windows:

* **Audio playback** goes through a Windows audio API. Waveforms, extract, replace and save work
  anywhere. Hearing a sound does not.
* **Drag and drop** is implemented directly against `DragAcceptFiles` and `WM_DROPFILES` through
  ctypes, rather than pulling in a native Tk extension for one convenience.
* **Shell integration**, meaning Show in folder and finding your real Desktop when OneDrive has
  redirected it, reads the Windows shell and registry.

The rest is pragmatic. Nearly everyone modding this game is running Cemu on Windows, and I would
rather ship one platform I actually test than three I do not. Every fallback path degrades quietly
instead of crashing, but "should work" is not the same as "does work" and I am not going to claim
the second.

The source is here. The interesting parts, meaning fastfile parsing, GX2 detiling, BCn and
DSP-ADPCM codecs, and the relinker, are plain Python with no Windows in them. If you want to run it
on Linux or macOS you can, minus playback and drag and drop. You just will not get a build from me.

* * *

## 📋 Before you edit

1. **Patch the signature check.** Nothing you save will load otherwise.
2. **Back up whatever you touch.** RPL patches back themselves up. Nothing else does.
3. **Cold start the game afterwards.** Resolved texture parts and loaded banks stay cached, so a
   hot reload can serve the old bytes and make a good edit look broken.
4. **Watch for duplicate copies.** If the same file exists somewhere else the game loads from, such
   as an update folder or a DLC folder, editing one is a coin flip on which gets read.

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

Worth knowing before you hit them.

Every check the program runs proves files are well-formed
and round-trip correctly. None of it proves the console accepts the result. Treat a successful save
as structurally sound rather than known working.

**`common.ff` cannot be saved.** The structural walk stops at a VehicleDef record it cannot resume
past, which affects roughly 17 of the 155 stock zones. Your file is not damaged and same-size image
replacement still works. Only growing the zone is refused.

**Some pak entries carry no format or dimensions** anywhere findable. Those can be extracted raw
but not previewed or replaced. They are shown rather than hidden.

**The GSC compiler does not cover the whole language.** No `%anim` or `#animtree`, no
`waittillmatch`, and vector constants compile to a longer but valid form. Scripts using those are
refused rather than silently mis-compiled. A single compiled script caps at 65,535 bytes.

**`KeyValuePairs` and `SoundPatch` assets are read-only.** The field that looks like a length is
actually a count, so resizing them desynchronises the zone.

**Added assets and textures are inert.** Nothing references them until you wire them up zone-side,
which this does not do for you.

**PC files are not supported.** The pak reader recognises little-endian paks, but the image decoder
is console-specific and the fastfile side is Wii U only (OAT hook to support PC coming soon).

* * *

<div align="center">

Built by **[tonytrawl](https://github.com/tonytrawl)**

Not affiliated with, endorsed by, or supported by Activision or Treyarch.<br>
For use with content you already own.

</div>
