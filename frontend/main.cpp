// PokemonSunNative: runs Pokémon Sun / Moon from code translated ahead of time to x86-64.
//
// Azahar provides the 3DS operating system services, the GPU (translated to Vulkan) and audio;
// the game's own ARM code is not emulated, it was compiled to native code by tools/recomp.

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <cstring>
#include <thread>

#define SDL_MAIN_HANDLED
#include <SDL.h>

#include "arm_recomp.h"
#include "common/common_paths.h"
#include "common/file_util.h"
#include "common/logging/backend.h"
#include "common/logging/log.h"
#include "common/settings.h"
#include "core/core.h"
#include "core/frontend/applets/default_applets.h"
#include "core/hle/service/cam/cam.h"
#include "core/hle/service/service.h"
#include "core/loader/loader.h"
#include "core/loader/smdh.h"
#include "game_window.h"
#include "input_common/main.h"
#include "network/network.h"
#include "video_core/gpu.h"
#include "video_core/renderer_base.h"

namespace {

std::string DataDir() {
    if (const char* d = std::getenv("POKEMONSUN_DATA"))
        return d;
    const char* xdg = std::getenv("XDG_DATA_HOME");
    std::string base = xdg && *xdg ? xdg : std::string(std::getenv("HOME")) + "/.local/share";
    return base + "/PokemonSunNative";
}

void ApplyControls() {
    using namespace Settings::NativeButton;
    auto key = [](SDL_Scancode s) { return InputCommon::GenerateKeyboardParam(s); };
    auto& p = Settings::values.current_input_profile;
    p.name = "Keyboard + Mouse";
    p.buttons[A] = key(SDL_SCANCODE_SPACE);       // talk / confirm
    p.buttons[B] = key(SDL_SCANCODE_LSHIFT);      // run (hold) / back
    p.buttons[X] = key(SDL_SCANCODE_E);           // menu
    p.buttons[Y] = key(SDL_SCANCODE_Q);
    p.buttons[Up] = key(SDL_SCANCODE_UP);
    p.buttons[Down] = key(SDL_SCANCODE_DOWN);
    p.buttons[Left] = key(SDL_SCANCODE_LEFT);
    p.buttons[Right] = key(SDL_SCANCODE_RIGHT);
    p.buttons[L] = key(SDL_SCANCODE_Z);
    p.buttons[R] = key(SDL_SCANCODE_C);
    p.buttons[Start] = key(SDL_SCANCODE_RETURN);
    p.buttons[Select] = key(SDL_SCANCODE_BACKSPACE);
    p.buttons[ZL] = key(SDL_SCANCODE_1);
    p.buttons[ZR] = key(SDL_SCANCODE_3);
    p.buttons[Debug] = "";
    p.buttons[Gpio14] = "";
    p.buttons[Home] = "";
    p.buttons[Power] = "";
    p.analogs[Settings::NativeAnalog::CirclePad] = InputCommon::GenerateAnalogParamFromKeys(
        SDL_SCANCODE_W, SDL_SCANCODE_S, SDL_SCANCODE_A, SDL_SCANCODE_D, SDL_SCANCODE_CAPSLOCK, 0.5f);
    p.analogs[Settings::NativeAnalog::CStick] = InputCommon::GenerateAnalogParamFromKeys(
        SDL_SCANCODE_I, SDL_SCANCODE_K, SDL_SCANCODE_J, SDL_SCANCODE_L, SDL_SCANCODE_CAPSLOCK, 0.5f);
    p.touch_device = "engine:emu_window";
    p.motion_device = "engine:motion_emu,update_period:100,sensitivity:0.01,tilt_clamp:90.0";
}

void ApplySettings() {
    auto& v = Settings::values;
    v.graphics_api = Settings::GraphicsAPI::Vulkan;
    v.physical_device = 0;
    v.resolution_factor = 0; // follow the window size
    v.async_shader_compilation = true;
    v.use_disk_shader_cache = true;
    v.use_hw_shader = true;
    v.use_vsync = true;
    v.layout_option = Settings::LayoutOption::LargeScreen;
    v.large_screen_proportion = 2.5f;
    v.small_screen_position = Settings::SmallScreenPosition::BottomRight;
    v.is_new_3ds = true;
    v.lle_applets = false; // only the game itself is translated
    v.use_cpu_jit = false;
    v.region_value = Settings::REGION_VALUE_AUTO_SELECT;
    v.log_filter = "*:Info";
    for (const auto& module : Service::service_module_map)
        v.lle_modules.emplace(module.name, false);
    for (auto& name : v.camera_name)
        name = "blank";
    ApplyControls();
}

} // namespace

