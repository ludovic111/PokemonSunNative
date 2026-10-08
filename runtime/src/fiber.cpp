#include "fiber.h"

#include <cstring>
#include <new>
#include <sys/mman.h>

namespace recomp {

Fiber::Fiber(Entry entry, void* user_, std::size_t size) : user(user_), stack_size(size) {
    stack = mmap(nullptr, stack_size, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | MAP_STACK, -1, 0);
    if (stack == MAP_FAILED)
        throw std::bad_alloc();
    // Guard page at the bottom so runaway recursion faults instead of corrupting memory.
    mprotect(stack, 4096, PROT_NONE);

    // Build the frame recomp_fiber_switch expects to pop: [mxcsr|fcw] r15 r14 r13 r12 rbx rbp ret.
    // After `ret` into recomp_fiber_start the stack pointer is 16-byte aligned, so its `call`
    // enters the entry function with the standard alignment.
    auto top = reinterpret_cast<std::uintptr_t>(stack) + stack_size;
    top &= ~std::uintptr_t(15);
    auto* p = reinterpret_cast<std::uint64_t*>(top);
    *--p = reinterpret_cast<std::uint64_t>(&recomp_fiber_start);    // return address
    *--p = 0;                                                       // rbp
    *--p = 0;                                                       // rbx
    *--p = reinterpret_cast<std::uint64_t>(this);                   // r12: argument
    *--p = reinterpret_cast<std::uint64_t>(entry);                  // r13: entry
    *--p = 0;                                                       // r14
    *--p = 0;                                                       // r15
    std::uint32_t mxcsr;
    std::uint16_t fcw;
    asm volatile("stmxcsr %0" : "=m"(mxcsr));
    asm volatile("fnstcw %0" : "=m"(fcw));
    std::uint64_t ctl = mxcsr | (std::uint64_t(fcw) << 32);
    *--p = ctl;
    sp = p;
}

Fiber::~Fiber() {
    if (stack)
        munmap(stack, stack_size);
}

} // namespace recomp
