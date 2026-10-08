"""ARMv6K (ARM state) decoder that produces C++ statements for each instruction.

Each decoded instruction is an `Insn`. Control flow is described by `kind`:
  'op'      ordinary instruction, execution continues at the next instruction
  'b'       direct branch to `target`
  'bl'      direct call to `target` (target bit 0 set = Thumb)
  'callr'   call through a register (BLX reg); `body` computes the target into `tgt`
  'jump'    computed branch (writes PC); `body` computes `tgt`; `interwork` says whether
            bit 0 selects Thumb
  'jtab'    jump table: `tgt` index, `table` lists target addresses
  'svc'     supervisor call with `imm`
  'undef'   undefined / unsupported instruction (stops decoding)
Conditional instructions carry `cond` != 14; the emitter wraps their body.
"""

from dataclasses import dataclass, field

COND_EXPR = [
    'c->z', '!c->z', 'c->c', '!c->c', 'c->n', '!c->n', 'c->v', '!c->v',
    '(c->c && !c->z)', '(!c->c || c->z)', '(c->n == c->v)', '(c->n != c->v)',
    '(!c->z && c->n == c->v)', '(c->z || c->n != c->v)', '1',
]


@dataclass
class Insn:
    addr: int
    size: int
    word: int
    thumb: bool = False
    cond: int = 14
    kind: str = 'op'
    target: int = None
    body: list = field(default_factory=list)
    interwork: bool = False
    imm: int = 0
    table: list = None
    literal: int = None       # address of a PC-relative literal that this instruction loads
    ticks: int = 1
    text: str = ''

    @property
    def end(self):
        return self.addr + self.size


class Ctx:
    """What the decoder needs to know about where the code lives."""

    def __init__(self, module_relative=False, read_word=None, is_const=None):
        self.module_relative = module_relative  # addresses are offsets from the module base
        self.read_word = read_word              # read a 32-bit word of the image (for literals)
        self.is_const = is_const                # address lies in read-only data of the image

    def addr(self, a):
        """C expression for guest address a."""
        a &= 0xFFFFFFFF
        if self.module_relative:
            return f'(MB + 0x{a:X}u)'
        return f'0x{a:X}u'


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def bits(w, hi, lo):
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)


def bit(w, n):
    return (w >> n) & 1


class Gen:
    """Builds the C++ body of one ARM instruction."""

    def __init__(self, ctx, insn):
        self.ctx = ctx
        self.i = insn
        self.lines = []

    def R(self, n):
        if n == 15:
            return self.ctx.addr(self.i.addr + 8)
        return f'c->r[{n}]'

    def emit(self, s):
        self.lines.append(s)

    def set_reg(self, n, expr, interwork=False):
        """Write a register; writing r15 turns the instruction into a computed jump."""
        if n == 15:
            self.emit(f'tgt = {expr};')
            self.i.kind = 'jump'
            self.i.interwork = interwork
        else:
            self.emit(f'c->r[{n}] = {expr};')


def imm_rot(w):
    imm8 = w & 0xFF
    rot = bits(w, 11, 8) * 2
    v = ((imm8 >> rot) | (imm8 << (32 - rot))) & 0xFFFFFFFF if rot else imm8
    return v, rot


def shift_imm_expr(g, rm, stype, imm5, need_carry):
    """Operand2 = Rm shifted by an immediate. Returns (expr, carry_expr or None)."""
    v = g.R(rm)
    if stype == 0:
        if imm5 == 0:
            return v, None
        return f'({v} << {imm5})', (f'(({v} >> {32 - imm5}) & 1)' if need_carry else None)
    if stype == 1:
        n = imm5 or 32
        if n == 32:
            return '0u', (f'({v} >> 31)' if need_carry else None)
        return f'({v} >> {n})', (f'(({v} >> {n - 1}) & 1)' if need_carry else None)
    if stype == 2:
        n = imm5 or 32
        if n == 32:
            return f'u32(s32({v}) >> 31)', (f'({v} >> 31)' if need_carry else None)
        return f'u32(s32({v}) >> {n})', (f'(({v} >> {n - 1}) & 1)' if need_carry else None)
    if imm5 == 0:
        return f'(({v} >> 1) | (c->c << 31))', (f'({v} & 1)' if need_carry else None)
    return f'ror({v}, {imm5})', (f'(({v} >> {imm5 - 1}) & 1)' if need_carry else None)


SHIFT_FN = ['lsl', 'lsr', 'asr', 'ror']


