"""Function discovery and control-flow recovery."""

from collections import deque

from . import arm, thumb

MAX_FUNC_INSNS = 200000


class Decoder:
    def __init__(self, img):
        self.img = img
        self.ctx = arm.Ctx(module_relative=img.module_relative, read_word=img.read32,
                           is_const=img.is_const)
        self.cache = {}

    def at(self, key):
        """Decode at key = address | thumb."""
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        a = key & ~1
        if key & 1:
            if not self.img.in_code(a, 2):
                insn = None
            else:
                hw = self.img.read16(a)
                nxt = self.img.read16(a + 2) if self.img.in_code(a + 2, 2) else None
                insn = thumb.decode(hw, nxt, a, self.ctx)
        else:
            if a & 3 or not self.img.in_code(a, 4):
                insn = None
            else:
                insn = arm.decode(self.img.read32(a), a, self.ctx)
        self.cache[key] = insn
        return insn


def next_key(insn):
    return insn.end | (1 if insn.thumb else 0)


def writes_reg(insn, r):
    """Conservative: could this (ARM) instruction change register r?"""
    w = insn.word
    if insn.kind in ('bl', 'callr', 'svc'):
        return r in (0, 1, 2, 3, 12, 14)
    t = arm.bits(w, 27, 25)
    if t in (0b000, 0b001):
        if arm.bits(w, 24, 23) == 0b10 and not arm.bit(w, 20):
            return arm.bits(w, 15, 12) == r  # misc (mrs, clz, ...)
        if arm.bits(w, 7, 4) == 0b1001 and t == 0:
            return r in (arm.bits(w, 19, 16), arm.bits(w, 15, 12))
        opc = arm.bits(w, 24, 21)
        if 8 <= opc <= 11:
            return False  # tst / teq / cmp / cmn
        return arm.bits(w, 15, 12) == r or (t == 0 and arm.bit(w, 4) and arm.bit(w, 7)
                                            and (arm.bit(w, 21) or not arm.bit(w, 24))
                                            and arm.bits(w, 19, 16) == r)
    if t in (0b010, 0b011):
        if arm.bit(w, 20) and arm.bits(w, 15, 12) == r:
            return True
        return (arm.bit(w, 21) or not arm.bit(w, 24)) and arm.bits(w, 19, 16) == r
    if t == 0b100:
        return (arm.bit(w, 20) and arm.bit(w, r)) or (arm.bit(w, 21) and arm.bits(w, 19, 16) == r)
    return True


def jump_table(dec, insn):
    """Recognise `cmp rX, #N ; ... ; ldrlo pc, [pc, rX, lsl #2] ; b default ; .word targets...`
    and `addls pc, pc, rX, lsl #2 ; b default ; b case0 ...`. Returns (index_reg, targets) or None."""
    img = dec.img
    if insn.thumb or insn.cond not in (3, 9):  # LO / LS
        return None
    w = insn.word
    ldr_pc = (w & 0x0FFFFFF0) == 0x079FF100  # ldr pc, [pc, rX, lsl #2]
    add_pc = (w & 0x0FFFFFF0) == 0x008FF100  # add pc, pc, rX, lsl #2
    if not (ldr_pc or add_pc):
        return None
    rx = w & 0xF
    count = None
    # Find the bounds check: the nearest `cmp rX, #imm` before, with rX unchanged since.
    for back in range(1, 12):
        a = insn.addr - 4 * back
        if not img.in_code(a, 4):
            break
        prev = dec.at(a)
        if prev is None or prev.kind == 'undef':
            break
        pw = prev.word
        if prev.cond == 14 and (pw & 0x0FF00000) == 0x03500000 and arm.bits(pw, 19, 16) == rx:
            count, _ = arm.imm_rot(pw)
            if insn.cond == 9:
                count += 1
            break
        if prev.kind in ('b', 'jump') and prev.cond == 14:
            break
        if writes_reg(prev, rx):
            break
    if count is None and add_pc:
        return None
    table = insn.addr + 8
    limit = count if count is not None else 4096
    if limit > 4096:
        return None
    targets = []
    for i in range(limit):
        a = table + 4 * i
        if not img.in_code(a, 4):
            break
        if ldr_pc:
            if img.module_relative:
                rel = img.relocs.get(a)
                if rel is None or rel[0] != img.name:
                    break
                t = rel[1]
            else:
                t = img.read32(a)
            if t & 3 or not img.in_code(t, 4):
                break
            targets.append(t)
        else:
            targets.append(a)
    if count is not None and len(targets) != count:
        return None
    if not targets:
        return None
    return rx, targets, ldr_pc


