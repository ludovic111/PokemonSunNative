#include "settings_menu.h"

#include <algorithm>
#include <cstdio>
#include <filesystem>

#include <SDL.h>
#include <SDL_vulkan.h>
#include <imgui.h>
#include <imgui_impl_sdl2.h>
#include <imgui_impl_vulkan.h>

#include "common/logging/log.h"
#include "common/settings.h"
#include "game_window.h"
#include "video_core/frame_interpolator.h"

namespace {

// Screen arrangements offered in the menu (index stored in UserSettings::layout)
struct LayoutChoice {
    const char* name;
    Settings::LayoutOption option;
};
constexpr LayoutChoice LAYOUTS[] = {
    {"Big top screen, touch screen beside it", Settings::LayoutOption::LargeScreen},
    {"Top screen above touch screen", Settings::LayoutOption::Default},
    {"Side by side, same size", Settings::LayoutOption::SideScreen},
    {"Big top screen, touch screen overlapping", Settings::LayoutOption::HybridScreen},
    {"Top screen only (no touch screen)", Settings::LayoutOption::SingleScreen},
};

constexpr const char* TOUCH_POSITIONS[] = {
    "Top right", "Middle right", "Bottom right", "Top left",
    "Middle left", "Bottom left", "Above", "Below",
};

constexpr const char* TEXTURE_FILTERS[] = {
    "None (original look)", "Anime4K", "Bicubic", "ScaleForce", "xBRZ", "MMPX",
};

constexpr int FPS_PRESETS[] = {60, 75, 90, 100, 120, 144, 165, 180, 240, 360};

int RefreshOf(GameWindow& window) {
    const int r = window.RefreshRate();
    return r > 0 ? r : 60;
}

std::string ResolutionLabel(int factor) {
    if (factor == 0)
        return "Match the window (automatic)";
    const int h = 240 * factor, w = 400 * factor;
    std::string name;
    switch (h) {
    case 720: name = " - 720p"; break;
    case 1200: name = " - 1200p"; break;
    case 1440: name = " - 1440p / 2K"; break;
    case 2160: name = " - 2160p / 4K"; break;
    case 2880: name = " - 2880p / 5K"; break;
    default: break;
    }
    return std::to_string(factor) + "x  (" + std::to_string(w) + " x " + std::to_string(h) +
           ")" + name + (factor == 1 ? " - original" : "");
}

std::string FpsLabel(int fps, int refresh) {
    if (fps < 0)
        return "Monitor refresh rate (" + std::to_string(refresh) + " FPS)";
    if (fps == 0)
        return "Original (30 FPS, no interpolation)";
    return std::to_string(fps) + " FPS";
}

void LoadFonts(float scale) {
    ImGuiIO& io = ImGui::GetIO();
    io.Fonts->Clear();
    const char* candidates[] = {
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    };
    for (const char* path : candidates) {
        std::error_code ec;
        if (std::filesystem::exists(path, ec) &&
            io.Fonts->AddFontFromFileTTF(path, 19.0f * scale)) {
            return;
        }
    }
    ImFontConfig config;
    config.SizePixels = 13.0f * std::max(1.0f, std::round(scale * 1.4f));
    io.Fonts->AddFontDefault(&config);
}

/// A frame's ImGui draw data, copied so it can be drawn later on the render thread.
struct DrawSnapshot {
    ImDrawData data;
    ImVector<ImDrawList*> lists;
    ~DrawSnapshot() {
        for (ImDrawList* list : lists)
            IM_DELETE(list);
    }
};

} // namespace

SettingsMenu::SettingsMenu(GameWindow& window_, UserSettings& settings_)
    : window{window_}, settings{settings_} {
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;

    int w, h;
    SDL_GetWindowSize(window.Handle(), &w, &h);
    int dw, dh;
    SDL_Vulkan_GetDrawableSize(window.Handle(), &dw, &dh);
    const float scale = std::clamp(static_cast<float>(std::max(dh, 720)) / 1080.0f, 1.0f, 2.5f);
    LoadFonts(scale);

    ImGui::StyleColorsDark();
    ImGuiStyle& style = ImGui::GetStyle();
    style.WindowRounding = 10.0f;
    style.FrameRounding = 6.0f;
    style.GrabRounding = 6.0f;
    style.WindowPadding = ImVec2(18, 16);
    style.FramePadding = ImVec2(10, 6);
    style.ItemSpacing = ImVec2(10, 9);
    style.Colors[ImGuiCol_WindowBg].w = 0.94f;
    style.ScaleAllSizes(scale);

    ImGui_ImplSDL2_InitForVulkan(window.Handle());
    context_ready = true;
    hint_until = SDL_GetTicks() / 1000.0 + 8.0;
}

