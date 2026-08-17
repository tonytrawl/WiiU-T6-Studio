#!/usr/bin/env python3
"""
sab_convert.py — Convert PC Black Ops 2 sound banks (.sabs / .sabl) to Wii U format.

PC banks store audio as FLAC (streamed .sabs, format 8) or raw PCM16-LE
(loaded .sabl, format 0). Wii U banks use format 9: Nintendo DSP-ADPCM,
resampled to 2/3 of the source rate (48000 -> 32000 Hz).

Container (identical on both platforms, all little-endian):

  SndAssetBankHeader (0x48 bytes):
    +0x00 u32  magic '2UX#' (0x23585532)
    +0x04 u32  version        = 14
    +0x08 u32  entrySize      = 20
    +0x0C u32  checksumSize   = 16
    +0x10 u32  dependencySize = 64
    +0x14 u32  entryCount
    +0x18 u32  dependencyCount = 8
    +0x1C u32  pad
    +0x20 u64  fileSize
    +0x28 u64  entryOffset     (0x800-aligned, near end of file)
    +0x30 u64  checksumOffset  (0x800-aligned)
    +0x38 u8[16] checksumChecksum = MD5(entryTable || checksumTable)
  then dependencyCount * 64-byte name strings; data starts at 0x800.

  SndAssetBankEntry (20 bytes each, at entryOffset):
    u32 id (SND_HashName of alias name — preserved verbatim from PC)
    u32 size, u32 offset, u32 frameCount (source-rate sample count)
    u8 frameRateIndex, u8 channelCount, u8 looping, u8 format

  checksumTable at checksumOffset: entryCount * MD5(blob bytes).

Wii U format-9 blob:
    u32 BE 0x12345678, u32 0
    per channel: 16 x s16 BE DSP coefficients + 16 zero bytes (pred/hist state)
    DSP-ADPCM frames (8 bytes = 14 samples); stereo interleaves one
    8-byte frame per channel (L,R,L,R,...).

Usage:
  python sab_convert.py <input.sabs/.sabl> [more inputs...] -o <output_dir>
"""
import argparse
import hashlib
import io
import os
import struct
import sys
import time

import numpy as np
from scipy.signal import resample_poly

try:
    from numba import njit
except ImportError:
    print("numba not installed - encoding will be extremely slow. pip install numba")
    def njit(*a, **k):
        def wrap(f):
            return f
        return wrap if not a or callable(a[0]) is False else a[0]

MAGIC = 0x23585532  # '2UX#'
HDR_FMT = '<8I3Q'
SAMPLES_PER_FRAME = 14
BYTES_PER_FRAME = 8

# ---------------------------------------------------------------------------
# DSP-ADPCM encoder (port of the canonical Nintendo dspadpcm algorithm)
# ---------------------------------------------------------------------------

@njit(cache=True)
def _inner_product_merge(vec_out, hb):
    # hb: 28-sample window, hb[14:] is the current frame
    for i in range(3):
        acc = 0.0
        for x in range(14):
            acc -= hb[14 + x - i] * hb[14 + x]
        vec_out[i] = acc


@njit(cache=True)
def _outer_product_merge(mtx, hb):
    for x in range(1, 3):
        for y in range(1, 3):
            acc = 0.0
            for z in range(14):
                acc += hb[14 + z - x] * hb[14 + z - y]
            mtx[x, y] = acc


