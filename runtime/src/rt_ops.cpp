// Out-of-line helpers for translated code that are too large to inline.

#include <recomp/rt.h>

namespace recomp {

u32 parallel(Cpu* c, u32 kind, u32 op, u32 a, u32 b) {
    const bool sign = kind < 4;
    const u32 mode = kind & 3; // 0 modular (sets GE), 1 saturating, 2 halving
    const bool bytes = op >= 4;
    const int lanes = bytes ? 4 : 2;
    const int width = bytes ? 8 : 16;
    const u32 lane_mask = bytes ? 0xFF : 0xFFFF;
    u32 result = 0, ge = 0;
    for (int i = 0; i < lanes; i++) {
        const int sh = i * width;
        // ASX: low lane subtracts the high lane of b, high lane adds the low lane of b.
        // SAX: the opposite.
        u32 bl = b;
        bool sub;
        if (op == 1 || op == 2) {
            bl = i == 0 ? (b >> 16) : b;
            sub = (op == 1) ? (i == 0) : (i == 1);
        } else {
            bl = b >> sh;
            sub = op == 3 || op == 7;
        }
        const u32 ua = (a >> sh) & lane_mask, ub = bl & lane_mask;
        s32 x, y;
        if (sign) {
            x = bytes ? s32(s8(ua)) : s32(s16(ua));
            y = bytes ? s32(s8(ub)) : s32(s16(ub));
        } else {
            x = s32(ua);
            y = s32(ub);
        }
        const s32 r = sub ? x - y : x + y;
        u32 lane;
        bool g;
        if (sign) {
            g = r >= 0;
        } else {
            g = sub ? r >= 0 : r >= (1 << width);
        }
        if (mode == 1) {
            s32 lo = sign ? -(1 << (width - 1)) : 0;
            s32 hi = sign ? (1 << (width - 1)) - 1 : (1 << width) - 1;
            lane = u32(r < lo ? lo : r > hi ? hi : r);
        } else if (mode == 2) {
            lane = u32(r >> 1);
        } else {
            lane = u32(r);
        }
        result |= (lane & lane_mask) << sh;
        if (g)
            ge |= bytes ? (1u << i) : (3u << (2 * i));
    }
    if (mode == 0)
        c->ge = ge;
    return result;
}

} // namespace recomp