class Function:
    def __init__(self, key):
        self.key = key            # entry address | thumb
        self.insns = {}           # key -> Insn
        self.labels = set()       # keys that need a label
        self.tables = {}          # key of jump-table insn -> (reg, targets, is_load)
        self.calls = set()        # direct call targets
        self.tails = set()        # direct tail-call targets


def explore(dec, key, entries, discover):
    """Collect the instructions of the function at `key`.

    With discover=True every branch target is followed (to find calls and literals);
    otherwise branches to other known entries become tail calls."""
    f = Function(key)
    work = deque([key])
    f.labels.add(key)
    seen = set()
    while work:
        k = work.popleft()
        if k in seen:
            continue
        seen.add(k)
        if len(seen) > MAX_FUNC_INSNS:
            break
        insn = dec.at(k)
        if insn is None:
            f.insns[k] = None
            continue
        f.insns[k] = insn
        nk = next_key(insn)
        kind = insn.kind
        if kind in ('op', 'svc', 'callr'):
            work.append(nk)
            if kind == 'callr':
                f.labels.add(nk)
        elif kind == 'bl':
            f.calls.add(insn.target)
            f.labels.add(nk)
            work.append(nk)
        elif kind == 'b':
            t = insn.target
            if insn.cond != 14:
                work.append(nk)
            if not discover and t in entries and t != key:
                f.tails.add(t)
            else:
                f.labels.add(t)
                work.append(t)
        elif kind == 'jump':
            jt = jump_table(dec, insn)
            if jt:
                f.tables[k] = jt
                for t in jt[1]:
                    f.labels.add(t)
                    work.append(t)
            if insn.cond != 14 or jt:
                work.append(nk)
                f.labels.add(nk)
        elif kind == 'undef':
            pass
    return f


def thumb_writes(w):
    """Registers a 16-bit Thumb instruction may write, or None if unknown / control flow."""
    if (w >> 13) == 0b000:
        return {w & 7}
    if (w >> 13) == 0b001:
        return set() if (w >> 11) & 3 == 1 else {(w >> 8) & 7}
    if (w >> 10) == 0b010000:
        return set() if ((w >> 6) & 0xF) in (8, 10, 11) else {w & 7}
    if (w >> 10) == 0b010001:
        op = (w >> 8) & 3
        if op in (0, 2):
            return {((w >> 4) & 8) | (w & 7)}
        return set() if op == 1 else None
    if (w >> 11) == 0b01001:
        return {(w >> 8) & 7}
    if (w >> 12) == 0b0101:
        return {w & 7} if ((w >> 9) & 7) >= 3 else set()
    if (w >> 13) == 0b011 or (w >> 12) == 0b1000:
        return {w & 7} if (w >> 11) & 1 else set()
    if (w >> 12) in (0b1001, 0b1010):
        return {(w >> 8) & 7} if ((w >> 12) == 0b1010 or (w >> 11) & 1) else set()
    if (w >> 12) == 0b1011:
        if (w >> 9) == 0b1011110:
            return {r for r in range(8) if (w >> r) & 1} | ({15} if (w >> 8) & 1 else set())
        if (w >> 8) in (0b10110010, 0b10111010):
            return {w & 7}
        return set()
    if (w >> 12) == 0b1100:
        return {r for r in range(8) if (w >> r) & 1} | {(w >> 8) & 7}
    return None