@njit(cache=True)
def _analyze_ranges(mtx, vec_idxs):
    recips = np.zeros(3)
    for x in range(1, 3):
        val = max(abs(mtx[x, 1]), abs(mtx[x, 2]))
        if val < 2.220446049250313e-16:
            return True
        recips[x] = 1.0 / val
    max_index = 0
    for i in range(1, 3):
        for x in range(1, i):
            tmp = mtx[x, i]
            for y in range(1, x):
                tmp -= mtx[x, y] * mtx[y, i]
            mtx[x, i] = tmp
        val = 0.0
        for x in range(i, 3):
            tmp = mtx[x, i]
            for y in range(1, i):
                tmp -= mtx[x, y] * mtx[y, i]
            mtx[x, i] = tmp
            tmp2 = abs(tmp) * recips[x]
            if tmp2 >= val:
                val = tmp2
                max_index = x
        if max_index != i:
            for y in range(1, 3):
                tmp = mtx[max_index, y]
                mtx[max_index, y] = mtx[i, y]
                mtx[i, y] = tmp
            recips[max_index] = recips[i]
        vec_idxs[i] = max_index
        if mtx[i, i] == 0.0:
            return True
        if i != 2:
            tmp = 1.0 / mtx[i, i]
            for x in range(i + 1, 3):
                mtx[x, i] *= tmp
    mn = 1.0e10
    mx = 0.0
    for i in range(1, 3):
        tmp = abs(mtx[i, i])
        if tmp < mn:
            mn = tmp
        if tmp > mx:
            mx = tmp
    if mn / mx < 1.0e-10:
        return True
    return False


@njit(cache=True)
def _bidirectional_filter(mtx, vec_idxs, vec):
    x = 0
    for i in range(1, 3):
        index = vec_idxs[i]
        tmp = vec[index]
        vec[index] = vec[i]
        if x != 0:
            for y in range(x, i):
                tmp -= vec[y] * mtx[i, y]
        elif tmp != 0.0:
            x = i
        vec[i] = tmp
    for i in range(2, 0, -1):
        tmp = vec[i]
        for y in range(i + 1, 3):
            tmp -= vec[y] * mtx[i, y]
        vec[i] = tmp / mtx[i, i]
    vec[0] = 1.0


@njit(cache=True)
def _quadratic_merge(vec):
    v2 = vec[2]
    tmp = 1.0 - (v2 * v2)
    if tmp == 0.0:
        return True
    v0 = (vec[0] - (v2 * v2)) / tmp
    v1 = (vec[1] - (vec[1] * v2)) / tmp
    vec[0] = v0
    vec[1] = v1
    return abs(v1) > 1.0


@njit(cache=True)
def _finish_record(vec, out):
    for z in range(1, 3):
        if vec[z] >= 1.0:
            vec[z] = 0.9999999999
        elif vec[z] <= -1.0:
            vec[z] = -0.9999999999
    out[0] = 1.0
    out[1] = (vec[2] * vec[1]) + vec[1]
    out[2] = vec[2]


@njit(cache=True)
def _matrix_filter(src, dst):
    mtx = np.zeros((3, 3))
    mtx[2, 0] = 1.0
    for i in range(1, 3):
        mtx[2, i] = -src[i]
    for i in range(2, 0, -1):
        val = 1.0 - (mtx[i, i] * mtx[i, i])
        if val == 0.0:
            val = 1.0e-300  # C computes inf here; keep going like the original
        for y in range(1, i + 1):
            mtx[i - 1, y] = ((mtx[i, i] * mtx[i, y]) + mtx[i, y]) / val
    dst[0] = 1.0
    for i in range(1, 3):
        dst[i] = 0.0
        for y in range(1, i + 1):
            dst[i] += mtx[i, y] * dst[i - y]


@njit(cache=True)
def _merge_finish_record(src, dst):
    val = src[0]
    dst[0] = 1.0
    dst[1] = 0.0
    dst[2] = 0.0
    for i in range(1, 3):
        v2 = 0.0
        for y in range(1, i):
            v2 += dst[y] * src[i - y]
        if val > 0.0:
            dst[i] = -(v2 + src[i]) / val
        else:
            dst[i] = 0.0
        for y in range(1, i):
            dst[y] += dst[i] * dst[i - y]
        val *= 1.0 - (dst[i] * dst[i])


@njit(cache=True)
def _contrast_vectors(s1, s2):
    val = (s2[2] * s2[1] + -s2[1]) / (1.0 - s2[2] * s2[2])
    val1 = (s1[0] * s1[0]) + (s1[1] * s1[1]) + (s1[2] * s1[2])
    val2 = (s1[0] * s1[1]) + (s1[1] * s1[2])
    val3 = s1[0] * s1[2]
    return val1 + (2.0 * val * val2) + (2.0 * (-s2[1] * val + -s2[2]) * val3)


