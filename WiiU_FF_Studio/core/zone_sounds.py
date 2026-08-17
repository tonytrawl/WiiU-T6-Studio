"""core.zone_sounds -- the SOUND assets in a fastfile are BANK METADATA, not audio.

WHAT A ZONE'S "SOUND" ASSET ACTUALLY IS
--------------------------------------
Measured on common_zm.ff: `SOUND 2`, spans of 4,794 and 4,775 bytes. That is a SndBank -- the
4,756-byte body (4,760 in common_mp) followed by the bank name, the alias lists, and the alias
records. Four kilobytes cannot hold audio, and it does not: there are no samples anywhere in a
.ff. The waveforms live in .sabs/.sabl, which the sound bank editor already handles.

So there is no "play" to offer here. What IS here is the thing the sound editor most needs and
cannot get from a bank file: the alias table pairing each entry's numeric id to its SOURCE
FILENAME. A .sabs stores only ids, so with no zone in reach its entries can only be listed as
hex. This module surfaces that table and names the bank file it belongs to, which is why a
zone view of it is worth having.

⚠ THE PAIRING FIELD IS `assetId` AT SndAlias+16, NOT `id` AT +4. `id` is derived from the ALIAS
name, `assetId` from the .snd FILE name, and a bank is keyed by the latter. Pairing on the wrong
one yields a table that looks plausible and matches almost nothing -- so `harvest()` reports its
match rate against real bank ids rather than assuming.

The walk itself is core.sab_names, already proven; this module is a thin view over it so the two
cannot drift apart.
"""
import os

from . import paths  # noqa: F401
from . import sab_names as SN


class ZoneBank(object):
    """One SndBank found inside a zone."""

    __slots__ = ('offset', 'name', 'entry_count', 'alias_count', 'data_size', 'aliases')

    def __init__(self, offset, name, entry_count, data_size, alias_count, aliases):
        self.offset = offset
        self.name = name
        self.entry_count = entry_count
        self.alias_count = alias_count
        self.data_size = data_size
        self.aliases = aliases            # [(assetId, assetFileName)]

    @property
    def label(self):
        return self.name or '(unnamed bank @0x%X)' % self.offset

    def __repr__(self):
        return ('ZoneBank(%r entries=%d aliases=%d named=%d)'
                % (self.name, self.entry_count, self.alias_count, len(self.aliases)))


def list_banks(zone):
    """Every SndBank in a decompressed zone, with its alias table. -> [ZoneBank]"""
    out = []
    seen = set()
    for body in SN.BODY_SIZES:
        for off, name, ecount, dsize, acount in SN.find_bodies(zone, body=body):
            if off in seen:
                continue
            seen.add(off)
            try:
                aliases = SN._aliases_at(zone, off, body)
            except Exception:
                aliases = []              # a desync on one body must not lose the others
            out.append(ZoneBank(off, name, ecount, dsize, acount, aliases))
    out.sort(key=lambda b: b.offset)
    return out


def name_map(zone):
    """{assetId: filename} across every bank in the zone -- the sound editor's dictionary."""
    names = {}
    for b in list_banks(zone):
        for aid, fname in b.aliases:
            names.setdefault(aid, fname)
    return names


def bank_files(bank, search_dirs=None):
    """Candidate .sabs/.sabl files on disk for this bank, by name. -> [path]

    A bank named `mpl_nuketown_2020` ships as `mpl_nuketown_2020.all.sabs`, `.english.sabs` and
    a matching `.sabl`, so match on the stem rather than expecting an exact filename.
    """
    if not bank.name:
        return []
    from . import settings as _st
    stem = bank.name.lower()
    out = []
    for d in (search_dirs if search_dirs is not None else _st.search_dirs()):
        if not d or not os.path.isdir(d):
            continue
        base = d.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(d):
            if dirpath.rstrip(os.sep).count(os.sep) - base >= 4:
                dirnames[:] = []
            for fn in sorted(files):
                low = fn.lower()
                if low.endswith(('.sabs', '.sabl')) and low.split('.')[0] == stem:
                    p = os.path.join(dirpath, fn)
                    if p not in out:
                        out.append(p)
    return out


def coverage(bank, bank_path):
    """How many of a real bank file's entries this zone can name. -> (named, total) or None."""
    try:
        from . import sab as S
        bank_file = S.SoundBank.open(bank_path)
    except Exception:
        return None
    names = {aid for aid, _f in bank.aliases}
    hit = sum(1 for e in bank_file.entries if e.id in names)
    return hit, len(bank_file.entries)
