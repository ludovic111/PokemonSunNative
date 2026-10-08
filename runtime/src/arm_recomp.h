// CPU backend that runs the game's ahead-of-time translated code inside Azahar's kernel.

#pragma once

#include <memory>
#include <unordered_map>
#include <vector>

#include <recomp/rt.h>

#include "core/arm/arm_interface.h"
#include "fiber.h"

namespace Core {
class System;
}
namespace Kernel {
class SVCContext;
}
namespace Memory {
class MemorySystem;
}

namespace recomp {

class ARM_Recomp final : public Core::ARM_Interface {
public:
    ARM_Recomp(Core::System& system, Memory::MemorySystem& memory, u32 core_id,
               std::shared_ptr<Core::Timing::Timer> timer);
    ~ARM_Recomp() override;

    void Run() override;
    void Step() override;
    void ClearInstructionCache() override {}
    void InvalidateCacheRange(u32, std::size_t) override {}
    void ClearExclusiveState() override {
        cpu.excl_on = 0;
    }
    void SetPageTable(const std::shared_ptr<Memory::PageTable>& page_table) override;
    void SetPC(u32 addr) override;
    u32 GetPC() const override {
        return cpu.r[15];
    }
    u32 GetReg(int index) const override {
        return cpu.r[index];
    }
    void SetReg(int index, u32 value) override {
        cpu.r[index] = value;
    }
    u32 GetVFPReg(int index) const override {
        return cpu.s[index];
    }
    void SetVFPReg(int index, u32 value) override {
        cpu.s[index] = value;
    }
    u32 GetVFPSystemReg(VFPSystemRegister reg) const override;
    void SetVFPSystemReg(VFPSystemRegister reg, u32 value) override;
    u32 GetCPSR() const override {
        return cpsr(&cpu);
    }
    void SetCPSR(u32 value) override;
    u32 GetCP15Register(CP15Register reg) const override;
    void SetCP15Register(CP15Register reg, u32 value) override;
    void SaveContext(ThreadContext& ctx) override;
    void LoadContext(const ThreadContext& ctx) override;
    void PrepareReschedule() override;
    void OnThreadSwitch(u32 thread_id) override;
    void OnThreadExit(u32 thread_id) override;
    bool HasSingleInstructionBreakAccuracy() override {
        return false;
    }

    // Called from translated code (on a guest fiber)
    void Yield();
    void Svc(u32 imm, u32 next_pc);
    void CommitTicks();

    Core::System& system;
    Memory::MemorySystem& memory;
    Cpu cpu{};

protected:
    std::shared_ptr<Memory::PageTable> GetPageTable() const override {
        return page_table;
    }

private:
    static void FiberMain(Fiber* fiber);
    void RefreshBudget();
    void FreeDeadFibers();

    std::shared_ptr<Memory::PageTable> page_table;
    std::unique_ptr<Kernel::SVCContext> svc_context;
    Fiber host;                                // the scheduler's context while a guest runs
    std::unordered_map<u32, std::unique_ptr<Fiber>> fibers; // by guest thread id
    std::vector<std::unique_ptr<Fiber>> dead;  // stopped threads, freed once off their stack
    Fiber* current = nullptr;                  // fiber of the loaded thread
    u32 current_thread = 0;
    bool in_guest = false;
    s64 budget_base = 0;                       // budget value that corresponds to no ticks used
};

/// Called by ldr:ro (through Core::ExternalCpu) to tell the dispatcher where modules live.
void ModuleLoaded(const std::string& name, u32 address, u32 size);
void ModuleUnloaded(const std::string& name, u32 address);

/// Install the backend factory and module hooks into Azahar.
void InstallBackend();

} // namespace recomp
