"""Read a decrypted 3DS cartridge image (.3ds / .cci): ExHeader, ExeFS code and RomFS files."""

import struct
from dataclasses import dataclass

MEDIA_UNIT = 0x200


def u32(b, o=0):
    return struct.unpack_from('<I', b, o)[0]


def u64(b, o=0):
    return struct.unpack_from('<Q', b, o)[0]


class RomError(Exception):
    pass


@dataclass
class CodeSet:
    name: str
    text_addr: int
    text_size: int
    ro_addr: int
    ro_size: int
    data_addr: int
    data_size: int
    bss_size: int
    compressed: bool
    stack_size: int


@dataclass
class RomFile:
    path: str
    offset: int  # absolute offset in the image
    size: int


def blz_decompress(data: bytes) -> bytes:
    """Nintendo's backwards LZ77, used for compressed .code sections."""
    footer = data[-8:]
    buffer_top_and_bottom = u32(footer, 0)
    extra = u32(footer, 4)
    out = bytearray(data) + bytearray(extra)
    src = len(data) - (buffer_top_and_bottom >> 24)
    dst = len(out)
    end = len(data) - (buffer_top_and_bottom & 0xFFFFFF)
    while src > end:
        src -= 1
        flags = data[src]
        for _ in range(8):
            if src <= end:
                break
            if flags & 0x80:
                src -= 2
                pair = data[src] | (data[src + 1] << 8)
                size = (pair >> 12) + 3
                disp = (pair & 0xFFF) + 3
                for _ in range(size):
                    dst -= 1
                    out[dst] = out[dst + disp]
            else:
                src -= 1
                dst -= 1
                out[dst] = data[src]
            flags = (flags << 1) & 0xFF
    return bytes(out)


class Cartridge:
    def __init__(self, path: str):
        self.path = path
        self.f = open(path, 'rb')
        ncsd = self.read(0, 0x200)
        if ncsd[0x100:0x104] != b'NCSD':
            raise RomError('not a 3DS cartridge image (.3ds / .cci)')
        self.ncch = u32(ncsd, 0x120) * MEDIA_UNIT
        h = self.read(self.ncch, 0x200)
        if h[0x100:0x104] != b'NCCH':
            raise RomError('cartridge has no main NCCH partition')
        self.header = h
        self.title_id = u64(h, 0x118)
        self.product_code = h[0x150:0x160].rstrip(b'\0').decode('ascii', 'replace')
        if not (h[0x18F] & 0x4):
            raise RomError('this dump is encrypted; a decrypted dump is required')
        self.exheader = self.read(self.ncch + 0x200, 0x400)
        self.exefs_off = self.ncch + u32(h, 0x1A0) * MEDIA_UNIT
        self.romfs_off = self.ncch + u32(h, 0x1B0) * MEDIA_UNIT
        self.romfs_size = u32(h, 0x1B4) * MEDIA_UNIT

    def read(self, off, size):
        self.f.seek(off)
        data = self.f.read(size)
        if len(data) != size:
            raise RomError('image is truncated')
        return data

    def code_set(self) -> CodeSet:
        ex = self.exheader
        return CodeSet(
            name=ex[0:8].rstrip(b'\0').decode('ascii', 'replace'),
            text_addr=u32(ex, 0x10), text_size=u32(ex, 0x18),
            ro_addr=u32(ex, 0x20), ro_size=u32(ex, 0x28),
            data_addr=u32(ex, 0x30), data_size=u32(ex, 0x38),
            bss_size=u32(ex, 0x3C), compressed=bool(ex[0xD] & 1),
            stack_size=u32(ex, 0x1C),
        )

    def exefs_files(self):
        hdr = self.read(self.exefs_off, 0x200)
        files = {}
        for i in range(10):
            name = hdr[i * 16:i * 16 + 8].rstrip(b'\0').decode('ascii', 'replace')
            off, size = struct.unpack_from('<II', hdr, i * 16 + 8)
            if name:
                files[name] = (self.exefs_off + 0x200 + off, size)
        return files

    def code(self) -> bytes:
        """The .code section, decompressed: text, rodata and data, each page-aligned."""
        off, size = self.exefs_files()['.code']
        data = self.read(off, size)
        if self.code_set().compressed:
            data = blz_decompress(data)
        return data

    def romfs_files(self):
        ivfc = self.read(self.romfs_off, 0x60)
        if ivfc[0:4] != b'IVFC':
            raise RomError('RomFS is missing or damaged')
        master_size = u32(ivfc, 0x08)
        block = 1 << u32(ivfc, 0x4C)
        l3 = self.romfs_off + ((0x60 + master_size + block - 1) // block) * block
        hdr = struct.unpack_from('<10I', self.read(l3, 0x28))
        dir_meta = self.read(l3 + hdr[3], hdr[4])
        file_meta = self.read(l3 + hdr[7], hdr[8])
        data_off = l3 + hdr[9]

        def dir_name(o):
            if o == 0:
                return ''
            nl = u32(dir_meta, o + 0x14)
            return dir_name(u32(dir_meta, o)) + '/' + dir_meta[o + 0x18:o + 0x18 + nl].decode('utf-16le')

        files = []
        o = 0
        while o < len(file_meta):
            parent = u32(file_meta, o)
            off, size = struct.unpack_from('<QQ', file_meta, o + 8)
            nl = u32(file_meta, o + 0x1C)
            name = file_meta[o + 0x20:o + 0x20 + nl].decode('utf-16le')
            files.append(RomFile(dir_name(parent) + '/' + name, data_off + off, size))
            o += 0x20 + ((nl + 3) & ~3)
        return files
