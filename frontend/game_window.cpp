// Copyright 2016-2026 Citra Emulator Project / Azahar Emulator Project / PokemonSunNative
// Licensed under GPLv2 or any later version
// Refer to the license.txt file included.

#include "game_window.h"

#include <algorithm>
#include <cstdlib>

#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <SDL_syswm.h>
#include <SDL_vulkan.h>

#include "common/logging/log.h"
#include "common/settings.h"
#include "core/core.h"
#include "input_common/keyboard.h"
#include "input_common/main.h"
#include "network/network.h"

namespace {

class DummyContext : public Frontend::GraphicsContext {};

constexpr double TURBO_SPEED = 400.0; // percent, while Tab is held

} // namespace

void GameWindow::InitializeSDL() {
    SDL_SetHint(SDL_HINT_VIDEO_WAYLAND_ALLOW_LIBDECOR, "1");
    SDL_SetHint(SDL_HINT_APP_NAME, "Pokémon Sun");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER) < 0) {
        LOG_CRITICAL(Frontend, "Failed to initialize SDL2: {}", SDL_GetError());
        std::exit(1);
    }
    InputCommon::Init();
    Network::Init();
    SDL_SetMainReady();
}

GameWindow::GameWindow(Core::System& system_, bool fullscreen_)
    : EmuWindow(false), system(system_), title("Pokémon Sun") {
    window = SDL_CreateWindow(title.c_str(), SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
                              1280, 720,
                              SDL_WINDOW_VULKAN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
    if (!window) {
        LOG_CRITICAL(Frontend, "Could not create the window: {}", SDL_GetError());
        std::exit(1);
    }
    SDL_SysWMinfo wm;
    SDL_VERSION(&wm.version);
    if (SDL_GetWindowWMInfo(window, &wm) == SDL_FALSE) {
        LOG_CRITICAL(Frontend, "Failed to get information from the window manager: {}",
                     SDL_GetError());
        std::exit(1);
    }
    switch (wm.subsystem) {
#ifdef SDL_VIDEO_DRIVER_X11
    case SDL_SYSWM_X11:
        window_info.type = Frontend::WindowSystemType::X11;
        window_info.display_connection = wm.info.x11.display;
        window_info.render_surface = reinterpret_cast<void*>(wm.info.x11.window);
        break;
#endif
#ifdef SDL_VIDEO_DRIVER_WAYLAND
    case SDL_SYSWM_WAYLAND:
        window_info.type = Frontend::WindowSystemType::Wayland;
        window_info.display_connection = wm.info.wl.display;
        window_info.render_surface = wm.info.wl.surface;
        break;
#endif
    default:
        LOG_CRITICAL(Frontend, "Unsupported window system {}", static_cast<int>(wm.subsystem));
        std::exit(1);
    }
    window_id = SDL_GetWindowID(window);
    SDL_SetWindowMinimumSize(window, 400, 240);
    if (fullscreen_)
        ToggleFullscreen();
    OnResize();
    SDL_PumpEvents();
}

GameWindow::~GameWindow() {
    SDL_DestroyWindow(window);
    SDL_Quit();
}

std::unique_ptr<Frontend::GraphicsContext> GameWindow::CreateSharedContext() const {
    return std::make_unique<DummyContext>();
}

void GameWindow::SetIcon(const std::vector<u16>& rgb565, int size) {
    if (rgb565.size() < static_cast<std::size_t>(size * size))
        return;
    SDL_Surface* s = SDL_CreateRGBSurfaceWithFormatFrom(
        const_cast<u16*>(rgb565.data()), size, size, 16, size * 2, SDL_PIXELFORMAT_RGB565);
    if (s) {
        SDL_SetWindowIcon(window, s);
        SDL_FreeSurface(s);
    }
}

void GameWindow::SetTitle(const std::string& t) {
    title = t;
    SDL_SetWindowTitle(window, title.c_str());
}

void GameWindow::UpdatePerformanceInfo(double fps, double speed) {
    const std::string t = turbo ? fmt::format("{} — fast-forward {:.0f} FPS", title, fps)
                                : fmt::format("{} — {:.0f} FPS", title, fps);
    SDL_SetWindowTitle(window, t.c_str());
    (void)speed;
}

int GameWindow::RefreshRate() const {
    SDL_DisplayMode mode;
    const int display = SDL_GetWindowDisplayIndex(window);
    if (display >= 0 && SDL_GetCurrentDisplayMode(display, &mode) == 0)
        return mode.refresh_rate;
    return 0;
}

void GameWindow::OnResize() {
    int w, h;
    SDL_Vulkan_GetDrawableSize(window, &w, &h);
    UpdateCurrentFramebufferLayout(w, h);
}

void GameWindow::ToggleFullscreen() {
    SetFullscreen(!fullscreen);
}

void GameWindow::SetFullscreen(bool on) {
    fullscreen = on;
    // Borderless at desktop resolution: instant alt-tab, no mode switch
    SDL_SetWindowFullscreen(window, fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
}

int GameWindow::CurrentMonitor() const {
    return SDL_GetWindowDisplayIndex(window);
}

void GameWindow::MoveToMonitor(int index) {
    if (index < 0 || index >= SDL_GetNumVideoDisplays())
        return;
    // On Wayland a fullscreen window goes to the monitor SDL thinks it is on, which follows the
    // window position: leave fullscreen, move, and go fullscreen again (on that monitor)
    const bool was_fullscreen = fullscreen;
    if (was_fullscreen)
        SDL_SetWindowFullscreen(window, 0);
    SDL_SetWindowPosition(window, SDL_WINDOWPOS_CENTERED_DISPLAY(index),
                          SDL_WINDOWPOS_CENTERED_DISPLAY(index));
    if (was_fullscreen)
        SDL_SetWindowFullscreen(window, SDL_WINDOW_FULLSCREEN_DESKTOP);
}

void GameWindow::SetMenuOpen(bool open) {
    menu_open = open;
    if (open) {
        InputCommon::GetKeyboard()->ReleaseAllKeys();
        TouchReleased();
        SetTurbo(false);
        SDL_ShowCursor(SDL_ENABLE);
        cursor_visible = true;
    }
}

void GameWindow::SetTurbo(bool on) {
    turbo = on;
    Settings::is_temporary_frame_limit = on;
    Settings::temporary_frame_limit = TURBO_SPEED;
}

void GameWindow::TapKey(int scancode) {
    InputCommon::GetKeyboard()->PressKey(scancode);
    // Held for ~3 frames so the game (which polls input once per frame) sees it
    tapped.emplace_back(scancode, SDL_GetTicks() + 70);
}

void GameWindow::ReleaseTappedKeys() {
    const u32 now = SDL_GetTicks();
    std::erase_if(tapped, [now](const auto& t) {
        if (static_cast<s32>(now - t.second) >= 0) {
            InputCommon::GetKeyboard()->ReleaseKey(t.first);
            return true;
        }
        return false;
    });
}

void GameWindow::OnKey(int scancode, bool pressed, bool repeat, u16 mods) {
    switch (scancode) {
    case SDL_SCANCODE_F11:
        if (pressed && !repeat)
            ToggleFullscreen();
        return;
    case SDL_SCANCODE_RETURN:
        if (pressed && !repeat && (mods & KMOD_ALT)) {
            ToggleFullscreen();
            return;
        }
        break;
    case SDL_SCANCODE_TAB:
        if (!repeat)
            SetTurbo(pressed);
        return;
    case SDL_SCANCODE_ESCAPE:
        // Esc opens / closes the menu like a PC game (the X button)
        scancode = SDL_SCANCODE_E;
        break;
    default:
        break;
    }
    if (repeat)
        return;
    if (pressed)
        InputCommon::GetKeyboard()->PressKey(scancode);
    else
        InputCommon::GetKeyboard()->ReleaseKey(scancode);
}

void GameWindow::OnMouseButton(u32 button, bool pressed, s32 x, s32 y) {
    switch (button) {
    case SDL_BUTTON_LEFT:
        if (pressed)
            TouchPressed(static_cast<unsigned>(std::max(x, 0)), static_cast<unsigned>(std::max(y, 0)));
        else
            TouchReleased();
        break;
    case SDL_BUTTON_RIGHT: // back / cancel (B)
        if (pressed)
            InputCommon::GetKeyboard()->PressKey(SDL_SCANCODE_LSHIFT);
        else
            InputCommon::GetKeyboard()->ReleaseKey(SDL_SCANCODE_LSHIFT);
        break;
    case SDL_BUTTON_MIDDLE: // menu (X)
        if (pressed)
            InputCommon::GetKeyboard()->PressKey(SDL_SCANCODE_E);
        else
            InputCommon::GetKeyboard()->ReleaseKey(SDL_SCANCODE_E);
        break;
    case SDL_BUTTON_X1: // side buttons: L / R
    case SDL_BUTTON_X2: {
        const int key = button == SDL_BUTTON_X1 ? SDL_SCANCODE_Z : SDL_SCANCODE_C;
        if (pressed)
            InputCommon::GetKeyboard()->PressKey(key);
        else
            InputCommon::GetKeyboard()->ReleaseKey(key);
        break;
    }
    default:
        break;
    }
}

void GameWindow::OnMouseMotion(s32 x, s32 y) {
    TouchMoved(static_cast<unsigned>(std::max(x, 0)), static_cast<unsigned>(std::max(y, 0)));
    last_motion_ticks = SDL_GetTicks();
    if (!cursor_visible) {
        SDL_ShowCursor(SDL_ENABLE);
        cursor_visible = true;
    }
}

void GameWindow::OnMouseWheel(s32 dy) {
    // Scroll lists with the wheel (D-pad up / down)
    if (dy > 0)
        TapKey(SDL_SCANCODE_UP);
    else if (dy < 0)
        TapKey(SDL_SCANCODE_DOWN);
}

void GameWindow::PollEvents() {
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        if (event_filter && event_filter(e))
            continue;
        switch (e.type) {
        case SDL_WINDOWEVENT:
            switch (e.window.event) {
            case SDL_WINDOWEVENT_SIZE_CHANGED:
            case SDL_WINDOWEVENT_RESIZED:
            case SDL_WINDOWEVENT_MAXIMIZED:
            case SDL_WINDOWEVENT_RESTORED:
                OnResize();
                break;
            case SDL_WINDOWEVENT_FOCUS_GAINED:
                focused = true;
                break;
            case SDL_WINDOWEVENT_FOCUS_LOST:
                focused = false;
                // Never leave a key stuck down while the window is in the background.
                // (PSN_SYNTHETIC_INPUT: automated tests whose input tools steal focus.)
                if (!std::getenv("PSN_SYNTHETIC_INPUT")) {
                    InputCommon::GetKeyboard()->ReleaseAllKeys();
                    SetTurbo(false);
                }
                break;
            case SDL_WINDOWEVENT_CLOSE:
                RequestClose();
                break;
            }
            break;
        case SDL_KEYDOWN:
        case SDL_KEYUP:
            OnKey(e.key.keysym.scancode, e.type == SDL_KEYDOWN, e.key.repeat != 0, e.key.keysym.mod);
            break;
        case SDL_MOUSEMOTION:
            if (e.motion.which != SDL_TOUCH_MOUSEID) {
                int w, h, dw, dh;
                SDL_GetWindowSize(window, &w, &h);
                SDL_Vulkan_GetDrawableSize(window, &dw, &dh);
                OnMouseMotion(e.motion.x * dw / std::max(w, 1), e.motion.y * dh / std::max(h, 1));
            }
            break;
        case SDL_MOUSEBUTTONDOWN:
        case SDL_MOUSEBUTTONUP:
            if (e.button.which != SDL_TOUCH_MOUSEID) {
                int w, h, dw, dh;
                SDL_GetWindowSize(window, &w, &h);
                SDL_Vulkan_GetDrawableSize(window, &dw, &dh);
                OnMouseButton(e.button.button, e.type == SDL_MOUSEBUTTONDOWN,
                              e.button.x * dw / std::max(w, 1), e.button.y * dh / std::max(h, 1));
            }
            break;
        case SDL_MOUSEWHEEL:
            OnMouseWheel(e.wheel.y);
            break;
        case SDL_QUIT:
            RequestClose();
            break;
        default:
            break;
        }
    }
    ReleaseTappedKeys();
    // Hide the cursor after a few seconds without mouse movement
    if (cursor_visible && !menu_open && SDL_GetTicks() - last_motion_ticks > 3000) {
        SDL_ShowCursor(SDL_DISABLE);
        cursor_visible = false;
    }
}
