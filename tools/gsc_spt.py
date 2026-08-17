#!/usr/bin/env python3
"""T6 compiled-script (ScriptParseTree) binary reader — big-endian / Wii U.

Header layout, reversed from genuine console scripts and cross-checked on the whole
patch_mp corpus (every table start == the previous table's end, EOF-exact):

    0x00 u64  magic                       80 47 53 43 0d 0a 00 06   ("\\x80GSC\\r\\n\\0\\x06")
    0x08 u32  source_crc
    0x0C u32  include_offset
    0x10 u32  animtree_offset
    0x14 u32  cseg_offset
    0x18 u32  stringtablefixup_offset
    0x1C u32  exports_offset
    0x20 u32  imports_offset
    0x24 u32  fixup_offset
    0x28 u32  profile_offset
    0x2C u32  cseg_size
    0x30 u16  name            (string offset of the embedded script path)
    0x32 u16  stringtablefixup_count
    0x34 u16  exports_count
    0x36 u16  imports_count
    0x38 u16  fixup_count
    0x3A u16  profile_count
    0x3C u8   include_count
    0x3D u8   animtree_count
    0x3E u8   flags
    0x40      string pool starts here

Record layouts:
    export  12 B  {crc u32, addr u32, name_off u16, params u8, flags u8}
    import   8 B  {name_off u16, ns_off u16, num_addr u16, params u8, flags u8}
                  followed by num_addr x u32 call-site addresses
    include       include_count x u32 string offsets
    strtabfixup   4 B  {str_off u16, num_addr u8, type u8} + num_addr x u32   (VARIABLE)
    animtree      8 B  {name_off u16, num_single u16, num_pairs u16, pad u16}
                  + num_single x u32 addr + num_pairs x {u32 name_off, u32 addr}  (VARIABLE)
                  The section's END is BOUNDARY-DRIVEN: stringtablefixup_offset is
                  authoritative, never a computed size (see parse()).

An empty table's offset collapses onto the next table's offset.

Import flags (per call site record):
    0x02  global/function call      0x04  method call (on an entity)
    0x10  the call site is inside a /# dev block #/
"""
import struct, os, re

MAGIC = b'\x80GSC\r\n\x00\x06'


class Import:
    __slots__ = ('name', 'ns', 'params', 'flags', 'num_addr')

    def __init__(self, name, ns, params, flags, num_addr):
        self.name, self.ns, self.params = name, ns, params
        self.flags, self.num_addr = flags, num_addr

    @property
    def dev_only(self):
        return bool(self.flags & 0x10)

    def __repr__(self):
        return '%s::%s/%d' % (self.ns or '', self.name, self.params)


class Export:
    __slots__ = ('name', 'params', 'flags', 'addr', 'crc')

    def __init__(self, name, params, flags, addr, crc):
        self.name, self.params, self.flags = name, params, flags
        self.addr, self.crc = addr, crc

    def __repr__(self):
        return '%s/%d' % (self.name, self.params)


