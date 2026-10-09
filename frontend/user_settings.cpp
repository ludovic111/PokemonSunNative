#include "user_settings.h"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>

std::string UserSettings::Path() {
    const char* xdg = std::getenv("XDG_CONFIG_HOME");
    const std::string base =
        xdg && *xdg ? xdg : std::string(std::getenv("HOME") ? std::getenv("HOME") : ".") + "/.config";
    return base + "/PokemonSunNative/settings.ini";
}

void UserSettings::Load() {
    std::ifstream in(Path());
    if (!in)
        return;
    std::map<std::string, std::string> kv;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#' || line[0] == ';')
            continue;
        const auto eq = line.find('=');
        if (eq == std::string::npos)
            continue;
        kv[line.substr(0, eq)] = line.substr(eq + 1);
    }
    auto get_int = [&](const char* key, int& out) {
        if (auto it = kv.find(key); it != kv.end())
            out = std::atoi(it->second.c_str());
    };
    auto get_bool = [&](const char* key, bool& out) {
        if (auto it = kv.find(key); it != kv.end())
            out = it->second == "1" || it->second == "true";
    };
    get_bool("fullscreen", fullscreen);
    get_int("monitor", monitor);
    get_int("fps", fps);
    get_int("vsync", vsync);
    get_int("resolution", resolution);
    get_int("texture_filter", texture_filter);
    get_bool("smooth_scaling", smooth_scaling);
    get_int("layout", layout);
    if (auto it = kv.find("touch_size"); it != kv.end())
        touch_size = static_cast<float>(std::atof(it->second.c_str()));
    get_int("touch_position", touch_position);
    get_bool("swap_screens", swap_screens);
}

void UserSettings::Save() const {
    std::error_code ec;
    std::filesystem::create_directories(std::filesystem::path(Path()).parent_path(), ec);
    std::ostringstream out;
    out << "# PokemonSunNative settings (also editable in the game: F1)\n"
        << "fullscreen=" << fullscreen << "\n"
        << "monitor=" << monitor << "\n"
        << "# fps: -1 = monitor refresh rate, 0 = the game's own 30 FPS\n"
        << "fps=" << fps << "\n"
        << "# vsync: -1 = automatic, 0 = off, 1 = on\n"
        << "vsync=" << vsync << "\n"
        << "# resolution: 0 = match the window, N = N x 240p (6 = 1440p)\n"
        << "resolution=" << resolution << "\n"
        << "texture_filter=" << texture_filter << "\n"
        << "smooth_scaling=" << smooth_scaling << "\n"
        << "layout=" << layout << "\n"
        << "touch_size=" << touch_size << "\n"
        << "touch_position=" << touch_position << "\n"
        << "swap_screens=" << swap_screens << "\n";
    std::ofstream(Path()) << out.str();
}
