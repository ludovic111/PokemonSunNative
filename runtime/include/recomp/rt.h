// Runtime support for code translated from the 3DS ARM11 executable.
//
// Every guest function becomes `void F(Cpu* c)`. Guest registers live in Cpu; guest memory is
// reached through the process page table (one host pointer per 4 KiB page, null for pages that
// need special handling such as GPU-cached memory or I/O).

#pragma once

#include <bit>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>

namespace recomp {

using u8 = std::uint8_t;
using u16 = std::uint16_t;
using u32 = std::uint32_t;
using u64 = std::uint64_t;
using s8 = std::int8_t;
using s16 = std::int16_t;
using s32 = std::int32_t;
using s64 = std::int64_t;

struct Cpu;
using HostFn = void (*)(Cpu*);

struct Cpu {
    u32 r[16];
    // Flags, each 0 or 1
    u32 n, z, c, v, q;
    u32 ge; // GE[3:0]
    u32 t;  // Thumb state
    u32 cpsr_other; // remaining CPSR bits (mode, E, A, I, F)
    u32 fpscr;
    u32 fpexc;
    alignas(16) u32 s[64]; // VFP registers: s[2n], s[2n+1] form dn
    s64 budget;            // ticks left in the current time slice
    u32 halt;              // a reschedule was requested
    u32 resume;            // re-enter a function at r[15] instead of its entry
    u32 excl_on, excl_addr;
    u64 excl_val;
    u8** pt;               // page table: host pointer per guest page
    u32 tls_uro, tls_urw;  // CP15 thread registers
    void* backend;         // the ARM_Recomp that owns this Cpu
};

// ---- Slow paths and services, implemented by the runtime ---------------------------------------

u8 rd8_slow(Cpu* c, u32 a);
u16 rd16_slow(Cpu* c, u32 a);
u32 rd32_slow(Cpu* c, u32 a);
u64 rd64_slow(Cpu* c, u32 a);
void wr8_slow(Cpu* c, u32 a, u8 v);
void wr16_slow(Cpu* c, u32 a, u16 v);
void wr32_slow(Cpu* c, u32 a, u32 v);
void wr64_slow(Cpu* c, u32 a, u64 v);

void yield(Cpu* c);                         // time slice used up or reschedule requested
void svc(Cpu* c, u32 imm, u32 next_pc);     // supervisor call
[[noreturn]] void undefined(Cpu* c, u32 pc, u32 insn);
void set_fpscr(Cpu* c, u32 value);          // VMSR: also updates host rounding / flush modes

// Indirect control flow. `jump` is used for a branch to a computed address that is neither the
// function's own return address nor one of its blocks; it calls the target if it is a known
// function (a tail call), otherwise it leaves the address in r[15] for an outer frame.
void call(Cpu* c, u32 target);              // indirect call (BLX reg): runs target until it returns
HostFn lookup(u32 target);                  // function starting at target (bit 0 = Thumb), or null
void bad_return(Cpu* c, u32 expected);      // a call came back to an unexpected address at the top

// ---- Memory ------------------------------------------------------------------------------------

#define RT_LIKELY(x) __builtin_expect(!!(x), 1)
#define RT_UNLIKELY(x) __builtin_expect(!!(x), 0)

inline u8 rd8(Cpu* c, u32 a) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p))
        return p[a & 0xFFF];
    return rd8_slow(c, a);
}
inline u16 rd16(Cpu* c, u32 a) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p && (a & 0xFFF) <= 0xFFE)) {
        u16 v;
        std::memcpy(&v, p + (a & 0xFFF), 2);
        return v;
    }
    return rd16_slow(c, a);
}
inline u32 rd32(Cpu* c, u32 a) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p && (a & 0xFFF) <= 0xFFC)) {
        u32 v;
        std::memcpy(&v, p + (a & 0xFFF), 4);
        return v;
    }
    return rd32_slow(c, a);
}
inline u64 rd64(Cpu* c, u32 a) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p && (a & 0xFFF) <= 0xFF8)) {
        u64 v;
        std::memcpy(&v, p + (a & 0xFFF), 8);
        return v;
    }
    return rd64_slow(c, a);
}
inline void wr8(Cpu* c, u32 a, u8 v) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p))
        p[a & 0xFFF] = v;
    else
        wr8_slow(c, a, v);
}
inline void wr16(Cpu* c, u32 a, u16 v) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p && (a & 0xFFF) <= 0xFFE))
        std::memcpy(p + (a & 0xFFF), &v, 2);
    else
        wr16_slow(c, a, v);
}
inline void wr32(Cpu* c, u32 a, u32 v) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p && (a & 0xFFF) <= 0xFFC))
        std::memcpy(p + (a & 0xFFF), &v, 4);
    else
        wr32_slow(c, a, v);
}
inline void wr64(Cpu* c, u32 a, u64 v) {
    u8* p = c->pt[a >> 12];
    if (RT_LIKELY(p && (a & 0xFFF) <= 0xFF8))
        std::memcpy(p + (a & 0xFFF), &v, 8);
    else
        wr64_slow(c, a, v);
}

