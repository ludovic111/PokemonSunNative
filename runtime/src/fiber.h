// Fibers: one host stack per guest thread, switched cooperatively.

#pragma once

#include <cstddef>
#include <cstdint>

extern "C" void recomp_fiber_switch(void** save_sp, void* new_sp);
extern "C" void recomp_fiber_start();

namespace recomp {

class Fiber {
public:
    using Entry = void (*)(Fiber*);

    /// A fiber for the calling (host) thread: only used as a place to save its context.
    Fiber() = default;
    /// A new fiber that runs entry(this) the first time it is switched to.
    Fiber(Entry entry, void* user, std::size_t stack_size);
    ~Fiber();
    Fiber(const Fiber&) = delete;
    Fiber& operator=(const Fiber&) = delete;

    /// Save the current context into `from` and continue `to`.
    static void Switch(Fiber& from, Fiber& to) {
        recomp_fiber_switch(&from.sp, to.sp);
    }

    void* user = nullptr;

private:
    void* sp = nullptr;
    void* stack = nullptr;
    std::size_t stack_size = 0;
};

} // namespace recomp