@njit(cache=True)
def _filter_records(vec_best, nexp, records, record_count):
    buffer_list = np.zeros((8, 3))
    buffer1 = np.zeros(8, dtype=np.int64)
    buffer2 = np.zeros(3)
    for _ in range(2):
        for y in range(nexp):
            buffer1[y] = 0
            for i in range(3):
                buffer_list[y, i] = 0.0
        for z in range(record_count):
            index = 0
            value = 1.0e30
            for i in range(nexp):
                temp_val = _contrast_vectors(vec_best[i], records[z])
                if temp_val < value:
                    value = temp_val
                    index = i
            buffer1[index] += 1
            _matrix_filter(records[z], buffer2)
            for i in range(3):
                buffer_list[index, i] += buffer2[i]
        for i in range(nexp):
            if buffer1[i] > 0:
                for y in range(3):
                    buffer_list[i, y] /= buffer1[i]
        for i in range(nexp):
            _merge_finish_record(buffer_list[i], vec_best[i])


@njit(cache=True)
def dsp_correlate_coefs(source):
    """Derive the 8 coefficient pairs for one channel. source: int16 array."""
    samples = source.shape[0]
    num_frames = (samples + 13) // 14
    coefs_out = np.zeros(16, dtype=np.int16)
    if samples == 0:
        return coefs_out

    hb = np.zeros(28)                      # 2-frame sliding window
    records = np.zeros((num_frames + 2, 3))
    record_count = 0
    vec1 = np.zeros(3)
    mtx = np.zeros((3, 3))
    vec_idxs = np.zeros(3, dtype=np.int64)
    vec_best = np.zeros((8, 3))

    pos = 0
    while pos < samples:
        for z in range(14):
            hb[z] = hb[14 + z]
        for z in range(14):
            if pos < samples:
                hb[14 + z] = source[pos]
                pos += 1
            else:
                hb[14 + z] = 0.0
        _inner_product_merge(vec1, hb)
        if abs(vec1[0]) > 10.0:
            _outer_product_merge(mtx, hb)
            if not _analyze_ranges(mtx, vec_idxs):
                _bidirectional_filter(mtx, vec_idxs, vec1)
                if not _quadratic_merge(vec1):
                    _finish_record(vec1, records[record_count])
                    record_count += 1

    if record_count == 0:
        return coefs_out  # silence: all-zero coefs (matches genuine banks)

    vec1[0] = 1.0
    vec1[1] = 0.0
    vec1[2] = 0.0
    tmpv = np.zeros(3)
    for z in range(record_count):
        _matrix_filter(records[z], tmpv)
        for y in range(1, 3):
            vec1[y] += tmpv[y]
    for y in range(1, 3):
        vec1[y] /= record_count
    _merge_finish_record(vec1, vec_best[0])

    nexp = 1
    for w in range(3):
        for i in range(nexp):
            vec_best[nexp + i, 0] = vec_best[i, 0]
            vec_best[nexp + i, 1] = -0.01 + vec_best[i, 1]
            vec_best[nexp + i, 2] = vec_best[i, 2]
        nexp = 1 << (w + 1)
        _filter_records(vec_best, nexp, records, record_count)

    for z in range(8):
        d = -vec_best[z, 1] * 2048.0
        if d > 32767.0:
            d = 32767.0
        elif d < -32768.0:
            d = -32768.0
        coefs_out[z * 2] = np.int16(round(d))
        d = -vec_best[z, 2] * 2048.0
        if d > 32767.0:
            d = 32767.0
        elif d < -32768.0:
            d = -32768.0
        coefs_out[z * 2 + 1] = np.int16(round(d))
    return coefs_out


@njit(cache=True)
def _ctrunc(a, b):
    # C-style integer division (truncate toward zero)
    q = a // b
    if (a % b != 0) and ((a < 0) != (b < 0)):
        q += 1
    return q


