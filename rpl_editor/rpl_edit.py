#!/usr/bin/env python3
"""rpl_edit.py -- read/modify/write Wii U RPL (Cafe ELF) with FULL metadata upkeep.

Supersedes the ad-hoc repack in mod_loader_patch/build_patch*.py, which updated
only sh_offset/sh_size and .rplcrcs. This module additionally:
  * preserves per-section payloads VERBATIM unless explicitly edited (so an
    unedited round-trip is byte-identical -- the gate),
  * rebuilds .rplcrcs for EVERY section, not just the two that were touched,
  * re-derives the .rplfileinfo allocation fields (textSize/dataSize/loadSize/
    tempSize) instead of inheriting stale ones,
  * refuses edits that would overrun a FILEINFO allocation bound or collide with
    a pinned VA (.syscall sits inside the text allocation, above .text).

Relocation rewriting (arbitrary mid-section insert) is NOT here yet -- see
shift_vas() at the bottom for the stub and what it must maintain. Append-only
growth needs no relocation work, which is why it is the first thing to prove.

Layout rule (derived from the stock t6mp update-build RPL and verified by a
byte-identical round-trip): section header table at e_shoff, then every section
with a payload written in ascending original-file-offset order, each 0x40-aligned.
"""
import struct
import zlib
import binascii

SHF_RPL_ZLIB = 0x08000000
SHT_RPL_EXPORTS = 0x80000001
SHT_RPL_IMPORTS = 0x80000002
SHT_RPL_CRCS = 0x80000003
SHT_RPL_FILEINFO = 0x80000004
SHT_NOBITS = 0x08

FILE_ALIGN = 0x40

# ---------------------------------------------------------------------------
# ⚠⚠ THE RPL FILE HOLDS *PRE-RELOCATION* ADDRESSES.
# Runtime address = file VA + delta. Project rule, independently re-derived here
# by builds B9 (absolute jump to a raw file VA -> crash at the loading screen)
# and B10 (same registers, PC-relative branch -> boots).
#
#   code   (.text, .syscall, added exec sections)  runtime = file VA + 0x2000
#   data   (.rodata, .data)                        runtime = file VA + 0x5000
#
# PC-relative b/bl are IMMUNE -- a uniform delta preserves relative distances,
# which is why B4/B8 booted and prove nothing about rebasing. Anything you bake
# into an instruction as an absolute (lis/ori, lis/addi) MUST have the delta
# applied, or you jump/read 0x2000-0x5000 short of the target.
# ---------------------------------------------------------------------------
RT_CODE_DELTA = 0x2000
RT_DATA_DELTA = 0x5000


def rt_code(va):
    """File VA -> runtime VA for anything in the text region."""
    return va + RT_CODE_DELTA


def rt_data(va):
    """File VA -> runtime VA for anything in the rodata/data region."""
    return va + RT_DATA_DELTA

# .rplfileinfo word offsets we re-derive
FI_TEXTSIZE, FI_TEXTALIGN = 0x04, 0x08
FI_DATASIZE, FI_DATAALIGN = 0x0C, 0x10
FI_LOADSIZE, FI_LOADALIGN = 0x14, 0x18
FI_TEMPSIZE = 0x1C


def _align(x, a):
    a = max(int(a), 1)
    return (x + a - 1) // a * a


class Section:
    __slots__ = ("idx", "name", "type", "flags", "addr", "offset", "size",
                 "link", "info", "addralign", "entsize", "raw", "_data", "_dirty",
                 "sname")

    def __init__(self, idx, hdr, raw):
        (self.name, self.type, self.flags, self.addr, self.offset, self.size,
         self.link, self.info, self.addralign, self.entsize) = hdr
        self.idx = idx
        self.raw = raw            # payload exactly as it sits in the file
        self._data = None         # decompressed view, materialised on demand
        self._dirty = False

    @property
    def compressed(self):
        return bool(self.flags & SHF_RPL_ZLIB) and self.size > 0

    @property
    def data(self):
        """Decompressed section contents (bytearray). Mutating it marks dirty."""
        if self._data is None:
            if self.compressed:
                declen = struct.unpack(">I", self.raw[:4])[0]
                d = zlib.decompress(bytes(self.raw[4:]))
                if len(d) != declen:
                    raise ValueError(f"sec {self.idx}: prefix {declen:#x} != inflated {len(d):#x}")
                self._data = bytearray(d)
            else:
                self._data = bytearray(self.raw)
        return self._data

    def mark_dirty(self):
        self.data  # force materialise before we lose self.raw as the source
        self._dirty = True

    def payload(self):
        """Bytes to write to the file for this section."""
        if not self._dirty:
            return bytes(self.raw)
        d = bytes(self.data)
        if self.compressed:
            return struct.pack(">I", len(d)) + zlib.compress(d, 9)
        return d


