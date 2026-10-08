"""VFPv2 instructions (coprocessors 10 and 11) in ARM state."""

from .arm import bits, bit

FPSID = 0x410120B4  # ARM11 VFP11


def sreg(vd, d):
    return (vd << 1) | d


def dreg(vd, d):
    return (d << 4) | vd


def load_store(g, w):
    p, u, d, wb, l = bit(w, 24), bit(w, 23), bit(w, 22), bit(w, 21), bit(w, 20)
    rn = bits(w, 19, 16)
    vd = bits(w, 15, 12)
    dbl = bit(w, 8)
    imm8 = w & 0xFF
    if bits(w, 24, 21) == 0b0010:  # VMOV between two core registers and a double / two singles
        rt2, rt = rn, vd
        vm = w & 0xF
        m = bit(w, 5)
        if bits(w, 7, 6) != 0 or bit(w, 4) != 1:
            return False
        if dbl:
            dm = dreg(vm, m)
            if l:
                g.i.text = 'vmov rr<-d'
                g.emit(f'u32 lo = c->s[{2 * dm}], hi = c->s[{2 * dm + 1}];')
                g.set_reg(rt, 'lo')
                g.set_reg(rt2, 'hi')
            else:
                g.i.text = 'vmov d<-rr'
                g.emit(f'c->s[{2 * dm}] = {g.R(rt)}; c->s[{2 * dm + 1}] = {g.R(rt2)};')
        else:
            sm = sreg(vm, m)
            if sm == 31:
                return False
            if l:
                g.i.text = 'vmov rr<-ss'
                g.emit(f'u32 lo = c->s[{sm}], hi = c->s[{sm + 1}];')
                g.set_reg(rt, 'lo')
                g.set_reg(rt2, 'hi')
            else:
                g.i.text = 'vmov ss<-rr'
                g.emit(f'c->s[{sm}] = {g.R(rt)}; c->s[{sm + 1}] = {g.R(rt2)};')
        return True
    first = dreg(vd, d) * 2 if dbl else sreg(vd, d)
    base = g.R(rn)
    if p and not wb:  # VLDR / VSTR
        off = imm8 * 4
        g.i.text = 'vldr' if l else 'vstr'
        nwords = 2 if dbl else 1
        if rn == 15:
            lit = ((g.i.addr + 8) & ~3) + (off if u else -off)
            lit &= 0xFFFFFFFF
            g.i.literal = lit
            if l and g.ctx.is_const and g.ctx.is_const(lit, 4 * nwords):
                for k in range(nwords):
                    g.emit(f'c->s[{first + k}] = 0x{g.ctx.read_word(lit + 4 * k):X}u;')
                return True
            base = g.ctx.addr((g.i.addr + 8) & ~3)
        g.emit(f'u32 ea = {base} {"+" if u else "-"} {off}u;')
        for k in range(nwords):
            if l:
                g.emit(f'c->s[{first + k}] = rd32(c, ea + {4 * k}u);')
            else:
                g.emit(f'wr32(c, ea + {4 * k}u, c->s[{first + k}]);')
        return True
    # VLDM / VSTM
    if p == u or (p and not wb):
        return False
    if rn == 15:
        return False
    nwords = imm8
    nregs_words = (imm8 & ~1) if dbl else imm8
    if nwords == 0 or first + nregs_words > 64:
        return False
    g.i.text = ('vldm' if l else 'vstm') + ('ia' if u else 'db')
    if u:
        g.emit(f'u32 ea = {base};')
        g.emit(f'u32 wbv = ea + {4 * nwords}u;')
    else:
        g.emit(f'u32 ea = {base} - {4 * nwords}u;')
        g.emit('u32 wbv = ea;')
    for k in range(nregs_words):
        if l:
            g.emit(f'c->s[{first + k}] = rd32(c, ea + {4 * k}u);')
        else:
            g.emit(f'wr32(c, ea + {4 * k}u, c->s[{first + k}]);')
    if wb:
        g.emit(f'c->r[{rn}] = wbv;')
    return True