@njit(cache=True)
def dsp_encode(source, coefs):
    """Encode one channel to DSP-ADPCM. Returns byte frames (uint8 array)."""
    samples = source.shape[0]
    num_frames = (samples + 13) // 14
    out = np.zeros(num_frames * 8, dtype=np.uint8)
    pcm = np.zeros(16, dtype=np.int64)          # 2 history + 14 samples
    in_samples = np.zeros((8, 16), dtype=np.int64)
    out_samples = np.zeros((8, 14), dtype=np.int64)
    scale = np.zeros(8, dtype=np.int64)
    dist_accum = np.zeros(8)
    c = coefs.astype(np.int64)

    hist1 = 0
    hist2 = 0
    for fr in range(num_frames):
        base = fr * 14
        n = min(14, samples - base)
        pcm[0] = hist2
        pcm[1] = hist1
        for s in range(14):
            pcm[2 + s] = source[base + s] if s < n else 0

        for i in range(8):
            c1 = c[i * 2]
            c2 = c[i * 2 + 1]
            in_samples[i, 0] = pcm[0]
            in_samples[i, 1] = pcm[1]
            distance = 0
            for s in range(14):
                v1 = _ctrunc(pcm[s] * c2 + pcm[s + 1] * c1, 2048)
                in_samples[i, s + 2] = v1
                v2 = pcm[s + 2] - v1
                v3 = min(max(v2, -32768), 32767)
                if abs(v3) > abs(distance):
                    distance = v3
            sc = 0
            while sc <= 12 and (distance > 7 or distance < -8):
                sc += 1
                distance = _ctrunc(distance, 2)
            sc = -1 if sc <= 1 else sc - 2
            while True:
                sc += 1
                dist_accum[i] = 0.0
                index = 0
                for s in range(14):
                    v1 = in_samples[i, s] * c2 + in_samples[i, s + 1] * c1
                    v2 = _ctrunc((pcm[s + 2] << 11) - v1, 2048)
                    fv = v2 / (1 << sc)
                    if v2 > 0:
                        v3 = int(fv + 0.4999999)
                    else:
                        v3 = int(fv - 0.4999999)
                    if v3 < -8:
                        d = -8 - v3
                        if index < d:
                            index = d
                        v3 = -8
                    elif v3 > 7:
                        d = v3 - 7
                        if index < d:
                            index = d
                        v3 = 7
                    out_samples[i, s] = v3
                    v1 = (v1 + ((v3 * (1 << sc)) << 11) + 1024) >> 11
                    v2c = min(max(v1, -32768), 32767)
                    in_samples[i, s + 2] = v2c
                    dv = pcm[s + 2] - v2c
                    dist_accum[i] += dv * float(dv)
                x = index + 8
                while x > 256:
                    sc += 1
                    if sc >= 12:
                        sc = 11
                    x >>= 1
                if not (sc < 12 and index > 1):
                    break
            scale[i] = sc

        best = 0
        mn = 1.0e300
        for i in range(8):
            if dist_accum[i] < mn:
                mn = dist_accum[i]
                best = i
        hist2 = in_samples[best, 14]
        hist1 = in_samples[best, 15]

        o = fr * 8
        out[o] = np.uint8(((best << 4) | (scale[best] & 0xF)) & 0xFF)
        for y in range(7):
            hi = out_samples[best, y * 2] & 0xF
            lo = out_samples[best, y * 2 + 1] & 0xF
            out[o + 1 + y] = np.uint8((hi << 4) | lo)
    return out


# ---------------------------------------------------------------------------
# SAB container
# ---------------------------------------------------------------------------

class SabEntry:
    __slots__ = ('id', 'size', 'offset', 'frames', 'rate_idx', 'channels',
                 'looping', 'fmt')

    def __init__(self, eid, size, offset, frames, rate_idx, channels, looping, fmt):
        self.id = eid
        self.size = size
        self.offset = offset
        self.frames = frames
        self.rate_idx = rate_idx
        self.channels = channels
        self.looping = looping
        self.fmt = fmt