def operand2(g, w, need_carry):
    """Emits code computing operand2 into `op` (and shifter carry into `sc` when needed).
    Returns True if `sc` was produced (otherwise carry is unchanged)."""
    if bit(w, 25):
        v, rot = imm_rot(w)
        g.emit(f'u32 op = 0x{v:X}u;')
        if need_carry and rot:
            g.emit(f'u32 sc = {v >> 31}u;')
            return True
        return False
    rm = w & 0xF
    stype = bits(w, 6, 5)
    if bit(w, 4) == 0:
        expr, carry = shift_imm_expr(g, rm, stype, bits(w, 11, 7), need_carry)
        g.emit(f'u32 op = {expr};')
        if carry:
            g.emit(f'u32 sc = {carry};')
            return True
        return False
    rs = bits(w, 11, 8)
    rmv = g.R(rm) if rm != 15 else g.ctx.addr(g.i.addr + 12)
    g.emit(f'u32 sh = {g.R(rs)} & 0xFF;')
    if need_carry:
        g.emit('u32 sc = c->c;')
        g.emit(f'u32 op = {SHIFT_FN[stype]}_c({rmv}, sh, sc);')
        return True
    g.emit(f'u32 op = {SHIFT_FN[stype]}({rmv}, sh);')
    return False


DP_NAMES = ['and', 'eor', 'sub', 'rsb', 'add', 'adc', 'sbc', 'rsc',
            'tst', 'teq', 'cmp', 'cmn', 'orr', 'mov', 'bic', 'mvn']


def data_processing(g, w):
    opc = bits(w, 24, 21)
    s = bit(w, 20)
    rn = bits(w, 19, 16)
    rd = bits(w, 15, 12)
    logical = opc in (0, 1, 8, 9, 12, 13, 14, 15)
    has_sc = operand2(g, w, s and logical)
    a = g.R(rn)
    g.i.text = DP_NAMES[opc] + ('s' if s and opc not in (8, 9, 10, 11) else '')
    if rd == 15 and s and opc not in (8, 9, 10, 11):
        # Exception return (SUBS pc, lr ...): not used by user code; treat as a plain jump.
        s = 0
    if opc in (0, 1, 12, 13, 14, 15, 8, 9):
        expr = {0: f'{a} & op', 1: f'{a} ^ op', 12: f'{a} | op', 13: 'op', 14: f'{a} & ~op',
                15: '~op', 8: f'{a} & op', 9: f'{a} ^ op'}[opc]
        g.emit(f'u32 res = {expr};')
        if s:
            g.emit('nz(c, res);')
            if has_sc:
                g.emit('c->c = sc;')
    else:
        if s:
            fn = {2: f'subs(c, {a}, op)', 3: f'subs(c, op, {a})', 4: f'adds(c, {a}, op)',
                  5: f'adcs(c, {a}, op)', 6: f'sbcs(c, {a}, op)', 7: f'sbcs(c, op, {a})',
                  10: f'subs(c, {a}, op)', 11: f'adds(c, {a}, op)'}[opc]
        else:
            fn = {2: f'{a} - op', 3: f'op - {a}', 4: f'{a} + op', 5: f'{a} + op + c->c',
                  6: f'{a} - op - (c->c ^ 1)', 7: f'op - {a} - (c->c ^ 1)'}[opc]
        g.emit(f'u32 res = {fn};')
    if opc in (8, 9, 10, 11):
        return
    if rd == 15:
        g.set_reg(15, 'res & ~3u')
    else:
        g.set_reg(rd, 'res')


def multiply(g, w):
    op = bits(w, 23, 21)
    s = bit(w, 20)
    rd = bits(w, 19, 16)
    rn = bits(w, 15, 12)
    rs = bits(w, 11, 8)
    rm = w & 0xF
    if op in (0, 1):
        g.i.text = 'mla' if op else 'mul'
        expr = f'{g.R(rm)} * {g.R(rs)}' + (f' + {g.R(rn)}' if op else '')
        g.emit(f'u32 res = {expr};')
        if s:
            g.emit('nz(c, res);')
        g.set_reg(rd, 'res')
        return True
    if op == 2:  # UMAAL
        g.i.text = 'umaal'
        g.emit(f'u64 res = u64({g.R(rm)}) * u64({g.R(rs)}) + u64({g.R(rn)}) + u64({g.R(rd)});')
        g.set_reg(rn, 'u32(res)')
        g.set_reg(rd, 'u32(res >> 32)')
        return True
    if op == 3:
        return False  # MLS is ARMv6T2
    signed = bit(w, 22)
    acc = bit(w, 21)
    g.i.text = ('s' if signed else 'u') + ('mlal' if acc else 'mull')
    if signed:
        prod = f's64(s32({g.R(rm)})) * s64(s32({g.R(rs)}))'
        g.emit(f'u64 res = u64({prod});')
    else:
        g.emit(f'u64 res = u64({g.R(rm)}) * u64({g.R(rs)});')
    if acc:
        g.emit(f'res += (u64({g.R(rd)}) << 32) | u64({g.R(rn)});')
    if s:
        g.emit('c->n = u32(res >> 63); c->z = res == 0;')
    g.set_reg(rn, 'u32(res)')
    g.set_reg(rd, 'u32(res >> 32)')
    return True