// ---- Time --------------------------------------------------------------------------------------

inline void tick(Cpu* c, s64 n) {
    c->budget -= n;
    if (RT_UNLIKELY(c->budget <= 0))
        yield(c);
}

// ---- Flags and arithmetic ----------------------------------------------------------------------

inline void nz(Cpu* c, u32 r) {
    c->n = r >> 31;
    c->z = r == 0;
}
inline u32 adds(Cpu* c, u32 a, u32 b) {
    u32 r = a + b;
    c->n = r >> 31;
    c->z = r == 0;
    c->c = r < a;
    c->v = ((a ^ r) & (b ^ r)) >> 31;
    return r;
}
inline u32 adcs(Cpu* c, u32 a, u32 b) {
    u64 w = u64(a) + u64(b) + u64(c->c);
    u32 r = u32(w);
    c->n = r >> 31;
    c->z = r == 0;
    c->c = u32(w >> 32);
    c->v = ((a ^ r) & (b ^ r)) >> 31;
    return r;
}
inline u32 subs(Cpu* c, u32 a, u32 b) {
    u32 r = a - b;
    c->n = r >> 31;
    c->z = r == 0;
    c->c = a >= b;
    c->v = ((a ^ b) & (a ^ r)) >> 31;
    return r;
}
inline u32 sbcs(Cpu* c, u32 a, u32 b) {
    u64 w = u64(a) + u64(~b) + u64(c->c);
    u32 r = u32(w);
    c->n = r >> 31;
    c->z = r == 0;
    c->c = u32(w >> 32);
    c->v = ((a ^ b) & (a ^ r)) >> 31;
    return r;
}

// Shifts. `n` is the effective amount (0 = no shift); carry is updated only when shifting.
inline u32 lsl_c(u32 v, u32 n, u32& carry) {
    if (n == 0)
        return v;
    if (n < 32) {
        carry = (v >> (32 - n)) & 1;
        return v << n;
    }
    carry = n == 32 ? (v & 1) : 0;
    return 0;
}
inline u32 lsr_c(u32 v, u32 n, u32& carry) {
    if (n == 0)
        return v;
    if (n < 32) {
        carry = (v >> (n - 1)) & 1;
        return v >> n;
    }
    carry = n == 32 ? (v >> 31) : 0;
    return 0;
}
inline u32 asr_c(u32 v, u32 n, u32& carry) {
    if (n == 0)
        return v;
    if (n < 32) {
        carry = (v >> (n - 1)) & 1;
        return u32(s32(v) >> n);
    }
    carry = v >> 31;
    return u32(s32(v) >> 31);
}
// Rotate by register: n is Rs[7:0]
inline u32 ror_c(u32 v, u32 n, u32& carry) {
    if (n == 0)
        return v;
    n &= 31;
    if (n == 0) {
        carry = v >> 31;
        return v;
    }
    u32 r = (v >> n) | (v << (32 - n));
    carry = r >> 31;
    return r;
}
inline u32 rrx_c(u32 v, u32 carry_in, u32& carry) {
    carry = v & 1;
    return (v >> 1) | (carry_in << 31);
}
inline u32 lsl(u32 v, u32 n) {
    return n < 32 ? v << n : 0;
}
inline u32 lsr(u32 v, u32 n) {
    return n < 32 ? v >> n : 0;
}
inline u32 asr(u32 v, u32 n) {
    return u32(s32(v) >> (n < 32 ? n : 31));
}
inline u32 ror(u32 v, u32 n) {
    n &= 31;
    return n ? (v >> n) | (v << (32 - n)) : v;
}

inline u32 cpsr(const Cpu* c) {
    return (c->n << 31) | (c->z << 30) | (c->c << 29) | (c->v << 28) | (c->q << 27) |
           (c->ge << 16) | (c->t << 5) | c->cpsr_other;
}
inline void msr(Cpu* c, u32 value, u32 mask) {
    if (mask & 0xF0000000) {
        c->n = value >> 31;
        c->z = (value >> 30) & 1;
        c->c = (value >> 29) & 1;
        c->v = (value >> 28) & 1;
    }
    if (mask & 0x08000000)
        c->q = (value >> 27) & 1;
    if (mask & 0x000F0000)
        c->ge = (value >> 16) & 0xF;
}

