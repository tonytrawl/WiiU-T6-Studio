"""
ff_assets.py -- light-weight asset browser / in-place editor for decompressed
T6 zones.

A zone is a flat stream of asset structs threaded with encoded zone pointers, so
without the full type schema we can't enumerate *every* asset's fields. We can,
however, reliably locate the assets that matter most for editing -- the ones that
carry an inline blob behind a `{ const char* name; int len; <blob>* buffer; }`
shape: scriptparsetrees (compiled GSC/CSC) and rawfiles. These are found by their
on-disk signature:

    [name_ptr = FF FF FF FF] [len : u32 BE] [buffer_ptr = FF FF FF FF]
    [name bytes ... 00] [buffer : len(+1) bytes, immediately after the name]

`scan_buffers()` returns those entries; `replace_buffer()` overwrites one in place
provided the replacement is the exact same length (a different length would shift
every following offset and break the pointer graph -- that needs a full rebuild).
"""
import struct
import re

FOLLOW = b"\xff\xff\xff\xff"


def _printable_name(zone, off, maxlen=192):
    end = zone.find(b"\x00", off)
    if end < 0 or end - off == 0 or end - off > maxlen:
        return None
    raw = zone[off:end]
    # names are ascii paths / identifiers
    if all(0x20 <= c < 0x7f for c in raw):
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            return None
    return None


def scan_buffers(zone):
    """Find every {name, len, buffer} inline-blob asset in a decompressed zone.

    Returns a list of dicts: name, struct_off, len, buf_off, buf_len, kind.
    `buf_len` is the number of bytes physically occupied by the blob (len + 1 for
    scripts, padded up to a 4-byte boundary), measured to the next plausible
    boundary so an exact-length replacement stays safe.
    """
    out = []
    seen = set()
    for m in re.finditer(FOLLOW, zone):
        s = m.start()
        # candidate struct: name_ptr@s, len@s+4, buffer_ptr@s+8
        if s in seen:
            continue
        if zone[s + 8:s + 12] != FOLLOW:
            continue
        ln = struct.unpack(">I", zone[s + 4:s + 8])[0]
        if ln == 0 or ln > 0x4000000:        # 0..64MB sanity
            continue
        name_off = s + 12
        name = _printable_name(zone, name_off)
        if not name:
            continue
        buf_off = name_off + len(name) + 1    # blob is right after name+NUL
        # blob length: scripts/rawfiles store len; scripts allocate len+1. Take the
        # generous len+1 and round to 4 so we cover the physical slot.
        buf_len = ln + 1
        buf_len += (-buf_len) % 4
        if buf_off + buf_len > len(zone):
            continue
        head = zone[buf_off:buf_off + 6]
        low = name.lower()
        if low.endswith(".gsc") or low.endswith(".csc") or head[:5] == b"GSC\r\n" or head[:6] == b"\x80GSC\r\n":
            kind = "script"
        elif "/" in name or "." in name:
            kind = "rawfile"
        else:
            kind = "blob"
        out.append({
            "name": name, "struct_off": s, "len": ln,
            "buf_off": buf_off, "buf_len": buf_len, "kind": kind,
            "head": head.hex(),
        })
        seen.add(s)
    # de-dup by struct offset, keep stable order
    out.sort(key=lambda e: e["struct_off"])
    return out


def extract_buffer(zone, entry):
    """Return the raw blob bytes for an entry (len bytes; the engine reads `len`)."""
    return zone[entry["buf_off"]:entry["buf_off"] + entry["len"]]


def replace_buffer(zone, entry, new_bytes):
    """Return a new zone with the entry's blob overwritten in place.

    Requires len(new_bytes) == entry['len'] (exact same length). Raises ValueError
    otherwise -- a different length would shift following offsets and corrupt the
    pointer graph; use the OAT rebuild path (OAT_GSC_DIR) for resizing edits.
    """
    if len(new_bytes) != entry["len"]:
        raise ValueError(
            f"replacement is {len(new_bytes)} bytes but the slot is {entry['len']} bytes. "
            f"In-place edits must be the exact same length (pad/truncate to match, or "
            f"rebuild the zone to resize).")
    b = bytearray(zone)
    b[entry["buf_off"]:entry["buf_off"] + entry["len"]] = new_bytes
    return bytes(b)