void SettingsMenu::ReleaseGpu() {
    if (!vulkan_ready)
        return;
    if (device_wait_idle)
        reinterpret_cast<PFN_vkDeviceWaitIdle>(device_wait_idle)(static_cast<VkDevice>(device));
    ImGui_ImplVulkan_Shutdown();
    vulkan_ready = false;
}

SettingsMenu::~SettingsMenu() {
    ReleaseGpu();
    if (context_ready) {
        ImGui_ImplSDL2_Shutdown();
        ImGui::DestroyContext();
    }
}

void SettingsMenu::InitVulkan(const VulkanInfo& info) {
    struct Loader {
        PFN_vkGetInstanceProcAddr gipa;
        VkInstance instance;
    } loader{reinterpret_cast<PFN_vkGetInstanceProcAddr>(info.get_instance_proc_addr),
             static_cast<VkInstance>(info.instance)};
    ImGui_ImplVulkan_LoadFunctions(
        info.api_version,
        [](const char* name, void* user) -> PFN_vkVoidFunction {
            auto* l = static_cast<Loader*>(user);
            return l->gipa(l->instance, name);
        },
        &loader);

    ImGui_ImplVulkan_InitInfo init{};
    init.ApiVersion = info.api_version;
    init.Instance = static_cast<VkInstance>(info.instance);
    init.PhysicalDevice = static_cast<VkPhysicalDevice>(info.physical_device);
    init.Device = static_cast<VkDevice>(info.device);
    init.QueueFamily = info.queue_family;
    init.Queue = static_cast<VkQueue>(info.queue);
    init.RenderPass = static_cast<VkRenderPass>(info.render_pass);
    init.DescriptorPoolSize = 16;
    // More buffer sets than frames that can be in flight: a set is reused only after its frame
    // has certainly finished on the GPU
    init.MinImageCount = 2;
    init.ImageCount = 8;
    init.MSAASamples = VK_SAMPLE_COUNT_1_BIT;
    if (!ImGui_ImplVulkan_Init(&init)) {
        LOG_ERROR(Frontend, "Settings menu: could not initialise Vulkan drawing");
        return;
    }
    device = info.device;
    device_wait_idle = reinterpret_cast<void*>(loader.gipa(loader.instance, "vkDeviceWaitIdle"));
    // The renderer guarantees the GPU queue is idle now: upload the font texture
    ImGui_ImplVulkan_CreateFontsTexture();
    vulkan_ready = true;
}

void SettingsMenu::SetOpen(bool value) {
    open = value;
    window.SetMenuOpen(value);
}

bool SettingsMenu::HandleEvent(const SDL_Event& e) {
    if (e.type == SDL_KEYDOWN && !e.key.repeat) {
        if (e.key.keysym.scancode == SDL_SCANCODE_F1 ||
            (open && e.key.keysym.scancode == SDL_SCANCODE_ESCAPE)) {
            SetOpen(!open);
            return true;
        }
    }
    if (!open)
        return false;
    ImGui_ImplSDL2_ProcessEvent(&e);
    switch (e.type) {
    case SDL_KEYDOWN:
    case SDL_KEYUP:
    case SDL_TEXTINPUT:
    case SDL_MOUSEMOTION:
    case SDL_MOUSEBUTTONDOWN:
    case SDL_MOUSEBUTTONUP:
    case SDL_MOUSEWHEEL:
        return true; // not for the game while the menu is open
    default:
        return false;
    }
}

void SettingsMenu::Changed() {
    settings.Save();
    Apply(settings, window, false);
}

