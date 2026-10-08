"""Thumb (ARMv6, 16-bit + BL/BLX pairs) decoder that produces C++ statements."""

from .arm import Insn, Gen, bits, bit, sx


class ThumbGen(Gen):
    def R(self, n):
        if n == 15:
            return self.ctx.addr(self.i.addr + 4)
        return f'c->r[{n}]'


def decode(hw, next_hw, addr, ctx):
    """Decode the Thumb instruction at addr (hw = halfword, next_hw = following halfword)."""
    insn = Insn(addr=addr, size=2, word=hw, thumb=True)
    g = ThumbGen(ctx, insn)
    w = hw

    def undef(why='undefined'):
        insn.kind = 'undef'
        insn.text = why
        insn.body = []
        return insn

    top5 = w >> 11
    if top5 <= 0b00010:  # shift by immediate
        op = top5
        imm = bits(w, 10, 6)
        rm, rd = bits(w, 5, 3), w & 7
        v = g.R(rm)
        insn.text = ['lsls', 'lsrs', 'asrs'][op]
        if op == 0:
            if imm == 0:
                g.emit(f'u32 res = {v};')
            else:
                g.emit(f'u32 res = {v} << {imm}; c->c = ({v} >> {32 - imm}) & 1;')
        elif op == 1:
            n = imm or 32
            if n == 32:
                g.emit(f'c->c = {v} >> 31; u32 res = 0;')
            else:
                g.emit(f'c->c = ({v} >> {n - 1}) & 1; u32 res = {v} >> {n};')
        else:
            n = imm or 32
            if n == 32:
                g.emit(f'c->c = {v} >> 31; u32 res = u32(s32({v}) >> 31);')
            else:
                g.emit(f'c->c = ({v} >> {n - 1}) & 1; u32 res = u32(s32({v}) >> {n});')
        g.emit('nz(c, res);')
        g.set_reg(rd, 'res')
    elif top5 == 0b00011:  # add/sub register or imm3
        imm_form = bit(w, 10)
        sub = bit(w, 9)
        rn, rd = bits(w, 5, 3), w & 7
        opnd = f'{bits(w, 8, 6)}u' if imm_form else g.R(bits(w, 8, 6))
        insn.text = 'subs' if sub else 'adds'
        g.set_reg(rd, f'{"subs" if sub else "adds"}(c, {g.R(rn)}, {opnd})')
    elif (w >> 13) == 0b001:  # mov/cmp/add/sub imm8
        op = bits(w, 12, 11)
        rd = bits(w, 10, 8)
        imm = w & 0xFF
        insn.text = ['movs', 'cmp', 'adds', 'subs'][op]
        if op == 0:
            g.emit(f'nz(c, {imm}u);')
            g.set_reg(rd, f'{imm}u')
        elif op == 1:
            g.emit(f'subs(c, {g.R(rd)}, {imm}u);')
        elif op == 2:
            g.set_reg(rd, f'adds(c, {g.R(rd)}, {imm}u)')
        else:
            g.set_reg(rd, f'subs(c, {g.R(rd)}, {imm}u)')
    elif (w >> 10) == 0b010000:  # data processing
        op = bits(w, 9, 6)
        rm, rd = bits(w, 5, 3), w & 7
        a, b = g.R(rd), g.R(rm)
        names = ['ands', 'eors', 'lsls', 'lsrs', 'asrs', 'adcs', 'sbcs', 'rors',
                 'tst', 'negs', 'cmp', 'cmn', 'orrs', 'muls', 'bics', 'mvns']
        insn.text = names[op]
        if op in (0, 1, 12, 14, 15, 8):
            expr = {0: f'{a} & {b}', 1: f'{a} ^ {b}', 12: f'{a} | {b}', 14: f'{a} & ~{b}',
                    15: f'~{b}', 8: f'{a} & {b}'}[op]
            g.emit(f'u32 res = {expr}; nz(c, res);')
            if op != 8:
                g.set_reg(rd, 'res')
        elif op in (2, 3, 4, 7):
            fn = {2: 'lsl_c', 3: 'lsr_c', 4: 'asr_c', 7: 'ror_c'}[op]
            g.emit(f'u32 sc = c->c; u32 res = {fn}({a}, {b} & 0xFF, sc); c->c = sc; nz(c, res);')
            g.set_reg(rd, 'res')
        elif op == 5:
            g.set_reg(rd, f'adcs(c, {a}, {b})')
        elif op == 6:
            g.set_reg(rd, f'sbcs(c, {a}, {b})')
        elif op == 9:
            g.set_reg(rd, f'subs(c, 0u, {b})')
        elif op == 10:
            g.emit(f'subs(c, {a}, {b});')
        elif op == 11:
            g.emit(f'adds(c, {a}, {b});')
        else:  # muls
            g.emit(f'u32 res = {a} * {b}; nz(c, res);')
            g.set_reg(rd, 'res')
    elif (w >> 10) == 0b010001:  # hi register ops / BX / BLX
        op = bits(w, 9, 8)
        rm = bits(w, 6, 3)
        rd = (bit(w, 7) << 3) | (w & 7)
        if op == 0:
            insn.text = 'add'
            if rd == 15:
                g.set_reg(15, f'({g.R(rd)} + {g.R(rm)}) & ~1u')
            else:
                g.set_reg(rd, f'{g.R(rd)} + {g.R(rm)}')
        elif op == 1:
            insn.text = 'cmp'
            g.emit(f'subs(c, {g.R(rd)}, {g.R(rm)});')
        elif op == 2:
            insn.text = 'mov'
            if rd == 15:
                g.set_reg(15, f'{g.R(rm)} & ~1u')
            else:
                g.set_reg(rd, g.R(rm))
        else:
            if bit(w, 7):
                insn.text = 'blx'
                insn.kind = 'callr'
            else:
                insn.text = 'bx'
                insn.kind = 'jump'
                insn.interwork = True
            g.emit(f'tgt = {g.R(rm)};')
    elif (w >> 11) == 0b01001:  # LDR literal
        rd = bits(w, 10, 8)
        lit = ((addr + 4) & ~3) + (w & 0xFF) * 4
        insn.literal = lit
        insn.text = 'ldr'
        if ctx.is_const and ctx.is_const(lit, 4):
            g.set_reg(rd, f'0x{ctx.read_word(lit):X}u')
        else:
            g.set_reg(rd, f'rd32(c, {ctx.addr(lit)})')
    elif (w >> 12) == 0b0101:  # load/store register offset
        op = bits(w, 11, 9)
        rm, rn, rd = bits(w, 8, 6), bits(w, 5, 3), w & 7
        g.emit(f'u32 ea = {g.R(rn)} + {g.R(rm)};')
        insn.text = ['str', 'strh', 'strb', 'ldrsb', 'ldr', 'ldrh', 'ldrb', 'ldrsh'][op]
        if op == 0:
            g.emit(f'wr32(c, ea, {g.R(rd)});')
        elif op == 1:
            g.emit(f'wr16(c, ea, u16({g.R(rd)}));')
        elif op == 2:
            g.emit(f'wr8(c, ea, u8({g.R(rd)}));')
        else:
            load = {3: 'u32(s32(s8(rd8(c, ea))))', 4: 'rd32(c, ea)', 5: 'u32(rd16(c, ea))',
                    6: 'u32(rd8(c, ea))', 7: 'u32(s32(s16(rd16(c, ea))))'}[op]
            g.set_reg(rd, load)
    elif (w >> 13) == 0b011:  # LDR/STR (B) immediate
        byte = bit(w, 12)
        load = bit(w, 11)
        imm = bits(w, 10, 6) * (1 if byte else 4)
        rn, rd = bits(w, 5, 3), w & 7
        g.emit(f'u32 ea = {g.R(rn)} + {imm}u;')
        insn.text = ('ldr' if load else 'str') + ('b' if byte else '')
        if load:
            g.set_reg(rd, 'u32(rd8(c, ea))' if byte else 'rd32(c, ea)')
        else:
            g.emit(f'wr8(c, ea, u8({g.R(rd)}));' if byte else f'wr32(c, ea, {g.R(rd)});')
    elif (w >> 12) == 0b1000:  # LDRH/STRH immediate
        load = bit(w, 11)
        imm = bits(w, 10, 6) * 2
        rn, rd = bits(w, 5, 3), w & 7
        g.emit(f'u32 ea = {g.R(rn)} + {imm}u;')
        insn.text = 'ldrh' if load else 'strh'
        if load:
            g.set_reg(rd, 'u32(rd16(c, ea))')
        else:
            g.emit(f'wr16(c, ea, u16({g.R(rd)}));')
    elif (w >> 12) == 0b1001:  # SP-relative
        load = bit(w, 11)
        rd = bits(w, 10, 8)
        g.emit(f'u32 ea = c->r[13] + {(w & 0xFF) * 4}u;')
        insn.text = 'ldr' if load else 'str'
        if load:
            g.set_reg(rd, 'rd32(c, ea)')
        else:
            g.emit(f'wr32(c, ea, {g.R(rd)});')
    elif (w >> 12) == 0b1010:  # ADR / ADD rd, sp, imm
        rd = bits(w, 10, 8)
        imm = (w & 0xFF) * 4
        if bit(w, 11):
            insn.text = 'add sp'
            g.set_reg(rd, f'c->r[13] + {imm}u')
        else:
            insn.text = 'adr'
            g.set_reg(rd, ctx.addr(((addr + 4) & ~3) + imm))
    elif (w >> 12) == 0b1011:  # misc
        if (w >> 8) == 0b10110000:
            imm = (w & 0x7F) * 4
            insn.text = 'sub sp' if bit(w, 7) else 'add sp'
            g.emit(f'c->r[13] {"-" if bit(w, 7) else "+"}= {imm}u;')
        elif (w >> 8) == 0b10110010:
            op = bits(w, 7, 6)
            rm, rd = bits(w, 5, 3), w & 7
            insn.text = ['sxth', 'sxtb', 'uxth', 'uxtb'][op]
            expr = {0: f'u32(s32(s16({g.R(rm)})))', 1: f'u32(s32(s8({g.R(rm)})))',
                    2: f'({g.R(rm)} & 0xFFFFu)', 3: f'({g.R(rm)} & 0xFFu)'}[op]
            g.set_reg(rd, expr)
        elif (w >> 9) == 0b1011010:  # PUSH
            regs = [r for r in range(8) if bit(w, r)] + ([14] if bit(w, 8) else [])
            insn.text = 'push'
            g.emit(f'u32 ea = c->r[13] - {4 * len(regs)}u;')
            for k, r in enumerate(regs):
                g.emit(f'wr32(c, ea + {4 * k}u, c->r[{r}]);')
            g.emit('c->r[13] = ea;')
        elif (w >> 9) == 0b1011110:  # POP
            regs = [r for r in range(8) if bit(w, r)]
            pc = bit(w, 8)
            insn.text = 'pop'
            g.emit('u32 ea = c->r[13];')
            for k, r in enumerate(regs):
                g.emit(f'c->r[{r}] = rd32(c, ea + {4 * k}u);')
            n = len(regs) + pc
            if pc:
                g.emit(f'u32 newpc = rd32(c, ea + {4 * len(regs)}u);')
            g.emit(f'c->r[13] = ea + {4 * n}u;')
            if pc:
                g.set_reg(15, 'newpc', interwork=True)
        elif (w >> 6) == 0b1011101000:
            rm, rd = bits(w, 5, 3), w & 7
            insn.text = 'rev'
            g.set_reg(rd, f'__builtin_bswap32({g.R(rm)})')
        elif (w >> 6) == 0b1011101001:
            rm, rd = bits(w, 5, 3), w & 7
            insn.text = 'rev16'
            g.emit(f'u32 v = {g.R(rm)};')
            g.set_reg(rd, '((v & 0x00FF00FFu) << 8) | ((v >> 8) & 0x00FF00FFu)')
        elif (w >> 6) == 0b1011101011:
            rm, rd = bits(w, 5, 3), w & 7
            insn.text = 'revsh'
            g.set_reg(rd, f'u32(s32(s16(__builtin_bswap16(u16({g.R(rm)})))))')
        elif (w >> 8) == 0b10111110:
            return undef('bkpt')
        elif (w & 0xFFE8) == 0xB660:  # CPS: privileged, no effect in user mode
            insn.text = 'cps'
        elif (w & 0xFFF7) == 0xB650:  # SETEND
            return undef('setend')
        elif (w & 0xFF0F) == 0xBF00:  # hints (ARMv6K: NOP, YIELD, WFE, WFI, SEV)
            insn.text = 'hint'
        else:
            return undef()
    elif (w >> 12) == 0b1100:  # LDMIA / STMIA
        load = bit(w, 11)
        rn = bits(w, 10, 8)
        regs = [r for r in range(8) if bit(w, r)]
        if not regs:
            return undef()
        insn.text = 'ldmia' if load else 'stmia'
        g.emit(f'u32 ea = {g.R(rn)};')
        if load:
            for k, r in enumerate(regs):
                g.emit(f'c->r[{r}] = rd32(c, ea + {4 * k}u);')
            if rn not in regs:
                g.emit(f'c->r[{rn}] = ea + {4 * len(regs)}u;')
        else:
            for k, r in enumerate(regs):
                g.emit(f'wr32(c, ea + {4 * k}u, c->r[{r}]);')
            g.emit(f'c->r[{rn}] = ea + {4 * len(regs)}u;')
    elif (w >> 12) == 0b1101:
        cond = bits(w, 11, 8)
        if cond == 0xF:
            insn.kind = 'svc'
            insn.imm = w & 0xFF
            insn.text = 'svc'
        elif cond == 0xE:
            return undef('udf')
        else:
            insn.kind = 'b'
            insn.cond = cond
            insn.target = ((addr + 4 + (sx(w & 0xFF, 8) << 1)) & 0xFFFFFFFF) | 1
            insn.text = 'b'
    elif top5 == 0b11100:
        insn.kind = 'b'
        insn.target = ((addr + 4 + (sx(w & 0x7FF, 11) << 1)) & 0xFFFFFFFF) | 1
        insn.text = 'b'
    elif top5 == 0b11110:  # BL/BLX prefix
        if next_hw is None or (next_hw >> 11) not in (0b11111, 0b11101):
            return undef('lone bl prefix')
        insn.size = 4
        insn.word = (w << 16) | next_hw
        base = addr + 4 + (sx(w & 0x7FF, 11) << 12)
        off = (next_hw & 0x7FF) << 1
        insn.kind = 'bl'
        if (next_hw >> 11) == 0b11111:
            insn.target = ((base + off) & 0xFFFFFFFF) | 1
            insn.text = 'bl'
        else:
            insn.target = (base + off) & 0xFFFFFFFC
            insn.text = 'blx'
    else:
        return undef()
    insn.body = g.lines
    return insn
