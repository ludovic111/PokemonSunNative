"""Translate Pokémon Sun / Moon (3DS) into C++.

usage: python3 -m recomp ROM.3ds OUTDIR [--only NAME[,NAME...]]
"""

import argparse
import os
import sys
import time

from . import analysis, cro, emit, image, rom

HINTS_DIR = os.path.join(os.path.dirname(__file__), 'hints')


def load_hints(title_id, module):
    """Function entries found at run time that static analysis cannot see.
    Lines: `<module> <hex address | thumb>`."""
    path = os.path.join(HINTS_DIR, f'{title_id:016X}.txt')
    out = []
    if os.path.exists(path):
        for line in open(path):
            line = line.split('#')[0].split()
            if len(line) == 2 and line[0] == module:
                out.append(int(line[1], 16))
    return out


TITLES = {
    0x0004000000164800: 'Pokémon Sun',
    0x0004000000175E00: 'Pokémon Moon',
}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='recomp')
    ap.add_argument('rom')
    ap.add_argument('outdir')
    ap.add_argument('--only', help='comma-separated module names to translate (debugging)')
    args = ap.parse_args(argv)
    t0 = time.time()
    log = lambda *a: print(*a, flush=True)

    cart = rom.Cartridge(args.rom)
    title = TITLES.get(cart.title_id)
    if not title:
        sys.exit(f'unsupported game (title ID {cart.title_id:016X}); expected Pokémon Sun or Moon')
    log(f'{title} ({cart.product_code})')

    modules = []
    static = None
    for f in cart.romfs_files():
        if f.path.endswith('.crs'):
            static = cro.parse(cart.read(f.offset, f.size), is_static=True)
            static.name = 'static'
        elif f.path.endswith('.cro'):
            modules.append(cro.parse(cart.read(f.offset, f.size)))
    if static is None:
        sys.exit('static.crs not found')
    main_img = image.main_image(cart, static)
    images = [main_img] + image.cro_images(modules)
    image.resolve_relocations(images)
    log(f'{len(images) - 1} modules, relocations resolved ({time.time() - t0:.1f}s)')

    # Function pointers that other modules hold into each image's code
    incoming = {img.name: set() for img in images}
    for img in images:
        for where, (tname, target, ttype) in img.relocs.items():
            if ttype == cro.SEG_CODE:
                incoming[tname].add(target)

    os.makedirs(args.outdir, exist_ok=True)
    only = set(args.only.split(',')) if args.only else None
    results = []
    for img in images:
        m = img.module
        seeds = set(incoming[img.name])
        candidates = set()
        for name, tag in m.named_exports.items():
            if tag and m.segments[tag[0]].type == cro.SEG_CODE:
                seeds.add(m.segments[tag[0]].offset + tag[1])
        for tag in m.indexed_exports:
            if tag and m.segments[tag[0]].type == cro.SEG_CODE:
                seeds.add(m.segments[tag[0]].offset + tag[1])
        for tag in (m.on_load, m.on_exit, m.on_unresolved):
            if tag and tag[0] < len(m.segments) and m.segments[tag[0]].type == cro.SEG_CODE:
                seeds.add(m.segments[tag[0]].offset + tag[1])
        if img is main_img:
            cs = img.code_set
            seeds.add(cs.text_addr)
            lo, hi = cs.text_addr, cs.text_addr + cs.text_size
            for start, size in ((cs.ro_addr, cs.ro_size), (cs.data_addr, cs.data_size)):
                run = []
                for a in range(start, start + size - 3, 4):
                    v = img.read32(a)
                    if lo <= (v & ~1) < hi and (v & 3) in (0, 1):
                        candidates.add(v)
                    # Tables of self-relative pointers (C++ static constructors, .init_array)
                    r = (a + v) & 0xFFFFFFFF
                    if lo <= (r & ~1) < hi and (r & 3) in (0, 1):
                        run.append(r)
                        continue
                    if len(run) >= 4:
                        candidates.update(run)
                    run = []
                if len(run) >= 4:
                    candidates.update(run)
        seeds.update(load_hints(cart.title_id, img.name))
        if only and img.name not in only:
            results.append((set(), {}))
            continue
        dec, entries = analysis.discover(img, seeds, candidates)
        _, labels = emit.write_image(img, dec, entries, args.outdir, log=log)
        results.append((entries, labels))
    emit.write_tables(images, results, args.outdir)
    log(f'done in {time.time() - t0:.1f}s')


if __name__ == '__main__':
    main()
