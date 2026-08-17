#!/usr/bin/env python3
"""
Native WiiU (T6, v148) zone WRITE engine — Stage 1 of the from-scratch linker.

Ports the semantics of OAT's InMemoryZoneOutputStream to Python, console-configured
(big-endian, 32-bit pointers, 8 blocks, 3 block bits). The whole point of a real
linker (vs the transplant band-aid) is that THIS engine assigns every asset's block
offset as it writes, so every pointer is emitted correctly by construction — no
remapping, no dangling references.

Pointer encoding (matches the loader): a zone pointer to (block b, offset o) is
    value = (b << 29) | (o & 0x1FFFFFFF) ; value += 1     (the +1 keeps 0 == null)
Sentinels: FOLLOW = 0xFFFFFFFF (data written inline, next in stream order),
           INSERT = 0xFFFFFFFE, null = 0.

Block model (learned from the genuine zone + OAT):
  * The decompressed stream is written in DFS order; each byte belongs to whichever
    block is active when written. A pointer's offset is that block's running size at
    write time.
  * File-backed blocks (their bytes appear in the stream): TEMP(0), VIRTUAL(5),
    PHYSICAL(6). RUNTIME/DELAY blocks emit no stream bytes (loader memsets them);
    their running size still advances so intra-block pointers resolve.
  * The 40-byte XFile header (size, externalSize, blockSize[8]) precedes all block
    data; the 24-byte XAssetList that follows sits at stream offset 40, and block 5
    begins at stream offset 64 (so block-5 offset == stream offset - 64).

This module is deliberately free of asset knowledge; the structural layer drives it.
"""
import struct

FOLLOW = 0xFFFFFFFF
INSERT = 0xFFFFFFFE

# console block indices / types
BLOCK_TEMP      = 0
BLOCK_RT_TEMP   = 1   # runtime, no file bytes
BLOCK_PHYSICAL  = 2
BLOCK_RT_PHYS   = 3
BLOCK_VIRTUAL   = 5
BLOCK_RT_VIRT   = 7

FILE_BACKED = {0, 2, 5, 6}          # blocks whose bytes appear in the stream
RUNTIME     = {1, 3, 4, 7}          # blocks that emit no stream bytes

BLOCK_BITS   = 3
OFFSET_MASK  = (1 << (32 - BLOCK_BITS)) - 1     # 0x1FFFFFFF
BLOCK_SHIFT  = 32 - BLOCK_BITS                   # 29


def encode_ptr(block, offset):
    """(block, offset) -> encoded zone pointer value."""
    return (((block << BLOCK_SHIFT) | (offset & OFFSET_MASK)) + 1) & 0xFFFFFFFF


def decode_ptr(v):
    """encoded value -> (block, offset). Caller checks for FOLLOW/INSERT/0 first."""
    v = (v - 1) & 0xFFFFFFFF
    return (v >> BLOCK_SHIFT), (v & OFFSET_MASK)


class ReusableEntry:
    __slots__ = ('start', 'end', 'entry_size', 'block_size', 'start_ptr')
    def __init__(self, start, entry_size, count, block_size, start_ptr):
        self.start = start
        self.end = start + entry_size * count
        self.entry_size = entry_size
        self.block_size = block_size
        self.start_ptr = start_ptr      # encoded zone pointer of element 0


class ZoneWriter:
    """DFS write engine. `emit()` returns the full stream (header + content)."""
    def __init__(self):
        self.buf = bytearray()          # content bytes (everything after the 40-byte header)
        self.block_size = [0] * 8       # running size per block (== next write offset)
        self.stack = []                 # active block stack
        # reusable-memory registry, keyed by a caller-supplied type tag so a later
        # write of the same source array emits an alias instead of duplicating it
        self.reusable = {}              # tag -> [ReusableEntry, ...]
        self.external_size = 0

    # ---- block management -------------------------------------------------
    def push_block(self, block):
        self.stack.append(block)

    def pop_block(self):
        return self.stack.pop()

    @property
    def cur_block(self):
        return self.stack[-1]

    def align(self, n):
        if n <= 1:
            return
        b = self.cur_block
        newsz = (self.block_size[b] + n - 1) & ~(n - 1)
        pad = newsz - self.block_size[b]
        if b in FILE_BACKED and pad:
            self.buf += b'\x00' * pad
        self.block_size[b] = newsz

    def zone_ptr(self):
        """Encoded pointer for the current write position of the active block."""
        return encode_ptr(self.cur_block, self.block_size[self.cur_block])

    # ---- primitive writes -------------------------------------------------
    def write_bytes(self, data):
        b = self.cur_block
        if b in FILE_BACKED:
            self.buf += data
        self.block_size[b] += len(data)

    def write_u32(self, v):
        self.write_bytes(struct.pack('>I', v & 0xFFFFFFFF))

    def write_u16(self, v):
        self.write_bytes(struct.pack('>H', v & 0xFFFF))

    def write_u8(self, v):
        self.write_bytes(bytes([v & 0xFF]))

    def write_cstr(self, s):
        if isinstance(s, str):
            s = s.encode('latin-1')
        self.write_bytes(s + b'\x00')

    def inc_block_pos(self, n):
        """Advance the active block without emitting file bytes (headroom / runtime)."""
        b = self.cur_block
        if b in FILE_BACKED:
            self.buf += b'\x00' * n
        self.block_size[b] += n

    # ---- pointer patching -------------------------------------------------
    def patch_u32(self, content_offset, v):
        struct.pack_into('>I', self.buf, content_offset, v & 0xFFFFFFFF)

    # ---- reusable-memory aliasing ----------------------------------------
    def reusable_should_write(self, tag, key, count, entry_size, block_size):
        """Return (True, None) if this array must be written fresh, else
        (False, encoded_ptr) to emit an alias to the already-written copy.
        `key` is a stable identity for the source array (e.g. its genuine offset)."""
        for e in self.reusable.get(tag, ()):
            if e.start <= key < e.end and (key - e.start) % e.entry_size == 0:
                idx = (key - e.start) // e.entry_size
                return False, (e.start_ptr + idx * e.block_size)
        return True, None

    def reusable_add(self, tag, key, count, entry_size, block_size):
        """Register an array just written at the current position for later reuse."""
        self.reusable.setdefault(tag, []).append(
            ReusableEntry(key, entry_size, count, block_size, self.zone_ptr()))

    # ---- finalize ---------------------------------------------------------
    def emit(self, total_size=None):
        """Prepend the 40-byte XFile header. `size` = total decompressed zone size;
        by default the current stream length, but a partial round-trip (container
        only, bodies not yet emitted) can pass the source value to carry it through."""
        total = 40 + len(self.buf) if total_size is None else total_size
        header = struct.pack('>II', total, self.external_size) + \
                 struct.pack('>8I', *self.block_size)
        return header + bytes(self.buf)


if __name__ == '__main__':
    # smoke test: encode/decode round-trip
    for b, o in [(5, 0x35c8), (0, 0), (5, 0x22ca9cf), (7, 0xbb6025b)]:
        v = encode_ptr(b, o)
        assert decode_ptr(v) == (b, o), (b, o, hex(v), decode_ptr(v))
    print("zone_stream: encode/decode OK")
