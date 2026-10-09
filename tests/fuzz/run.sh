#!/bin/sh
# Differential test of the translator against dynarmic: tests/fuzz/run.sh [COUNT] [SEED] [ROUNDS]
# Needs a previous ./build.sh (for the dynarmic library).
set -e
cd "$(dirname "$0")/../.."
COUNT=${1:-20000}; SEED=${2:-1}; ROUNDS=${3:-3}
D=external/azahar/externals/dynarmic; B=build/azahar/externals; O=build/fuzz
mkdir -p $O
python3 -I tests/fuzz/gen.py $O/tests_$SEED.cpp "$COUNT" "$SEED"
clang++ -std=c++20 -O1 -ffp-contract=off -w -Iruntime/include -I$D/src -Itests/fuzz \
  tests/fuzz/harness.cpp $O/tests_$SEED.cpp runtime/src/rt_ops.cpp \
  $B/dynarmic/src/dynarmic/libdynarmic.a $B/dynarmic/externals/zydis/libZydis.a \
  $B/dynarmic/externals/zydis/zycore/libZycore.a $B/dynarmic/externals/mcl/src/libmcl.a \
  $B/fmt/libfmt.a -o $O/fuzz_$SEED
exec $O/fuzz_$SEED "$ROUNDS"
