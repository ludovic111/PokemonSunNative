// Minimal x86-64 SysV context switch for guest-thread fibers.
//
// void recomp_fiber_switch(void** save_sp, void* new_sp)
//   Saves callee-saved registers, MXCSR and the x87 control word on the current stack, stores
//   the stack pointer in *save_sp, then restores the same from new_sp.

    .text
    .globl recomp_fiber_switch
    .type recomp_fiber_switch, @function
recomp_fiber_switch:
    pushq %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    subq $8, %rsp
    stmxcsr (%rsp)
    fnstcw 4(%rsp)
    movq %rsp, (%rdi)
    movq %rsi, %rsp
    ldmxcsr (%rsp)
    fldcw 4(%rsp)
    addq $8, %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp
    ret
    .size recomp_fiber_switch, .-recomp_fiber_switch

// First code run on a new fiber: r12 holds the Fiber*, r13 the entry function.
    .globl recomp_fiber_start
    .type recomp_fiber_start, @function
recomp_fiber_start:
    movq %r12, %rdi
    callq *%r13
    ud2
    .size recomp_fiber_start, .-recomp_fiber_start

    .section .note.GNU-stack,"",@progbits
