<div align="center">

[![WII U T6 Studio](icon.png)](https://github.com/tonytrawl/WiiU-T6-Studio)

# WII U T6 Studio

**Fastfiles, texture paks, sound banks and the engine itself — in one window.**

Edit Call of Duty: Black Ops II on Wii U without a pile of single-purpose tools.

[![Latest release](https://img.shields.io/github/v/release/tonytrawl/WiiU-T6-Studio?style=for-the-badge&logo=github&color=e8a33d)](https://github.com/tonytrawl/WiiU-T6-Studio/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/tonytrawl/WiiU-T6-Studio/total?style=for-the-badge&color=4d8fd6)](https://github.com/tonytrawl/WiiU-T6-Studio/releases)
[![Platform](https://img.shields.io/badge/platform-Windows-7cb342?style=for-the-badge&logo=windows)](#why-windows-only)
[![License](https://img.shields.io/badge/license-GPL%20v3.0-b07cd6?style=for-the-badge)](LICENSE)

</div>

* * *

Black Ops II on Wii U keeps its content in four different containers, and until now each one
needed its own tool — assuming a tool existed at all. This is those tools merged into a single
program, plus the engine patcher you need to make any of it actually load.

It opens `.ff` fastfiles, `.ipak` texture paks, `.sabs` / `.sabl` sound banks and the engine's
own `.rpl` / `.rpx` modules. The window reshapes itself around whatever you opened, and you can
have several open at once — a map's zone next to the pak its textures stream from, for instance.

* * *

## ⚠️ Read this first

A fastfile written by this tool — or by any tool — has **no valid RSA signature**. Nobody outside
Treyarch has the key. An unpatched console checks that signature and refuses the file outright,
which shows up as a crash or a hang the moment the zone is requested.

This is not theoretical. A user with a EU copy had every single edit fail, including saving a zone
with *no changes made at all*, while the same build worked fine elsewhere. The zones were compared
byte for byte: decompressed payloads identical, container framing identical, and the only
difference was that 256-byte signature block.

So before you edit anything, open your engine RPL in the **RPL** tab and apply the signature
patch. The tool checks for this on startup and will tell you if it looks unpatched.

* * *

## 🗂️ Fastfiles (`.ff`)

Browse every asset in a zone. GSC and CSC scripts get a disassembly listing plus an editable
assembly view that works whether or not you have the source, and there's a decompiler that lands
around 98% on retail bytecode. Lua and HKS get the same treatment. Text, cfg and csv files are
edited directly. Models render in a small software preview — geometry and shading only, enough to
identify something, not to judge how it looks in game.

Scripts and raw files can grow. The zone gets rebuilt and every pointer re-pointed, and afterwards
the tool re-walks the result and **refuses to write a file whose walk broke**. That check is the
main thing standing between you and a zone that loads to a black screen.

Zones also carry their own inline textures — often hundreds of them, embedded in materials and FX
rather than sitting in the asset list. Those are browsable, exportable and replaceable, and unlike
pak textures they can change resolution, because the record is rewritten along with the pixels.

## 🖼️ Texture paks (`.ipak`)

Preview, extract and replace streamed textures. Replacement re-encodes into the entry's existing
format because the zone tells the GPU how to read those bytes, and it covers **every part** of an
image rather than just the one you clicked — a streamed texture is split across up to three parts
holding different mip tiers, so doing one leaves the rest showing the original.

The list has a gallery view if you'd rather browse visually than read names.

**The one that catches everyone:** the console binds each part from the *first* pak it finds it
in and never looks again. If a base-game pak holds the same texture and loads earlier, your edit
is never read — the save succeeds, the game shows the old image, and nothing reports a problem.
The tool warns you before you spend the edit, and **Find asset** will show you every pak holding
each part.

## 🔊 Sound banks (`.sabs` / `.sabl`)

Browse entries, see the waveform, play them back, extract to WAV, replace, add and delete.
Untouched entries are written back byte for byte including their original checksums — retail
itself ships entries whose stored checksum doesn't match a recomputation, and "correcting" them
is what broke banks in earlier attempts.

The Wii U format took some working out. Audio is DSP-ADPCM stored at 2/3 of the nominal rate
(48000 → 32000) while the frame count keeps the original figure, and multi-channel payloads are
block-interleaved at 64 KB in streamed banks but stored flat in loaded ones — with nothing in the
file to tell you which, only the extension.

## ⚙️ Engine RPLs (`.rpl` / `.rpx`)

Three patches: the fastfile signature check, the DLC load gate, and the internal render
resolution (eleven presets from 640×480 up, or type your own).

Everything is located by symbol name and instruction pattern rather than by address, because the
base, MP and update builds put the same code in different places — the resolution site sits at
`0x0297B418` in one and `0x027FA17C` in another. A patcher pinned to an address quietly does
nothing on the wrong file.

Your original is copied to `<name>.stock` before the first edit and that backup is never
overwritten, so it's always the untouched file no matter how many patches you apply later.

> Patch **both** RPLs. `t6_cafef_rpl` loads first and carries its own copy of the same engine
> code; boot-time render setup runs entirely in there.

* * *

## 🔍 Finding things

Texture entries store a numeric hash and nothing else — no names anywhere in the file. Readable
names come from a dictionary harvested out of your zones, which ships prebuilt.

**Find asset** searches that dictionary and tells you which pak *or fastfile* holds what you're
after, including textures that live inline in a zone rather than in any pak. Type part of a name
or paste a raw hash. It reads an index rather than decoding anything, so a search across tens of
thousands of keys comes back instantly.

**Bulk replace** is the same machinery pointed at a folder. Drop in a pile of images named the way
the game names them and it finds every pak and zone carrying each one and rewrites them all. It
groups work by destination file, so twenty textures living in one pak open and save that pak once
rather than twenty times. Anything it can't match is reported and the rest carries on — a batch
that dies halfway leaves you unable to tell what landed.

DDS is fully supported, which matters because it's what PC texture mods ship. PNG, TGA, BMP, JPG,
GIF, TIFF and WebP work too.

* * *

## 🪟 Why Windows only

The release is a single Windows executable, and that's the only platform being supported for now.
Some of that is dependencies and some of it is honesty about testing.

Three things genuinely tie to Windows:

- **Audio playback** goes through a Windows audio API. Waveforms, extract, replace and save all
  work anywhere; hearing a sound doesn't.
- **Drag and drop** is implemented directly against `DragAcceptFiles` / `WM_DROPFILES` through
  ctypes, rather than pulling in a native Tk extension just for that one convenience.
- **Shell integration** — Show in folder, and finding your real Desktop when OneDrive has
  redirected it — reads the Windows shell and registry.

The rest is more pragmatic. Nearly everyone modding this game is running Cemu on Windows, and I'd
rather ship one platform I actually test than three I don't. Every fallback path degrades quietly
rather than crashing, but "should work" isn't the same as "does work", and I'm not going to claim
the second.

The source is here and the interesting parts — fastfile parsing, GX2 detiling, BCn and DSP-ADPCM
codecs, the relinker — are plain Python with no Windows in them. If you want to run it on Linux or
macOS you can, minus playback and drag-and-drop. You just won't get a build from me.

* * *

## 📋 Before you edit

1. **Patch the signature check.** Nothing you save will load otherwise.
2. **Back up whatever you touch.** RPL patches back themselves up; nothing else does.
3. **Cold start the game afterwards.** Resolved texture parts and loaded banks stay cached, so a
   hot reload can serve the old bytes and make a perfectly good edit look broken.
4. **Watch for duplicate copies.** If the same file exists somewhere else the game loads from —
   an update folder, a DLC folder — editing one is a coin flip on which gets read.

* * *

## 🧪 Checking a build

Every copy can test itself:

```
WiiU_T6_Studio.exe --selftest
```

Read the counts rather than the colour. A gate reported `SKIP` is **not** a pass — it means the
data it needed wasn't on that machine, so the check never ran.

* * *

## 🧱 Known limits

Worth knowing before you hit them.

**Nothing here is proven on console.** Every check the program runs proves files are well-formed
and round-trip correctly. None of it proves the console accepts the result. Treat a successful
save as "structurally sound", not "known working".

**`ui_mp` and `ui_zm` used to be unsavable** and now aren't, but `common.ff` still is — the
structural walk stops at a VehicleDef record it can't resume past. That affects roughly 17 of the
155 stock zones. Your file isn't damaged and same-size image replacement still works; only growing
the zone is refused.

**Some pak entries carry no format or dimensions** anywhere findable. Those can be extracted raw
but not previewed or replaced. They're shown rather than hidden.

**The GSC compiler doesn't cover the whole language** — no `%anim` / `#animtree`, no
`waittillmatch`, and vector constants compile to a longer but valid form. Scripts using those are
refused rather than silently mis-compiled. A single compiled script caps at 65,535 bytes.

**`KeyValuePairs` and `SoundPatch` assets are read-only.** The field that looks like a length is
actually a count, so resizing them desynchronises the zone.

**Added assets and textures are inert.** Nothing references them until you wire them up zone-side,
which this doesn't do for you.

**PC files aren't supported.** The pak reader recognises little-endian paks, but the image decoder
is console-specific and the fastfile side is Wii U only.

* * *

<div align="center">

Built by **[tonytrawl](https://github.com/tonytrawl)**

Not affiliated with, endorsed by, or supported by Activision or Treyarch.<br>
For use with content you already own.

</div>