// ---- Saturation, parallel arithmetic (ARMv6 media) ---------------------------------------------

inline s32 sat_s(Cpu* c, s64 v, u32 bits) { // signed saturate to `bits` bits, sets Q
    s64 hi = (s64(1) << (bits - 1)) - 1, lo = -(s64(1) << (bits - 1));
    if (v > hi) {
        c->q = 1;
        return s32(hi);
    }
    if (v < lo) {
        c->q = 1;
        return s32(lo);
    }
    return s32(v);
}
inline u32 sat_u(Cpu* c, s64 v, u32 bits) { // unsigned saturate to `bits` bits, sets Q
    s64 hi = bits >= 32 ? s64(0xFFFFFFFF) : (s64(1) << bits) - 1;
    if (v > hi) {
        c->q = 1;
        return u32(hi);
    }
    if (v < 0) {
        c->q = 1;
        return 0;
    }
    return u32(v);
}
inline s32 qadd(Cpu* c, s32 a, s32 b) {
    return sat_s(c, s64(a) + s64(b), 32);
}
inline s32 qsub(Cpu* c, s32 a, s32 b) {
    return sat_s(c, s64(a) - s64(b), 32);
}

// Parallel add/subtract. op: 0 ADD16, 1 ASX, 2 SAX, 3 SUB16, 4 ADD8, 7 SUB8
// kind: 0 signed (sets GE), 1 saturating signed, 2 halving signed,
//       4 unsigned (sets GE), 5 saturating unsigned, 6 halving unsigned
u32 parallel(Cpu* c, u32 kind, u32 op, u32 a, u32 b);

inline u32 sel(const Cpu* c, u32 a, u32 b) {
    u32 r = 0;
    for (int i = 0; i < 4; i++) {
        u32 m = 0xFFu << (8 * i);
        r |= ((c->ge >> i) & 1) ? (a & m) : (b & m);
    }
    return r;
}
inline u32 usad8(u32 a, u32 b) {
    u32 s = 0;
    for (int i = 0; i < 32; i += 8) {
        s32 d = s32((a >> i) & 0xFF) - s32((b >> i) & 0xFF);
        s += u32(d < 0 ? -d : d);
    }
    return s;
}

// ---- VFP ---------------------------------------------------------------------------------------

inline float S(const Cpu* c, u32 i) {
    return std::bit_cast<float>(c->s[i]);
}
inline void setS(Cpu* c, u32 i, float f) {
    c->s[i] = std::bit_cast<u32>(f);
}
inline double D(const Cpu* c, u32 i) {
    u64 v = u64(c->s[2 * i]) | (u64(c->s[2 * i + 1]) << 32);
    return std::bit_cast<double>(v);
}
inline void setD(Cpu* c, u32 i, double d) {
    u64 v = std::bit_cast<u64>(d);
    c->s[2 * i] = u32(v);
    c->s[2 * i + 1] = u32(v >> 32);
}
inline u64 Dbits(const Cpu* c, u32 i) {
    return u64(c->s[2 * i]) | (u64(c->s[2 * i + 1]) << 32);
}
inline void setDbits(Cpu* c, u32 i, u64 v) {
    c->s[2 * i] = u32(v);
    c->s[2 * i + 1] = u32(v >> 32);
}
template <typename F>
inline void vcmp(Cpu* c, F a, F b) {
    u32 nzcv;
    if (std::isnan(a) || std::isnan(b))
        nzcv = 0x3;
    else if (a == b)
        nzcv = 0x6;
    else if (a < b)
        nzcv = 0x8;
    else
        nzcv = 0x2;
    c->fpscr = (c->fpscr & 0x0FFFFFFF) | (nzcv << 28);
}
// Float to integer, saturating, NaN -> 0. round: true = FPSCR rounding mode, false = toward zero
template <typename F>
inline s32 f2s(F v, bool round) {
    if (std::isnan(v))
        return 0;
    if (round)
        v = std::nearbyint(v);
    if (v >= F(2147483648.0))
        return std::numeric_limits<s32>::max();
    if (v <= F(-2147483649.0))
        return std::numeric_limits<s32>::min();
    return s32(v);
}
template <typename F>
inline u32 f2u(F v, bool round) {
    if (std::isnan(v))
        return 0;
    if (round)
        v = std::nearbyint(v);
    if (v >= F(4294967296.0))
        return 0xFFFFFFFF;
    if (v <= F(-1.0))
        return 0;
    return u32(v);
}

} // namespace recomp

namespace recomp {
extern u32 mod_base[]; // load address of each translated module (0 for the main executable)
}
