// In-game graphics settings (F1), drawn with Dear ImGui over the game screens.

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "user_settings.h"
#include "video_core/renderer_base.h"

union SDL_Event;
class GameWindow;

class SettingsMenu final : public VideoCore::Overlay {
public:
    SettingsMenu(GameWindow& window, UserSettings& settings);
    ~SettingsMenu() override;

    void InitVulkan(const VulkanInfo& info) override;
    std::function<void(void*)> Record(u32 width, u32 height) override;

    /// Window events go here first; returns true if the menu used the event.
    bool HandleEvent(const SDL_Event& event);
    /// Free the menu's GPU resources (before the renderer goes away).
    void ReleaseGpu();
    bool IsOpen() const {
        return open;
    }

    /// Push `settings` to the emulator core, the renderer and the window. `startup` is true
    /// before the game is loaded (nothing to refresh yet).
    static void Apply(const UserSettings& settings, GameWindow& window, bool startup);

private:
    void Draw();
    void Changed();
    void SetOpen(bool value);

    GameWindow& window;
    UserSettings& settings;
    bool open = false;
    bool vulkan_ready = false;
    void* device = nullptr;
    void* device_wait_idle = nullptr; // PFN_vkDeviceWaitIdle
    bool context_ready = false;
    double hint_until = 0.0; // show "F1: settings" until this time (seconds, SDL ticks)
    int custom_fps = 200;
};