def halfword_ops(g, w):
    """SMLAxy, SMLAWy, SMULWy, SMLALxy, SMULxy"""
    op = bits(w, 22, 21)
    rd = bits(w, 19, 16)
    rn = bits(w, 15, 12)
    rs = bits(w, 11, 8)
    y = bit(w, 6)
    x = bit(w, 5)
    rm = w & 0xF
    half = lambda r, top: f's32(s16({g.R(r)} >> 16))' if top else f's32(s16({g.R(r)}))'
    if op == 0:
        g.i.text = 'smlaxy'
        g.emit(f's32 p = {half(rm, x)} * {half(rs, y)};')
        g.emit(f's64 sum = s64(p) + s64(s32({g.R(rn)}));')
        g.emit('if (sum != s64(s32(sum))) c->q = 1;')
        g.set_reg(rd, 'u32(sum)')
    elif op == 1:
        g.emit(f's64 p = (s64(s32({g.R(rm)})) * s64({half(rs, y)})) >> 16;')
        if x == 0:
            g.i.text = 'smlawy'
            g.emit(f's64 sum = s64(s32(p)) + s64(s32({g.R(rn)}));')
            g.emit('if (sum != s64(s32(sum))) c->q = 1;')
            g.set_reg(rd, 'u32(sum)')
        else:
            g.i.text = 'smulwy'
            g.set_reg(rd, 'u32(p)')
    elif op == 2:
        g.i.text = 'smlalxy'
        g.emit(f's64 p = s64({half(rm, x)} * {half(rs, y)});')
        g.emit(f'u64 res = ((u64({g.R(rd)}) << 32) | u64({g.R(rn)})) + u64(p);')
        g.set_reg(rn, 'u32(res)')
        g.set_reg(rd, 'u32(res >> 32)')
    else:
        g.i.text = 'smulxy'
        g.set_reg(rd, f'u32({half(rm, x)} * {half(rs, y)})')


def load_store(g, w):
    """LDR/STR/LDRB/STRB, immediate or register offset."""
    p, u, b, wb, l = bit(w, 24), bit(w, 23), bit(w, 22), bit(w, 21), bit(w, 20)
    rn = bits(w, 19, 16)
    rd = bits(w, 15, 12)
    g.i.text = ('ldr' if l else 'str') + ('b' if b else '')
    if bit(w, 25) == 0:
        imm = w & 0xFFF
        off = f'{imm}u' if imm else None
        # Literal pool load: fold constants from read-only image data
        if rn == 15 and p and not wb:
            lit = (g.i.addr + 8 + (imm if u else -imm)) & 0xFFFFFFFF
            g.i.literal = lit
            if l and not b and g.ctx.is_const and g.ctx.is_const(lit, 4) and rd != 15:
                g.set_reg(rd, f'0x{g.ctx.read_word(lit):X}u')
                return
    else:
        expr, _ = shift_imm_expr(g, w & 0xF, bits(w, 6, 5), bits(w, 11, 7), False)
        off = expr
    base = g.R(rn)
    if off is None:
        g.emit(f'u32 ea = {base};')
        g.emit('u32 wbv = ea;')
    else:
        sign = '+' if u else '-'
        g.emit(f'u32 wbv = {base} {sign} {off};')
        g.emit(f'u32 ea = {"wbv" if p else base};')
    writeback = (not p) or wb
    if l:
        g.emit(f'u32 val = {"rd8(c, ea)" if b else "rd32(c, ea)"};')
        if writeback and rn != 15:
            g.emit(f'c->r[{rn}] = wbv;')
        if rd == 15:
            g.set_reg(15, 'val', interwork=True)
        else:
            g.set_reg(rd, 'val')
    else:
        src = g.R(rd) if rd != 15 else g.ctx.addr(g.i.addr + 8)
        g.emit(f'{"wr8(c, ea, u8(" + src + "))" if b else "wr32(c, ea, " + src + ")"};')
        if writeback and rn != 15:
            g.emit(f'c->r[{rn}] = wbv;')


