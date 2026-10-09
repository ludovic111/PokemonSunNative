// Prints ARM11 cycle counts for instructions, using Azahar's table (arm_tick_counts.cpp), so
// translated code advances the 3DS clock exactly like Azahar's own CPU backends.
//
// stdin:  records of 5 bytes: u8 thumb, u32 little-endian instruction
//         (Thumb BL/BLX pairs: first halfword in the upper 16 bits)
// stdout: one u8 cycle count per record

#include <cstdint>
#include <cstdio>

#include "core/arm/dynarmic/arm_tick_counts.h"

int main() {
    unsigned char rec[5];
    while (std::fread(rec, 1, 5, stdin) == 5) {
        const std::uint32_t insn = rec[1] | (rec[2] << 8) | (rec[3] << 16) | (std::uint32_t(rec[4]) << 24);
        const std::uint64_t ticks = Core::TicksForInstruction(rec[0] != 0, insn);
        const unsigned char out = static_cast<unsigned char>(ticks > 255 ? 255 : ticks);
        std::fwrite(&out, 1, 1, stdout);
    }
    return 0;
}
