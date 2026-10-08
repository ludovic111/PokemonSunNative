#include "arm_recomp.h"

#include <algorithm>
#include <cstdlib>
#include <mutex>
#include <string>

#include <recomp/tables.h>

#include "common/logging/log.h"
#include "core/arm/external_cpu.h"
#include "core/core.h"
#include "core/core_timing.h"
#include "core/hle/kernel/svc.h"
#include "core/memory.h"

namespace recomp {

namespace {

constexpr std::size_t FIBER_STACK_SIZE = 16 * 1024 * 1024;

ARM_Recomp& Backend(Cpu* c) {
    return *static_cast<ARM_Recomp*>(c->backend);
}

// ---- Code lookup -------------------------------------------------------------------------------

struct LoadedModule {
    u32 start, end; // absolute code range
    u32 base;       // load address
    const ImageInfo* image;
};

std::vector<LoadedModule> loaded_modules;

struct CacheSlot {
    u32 key;
    HostFn fn;
};
constexpr u32 CACHE_SIZE = 1 << 16;
CacheSlot lookup_cache[CACHE_SIZE];

HostFn Find(const Entry* entries, u32 count, u32 key) {
    const Entry* end = entries + count;
    const Entry* it = std::lower_bound(entries, end, key,
                                       [](const Entry& e, u32 k) { return e.key < k; });
    return (it != end && it->key == key) ? it->fn : nullptr;
}

template <bool Labels>
HostFn LookupIn(u32 target) {
    const u32 addr = target & ~1u;
    const ImageInfo& main = images[0];
    if (addr >= main.code_start && addr < main.code_end) {
        return Labels ? Find(main.labels, main.num_labels, target)
                      : Find(main.entries, main.num_entries, target);
    }
    for (const LoadedModule& m : loaded_modules) {
        if (addr >= m.start && addr < m.end) {
            const u32 key = target - m.base;
            return Labels ? Find(m.image->labels, m.image->num_labels, key)
                          : Find(m.image->entries, m.image->num_entries, key);
        }
    }
    return nullptr;
}

/// Describe a guest address as `module offset` (the format of tools/recomp/hints/*.txt).
std::string Where(u32 target) {
    const u32 addr = target & ~1u;
    if (addr >= images[0].code_start && addr < images[0].code_end)
        return fmt::format("static {:X}", target);
    for (const LoadedModule& m : loaded_modules) {
        if (addr >= m.start && addr < m.end)
            return fmt::format("{} {:X}", m.image->name, target - m.base);
    }
    return fmt::format("? {:X}", target);
}

[[noreturn]] void Fatal(Cpu* c, const std::string& what) {
    LOG_CRITICAL(Core_ARM11, "{}", what);
    for (int i = 0; i < 16; i += 4) {
        LOG_CRITICAL(Core_ARM11, "r{:<2} {:08X}  r{:<2} {:08X}  r{:<2} {:08X}  r{:<2} {:08X}", i,
                     c->r[i], i + 1, c->r[i + 1], i + 2, c->r[i + 2], i + 3, c->r[i + 3]);
    }
    LOG_CRITICAL(Core_ARM11, "cpsr {:08X}", cpsr(c));
    for (const LoadedModule& m : loaded_modules) {
        LOG_CRITICAL(Core_ARM11, "module {} at {:08X}", m.image->name, m.base);
    }
    Common::Log::Stop();
    std::abort();
}

} // namespace

HostFn lookup(u32 target) {
    CacheSlot& slot = lookup_cache[(target >> 1) & (CACHE_SIZE - 1)];
    if (slot.key == target && slot.fn)
        return slot.fn;
    HostFn fn = LookupIn<false>(target);
    if (fn) {
        slot.key = target;
        slot.fn = fn;
    }
    return fn;
}

void call(Cpu* c, u32 target) {
    if (HostFn fn = lookup(target)) {
        fn(c);
        return;
    }
    if (HostFn fn = LookupIn<true>(target)) {
        c->r[15] = target & ~1u;
        c->t = target & 1;
        c->resume = 1;
        fn(c);
        return;
    }
    Fatal(c, fmt::format("call to {:08X} ({}): no translated code there; add it to "
                         "tools/recomp/hints", target, Where(target)));
}

void bad_return(Cpu* c, u32 expected) {
    Fatal(c, fmt::format("returned to {:08X}, expected {:08X}", c->r[15] | c->t, expected));
}

void ModuleLoaded(const std::string& name, u32 address, u32 size) {
    for (u32 i = 1; i < num_images; i++) {
        const ImageInfo& img = images[i];
        if (name != img.name)
            continue;
        mod_base[i] = address;
        loaded_modules.push_back({address + img.code_start, address + img.code_end, address, &img});
        std::fill(std::begin(lookup_cache), std::end(lookup_cache), CacheSlot{});
        LOG_INFO(Core_ARM11, "translated module {} active at {:08X}", name, address);
        return;
    }
    LOG_ERROR(Core_ARM11, "module {} loaded at {:08X} has no translated code", name, address);
}

void ModuleUnloaded(const std::string& name, u32 address) {
    std::erase_if(loaded_modules, [&](const LoadedModule& m) { return m.base == address; });
    std::fill(std::begin(lookup_cache), std::end(lookup_cache), CacheSlot{});
}

// ---- Slow memory paths ---------------------------------------------------------------------------

u8 rd8_slow(Cpu* c, u32 a) {
    return Backend(c).memory.Read8(a);
}
u16 rd16_slow(Cpu* c, u32 a) {
    return Backend(c).memory.Read16(a);
}
u32 rd32_slow(Cpu* c, u32 a) {
    return Backend(c).memory.Read32(a);
}
u64 rd64_slow(Cpu* c, u32 a) {
    return Backend(c).memory.Read64(a);
}
void wr8_slow(Cpu* c, u32 a, u8 v) {
    Backend(c).memory.Write8(a, v);
}
void wr16_slow(Cpu* c, u32 a, u16 v) {
    Backend(c).memory.Write16(a, v);
}
void wr32_slow(Cpu* c, u32 a, u32 v) {
    Backend(c).memory.Write32(a, v);
}
void wr64_slow(Cpu* c, u32 a, u64 v) {
    Backend(c).memory.Write64(a, v);
}

// ---- Services ------------------------------------------------------------------------------------

void yield(Cpu* c) {
    Backend(c).Yield();
}

void svc(Cpu* c, u32 imm, u32 next_pc) {
    Backend(c).Svc(imm, next_pc);
}

void undefined(Cpu* c, u32 pc, u32 insn) {
    Fatal(c, fmt::format("undefined instruction {:08X} at {:08X}", insn, pc));
}

void set_fpscr(Cpu* c, u32 value) {
    c->fpscr = value;
    u32 mxcsr;
    asm volatile("stmxcsr %0" : "=m"(mxcsr));
    mxcsr &= ~((3u << 13) | (1u << 15) | (1u << 6));
    static constexpr u32 rounding[4] = {0, 2, 1, 3}; // ARM RN, RP, RM, RZ -> x86 RC
    mxcsr |= rounding[(value >> 22) & 3] << 13;
    if (value & (1u << 24))
        mxcsr |= (1u << 15) | (1u << 6); // flush to zero, denormals are zero
    asm volatile("ldmxcsr %0" : : "m"(mxcsr));
}

// ---- Backend -------------------------------------------------------------------------------------

ARM_Recomp::ARM_Recomp(Core::System& system_, Memory::MemorySystem& memory_, u32 core_id,
                       std::shared_ptr<Core::Timing::Timer> timer_)
    : ARM_Interface(core_id, timer_), system(system_), memory(memory_),
      svc_context(std::make_unique<Kernel::SVCContext>(system_)) {
    cpu.backend = this;
    cpu.cpsr_other = 0x10;
    SetPageTable(memory.GetCurrentPageTable());
}

ARM_Recomp::~ARM_Recomp() = default;

void ARM_Recomp::SetPageTable(const std::shared_ptr<Memory::PageTable>& pt) {
    page_table = pt;
    cpu.pt = pt ? pt->GetPointerArray().data() : nullptr;
}

void ARM_Recomp::Run() {
    FreeDeadFibers();
    if (!current) {
        LOG_CRITICAL(Core_ARM11, "Run() without a loaded thread");
        return;
    }
    cpu.halt = 0;
    RefreshBudget();
    in_guest = true;
    Fiber::Switch(host, *current);
    in_guest = false;
}

void ARM_Recomp::Step() {
    LOG_WARNING(Core_ARM11, "single-stepping is not supported by translated code");
    Run();
}

void ARM_Recomp::RefreshBudget() {
    cpu.budget = budget_base = GetTimer().GetDowncount();
}

void ARM_Recomp::CommitTicks() {
    const s64 used = budget_base - cpu.budget;
    if (used > 0)
        GetTimer().AddTicks(static_cast<u64>(used));
    budget_base = cpu.budget;
}

void ARM_Recomp::Yield() {
    CommitTicks();
    Fiber::Switch(*current, host);
    // Resumed by Run(), which refreshed the budget.
}

void ARM_Recomp::PrepareReschedule() {
    cpu.halt = 1;
    if (in_guest) {
        // Stop at the next block boundary without charging ticks that were not used.
        budget_base -= cpu.budget;
        cpu.budget = 0;
    }
}

void ARM_Recomp::Svc(u32 imm, u32 next_pc) {
    cpu.r[15] = next_pc;
    CommitTicks();
    svc_context->CallSVC(imm);
    RefreshBudget();
    if (cpu.halt || cpu.budget <= 0)
        Yield();
}

void ARM_Recomp::SetPC(u32 addr) {
    if (in_guest && addr != cpu.r[15]) {
        LOG_WARNING(Core_ARM11, "SetPC({:08X}) while translated code is running is ignored", addr);
    }
    cpu.r[15] = addr & ~1u;
}

u32 ARM_Recomp::GetVFPSystemReg(VFPSystemRegister reg) const {
    switch (reg) {
    case VFP_FPSCR:
        return cpu.fpscr;
    case VFP_FPEXC:
        return cpu.fpexc;
    default:
        return 0;
    }
}

void ARM_Recomp::SetVFPSystemReg(VFPSystemRegister reg, u32 value) {
    switch (reg) {
    case VFP_FPSCR:
        cpu.fpscr = value;
        break;
    case VFP_FPEXC:
        cpu.fpexc = value;
        break;
    default:
        break;
    }
}

void ARM_Recomp::SetCPSR(u32 value) {
    msr(&cpu, value, 0xFFFF0000u);
    cpu.t = (value >> 5) & 1;
    cpu.cpsr_other = value & 0x0000FFDFu;
}

u32 ARM_Recomp::GetCP15Register(CP15Register reg) const {
    switch (reg) {
    case CP15_THREAD_UPRW:
        return cpu.tls_urw;
    case CP15_THREAD_URO:
        return cpu.tls_uro;
    default:
        return 0;
    }
}

void ARM_Recomp::SetCP15Register(CP15Register reg, u32 value) {
    switch (reg) {
    case CP15_THREAD_UPRW:
        cpu.tls_urw = value;
        break;
    case CP15_THREAD_URO:
        cpu.tls_uro = value;
        break;
    default:
        break;
    }
}

void ARM_Recomp::SaveContext(ThreadContext& ctx) {
    for (int i = 0; i < 16; i++)
        ctx.cpu_registers[i] = cpu.r[i];
    ctx.cpsr = cpsr(&cpu);
    for (int i = 0; i < 64; i++)
        ctx.fpu_registers[i] = cpu.s[i];
    ctx.fpscr = cpu.fpscr;
    ctx.fpexc = cpu.fpexc;
}

void ARM_Recomp::LoadContext(const ThreadContext& ctx) {
    for (int i = 0; i < 16; i++)
        cpu.r[i] = ctx.cpu_registers[i];
    SetCPSR(ctx.cpsr);
    cpu.r[15] &= ~1u;
    for (int i = 0; i < 64; i++)
        cpu.s[i] = ctx.fpu_registers[i];
    cpu.fpscr = ctx.fpscr;
    cpu.fpexc = ctx.fpexc;
    cpu.excl_on = 0;
    auto& fiber = fibers[current_thread];
    if (!fiber)
        fiber = std::make_unique<Fiber>(&ARM_Recomp::FiberMain, this, FIBER_STACK_SIZE);
    current = fiber.get();
}

void ARM_Recomp::OnThreadSwitch(u32 thread_id) {
    current_thread = thread_id;
}

void ARM_Recomp::OnThreadExit(u32 thread_id) {
    auto it = fibers.find(thread_id);
    if (it == fibers.end())
        return;
    // The thread may be exiting from its own fiber; free it once we are back on the host stack.
    dead.push_back(std::move(it->second));
    fibers.erase(it);
}

void ARM_Recomp::FreeDeadFibers() {
    for (auto& f : dead) {
        if (f.get() == current)
            current = nullptr;
    }
    dead.clear();
}

void ARM_Recomp::FiberMain(Fiber* fiber) {
    auto* self = static_cast<ARM_Recomp*>(fiber->user);
    Cpu* c = &self->cpu;
    for (;;) {
        const u32 target = c->r[15] | c->t;
        if (HostFn fn = lookup(target)) {
            fn(c);
            continue;
        }
        if (HostFn fn = LookupIn<true>(target)) {
            c->resume = 1;
            fn(c);
            continue;
        }
        Fatal(c, fmt::format("thread reached {:08X} ({}): no translated code there", target,
                             Where(target)));
    }
}

void InstallBackend() {
    auto& ext = Core::GetExternalCpu();
    ext.factory = [](Core::System& system, Memory::MemorySystem& memory, u32 core_id,
                     std::shared_ptr<Core::Timing::Timer> timer) {
        return std::make_shared<ARM_Recomp>(system, memory, core_id, std::move(timer));
    };
    ext.on_cro_loaded = ModuleLoaded;
    ext.on_cro_unloaded = ModuleUnloaded;
}

} // namespace recomp