def extra_load_store(g, w):
    """LDRH/STRH/LDRSB/LDRSH/LDRD/STRD"""
    p, u, i, wb, l = bit(w, 24), bit(w, 23), bit(w, 22), bit(w, 21), bit(w, 20)
    rn = bits(w, 19, 16)
    rd = bits(w, 15, 12)
    sh = bits(w, 6, 5)
    if i:
        imm = (bits(w, 11, 8) << 4) | (w & 0xF)
        off = f'{imm}u' if imm else None
        if rn == 15 and p and not wb:
            g.i.literal = (g.i.addr + 8 + (imm if u else -imm)) & 0xFFFFFFFF
    else:
        off = g.R(w & 0xF)
    base = g.R(rn)
    if off is None:
        g.emit(f'u32 ea = {base};')
        g.emit('u32 wbv = ea;')
    else:
        g.emit(f'u32 wbv = {base} {"+" if u else "-"} {off};')
        g.emit(f'u32 ea = {"wbv" if p else base};')
    writeback = ((not p) or wb) and rn != 15
    if l:
        g.i.text = {1: 'ldrh', 2: 'ldrsb', 3: 'ldrsh'}[sh]
        load = {1: 'u32(rd16(c, ea))', 2: 'u32(s32(s8(rd8(c, ea))))', 3: 'u32(s32(s16(rd16(c, ea))))'}[sh]
        g.emit(f'u32 val = {load};')
        if writeback:
            g.emit(f'c->r[{rn}] = wbv;')
        g.set_reg(rd, 'val', interwork=True)
        return True
    if sh == 1:
        g.i.text = 'strh'
        g.emit(f'wr16(c, ea, u16({g.R(rd)}));')
        if writeback:
            g.emit(f'c->r[{rn}] = wbv;')
        return True
    if rd & 1 or rd == 14:
        return False
    if sh == 2:
        g.i.text = 'ldrd'
        g.emit('u32 v0 = rd32(c, ea), v1 = rd32(c, ea + 4);')
        if writeback:
            g.emit(f'c->r[{rn}] = wbv;')
        g.emit(f'c->r[{rd}] = v0; c->r[{rd + 1}] = v1;')
    else:
        g.i.text = 'strd'
        g.emit(f'wr32(c, ea, {g.R(rd)}); wr32(c, ea + 4, {g.R(rd + 1)});')
        if writeback:
            g.emit(f'c->r[{rn}] = wbv;')
    return True


def block_transfer(g, w):
    p, u, s, wb, l = bit(w, 24), bit(w, 23), bit(w, 22), bit(w, 21), bit(w, 20)
    rn = bits(w, 19, 16)
    regs = [r for r in range(16) if bit(w, r)]
    n = len(regs)
    if n == 0 or rn == 15:
        return False
    g.i.text = ('ldm' if l else 'stm') + ('i' if u else 'd') + ('b' if p else 'a')
    if u:
        start = f'{g.R(rn)} + 4u' if p else g.R(rn)
        g.emit(f'u32 wbv = {g.R(rn)} + {4 * n}u;')
    else:
        start = f'{g.R(rn)} - {4 * n}u' if p else f'{g.R(rn)} - {4 * n - 4}u'
        g.emit(f'u32 wbv = {g.R(rn)} - {4 * n}u;')
    g.emit(f'u32 ea = {start};')
    if l:
        for k, r in enumerate(regs):
            dst = 'tgt' if r == 15 else f'c->r[{r}]'
            if r == 15:
                g.emit(f'u32 newpc = rd32(c, ea + {4 * k}u);')
            elif r == rn:
                g.emit(f'u32 basev = rd32(c, ea + {4 * k}u);')
            else:
                g.emit(f'{dst} = rd32(c, ea + {4 * k}u);')
        if wb and rn not in regs:
            g.emit(f'c->r[{rn}] = wbv;')
        if rn in regs:
            g.emit(f'c->r[{rn}] = basev;')
        if 15 in regs and not s:
            g.set_reg(15, 'newpc', interwork=True)
        elif 15 in regs:
            g.set_reg(15, 'newpc', interwork=True)
    else:
        for k, r in enumerate(regs):
            src = g.ctx.addr(g.i.addr + 8) if r == 15 else f'c->r[{r}]'
            g.emit(f'wr32(c, ea + {4 * k}u, {src});')
        if wb:
            g.emit(f'c->r[{rn}] = wbv;')
    return True


PAR_OPS = {0: 'add16', 1: 'asx', 2: 'sax', 3: 'sub16', 4: 'add8', 7: 'sub8'}
PAR_KIND = {1: 0, 2: 1, 3: 2, 5: 4, 6: 5, 7: 6}