void SettingsMenu::Apply(const UserSettings& s, GameWindow& window, bool startup) {
    auto& v = Settings::values;
    v.resolution_factor = static_cast<u32>(std::clamp(s.resolution, 0, 15));
    v.texture_filter = static_cast<Settings::TextureFilter>(std::clamp(s.texture_filter, 0, 5));
    v.filter_mode = s.smooth_scaling;
    const int layout = std::clamp(s.layout, 0, static_cast<int>(std::size(LAYOUTS)) - 1);
    v.layout_option = LAYOUTS[layout].option;
    v.large_screen_proportion = std::clamp(s.touch_size, 1.0f, 6.0f);
    v.small_screen_position =
        static_cast<Settings::SmallScreenPosition>(std::clamp(s.touch_position, 0, 7));
    v.swap_screen = s.swap_screens;

    const int refresh = window.RefreshRate();
    const double fps = s.fps < 0 ? (refresh > 0 ? refresh : 60) : s.fps;
    VideoCore::SetDisplayRate(fps);
    if (s.vsync >= 0)
        v.use_vsync = s.vsync == 1;
    else // automatic: wait for the display unless showing more frames than it can
        v.use_vsync = refresh <= 0 || fps <= 0.0 || fps <= refresh + 1;

    if (!startup) {
        if (s.monitor >= 0 && s.monitor != window.CurrentMonitor())
            window.MoveToMonitor(s.monitor);
        if (s.fullscreen != window.IsFullscreen())
            window.SetFullscreen(s.fullscreen);
        window.RefreshLayout();
    }
}

std::function<void(void*)> SettingsMenu::Record(u32 width, u32 height) {
    const double now = SDL_GetTicks() / 1000.0;
    const bool hint = now < hint_until && !open;
    if (!vulkan_ready || (!open && !hint))
        return {};

    ImGui_ImplVulkan_NewFrame();
    ImGui_ImplSDL2_NewFrame();
    ImGui::NewFrame();
    if (open) {
        Draw();
    } else {
        ImGui::SetNextWindowPos(ImVec2(16, 16));
        ImGui::SetNextWindowBgAlpha(0.6f);
        ImGui::Begin("##hint", nullptr,
                     ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                         ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoFocusOnAppearing |
                         ImGuiWindowFlags_NoNav);
        ImGui::TextUnformatted("F1: graphics settings");
        ImGui::End();
    }
    ImGui::Render();

    const ImDrawData* dd = ImGui::GetDrawData();
    if (!dd || dd->CmdListsCount == 0)
        return {};
    auto snap = std::make_shared<DrawSnapshot>();
    snap->data = *dd;
    snap->data.CmdLists.clear();
    for (ImDrawList* list : dd->CmdLists) {
        ImDrawList* copy = list->CloneOutput();
        snap->lists.push_back(copy);
        snap->data.CmdLists.push_back(copy);
    }
    (void)width;
    (void)height;
    return [snap](void* cmdbuf) {
        ImGui_ImplVulkan_RenderDrawData(&snap->data, static_cast<VkCommandBuffer>(cmdbuf));
    };
}

