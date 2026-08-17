"""Pure-Python Salsa20/20 stream cipher (no dependencies).

Reference: D. J. Bernstein's Salsa20 spec. Standard 512-bit state, 20-round
variant, 256-bit key, 64-bit nonce. The core is fully inlined and the data XOR
is done as a single big-integer operation, which makes it fast enough to decrypt
whole fastfiles without a C extension.
"""
import struct

_SIGMA = b"expand 32-byte k"
_M = 0xFFFFFFFF


def _core(state):
    """One Salsa20 block: 20 rounds over the 16-word state, inlined."""
    x0, x1, x2, x3, x4, x5, x6, x7, x8, x9, x10, x11, x12, x13, x14, x15 = state
    j0, j1, j2, j3, j4, j5, j6, j7, j8, j9, j10, j11, j12, j13, j14, j15 = state
    for _ in range(10):
        # column rounds
        t = (x0 + x12) & _M; x4 ^= ((t << 7) | (t >> 25)) & _M
        t = (x4 + x0) & _M; x8 ^= ((t << 9) | (t >> 23)) & _M
        t = (x8 + x4) & _M; x12 ^= ((t << 13) | (t >> 19)) & _M
        t = (x12 + x8) & _M; x0 ^= ((t << 18) | (t >> 14)) & _M

        t = (x5 + x1) & _M; x9 ^= ((t << 7) | (t >> 25)) & _M
        t = (x9 + x5) & _M; x13 ^= ((t << 9) | (t >> 23)) & _M
        t = (x13 + x9) & _M; x1 ^= ((t << 13) | (t >> 19)) & _M
        t = (x1 + x13) & _M; x5 ^= ((t << 18) | (t >> 14)) & _M

        t = (x10 + x6) & _M; x14 ^= ((t << 7) | (t >> 25)) & _M
        t = (x14 + x10) & _M; x2 ^= ((t << 9) | (t >> 23)) & _M
        t = (x2 + x14) & _M; x6 ^= ((t << 13) | (t >> 19)) & _M
        t = (x6 + x2) & _M; x10 ^= ((t << 18) | (t >> 14)) & _M

        t = (x15 + x11) & _M; x3 ^= ((t << 7) | (t >> 25)) & _M
        t = (x3 + x15) & _M; x7 ^= ((t << 9) | (t >> 23)) & _M
        t = (x7 + x3) & _M; x11 ^= ((t << 13) | (t >> 19)) & _M
        t = (x11 + x7) & _M; x15 ^= ((t << 18) | (t >> 14)) & _M

        # row rounds
        t = (x0 + x3) & _M; x1 ^= ((t << 7) | (t >> 25)) & _M
        t = (x1 + x0) & _M; x2 ^= ((t << 9) | (t >> 23)) & _M
        t = (x2 + x1) & _M; x3 ^= ((t << 13) | (t >> 19)) & _M
        t = (x3 + x2) & _M; x0 ^= ((t << 18) | (t >> 14)) & _M

        t = (x5 + x4) & _M; x6 ^= ((t << 7) | (t >> 25)) & _M
        t = (x6 + x5) & _M; x7 ^= ((t << 9) | (t >> 23)) & _M
        t = (x7 + x6) & _M; x4 ^= ((t << 13) | (t >> 19)) & _M
        t = (x4 + x7) & _M; x5 ^= ((t << 18) | (t >> 14)) & _M

        t = (x10 + x9) & _M; x11 ^= ((t << 7) | (t >> 25)) & _M
        t = (x11 + x10) & _M; x8 ^= ((t << 9) | (t >> 23)) & _M
        t = (x8 + x11) & _M; x9 ^= ((t << 13) | (t >> 19)) & _M
        t = (x9 + x8) & _M; x10 ^= ((t << 18) | (t >> 14)) & _M

        t = (x15 + x14) & _M; x12 ^= ((t << 7) | (t >> 25)) & _M
        t = (x12 + x15) & _M; x13 ^= ((t << 9) | (t >> 23)) & _M
        t = (x13 + x12) & _M; x14 ^= ((t << 13) | (t >> 19)) & _M
        t = (x14 + x13) & _M; x15 ^= ((t << 18) | (t >> 14)) & _M

    return ((x0 + j0) & _M, (x1 + j1) & _M, (x2 + j2) & _M, (x3 + j3) & _M,
            (x4 + j4) & _M, (x5 + j5) & _M, (x6 + j6) & _M, (x7 + j7) & _M,
            (x8 + j8) & _M, (x9 + j9) & _M, (x10 + j10) & _M, (x11 + j11) & _M,
            (x12 + j12) & _M, (x13 + j13) & _M, (x14 + j14) & _M, (x15 + j15) & _M)


class Salsa20:
    def __init__(self, key: bytes, nonce: bytes, counter: int = 0):
        assert len(key) == 32, "key must be 32 bytes"
        assert len(nonce) == 8, "nonce must be 8 bytes"
        k = struct.unpack('<8I', key)
        c = struct.unpack('<4I', _SIGMA)
        n = struct.unpack('<2I', nonce)
        self._base = [
            c[0], k[0], k[1], k[2],
            k[3], c[1], n[0], n[1],
            0, 0, c[2], k[4],
            k[5], k[6], k[7], c[3],
        ]
        self._counter = counter

    def keystream(self, length):
        base = self._base
        ctr = self._counter
        pack = struct.Struct('<16I').pack
        blocks = []
        produced = 0
        while produced < length:
            base[8] = ctr & _M
            base[9] = (ctr >> 32) & _M
            blocks.append(pack(*_core(base)))
            produced += 64
            ctr += 1
        return b"".join(blocks)[:length]

    def decrypt(self, data: bytes) -> bytes:
        n = len(data)
        ks = self.keystream(n)
        return (int.from_bytes(data, 'little') ^ int.from_bytes(ks, 'little')).to_bytes(n, 'little')

    encrypt = decrypt


if __name__ == "__main__":
    s = Salsa20(b"\x00" * 32, b"\x00" * 8)
    print(s.keystream(8).hex())   # ECRYPT set1 vector start: 9a97f65b9b4c721b
