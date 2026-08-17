"""core.rpl_patch -- locate and apply the engine RPL patches, structurally.

WHY THIS EXISTS
---------------
A repacked fastfile carries NO valid RSA signature (we do not have the private key), so an
unpatched console REJECTS it. That is not a theory: a user with a EU copy had every edit fail --
including a save with NO changes at all -- while the identical tool worked here, because this
machine's RPLs already carry the signature patch. Measured on his files: the decompressed zone
was byte-identical (md5 3d1fab8a0873 both sides) and the container framing matched (3,526 chunks,
none over the 0x8000 buffer); the ONLY difference was the 256-byte signature block, 255/256
non-zero in his genuine file versus all zeros in every file this tool writes.

⇒ **The signature patch is not an optional extra. It is the thing that makes an edited fastfile
loadable at all.** Shipping the editors without it shipped half a tool.

EVERY PATCH IS LOCATED STRUCTURALLY -- by symbol name and instruction pattern -- never by a
hard-coded address. The base, MP and update builds all have different VAs for the same code, and
the resolution site is at 0x0297B418 in t6mp but 0x027FA17C in t6_cafef. A patcher pinned to an
address silently does nothing on the wrong build.

⚠ PATCH BOTH RPLs. `t6_cafef_rpl` loads first and carries its own statically-linked copy of the
same engine code; patching only `t6mp_cafef_rpl` has cost this project multiple boots. Boot-time
render init in particular runs entirely in the BASE rpl.

⚠ THE CODE LIVES IN A COMPRESSED SECTION. `.text` has sh_flags & 0x08000000, so a patch is
decompress -> edit -> re-deflate -> fix sh_size -> fix the per-section CRC in SHT_RPL_CRCS. It is
not a raw byte poke, and a rebuild that forgets the CRC produces a file the loader rejects.

NO DISASSEMBLER DEPENDENCY. The handful of instruction forms involved are decoded inline below,
so the shipped tool needs nothing beyond the standard library.
"""
import binascii
import struct
import zlib

# --- PPC encodings we need -------------------------------------------------------------------
CMPWI_R12_1 = 0x2C0C0001        # cmpwi r12, 1      -- marks the sig function's success test
LI_R4_10E = 0x3880010E          # li   r4, 0x10e    -- the instruction after the bl we replace
LI_R3_1 = 0x38600001            # li   r3, 1        -- what the DLC load gate becomes
LI_R0_MASK = 0x38000000         # li   r0, imm      -- resolution width
LI_R9_MASK = 0x39200000         # li   r9, imm      -- resolution height

SEC_TEXT_ADDR = 0x02000000
SHT_RPL_CRCS = 0x80000003


class RplPatchError(Exception):
    pass


# --- tiny PPC decode helpers (no capstone) ---------------------------------------------------
def _is_bl(w):
    return (w & 0xFC000003) == 0x48000001


def _branch_target(w, va):
    """Target of b/bl (opcode 18) or bc/bcl (opcode 16), or None if not a branch."""
    op = w >> 26
    if op == 18:
        off = w & 0x03FFFFFC
        if off & 0x02000000:
            off -= 0x04000000
        return (va if not (w & 2) else 0) + off
    if op == 16:
        off = w & 0x0000FFFC
        if off & 0x8000:
            off -= 0x10000
        return (va if not (w & 2) else 0) + off
    return None


def _bl_dest(w, va):
    return _branch_target(w, va) if _is_bl(w) else None


# --- container ------------------------------------------------------------------------------
def _sections(d):
    shoff = struct.unpack(">I", d[0x20:0x24])[0]
    n = struct.unpack(">H", d[0x30:0x32])[0]
    secs = [list(struct.unpack(">IIIIIIIIII", d[shoff + i * 40:shoff + i * 40 + 40]))
            for i in range(n)]
    return shoff, n, secs


def _sec_bytes(d, sh):
    fl, sz, off = sh[2], sh[5], sh[4]
    raw = bytes(d[off:off + sz])
    return zlib.decompress(raw[4:]) if (fl & 0x08000000 and sz) else raw


