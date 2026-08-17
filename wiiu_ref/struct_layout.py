#!/usr/bin/env python3
"""
Compute 32-bit struct layouts (field offsets/sizes/alignment) from OAT's
T6_Assets.h. Foundation for the native Wii U zone walker: the walker needs,
per struct, the byte size and the offset+type of every pointer member so it
can follow the graph. Big-endian is handled at read time; layout is arch (32-bit).
"""
import re, sys

# base primitive types: name -> (size, align)
PRIM = {
    'char': (1, 1), 'signed char': (1, 1), 'unsigned char': (1, 1), 'bool': (1, 1),
    'uint8_t': (1, 1), 'int8_t': (1, 1), 'byte': (1, 1),
    'short': (2, 2), 'unsigned short': (2, 2), 'int16_t': (2, 2), 'uint16_t': (2, 2),
    'ScriptString': (2, 2), 'LeafBrush': (2, 2),
    'int': (4, 4), 'unsigned int': (4, 4), 'unsigned': (4, 4), 'int32_t': (4, 4),
    'uint32_t': (4, 4), 'float': (4, 4), 'long': (4, 4), 'unsigned long': (4, 4),
    'vec_t': (4, 4), 'scr_string_t': (2, 2),
    'int64_t': (8, 8), 'uint64_t': (8, 8), 'double': (8, 8),
    'vec2_t': (8, 4), 'vec3_t': (12, 4), 'vec4_t': (16, 4),
}
PTR = (4, 4)  # 32-bit pointer

# 64-bit scalar base types; on console these align to 4 (see type_info). Kept narrow to
# scalars so structs/unions (e.g. custom_m128) retain their computed alignment.
_SCALAR64 = {'int64_t', 'uint64_t', 'long long', 'unsigned long long', 'double'}


