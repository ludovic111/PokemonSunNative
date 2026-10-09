// Copyright 2016-2026 Citra Emulator Project / Azahar Emulator Project / PokemonSunNative
// Licensed under GPLv2 or any later version
// Refer to the license.txt file included.

// The game's window: SDL2 + Vulkan, with keyboard and mouse controls tuned for playing on a PC.

#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <utility>
#include <vector>

#include "common/common_types.h"
#include "core/frontend/emu_window.h"

union SDL_Event;
struct SDL_Window;

namespace Core {
class System;
}

class GameWindow : public Frontend::EmuWindow {
public:
    GameWindow(Core::System& system, bool fullscreen);
    ~GameWindow() override;

    static void InitializeSDL();

    void PollEvents() override;
    std::unique_ptr<Frontend::GraphicsContext> CreateSharedContext() const override;

    bool IsOpen() const {
        return is_open;
    }
    void RequestClose() {
        is_open = false;
    }
    /// The game pauses while the window is in the background.
    bool IsFocused() const {
        return focused;
    }
    /// Sets the window icon from the game's 48x48 RGB565 icon.
    void SetIcon(const std::vector<u16>& rgb565, int size);
    void SetTitle(const std::string& title);
    void UpdatePerformanceInfo(double fps, double speed);
    /// Refresh rate of the monitor the window is on, in Hz (0 if unknown).
    int RefreshRate() const;

    SDL_Window* Handle() const {
        return window;
    }
    bool IsFullscreen() const {
        return fullscreen;
    }
    /// Borderless fullscreen on the current monitor, or a normal window.
    void SetFullscreen(bool on);
    /// Index of the monitor the window is on.
    int CurrentMonitor() const;
    /// Put the game on another monitor (fullscreen there if it is fullscreen).
    void MoveToMonitor(int index);
    /// Recompute the screen layout after a settings change.
    void RefreshLayout() {
        OnResize();
    }
    /// Events are offered to `filter` first (the settings menu); it returns true to keep them.
    void SetEventFilter(std::function<bool(const SDL_Event&)> filter) {
        event_filter = std::move(filter);
    }
    /// While a menu is open the game gets no input and the cursor stays visible.
    void SetMenuOpen(bool open);

private:
    void OnKey(int scancode, bool pressed, bool repeat, u16 mods);
    void OnMouseButton(u32 button, bool pressed, s32 x, s32 y);
    void OnMouseMotion(s32 x, s32 y);
    void OnMouseWheel(s32 dy);
    void OnResize();
    void ToggleFullscreen();
    void TapKey(int scancode);
    void ReleaseTappedKeys();
    void SetTurbo(bool on);

    Core::System& system;
    SDL_Window* window = nullptr;
    u32 window_id = 0;
    std::atomic_bool is_open{true};
    bool focused = true;
    bool fullscreen = false;
    bool turbo = false;
    std::string title;
    std::function<bool(const SDL_Event&)> event_filter;
    bool menu_open = false;
    u32 last_motion_ticks = 0;
    bool cursor_visible = true;
    // Keys pressed on behalf of the mouse wheel, released a few frames later
    std::vector<std::pair<int, u32>> tapped;
};
