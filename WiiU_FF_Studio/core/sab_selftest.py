"""core.sab_selftest -- gate battery for the sound bank editor.

Run:  python -m core.sab_selftest        (from WiiU_FF_Studio/)

  S1   re-emit           untouched retail banks rebuild BYTE-EXACT
  S1r  reachability      a bank with one byte changed does NOT rebuild byte-exact
  S2   checksums         every stored per-entry MD5 matches its blob
  S3   header checksum   header MD5 == MD5(entryTable ++ checksumTable)
  S4   decode            every entry decodes, with a sane length and amplitude
  S5   codec round-trip  encode -> decode SNR is in the documented genuine range
  S6   replace           replacing an entry keeps its id and re-reads correctly
  S7   add / delete      a new entry survives a save/reload; delete removes it
  S8   wav export        exported WAV re-opens with the right rate and channels
  S9   header token       an edit PRESERVES the 16-byte token the consuming .ff also stores
  S10  idempotent build   three consecutive builds are byte-identical

S1 is the strong one: byte-exact re-emission of a retail bank means the writer reproduces the
retail linker's own layout, so a bank we save differs from a shipped one only where we intended.
S1r exists because a check that cannot fail proves nothing -- it corrupts a bank and requires
S1's comparison to notice.
"""
import glob
import io
import os
import sys
import wave

import numpy as np

from . import paths  # noqa: F401
from . import sab as S

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
def _retail_sound_dirs():
    """Sound folders to test against, discovered rather than hard-coded.

    The battery must run on any machine and any platform, so it asks core.settings where the
    game content is and looks for a `sound` folder there. Finding none is a SKIP, never a pass.
    """
    from . import settings as _st
    out = []
    for d in _st.search_dirs():
        for cand in (os.path.join(d, 'sound'), d):
            if os.path.isdir(cand) and cand not in out:
                out.append(cand)
    return out


def _roots():
    """Directories to search for test banks.

    ⚠ NEVER glob a frozen ROOT. `ROOT` is derived from this file, so inside a PyInstaller bundle
    it resolves to the PARENT OF THE _MEIxxxx EXTRACTION DIR -- i.e. the system TEMP directory --
    and `ROOT/**/*.sab*` then scoops up every stray bank anyone ever wrote to TEMP, including
    deliberately-damaged test artifacts. Measured: that polluted the corpus with 8 files and
    produced a spurious S3 FAIL (111/112) in the packaged binary while the source run was
    136/136 clean. The corpus must be real game data plus whatever sits next to the binary.
    """
    roots = [] if getattr(sys, 'frozen', False) else [ROOT]
    if getattr(sys, 'frozen', False):
        roots.append(os.path.dirname(sys.executable))
    roots.append(os.getcwd())
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def _deploy_dirs():
    """Directories this tool WRITES INTO, so a bank found there may be our own output.

    ⚠ A RETAIL INVARIANT MUST NOT BE MEASURED ON OUR OWN OUTPUT. `SoundBank.build()`
    deliberately PRESERVES the 0x38..0x48 header token of an opened bank rather than
    recomputing it, because the SndBank asset inside the .ff carries the same token and
    patching the zone to agree is the worse trade. An edited bank whose entry table has moved
    is therefore EXPECTED to violate S3's identity -- by design, not by defect.

    Measured: widening discovery to the live Cemu content folder pulled in a deployed
    `mpl_nuketown_2020.all.sabs` (4,100,096 B, written by this editor) and flipped S3 to FAIL
    43/44 with NO code change -- core/sab.py was byte-identical to the build that had just
    passed S3 clean. The sample changed, not the behaviour.

    This is a scoping fix, not a softened gate: a mismatch anywhere outside a deploy directory
    still FAILS, and every excluded bank is printed rather than silently dropped.
    """
    out = []
    try:
        from . import settings as _st
        for d in _st._cemu_content_dirs():
            out.append(os.path.abspath(d))
    except Exception:
        pass
    return out


def _is_deployed(path):
    """True when `path` is OUR OWN OUTPUT rather than retail data.

    ⚠ 'Converted_Sound_Banks' COUNTS. It is not a deploy target, but every bank in it was written
    by this project's own converter, so it is exactly as unsuitable for measuring a retail
    invariant. Measured: S12 selected Converted_Sound_Banks/mpl_dig.all.sabs -- converted
    2026-07-05, before the blob-layout fix -- and graded today's decoder against a file the old
    ENCODER wrote. That is the closed loop this module's S3 note already warns about, reached
    through a different door.
    """
    p = os.path.abspath(path)
    parts = set(os.path.normcase(p).split(os.sep))
    if os.path.normcase('Converted_Sound_Banks') in parts:
        return True
    for d in _deploy_dirs():
        if p == d or p.startswith(d + os.sep):
            return True
    return False