class Layout:
    def __init__(self, header_path, console=False):
        # console=True omits the ID3D11 GPU-handle pointer fields (vs/ps/vb0/indexBuffer/
        # decl[]/basemap/state), which genuine Wii U (T6 v148) structs do not carry.
        self.console = console
        self.src = open(header_path, encoding='utf-8', errors='replace').read()
        self.structs = {}   # name -> dict(fields=[...], size, align)
        self.enums = set()
        self.typedefs = {}  # alias -> (base_name, size, align, array)
        self._scan_enums()
        self._scan_typedefs()
        self._scan_structs()

    # ---- pre-scan enums (size from ": base", default 4) + named constant values ----
    def _scan_enums(self):
        self.enum_size = {}   # enum name -> byte size
        for m in re.finditer(r'enum\s+(\w+)\s*(?::\s*([\w ]+?)\s*)?\{', self.src):
            name, base = m.group(1), (m.group(2) or '').strip()
            self.enums.add(name)
            self.enum_size[name] = PRIM.get(base, (4, 4))[0] if base else 4
        self.enum_vals = {}
        for m in re.finditer(r'enum\s+\w+\s*(?::\s*[\w ]+?\s*)?\{(.*?)\}', self.src, re.S):
            v = 0
            for line in m.group(1).split(','):
                line = line.split('//')[0].strip()
                if not line:
                    continue
                if '=' in line:
                    nm, val = line.split('=', 1)
                    nm = nm.strip(); val = val.strip()
                    try:
                        v = int(val, 0)
                    except ValueError:
                        continue
                else:
                    nm = line
                if re.match(r'^[A-Za-z_]\w*$', nm):
                    self.enum_vals[nm] = v
                v += 1

    # ---- typedefs incl tdef_align32(N) ----
    def _scan_typedefs(self):
        # typedef tdef_align32(N) BaseType AliasName;  (aligned char blobs etc.)
        for m in re.finditer(r'typedef\s+tdef_align(?:32)?\((\d+)\)\s+(\w+)\s+(\w+)\s*;', self.src):
            align, base, alias = int(m.group(1)), m.group(2), m.group(3)
            bs, ba = self._base_size_align(base)
            self.typedefs[alias] = (base, bs, max(align, 1) if False else bs, 0)
            # tdef_align32(N) on a char means array element align; treat alias size=base size, align=N
            self.typedefs[alias] = (base, bs, align, 0)
        # plain typedef Base Alias;
        for m in re.finditer(r'typedef\s+(\w[\w\s]*?)\s+(\w+)\s*;', self.src):
            base, alias = m.group(1).strip(), m.group(2)
            if alias in self.typedefs or 'tdef_align' in base:
                continue
            self.typedefs[alias] = (base, None, None, 0)

    def _base_size_align(self, t):
        t = t.strip()
        if t in PRIM:
            return PRIM[t]
        if t in self.enums:
            return (self.enum_size.get(t, 4), self.enum_size.get(t, 4))
        return (None, None)  # resolved later (struct/typedef)

    # ---- structs/unions ----
    def _scan_structs(self):
        # capture: struct [type_align32(N)] Name { ... };  and unions
        pat = re.compile(
            r'(struct|union)\s+(?:type_align(?:32)?\((\d+)\)\s+)?(\w+)\s*\{(.*?)\n\s*\};',
            re.S)
        for m in pat.finditer(self.src):
            kind, align_attr, name, body = m.group(1), m.group(2), m.group(3), m.group(4)
            self.structs[name] = {
                'kind': kind, 'align_attr': int(align_attr) if align_attr else None,
                'body': body, 'fields': None, 'size': None, 'align': None,
            }

    # ---- resolve a type token to (size, align, is_pointer, base) ----
    def type_info(self, decl, name_and_array):
        """decl = type part; name_and_array = 'name' or 'name[3]' or '*name' etc."""
        is_ptr = '*' in name_and_array or decl.endswith('*')
        arr = 1
        for a in re.findall(r'\[(\d+)\]', name_and_array):
            arr *= int(a)
        base = decl.replace('const', '').replace('*', '').strip()
        base = re.sub(r'gcc_align32?\(\d+\)|volatile', '', base).strip()
        base = re.sub(r'\bstruct\b|\bunion\b|\benum\b', '', base).strip()
        if is_ptr:
            sz, al = PTR
            return sz * arr, al, True, base, arr
        # resolve base
        sz, al = self._resolve(base)
        # console (WiiU v148) aligns 64-bit scalars to 4, not 8. Verified against the
        # genuine zone + menudef_t_t6_load_db.cpp SwapEndianness offsets: e.g. menuDef_t
        # `gcc_align32(8) uint64_t showBits` sits at +292 on console vs +296 on PC (visibleExp
        # ends at 292; PC pads to an 8-aligned 296, console does not). The gcc_align32(8) attr
        # is stripped above, so the only remaining 8-align source is the uint64/int64/double
        # natural alignment -- clamp it to 4 for console. Structs/unions keep their own align.
        if self.console and al == 8 and base in _SCALAR64:
            al = 4
        return sz * arr, al, False, base, arr

    def _resolve(self, base):
        base = base.strip()
        if base in PRIM:
            return PRIM[base]
        if base in self.enums:
            sz = self.enum_size.get(base, 4)
            return (sz, sz)
        # typedef chain
        if base in self.typedefs:
            b, sz, al, _ = self.typedefs[base]
            if sz is not None:
                return (sz, al)
            return self._resolve(b)
        if base in self.structs:
            self._compute(base)
            s = self.structs[base]
            return (s['size'], s['align'])
        raise KeyError("unknown type: %r" % base)

    # ---- compute a struct's field offsets ----
    def _compute(self, name):
        s = self.structs[name]
        if s['fields'] is not None:
            return s
        fields = []
        off = 0
        maxal = 1
        is_union = s['kind'] == 'union'
        for line in s['body'].split('\n'):
            line = line.split('//')[0].strip().rstrip(';').strip()
            if not line or line.startswith('#'):
                continue
            # split into type + declarator(s)
            m = re.match(r'(.+?)\s*([A-Za-z_]\w*(?:\s*\[\d+\])*)$', line)
            if not m:
                m = re.match(r'(.+?\*)\s*([A-Za-z_]\w*(?:\s*\[\d+\])*)$', line)
            if not m:
                continue
            decl, nm = m.group(1).strip(), m.group(2).strip()
            try:
                sz, al, is_ptr, base, arr = self.type_info(decl, ('*' if decl.endswith('*') else '') + nm)
            except KeyError as e:
                fields.append({'name': nm, 'error': str(e)})
                continue
            # console (WiiU v148) omits ID3D11 GPU-handle pointer fields entirely
            if self.console and is_ptr and 'D3D11' in base:
                continue
            if is_union:
                foff = 0
                off = max(off, sz)
            else:
                off = (off + al - 1) & ~(al - 1)
                foff = off
                off += sz
            maxal = max(maxal, al)
            bare = re.sub(r'\s*\[\d+\]', '', nm).replace('*', '')
            fields.append({'name': bare, 'offset': foff, 'size': sz,
                           'align': al, 'is_ptr': is_ptr, 'base': base, 'arr': arr,
                           'ptr2': is_ptr and (decl + nm).count('*') >= 2})
        if s['align_attr']:
            maxal = max(maxal, s['align_attr'])
        size = off if is_union else off
        size = (size + maxal - 1) & ~(maxal - 1)
        s['fields'], s['size'], s['align'] = fields, size, maxal
        return s

    def get(self, name):
        return self._compute(name)


def main():
    hdr = sys.argv[1] if len(sys.argv) > 1 else \
        "../tools/ref_oat/src/Common/Game/T6/T6_Assets.h"
    L = Layout(hdr)
    checks = [('XSurface', 80), ('GfxPackedVertex', 32), ('KeyValuePairs', 12),
              ('XModel', None), ('Glasses', None), ('MaterialInfo', None),
              ('XSurfaceVertexInfo', 16), ('XModelLodInfo', None)]
    print("%-24s %6s %6s  %s" % ("struct", "size", "align", "status"))
    for nm, expect in checks:
        try:
            s = L.get(nm)
            ok = "" if expect is None else ("OK" if s['size'] == expect else "EXPECT %d" % expect)
            print("%-24s %6d %6d  %s" % (nm, s['size'], s['align'], ok))
        except Exception as e:
            print("%-24s   ERROR %s" % (nm, e))
    # detail for XSurface
    print("\nXSurface fields:")
    for f in L.get('XSurface')['fields']:
        if 'error' in f:
            print("  !", f['name'], f['error'])
        else:
            print("  +0x%02x %-16s %s%s size=%d" % (
                f['offset'], f['name'], f['base'], '*' if f['is_ptr'] else '', f['size']))


if __name__ == '__main__':
    main()