void SettingsMenu::Draw() {
    const ImGuiViewport* vp = ImGui::GetMainViewport();
    const float em = ImGui::GetFontSize();
    ImGui::SetNextWindowPos(ImVec2(vp->WorkPos.x + vp->WorkSize.x * 0.5f,
                                   vp->WorkPos.y + vp->WorkSize.y * 0.5f),
                            ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(std::min(vp->WorkSize.x - 2 * em, 36 * em), 0));
    ImGui::Begin("Graphics settings", nullptr,
                 ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoMove |
                     ImGuiWindowFlags_NoResize | ImGuiWindowFlags_AlwaysAutoResize);

    const float label_w = 12 * em;
    auto row = [&](const char* label) {
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted(label);
        ImGui::SameLine(label_w);
        ImGui::SetNextItemWidth(-1);
    };
    const int refresh = RefreshOf(window);

    // ---- Display ----
    ImGui::SeparatorText("Display");
    {
        row("Monitor");
        const int count = SDL_GetNumVideoDisplays();
        const int current = window.CurrentMonitor();
        auto name_of = [](int i) {
            SDL_DisplayMode mode{};
            SDL_GetCurrentDisplayMode(i, &mode);
            const char* name = SDL_GetDisplayName(i);
            char buf[160];
            std::snprintf(buf, sizeof(buf), "%d: %s  %dx%d  %d Hz", i + 1, name ? name : "Display",
                          mode.w, mode.h, mode.refresh_rate);
            return std::string(buf);
        };
        if (ImGui::BeginCombo("##monitor", name_of(current < 0 ? 0 : current).c_str())) {
            for (int i = 0; i < count; i++) {
                if (ImGui::Selectable(name_of(i).c_str(), i == current)) {
                    settings.monitor = i;
                    Changed();
                }
            }
            ImGui::EndCombo();
        }

        row("Mode");
        int mode = window.IsFullscreen() ? 0 : 1;
        if (ImGui::RadioButton("Fullscreen", mode == 0) && mode != 0) {
            settings.fullscreen = true;
            Changed();
        }
        ImGui::SameLine();
        if (ImGui::RadioButton("Window", mode == 1) && mode != 1) {
            settings.fullscreen = false;
            Changed();
        }

        row("Frame rate");
        if (ImGui::BeginCombo("##fps", FpsLabel(settings.fps, refresh).c_str())) {
            if (ImGui::Selectable(FpsLabel(-1, refresh).c_str(), settings.fps < 0)) {
                settings.fps = -1;
                Changed();
            }
            if (ImGui::Selectable(FpsLabel(0, refresh).c_str(), settings.fps == 0)) {
                settings.fps = 0;
                Changed();
            }
            for (int f : FPS_PRESETS) {
                if (ImGui::Selectable(FpsLabel(f, refresh).c_str(), settings.fps == f)) {
                    settings.fps = f;
                    Changed();
                }
            }
            ImGui::EndCombo();
        }
        row("Custom FPS");
        ImGui::SetNextItemWidth(8 * em);
        ImGui::InputInt("##customfps", &custom_fps, 10, 50);
        custom_fps = std::clamp(custom_fps, 10, 1000);
        ImGui::SameLine();
        if (ImGui::Button("Use")) {
            settings.fps = custom_fps;
            Changed();
        }

        row("V-Sync");
        const char* vsync_names[] = {"Automatic", "Off", "On"};
        int vsync = settings.vsync + 1;
        if (ImGui::Combo("##vsync", &vsync, vsync_names, 3)) {
            settings.vsync = vsync - 1;
            Changed();
        }
    }

    // ---- Rendering ----
    ImGui::SeparatorText("Rendering");
    {
        row("Resolution");
        if (ImGui::BeginCombo("##res", ResolutionLabel(settings.resolution).c_str())) {
            for (int f = 0; f <= 12; f++) {
                if (ImGui::Selectable(ResolutionLabel(f).c_str(), settings.resolution == f)) {
                    settings.resolution = f;
                    Changed();
                }
            }
            ImGui::EndCombo();
        }
        if (settings.resolution == 0) {
            ImGui::SetCursorPosX(label_w);
            ImGui::TextDisabled("Renders the 3D at the size the top screen is shown at.");
        }

        row("Texture filter");
        int filter = settings.texture_filter;
        if (ImGui::Combo("##filter", &filter, TEXTURE_FILTERS,
                         static_cast<int>(std::size(TEXTURE_FILTERS)))) {
            settings.texture_filter = filter;
            Changed();
        }

        row("Screen scaling");
        if (ImGui::RadioButton("Smooth", settings.smooth_scaling) && !settings.smooth_scaling) {
            settings.smooth_scaling = true;
            Changed();
        }
        ImGui::SameLine();
        if (ImGui::RadioButton("Sharp", !settings.smooth_scaling) && settings.smooth_scaling) {
            settings.smooth_scaling = false;
            Changed();
        }
    }

    // ---- Layout ----
    ImGui::SeparatorText("Screens");
    {
        row("Layout");
        if (ImGui::BeginCombo("##layout", LAYOUTS[std::clamp(settings.layout, 0, 4)].name)) {
            for (int i = 0; i < static_cast<int>(std::size(LAYOUTS)); i++) {
                if (ImGui::Selectable(LAYOUTS[i].name, settings.layout == i)) {
                    settings.layout = i;
                    Changed();
                }
            }
            ImGui::EndCombo();
        }
        if (settings.layout == 0 || settings.layout == 3) {
            row("Top screen size");
            if (ImGui::SliderFloat("##touchsize", &settings.touch_size, 1.0f, 6.0f, "%.1fx touch screen"))
                Changed();
            row("Touch screen");
            int pos = settings.touch_position;
            if (ImGui::Combo("##touchpos", &pos, TOUCH_POSITIONS, 8)) {
                settings.touch_position = pos;
                Changed();
            }
        }
        row("Swap screens");
        if (ImGui::Checkbox("##swap", &settings.swap_screens))
            Changed();
    }

    ImGui::Spacing();
    ImGui::Separator();
    ImGui::TextDisabled("Changes apply and save immediately. Window mode: drag the title bar to "
                        "move it;\nin fullscreen, Super+Shift+Left/Right also moves the game to "
                        "another monitor.");
    if (ImGui::Button("Reset to defaults")) {
        settings = UserSettings{};
        Changed();
    }
    ImGui::SameLine();
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + ImGui::GetContentRegionAvail().x - 6 * em);
    if (ImGui::Button("Close (F1)", ImVec2(6 * em, 0)))
        SetOpen(false);
    ImGui::End();
}
