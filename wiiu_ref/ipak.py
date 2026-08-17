#!/usr/bin/env python3
"""
T6 IPAK container: read (enumerate, extract by hash) and write (author a
console .ipak from {hash -> blob}). Pure Python, no dependencies.

Container format (derived from genuine Wii U content/mp_raid.ipak and friends,
xbox_nuketown_extracted/zm_nuked.ipak, xbox_nuketown_extracted/
dlczm0_load_zm.ipak, "ps3 ff/zm_nuked.ipak", and cross-checked against the OAT
reference reader/writer headers; same struct layout on every platform,
endianness flips):

  header (16 B): magic 'IPAK' (big-endian file: Wii U / 360 / PS3) or 'KAPI'
                 (little-endian, PC), version 0x50000, fileSize, sectionCount
  sections (16 B each): { type, offset, size, itemCount }
                 type 1 = index, type 2 = data. 360/PS3 retail paks carry only
                 those two; Wii U retail adds type 4 (the {nameHash, dataHash}
                 key list of every part the map uses, including parts stored in
                 other paks) and type 3 (per-part "iwi: images/<name>.iwi"
                 source text records) - linker metadata, carried opaquely here.
                 OAT-authored paks instead add a 'META' branding section.
  index entry (16 B): { nameHash u32, dataHash u32, offset u32, size u32 }
                 The two hash words are the big-endian serialization of
                 u64 combinedKey = nameHash << 32 | dataHash; entries sorted by
                 combinedKey ascending. offset is relative to the data section
                 start; size is the FILE span of the entry (headers + payload).
  hashes:  nameHash = R_HashString(imageName): h = 33*h ^ (byte | 0x20), seed 0.
             This is the u32 the console GfxImage stores at +324 and PC at
             body+76, so .ff image refs carry across platforms unchanged.
           dataHash = (partIndex << 29) | (crc32(part payload) & 0x1FFFFFFF).
             The GfxImage streamedParts[i] hash field at part+4 holds exactly
             this value; it is the second index key word. Verified crc-exact on
             60/60 genuine PS3 entries (LZO path).
  data section: per entry a run of blocks. Block = 128 B header
             { u32 countAndOffset: count in the top byte, entry-relative
               decompressed offset in the low 24 bits; u32 commands[31] } then
             the command payloads back to back. Command u32 = compressed byte
             << 24 | payload size (24 bits). compressed: 0 = raw copy,
             1 = LZO1X, 2 = XMemCompress/LZX (Xbox 360), 0xCF = skip (padding).
             Wii U retail payloads are UNCOMPRESSED (cmd 0, 0x7FF0 bytes per
             command); PS3 uses LZO (cmd 1), 360 XMem (cmd 2).
  layout:    block headers are 128-aligned; sections and the file end are
             0x40000-aligned. Read windows are 0x40000 wide starting at the
             previous 0x8000 boundary of the entry's first block; a block or
             command never spans a window boundary: the writer closes the
             block, emits a one-command skip block (fill 0xCD, offset word 0)
             up to the boundary, and continues with a fresh header exactly at
             the boundary (8 x 0x7FF0 commands then fill one whole window).
             Only the final command of an entry may be short. Padding bytes:
             0xA7 between sections, 0x93 for 128-alignment inside the data
             section, 0xCD inside skip commands.

Verified in __main__ (run: python wiiu_ref/ipak.py):
  - parse + re-emit of genuine Wii U/360/PS3 paks is byte-exact
  - PS3 LZO entries decompress with crc32 dataHash match
  - GOLD: genuine Wii U content/mp_raid.ipak is rebuilt BYTE-EXACT from
    nothing but its (nameHash, dataHash, payload) triples + the two metadata
    sections, so write_ipak() is retail-canonical
  - all 124 mp_raid.ipak keys match GfxImage streamedParts of
    mp_raid_genuine.zone exactly; the remaining zone part keys resolve in
    content/base_split*.ipak (parts 1+) and content/lowmip_split*.ipak
    (part 0), 2249/2249 total -> common-asset passthrough is pure key lookup

Pixel payloads for authored parts must be GX2-tiled first
(wiiu_ref/gx2_texture.py tile()).
"""
import struct
import sys
import zlib

