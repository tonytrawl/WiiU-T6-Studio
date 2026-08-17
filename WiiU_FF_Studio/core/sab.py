"""core.sab -- read, preview and edit T6 sound banks (.sabs / .sabl).

CONTAINER (from WiiU_FF_Studio/SAB_FORMAT_NOTES.md, derived by diffing PC and Wii U banks)
------------------------------------------------------------------------------------------
Unlike fastfiles, sound banks are **little-endian on every platform**, including Wii U.

    header 0x48 B
      +0x00 u32   magic '2UX#'      +0x04 u32 version = 14
      +0x08 u32   entrySize = 20    +0x0C u32 checksumSize = 16
      +0x10 u32   dependencySize=64 +0x14 u32 entryCount
      +0x18 u32   dependencyCount   +0x1C u32 pad
      +0x20 u64   fileSize
      +0x28 u64   entryOffset       (0x800-aligned, table sits near the END)
      +0x30 u64   checksumOffset    (0x800-aligned)
      +0x38 u8[16] MD5(entryTable ++ checksumTable)
    +0x48  dependencyCount x 64-byte C-string bank names
    0x800  packed audio blobs, 8-byte aligned
    entry (20 B): id u32, size u32, offset u32, frameCount u32,
                  rateIdx u8, channels u8, looping u8, format u8
    checksum table: entryCount x MD5(blob)

AUDIO
-----
Wii U banks are format 9 = Nintendo DSP-ADPCM, always rateIdx 6. The stored audio is
resampled to **2/3 of the source rate (48000 -> 32000 Hz)** while `frameCount` keeps the
ORIGINAL 48 kHz sample count -- so frameCount is not the length of the stored stream, and
duration must be computed from the 48 kHz count, not from the decoded sample count.

    blob:  +0 u32 BE 0x12345678
           +4 u32    usually 0
           +8 per channel 48 B: s16 BE coef[16] then 16 zero bytes
           then DSP-ADPCM frames, 8 B = 14 samples; stereo interleaves one frame per channel

NAMES ARE NOT IN THE BANK -- BUT THEY ARE RECOVERABLE
-----------------------------------------------------
An entry identifies itself by a u32 id and nothing else; the bank stores no names. An earlier
attempt to recover them by guessing the hash function correctly refused to ship (187,980
candidate strings x 8 hash variants -> 0 hits on 357 ids).

The names live in the SndBank asset inside the fastfiles, which carries per alias BOTH the id
the bank is keyed by (`assetId` at SndAlias+16) AND the source filename -- so no hash function
is needed. `core.sab_names` harvests that into a cache; `attach_shared_names()` applies it.
Measured from common_mp.ff alone: 91/104 (87.5%) of mpl_common.all.sabs entries named.

This is OPTIONAL ENRICHMENT, not a dependency: with no fastfile present the editor lists entries
by id exactly as before, and every other operation is unaffected. `load_names()` still accepts a
hand-made JSON dictionary.
"""
import hashlib
import io
import json
import os
import struct
import wave

import numpy as np

from . import paths  # noqa: F401

MAGIC = 0x58553223            # '#2UX' little-endian read of b'2UX#'
HDR_FMT = '<8I3Q'
HDR_SIZE = 0x48
ENTRY_SIZE = 20
CHECKSUM_SIZE = 16
DEP_SIZE = 64
DATA_START = 0x800
ALIGN_BLOB = 8
ALIGN_TABLE = 0x800

FMT_PCM16 = 0
FMT_FLAC = 8
FMT_DSPADPCM = 9

# rateIdx -> Hz. Only 6 is ever observed in retail banks, on either platform.
RATE_TABLE = {0: 8000, 1: 12000, 2: 16000, 3: 22050, 4: 24000, 5: 32000,
              6: 48000, 7: 44100, 8: 96000}

# Wii U stores audio at 2/3 of the nominal rate (48000 -> 32000).
WIIU_RATE_NUM, WIIU_RATE_DEN = 2, 3