def _has_old_blob_layout(bank):
    """True when this bank was written by THIS TOOL before the blob-layout fix. Path-independent.

    Retail reserves blob[104:160] as 56 zero bytes on 12,016 / 12,016 format-9 entries. The old
    encoder believed a stereo header was 8 + 48*channels = 104 bytes and wrote AUDIO from there,
    so a non-zero window in that range cannot occur in retail data and is proof the file is our
    own earlier output.

    ⚠ THIS IDENTIFIES OLD OUTPUT ONLY. Banks written after the fix are correctly shaped and will
    look retail here -- which is the point: the ones this flags are exactly the ones that need
    re-converting. Measured: cmn_root 0/188 entries flagged (retail), zmb_common 1/83 and
    zmb_rt_load 2/2 flagged (ours, mtime 2026-08-13).
    """
    try:
        from . import sab as _S
        for e in bank.entries:
            if e.fmt != _S.FMT_DSPADPCM:
                continue
            blob = bank.blob(e)
            if len(blob) >= _S.BLOB_HEADER and any(blob[104:160]):
                return True
    except Exception:
        pass
    return False


def _banks(limit=None):
    out = []
    pats = []
    for r in _retail_sound_dirs():
        pats.append(os.path.join(r, '*.sab*'))
        pats.append(os.path.join(r, '**', '*.sab*'))
    for r in _roots():
        pats.append(os.path.join(r, 'Converted_Sound_Banks', '**', '*.sab*'))
        pats.append(os.path.join(r, '**', '*.sab*'))
    for pat in pats:
        for p in sorted(glob.glob(pat, recursive=True)):
            if p not in out:
                out.append(p)
        if out and limit and len(out) >= limit:
            break
    return out[:limit] if limit else out


class Battery(object):
    def __init__(self):
        self.rows = []

    def add(self, gate, status, detail=''):
        self.rows.append((gate, status, detail))

    def report(self):
        w = max((len(r[0]) for r in self.rows), default=4)
        for g, s, d in self.rows:
            print('  %-6s %-*s %s' % (s, w, g, d))
        f = sum(1 for r in self.rows if r[1] == 'FAIL')
        p = sum(1 for r in self.rows if r[1] == 'PASS')
        sk = sum(1 for r in self.rows if r[1] == 'SKIP')
        print()
        print('  %d passed, %d failed, %d skipped' % (p, f, sk))
        if sk:
            print('  (a skipped gate is NOT a pass -- its data was not on this machine)')
        return f


def _snr(ref, test):
    n = min(len(ref), len(test))
    ref, test = ref[:n].astype(float), test[:n].astype(float)
    best = -1e9
    for lag in range(0, 9):
        a = ref[lag:]
        c = test[:len(a)]
        err = ((a - c) ** 2).sum()
        if err == 0:
            return 999.0
        best = max(best, 10 * np.log10(max((a ** 2).sum(), 1e-9) / err))
    return best