def data(g, w):
    dbl = bit(w, 8)
    if bit(w, 4):
        return transfer(g, w)
    p, d, q, r, s = bit(w, 23), bit(w, 22), bit(w, 21), bit(w, 20), bit(w, 6)
    vn, vd, vm = bits(w, 19, 16), bits(w, 15, 12), w & 0xF
    n, m = bit(w, 7), bit(w, 5)
    if dbl:
        rd, rn, rm = dreg(vd, d), dreg(vn, n), dreg(vm, m)
        get, put, T = 'D', 'setD', 'double'
    else:
        rd, rn, rm = sreg(vd, d), sreg(vn, n), sreg(vm, m)
        get, put, T = 'S', 'setS', 'float'
    Rd, Rn, Rm = f'{get}(c, {rd})', f'{get}(c, {rn})', f'{get}(c, {rm})'

    def set_(expr):
        g.emit(f'{put}(c, {rd}, {T}({expr}));')

    pqrs = (p << 3) | (q << 2) | (r << 1) | s
    if pqrs != 0b1111:
        g.emit(f'{T} a = {Rn}, b = {Rm};')
        ops = {
            0b0000: ('vmla', f'{Rd} + a * b'),
            0b0001: ('vmls', f'{Rd} - a * b'),
            0b0010: ('vnmls', f'-{Rd} + a * b'),
            0b0011: ('vnmla', f'-{Rd} - a * b'),
            0b0100: ('vmul', 'a * b'),
            0b0101: ('vnmul', '-(a * b)'),
            0b0110: ('vadd', 'a + b'),
            0b0111: ('vsub', 'a - b'),
            0b1000: ('vdiv', 'a / b'),
        }
        if pqrs not in ops:
            return False
        g.i.text, expr = ops[pqrs]
        if pqrs in (0, 1, 2, 3):
            g.emit(f'{T} prod = a * b;')
            expr = {0: f'{Rd} + prod', 1: f'{Rd} - prod', 2: f'-{Rd} + prod', 3: f'-{Rd} - prod'}[pqrs]
        set_(expr)
        return True
    ext = (vn << 1) | n
    if ext == 0b00000:
        g.i.text = 'vmov'
        if dbl:
            g.emit(f'setDbits(c, {rd}, Dbits(c, {rm}));')
        else:
            g.emit(f'c->s[{rd}] = c->s[{rm}];')
    elif ext == 0b00001:
        g.i.text = 'vabs'
        if dbl:
            g.emit(f'c->s[{2 * rd}] = c->s[{2 * rm}]; c->s[{2 * rd + 1}] = c->s[{2 * rm + 1}] & 0x7FFFFFFFu;')
        else:
            g.emit(f'c->s[{rd}] = c->s[{rm}] & 0x7FFFFFFFu;')
    elif ext == 0b00010:
        g.i.text = 'vneg'
        if dbl:
            g.emit(f'c->s[{2 * rd}] = c->s[{2 * rm}]; c->s[{2 * rd + 1}] = c->s[{2 * rm + 1}] ^ 0x80000000u;')
        else:
            g.emit(f'c->s[{rd}] = c->s[{rm}] ^ 0x80000000u;')
    elif ext == 0b00011:
        g.i.text = 'vsqrt'
        set_(f'std::sqrt({Rm})')
    elif ext in (0b01000, 0b01001):
        g.i.text = 'vcmp'
        g.emit(f'vcmp<{T}>(c, {Rd}, {Rm});')
    elif ext in (0b01010, 0b01011):
        g.i.text = 'vcmp0'
        g.emit(f'vcmp<{T}>(c, {Rd}, {T}(0));')
    elif ext == 0b01111:
        g.i.text = 'vcvt.f'
        if dbl:  # double -> single
            g.emit(f'setS(c, {sreg(vd, d)}, float(D(c, {rm})));')
        else:  # single -> double
            g.emit(f'setD(c, {dreg(vd, d)}, double(S(c, {rm})));')
    elif ext in (0b10000, 0b10001):
        g.i.text = 'vcvt.from_int'
        sm = sreg(vm, m)
        src = f'c->s[{sm}]' if ext == 0b10000 else f's32(c->s[{sm}])'
        set_(src)
    elif ext in (0b11000, 0b11001, 0b11010, 0b11011):
        g.i.text = 'vcvt.to_int'
        sd = sreg(vd, d)
        signed = ext in (0b11010, 0b11011)
        toward_zero = ext & 1
        fn = 'f2s' if signed else 'f2u'
        g.emit(f'c->s[{sd}] = u32({fn}<{T}>({Rm}, {"false" if toward_zero else "true"}));')
    else:
        return False
    return True


def transfer(g, w):
    """VMOV core <-> single / half-double, VMRS, VMSR."""
    opc1 = bits(w, 23, 21)
    l = bit(w, 20)
    vn = bits(w, 19, 16)
    rt = bits(w, 15, 12)
    n = bit(w, 7)
    dbl = bit(w, 8)
    if bits(w, 6, 5) != 0 or (w & 0xF) != 0:
        return False
    if opc1 == 0b111 and not dbl:
        if l:
            g.i.text = 'vmrs'
            src = {0b0001: 'c->fpscr', 0b0000: f'0x{FPSID:X}u', 0b1000: 'c->fpexc',
                   0b0110: '0u', 0b0111: '0x11111111u'}.get(vn)
            if src is None:
                return False
            if rt == 15:
                g.emit(f'msr(c, {src}, 0xF0000000u);')
            else:
                g.set_reg(rt, src)
        else:
            g.i.text = 'vmsr'
            if vn == 0b0001:
                g.emit(f'set_fpscr(c, {g.R(rt)});')
            elif vn == 0b1000:
                g.emit(f'c->fpexc = {g.R(rt)};')
            elif vn == 0b0000:
                pass
            else:
                return False
        return True
    if opc1 == 0b000 and not dbl:
        sn = sreg(vn, n)
        g.i.text = 'vmov'
        if l:
            g.set_reg(rt, f'c->s[{sn}]')
        else:
            g.emit(f'c->s[{sn}] = {g.R(rt)};')
        return True
    if opc1 in (0b000, 0b001) and dbl and n == 0:
        idx = 2 * vn + (opc1 & 1)
        g.i.text = 'vmov.32'
        if l:
            g.set_reg(rt, f'c->s[{idx}]')
        else:
            g.emit(f'c->s[{idx}] = {g.R(rt)};')
        return True
    return False
