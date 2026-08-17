"""Black Ops 2 (T6) Wii U / Xbox 360 fastfile -> decrypted+decompressed zone.

Reimplements the T6 "Xenon" signed-fastfile load pipeline (big-endian,
Salsa20 + raw-deflate, 4 interleaved XChunk streams) as documented by
OpenAssetTools, but with the Wii U Salsa20 key.

Layout (all multi-byte fields big-endian on Wii U/360):
  0x000  char magic[8]        "TAff0100"  (signed Treyarch)
  0x008  u32  version         148 (Wii U) / 146 (360)
  0x00C  char authMagic[8]    "PHEEBs71"
  0x014  u32  loadFlags       (0)
  0x018  char fastfileName[32]
  0x038  u8   signature[256]  (RSA-2048 PSS over the IV-hash blocks)
  0x138  XChunk stream begins: repeated [u32 size][size bytes]
         - chunk i is processed by stream (i % 4)
         - each chunk: Salsa20 decrypt, then raw-inflate (one full stream)
         - size==0 terminates; size fields never straddle a 0x80000 boundary
"""
import struct, zlib, hashlib, sys, os
from salsa20 import Salsa20

# Wii U Salsa20 key (supplied by user). Swap for the 360 key to read Xenon FFs.
KEY_WIIU = bytes([
    0xB3, 0xBD, 0x6B, 0x2C, 0x82, 0x42, 0x8D, 0x11,
    0xB8, 0x88, 0x2D, 0x4C, 0x6D, 0x18, 0xCC, 0x79,
    0xE2, 0x70, 0x9F, 0x6B, 0xD4, 0x39, 0x91, 0x35,
    0xFD, 0xDE, 0x14, 0xE6, 0x8F, 0x3A, 0xBC, 0xCE,
])
KEY_XENON = bytes([
    0x0E, 0x50, 0xF4, 0x9F, 0x41, 0x23, 0x17, 0x09, 0x60, 0x38, 0x66, 0x56, 0x22, 0xDD, 0x09, 0x13,
    0x32, 0xA2, 0x09, 0xBA, 0x0A, 0x05, 0xA0, 0x0E, 0x13, 0x77, 0xCE, 0xDB, 0x0A, 0x3C, 0xB1, 0xD3,
])

STREAM_COUNT      = 4
XCHUNK_SIZE       = 0x8000
VANILLA_BUFSIZE   = 0x80000
BLOCK_HASHES      = 200
SHA1_SIZE         = 20
IV_SIZE           = 8
HEADER_DATA_START = 0x138


class HashChain:
    """Per-fastfile IV state: BLOCK_HASHES x STREAM_COUNT x 20-byte table,
    seeded from the zone name, advanced by SHA1 of each decrypted chunk."""
    def __init__(self, zone_name):
        name = zone_name[:31].encode('latin1')
        total = BLOCK_HASHES * STREAM_COUNT * SHA1_SIZE
        buf = bytearray(total)
        off = 0
        for i in range(0, total, 4):
            buf[i:i+4] = bytes([name[off]]) * 4
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


KEY_PC = bytes([
    0x64, 0x1D, 0x8A, 0x2F, 0xE3, 0x1D, 0x3A, 0xA6, 0x36, 0x22, 0xBB, 0xC9, 0xCE, 0x85, 0x87, 0x22,
    0x9D, 0x42, 0xB0, 0xF8, 0xED, 0x9B, 0x92, 0x41, 0x30, 0xBF, 0x88, 0xB6, 0x5E, 0xDC, 0x50, 0xBE,
])


def detect_platform(data):
    """Return (endian_char, key, version, label) from the header."""
    magic = data[0:8]
    assert magic[:4]==b'TAff' and magic[5:]==b'100', f"bad magic {magic!r}"
    ver_be = struct.unpack('>I', data[8:12])[0]
    ver_le = struct.unpack('<I', data[8:12])[0]
    if ver_be == 148:
        return '>', KEY_WIIU, 148, 'WiiU'
    if ver_be == 146:
        return '>', KEY_XENON, 146, 'Xbox360/PS3'   # both BE, v146 (PS3 key may differ)
    if ver_le == 147:
        return '<', KEY_PC, 147, 'PC'
    raise ValueError(f"unknown version be={ver_be} le={ver_le}")


def parse_header(data, endian='>'):
    magic = data[0:8]
    version = struct.unpack(endian + 'I', data[8:12])[0]
    auth = data[12:20]
    flags = struct.unpack(endian + 'I', data[20:24])[0]
    name = data[24:56].split(b'\x00')[0].decode('latin1')
    sig = data[56:312]
    assert magic[:4]==b'TAff' and magic[5:]==b'100', f"bad magic {magic!r}"
    assert auth == b'PHEEBs71', f"bad auth magic {auth!r}"
    return {'magic': magic, 'version': version, 'flags': flags,
            'name': name, 'sig': sig}


def iter_chunks(data, endian='>', start=HEADER_DATA_START):
    """Yield raw encrypted chunk payloads in file order, honoring the
    0x80000 super-block size-field alignment rule."""
    pos = start
    vbuf_off = start  # tracks absolute position mod VANILLA_BUFSIZE
    while True:
        if vbuf_off + 4 > VANILLA_BUFSIZE:
            skip = VANILLA_BUFSIZE - vbuf_off
            pos += skip
            vbuf_off = 0
        vbuf_off = (vbuf_off + 4) % VANILLA_BUFSIZE
        if pos + 4 > len(data):
            return
        size = struct.unpack(endian + 'I', data[pos:pos+4])[0]
        pos += 4
        if size == 0:
            return
        if size > XCHUNK_SIZE:
            raise ValueError(f"chunk size {size:#x} > max at pos {pos-4:#x}")
        chunk = data[pos:pos+size]
        if len(chunk) != size:
            raise ValueError(f"truncated chunk at {pos:#x}")
        pos += size
        vbuf_off = (vbuf_off + size) % VANILLA_BUFSIZE
        yield chunk


def decrypt_ff(data, key, endian='>'):
    hdr = parse_header(data, endian)
    chain = HashChain(hdr['name'])
    out = bytearray()
    n = 0
    for i, enc in enumerate(iter_chunks(data, endian)):
        stream = i % STREAM_COUNT
        iv = chain.iv(stream)
        dec = Salsa20(key, iv).decrypt(enc)
        chain.advance(stream, dec)
        try:
            inflated = zlib.decompress(dec, -15)
        except Exception as e:
            raise RuntimeError(
                f"chunk {i} (stream {stream}) inflate failed: {e}\n"
                f"  iv={iv.hex()} encLen={len(enc)} dec[:16]={dec[:16].hex()}")
        out += inflated
        n += 1
    return hdr, bytes(out), n


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../common_mp.ff"
    data = open(path, "rb").read()
    endian, key, ver, label = detect_platform(data)
    print(f"platform={label} version={ver} endian={'BE' if endian=='>' else 'LE'}")
    hdr, zone, n = decrypt_ff(data, key, endian)
    print(f"name={hdr['name']!r} version={hdr['version']} chunks={n}")
    print(f"decompressed zone size = {len(zone)} bytes")
    print(f"zone[:32] = {zone[:32].hex()}")
    outpath = os.path.splitext(path)[0] + ".zone"
    open(outpath, "wb").write(zone)
    print(f"wrote {outpath}")