def computed_pointers(img, f):
    """Addresses formed PC-relatively in straight-line code, e.g. `ldr r1, [pc, #x] ; add r1, pc`
    or `adr r1, label`: likely function pointers handed to other code."""
    out = []
    known = {}
    for k in sorted(f.insns):
        insn = f.insns[k]
        if insn is None:
            known = {}
            continue
        if k in f.labels:
            known = {}
        w = insn.word
        if insn.thumb:
            if insn.size != 2:
                known = {}
                continue
            if (w >> 11) == 0b01001:  # ldr rd, [pc, #imm]
                rd = (w >> 8) & 7
                lit = ((insn.addr + 4) & ~3) + (w & 0xFF) * 4
                known[rd] = img.read32(lit) if img.in_code(lit, 4) or img.is_const(lit, 4) else None
                continue
            if (w & 0xFF78) == 0x4478:  # add rd, pc
                rd = ((w >> 4) & 8) | (w & 7)
                if known.get(rd) is not None:
                    v = (known[rd] + insn.addr + 4) & 0xFFFFFFFF
                    out.append(v)
                known.pop(rd, None)
                continue
            if (w >> 11) == 0b10100:  # adr rd, label
                out.append(((insn.addr + 4) & ~3) + (w & 0xFF) * 4)
                continue
            written = thumb_writes(w)
            if written is None or insn.kind != 'op':
                known = {}
            else:
                for r in written:
                    known.pop(r, None)
            continue
        # ARM
        if insn.cond == 14 and (w & 0x0F7F0000) == 0x051F0000 and arm.bit(w, 20):  # ldr rd, [pc, #imm]
            rd = arm.bits(w, 15, 12)
            off = w & 0xFFF
            lit = insn.addr + 8 + (off if arm.bit(w, 23) else -off)
            known[rd] = img.read32(lit) if img.in_code(lit, 4) or img.is_const(lit, 4) else None
            continue
        if insn.cond == 14 and (w & 0x0FE00010) == 0x00800000 and arm.bits(w, 11, 5) == 0:
            rn, rd, rm = arm.bits(w, 19, 16), arm.bits(w, 15, 12), w & 0xF
            if 15 in (rn, rm):
                other = rm if rn == 15 else rn
                if known.get(other) is not None:
                    out.append((known[other] + insn.addr + 8) & 0xFFFFFFFF)
            known.pop(rd, None)
            continue
        if insn.cond == 14 and (w & 0x0FEF0000) in (0x028F0000, 0x024F0000):  # adr (add/sub rd, pc, #imm)
            v, _ = arm.imm_rot(w)
            out.append((insn.addr + 8 + (v if arm.bit(w, 23) else -v)) & 0xFFFFFFFF)
            known.pop(arm.bits(w, 15, 12), None)
            continue
        if insn.kind != 'op':
            known = {}
        else:
            known.pop(arm.bits(w, 15, 12), None)
    return out


def plausible_entry(dec, key, limit=24):
    """Cheap check that `key` starts real code: no undefined instruction before the first
    unconditional control transfer."""
    k = key
    for _ in range(limit):
        insn = dec.at(k)
        if insn is None or insn.kind == 'undef':
            return False
        if insn.cond == 14 and insn.kind in ('b', 'jump'):
            return True
        k = next_key(insn)
    return True


def discover(img, seeds, candidates=()):
    """Find all function entries in an image, starting from `seeds` (address | thumb, trusted)
    and `candidates` (possible function pointers found in data, checked before use)."""
    dec = Decoder(img)
    entries = set()
    work = deque(s for s in seeds if dec.at(s) is not None)
    work.extend(c for c in candidates if plausible_entry(dec, c))
    candidates = set()
    while work:
        k = work.popleft()
        if k in entries:
            continue
        entries.add(k)
        f = explore(dec, k, entries, discover=True)
        for t in f.calls:
            if t not in entries and dec.at(t) is not None:
                work.append(t)
        for v in computed_pointers(img, f):
            if img.in_code(v & ~1, 2) and (v & 3) in (0, 1, 3):
                candidates.add(v if (v & 1) or not (v & 2) else v)
        for insn in f.insns.values():
            if insn is None or insn.literal is None:
                continue
            lit = insn.literal
            # Function pointers loaded from literal pools
            if img.module_relative:
                rel = img.relocs.get(lit)
                if rel and rel[0] == img.name and rel[2] == 0:
                    candidates.add(rel[1])
            elif img.in_code(lit, 4) or img.is_const(lit, 4):
                v = img.read32(lit)
                if img.in_code(v & ~1, 2):
                    candidates.add(v)
        for c in list(candidates):
            candidates.discard(c)
            if c not in entries and plausible_entry(dec, c):
                work.append(c)
    return dec, entries