def media(g, w):
    op1 = bits(w, 24, 20)
    rd = bits(w, 15, 12)
    rn = bits(w, 19, 16)
    rm = w & 0xF
    op2 = bits(w, 7, 5)
    if bits(w, 24, 23) == 0b00:  # parallel add/subtract
        kind = PAR_KIND.get(bits(w, 22, 20))
        if kind is None or op2 not in PAR_OPS:
            return False
        g.i.text = PAR_OPS[op2]
        g.set_reg(rd, f'parallel(c, {kind}, {op2}, {g.R(rn)}, {g.R(rm)})')
        return True
    if bits(w, 24, 23) == 0b01:
        sub = bits(w, 22, 20)
        if sub == 0 and bit(w, 5) == 0:  # PKH
            imm = bits(w, 11, 7)
            if bit(w, 6) == 0:
                g.i.text = 'pkhbt'
                g.set_reg(rd, f'({g.R(rn)} & 0xFFFFu) | (({g.R(rm)} << {imm}) & 0xFFFF0000u)')
            else:
                g.i.text = 'pkhtb'
                g.set_reg(rd, f'({g.R(rn)} & 0xFFFF0000u) | (u32(s32({g.R(rm)}) >> {imm or 31}) & 0xFFFFu)')
            return True
        if (sub & 0b010) and bit(w, 5) == 0:  # SSAT / USAT
            sat = bits(w, 20, 16)
            imm = bits(w, 11, 7)
            if bit(w, 6):
                operand = f's64(s32({g.R(rm)}) >> {imm or 31})'
            else:
                operand = f's64(s32({g.R(rm)} << {imm}))'
            if bit(w, 22):
                g.i.text = 'usat'
                g.set_reg(rd, f'sat_u(c, {operand}, {sat})')
            else:
                g.i.text = 'ssat'
                g.set_reg(rd, f'u32(sat_s(c, {operand}, {sat + 1}))')
            return True
        low = bits(w, 7, 4)
        if sub == 0b010 and low == 0b0011:
            sat = bits(w, 19, 16) + 1
            g.i.text = 'ssat16'
            g.emit(f'u32 lo = u32(sat_s(c, s16({g.R(rm)}), {sat})) & 0xFFFF;')
            g.emit(f'u32 hi = u32(sat_s(c, s16({g.R(rm)} >> 16), {sat})) & 0xFFFF;')
            g.set_reg(rd, 'lo | (hi << 16)')
            return True
        if sub == 0b110 and low == 0b0011:
            sat = bits(w, 19, 16)
            g.i.text = 'usat16'
            g.emit(f'u32 lo = sat_u(c, s16({g.R(rm)}), {sat});')
            g.emit(f'u32 hi = sat_u(c, s16({g.R(rm)} >> 16), {sat});')
            g.set_reg(rd, 'lo | (hi << 16)')
            return True
        if sub == 0 and low == 0b1011:
            g.i.text = 'sel'
            g.set_reg(rd, f'sel(c, {g.R(rn)}, {g.R(rm)})')
            return True
        if sub == 0b011 and low == 0b0011:
            g.i.text = 'rev'
            g.set_reg(rd, f'__builtin_bswap32({g.R(rm)})')
            return True
        if sub == 0b011 and low == 0b1011:
            g.i.text = 'rev16'
            g.emit(f'u32 v = {g.R(rm)};')
            g.set_reg(rd, '((v & 0x00FF00FFu) << 8) | ((v >> 8) & 0x00FF00FFu)')
            return True
        if sub == 0b111 and low == 0b1011:
            g.i.text = 'revsh'
            g.set_reg(rd, f'u32(s32(s16(__builtin_bswap16(u16({g.R(rm)})))))')
            return True
        if low == 0b0111:  # extend (and add)
            rot = bits(w, 11, 10) * 8
            v = f'ror({g.R(rm)}, {rot})' if rot else g.R(rm)
            g.emit(f'u32 v = {v};')
            acc = rn != 15
            if sub == 0b000:
                g.i.text = 'sxtab16' if acc else 'sxtb16'
                lo = 'u32(s32(s8(v)))'
                hi = 'u32(s32(s8(v >> 16)))'
                if acc:
                    g.emit(f'u32 a = {g.R(rn)};')
                    g.set_reg(rd, f'((a + {lo}) & 0xFFFFu) | (((a >> 16) + {hi}) << 16)')
                else:
                    g.set_reg(rd, f'({lo} & 0xFFFFu) | ({hi} << 16)')
                return True
            if sub == 0b100:
                g.i.text = 'uxtab16' if acc else 'uxtb16'
                if acc:
                    g.emit(f'u32 a = {g.R(rn)};')
                    g.set_reg(rd, '((a + (v & 0xFF)) & 0xFFFFu) | (((a >> 16) + ((v >> 16) & 0xFF)) << 16)')
                else:
                    g.set_reg(rd, 'v & 0x00FF00FFu')
                return True
            ext = {0b010: 'u32(s32(s8(v)))', 0b011: 'u32(s32(s16(v)))',
                   0b110: '(v & 0xFFu)', 0b111: '(v & 0xFFFFu)'}.get(sub)
            if ext is None:
                return False
            g.i.text = {0b010: 'sxtb', 0b011: 'sxth', 0b110: 'uxtb', 0b111: 'uxth'}[sub]
            g.set_reg(rd, f'{g.R(rn)} + {ext}' if acc else ext)
            return True
        return False
    if bits(w, 24, 23) == 0b10:  # signed multiplies
        sub = bits(w, 22, 20)
        rdh = bits(w, 19, 16)
        ra = bits(w, 15, 12)
        rmm = bits(w, 11, 8)
        rnn = w & 0xF
        m = bit(w, 5)
        if sub == 0b000 and bit(w, 7) == 0 and bit(w, 4):
            op_sub = bit(w, 6)
            mv = f'ror({g.R(rmm)}, 16)' if m else g.R(rmm)
            g.emit(f'u32 a = {g.R(rnn)}, b = {mv};')
            g.emit('s32 p1 = s32(s16(a)) * s32(s16(b)), p2 = s32(s16(a >> 16)) * s32(s16(b >> 16));')
            if op_sub:
                g.emit('s64 sum = s64(p1) - s64(p2);')
            else:
                g.emit('s64 sum = s64(p1) + s64(p2);')
            if ra != 15:
                g.emit(f'sum += s64(s32({g.R(ra)}));')
            g.emit('if (sum != s64(s32(sum))) c->q = 1;')
            g.i.text = ('smlsd' if op_sub else 'smlad') if ra != 15 else ('smusd' if op_sub else 'smuad')
            g.set_reg(rdh, 'u32(sum)')
            return True
        if sub == 0b100 and bit(w, 7) == 0 and bit(w, 4):
            op_sub = bit(w, 6)
            mv = f'ror({g.R(rmm)}, 16)' if m else g.R(rmm)
            g.emit(f'u32 a = {g.R(rnn)}, b = {mv};')
            g.emit('s64 p1 = s32(s16(a)) * s32(s16(b)), p2 = s32(s16(a >> 16)) * s32(s16(b >> 16));')
            g.emit(f's64 acc = s64((u64({g.R(rdh)}) << 32) | u64({g.R(ra)}));')
            g.emit('acc += ' + ('p1 - p2;' if op_sub else 'p1 + p2;'))
            g.i.text = 'smlsld' if op_sub else 'smlald'
            g.set_reg(ra, 'u32(u64(acc))')
            g.set_reg(rdh, 'u32(u64(acc) >> 32)')
            return True
        if sub == 0b101 and bit(w, 4):
            r = bit(w, 5)
            g.emit(f's64 prod = s64(s32({g.R(rnn)})) * s64(s32({g.R(rmm)}));')
            if bits(w, 7, 6) == 0b00:
                acc = f'(s64(s32({g.R(ra)})) << 32)' if ra != 15 else '0'
                g.emit(f's64 res = {acc} + prod' + (' + 0x80000000ll;' if r else ';'))
                g.i.text = 'smmla' if ra != 15 else 'smmul'
            elif bits(w, 7, 6) == 0b11:
                g.emit(f's64 res = (s64(s32({g.R(ra)})) << 32) - prod' + (' + 0x80000000ll;' if r else ';'))
                g.i.text = 'smmls'
            else:
                return False
            g.set_reg(rdh, 'u32(u64(res) >> 32)')
            return True
        return False
    if op1 == 0b11000 and op2 == 0 and bit(w, 4):
        rdh = bits(w, 19, 16)
        ra = bits(w, 15, 12)
        g.i.text = 'usad8' if ra == 15 else 'usada8'
        expr = f'usad8({g.R(w & 0xF)}, {g.R(bits(w, 11, 8))})'
        g.set_reg(rdh, expr if ra == 15 else f'{g.R(ra)} + {expr}')
        return True
    return False