int main(int argc, char** argv) {
    std::string rom;
    bool fullscreen = true;
    bool pause_in_background = true;
    const char* usage = "usage: %s [--windowed] [--keep-running] [--speed PERCENT] GAME.3ds\n"
                        "  --windowed      start in a window instead of fullscreen\n"
                        "  --keep-running  keep playing while the window is in the background\n"
                        "  --speed PERCENT game speed (100 = normal)\n";
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--windowed" || a == "-w")
            fullscreen = false;
        else if (a == "--keep-running")
            pause_in_background = false;
        else if (a == "--speed" && i + 1 < argc)
            Settings::values.frame_limit = std::atof(argv[++i]);
        else if (a == "--help" || a == "-h") {
            std::printf(usage, argv[0]);
            return 0;
        } else
            rom = a;
    }
    if (rom.empty()) {
        std::fprintf(stderr, usage, argv[0]);
        return 1;
    }

    const std::string data_dir = DataDir() + "/";
    std::filesystem::create_directories(data_dir);
    FileUtil::SetUserPath(data_dir);

    ApplySettings();
    Common::Log::Initialize();
    Common::Log::Start();
    Common::Log::SetColorConsoleBackendEnabled(true);

    auto& system = Core::System::GetInstance();
    // Developer A/B switch: run the original ARM code in Azahar's dynarmic instead of the
    // translation, to tell translation bugs from everything else. Not for playing.
    if (std::getenv("PSN_REFERENCE_CPU")) {
        Settings::values.use_cpu_jit = true;
        LOG_WARNING(Frontend, "PSN_REFERENCE_CPU set: using dynarmic, not the translated code");
    } else {
        recomp::InstallBackend();
        recomp::SetFatalHandler([data_dir](const std::string& what) {
            const std::string msg = fmt::format(
                "The game stopped: {}\n\nPlease report this at "
                "https://github.com/ludovic111/PokemonSunNative/issues with the lines marked "
                "\"Critical\" in\n{}log/azahar_log.txt\n\nYour last in-game save is safe.",
                what, data_dir);
            SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "Pokémon Sun", msg.c_str(), nullptr);
        });
    }
    system.ApplySettings();
    Frontend::RegisterDefaultApplets(system);

    GameWindow::InitializeSDL();
    auto window = std::make_unique<GameWindow>(system, fullscreen);
    const auto scope = window->Acquire();

    const auto result = system.Load(*window, rom);
    if (result != Core::System::ResultStatus::Success) {
        LOG_CRITICAL(Frontend, "Could not start the game ({}): {}", static_cast<int>(result),
                     system.GetStatusDetails());
        return 1;
    }

    // Window title and icon from the game itself
    std::vector<u8> smdh_data;
    if (system.GetAppLoader().ReadIcon(smdh_data) == Loader::ResultStatus::Success &&
        Loader::IsValidSMDH(smdh_data)) {
        Loader::SMDH smdh;
        std::memcpy(&smdh, smdh_data.data(), sizeof(smdh));
        window->SetIcon(smdh.GetIcon(true), 48);
    }
    std::string title;
    if (system.GetAppLoader().ReadTitle(title) == Loader::ResultStatus::Success && !title.empty())
        window->SetTitle(title);

    std::atomic_bool stop_loading{false};
    system.GPU().Renderer().Rasterizer()->LoadDefaultDiskResources(stop_loading, nullptr);

    u32 last_stats = SDL_GetTicks();
    while (window->IsOpen()) {
        if (pause_in_background && !window->IsFocused()) {
            // Paused in the background: keep the window responsive, use no CPU
            window->PollEvents();
            SDL_Delay(20);
            continue;
        }
        const auto status = system.RunLoop();
        if (status == Core::System::ResultStatus::ShutdownRequested)
            window->RequestClose();
        else if (status != Core::System::ResultStatus::Success)
            LOG_ERROR(Frontend, "Run loop error {}: {}", static_cast<int>(status),
                      system.GetStatusDetails());
        if (SDL_GetTicks() - last_stats > 1000) {
            const auto stats = system.GetAndResetPerfStats();
            window->UpdatePerformanceInfo(stats.game_fps, stats.emulation_speed);
            last_stats = SDL_GetTicks();
        }
    }
    window->RequestClose();
    Network::Shutdown();
    InputCommon::Shutdown();
    system.Shutdown();
    return 0;
}