CHUNK = 0x8000
WINDOW = CHUNK * 8            # section / file alignment in genuine paks
BLOCK_HDR = 128
CMD_MAX = 0x7FF0              # payload bytes per command (retail Wii U
                              # linker value, measured; OAT uses 0x7F00)
PAD = 0xA7                    # section-level padding
PAD_DATA = 0x93               # 128-alignment padding inside the data section
PAD_SKIP = 0xCD               # skip-command payload fill
CMD_RAW, CMD_LZO, CMD_XMEM, CMD_SKIP = 0, 1, 2, 0xCF

MAGIC_BE = b'IPAK'
MAGIC_LE = b'KAPI'
VERSION = 0x50000
SEC_INDEX, SEC_DATA = 1, 2


def r_hash_string(name, h=0):
    """T6 R_HashString: case-folding djb-xor. == GfxImage.hash for the name."""
    for c in name.encode('latin-1'):
        h = (33 * h & 0xFFFFFFFF) ^ (c | 0x20)
    return h


def data_hash(payload, part_index=0):
    """Index dataHash / GfxImage streamedParts[i].hash for a part payload."""
    return (part_index << 29) | (zlib.crc32(payload) & 0x1FFFFFFF)


def lzo1x_decompress(src):
    """Pure-Python LZO1X decompressor (transcribed from minilzo lzo1x_d.c)."""
    ip = 0
    out = bytearray()

    def copy_match(dist, length):
        pos = len(out) - dist
        if pos < 0:
            raise ValueError("lzo: match before start")
        for _ in range(length):
            out.append(out[pos])
            pos += 1

    def first_literal_run():
        nonlocal ip
        t = src[ip]; ip += 1
        if t >= 16:
            return t
        dist = (1 + 0x0800) + (t >> 2) + (src[ip] << 2); ip += 1
        copy_match(dist, 3)
        lit = src[ip - 2] & 3
        if lit:
            out.extend(src[ip:ip + lit]); ip += lit
            t = src[ip]; ip += 1
            return t
        return None                      # back to literal-run state

    t = src[ip]
    tok = None
    if t > 17:
        ip += 1
        t -= 17
        out.extend(src[ip:ip + t]); ip += t
        if t < 4:
            tok = src[ip]; ip += 1       # match_next
        else:
            tok = first_literal_run()

    while True:
        if tok is None:
            t = src[ip]; ip += 1
            if t < 16:
                if t == 0:
                    while src[ip] == 0:
                        t += 255; ip += 1
                    t += 15 + src[ip]; ip += 1
                t += 3
                out.extend(src[ip:ip + t]); ip += t
                tok = first_literal_run()
                continue
            tok = t
        # match state: tok is the current token
        t = tok
        if t >= 64:
            dist = 1 + ((t >> 2) & 7) + (src[ip] << 3); ip += 1
            copy_match(dist, (t >> 5) - 1 + 2)
        elif t >= 32:
            length = t & 31
            if length == 0:
                while src[ip] == 0:
                    length += 255; ip += 1
                length += 31 + src[ip]; ip += 1
            dist = 1 + (src[ip] >> 2) + (src[ip + 1] << 6); ip += 2
            copy_match(dist, length + 2)
        elif t >= 16:
            length = t & 7
            if length == 0:
                while src[ip] == 0:
                    length += 255; ip += 1
                length += 7 + src[ip]; ip += 1
            dist = ((t & 8) << 11) + (src[ip] >> 2) + (src[ip + 1] << 6)
            ip += 2
            if dist == 0:
                return bytes(out)        # end-of-stream marker
            copy_match(dist + 0x4000, length + 2)
        else:                            # M1: 2-byte match
            dist = 1 + (t >> 2) + (src[ip] << 2); ip += 1
            copy_match(dist, 2)
        lit = src[ip - 2] & 3
        if lit:
            out.extend(src[ip:ip + lit]); ip += lit
            tok = src[ip]; ip += 1
        else:
            tok = None


