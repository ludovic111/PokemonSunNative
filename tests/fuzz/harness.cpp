// Differential test: run each generated instruction in dynarmic and in its translation, compare.

#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <random>
#include <string>

#include <dynarmic/interface/A32/a32.h>
#include <dynarmic/interface/A32/config.h>
#include <dynarmic/interface/exclusive_monitor.h>

#include "harness.h"

using namespace recomp;

static constexpr u32 BASE = 0x00010000;

struct Memory {
    std::map<u32, u8> bytes;
    u8 Read(u32 a) const {
        auto it = bytes.find(a);
        if (it != bytes.end())
            return it->second;
        u32 h = a * 2654435761u;
        h ^= h >> 13;
        return u8(h * 0x5bd1e995u >> 24);
    }
    void Write(u32 a, u8 v) {
        bytes[a] = v;
    }
};

static Memory* g_mem; // memory for the translated side
static u32 g_code[2];

// ---- runtime stubs for the translated side ----
namespace recomp {
u32 mod_base[1];
u8 rd8_slow(Cpu*, u32 a) { return g_mem->Read(a); }
u16 rd16_slow(Cpu*, u32 a) { return u16(g_mem->Read(a) | (g_mem->Read(a + 1) << 8)); }
u32 rd32_slow(Cpu*, u32 a) { return rd16_slow(nullptr, a) | (u32(rd16_slow(nullptr, a + 2)) << 16); }
u64 rd64_slow(Cpu*, u32 a) { return rd32_slow(nullptr, a) | (u64(rd32_slow(nullptr, a + 4)) << 32); }
void wr8_slow(Cpu*, u32 a, u8 v) { g_mem->Write(a, v); }
void wr16_slow(Cpu*, u32 a, u16 v) { wr8_slow(nullptr, a, u8(v)); wr8_slow(nullptr, a + 1, u8(v >> 8)); }
void wr32_slow(Cpu*, u32 a, u32 v) { wr16_slow(nullptr, a, u16(v)); wr16_slow(nullptr, a + 2, u16(v >> 16)); }
void wr64_slow(Cpu*, u32 a, u64 v) { wr32_slow(nullptr, a, u32(v)); wr32_slow(nullptr, a + 4, u32(v >> 32)); }
void yield(Cpu*) {}
void svc(Cpu*, u32, u32) {}
void undefined(Cpu*, u32 pc, u32 insn) { std::fprintf(stderr, "undefined %08x %08x\n", pc, insn); std::abort(); }
void set_fpscr(Cpu* c, u32 v) { c->fpscr = v; }
void call(Cpu*, u32) {}
HostFn lookup(u32) { return nullptr; }
void bad_return(Cpu*, u32) {}
} // namespace recomp

// ---- dynarmic side ----
struct Env final : Dynarmic::A32::UserCallbacks {
    Memory mem;
    bool thumb = false;
    unsigned size = 4;
    bool exception = false;
    std::string why;

    std::optional<std::uint32_t> MemoryReadCode(u32 a) override {
        if (a == BASE) {
            if (!thumb)
                return g_code[0];
            u32 hw0 = size == 4 ? g_code[0] >> 16 : g_code[0];
            u32 hw1 = size == 4 ? g_code[0] & 0xFFFF : 0xDE00;
            return hw0 | (hw1 << 16);
        }
        return 0xE7F000F0u; // udf
    }
    u8 MemoryRead8(u32 a) override { return mem.Read(a); }
    u16 MemoryRead16(u32 a) override { return u16(mem.Read(a) | (mem.Read(a + 1) << 8)); }
    u32 MemoryRead32(u32 a) override { return MemoryRead16(a) | (u32(MemoryRead16(a + 2)) << 16); }
    u64 MemoryRead64(u32 a) override { return MemoryRead32(a) | (u64(MemoryRead32(a + 4)) << 32); }
    void MemoryWrite8(u32 a, u8 v) override { mem.Write(a, v); }
    void MemoryWrite16(u32 a, u16 v) override { MemoryWrite8(a, u8(v)); MemoryWrite8(a + 1, u8(v >> 8)); }
    void MemoryWrite32(u32 a, u32 v) override { MemoryWrite16(a, u16(v)); MemoryWrite16(a + 2, u16(v >> 16)); }
    void MemoryWrite64(u32 a, u64 v) override { MemoryWrite32(a, u32(v)); MemoryWrite32(a + 4, u32(v >> 32)); }
    void InterpreterFallback(u32, size_t) override { exception = true; why = "fallback"; }
    void CallSVC(u32) override { exception = true; why = "svc"; }
    void ExceptionRaised(u32, Dynarmic::A32::Exception e) override {
        exception = true;
        why = "exception " + std::to_string(int(e));
    }
    void AddTicks(u64) override {}
    u64 GetTicksRemaining() override { return 1000; }
};

static bool same_float_reg(u32 a, u32 b) {
    if (a == b)
        return true;
    float fa, fb;
    std::memcpy(&fa, &a, 4);
    std::memcpy(&fb, &b, 4);
    return std::isnan(fa) && std::isnan(fb);
}

