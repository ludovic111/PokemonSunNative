"""Parse CRO / CRS dynamic modules (the 3DS equivalent of shared libraries).

Layout follows Azahar's src/core/hle/service/ldr_ro/cro_helper.h. All offsets stored in the
file are relative to the start of the file; at load time ldr:ro rebases them to the module's
address. For static.crs (describing the main executable) segment offsets are absolute addresses.
"""

import struct
from dataclasses import dataclass, field

HASH_SIZE = 0x80

FIELDS = [
    'Magic', 'NameOffset', 'NextCRO', 'PreviousCRO', 'FileSize', 'BssSize', 'FixedSize',
    'UnknownZero', 'UnkSegmentTag', 'OnLoadSegmentTag', 'OnExitSegmentTag',
    'OnUnresolvedSegmentTag', 'CodeOffset', 'CodeSize', 'DataOffset', 'DataSize',
    'ModuleNameOffset', 'ModuleNameSize', 'SegmentTableOffset', 'SegmentNum',
    'ExportNamedSymbolTableOffset', 'ExportNamedSymbolNum', 'ExportIndexedSymbolTableOffset',
    'ExportIndexedSymbolNum', 'ExportStringsOffset', 'ExportStringsSize', 'ExportTreeTableOffset',
    'ExportTreeNum', 'ImportModuleTableOffset', 'ImportModuleNum', 'ExternalRelocationTableOffset',
    'ExternalRelocationNum', 'ImportNamedSymbolTableOffset', 'ImportNamedSymbolNum',
    'ImportIndexedSymbolTableOffset', 'ImportIndexedSymbolNum', 'ImportAnonymousSymbolTableOffset',
    'ImportAnonymousSymbolNum', 'ImportStringsOffset', 'ImportStringsSize',
    'StaticAnonymousSymbolTableOffset', 'StaticAnonymousSymbolNum', 'InternalRelocationTableOffset',
    'InternalRelocationNum', 'StaticRelocationTableOffset', 'StaticRelocationNum',
]

SEG_CODE, SEG_RODATA, SEG_DATA, SEG_BSS = range(4)

# Relocation types
R_NONE = 0
R_ABS32 = 2
R_REL32 = 3
R_THM_CALL = 10
R_ARM_CALL = 28
R_ARM_JUMP24 = 29
R_TARGET1 = 38
R_PREL31 = 42


@dataclass
class Segment:
    offset: int
    size: int
    type: int


@dataclass
class Relocation:
    segment: int       # segment index of the patched location
    offset: int        # offset of the patched location within that segment
    type: int
    addend: int
    symbol_segment: int = -1  # internal relocations: segment the value points into


@dataclass
class Import:
    kind: str          # 'named', 'indexed' or 'anonymous'
    module: str        # exporting module ('' when resolved by name across all modules)
    key: object        # name, index, or (segment, offset)
    relocations: list = field(default_factory=list)


@dataclass
class Module:
    name: str
    data: bytes
    is_static: bool
    segments: list
    named_exports: dict           # name -> (segment, offset)
    indexed_exports: list         # [(segment, offset)]
    imports: list                 # [Import]
    internal_relocations: list    # [Relocation]
    on_load: tuple
    on_exit: tuple
    on_unresolved: tuple

    def segment_file_offset(self, seg):
        return self.segments[seg].offset

    def code_segment(self):
        for i, s in enumerate(self.segments):
            if s.type == SEG_CODE:
                return i, s
        return None, None


def _tag(raw):
    if raw == 0xFFFFFFFF:
        return None
    return (raw & 0xF, raw >> 4)


def _cstr(data, off):
    end = data.index(b'\0', off)
    return data[off:end].decode('ascii', 'replace')


def parse(data: bytes, is_static=False) -> Module:
    hdr = {name: struct.unpack_from('<I', data, HASH_SIZE + i * 4)[0] for i, name in enumerate(FIELDS)}
    magic = struct.pack('<I', hdr['Magic'])
    if magic not in (b'CRO0', b'FIXD'):
        raise ValueError('not a CRO/CRS module')

    def table(name, size, count_field=None):
        off = hdr[name + 'TableOffset']
        num = hdr[(count_field or name) + 'Num']
        return [data[off + i * size:off + (i + 1) * size] for i in range(num)]

    segments = []
    for e in table('Segment', 12):
        off, size, typ = struct.unpack('<III', e)
        segments.append(Segment(off, size, typ))

    named = {}
    for e in table('ExportNamedSymbol', 8):
        name_off, tag = struct.unpack('<II', e)
        named[_cstr(data, name_off)] = _tag(tag)
    indexed = [_tag(struct.unpack('<I', e)[0]) for e in table('ExportIndexedSymbol', 4)]

    ext_off = hdr['ExternalRelocationTableOffset']

    def batch(off):
        relocs = []
        i = (off - ext_off) // 12
        while True:
            tag, typ, end, _resolved, _pad, addend = struct.unpack_from('<IBBBBi', data, ext_off + i * 12)
            t = _tag(tag)
            relocs.append(Relocation(t[0], t[1], typ, addend))
            if end:
                return relocs
            i += 1

    imports = []
    for e in table('ImportNamedSymbol', 8):
        name_off, batch_off = struct.unpack('<II', e)
        imports.append(Import('named', '', _cstr(data, name_off), batch(batch_off)))
    for e in table('ImportModule', 20):
        name_off, idx_off, idx_num, anon_off, anon_num = struct.unpack('<IIIII', e)
        mod = _cstr(data, name_off)
        for i in range(idx_num):
            index, batch_off = struct.unpack_from('<II', data, idx_off + i * 8)
            imports.append(Import('indexed', mod, index, batch(batch_off)))
        for i in range(anon_num):
            tag, batch_off = struct.unpack_from('<II', data, anon_off + i * 8)
            imports.append(Import('anonymous', mod, _tag(tag), batch(batch_off)))

    internal = []
    for e in table('InternalRelocation', 12):
        tag, typ, sym_seg, _a, _b, addend = struct.unpack('<IBBBBi', e)
        t = _tag(tag)
        internal.append(Relocation(t[0], t[1], typ, addend, sym_seg))

    name = _cstr(data, hdr['ModuleNameOffset']) if hdr['ModuleNameSize'] else ''
    return Module(name, data, is_static, segments, named, indexed, imports, internal,
                  _tag(hdr['OnLoadSegmentTag']), _tag(hdr['OnExitSegmentTag']),
                  _tag(hdr['OnUnresolvedSegmentTag']))
