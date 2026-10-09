// Player-facing settings, saved in ~/.config/PokemonSunNative/settings.ini.

#pragma once

#include <string>

struct UserSettings {
    // Display
    bool fullscreen = true;
    int monitor = -1;          // SDL display index, -1 = wherever the window opens
    int fps = -1;              // -1 = monitor refresh rate, 0 = the game's own 30 FPS, else FPS
    int vsync = -1;            // -1 = automatic (on when FPS <= refresh rate), 0 off, 1 on

    // Rendering
    int resolution = 0;        // internal resolution: 0 = match the window, N = N x 240p
    int texture_filter = 0;    // Settings::TextureFilter
    bool smooth_scaling = true; // linear (true) or nearest (false) when scaling screens

    // Layout
    int layout = 0;            // see LayoutChoice in settings_menu.cpp
    float touch_size = 2.5f;   // large screen / small screen ratio (Large screen layout)
    int touch_position = 2;    // Settings::SmallScreenPosition
    bool swap_screens = false;

    /// Read settings.ini (missing file or keys keep the defaults).
    void Load();
    /// Write settings.ini.
    void Save() const;
    static std::string Path();
};