def symbols(d):
    """{name: (value, size)} from .symtab. Retail BO2 RPLs ship full symbol tables."""
    _sh, n, secs = _sections(d)
    symis = [i for i in range(n) if secs[i][1] == 2]
    if not symis:
        return {}
    symtab = _sec_bytes(d, secs[symis[0]])
    strtab = _sec_bytes(d, secs[secs[symis[0]][6]])
    out = {}
    for o in range(0, len(symtab) - 15, 16):
        nm, val, sz, _i, _o, _s = struct.unpack(">IIIBBH", symtab[o:o + 16])
        e = strtab.find(b"\0", nm)
        if e <= nm:
            continue
        name = strtab[nm:e].decode("latin1", "replace")
        if val and name not in out:
            out[name] = (val, sz)
    return out


class Rpl(object):
    """An RPL opened for patching: .text decompressed once, rebuilt once."""

    def __init__(self, path):
        self.path = path
        self.data = bytearray(open(path, "rb").read())
        self.shoff, self.n, self.secs = _sections(self.data)
        try:
            self.text_i = next(i for i in range(self.n)
                               if self.secs[i][3] == SEC_TEXT_ADDR and self.secs[i][1] == 1)
        except StopIteration:
            raise RplPatchError("%s: no executable .text at %#x -- is this a Cafe RPL?"
                                % (path, SEC_TEXT_ADDR))
        t = self.secs[self.text_i]
        self.taddr, self.toff, self.tsz = t[3], t[4], t[5]
        try:
            self.text = bytearray(zlib.decompress(bytes(self.data[self.toff:
                                                                  self.toff + self.tsz])[4:]))
        except zlib.error as ex:
            raise RplPatchError("%s: .text did not inflate (%s)" % (path, ex))
        self.syms = symbols(self.data)
        self.applied = []

    # -- reads -------------------------------------------------------------------------------
    def word(self, va):
        return struct.unpack_from(">I", self.text, va - self.taddr)[0]

    def set_word(self, va, w):
        struct.pack_into(">I", self.text, va - self.taddr, w)

    def sym(self, *names):
        for nm in names:
            for key in (nm, nm + "__Fv", nm + "__Fi"):
                if key in self.syms:
                    return self.syms[key]
            for key, val in self.syms.items():
                if nm in key:
                    return val
        return None

    def build(self, out_path):
        """Re-deflate .text, fix its CRC and size, relayout, write. -> bytes written."""
        newdec = bytes(self.text)
        newcrc = binascii.crc32(newdec) & 0xFFFFFFFF
        stored = struct.pack(">I", len(newdec)) + zlib.compress(newdec, 9)
        crci = next(i for i in range(self.n) if self.secs[i][1] == SHT_RPL_CRCS)
        prefix = bytearray(self.data[:self.toff])
        struct.pack_into(">I", prefix, self.secs[crci][4] + self.text_i * 4, newcrc)

        out = bytearray(prefix)
        for i in sorted((i for i in range(self.n)
                         if self.secs[i][4] >= self.toff and self.secs[i][5] > 0),
                        key=lambda i: self.secs[i][4]):
            while len(out) % 0x40:
                out.append(0)
            no = len(out)
            data = stored if i == self.text_i else bytes(
                self.data[self.secs[i][4]:self.secs[i][4] + self.secs[i][5]])
            out += data
            struct.pack_into(">I", out, self.shoff + i * 40 + 16, no)
            if i == self.text_i:
                struct.pack_into(">I", out, self.shoff + i * 40 + 20, len(data))
        with open(out_path, "wb") as f:
            f.write(out)
        return len(out)