class Script:
    def __init__(self, path, data):
        self.path = path
        self.data = data
        self.parse()

    def _str(self, off):
        if off <= 0 or off >= len(self.data):
            return ''
        end = self.data.find(b'\x00', off)
        if end < 0:
            end = len(self.data)
        return self.data[off:end].decode('latin-1')

    def parse(self):
        d = self.data
        if not d.startswith(MAGIC):
            raise ValueError('not a T6 console script: %s' % self.path)
        (self.crc, self.include_off, self.animtree_off, self.cseg_off,
         self.stfix_off, self.exports_off, self.imports_off,
         self.fixup_off, self.profile_off, self.cseg_size) = struct.unpack_from('>10I', d, 8)
        (self.name_off, self.stfix_n, self.exports_n, self.imports_n,
         self.fixup_n, self.profile_n) = struct.unpack_from('>6H', d, 0x30)
        self.include_n, self.animtree_n, self.flags = struct.unpack_from('>3B', d, 0x3C)

        self.name = self._str(self.name_off)

        # includes: u32 string offsets (NOT u16)
        self.includes = []
        for i in range(self.include_n):
            (off,) = struct.unpack_from('>I', d, self.include_off + i * 4)
            self.includes.append(self._str(off).replace('\\', '/').lower())

        # exports
        self.exports = []
        for i in range(self.exports_n):
            crc, addr, noff, params, flags = struct.unpack_from('>IIHBB', d, self.exports_off + i * 12)
            self.exports.append(Export(self._str(noff).lower(), params, flags, addr, crc))

        # imports (variable length)
        self.imports = []
        p = self.imports_off
        for i in range(self.imports_n):
            noff, nsoff, num_addr, params, flags = struct.unpack_from('>HHHBB', d, p)
            p += 8 + num_addr * 4
            ns = self._str(nsoff).replace('\\', '/').lower() if nsoff else ''
            self.imports.append(Import(self._str(noff).lower(), ns, params, flags, num_addr))
        self._imports_end = p

        # animtrees sit between imports and stringtablefixup when present.
        # Entry: {name_off u16, num_single u16, num_pairs u16, pad u16} followed by
        # num_single x u32 bare code addresses, then num_pairs x {u32 name_off, u32 addr}.
        # num_single was long misread as pad, which made the entry look like it had a
        # variable-size header (8, 12 and 16 + 8n all measured; refuted fixed formulas).
        # The section END is BOUNDARY-DRIVEN regardless: stringtablefixup_offset is
        # authoritative, so an unmodelled variant can never push the walk past the section.
        self.animtrees = []
        p = self.animtree_off
        for i in range(self.animtree_n):
            if p + 8 > self.stfix_off:
                break
            noff, n_single, n_pairs, _ = struct.unpack_from('>4H', d, p)
            p += 8 + n_single * 4 + n_pairs * 8
            self.animtrees.append(self._str(noff))
        self._animtree_walk_end = p
        self._animtree_end = self.stfix_off

        # string-table fixups: variable length, same shape as imports
        self.strrefs = []
        p = self.stfix_off
        for i in range(self.stfix_n):
            soff, num_addr, ty = struct.unpack_from('>HBB', d, p)
            p += 4 + num_addr * 4
            self.strrefs.append(self._str(soff))
        self._strfix_end = p

    def selfcheck(self):
        """Every table must end exactly where the next one starts. Proves the layout."""
        errs = []
        if self.cseg_off + self.cseg_size != self.exports_off:
            errs.append('cseg end %#x != exports %#x' % (self.cseg_off + self.cseg_size, self.exports_off))
        if self.exports_off + self.exports_n * 12 != self.imports_off:
            errs.append('exports end %#x != imports %#x' % (self.exports_off + self.exports_n * 12, self.imports_off))
        if self._imports_end != self.animtree_off:
            errs.append('imports end %#x != animtree %#x' % (self._imports_end, self.animtree_off))
        if self.animtree_off > self.stfix_off:
            errs.append('animtree %#x starts after stringtablefixup %#x'
                        % (self.animtree_off, self.stfix_off))
        elif self._animtree_walk_end != self.stfix_off:
            # the walk landing exactly on the next section proves the entry model;
            # a miss means an unmodelled entry variant, not a broken file extent
            errs.append('animtree walk end %#x != stringtablefixup %#x (unmodelled entry variant)'
                        % (self._animtree_walk_end, self.stfix_off))
        if self._strfix_end != self.fixup_off:
            errs.append('strfix end %#x != fixup %#x' % (self._strfix_end, self.fixup_off))
        if self.include_off + self.include_n * 4 != self.cseg_off:
            errs.append('include end %#x != cseg %#x' % (self.include_off + self.include_n * 4, self.cseg_off))
        return errs


def load_dir(root):
    """Load every .gsc/.csc under root. Returns {relpath: Script}."""
    out = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(('.gsc', '.csc')):
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root).replace('\\', '/').lower()
            try:
                out[rel] = Script(rel, open(full, 'rb').read())
            except Exception as e:
                print('  ! skip %s: %s' % (rel, e))
    return out


if __name__ == '__main__':
    import sys
    root = sys.argv[1]
    scripts = load_dir(root)
    bad = 0
    for rel, s in sorted(scripts.items()):
        errs = s.selfcheck()
        if errs:
            bad += 1
            print('%-55s %s' % (rel, '; '.join(errs)))
    print('%d scripts, %d layout failures' % (len(scripts), bad))