def coproc_reg(g, w, ctx):
    """MRC/MCR (non-VFP)."""
    cp = bits(w, 11, 8)
    l = bit(w, 20)
    rt = bits(w, 15, 12)
    crn = bits(w, 19, 16)
    crm = w & 0xF
    opc1 = bits(w, 23, 21)
    opc2 = bits(w, 7, 5)
    if cp != 15:
        return False
    key = (crn, opc1, crm, opc2)
    if l:
        g.i.text = 'mrc'
        src = {(13, 0, 0, 3): 'c->tls_uro', (13, 0, 0, 2): 'c->tls_urw'}.get(key)
        if src is None:
            return False
        if rt == 15:
            g.emit(f'msr(c, {src}, 0xF0000000u);')
        else:
            g.set_reg(rt, src)
        return True
    g.i.text = 'mcr'
    if key == (13, 0, 0, 2):
        g.emit(f'c->tls_urw = {g.R(rt)};')
        return True
    if crn == 7:  # cache maintenance and barriers: nothing to do natively
        return True
    return False


def decode(word, addr, ctx):
    """Decode one ARM instruction at `addr`."""
    from . import vfp
    w = word
    insn = Insn(addr=addr, size=4, word=w)
    g = Gen(ctx, insn)
    cond = w >> 28

    def undef(why='undefined'):
        insn.kind = 'undef'
        insn.text = why
        insn.body = []
        insn.cond = 14
        return insn

    if cond == 0xF:
        if bits(w, 27, 25) == 0b101:  # BLX immediate
            off = (sx(w & 0xFFFFFF, 24) << 2) | (bit(w, 24) << 1)
            insn.kind = 'bl'
            insn.target = ((addr + 8 + off) & 0xFFFFFFFF) | 1
            insn.text = 'blx'
            return insn
        if w == 0xF57FF01F:
            insn.text = 'clrex'
            insn.body = ['c->excl_on = 0;']
            return insn
        if (w & 0xFD70F000) == 0xF550F000:  # PLD
            insn.text = 'pld'
            return insn
        if (w & 0xFFFFFF00) in (0xF57FF040, 0xF57FF050, 0xF57FF060):  # DSB/DMB/ISB
            insn.text = 'barrier'
            return insn
        return undef('unconditional')
    insn.cond = cond
    t = bits(w, 27, 25)
    ok = True
    if t == 0b000:
        if bits(w, 7, 4) == 0b1001:
            b2423 = bits(w, 24, 23)
            if bit(w, 24) == 0:
                ok = multiply(g, w)
            elif b2423 == 0b10 and bits(w, 21, 20) == 0b00:  # SWP/SWPB
                rn, rd, rm = bits(w, 19, 16), bits(w, 15, 12), w & 0xF
                if bit(w, 22):
                    insn.text = 'swpb'
                    g.emit(f'u32 ea = {g.R(rn)}; u32 val = rd8(c, ea); wr8(c, ea, u8({g.R(rm)}));')
                else:
                    insn.text = 'swp'
                    g.emit(f'u32 ea = {g.R(rn)}; u32 val = rd32(c, ea); wr32(c, ea, {g.R(rm)});')
                g.set_reg(rd, 'val')
            elif b2423 == 0b11:  # exclusives
                op = bits(w, 22, 20)
                rn, rd, rt = bits(w, 19, 16), bits(w, 15, 12), w & 0xF
                size = {0: 32, 1: 32, 2: 64, 3: 64, 4: 8, 5: 8, 6: 16, 7: 16}[op]
                load = op & 1
                rdfn = {8: 'rd8', 16: 'rd16', 32: 'rd32', 64: 'rd64'}[size]
                wrfn = {8: 'wr8', 16: 'wr16', 32: 'wr32', 64: 'wr64'}[size]
                cast = {8: 'u8', 16: 'u16', 32: 'u32', 64: 'u64'}[size]
                insn.text = ('ldrex' if load else 'strex') + {8: 'b', 16: 'h', 32: '', 64: 'd'}[size]
                g.emit(f'u32 ea = {g.R(rn)};')
                if load:
                    g.emit(f'u64 val = {rdfn}(c, ea);')
                    g.emit('c->excl_on = 1; c->excl_addr = ea; c->excl_val = val;')
                    if size == 64:
                        g.emit(f'c->r[{rd}] = u32(val); c->r[{rd + 1}] = u32(val >> 32);')
                    else:
                        g.set_reg(rd, 'u32(val)')
                else:
                    val = f'(u64({g.R(rt)}) | (u64({g.R(rt + 1)}) << 32))' if size == 64 else g.R(rt)
                    g.emit(f'u32 fail = 1;')
                    g.emit(f'if (c->excl_on && c->excl_addr == ea && u64({rdfn}(c, ea)) == c->excl_val) '
                           f'{{ {wrfn}(c, ea, {cast}({val})); fail = 0; }}')
                    g.emit('c->excl_on = 0;')
                    g.set_reg(rd, 'fail')
            else:
                ok = False
        elif bit(w, 4) and bit(w, 7):
            ok = extra_load_store(g, w)
        elif bits(w, 24, 23) == 0b10 and bit(w, 20) == 0:
            op = bits(w, 22, 21)
            low = bits(w, 7, 4)
            if low == 0b0000:
                if op in (0, 2):  # MRS (SPSR reads return CPSR: there is no SPSR in user mode)
                    insn.text = 'mrs'
                    g.set_reg(bits(w, 15, 12), 'cpsr(c)')
                elif op == 1:  # MSR CPSR, register
                    insn.text = 'msr'
                    mask = (0xFF000000 if bit(w, 19) else 0) | (0x00FF0000 if bit(w, 18) else 0)
                    g.emit(f'msr(c, {g.R(w & 0xF)}, 0x{mask:X}u);')
                else:
                    insn.text = 'msr spsr'
            elif low == 0b0001 and op == 1:
                insn.text = 'bx'
                g.emit(f'tgt = {g.R(w & 0xF)};')
                insn.kind = 'jump'
                insn.interwork = True
            elif low == 0b0001 and op == 3:
                insn.text = 'clz'
                g.set_reg(bits(w, 15, 12), f'u32(__builtin_clzg({g.R(w & 0xF)}, 32))')
            elif low == 0b0010 and op == 1:
                insn.text = 'bxj'
                g.emit(f'tgt = {g.R(w & 0xF)};')
                insn.kind = 'jump'
                insn.interwork = True
            elif low == 0b0011 and op == 1:
                insn.text = 'blx'
                g.emit(f'tgt = {g.R(w & 0xF)};')
                insn.kind = 'callr'
            elif low == 0b0101:
                rn, rd, rm = bits(w, 19, 16), bits(w, 15, 12), w & 0xF
                a, b = f's32({g.R(rm)})', f's32({g.R(rn)})'
                insn.text = ['qadd', 'qsub', 'qdadd', 'qdsub'][op]
                if op == 0:
                    g.set_reg(rd, f'u32(qadd(c, {a}, {b}))')
                elif op == 1:
                    g.set_reg(rd, f'u32(qsub(c, {a}, {b}))')
                elif op == 2:
                    g.set_reg(rd, f'u32(qadd(c, {a}, qadd(c, {b}, {b})))')
                else:
                    g.set_reg(rd, f'u32(qsub(c, {a}, qadd(c, {b}, {b})))')
            elif low == 0b0111 and op == 1:
                insn.text = 'bkpt'
                return undef('bkpt')
            elif bit(w, 7) and not bit(w, 4):
                halfword_ops(g, w)
            else:
                ok = False
        else:
            data_processing(g, w)
    elif t == 0b001:
        if bits(w, 24, 23) == 0b10 and bit(w, 20) == 0:
            op = bits(w, 22, 21)
            if op == 1 and bit(w, 22) == 0:
                if bits(w, 19, 16) == 0:  # hints: NOP, YIELD, WFE, WFI, SEV
                    insn.text = 'hint'
                else:
                    insn.text = 'msr'
                    v, _ = imm_rot(w)
                    mask = (0xFF000000 if bit(w, 19) else 0) | (0x00FF0000 if bit(w, 18) else 0)
                    g.emit(f'msr(c, 0x{v:X}u, 0x{mask:X}u);')
            elif op == 3 and bit(w, 22):
                insn.text = 'msr spsr'
            else:
                ok = False  # MOVW/MOVT are ARMv6T2+
        else:
            data_processing(g, w)
    elif t == 0b010:
        load_store(g, w)
    elif t == 0b011:
        if bit(w, 4) == 0:
            load_store(g, w)
        elif bits(w, 27, 20) == 0x7F and bits(w, 7, 4) == 0xF:
            return undef('udf')
        else:
            ok = media(g, w)
    elif t == 0b100:
        ok = block_transfer(g, w)
    elif t == 0b101:
        off = sx(w & 0xFFFFFF, 24) << 2
        insn.target = (addr + 8 + off) & 0xFFFFFFFF
        if bit(w, 24):
            insn.kind = 'bl'
            insn.text = 'bl'
        else:
            insn.kind = 'b'
            insn.text = 'b'
    elif t == 0b110:
        cp = bits(w, 11, 8)
        if cp in (10, 11):
            ok = vfp.load_store(g, w)
        else:
            ok = False
    else:
        if bit(w, 24):
            insn.kind = 'svc'
            insn.imm = w & 0xFFFFFF
            insn.text = 'svc'
        else:
            cp = bits(w, 11, 8)
            if cp in (10, 11):
                ok = vfp.data(g, w)
            elif bit(w, 4):
                ok = coproc_reg(g, w, ctx)
            else:
                ok = False
    if not ok:
        return undef()
    insn.body = g.lines
    return insn
