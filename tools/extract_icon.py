"""Write the game's 48x48 icon (from the cartridge's SMDH) as a PNG, for the desktop launcher.

usage: python3 extract_icon.py GAME.3ds OUT.png
"""

import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recomp import rom  # noqa: E402


def morton(x, y):
    m = 0
    for i in range(3):
        m |= ((x >> i) & 1) << (2 * i) | ((y >> i) & 1) << (2 * i + 1)
    return m


def png(width, height, rgb):
    raw = b''.join(b'\x00' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind, data):
        c = struct.pack('>I', len(data)) + kind + data
        return c + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def main():
    cart = rom.Cartridge(sys.argv[1])
    off, size = cart.exefs_files()['icon']
    smdh = cart.read(off, size)
    icon = smdh[0x24C0:0x24C0 + 48 * 48 * 2]
    out = bytearray(48 * 48 * 3)
    for y in range(48):
        for x in range(48):
            tile = (y // 8) * 6 + x // 8
            i = (tile * 64 + morton(x % 8, y % 8)) * 2
            v = icon[i] | (icon[i + 1] << 8)
            r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
            o = (y * 48 + x) * 3
            out[o:o + 3] = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    with open(sys.argv[2], 'wb') as fp:
        fp.write(png(48, 48, bytes(out)))


if __name__ == '__main__':
    main()
