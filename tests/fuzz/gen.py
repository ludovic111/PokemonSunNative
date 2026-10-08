"""Generate random ARM/Thumb/VFP instructions, translated by the recompiler, for the
differential test against dynarmic (tests/fuzz/harness.cpp).

usage: python3 gen.py OUT.cpp [COUNT] [SEED]
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))

from recomp import arm, thumb  # noqa: E402

BASE = 0x00010000

# (mask, value) templates: random bits are filled in where mask is 0
ARM_TEMPLATES = [
    (0x0E000000, 0x00000000),  # data processing reg / misc / multiply / extra ld-st
    (0x0E000000, 0x02000000),  # data processing imm
    (0x0E000000, 0x04000000),  # ld/st imm
    (0x0E000010, 0x06000000),  # ld/st reg
    (0x0E000010, 0x06000010),  # media
    (0x0F8000F0, 0x06000010),  # parallel add/sub
    (0x0F800010, 0x06800010),  # pack/sat/extend/rev
    (0x0F8000F0, 0x07000010),  # signed multiply
    (0x0FC000F0, 0x00000090),  # mul/mla
    (0x0F8000F0, 0x00800090),  # long multiply
    (0x0FB00FF0, 0x01000090),  # swp
    (0x0E000090, 0x00000090),  # extra load/store
    (0x0F900090, 0x01000080),  # smulxy family
    (0x0F9000F0, 0x01000050),  # qadd family
    (0x0FFF0FF0, 0x016F0F10),  # clz
    (0x0FFFFFF0, 0x012FFF10),  # bx
    (0x0FFFFFF0, 0x012FFF30),  # blx reg
    (0x0E000000, 0x08000000),  # ldm/stm
    (0x0E000000, 0x0A000000),  # b/bl
    (0x0F000E10, 0x0E000A00),  # vfp data processing
    (0x0FB00E50, 0x0EB00A40),  # vfp extension ops
    (0x0F000E10, 0x0E000A10),  # vfp transfers
    (0x0E000E00, 0x0C000A00),  # vfp load/store
    (0x0FE00E00, 0x0C400A00),  # vmov two regs
    (0x0FB00FFF, 0x0E000A10),  # vmov core<->single
    (0x0FFF0FFF, 0x0EF10A10),  # vmrs
]


def rand_arm(rng):
    if rng.random() < 0.3:
        return rng.getrandbits(32)
    mask, val = rng.choice(ARM_TEMPLATES)
    w = (rng.getrandbits(32) & ~mask) | val
    cond = 14 if rng.random() < 0.6 else rng.randrange(15)
    return (w & 0x0FFFFFFF) | (cond << 28)


def rand_thumb(rng):
    hw = rng.getrandbits(16)
    if (hw >> 11) == 0b11110:
        nxt = (0b11111 << 11) | rng.getrandbits(11) if rng.random() < 0.7 else (0b11101 << 11) | (rng.getrandbits(11) & ~1)
    else:
        nxt = rng.getrandbits(16)
    return hw, nxt


def epilogue(insn):
    nxt = insn.end
    ret = nxt | (1 if insn.thumb else 0)
    if insn.kind == 'b':
        return [f'c->r[15] = 0x{insn.target & ~1:X}u; c->t = {insn.target & 1};']
    if insn.kind == 'bl':
        return [f'c->r[14] = 0x{ret:X}u; c->r[15] = 0x{insn.target & ~1:X}u; c->t = {insn.target & 1};']
    if insn.kind == 'callr':
        return insn.body + [f'c->r[14] = 0x{ret:X}u; c->t = tgt & 1; c->r[15] = tgt & (c->t ? ~1u : ~3u);']
    if insn.kind == 'jump':
        if insn.interwork:
            j = 'c->t = tgt & 1; tgt &= c->t ? ~1u : ~3u;'
        else:
            j = 'tgt &= ~1u;' if insn.thumb else 'tgt &= ~3u;'
        return insn.body + [j, 'c->r[15] = tgt;']
    return insn.body


def main():
    out = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    ctx = arm.Ctx()
    tests = []
    body = []
    while len(tests) < count:
        is_thumb = rng.random() < 0.25
        if is_thumb:
            hw, nxt = rand_thumb(rng)
            insn = thumb.decode(hw, nxt, BASE, ctx)
            word = insn.word if insn.size == 4 else hw
        else:
            word = rand_arm(rng)
            insn = arm.decode(word, BASE, ctx)
        if insn.kind == 'svc' or insn.text in ('mrc', 'mcr', 'bkpt', 'udf', 'setend', 'unconditional'):
            continue
        if insn.kind == 'undef' and not is_thumb:
            cp_space = ((word >> 25) & 7) in (6, 7) and not ((word >> 24) & 0xF) == 0xF
            if (word >> 28) == 0xF or (cp_space and ((word >> 8) & 0xF) not in (10, 11)):
                continue
        if insn.kind == 'undef' and is_thumb and (word >> 11) in (0b11101, 0b11110, 0b11111):
            continue
        if insn.kind == 'undef':
            # Still tested: dynarmic accepting an instruction we reject is a decoder gap
            tests.append((word, insn.size, 1 if is_thumb else 0, 'undef'))
            body.append(f'static void t{len(tests) - 1}(Cpu* c) {{ (void)c; }}')
            continue
        if insn.kind == 'jump' and insn.text in ('bxj',):
            continue
        i = len(tests)
        lines = [f'static void t{i}(Cpu* c) {{ // {insn.text}', '    u32 tgt = 0; (void)tgt;',
                 f'    c->r[15] = 0x{insn.end:X}u;']
        cond = arm.COND_EXPR[insn.cond]
        lines.append(f'    if ({cond}) {{')
        lines += ['        ' + s for s in epilogue(insn)]
        lines.append('    }')
        lines.append('}')
        body.append('\n'.join(lines))
        tests.append((word, insn.size, 1 if is_thumb else 0, insn.text))
    with open(out, 'w') as fp:
        fp.write('#include <recomp/rt.h>\n#include "harness.h"\nusing namespace recomp;\n')
        fp.write('\n'.join(body))
        fp.write('\nconst TestCase tests[] = {\n')
        for i, (word, size, th, text) in enumerate(tests):
            fn = 'nullptr' if text == 'undef' else f't{i}'
            fp.write(f'    {{0x{word:X}u, {size}, {th}, {fn}, "{text}"}},\n')
        fp.write('};\n')
        fp.write(f'const unsigned num_tests = {len(tests)};\n')


if __name__ == '__main__':
    main()
