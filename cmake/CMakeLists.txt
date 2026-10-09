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

# Dear ImGui, for the in-game settings menu (drawn with the game's own Vulkan device)
set(IMGUI_DIR ${PSN_SOURCE_DIR}/external/imgui)
add_library(psn_imgui STATIC
    ${IMGUI_DIR}/imgui.cpp
    ${IMGUI_DIR}/imgui_draw.cpp
    ${IMGUI_DIR}/imgui_tables.cpp
    ${IMGUI_DIR}/imgui_widgets.cpp
    ${IMGUI_DIR}/backends/imgui_impl_sdl2.cpp
    ${IMGUI_DIR}/backends/imgui_impl_vulkan.cpp
)
target_include_directories(psn_imgui PUBLIC ${IMGUI_DIR} ${IMGUI_DIR}/backends)
target_compile_definitions(psn_imgui PUBLIC IMGUI_IMPL_VULKAN_NO_PROTOTYPES IMGUI_DISABLE_OBSOLETE_FUNCTIONS)
target_link_libraries(psn_imgui PUBLIC SDL2::SDL2 vulkan-headers)

add_executable(pokemon-sun
    ${PSN_SOURCE_DIR}/frontend/main.cpp
    ${PSN_SOURCE_DIR}/frontend/game_window.cpp
    ${PSN_SOURCE_DIR}/frontend/game_window.h
    ${PSN_SOURCE_DIR}/frontend/settings_menu.cpp
    ${PSN_SOURCE_DIR}/frontend/settings_menu.h
    ${PSN_SOURCE_DIR}/frontend/user_settings.cpp
    ${PSN_SOURCE_DIR}/frontend/user_settings.h
)
target_include_directories(pokemon-sun PRIVATE ${PSN_SOURCE_DIR}/frontend)
target_link_libraries(pokemon-sun PRIVATE psn_imgui psn_game psn_runtime citra_common citra_core video_core
    audio_core input_common network SDL2::SDL2 fmt)
