"""
wiiu_ff.py -- Wii U Black Ops II (T6) fastfile codec.

Decrypt + decompress a Wii U `.ff` into its raw decompressed zone, and pack a
zone back into a valid Wii U v148 fastfile. Big-endian throughout; Salsa20 over
four interleaved chunk streams, each chunk raw-deflate compressed, framed with
the 0x80000 super-block size-field alignment the loader expects.

Header layout (all multi-byte fields big-endian):
  0x000  char  magic[8]        "TAff0100"
  0x008  u32   version         148
  0x00C  char  authMagic[8]    "PHEEBs71"
  0x014  u32   loadFlags       0
  0x018  char  fastfileName[32]
  0x038  u8    signature[256]  RSA-2048 PSS (emitted as zeros; see note below)
  0x138  XChunk stream: repeated [u32 size][size bytes], chunk i -> stream i%4,
         size 0 terminates, size fields never straddle a 0x80000 boundary.

Signature note: the 256-byte RSA block is signed with a private key we do not
have, so pack writes zeros there. Whether the target accepts that depends on
the loader (unsigned-FF path / signature-check bypass).
"""
import struct
import zlib
import hashlib
import os

# Wii U Salsa20 key.
KEY_WIIU = bytes([
    0xB3, 0xBD, 0x6B, 0x2C, 0x82, 0x42, 0x8D, 0x11,
    0xB8, 0x88, 0x2D, 0x4C, 0x6D, 0x18, 0xCC, 0x79,
    0xE2, 0x70, 0x9F, 0x6B, 0xD4, 0x39, 0x91, 0x35,
    0xFD, 0xDE, 0x14, 0xE6, 0x8F, 0x3A, 0xBC, 0xCE,
])

WIIU_VERSION      = 148
STREAM_COUNT      = 4
XCHUNK_SIZE       = 0x8000      # max compressed bytes per chunk
VANILLA_BUFSIZE   = 0x80000     # super-block size; size fields align to this
BLOCK_HASHES      = 200
SHA1_SIZE         = 20
IV_SIZE           = 8
HEADER_DATA_START = 0x138

# Pack tuning. Genuine Wii U fastfiles emit a 40-byte header block FIRST, then
# uniform 0x7FC0 (32704) uncompressed blocks -- deliberately 64 bytes UNDER the
# console's fixed 0x8000 decompression buffer. Writing full 0x8000 blocks rides
# the buffer to its exact edge: the zone DB-loads but the accumulated boundary
# desync corrupts memory and crashes later during streaming. Match genuine.
HEADER_BLOCK       = 40         # first decompression unit (DB zone header)
UNCOMPRESSED_BLOCK = 0x7FC0     # 32704; every block after the header
FILE_SUFFIX_MIN    = 0x40
FILE_SUFFIX_ALIGN  = 0x40

try:
    from salsa20 import Salsa20
except ImportError:  # allow `import wiiu_ff` from a parent dir
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from salsa20 import Salsa20


class HashChain:
    """Per-fastfile IV state: BLOCK_HASHES x STREAM_COUNT x 20-byte table,
    seeded from the zone name and advanced by SHA-1 of each decrypted chunk."""

    def __init__(self, zone_name):
        name = (zone_name[:31] or " ").encode('latin1')
        total = BLOCK_HASHES * STREAM_COUNT * SHA1_SIZE
        buf = bytearray(total)
        off = 0
        for i in range(0, total, 4):
            buf[i:i + 4] = bytes([name[off]]) * 4
            off = (off + 1) % len(name)
        self.buf = buf
        self.block_index = [0] * STREAM_COUNT

    def _slot(self, stream, block):
        return (block * STREAM_COUNT + stream) * SHA1_SIZE

    def iv(self, stream):
        s = self._slot(stream, self.block_index[stream])
        return bytes(self.buf[s:s + IV_SIZE])

    def advance(self, stream, decrypted_chunk):
        sha = hashlib.sha1(decrypted_chunk).digest()
        self.block_index[stream] = (self.block_index[stream] + 1) % BLOCK_HASHES
        s = self._slot(stream, self.block_index[stream])
        for i in range(SHA1_SIZE):
            self.buf[s + i] ^= sha[i]


def is_wiiu_fastfile(data):
    """True if `data` looks like a Wii U (TAff0100 / v148, big-endian) fastfile."""
    if len(data) < 12 or data[0:4] != b'TAff' or data[5:8] != b'100':
        return False
    return struct.unpack('>I', data[8:12])[0] == WIIU_VERSION


def parse_header(data):
    magic = data[0:8]
    version = struct.unpack('>I', data[8:12])[0]
    auth = data[12:20]
    flags = struct.unpack('>I', data[20:24])[0]
    name = data[24:56].split(b'\x00')[0].decode('latin1')
    sig = data[56:312]
    if magic[:4] != b'TAff' or magic[5:] != b'100':
        raise ValueError(f"not a TAff fastfile (magic {magic!r})")
    if version != WIIU_VERSION:
        raise ValueError(f"not a Wii U fastfile (version {version}, expected {WIIU_VERSION})")
    if auth != b'PHEEBs71':
        raise ValueError(f"bad auth magic {auth!r}")
    return {'magic': magic, 'version': version, 'flags': flags, 'name': name, 'sig': sig}