class IPakEntry(object):
    __slots__ = ('name_hash', 'data_hash', 'offset', 'size')

    def __init__(self, name_hash, data_hash_, offset, size):
        self.name_hash, self.data_hash = name_hash, data_hash_
        self.offset, self.size = offset, size

    @property
    def key(self):
        return (self.name_hash << 32) | self.data_hash

    @property
    def part_index(self):
        return self.data_hash >> 29

    def __repr__(self):
        return ('IPakEntry(name=%#010x data=%#010x off=%#x size=%#x)' %
                (self.name_hash, self.data_hash, self.offset, self.size))


class IPak(object):
    def __init__(self, data, endian=None):
        self.raw = data
        magic = data[:4]
        if magic == MAGIC_BE:
            self.e = '>'
        elif magic == MAGIC_LE:
            self.e = '<'
        else:
            raise ValueError('not an IPAK: magic %r' % magic)
        version, self.file_size, nsec = struct.unpack(self.e + '3I', data[4:16])
        if version != VERSION:
            raise ValueError('unexpected IPAK version %#x' % version)
        self.sections = [struct.unpack(self.e + '4I', data[16 + 16*i:32 + 16*i])
                         for i in range(nsec)]
        self.data_off = self.data_size = None
        self.index_off = None
        self.entries = []
        self.extra_sections = []      # [(type, raw bytes, itemCount)] beyond 1/2
        for typ, off, size, count in self.sections:
            if typ == SEC_DATA:
                self.data_off, self.data_size = off, size
            elif typ == SEC_INDEX:
                self.index_off = off
                for i in range(count):
                    a, b, eo, es = struct.unpack(
                        self.e + '4I', data[off + 16*i:off + 16*i + 16])
                    # the key is one u64 (nameHash<<32 | dataHash) serialized
                    # in FILE endianness, so the word ORDER flips with it:
                    # BE stores nameHash first, LE (PC) stores dataHash first
                    nh, dh = (a, b) if self.e == '>' else (b, a)
                    self.entries.append(IPakEntry(nh, dh, eo, es))
            else:
                # Wii U retail paks carry two extra sections: type 4 = key list
                # {nameHash,dataHash} and type 3 = per-part source records
                # ("iwi: images/<name>.iwi\nformat: ...\n"). Linker metadata,
                # carried opaquely.
                self.extra_sections.append((typ, data[off:off + size], count))
        self.by_key = {en.key: en for en in self.entries}

    @classmethod
    def read(cls, path):
        with open(path, 'rb') as f:
            return cls(f.read())

    def find(self, name_hash, data_hash_=None):
        """Entries for one nameHash (all parts), or the exact part."""
        if data_hash_ is not None:
            return self.by_key.get((name_hash << 32) | data_hash_)
        return [en for en in self.entries if en.name_hash == name_hash]

    def entry_span(self, entry):
        """Raw file bytes of the entry (block headers + payloads)."""
        base = self.data_off + entry.offset
        return self.raw[base:base + entry.size]

    def extract(self, entry, verify=False):
        """Decompressed payload of an entry. cmd 2 (XMem/LZX, Xbox 360) is not
        implemented in pure Python and raises."""
        d = self.raw
        p = self.data_off + entry.offset
        end = p + entry.size
        out = bytearray()
        while p < end:
            first, = struct.unpack(self.e + 'I', d[p:p + 4])
            count = first >> 24
            cmds = struct.unpack(self.e + '%dI' % count, d[p + 4:p + 4 + 4*count])
            p += BLOCK_HDR
            for c in cmds:
                comp, sz = c >> 24, c & 0xFFFFFF
                if comp == CMD_RAW:
                    out += d[p:p + sz]
                elif comp == CMD_LZO:
                    out += lzo1x_decompress(d[p:p + sz])
                elif comp == CMD_SKIP:
                    pass
                elif comp == CMD_XMEM:
                    raise NotImplementedError(
                        'cmd 2 = XMemCompress (Xbox 360), no pure-Python codec')
                else:
                    raise ValueError('unknown ipak command %#x' % comp)
                p += sz
            p = (p + BLOCK_HDR - 1) & ~(BLOCK_HDR - 1)
        blob = bytes(out)
        if verify and (zlib.crc32(blob) & 0x1FFFFFFF) != (entry.data_hash & 0x1FFFFFFF):
            raise ValueError('dataHash mismatch for %r' % entry)
        return blob


