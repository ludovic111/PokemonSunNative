"""Code images to translate: the main executable and each CRO module."""

import struct

from . import cro


class Image:
    """One translatable unit. Addresses are absolute for the main executable and file offsets
    (relative to the load address) for CRO modules."""

    def __init__(self, index, name, data, base, code_ranges, const_ranges, module_relative, module=None):
        self.index = index
        self.name = name
        self.data = data
        self.base = base                  # address of data[0]
        self.code_ranges = code_ranges
        self.const_ranges = const_ranges
        self.module_relative = module_relative
        self.module = module              # parsed CRO/CRS, if any
        self.relocs = {}                  # address of a relocated word -> (image name, target address)

    def in_code(self, a, size=2):
        return any(s <= a and a + size <= e for s, e in self.code_ranges)

    def is_const(self, a, size):
        """True if [a, a+size) is read-only and never patched at load time (safe to fold)."""
        if not any(s <= a and a + size <= e for s, e in self.const_ranges):
            return False
        # ldr:ro patches some words of the main executable's text and rodata when modules load
        return not any(w in self.relocs for w in range(a & ~3, a + size, 4))

    def read32(self, a):
        o = a - self.base
        return struct.unpack_from('<I', self.data, o)[0]

    def read16(self, a):
        o = a - self.base
        return struct.unpack_from('<H', self.data, o)[0]

    def code_words(self):
        for s, e in self.code_ranges:
            for a in range(s, e - 3, 4):
                yield a, self.read32(a)


def main_image(cart, static_crs):
    cs = cart.code_set()
    code = cart.code()
    text = (cs.text_addr, cs.text_addr + cs.text_size)
    ro = (cs.ro_addr, cs.ro_addr + cs.ro_size)
    img = Image(0, 'static', code, cs.text_addr, [text], [text, ro], False, static_crs)
    img.code_set = cs
    return img


def cro_images(modules, first_index=1):
    images = []
    for k, m in enumerate(modules):
        seg_i, seg = m.code_segment()
        if seg is None or seg.size == 0:
            continue
        ro = [(s.offset, s.offset + s.size) for s in m.segments if s.type == cro.SEG_RODATA and s.size]
        # Literal words in the code segment are patched at load time; never fold them.
        img = Image(first_index + len(images), m.name, m.data, 0, [(seg.offset, seg.offset + seg.size)],
                    [], True, m)
        img.ro_ranges = ro
        images.append(img)
    return images


def resolve_relocations(images):
    """Fill image.relocs with the target of every relocated word (both internal and imported)."""
    by_name = {img.name: img for img in images}
    named = {}
    for img in images:
        for name, tag in img.module.named_exports.items():
            named.setdefault(name, (img, tag))

    def seg_addr(img, tag):
        seg, off = tag
        return img.module.segments[seg].offset + off

    for img in images:
        m = img.module
        for r in ([] if m.is_static else m.internal_relocations):
            where = m.segments[r.segment].offset + r.offset
            if r.type in (cro.R_ABS32, cro.R_TARGET1):
                tseg = m.segments[r.symbol_segment]
                img.relocs[where] = (img.name, tseg.offset + r.addend, tseg.type)
        for imp in m.imports:
            if imp.kind == 'named':
                hit = named.get(imp.key)
                if not hit:
                    continue
                timg, tag = hit
            else:
                timg = by_name.get(imp.module)
                if timg is None:
                    continue
                if imp.kind == 'indexed':
                    if imp.key >= len(timg.module.indexed_exports):
                        continue
                    tag = timg.module.indexed_exports[imp.key]
                else:
                    tag = imp.key
            if tag is None:
                continue
            target = seg_addr(timg, tag)
            ttype = timg.module.segments[tag[0]].type
            for r in imp.relocations:
                if r.type not in (cro.R_ABS32, cro.R_TARGET1):
                    continue
                where = m.segments[r.segment].offset + r.offset
                img.relocs[where] = (timg.name, target + r.addend, ttype)