def _iter_chunks(data, start=HEADER_DATA_START):
    pos = start
    vbuf_off = start
    while True:
        if vbuf_off + 4 > VANILLA_BUFSIZE:
            pos += VANILLA_BUFSIZE - vbuf_off
            vbuf_off = 0
        vbuf_off = (vbuf_off + 4) % VANILLA_BUFSIZE
        if pos + 4 > len(data):
            return
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        pos += 4
        if size == 0:
            return
        if size > XCHUNK_SIZE:
            raise ValueError(f"chunk size {size:#x} > max at {pos - 4:#x}")
        chunk = data[pos:pos + size]
        if len(chunk) != size:
            raise ValueError(f"truncated chunk at {pos:#x}")
        pos += size
        vbuf_off = (vbuf_off + size) % VANILLA_BUFSIZE
        yield chunk


def decrypt(data, key=KEY_WIIU, progress=None):
    """Wii U fastfile bytes -> (header dict, decompressed zone bytes, chunk count).

    `progress`, if given, is called as progress(done_bytes, total_bytes) every so
    often so a UI can show how far the (CPU-bound) decrypt has got.
    """
    hdr = parse_header(data)
    chain = HashChain(hdr['name'])
    total = len(data)
    out = bytearray()
    n = 0
    for i, enc in enumerate(_iter_chunks(data)):
        stream = i % STREAM_COUNT
        iv = chain.iv(stream)
        dec = Salsa20(key, iv).decrypt(enc)
        chain.advance(stream, dec)
        try:
            out += zlib.decompress(dec, -15)
        except Exception as e:
            raise RuntimeError(
                f"chunk {i} (stream {stream}) inflate failed: {e}\n"
                f"  iv={iv.hex()} encLen={len(enc)} dec[:16]={dec[:16].hex()}")
        n += 1
        if progress is not None and (n & 0x3f) == 0:
            progress(min(HEADER_DATA_START + len(out), total), total)
    if progress is not None:
        progress(total, total)
    return hdr, bytes(out), n


def _deflate_chunks(zone):
    # Genuine layout: a 40-byte header block, then uniform 0x7FC0 blocks. The
    # first block is the DB zone header the loader reads before streaming; every
    # subsequent block stays under the fixed 0x8000 decompression buffer.
    chunks = []
    pos = 0
    first = True
    while pos < len(zone):
        block = HEADER_BLOCK if first else UNCOMPRESSED_BLOCK
        while True:
            raw = zone[pos:pos + block]
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            comp = c.compress(raw) + c.flush()
            if len(comp) <= XCHUNK_SIZE or block <= 0x1000:
                break
            block -= 0x1000
        chunks.append(comp)
        pos += len(raw)
        first = False
    return chunks


def build_header(name):
    h = bytearray(HEADER_DATA_START)
    h[0:8] = b'TAff0100'
    struct.pack_into('>I', h, 8, WIIU_VERSION)
    h[12:20] = b'PHEEBs71'
    struct.pack_into('>I', h, 20, 0)
    nb = name.encode('latin1')[:31]
    h[24:24 + len(nb)] = nb
    return bytes(h)


def pack(zone, name, key=KEY_WIIU):
    """Decompressed zone bytes -> Wii U v148 fastfile bytes."""
    chain = HashChain(name)
    out = bytearray(build_header(name))
    vbuf_off = HEADER_DATA_START % VANILLA_BUFSIZE
    for i, comp in enumerate(_deflate_chunks(zone)):
        if vbuf_off + 4 > VANILLA_BUFSIZE:
            out += b'\x00' * (VANILLA_BUFSIZE - vbuf_off)
            vbuf_off = 0
        stream = i % STREAM_COUNT
        iv = chain.iv(stream)
        enc = Salsa20(key, iv).encrypt(comp)
        chain.advance(stream, comp)
        out += struct.pack('>I', len(comp)) + enc
        vbuf_off = (vbuf_off + 4 + len(comp)) % VANILLA_BUFSIZE
    if vbuf_off + 4 > VANILLA_BUFSIZE:
        out += b'\x00' * (VANILLA_BUFSIZE - vbuf_off)
    out += struct.pack('>I', 0)
    out += b'\x00' * FILE_SUFFIX_MIN
    out += b'\x00' * ((-len(out)) % FILE_SUFFIX_ALIGN)
    # 2026-07-14 (skate boot 13 breakthrough): the engine plans reads out to
    # ~ceil(size/0x40000)+1 blocks. Real WiiU title packaging zero-pads content past
    # EOF; Cemu loose mlc files return SHORT final reads leaving STALE bytes in the
    # 512KB read double-buffer, which the chunk parser then consumes as garbage chunk
    # lengths -> runaway in-place decrypt (the +0x3817ce crash family). Pad every ff
    # to a 256KB multiple plus one spare zero block so every planned read succeeds
    # with real zeros.
    target = ((len(out) + 0x3FFFF) & ~0x3FFFF) + 0x40000
    out += b'\x00' * (target - len(out))
    return bytes(out)


# ---- minimal CLI ----------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3 or sys.argv[1] not in ("decrypt", "pack"):
        print("usage: wiiu_ff.py decrypt <in.ff> [out.zone]")
        print("       wiiu_ff.py pack    <in.zone> <name> [out.ff]")
        raise SystemExit(2)
    if sys.argv[1] == "decrypt":
        data = open(sys.argv[2], "rb").read()
        hdr, zone, n = decrypt(data)
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(sys.argv[2])[0] + ".zone"
        open(out, "wb").write(zone)
        print(f"name={hdr['name']!r} chunks={n} zone={len(zone)} bytes -> {out}")
    else:
        zone = open(sys.argv[2], "rb").read()
        name = sys.argv[3]
        out = sys.argv[4] if len(sys.argv) > 4 else os.path.splitext(sys.argv[2])[0] + ".ff"
        ff = pack(zone, name)
        open(out, "wb").write(ff)
        print(f"packed {len(zone)} byte zone -> {len(ff)} byte ff ({out})  name={name!r}")