# ---------------------------------------------------------------- writing ---

def _pad_to(buf, align, pad=PAD):
    rem = -len(buf) % align
    buf += bytes([pad]) * rem


def _write_entry_data(buf, payload):
    """Emit one entry the way the retail Wii U linker does (measured on
    content/mp_raid.ipak and byte-exactly reproduced):
      - commands are uncompressed (cmd 0), CMD_MAX = 0x7FF0 payload bytes
      - block headers are 128-aligned; command payloads follow back to back
      - the 0x40000 read window is absolute in the data section (the data
        section itself starts window-aligned). When the next full command
        would cross a window boundary, the current block is closed, a
        one-command skip block (cmd 0xCF, offset word 0) pads to the boundary,
        and an 8-command block spans each following full window
      - only the final command of the entry may be shorter than CMD_MAX
    Returns (entry_offset, entry_size) into buf."""
    _pad_to(buf, BLOCK_HDR, PAD_DATA)
    start = len(buf)
    window_start = start & ~(CHUNK - 1)   # previous chunk boundary; read
                                          # windows are window_start + k*0x40000

    def flush_block(hdr_at, cmds, off_at_start):
        first = (len(cmds) << 24) | (off_at_start & 0xFFFFFF)
        words = [first] + cmds + [0] * (31 - len(cmds))
        buf[hdr_at:hdr_at + BLOCK_HDR] = struct.pack('>32I', *words)

    def new_block():
        _pad_to(buf, BLOCK_HDR, PAD_DATA)
        hdr_at = len(buf)
        buf.extend(b'\x00' * BLOCK_HDR)
        return hdr_at, []

    file_off = 0                                  # decompressed offset
    hdr_at, cmds = new_block()
    block_off = 0
    pos = 0
    while pos < len(payload):
        n = min(len(payload) - pos, CMD_MAX)
        if (len(buf) - window_start) % WINDOW == 0 and cmds:
            # exactly at a read-window boundary: a block never spans one
            flush_block(hdr_at, cmds, block_off)
            hdr_at, cmds = new_block()
            block_off = file_off
        boundary = window_start + ((len(buf) - window_start) // WINDOW + 1) * WINDOW
        if len(buf) + n > boundary:
            # close the block, skip-pad to the window boundary, reopen there
            flush_block(hdr_at, cmds, block_off)
            skip_at, _ = new_block()
            skip_len = boundary - len(buf)
            flush_block(skip_at, [(CMD_SKIP << 24) | skip_len], 0)
            buf.extend(bytes([PAD_SKIP]) * skip_len)
            hdr_at, cmds = new_block()
            block_off = file_off
        elif len(cmds) == 31:
            flush_block(hdr_at, cmds, block_off)
            hdr_at, cmds = new_block()
            block_off = file_off
        buf.extend(payload[pos:pos + n])
        cmds.append((CMD_RAW << 24) | n)
        pos += n
        file_off += n
    flush_block(hdr_at, cmds, block_off)
    _pad_to(buf, BLOCK_HDR, PAD_DATA)
    return start, len(buf) - start


def write_ipak(entries, endian='>', extra_sections=(), keep_order=False):
    """Author an ipak. entries: iterable of (name_hash, data_hash, payload) or
    (name, part_index, payload) -- strings are hashed, part payloads crc'd.
    endian '>' = console (Wii U / 360 / PS3 layout), '<' = PC.
    Payloads are written uncompressed (cmd 0), same as retail Wii U paks.
    The index is always sorted by combined key; the data section follows the
    given entry order when keep_order is set (retail data order is unrelated
    to key order), otherwise key order.
    extra_sections: [(type, raw_bytes, itemCount)] appended verbatim (Wii U
    retail carries metadata sections 4 and 3). Returns bytes."""
    norm = []
    for a, b, payload in entries:
        if isinstance(a, str):
            norm.append((r_hash_string(a), data_hash(payload, b), payload))
        else:
            norm.append((a, b, payload))
    if not keep_order:
        norm.sort(key=lambda t: (t[0] << 32) | t[1])

    body = bytearray()
    index = []
    for nh, dh, payload in norm:
        off, size = _write_entry_data(body, payload)
        index.append((nh, dh, off, size))
    index.sort(key=lambda t: (t[0] << 32) | t[1])
    data_size = len(body)

    nsec = 2 + len(extra_sections)
    out = bytearray()
    out += (MAGIC_BE if endian == '>' else MAGIC_LE)
    out += b'\x00' * 12                       # header filled at the end
    out += b'\x00' * (16 * nsec)              # section descriptors
    _pad_to(out, WINDOW)
    secs = []
    data_off = len(out)
    out += body
    secs.append((SEC_DATA, data_off, data_size, len(index)))
    _pad_to(out, WINDOW)
    index_off = len(out)
    for nh, dh, off, size in index:
        a, b = (nh, dh) if endian == '>' else (dh, nh)
        out += struct.pack(endian + '4I', a, b, off, size)
    secs.append((SEC_INDEX, index_off, 16 * len(index), len(index)))
    for typ, raw, count in extra_sections:
        _pad_to(out, WINDOW)
        secs.append((typ, len(out), len(raw), count))
        out += raw
    _pad_to(out, WINDOW)

    out[4:16] = struct.pack(endian + '3I', VERSION, len(out), nsec)
    for i, sec in enumerate(secs):
        out[16 + 16*i:32 + 16*i] = struct.pack(endian + '4I', *sec)
    return bytes(out)


# ------------------------------------------------- PC source-blob lookup ---

# PC ipak entries are complete .iwi files: 'IWi' magic, version 0x1B (T6),
# u8 iwiFormat, u8 flags, u16 width, height, depth, then the mip data stored
# smallest-mip-first with the TOP mip at the file tail.
IWI_TO_GX2 = {0x0b: 0x31, 0x0c: 0x32, 0x0d: 0x33, 0x0e: 0x35, 0x01: 0x1a,
              0x08: 0x1a}


def parse_iwi(blob):
    """-> dict(iwi_format, gx2_format, width, height, depth, mips[, cube, cube_mips]) where mips
    is [(w, h, offset, size)] from the top mip down (top mip sits at the file
    tail; offsets are into blob).

    CUBE (rule B3, baked 2026-07-30): flag 0x04 marks a 6-face cubemap. The interleave is
    **MIP-MAJOR, SMALLEST-FIRST** (NOT face-major): file order = [smallest mip: face0..5] ...
    [top mip: face0..5], so the top mip's 6 faces sit at the TAIL. Established 07-26 by a
    mip-pyramid self-consistency test (2.58 vs 17.87, 6.9x over every alternative) then a
    byte-exact 6-face round-trip vs the genuine carrier skybox. The old code walked a cube blob
    as one 2D mip chain and invented ~10 bogus "mips" (mp_skate_ft shipped 22.9% of its pixels
    with faces 0-3 entirely absent). For cubes: `cube=True`, `cube_mips` = top-first list of
    6-face groups [(w,h,off,size) x6], and `mips` = the face-0 representative chain."""
    if blob[:3] != b'IWi':
        raise ValueError('not an IWI blob')
    version = blob[3]
    ifmt, flags = blob[4], blob[5]
    w, h, depth = struct.unpack('<HHH', blob[6:12])
    gfmt = IWI_TO_GX2.get(ifmt)

    def level_size(lw, lh):
        if gfmt in (0x31,):
            return max(1, (lw+3)//4) * max(1, (lh+3)//4) * 8
        if gfmt in (0x32, 0x33, 0x35):
            return max(1, (lw+3)//4) * max(1, (lh+3)//4) * 16
        return lw * lh * 4

    if flags & 0x04:
        # 6-face cube: walk the tail backwards in 6-face groups, top mip first
        cube_mips = []
        end = len(blob)
        lw, lh = w, h
        while lw >= 1 and lh >= 1:
            sz = level_size(lw, lh)
            if end - 6 * sz < 12:              # would run into the 12-B header
                break
            grp = [(lw, lh, end - 6 * sz + f * sz, sz) for f in range(6)]
            cube_mips.append(grp)
            end -= 6 * sz
            if lw == 1 and lh == 1:
                break
            lw, lh = max(1, lw // 2), max(1, lh // 2)
        if not cube_mips:
            raise ValueError('IWI cube flag set but no 6-face group fits (blob %d B, %dx%d)'
                             % (len(blob), w, h))
        return dict(version=version, iwi_format=ifmt, gx2_format=gfmt, flags=flags,
                    width=w, height=h, depth=depth,
                    mips=[grp[0] for grp in cube_mips], cube=True, cube_mips=cube_mips)

    mips = []
    end = len(blob)
    lw, lh = w, h
    while lw >= 1 and lh >= 1 and end > 0:
        sz = level_size(lw, lh)
        if end - sz < 0:
            break
        mips.append((lw, lh, end - sz, sz))
        end -= sz
        if lw == 1 and lh == 1:
            break
        lw, lh = max(1, lw // 2), max(1, lh // 2)
    return dict(version=version, iwi_format=ifmt, gx2_format=gfmt, flags=flags,
                width=w, height=h, depth=depth, mips=mips)


class PcImageSource(object):
    """Find a map image's source pixels in the PC (KAPI) ipaks.

    paths: iterable of PC .ipak paths (e.g. base/mp/zm/dlc0-4). Lookup key is
    the platform-independent nameHash = R_HashString(imageName) =
    GfxImage.hash (identical in PC and Wii U .ff, verified on mp_raid)."""

    def __init__(self, paths):
        self.paks = [IPak.read(p) for p in paths]
        self.by_name = {}
        for pak in self.paks:
            for en in pak.entries:
                self.by_name.setdefault(en.name_hash, []).append((pak, en))

    def find_pc_source(self, image_name_or_hash, verify=True):
        """-> list of dicts (one per PC part, part 0 first), each with the raw
        iwi bytes, the parsed header (gx2_format, width, height, mips) and the
        (name_hash, data_hash) key. Empty list if the image is not in any pak.
        Feed a mip slice (iwi['mips']) to gx2_texture.tile() to produce the
        matching Wii U part payload (proven byte-identical on mp_raid)."""
        nh = (r_hash_string(image_name_or_hash)
              if isinstance(image_name_or_hash, str) else image_name_or_hash)
        out = []
        for pak, en in self.by_name.get(nh, []):
            blob = pak.extract(en, verify=verify)
            out.append(dict(name_hash=en.name_hash, data_hash=en.data_hash,
                            part_index=en.part_index, blob=blob,
                            iwi=parse_iwi(blob), pak=pak))
        out.sort(key=lambda r: r['part_index'])
        return out


def reserialize(pak):
    """Re-emit a parsed IPak from its parsed fields plus the raw section
    bytes. Byte-exact against the genuine file iff the parse is complete."""
    out = bytearray()
    out += (MAGIC_BE if pak.e == '>' else MAGIC_LE)
    out += struct.pack(pak.e + '3I', VERSION, pak.file_size, len(pak.sections))
    for sec in pak.sections:
        out += struct.pack(pak.e + '4I', *sec)
    extra = {typ: raw for typ, raw, _ in pak.extra_sections}
    for typ, off, size, count in sorted(pak.sections, key=lambda s: s[1]):
        _pad_to(out, WINDOW)
        assert len(out) == off, 'section %d not window-aligned' % typ
        if typ == SEC_DATA:
            out += pak.raw[off:off + size]
        elif typ == SEC_INDEX:
            for en in pak.entries:
                a, b = ((en.name_hash, en.data_hash) if pak.e == '>' else
                        (en.data_hash, en.name_hash))
                out += struct.pack(pak.e + '4I', a, b, en.offset, en.size)
        else:
            out += extra[typ]
    _pad_to(out, WINDOW)
    return bytes(out)


# ----------------------------------------------------------- verification ---

def _verify():
    import os
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    paks = [os.path.join(root, p) for p in (
        'xbox_nuketown_extracted/dlczm0_load_zm.ipak',
        'xbox_nuketown_extracted/zm_nuked.ipak',
        'ps3 ff/zm_nuked.ipak')]

    for path in paks:
        if not os.path.exists(path):
            print('MISSING', path)
            continue
        pak = IPak.read(path)
        again = reserialize(pak)
        exact = again == pak.raw
        print('%s: %d entries, data %#x..%#x, re-emit byte-exact: %s' %
              (os.path.basename(os.path.dirname(path)) + '/' +
               os.path.basename(path), len(pak.entries), pak.data_off,
               pak.data_off + pak.data_size, exact))
        assert exact
        # index invariants
        keys = [en.key for en in pak.entries]
        assert keys == sorted(keys), 'index not sorted by combined key'
        pos = 0
        for en in sorted(pak.entries, key=lambda x: x.offset):
            assert en.offset == pos, 'entries not contiguous'
            pos += en.size
        assert pos == pak.data_size

    # PS3 pak entries are LZO: full decompress + crc verification
    ps3 = IPak.read(paks[2])
    n = 0
    for en in ps3.entries[:200]:
        ps3.extract(en, verify=True)
        n += 1
    print('ps3 zm_nuked.ipak: %d/%d entries decompressed, crc32 dataHash OK'
          % (n, n))

    # authoring round trip: real Wii U image payloads -> BE ipak -> read back
    zone_path = os.path.join(root, 'wiiu_ref', 'mp_raid_genuine.zone')
    triples = []
    if os.path.exists(zone_path):
        d = open(zone_path, 'rb').read()
        import re as _re
        got = 0
        for m in _re.finditer(rb'\xff\xff\xff\xff', d):
            B = m.start() - 320
            if B < 0:
                continue
            spc, streaming = d[B + 316], d[B + 171]
            if spc != 0 or streaming != 0 or d[B + 317:B + 320] != b'\x00\x00\x00':
                continue
            base = struct.unpack('>I', d[B + 160:B + 164])[0]
            if not (0x1000 <= base <= 0x200000):
                continue
            if struct.unpack('>I', d[B + 176:B + 180])[0] != 0xFFFFFFFF:
                continue
            e = d.index(b'\x00', B + 328)
            nm = d[B + 328:e].decode('latin-1', 'replace')
            if not nm or not nm[0].isalpha():
                continue
            stored_hash = struct.unpack('>I', d[B + 324:B + 328])[0]
            if r_hash_string(nm) != stored_hash:
                continue
            pixels = d[e + 1:e + 1 + base]           # GX2-tiled, as shipped
            triples.append((nm, 0, pixels))
            got += 1
            if got == 12:
                break
        blob = write_ipak(triples, endian='>')
        pak2 = IPak(blob)
        assert reserialize(pak2) == blob
        ok = 0
        for nm, pi, pixels in triples:
            en = pak2.find(r_hash_string(nm), data_hash(pixels, pi))
            assert en is not None, nm
            assert pak2.extract(en, verify=True) == pixels, nm
            ok += 1
        print('authoring: %d Wii U GX2 payloads -> new BE ipak -> extract '
              'byte-exact, keys re-derived from name+payload' % ok)
    else:
        print('SKIP authoring test:', zone_path, 'missing')

    # genuine Wii U retail paks (the real target format)
    wiiu = (r'C:\Users\Tony - Main Rig\Downloads'
            r'\Wii U Call of Duty Black Ops 2 USA WUP\wuo\content')
    mp_raid_pak = os.path.join(wiiu, 'mp_raid.ipak')
    if os.path.exists(mp_raid_pak):
        pak = IPak.read(mp_raid_pak)
        assert reserialize(pak) == pak.raw
        for en in pak.entries:
            pak.extract(en, verify=True)
        print('WiiU mp_raid.ipak: %d entries, re-emit byte-exact, all '
              'payloads crc-verified (uncompressed cmd 0)' % len(pak.entries))
        trip = [(en.name_hash, en.data_hash, pak.extract(en))
                for en in sorted(pak.entries, key=lambda x: x.offset)]
        blob = write_ipak(trip, endian='>',
                          extra_sections=pak.extra_sections, keep_order=True)
        assert blob == pak.raw
        print('WiiU GOLD: mp_raid.ipak rebuilt byte-exact from '
              '(key, payload) triples by write_ipak()')
    else:
        print('SKIP Wii U retail checks:', mp_raid_pak, 'missing')

    # PC (KAPI, little-endian) paks + cross-platform key identity
    pc_dir = r'E:\pluto_t6_full_game\zone\all'
    if os.path.exists(os.path.join(pc_dir, 'base.ipak')) and \
            os.path.exists(mp_raid_pak):
        src = PcImageSource([os.path.join(pc_dir, p)
                             for p in ('base.ipak', 'mp.ipak')])
        for pak in src.paks:
            assert reserialize(pak) == pak.raw
        print('PC base/mp.ipak: %d + %d entries, re-emit byte-exact' %
              tuple(len(p.entries) for p in src.paks))
        wu = IPak.read(mp_raid_pak)
        wu_names = {en.name_hash for en in wu.entries}
        found = sum(1 for nh in wu_names if src.by_name.get(nh))
        print('cross-platform: %d/%d Wii U mp_raid nameHashes found in PC '
              'base+mp' % (found, len(wu_names)))
        assert found == len(wu_names)
        # pixel identity: detiled Wii U part == PC IWI mip slice
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import gx2_texture as gx
        exact = 0
        for en in wu.entries:
            parts = src.find_pc_source(en.name_hash)
            if not parts:
                continue
            iwi = parts[0]['iwi']
            gfmt = iwi['gx2_format']
            payload = wu.extract(en)
            hit = False
            for lw, lh, off, sz in iwi['mips']:
                for tm in (4, 2):
                    if gx.surface_info(gfmt, lw, lh, tm).size != len(payload):
                        continue
                    lin = gx.detile(payload, lw, lh, gfmt, tm)
                    tight = gx.crop_linear(lin, lw, lh, gfmt,
                                           gx.surface_info(gfmt, lw, lh, tm).pitch)
                    if tight == parts[0]['blob'][off:off + sz]:
                        hit = True
                    break
                if hit:
                    break
            exact += hit
        print('PIXEL IDENTITY: %d/%d Wii U mp_raid.ipak payloads equal the '
              'GX2-detiled PC IWI mip slice' % (exact, len(wu.entries)))
        assert exact == len(wu.entries)
    else:
        print('SKIP PC cross-platform checks (PC paks or Wii U pak missing)')
    print('ALL CHECKS PASSED')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] not in ('-v', '--verify'):
        pak = IPak.read(sys.argv[1])
        print('%s: endian %s, %d entries' %
              (sys.argv[1], 'BE' if pak.e == '>' else 'LE', len(pak.entries)))
        for en in pak.entries[:40]:
            print('  %r part=%d' % (en, en.part_index))
        if len(pak.entries) > 40:
            print('  ... %d more' % (len(pak.entries) - 40))
    else:
        _verify()