int main(int argc, char** argv) {
    unsigned rounds = argc > 1 ? std::atoi(argv[1]) : 4;
    Env env;
    Dynarmic::A32::UserConfig cfg;
    cfg.callbacks = &env;
    cfg.arch_version = Dynarmic::A32::ArchVersion::v6K;
    cfg.define_unpredictable_behaviour = false;
    static Dynarmic::ExclusiveMonitor monitor(1);
    cfg.global_monitor = &monitor;
    cfg.processor_id = 0;
    Dynarmic::A32::Jit jit(cfg);
    std::mt19937 rng(1234);
    unsigned ran = 0, skipped = 0, failed = 0;
    std::map<std::string, unsigned> fail_by_text;
    for (unsigned r = 0; r < rounds; r++) {
        for (unsigned t = 0; t < num_tests; t++) {
            const TestCase& tc = tests[t];
            g_code[0] = tc.word;
            env.thumb = tc.thumb;
            env.size = tc.size;
            env.exception = false;
            env.mem.bytes.clear();
            Memory mem2;
            g_mem = &mem2;

            Cpu c{};
            std::array<u32, 16> regs{};
            for (int i = 0; i < 15; i++) {
                u32 v = rng();
                switch (rng() % 4) {
                case 0: v &= 0xFF; break;
                case 1: v = 0x08000000 + (v & 0xFFFF); break;
                case 2: v = (rng() & 1) ? 0xFFFFFFFF - (v & 0xF) : 0x80000000 + (v & 0xF); break;
                default: break;
                }
                regs[i] = v;
            }
            regs[15] = BASE;
            u32 cpsr = (rng() & 0xF80F0000) | 0x10 | (tc.thumb ? 0x20 : 0);
            std::array<u32, 64> ext{};
            for (int i = 0; i < 32; i++) {
                float f = float(int(rng() % 2000) - 1000) / float(1 + rng() % 64);
                u32 v = rng() % 8 == 0 ? rng() : std::bit_cast<u32>(f);
                ext[i] = v;
            }
            jit.Regs() = regs;
            jit.SetCpsr(cpsr);
            jit.ExtRegs() = ext;
            jit.SetFpscr(0);
            jit.ClearCache();
            if (std::getenv("FUZZ_TRACE"))
                std::fprintf(stderr, "%08x %u\n", tc.word, tc.thumb);
            jit.Step();
            if (env.exception) {
                skipped++;
                continue;
            }
            if (!tc.fn) {
                // Informational: dynarmic also accepts later-architecture encodings (Thumb-2,
                // ARMv7, VFPv3/4) that the 3DS's ARM11 does not have.
                if (fail_by_text["rejected (informational)"]++ < 0)
                    std::printf("REJECTED %08x (thumb %u): dynarmic runs it, the decoder does not\n",
                                tc.word, tc.thumb);
                continue;
            }
            for (int i = 0; i < 16; i++)
                c.r[i] = regs[i];
            c.n = cpsr >> 31; c.z = (cpsr >> 30) & 1; c.c = (cpsr >> 29) & 1; c.v = (cpsr >> 28) & 1;
            c.q = (cpsr >> 27) & 1; c.ge = (cpsr >> 16) & 0xF; c.t = tc.thumb; c.cpsr_other = 0x10;
            for (int i = 0; i < 64; i++) c.s[i] = ext[i];
            static u8* null_pt[1 << 20];
            c.pt = null_pt;
            tc.fn(&c);
            ran++;

            std::string diff;
            for (int i = 0; i < 16; i++)
                if (c.r[i] != jit.Regs()[i])
                    diff += "r" + std::to_string(i) + " exp " + std::to_string(jit.Regs()[i]) + " got " + std::to_string(c.r[i]) + "; ";
            u32 want_cpsr = jit.Cpsr() & 0xF80F0020;
            u32 got_cpsr = recomp::cpsr(&c) & 0xF80F0020;
            if (want_cpsr != got_cpsr) {
                char buf[64];
                std::snprintf(buf, sizeof buf, "cpsr exp %08x got %08x; ", want_cpsr, got_cpsr);
                diff += buf;
            }
            for (int i = 0; i < 32; i++)
                if (!same_float_reg(c.s[i], jit.ExtRegs()[i])) {
                    char buf[64];
                    std::snprintf(buf, sizeof buf, "s%d exp %08x got %08x; ", i, jit.ExtRegs()[i], c.s[i]);
                    diff += buf;
                }
            if ((c.fpscr & 0xF0000000) != (jit.Fpscr() & 0xF0000000))
                diff += "fpscr flags; ";
            if (env.mem.bytes != mem2.bytes)
                diff += "memory; ";
            if (!diff.empty()) {
                failed++;
                if (fail_by_text[tc.text]++ < 3)
                    std::printf("FAIL %s %08x (thumb %u): %s\n", tc.text, tc.word, tc.thumb, diff.c_str());
            }
        }
    }
    std::printf("ran %u, skipped %u (dynarmic rejected), failed %u\n", ran, skipped, failed);
    for (auto& [k, v] : fail_by_text)
        std::printf("  %-12s %u\n", k.c_str(), v);
    return failed ? 1 : 0;
}