class SabError(Exception):
    pass


# --------------------------------------------------------------------------- codec

def _signed_nibbles(frames):
    """(n,8) uint8 frames -> (n,14) int8 sample nibbles, high nibble first."""
    body = frames[:, 1:]                                  # (n, 7)
    hi = (body >> 4).astype(np.int16)
    lo = (body & 0xF).astype(np.int16)
    nib = np.empty((frames.shape[0], 14), np.int16)
    nib[:, 0::2] = hi
    nib[:, 1::2] = lo
    nib[nib > 7] -= 16                                    # sign-extend 4 bits
    return nib


def decode_dsp_adpcm(data, coefs, sample_limit=None):
    """One channel of Nintendo DSP-ADPCM -> int16 PCM.

    Mirrors `sab_convert.dsp_encode` exactly, which is the inverse we already ship:
        header byte = (coefIndex << 4) | scale
        sample = clamp((hist2*c2 + hist1*c1 + ((nib << scale) << 11) + 1024) >> 11)

    The filter is a 2-tap IIR, so it cannot be vectorised across samples; the per-frame setup
    is vectorised and the inner loop is kept tight. `sample_limit` stops early, which is what
    makes previewing a several-minute music stream instant.
    """
    n_frames = len(data) // 8
    if n_frames == 0:
        return np.zeros(0, np.int16)
    if sample_limit is not None:
        n_frames = min(n_frames, (int(sample_limit) + 13) // 14)
    frames = np.frombuffer(data[:n_frames * 8], np.uint8).reshape(n_frames, 8)

    ps = frames[:, 0]
    scale = (ps & 0xF).astype(np.int32)
    cidx = (ps >> 4).astype(np.int32)
    nib = _signed_nibbles(frames).astype(np.int32)

    c = np.asarray(coefs, np.int32)
    if cidx.max() * 2 + 1 >= len(c):
        raise SabError('coefficient index %d out of range for %d coefficients'
                       % (cidx.max(), len(c)))
    c1 = c[cidx * 2]
    c2 = c[cidx * 2 + 1]
    # Pre-scale the residual: ((nib << scale) << 11) == nib * 2**(scale+11)
    resid = (nib.astype(np.int64) << (scale[:, None] + 11)) + 1024

    out = np.empty(n_frames * 14, np.int16)
    h1 = 0
    h2 = 0
    k = 0
    for f in range(n_frames):
        a1 = int(c1[f])
        a2 = int(c2[f])
        rf = resid[f]
        for s in range(14):
            v = (h2 * a2 + h1 * a1 + int(rf[s])) >> 11
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
            out[k] = v
            k += 1
            h2 = h1
            h1 = v
    return out


#: A format-9 blob is a FIXED 160-byte header followed by channels*per bytes of audio.
#
#   +0x00 u32 BE 0x12345678
#   +0x04 u32    aux (zero on 11,864 of 12,016 retail entries; unidentified, does not select
#                     a layout -- non-zero values appear across both extensions and channel counts)
#   +0x08 48 B   channel 0 coefficients: s16 BE coef[16] then 16 zero bytes
#   +0x38 48 B   channel 1 coefficients -- ALWAYS PRESENT, all zero in a mono blob
#   +0x68 56 B   zero on 12,016 / 12,016 retail entries; purpose unknown, keep emitting zeros
#   +0xA0        audio
#
# ⚠ 160 IS NOT 8 + 48*channels. Treating the header as channel-dependent puts the audio base
# 56 bytes early on mono and lands stereo on the wrong grid entirely.
BLOB_HEADER = 160
INTERLEAVE_BLOCK = 0x10000


def blob_chunks(size, channels, streamed):
    """-> ([[(blob_offset, nbytes), ...] per channel], bytes_per_channel).

    ⚠ THE PREVIOUS IMPLEMENTATION DE-INTERLEAVED ONE 8-BYTE FRAME PER CHANNEL. That is simply not
    the layout: a STREAMED multi-channel payload is interleaved in 0x10000-byte BLOCKS, so the old
    reader produced, for each "channel", a mangling of both real channels advancing at roughly
    twice real time. Measured against the retail PC masters, the old split correlates 0.027; this
    one correlates 0.998-1.000.

    ⚠ THE CONTAINER PICKS THE LAYOUT, AND THERE IS NO IN-BAND FLAG. `.sabs` (streamed) banks are
    block-interleaved; `.sabl` (loaded) banks are FLAT -- each channel stored end to end. The bank
    headers are byte-identical across all 53 .sabs and 51 .sabl files on disk, so the extension is
    the only discriminator. Measured 178/178 on every entry long enough for the two layouts to
    differ, zero exceptions. It only matters when per > 0xFFB0; below that there is one block and
    the two layouts coincide, which is why 67.6% of stereo entries never exposed the bug.

    ⚠ WHY BLOCK 0 IS 80 BYTES SHORT. The 160-byte header is charged equally to the channels' first
    blocks, so stereo block 0 spans 0xFFB0 and EVERY later boundary lands on an exact multiple of
    0x10000 inside the blob. That deficit is the whole reason a naive `2*nfull*B + rem` boundary
    misses the final block by 56 bytes on multi-block entries -- 32.4% of retail stereo.
    """
    per = ((size - BLOB_HEADER) // channels) & ~7
    if channels == 1 or not streamed:
        return [[(BLOB_HEADER + c * per, per)] for c in range(channels)], per
    anchor = BLOB_HEADER // channels                 # 80 for stereo: the shared header deficit
    out = [[] for _ in range(channels)]
    pos, off, k = BLOB_HEADER, 0, 0
    while off < per:
        end = min((k + 1) * INTERLEAVE_BLOCK - anchor, per)
        n = end - off
        for c in range(channels):
            out[c].append((pos, n))
            pos += n
        off, k = end, k + 1
    return out, per


def parse_blob(blob, channels, streamed=True):
    """Format-9 blob -> (coefs per channel, adpcm stream per channel).

    `streamed` selects the block-interleaved (.sabs) or flat (.sabl) layout; see blob_chunks.
    """
    if len(blob) < 8:
        raise SabError('blob too small (%d bytes)' % len(blob))
    magic = struct.unpack_from('>I', blob, 0)[0]
    if magic != 0x12345678:
        raise SabError('format-9 blob magic is %#010x, expected 0x12345678' % magic)
    coefs = []
    o = 8
    for _ch in range(channels):
        if o + 48 > len(blob):
            raise SabError('blob truncated inside the channel headers')
        coefs.append(np.array(struct.unpack_from('>16h', blob, o), np.int32))
        o += 48
    if len(blob) < BLOB_HEADER:
        raise SabError('blob truncated inside the %d-byte header' % BLOB_HEADER)
    chunks, _per = blob_chunks(len(blob), channels, streamed)
    return coefs, [b''.join(bytes(blob[a:a + n]) for a, n in ch) for ch in chunks]


# --------------------------------------------------------------------------- model

class Entry(object):
    __slots__ = ('index', 'id', 'size', 'offset', 'frames', 'rate_idx',
                 'channels', 'looping', 'fmt', '_blob', 'note', 'stored_checksum',
                 'src_offset', 'src_size')

    def __init__(self, index, eid, size, offset, frames, rate_idx, channels, looping, fmt):
        self.stored_checksum = None   # retail's own MD5; preserved unless this entry is edited
        self.index = index
        self.id = eid
        self.size = size
        self.offset = offset
        self.frames = frames
        self.rate_idx = rate_idx
        self.channels = channels
        self.looping = looping
        self.fmt = fmt
        self._blob = None          # set when the entry has been replaced/added
        # WHERE THIS ENTRY'S BYTES LIVE IN THE FILE WE OPENED. `offset`/`size` describe the
        # layout being BUILT and are rewritten on every build(); these two never move, so an
        # unedited entry can always be re-read no matter how many times we rebuild.
        self.src_offset = offset
        self.src_size = size
        self.note = ''

    @property
    def label(self):
        return '0x%08X' % self.id

    @property
    def nominal_rate(self):
        """The rate frameCount is expressed in (48000 in every retail bank)."""
        return RATE_TABLE.get(self.rate_idx, 48000)

    @property
    def stored_rate(self):
        """The rate the audio is actually stored at. Wii U resamples to 2/3."""
        if self.fmt == FMT_DSPADPCM:
            return self.nominal_rate * WIIU_RATE_NUM // WIIU_RATE_DEN
        return self.nominal_rate

    @property
    def duration(self):
        """Seconds, from frameCount at its NOMINAL rate -- not from decoded length."""
        r = self.nominal_rate
        return (self.frames / float(r)) if r else 0.0

    @property
    def format_name(self):
        return {FMT_PCM16: 'PCM16', FMT_FLAC: 'FLAC', FMT_DSPADPCM: 'DSP-ADPCM'}.get(
            self.fmt, 'fmt %d' % self.fmt)

    def __repr__(self):
        return '<Entry %s %s %dch %.2fs %s>' % (self.label, self.format_name,
                                                self.channels, self.duration,
                                                'loop' if self.looping else '')


class SoundBank(object):
    """An open bank. Edits are staged in memory; `save()` rebuilds the container."""

    def __init__(self, data, path=None):
        self.raw = data
        self.path = path
        self.dirty = False
        self.names = {}

        if len(data) < HDR_SIZE:
            raise SabError('file is too small to be a sound bank')
        (magic, self.version, self.entry_size, self.checksum_size, self.dep_size,
         entry_count, self.dep_count, self._pad, self.file_size,
         self.entry_offset, self.checksum_offset) = struct.unpack_from(HDR_FMT, data, 0)
        if data[:4] != b'2UX#':
            raise SabError('not a sound bank: magic %r (expected %r)' % (data[:4], b'2UX#'))
        if self.entry_size != ENTRY_SIZE:
            raise SabError('unexpected entry size %d' % self.entry_size)

        self.dependencies = []
        for i in range(self.dep_count):
            s = data[HDR_SIZE + i * self.dep_size:HDR_SIZE + (i + 1) * self.dep_size]
            self.dependencies.append(s.split(b'\x00')[0].decode('latin-1'))

        self.entries = []
        for i in range(entry_count):
            o = self.entry_offset + i * ENTRY_SIZE
            if o + ENTRY_SIZE > len(data):
                raise SabError('entry table runs past the end of the file')
            eid, size, off, fc = struct.unpack_from('<4I', data, o)
            fri, ch, loop, fmt = struct.unpack_from('<4B', data, o + 16)
            self.entries.append(Entry(i, eid, size, off, fc, fri, ch, loop, fmt))

        self.checksums = [bytes(data[self.checksum_offset + i * CHECKSUM_SIZE:
                                     self.checksum_offset + (i + 1) * CHECKSUM_SIZE])
                          for i in range(entry_count)]
        self.header_checksum = bytes(data[0x38:0x48])
        # True only for a bank this tool authored from nothing, where no .ff has yet agreed on
        # a header token. Set by from_scratch(); an opened retail bank always preserves.
        self._header_token_is_ours = False
        for i, e in enumerate(self.entries):
            if i < len(self.checksums):
                e.stored_checksum = self.checksums[i]

    # ---- construction ----------------------------------------------------------
    @classmethod
    def open(cls, path):
        with open(path, 'rb') as f:
            return cls(f.read(), path)

    def load_names(self, path):
        """Attach an optional {id: name} dictionary. See the module docstring: names are NOT
        recoverable from the bank itself, so this is the only way to get them."""
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        out = {}
        for k, v in raw.items():
            out[int(k, 16) if isinstance(k, str) and k.lower().startswith('0x')
                else int(k)] = v
        self.names = out
        return len(out)

    def attach_shared_names(self, rebuild=False, progress=None):
        """Apply the harvested id -> name dictionary. Returns (named, total).

        Optional: with no fastfiles reachable this is a no-op and entries keep their hex ids.
        """
        from . import sab_names as NN
        try:
            shared = NN.load_shared(rebuild=rebuild, progress=progress,
                                    build_if_missing=rebuild)
        except Exception:
            return 0, len(self.entries)
        for k, v in shared.items():
            self.names.setdefault(k, v)
        return NN.match_rate(self.names, self)

    def name_of(self, entry):
        """Display name: the harvested source filename when known, else the hex id.

        The harvested names are full source paths (`raw\\sound\\amb\\...\\foo.LN65.wiiu.snd`),
        so the leaf is shown -- the full path is still available in the detail pane.
        """
        n = self.names.get(entry.id)
        if not n:
            return entry.label
        leaf = n.replace('\\', '/').rsplit('/', 1)[-1]
        return leaf or n

    # ---- reading ---------------------------------------------------------------
    def blob(self, entry):
        """This entry's payload bytes.

        Unedited entries are read at their SOURCE offset, not their current one: build() rewrites
        `offset`/`size` as it lays the file out, so reading at the live offset made build()
        non-idempotent -- a second build resolved new offsets against the original bytes and
        silently corrupted every untouched entry after the first edit.
        """
        if entry._blob is not None:
            return entry._blob
        return self.raw[entry.src_offset:entry.src_offset + entry.src_size]

    def verify_checksum(self, entry):
        """Stored MD5 vs the blob. Returns True/False, or None when there is no stored value.

        ⚠ False is NOT automatically a defect. ~3% of RETAIL entries (measured: 380 of 12,667,
        almost all in .sabl banks) ship with a stored MD5 that does not match their own blob.
        Those files still re-emit byte-exact because the checksum is preserved rather than
        recomputed -- see `build()`.
        """
        if entry.stored_checksum is None:
            return None
        return hashlib.md5(self.blob(entry)).digest() == entry.stored_checksum

    def decode(self, entry, seconds=None):
        """-> (pcm int16 (n, channels), sample_rate). `seconds` decodes only a prefix."""
        blob = self.blob(entry)
        if entry.fmt == FMT_PCM16:
            pcm = np.frombuffer(blob, '<i2')
            pcm = pcm[:(len(pcm) // entry.channels) * entry.channels]
            return pcm.reshape(-1, entry.channels), entry.nominal_rate
        if entry.fmt == FMT_FLAC:
            import soundfile as sf
            pcm, sr = sf.read(io.BytesIO(blob), dtype='int16', always_2d=True)
            return pcm, sr
        if entry.fmt != FMT_DSPADPCM:
            raise SabError('no decoder for format %d' % entry.fmt)

        # The BANK's extension decides the interleave, and there is no in-band flag -- see
        # blob_chunks. Default to streamed when the path is unknown: .sabs is the majority and
        # a single-block entry decodes identically either way.
        streamed = not str(getattr(self, 'path', '') or '').lower().endswith('.sabl')
        coefs, streams = parse_blob(blob, entry.channels, streamed)
        rate = entry.stored_rate
        limit = int(seconds * rate) if seconds else None
        chans = [decode_dsp_adpcm(s, c, limit) for s, c in zip(streams, coefs)]
        n = min(len(c) for c in chans)
        return np.stack([c[:n] for c in chans], axis=1), rate

    def to_wav(self, entry, seconds=None):
        """-> WAV file bytes, ready for playback or export."""
        pcm, rate = self.decode(entry, seconds)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(pcm.shape[1])
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.astype('<i2').tobytes())
        return buf.getvalue()

    def export(self, entry, path, seconds=None):
        with open(path, 'wb') as f:
            f.write(self.to_wav(entry, seconds))
        return path

    # ---- editing ---------------------------------------------------------------
    def _encode_wav(self, path_or_bytes, looping=False):
        """WAV (any rate/width supported by `wave`) -> (blob, frameCount, channels).

        The source is resampled to the Wii U's 2/3 convention by the shipped encoder, so the
        caller supplies ordinary 48 kHz audio and gets a correct console blob back.
        """
        import sab_convert as SC
        data = (open(path_or_bytes, 'rb').read()
                if isinstance(path_or_bytes, str) else path_or_bytes)
        with wave.open(io.BytesIO(data), 'rb') as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            frames = w.readframes(n)
        if sw != 2:
            raise SabError('only 16-bit PCM WAV is supported (this file is %d-bit)' % (sw * 8))
        pcm = np.frombuffer(frames, '<i2').reshape(-1, ch)
        if sr != 48000:
            # Bring it to the 48 kHz the bank's frameCount is expressed in, so duration and
            # pitch stay correct; the encoder then applies the console's own 2/3 step.
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(48000, sr)
            pcm = resample_poly(pcm.astype(np.float64), 48000 // g, sr // g, axis=0)
            pcm = np.clip(np.round(pcm), -32768, 32767).astype(np.int16)
        # Same discriminator as decode(): the BANK's extension picks block-interleaved (.sabs) vs
        # FLAT (.sabl). Encoding with the streamed default into a .sabl bank wrote multi-block stereo
        # entries in a layout the console does not read for loaded banks.
        streamed = not str(getattr(self, 'path', '') or '').lower().endswith('.sabl')
        blob = SC.encode_entry_wiiu(pcm, streamed=streamed)
        return blob, len(pcm), pcm.shape[1]

    def replace(self, entry, wav_path_or_bytes):
        """Replace an entry's audio, keeping its id so existing aliases still bind."""
        blob, frame_count, channels = self._encode_wav(wav_path_or_bytes)
        entry._blob = blob
        entry.size = len(blob)
        entry.frames = frame_count
        entry.channels = channels
        entry.fmt = FMT_DSPADPCM
        entry.rate_idx = 6
        entry.stored_checksum = None      # edited: its checksum must now be recomputed
        entry.note = 'replaced'
        self.dirty = True
        return entry

    def add(self, entry_id, wav_path_or_bytes, looping=False):
        """Add a brand new entry. `entry_id` is the SND_HashName the alias will look up."""
        if any(e.id == entry_id for e in self.entries):
            raise SabError('id 0x%08X is already in this bank -- use replace()' % entry_id)
        blob, frame_count, channels = self._encode_wav(wav_path_or_bytes)
        e = Entry(len(self.entries), entry_id, len(blob), 0, frame_count,
                  6, channels, 1 if looping else 0, FMT_DSPADPCM)
        e._blob = blob
        e.stored_checksum = None
        e.note = 'added'
        self.entries.append(e)
        self.dirty = True
        return e

    def delete(self, entry):
        self.entries.remove(entry)
        for i, e in enumerate(self.entries):
            e.index = i
        self.dirty = True

    # ---- writing ---------------------------------------------------------------
    def build(self):
        """Rebuild the whole container. Byte-exact against the input when nothing was edited."""
        out = bytearray(DATA_START)
        out[0:4] = b'2UX#'
        for i, dep in enumerate(self.dependencies):
            o = HDR_SIZE + i * DEP_SIZE
            out[o:o + DEP_SIZE] = dep.encode('latin-1').ljust(DEP_SIZE, b'\x00')

        # ⚠ READ EVERY PAYLOAD BEFORE MOVING ANYTHING. `blob()` locates an unedited entry with
        # `raw[entry.offset : offset+size]`, and the layout loop below OVERWRITES entry.offset.
        # Reading and relocating in one pass therefore made build() non-idempotent: a second
        # build in the same session resolved the new offsets against the ORIGINAL raw bytes and
        # silently corrupted every untouched entry after the first edit. Measured before the fix:
        # build() twice on one session produced different bytes. Snapshotting first makes the
        # operation a pure function of the entry list, so build() can be called any number of
        # times -- which the GUI does whenever the user saves twice.
        payloads = [self.blob(e) for e in self.entries]

        checksums = []
        for e, blob in zip(self.entries, payloads):
            pad = -len(out) % ALIGN_BLOB
            if pad:
                out += b'\x00' * pad
            e.offset = len(out)
            e.size = len(blob)
            out += blob
            # ⚠ PRESERVE, DO NOT RECOMPUTE, for entries we did not touch.
            # Measured: ~3% of retail entries (380 of 12,667, concentrated in .sabl) carry a
            # stored MD5 that does NOT equal MD5(blob), even though the blob sits exactly at the
            # declared offset and size and the file re-emits byte-exact otherwise. Whatever
            # retail hashed there, we do not know it -- so "correcting" it would corrupt a
            # faithful round-trip of a file we were only meant to read. Recompute only where we
            # actually replaced the audio.
            if e._blob is None and e.stored_checksum is not None:
                checksums.append(e.stored_checksum)
            else:
                checksums.append(hashlib.md5(blob).digest())

        pad = -len(out) % ALIGN_TABLE
        if pad:
            out += b'\x00' * pad
        entry_offset = len(out)
        entry_table = bytearray()
        for e in self.entries:
            entry_table += struct.pack('<4I', e.id, e.size, e.offset, e.frames)
            entry_table += struct.pack('<4B', e.rate_idx, e.channels, e.looping, e.fmt)
        out += entry_table

        pad = -len(out) % ALIGN_TABLE
        if pad:
            out += b'\x00' * pad
        checksum_offset = len(out)
        checksum_table = b''.join(checksums)
        out += checksum_table

        # The file itself is padded to 0x800. Measured, not assumed: without this every retail
        # bank re-emitted 496-1248 bytes short, and every real fileSize is an exact multiple
        # (e.g. cmn_root 79,222,784 = 38683 * 0x800). The header's fileSize covers the padding.
        pad = -len(out) % ALIGN_TABLE
        if pad:
            out += b'\x00' * pad

        struct.pack_into(HDR_FMT, out, 0,
                         MAGIC, self.version, ENTRY_SIZE, CHECKSUM_SIZE, DEP_SIZE,
                         len(self.entries), self.dep_count, self._pad,
                         len(out), entry_offset, checksum_offset)
        out[0:4] = b'2UX#'
        # ⚠⚠ THIS TOKEN IS SHARED WITH THE FASTFILE. DO NOT RECOMPUTE IT ON AN EDIT.
        # The 16 bytes at file[0x38:0x48] are ALSO baked into the zone that consumes this bank,
        # inside its SndBank asset body, and the console byte-compares the two at load. Measured:
        # mpl_common.all.sabs/.sabl checksums appear verbatim in common_mp.ff, and
        # mpl_raid.all.sabs/.sabl in all 9 genuine mp_raid fastfiles.
        #
        # Recomputing it is what froze the game: any replace changes a blob -> changes the
        # checksum table -> changes this MD5 -> it no longer matches the copy frozen in the .ff,
        # the bank fails validation and the load aborts. Since the engine only COMPARES this
        # token against the zone's copy, preserving the original keeps an edited bank acceptable
        # without having to patch the .ff.
        #
        # Same rule as the per-entry checksums above: preserve what a consumer already agreed on,
        # recompute only what we authored. A bank built from scratch has no prior agreement, so
        # it gets a freshly computed token.
        if self.header_checksum and not self._header_token_is_ours:
            out[0x38:0x48] = self.header_checksum
        else:
            out[0x38:0x48] = hashlib.md5(bytes(entry_table) + checksum_table).digest()
        return bytes(out)

    def save(self, path=None):
        blob = self.build()
        target = path or self.path
        if not target:
            raise SabError('no output path given')
        with open(target, 'wb') as f:
            f.write(blob)
        self.dirty = False
        return {'path': target, 'bytes': len(blob), 'entries': len(self.entries)}