class SabFile:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = f.read()
        d = self.data
        (magic, self.version, self.entry_size, self.checksum_size,
         self.dep_size, entry_count, self.dep_count, _pad,
         self.file_size, self.entry_offset, self.checksum_offset) = \
            struct.unpack_from(HDR_FMT, d, 0)
        if magic != MAGIC:
            raise ValueError(f'{path}: not a 2UX# sound bank')
        self.dep_blob = d[0x48:0x48 + self.dep_count * self.dep_size]
        self.entries = []
        for i in range(entry_count):
            o = self.entry_offset + i * self.entry_size
            eid, size, off, fc = struct.unpack_from('<4I', d, o)
            fri, ch, loop, fmt = struct.unpack_from('<4B', d, o + 16)
            self.entries.append(SabEntry(eid, size, off, fc, fri, ch, loop, fmt))

    def blob(self, e):
        return self.data[e.offset:e.offset + e.size]


def decode_entry_pcm(sab, e):
    """Decode a PC entry to int16 ndarray of shape (samples, channels)."""
    blob = sab.blob(e)
    if e.fmt == 0:      # PCM16 LE
        pcm = np.frombuffer(blob, dtype='<i2')
        pcm = pcm[:(len(pcm) // e.channels) * e.channels]
        return pcm.reshape(-1, e.channels)
    if e.fmt == 8:      # FLAC
        import soundfile as sf
        pcm, _sr = sf.read(io.BytesIO(blob), dtype='int16', always_2d=True)
        return pcm
    raise ValueError(f'unsupported source format {e.fmt} (entry 0x{e.id:08x})')


def encode_entry_wiiu(pcm, streamed=True):
    """PCM (samples, ch) at source rate -> Wii U format-9 blob bytes.

    ⚠ THE LAYOUT COMES FROM core.sab.blob_chunks, NOT FROM A SECOND COPY HERE. Writer and reader
    disagreeing about a container is the defect class that produced this fix in the first place;
    one definition means a change to the layout cannot land on only one side.

    ⚠ WHAT THIS USED TO WRITE WAS WRONG IN THREE WAYS, measured against 12,016 retail entries:
    a 8+48*channels header (56 mono / 104 stereo) where retail always has 160; no second
    coefficient slot on a mono blob, where retail always reserves one; and a one-frame-per-channel
    interleave where a streamed bank uses 0x10000-byte blocks. Any bank this tool wrote before
    this fix therefore has a malformed blob -- harmless-looking for mono (104 bytes of header
    decoded as ~5.7 ms of noise at the head) but wholly wrong for multi-block stereo.
    """
    from core import sab as SAB

    channels = pcm.shape[1]
    if channels > 2:
        raise ValueError('format-9 blobs hold at most 2 channels; got %d. The header reserves '
                         'exactly two coefficient slots and no 3-channel retail blob exists to '
                         'derive the layout from.' % channels)
    # Wii U banks store audio resampled to 2/3 of the source rate
    res = resample_poly(pcm.astype(np.float64), 2, 3, axis=0)
    res = np.clip(np.round(res), -32768, 32767).astype(np.int16)

    coef_slots, streams = [], []
    for ch in range(channels):
        mono = np.ascontiguousarray(res[:, ch])
        coefs = dsp_correlate_coefs(mono)
        adpcm = dsp_encode(mono, coefs)
        coef_slots.append(struct.pack('>16h', *coefs) + b'\x00' * 16)
        streams.append(bytes(adpcm))
    while len(coef_slots) < 2:
        coef_slots.append(b'\x00' * 48)          # retail reserves slot 1 even on a mono blob

    head = (struct.pack('>I', 0x12345678) + b'\x00' * 4
            + coef_slots[0] + coef_slots[1]
            + b'\x00' * 56)                      # zero on 12,016/12,016 retail entries
    assert len(head) == SAB.BLOB_HEADER, len(head)

    per = (min(len(s) for s in streams)) & ~7    # channels are equal length in every retail entry
    streams = [s[:per] for s in streams]
    size = SAB.BLOB_HEADER + channels * per
    chunks, _per = SAB.blob_chunks(size, channels, streamed)

    buf = bytearray(size)
    buf[:SAB.BLOB_HEADER] = head
    for c, ch_chunks in enumerate(chunks):
        src, o = streams[c], 0
        for off, n in ch_chunks:
            buf[off:off + n] = src[o:o + n]
            o += n
    return bytes(buf)


def align(v, a):
    return (v + a - 1) & ~(a - 1)


def convert_bank(in_path, out_path, verbose=True):
    sab = SabFile(in_path)
    # ⚠ THE CONTAINER PICKS THE LAYOUT AND THERE IS NO IN-BAND FLAG (core.sab.blob_chunks): a
    # `.sabs` (streamed) bank is 0x10000-block-interleaved, a `.sabl` (loaded) bank is FLAT. This
    # used to call encode_entry_wiiu() with its `streamed=True` default for BOTH extensions, so a
    # stereo .sabl entry longer than one block (per > 0xFFB0; measured: 3/51 in mpl_nuketown_2020,
    # 1/46 in mpl_dig) was written interleaved where the console reads it flat. Same rule as the
    # reader (core.sab.SoundBank.decode): the extension is the discriminator.
    streamed = not str(in_path).lower().endswith('.sabl')
    t0 = time.time()
    blobs = []
    for i, e in enumerate(sab.entries):
        pcm = decode_entry_pcm(sab, e)
        if pcm.shape[1] != e.channels:
            raise ValueError(f'channel mismatch entry 0x{e.id:08x}')
        blob = encode_entry_wiiu(pcm, streamed=streamed)
        blobs.append(blob)
        if verbose:
            print(f'  [{i + 1}/{len(sab.entries)}] 0x{e.id:08x} '
                  f'{pcm.shape[0]} samp x{e.channels}ch -> {len(blob)} B '
                  f'({time.time() - t0:.1f}s)')

    # layout
    data_start = 0x800
    offsets = []
    pos = data_start
    for b in blobs:
        offsets.append(pos)
        pos += align(len(b), 8)
    entry_offset = align(pos, 0x800)
    n = len(sab.entries)
    checksum_offset = align(entry_offset + n * 20, 0x800)
    file_size = align(checksum_offset + n * 16, 0x800)

    entry_table = bytearray()
    checksum_table = bytearray()
    for e, blob, off in zip(sab.entries, blobs, offsets):
        entry_table += struct.pack('<4I4B', e.id, len(blob), off, e.frames,
                                   e.rate_idx, e.channels, e.looping, 9)
        checksum_table += hashlib.md5(blob).digest()
    hdr_sum = hashlib.md5(bytes(entry_table) + bytes(checksum_table)).digest()

    out = bytearray(file_size)
    struct.pack_into(HDR_FMT, out, 0, MAGIC, 14, 20, 16, 64, n, sab.dep_count, 0,
                     file_size, entry_offset, checksum_offset)
    out[0x38:0x48] = hdr_sum
    out[0x48:0x48 + len(sab.dep_blob)] = sab.dep_blob
    for blob, off in zip(blobs, offsets):
        out[off:off + len(blob)] = blob
    out[entry_offset:entry_offset + len(entry_table)] = entry_table
    out[checksum_offset:checksum_offset + len(checksum_table)] = checksum_table

    with open(out_path, 'wb') as f:
        f.write(out)
    if verbose:
        print(f'  wrote {out_path} ({file_size} B, {n} entries, '
              f'{time.time() - t0:.1f}s)')


def main():
    ap = argparse.ArgumentParser(description='Convert PC BO2 .sabs/.sabl to Wii U format')
    ap.add_argument('inputs', nargs='+', help='PC .sabs/.sabl files')
    ap.add_argument('-o', '--out-dir', required=True, help='output directory')
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    for p in args.inputs:
        out = os.path.join(args.out_dir, os.path.basename(p))
        print(f'converting {p}')
        convert_bank(p, out)


if __name__ == '__main__':
    main()