# --- patches --------------------------------------------------------------------------------
def find_signature(r):
    """-> dict(site, target, current, desc) for the fastfile RSA signature check.

    `__DBX_AuthLoad_ValidateSignature_Try` returns 1 for a valid signature. We replace the
    `bl DB_SetPublicKey` near the top with an unconditional branch to the function's OWN success
    block, skipping all RSA work. The state pointers are already set up at that point, so the
    0x80 "validated" flag is still set and nothing downstream changes.
    """
    hit = r.sym("ValidateSignature_Try")
    if not hit:
        return None
    va, sz = hit
    span = sz or 292
    succ = site = None
    for off in range(0, span, 4):
        w = r.word(va + off)
        if w == CMPWI_R12_1:
            succ = _branch_target(r.word(va + off + 4), va + off + 4)
        if w == LI_R4_10E and off >= 4:
            site = va + off - 4
    if site is None:
        return None
    cur = r.word(site)
    # ⚠ RECOGNISE THE PATCHED STATE, NOT JUST THE STOCK ONE. Applying this patch REPLACES the
    # `bl DB_SetPublicKey` with an unconditional `b <success>`, so on an already-patched RPL the
    # word is no longer a bl. Requiring a bl here made the tool report "signature check not
    # present" for a file that is patched and working -- measured on the live t6mp, where
    # 0x021941e4 reads 48000094 (`b +0x94`). Report BOTH states instead.
    is_stock = _is_bl(cur)
    is_patched = (cur >> 26) == 18 and not _is_bl(cur)
    if not (is_stock or is_patched):
        return None
    if succ is None and is_stock:
        return None
    return dict(site=site, target=succ, current=cur, already=is_patched,
                desc=("already patched -- RSA validation is skipped" if is_patched else
                      "skip RSA validation -> branch to the function's success block"))


def apply_signature(r):
    f = find_signature(r)
    if not f:
        raise RplPatchError("signature check not found (no ValidateSignature_Try, or the "
                            "function's layout differs from every build seen so far)")
    rel = (f['target'] - f['site']) & 0x03FFFFFC
    r.set_word(f['site'], 0x48000000 | rel)
    r.applied.append(('signature', f['site']))
    return f


def find_dlc_loadgate(r):
    """-> dict for the DLC load gate: make the loader request every pack's load fastfile.

    ⚠ PATCH THE CALL SITE, NOT THE OWNERSHIP FUNCTION. Forcing
    `__Content_DoWeHaveIndexedContentPack` to 1 marks packs OWNED, and the ZM globe then renders
    a map pack with no hardware map row and faults. `Content_IsIndexedContentPackEnabled` has
    only two callers, both in the DB load path, so replacing the call inside
    `DB_LoadLoadFastfilesForNewContent` with `li r3,1` unlocks loading WITHOUT touching
    ownership. Undeployed packs simply miss and are tolerated.
    """
    caller = r.sym("DB_LoadLoadFastfilesForNewContent")
    callee = r.sym("Content_IsIndexedContentPackEnabled")
    if not caller or not callee:
        return None
    cva, csz = caller
    if not csz:
        return None
    sites = []
    for off in range(0, csz, 4):
        va = cva + off
        if _bl_dest(r.word(va), va) == callee[0]:
            sites.append(va)
    if len(sites) != 1:
        return dict(site=None, sites=sites, current=None,
                    desc="expected exactly one call site, found %d" % len(sites))
    return dict(site=sites[0], sites=sites, current=r.word(sites[0]),
                desc="every content pack reports enabled, so its load fastfile is requested")


def apply_dlc_loadgate(r):
    f = find_dlc_loadgate(r)
    if not f or not f.get('site'):
        raise RplPatchError("DLC load gate not found (%s)"
                            % (f['desc'] if f else "no Content_IsIndexedContentPackEnabled call "
                                                   "inside DB_LoadLoadFastfilesForNewContent"))
    r.set_word(f['site'], LI_R3_1)
    r.applied.append(('dlc_loadgate', f['site']))
    return f


def find_resolution(r):
    """-> dict for the render resolution immediates in R_GetWndParms.

    Sizing is centralised: two adjacent `li` immediates feed all six GfxWindowParms dimension
    stores, which reach vidConfig and then the render targets. Located by the ADJACENT PAIR
    rather than by address, because the site is at a different VA in every build.
    """
    # Modes to RECOGNISE. This list is the detector, not the menu: an RPL already patched to any
    # of these must still be located, or the tool reports "not found" for a file it patched
    # itself. Stock is 1280x720; the rest are values this patch can produce.
    for want_w, want_h in ((1280, 720), (1600, 900), (1920, 1080),
                           (640, 480), (854, 480), (960, 540), (1024, 576),
                           (1152, 648), (1366, 768), (1440, 810), (1706, 960)):
        pat_w = LI_R0_MASK | want_w
        pat_h = LI_R9_MASK | want_h
        sites = []
        for off in range(0, len(r.text) - 8, 4):
            if (struct.unpack_from(">I", r.text, off)[0] == pat_w
                    and struct.unpack_from(">I", r.text, off + 4)[0] == pat_h):
                sites.append(r.taddr + off)
        if len(sites) == 1:
            return dict(site=sites[0], sites=sites, width=want_w, height=want_h,
                        desc="internal render resolution (scaled into a legal TV buffer)")
        if len(sites) > 1:
            return dict(site=None, sites=sites, width=want_w, height=want_h,
                        desc="pattern is NOT unique (%d sites) -- refusing to guess"
                             % len(sites))
    return None