def run():
    b = Battery()
    banks = _banks()
    if not banks:
        b.add('S1-S8', 'SKIP', 'no .sabs/.sabl found on this machine')
        return b.report()

    # ---- S1 byte-exact re-emit -------------------------------------------------
    exact = diff = err = 0
    entries = 0
    first_bad = None
    for p in banks:
        try:
            bank = S.SoundBank.open(p)
            entries += len(bank.entries)
            if bank.build() == bank.raw:
                exact += 1
            else:
                diff += 1
                first_bad = first_bad or os.path.basename(p)
        except Exception as ex:
            err += 1
            first_bad = first_bad or '%s: %s' % (os.path.basename(p), ex)
    b.add('S1', 'PASS' if diff == 0 and err == 0 else 'FAIL',
          '%d/%d banks re-emit byte-exact (%d entries)%s'
          % (exact, len(banks), entries, '' if not first_bad else '; first bad: %s' % first_bad))

    # ---- S1r reachability ------------------------------------------------------
    try:
        bank = S.SoundBank.open(banks[0])
        raw = bytearray(bank.raw)
        e0 = bank.entries[0]
        raw[e0.offset] ^= 0xFF                       # flip a byte of real audio
        tampered = S.SoundBank(bytes(raw), banks[0])
        caught = tampered.build() != bank.raw
        also = tampered.verify_checksum(tampered.entries[0]) is False
        b.add('S1r', 'PASS' if caught and also else 'FAIL',
              'a tampered bank is detected by both the re-emit compare and its MD5'
              if caught and also else
              'tampering went unnoticed (re-emit differs: %s, md5 differs: %s)' % (caught, also))
    except Exception as ex:
        b.add('S1r', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

    # ---- S2 / S3 checksums -----------------------------------------------------
    bad = checked = 0
    hdr_ok = hdr_tot = 0
    hdr_bad = []            # non-deployed banks that broke the identity -- a real failure
    hdr_deployed = []       # banks under a deploy dir, reported but not judged
    import hashlib
    import struct
    for p in banks:
        try:
            bank = S.SoundBank.open(p)
        except Exception:
            continue
        for e in bank.entries:
            v = bank.verify_checksum(e)
            if v is not None:
                checked += 1
                bad += int(not v)
        et = bytearray()
        for e in bank.entries:
            et += struct.pack('<4I', e.id, e.size, e.offset, e.frames)
            et += struct.pack('<4B', e.rate_idx, e.channels, e.looping, e.fmt)
        ct = b''.join(bank.checksums)
        matched = hashlib.md5(bytes(et) + ct).digest() == bank.header_checksum
        if _is_deployed(p) or _has_old_blob_layout(bank):
            # Our own output -- by path (see _deploy_dirs) OR by its own bytes (see
            # _has_old_blob_layout, which catches copies deployed somewhere the path rules do not
            # know about, e.g. straight into the game install). Counted separately and printed,
            # never silently skipped: a gate that quietly drops its awkward samples is not a gate.
            # If one of these matches, that is fine too; it just was not edited.
            hdr_deployed.append((os.path.basename(p), 'matches' if matched else 'preserved token'))
            continue
        hdr_tot += 1
        hdr_ok += int(matched)
        if not matched:
            hdr_bad.append(p)
    # ⚠ `bad` is NOT a failure count. Retail itself ships entries whose stored MD5 does not
    # equal MD5(blob) -- measured 380 of 12,667 (3.0%), almost all in .sabl banks, with the blob
    # sitting exactly at its declared offset and size. We do not know what retail hashed there,
    # so build() PRESERVES the stored value for untouched entries instead of "correcting" it;
    # that is what keeps S1 byte-exact. The gate is therefore that the anomaly stays a small
    # known minority, not that it is zero.
    rate = (100.0 * bad / checked) if checked else 0.0
    b.add('S2', 'PASS' if checked and rate < 10.0 else 'FAIL',
          '%d/%d stored MD5s match their blob; %d (%.1f%%) are retail anomalies, preserved '
          'verbatim rather than recomputed' % (checked - bad, checked, bad, rate))
    s3msg = '%d/%d header checksums == MD5(entryTable ++ checksumTable)' % (hdr_ok, hdr_tot)
    if hdr_deployed:
        s3msg += ' | %d bank(s) under a deploy dir NOT judged (our own output): %s' % (
            len(hdr_deployed), ', '.join('%s [%s]' % t for t in hdr_deployed[:4]))
    if hdr_bad:
        s3msg += ' | BROKEN: %s' % ', '.join(os.path.basename(x) for x in hdr_bad[:6])
    b.add('S3', 'PASS' if hdr_tot and hdr_ok == hdr_tot else 'FAIL', s3msg)

    # ---- S4 decode -------------------------------------------------------------
    dec = derr = 0
    peaks, ratios = [], []
    for p in banks[:12]:
        try:
            bank = S.SoundBank.open(p)
        except Exception:
            continue
        for e in bank.entries[:25]:
            try:
                pcm, sr = bank.decode(e, seconds=4)
                dec += 1
                if len(pcm):
                    peaks.append(float(np.abs(pcm).max()) / 32768.0)
                exp = e.frames * 2 // 3
                if exp and len(pcm) >= exp:
                    ratios.append(len(pcm) / exp)
            except Exception:
                derr += 1
    silent = sum(1 for x in peaks if x < 0.001)
    ratio_ok = (not ratios) or (0.99 <= float(np.median(ratios)) <= 1.05)
    b.add('S4', 'PASS' if derr == 0 and dec and silent < max(1, dec // 10) and ratio_ok else 'FAIL',
          '%d entries decoded, %d errors, %d silent, median peak %.2f, length ratio %.4f'
          % (dec, derr, silent, float(np.median(peaks)) if peaks else 0,
             float(np.median(ratios)) if ratios else 0))

    # ---- S5 codec round-trip ---------------------------------------------------
    try:
        import sab_convert as SC
        from scipy.signal import resample_poly
        t = np.arange(48000) / 48000.0
        tone = (0.6 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).reshape(-1, 1)
        blob = SC.encode_entry_wiiu(tone)
        coefs, streams = S.parse_blob(blob, 1)
        got = S.decode_dsp_adpcm(streams[0], coefs[0])
        ref = resample_poly(tone.astype(np.float64), 2, 3, axis=0)[:, 0]
        ref = np.clip(np.round(ref), -32768, 32767).astype(np.int16)
        v = _snr(ref, got)
        # The format notes record 23-46 dB for genuine-quality content; a broken decoder
        # scores at or below 0.
        b.add('S5', 'PASS' if v >= 20 else 'FAIL',
              'encode -> decode SNR %.1f dB on a 440 Hz tone (documented genuine range 23-46 dB '
              'for real content; a wrong decoder scores <= 0)' % v)
    except Exception as ex:
        b.add('S5', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

    # ---- S6 / S7 / S8 edit operations -----------------------------------------
    import tempfile
    try:
        src = min(banks, key=lambda p: os.path.getsize(p))
        bank = S.SoundBank.open(src)
        n0 = len(bank.entries)
        target = bank.entries[0]
        keep_id = target.id

        t = np.arange(24000) / 48000.0
        tone = (0.4 * 32767 * np.sin(2 * np.pi * 660 * t)).astype(np.int16)
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(tone.tobytes())
        wav = wav.getvalue()

        bank.replace(target, wav)
        tmp = os.path.join(tempfile.gettempdir(), '_sab_selftest.sabs')
        bank.save(tmp)
        rb = S.SoundBank.open(tmp)
        e = rb.entries[0]
        pcm, sr = rb.decode(e)
        id_ok = e.id == keep_id
        md5_ok = rb.verify_checksum(e)
        dur_ok = abs(len(pcm) / float(sr) - 0.5) < 0.05
        b.add('S6', 'PASS' if id_ok and md5_ok and dur_ok else 'FAIL',
              'replaced entry keeps id %s, MD5 %s, duration %.2fs (expected 0.50)'
              % ('kept' if id_ok else 'CHANGED', 'ok' if md5_ok else 'BAD',
                 len(pcm) / float(sr)))

        new_id = 0x5AB70001
        rb.add(new_id, wav)
        rb.save(tmp)
        rb2 = S.SoundBank.open(tmp)
        found = [x for x in rb2.entries if x.id == new_id]
        add_ok = len(found) == 1 and rb2.verify_checksum(found[0]) and len(rb2.entries) == n0 + 1
        rb2.delete(found[0])
        rb2.save(tmp)
        rb3 = S.SoundBank.open(tmp)
        del_ok = (len(rb3.entries) == n0 and not any(x.id == new_id for x in rb3.entries)
                  and all(rb3.verify_checksum(x) for x in rb3.entries))
        b.add('S7', 'PASS' if add_ok and del_ok else 'FAIL',
              'add -> %d entries and MD5 ok; delete -> back to %d with every MD5 still valid'
              % (n0 + 1, n0) if add_ok and del_ok
              else 'add ok: %s, delete ok: %s' % (add_ok, del_ok))

        wv = rb3.to_wav(rb3.entries[0], seconds=1)
        with wave.open(io.BytesIO(wv), 'rb') as w:
            ok = w.getsampwidth() == 2 and w.getnchannels() in (1, 2) and w.getframerate() > 0
            b.add('S8', 'PASS' if ok else 'FAIL',
                  'exported WAV: %d Hz, %d ch, %d-bit' % (w.getframerate(), w.getnchannels(),
                                                          w.getsampwidth() * 8))
        try:
            os.remove(tmp)
        except OSError:
            pass
    except Exception as ex:
        import traceback
        b.add('S6-S8', 'FAIL', '%s: %s' % (type(ex).__name__, ex))
        if os.environ.get('SAB_SELFTEST_TRACE'):
            traceback.print_exc()

    # ---- S9 the header token is PRESERVED across an edit --------------------------
    # It is shared with the fastfile that consumes the bank (measured: mpl_common's token is
    # inside common_mp.ff, mpl_raid's inside all 9 genuine raid .ff). The console byte-compares
    # them, so recomputing it is what made an edited bank fail to load.
    try:
        src = None
        for p in banks:
            bk = S.SoundBank.open(p)
            if bk.entries and bk.entries[0].fmt == S.FMT_DSPADPCM:
                src = (p, bk)
                break
        if src is None:
            b.add('S9', 'SKIP', 'no DSP-ADPCM bank available')
        else:
            p, bk = src
            before = bk.header_checksum
            e = bk.entries[0]
            bk.replace(e, bk.to_wav(e, seconds=1))
            after = S.SoundBank(bk.build())
            same = after.header_checksum == before
            entry_ok = after.verify_checksum(after.entries[0])
            b.add('S9', 'PASS' if same and entry_ok else 'FAIL',
                  'header token unchanged by an edit (%s), while the edited entry gets a fresh '
                  'MD5 (%s)' % (before.hex()[:16], 'ok' if entry_ok else 'BAD')
                  if same and entry_ok else
                  'token preserved=%s edited-entry md5 ok=%s' % (same, entry_ok))
    except Exception as ex:
        b.add('S9', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

    # ---- S10 build() is idempotent -------------------------------------------------
    try:
        p = min(banks, key=lambda x: os.path.getsize(x))
        bk = S.SoundBank.open(p)
        e = bk.entries[0]
        bk.replace(e, bk.to_wav(e, seconds=1))
        outs = [bk.build() for _ in range(3)]
        idem = outs[0] == outs[1] == outs[2]
        after = S.SoundBank(outs[-1])
        base = S.SoundBank.open(p)
        new_bad = (sum(1 for x in after.entries if after.verify_checksum(x) is False)
                   - sum(1 for x in base.entries if base.verify_checksum(x) is False))
        b.add('S10', 'PASS' if idem and new_bad <= 0 else 'FAIL',
              'three consecutive builds are byte-identical and introduce no new MD5 mismatches'
              if idem and new_bad <= 0
              else 'idempotent=%s new mismatches=%d' % (idem, new_bad))
    except Exception as ex:
        b.add('S10', 'FAIL', '%s: %s' % (type(ex).__name__, ex))

    # ---- S11 the name dictionary is usable in THIS build --------------------
    # The packaged editor shipped without `_sab_names.cache` and therefore showed bare hex ids
    # for every entry, while the source build named them. open() is CACHE-ONLY by design (a
    # harvest decrypts every .ff and looks like a hang), so a missing cache means no names at
    # all -- and no other gate notices, because every one of them works fine on unnamed entries.
    try:
        from . import sab_names as SN
        names = SN.load_shared(build_if_missing=False) or {}
        n = len(names)
        # ⚠ THE SUBJECT MUST NOT BE banks[0]. What this gate proves is that the packaged build
        # can LOAD its dictionary and that the id pairing is the right one (assetId @SndAlias+16,
        # NOT id @+4). It does NOT measure how much of the user's library is covered -- that
        # depends entirely on which game folders they have configured, so scoring one
        # directory-order-dependent bank made the gate flip on an unrelated discovery change:
        # in a single run mpl_dig.all.sabl scored 0/46 while mpl_nuketown_2020.all.sabl scored
        # 51/51. Judge the best-covered bank, and report the library-wide census alongside it so
        # a thin dictionary is still visible rather than hidden behind a PASS.
        best = (0.0, 0, 0, '-')
        tot_hit = tot_all = covered = 0
        for p in banks:
            try:
                bank = S.SoundBank.open(p)
            except Exception:
                continue
            cnt = len(bank.entries)
            if not cnt:
                continue
            hit = sum(1 for e in bank.entries if e.id in names)
            tot_hit += hit
            tot_all += cnt
            frac = hit / float(cnt)
            covered += int(frac >= 0.5)
            if frac > best[0]:
                best = (frac, hit, cnt, os.path.basename(p))
        overall = (100.0 * tot_hit / tot_all) if tot_all else 0.0
        b.add('S11', 'PASS' if n > 0 and best[0] >= 0.5 else 'FAIL',
              'dictionary has %d ids; best bank %s %d/%d (%.0f%%); %d/%d banks >=50%%; '
              'library-wide %d/%d (%.0f%%). Low library-wide coverage means zones the dictionary '
              'was never harvested from -- point the tool at more game folders -- whereas a '
              'best bank near 0%% would mean the id pairing itself is broken.'
              % (n, best[3], best[1], best[2], best[0] * 100, covered, len(banks),
                 tot_hit, tot_all, overall))
    except Exception as ex:
        b.add('S11', 'FAIL', 'name dictionary unusable: %s: %s' % (type(ex).__name__, ex))

    # ---- S12 the format-9 blob layout (STRUCTURAL ONLY -- read the warning) --------------
    # ⚠ THIS GATE EXISTS BECAUSE THE OLD LAYOUT PASSED EVERY OTHER GATE. Byte-exact re-emit (S1),
    # matching MD5s (S2) and a clean decode (S4) all hold when the blob is merely SPLIT wrongly --
    # none of them look at whether the two channels are the right two channels.
    #
    # WHAT IT CHECKS: STRUCTURE, oracle-free, fleet-wide -- every format-9 entry's chunk table
    # tiles [BLOB_HEADER, size) exactly, no gap, no overlap, per-channel totals equal.
    #
    # ⛔ WHAT IT DELIBERATELY DOES NOT CHECK, AND WHY -- DO NOT RE-ADD IT.
    # This gate briefly carried a CONTENT test: on a multi-block stereo entry, the correct split
    # should be more channel-coherent than the old per-frame split. That premise is UNSOUND, and
    # it shipped green only because it stopped at the first entry it found -- i.e. its verdict was
    # decided by directory order, the same defect that has now bitten three gates in this project.
    # Measured over 24 retail multi-block stereo entries, judging the SAME layouts:
    #     coherence contrast (new > old + 0.25) held on  5/24 = 21%
    #     seam continuity    (old seam > 1.5x new) held on 1/24 =  4%
    # Both are proxies for the layout but are dominated by the CONTENT: retail stereo ranges from
    # near-mono (0.99) to genuinely wide (<0.1), so either can favour either layout by accident.
    # mpl_* entries are decisive for the correct layout (coherence 0.98-1.0000 vs 0.07-0.33) while
    # spl_* entries favour the old split on both proxies -- an open question for the oracle lane,
    # not something a cheap local proxy can settle.
    # ⇒ CONTENT CORRECTNESS IS NOT COVERED HERE. It rests on cross-correlation against the retail
    #   PC masters (740/740 decidable, 1,837/1,849 fleet-wide), which needs data this battery
    #   cannot assume is present. A gate must not claim what it cannot measure.
    # ⚠ RELATED, from the re-conversion lane: the 56-zero test at blob[104:160] is BLIND to
    #   interleave errors -- a correct-header / swapped-interleave control passes it. It identifies
    #   OUR OLD OUTPUT only, and must never be used as a sole acceptance test.
    try:
        import numpy as _np
        from . import sab as _S
        checked = good = 0
        for p in _banks(60):
            try:
                bank = _S.SoundBank.open(p)
            except Exception:
                continue
            streamed = not p.lower().endswith('.sabl')
            for e in bank.entries:
                if e.fmt != _S.FMT_DSPADPCM:
                    continue
                chunks, per = _S.blob_chunks(e.size, e.channels, streamed)
                covered = sum(n for c in chunks for _o, n in c)
                equal = len(set(sum(n for _o, n in c) for c in chunks)) == 1
                checked += 1
                if covered == e.channels * per and equal:
                    good += 1
        # Count how many multi-block stereo entries EXIST, purely to report coverage honestly.
        # No verdict is derived from their content -- see the block comment above.
        multiblock = 0
        for p in _banks(60):
            try:
                bank = _S.SoundBank.open(p)
            except Exception:
                continue
            if not p.lower().endswith('.sabs'):
                continue
            for e in bank.entries:
                if (e.fmt == _S.FMT_DSPADPCM and e.channels == 2
                        and (((e.size - _S.BLOB_HEADER) // 2) & ~7) > 0xFFB0):
                    multiblock += 1
        b.add('S12', 'PASS' if (checked and good == checked) else
                     ('SKIP' if not checked else 'FAIL'),
              '%d/%d format-9 entries tile exactly (%d multi-block stereo present). '
              'STRUCTURE ONLY -- channel content is NOT verified here; that needs the PC-master '
              'oracle.' % (good, checked, multiblock))
    except Exception as ex:
        b.add('S12', 'FAIL', 'blob layout gate failed: %s: %s' % (type(ex).__name__, ex))

    return b.report()


if __name__ == '__main__':
    print('Sound bank editor gate battery')
    print()
    sys.exit(1 if run() else 0)