class Rpl:
    def __init__(self, blob):
        self.blob = bytearray(blob)
        d = self.blob
        if d[:4] != b"\x7fELF":
            raise ValueError("not an ELF/RPL")
        self.e_shoff = struct.unpack(">I", d[0x20:0x24])[0]
        self.e_shentsize = struct.unpack(">H", d[0x2E:0x30])[0]
        self.e_shnum = struct.unpack(">H", d[0x30:0x32])[0]
        self.e_shstrndx = struct.unpack(">H", d[0x32:0x34])[0]
        if self.e_shentsize != 40:
            raise ValueError(f"unexpected e_shentsize {self.e_shentsize}")
        self.sections = []
        for i in range(self.e_shnum):
            o = self.e_shoff + i * 40
            hdr = struct.unpack(">IIIIIIIIII", d[o:o + 40])
            off, size = hdr[4], hdr[5]
            raw = bytes(d[off:off + size]) if (size and hdr[1] != SHT_NOBITS) else b""
            self.sections.append(Section(i, hdr, raw))
        # resolve names
        shstr = self.sections[self.e_shstrndx].data
        for s in self.sections:
            e = shstr.find(b"\0", s.name)
            s.sname = shstr[s.name:e].decode("latin1")

    # ---- lookup helpers -------------------------------------------------
    def by_name(self, name):
        for s in self.sections:
            if s.sname == name:
                return s
        raise KeyError(name)

    def by_type(self, t):
        return [s for s in self.sections if s.type == t]

    # ---- metadata rebuild ----------------------------------------------
    def _fileinfo(self):
        fi = self.by_type(SHT_RPL_FILEINFO)
        return fi[0] if fi else None

    def fi_get(self, off):
        return struct.unpack(">I", self._fileinfo().data[off:off + 4])[0]

    def fi_set(self, off, val):
        fi = self._fileinfo()
        fi.mark_dirty()
        struct.pack_into(">I", fi.data, off, val)

    def _rebuild_crcs(self):
        """.rplcrcs holds one BE32 CRC32 per section, over the DECOMPRESSED bytes.
        The CRCS and FILEINFO sections themselves carry 0."""
        crcs = self.by_type(SHT_RPL_CRCS)
        if not crcs:
            return
        c = crcs[0]
        c.mark_dirty()
        buf = bytearray(4 * self.e_shnum)
        for s in self.sections:
            # measured against stock: every section is CRC'd over its
            # DECOMPRESSED bytes except .rplcrcs itself, which carries 0.
            if s.type == SHT_RPL_CRCS or not s.size:
                v = 0
            else:
                v = binascii.crc32(bytes(s.data)) & 0xFFFFFFFF
            struct.pack_into(">I", buf, s.idx * 4, v)
        c._data = buf   # resizes automatically when a section has been added

    def _rebuild_fileinfo(self):
        """Re-derive the loader allocation sizes from current section contents.

        Semantics (matched against the stock t6mp RPL by check_fileinfo()):
          textSize = align(sum of aligned sizes of sections with VA in text range)
          dataSize = same for the data/rodata range, incl. .bss
          loadSize = same for the 'load' range (VA 0 or >= 0xC0000000)
          tempSize = scratch for the loader's decompression / relocation pass
        We only ever RAISE these; never shrink below stock, so we cannot make the
        loader under-allocate relative to a known-good build.
        """
        fi = self._fileinfo()
        if fi is None:
            return
        text = data = load = 0
        for s in self.sections:
            if not s.size:
                continue
            n = s.size if s.type == SHT_NOBITS else len(s.data)
            a = s.addr
            if s.type in (SHT_RPL_CRCS, SHT_RPL_FILEINFO) or s.sname in (
                    ".symtab", ".strtab", ".shstrtab"):
                # consumed from the file / debug only -- not part of any
                # loader allocation (verified: including them overruns loadSize)
                continue
            if a == 0 or a >= 0xC0000000:
                load = _align(load, s.addralign) + n
            elif a < 0x10000000:
                text = _align(text, s.addralign) + n
            else:
                data = _align(data, s.addralign) + n
        return {"text": text, "data": data, "load": load}

    # ---- write ----------------------------------------------------------
    def build(self, rebuild_meta=True):
        if rebuild_meta:
            self._rebuild_crcs()

        hdr_end = self.e_shoff + self.e_shnum * 40
        # sections that occupy file space, in ascending ORIGINAL offset order
        placed = sorted((s for s in self.sections
                         if s.size and s.type != SHT_NOBITS),
                        key=lambda s: s.offset)
        out = bytearray(self.blob[:hdr_end])
        newhdr = {}
        for k, s in enumerate(placed):
            p = s.payload()
            # Measured layout rule (byte-identical round-trip on both RPLs):
            # the FIRST payload section packs immediately after the section
            # header table at its own sh_addralign (hdr_end 0x5e0 is only
            # 4-aligned, not 0x40); every later section is 0x40-aligned.
            target = _align(len(out), s.addralign if k == 0 else FILE_ALIGN)
            out.extend(b"\0" * (target - len(out)))
            newhdr[s.idx] = (len(out), len(p))
            out += p
        # Write the FULL header for every section, not just offset/size -- a
        # section added by add_section() has no bytes in the original blob to
        # inherit. G1 byte-identity still holds because the values round-trip.
        for s in self.sections:
            o = self.e_shoff + s.idx * 40
            off, size = newhdr.get(s.idx, (s.offset, s.size))
            struct.pack_into(">IIIIIIIIII", out, o,
                             s.name, s.type, s.flags, s.addr, off, size,
                             s.link, s.info, s.addralign, s.entsize)
        return bytes(out)

    # ---- append-growth API ----------------------------------------------
    def append(self, secname, blob, align=4):
        """Append bytes to the END of a section's decompressed data.
        Returns (file_offset_in_section, VA). No relocations move, so this is the
        only growth mode that is safe without a relocation rewriter."""
        s = self.by_name(secname)
        s.mark_dirty()
        while len(s.data) % align:
            s.data.append(0)
        off = len(s.data)
        s.data.extend(blob)
        return off, s.addr + off

    def add_section(self, blob, addr, flags=0x6, stype=1, addralign=0x20):
        """Insert a NEW section JUST BEFORE the .rplcrcs/.rplfileinfo trailer.

        ⚠ MEASURED THE HARD WAY (build B6, 2026-07-26): appending at the END of
        the section header table makes Cemu's RPL loader fail with
            RPLLoader: Invalid FILEINFO magic
        and crash. **SHT_RPL_FILEINFO must remain the LAST section header.** So
        we insert ahead of the trailer instead and renumber it upward.

        This is the scalable alternative to append() and it is far cheaper than a
        relocation rewriter. Only the trailer indices shift, and nothing
        references them: on both RPLs the only real section-index references are
        `.rela.*` sh_link/sh_info and e_shstrndx, all <= 33 (`.symtab`'s sh_info
        is a first-nonlocal-symbol COUNT, not an index -- do not "fix" it).
        Nothing is relocated, so no `.rela` entry is needed.

        Three knock-on effects, all handled:
          * e_shnum +1  -> the header table grows 40 bytes, so the first payload
            section shifts; build() re-flows every offset anyway.
          * .rplcrcs is 4*shnum bytes -> _rebuild_crcs() resizes it.
          * sh_name = 0 (empty). The SDK itself ships .rplcrcs and .rplfileinfo
            with empty names, so this needs no .shstrtab growth -- which matters,
            because .shstrtab has only 6 bytes of VA headroom before
            .dimport_t6_cafef_launcher.rpx.

        Defaults mirror .syscall (PROGBITS, ALLOC|EXECINSTR, uncompressed), which
        is the SDK's own precedent for a small executable section sitting inside
        the text allocation above .text.
        """
        trailer = min((s.idx for s in self.sections
                       if s.type in (SHT_RPL_CRCS, SHT_RPL_FILEINFO)),
                      default=len(self.sections))
        # nothing may reference an index at or beyond the insertion point
        if self.e_shstrndx >= trailer:
            raise ValueError(f"e_shstrndx {self.e_shstrndx} >= insert point {trailer}")
        for x in self.sections:
            if x.link >= trailer and x.link:
                raise ValueError(f"sec {x.idx} sh_link {x.link} >= insert point")
            if x.type == 4 and x.info >= trailer:   # SHT_RELA: sh_info IS an index
                raise ValueError(f"sec {x.idx} sh_info {x.info} >= insert point")

        hdr = (0, stype, flags, addr, 0, len(blob), 0, 0, addralign, 0)
        s = Section(trailer, hdr, bytes(blob))
        s.sname = ""
        # sort key for build(): payload lands last in FILE order (independent of
        # header index -- sh_offset is explicit)
        s.offset = 0xFFFFFFF0
        s._data = bytearray(blob)
        s._dirty = True
        self.sections.insert(trailer, s)
        for x in self.sections[trailer + 1:]:
            x.idx += 1
        self.e_shnum += 1
        struct.pack_into(">H", self.blob, 0x30, self.e_shnum)
        # The header table just grew by 40 bytes, pushing the first payload
        # section forward -- build() re-flows every offset from hdr_end, so no
        # collision is possible. The only real requirement is that the source
        # blob still covers the (now larger) prefix we copy.
        if len(self.blob) < self.e_shoff + self.e_shnum * 40:
            raise ValueError("blob shorter than the grown section header table")
        return s

    def grow_bss(self, nbytes, align=0x20):
        """Reserve `nbytes` of new zero-init scratch by extending .bss.

        .bss is SHT_NOBITS, so this costs ZERO file bytes -- only the FILEINFO
        dataSize allocation. It is the answer to `.data` having a useless 0x1C
        bytes of headroom before .module_id.

        Measured: .bss is the LAST section in the data region and
        dataSize == (.bss end - 0x10000000) exactly, on both RPLs. So the only
        metadata to maintain is sh_size and dataSize.

        Returns the FILE VA of the reserved block -- remember to pass it through
        rt_data() before baking it into an instruction.
        """
        b = self.by_name(".bss")
        start = _align(b.addr + b.size, align)
        b.size = (start + nbytes) - b.addr
        cur = self._rebuild_fileinfo()
        if cur["data"] > self.fi_get(FI_DATASIZE):
            self.fi_set(FI_DATASIZE, _align(cur["data"], 0x100))
        return start

    def import_stub_range(self):
        """(lo, hi, count) of the loader's IMPORT TRAMPOLINE area, or None.

        ⚠⚠ THE GAP ABOVE .text IS NOT FREE SPACE. Measured on t6mp: all 6,288
        R_PPC_REL24 relocations against import symbols target 442 distinct stubs
        spanning 0x02B562B8..0x02B5B748 -- i.e. essentially the whole
        .text-end(0x02B5626C) -> .syscall(0x02B5B940) gap I first mistook for
        22,228 bytes of cave. Only 0x4C bytes are actually free before the first
        stub. Builds B2/B3/B4 fit inside that 0x4C purely by luck.

        Use add_section() above .syscall instead -- that is what B8-B12 did.

        Bonus: these stubs are how you CALL AN IMPORT from new code. They are
        real code at runtime and PC-relative, so `ppc.branch(from, stub, link=1)`
        reaches them with no relocation and no delta (both sides shift equally).
        """
        try:
            text = self.by_name(".text")
            sym = bytes(self.by_name(".symtab").data)
            rela = bytes(self.by_name(".rela.text").data)
        except KeyError:
            return None
        impsecs = {s.idx for s in self.sections if s.type == SHT_RPL_IMPORTS}
        imps = set()
        for i in range(len(sym) // 16):
            shndx = struct.unpack(">H", sym[i * 16 + 14:i * 16 + 16])[0]
            if shndx in impsecs:
                imps.add(i)
        tg = []
        for o in range(0, len(rela), 12):
            off, info, _ = struct.unpack(">III", rela[o:o + 12])
            if (info & 0xFF) == 10 and (info >> 8) in imps:
                w = struct.unpack(">I", bytes(text.data[off - text.addr:off - text.addr + 4]))[0]
                # An edit may have replaced a relocated call with something that
                # is not a branch (e.g. a nop). Decoding its low bits as a
                # displacement yields a garbage "stub" far outside the real
                # range and poisons the whole span. Only trust real b/bl.
                if (w >> 26) != 18:
                    continue
                d = w & 0x03FFFFFC
                if d & 0x02000000:
                    d -= 0x04000000
                tg.append(off + d)
        return (min(tg), max(tg), len(set(tg))) if tg else None

    def import_stub(self, name):
        """File VA of an import's trampoline stub, for a PC-relative call."""
        sym = bytes(self.by_name(".symtab").data)
        st = bytes(self.by_name(".strtab").data)
        text = self.by_name(".text")
        idx = None
        for i in range(len(sym) // 16):
            nm = struct.unpack(">I", sym[i * 16:i * 16 + 4])[0]
            e = st.find(b"\0", nm)
            if st[nm:e] == name.encode():
                idx = i
                break
        if idx is None:
            raise KeyError(name)
        rela = bytes(self.by_name(".rela.text").data)
        for o in range(0, len(rela), 12):
            off, info, _ = struct.unpack(">III", rela[o:o + 12])
            if (info >> 8) == idx and (info & 0xFF) == 10:
                w = struct.unpack(">I", bytes(text.data[off - text.addr:off - text.addr + 4]))[0]
                d = w & 0x03FFFFFC
                if d & 0x02000000:
                    d -= 0x04000000
                return off + d
        raise KeyError(f"{name}: no REL24 call site")

    def check_headroom(self):
        """Assert no section's decompressed content overruns its FILEINFO
        allocation, and that no grown section swallows a pinned VA above it."""
        problems = []
        cur = self._rebuild_fileinfo()
        fi_text = self.fi_get(FI_TEXTSIZE)
        fi_data = self.fi_get(FI_DATASIZE)
        if cur["text"] > fi_text:
            problems.append(f"text region {cur['text']:#x} > FILEINFO textSize {fi_text:#x}")
        if cur["data"] > fi_data:
            problems.append(f"data region {cur['data']:#x} > FILEINFO dataSize {fi_data:#x}")
        # pinned-VA collision: any section whose VA lies above another's end
        spans = [(s.addr, s.addr + len(s.data), s.sname)
                 for s in self.sections
                 if s.size and s.addr and s.type != SHT_NOBITS]
        spans.sort()
        for (a0, a1, n0), (b0, b1, n1) in zip(spans, spans[1:]):
            if a1 > b0:
                problems.append(f"VA overlap: {n0} ends {a1:#x} > {n1} starts {b0:#x}")
        # nothing may extend into the loader's import trampoline area
        rng = self.import_stub_range()
        if rng:
            lo, hi, n = rng
            for a0, a1, nm in spans:
                if a0 < hi and a1 > lo:
                    problems.append(
                        f"{nm} ({a0:#x}..{a1:#x}) collides with the IMPORT TRAMPOLINE "
                        f"area {lo:#x}..{hi:#x} ({n} stubs) -- use add_section() above .syscall")
        return problems


def shift_vas(rpl, at_va, delta):
    """NOT IMPLEMENTED -- arbitrary mid-section insert/shrink.

    To make this correct it must, atomically:
      1. .rela.text/.rela.data/.rela.rodata: r_offset += delta where >= at_va
         (and any R_PPC_* addend that encodes a target VA >= at_va)
      2. .symtab: st_value += delta where >= at_va
      3. .text: re-encode every b/bl/bc whose source and target straddle at_va
      4. .fexports/.dexports: exported VAs >= at_va
      5. FILEINFO textSize/dataSize + sdaBase/sda2Base invariants
    Gate: run with delta=0 and require byte-identical output vs stock before
    trusting any delta != 0.
    """
    raise NotImplementedError("relocation rewriter not built; use append() instead")


def load(path):
    with open(path, "rb") as f:
        return Rpl(f.read())