def apply_resolution(r, width, height):
    f = find_resolution(r)
    if not f or not f.get('site'):
        raise RplPatchError("resolution site not found or not unique (%s)"
                            % (f['desc'] if f else "no adjacent li pair matched a known mode"))
    if not (0 < width <= 0x7FFF and 0 < height <= 0x7FFF):
        raise RplPatchError("%dx%d does not fit the 16-bit immediates this patch edits"
                            % (width, height))
    r.set_word(f['site'], LI_R0_MASK | width)
    r.set_word(f['site'] + 4, LI_R9_MASK | height)
    r.applied.append(('resolution', f['site']))
    out = dict(f)
    out['new_width'], out['new_height'] = width, height
    return out


BACKUP_SUFFIX = '.stock'


def backup_path(path):
    return path + BACKUP_SUFFIX


def ensure_backup(path):
    """Preserve the STOCK file before the first edit. -> (backup_path, created: bool)

    ⚠ WRITE-ONCE, DELIBERATELY. Patching is incremental -- you may apply the signature patch
    today and the DLC gate next week, each time reading the already-patched file. A backup that
    overwrites itself on every run would, on the second run, replace the stock copy with a
    patched one and the true original would be gone for good. So the backup is created ONLY if
    it does not already exist, which means `<name>.stock` is always the untouched file.
    """
    import os
    import shutil
    bp = backup_path(path)
    if os.path.exists(bp):
        return bp, False
    shutil.copy2(path, bp)
    return bp, True


def write_patched(r, out_path=None, backup=True):
    """Write the patched RPL, taking a write-once backup of the original first.

    Writing in place is the normal case -- the game loads the RPL from a fixed name -- so the
    backup is what makes that safe.
    """
    out_path = out_path or r.path
    made = None
    if backup:
        import os
        if os.path.abspath(out_path) == os.path.abspath(r.path):
            made = ensure_backup(r.path)
    size = r.build(out_path)
    return dict(out=out_path, bytes=size, backup=made[0] if made else None,
                backup_created=bool(made and made[1]), applied=list(r.applied))


#: name -> (finder, human label, one-line description)
PATCHES = {
    'signature': (find_signature, 'Fastfile signature check',
                  'REQUIRED to load any edited or repacked .ff. Without it the console rejects '
                  'the file -- this is why an edit can fail on one machine and work on another.'),
    'dlc_loadgate': (find_dlc_loadgate, 'DLC load gate',
                     'Request every content pack\'s load fastfile, without marking packs owned '
                     '(which crashes the ZM globe).'),
    'resolution': (find_resolution, 'Render resolution',
                   'Change the internal render resolution. Boot-time only -- the engine has no '
                   'teardown path, so it cannot be toggled live.'),
}


def survey(path):
    """What can be patched in this RPL, and what is already patched. -> (Rpl, {name: info})"""
    r = Rpl(path)
    found = {}
    for name, (finder, label, desc) in PATCHES.items():
        try:
            f = finder(r)
        except Exception as ex:                      # a probe must never take the survey down
            f = None
            found[name] = dict(ok=False, label=label, desc=desc,
                               detail='probe failed: %s: %s' % (type(ex).__name__, ex))
            continue
        if not f:
            found[name] = dict(ok=False, label=label, desc=desc,
                               detail='not present in this RPL')
        elif not f.get('site'):
            found[name] = dict(ok=False, label=label, desc=desc, detail=f.get('desc'))
        else:
            info = dict(ok=True, label=label, desc=desc, site=f['site'], detail=f.get('desc'))
            if name == 'signature':
                info['already'] = bool(f.get('already'))
            elif name == 'dlc_loadgate':
                info['already'] = r.word(f['site']) == LI_R3_1
            elif name == 'resolution':
                info['already'] = False
                info['width'], info['height'] = f['width'], f['height']
            found[name] = info
    return r, found
