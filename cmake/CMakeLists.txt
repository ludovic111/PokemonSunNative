# Built as part of Azahar's source tree (see external/azahar/src/CMakeLists.txt), so that the
# runtime links against its kernel, services, GPU and audio libraries.

set(PSN_GEN_DIR "" CACHE PATH "Directory with the C++ translated from the game (tools/recomp)")

enable_language(ASM)

add_library(psn_runtime STATIC
    ${PSN_SOURCE_DIR}/runtime/src/arm_recomp.cpp
    ${PSN_SOURCE_DIR}/runtime/src/arm_recomp.h
    ${PSN_SOURCE_DIR}/runtime/src/fiber.cpp
    ${PSN_SOURCE_DIR}/runtime/src/fiber.h
    ${PSN_SOURCE_DIR}/runtime/src/fiber.S
    ${PSN_SOURCE_DIR}/runtime/src/rt_ops.cpp
)
target_include_directories(psn_runtime PUBLIC ${PSN_SOURCE_DIR}/runtime/include ${PSN_SOURCE_DIR}/runtime/src)
target_link_libraries(psn_runtime PUBLIC citra_common citra_core)

# The translated game code. Built with flags that keep guest semantics: no FMA contraction
# (VFP multiply-add rounds twice) and no type-based aliasing assumptions.
file(GLOB PSN_GEN_SOURCES CONFIGURE_DEPENDS ${PSN_GEN_DIR}/*.cpp)
add_library(psn_game OBJECT ${PSN_GEN_SOURCES})
target_include_directories(psn_game PRIVATE ${PSN_SOURCE_DIR}/runtime/include)
target_compile_options(psn_game PRIVATE -O2 -ffp-contract=off -fno-strict-aliasing -w)
set_target_properties(psn_game PROPERTIES UNITY_BUILD OFF)

add_executable(pokemon-sun
    ${PSN_SOURCE_DIR}/frontend/main.cpp
    ${PSN_SOURCE_DIR}/frontend/emu_window_sdl2.cpp
    ${PSN_SOURCE_DIR}/frontend/emu_window_sdl2.h
    ${PSN_SOURCE_DIR}/frontend/emu_window_sdl2_vk.cpp
    ${PSN_SOURCE_DIR}/frontend/emu_window_sdl2_vk.h
)
target_include_directories(pokemon-sun PRIVATE ${PSN_SOURCE_DIR}/frontend)
target_link_libraries(pokemon-sun PRIVATE psn_game psn_runtime citra_common citra_core video_core
    audio_core input_common network SDL2::SDL2 fmt)
