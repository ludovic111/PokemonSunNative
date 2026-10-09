#!/usr/bin/env bash
# Build Pokémon Sun as a native Linux program from your own cartridge dump.
#
#   ./build.sh [GAME.3ds | GAME.cci | archive.7z | archive.zip]
#
# Without an argument the script looks in ./rom and in this folder. The game's code is translated
# to C++ (build/gen), compiled with clang, and a `pokemon-sun` launcher plus a desktop entry are
# installed for your user. Nothing derived from the game leaves your machine.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$PWD
BUILD=$ROOT/build
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/PokemonSunNative
BIN_DIR=$HOME/.local/bin

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- Tools --------------------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1; }
missing=()
for t in git python3 clang clang++ cmake; do need "$t" || missing+=("$t"); done
if ((${#missing[@]})); then
  die "missing tools: ${missing[*]}
  Ubuntu / Debian:  sudo apt install git python3 clang cmake ninja-build pkg-config
  Fedora:           sudo dnf install git python3 clang cmake ninja-build pkgconf
  Arch:             sudo pacman -S git python clang cmake ninja pkgconf"
fi
cmake_version=$(cmake --version | head -1 | grep -oE '[0-9]+\.[0-9]+')
python3 -c "import sys; sys.exit(0 if tuple(map(int, '$cmake_version'.split('.'))) >= (3, 25) else 1)" \
  || die "CMake 3.25 or newer is needed (found $cmake_version): pip install --user cmake, or your distro's backports"
generator=(-G "Unix Makefiles"); need ninja && generator=(-G Ninja)

# ---- Sources ------------------------------------------------------------------------------------
if [ ! -e external/azahar/CMakeLists.txt ] || [ ! -e external/azahar/externals/dynarmic/CMakeLists.txt ]; then
  say "Fetching Azahar and its libraries (one time, ~450 MB)"
  git submodule update --init --recursive
fi
if git -C external/azahar apply --reverse --check "$ROOT/patches/azahar.patch" 2>/dev/null; then
  :
else
  say "Applying PokemonSunNative's hooks to Azahar"
  git -C external/azahar apply "$ROOT/patches/azahar.patch" || die "patches/azahar.patch does not apply (is external/azahar at the pinned commit?)"
fi

# ---- The game -----------------------------------------------------------------------------------
find_rom() {
  local f
  for f in rom/*.3ds rom/*.cci rom/*.3DS rom/*.CCI ./*.3ds ./*.cci; do [ -f "$f" ] && { readlink -f "$f"; return; }; done
}
extract() { # archive -> rom/
  mkdir -p rom
  case "$1" in
    *.zip|*.ZIP)
      python3 -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall('rom')" "$1" ;;
    *)
      local x=""
      for t in 7zz 7z 7za; do need $t && { x=$t; break; }; done
      [ -n "$x" ] || die "extracting $1 needs 7-Zip: sudo apt install 7zip  (or extract it into ./rom yourself)"
      "$x" x -y -orom "$1" >/dev/null ;;
  esac
}
ROM=""
if [ $# -ge 1 ]; then
  case "$1" in
    *.7z|*.zip|*.ZIP|*.7Z) say "Extracting $1"; extract "$1"; ROM=$(find_rom) ;;
    *) ROM=$(readlink -f "$1") ;;
  esac
else
  ROM=$(find_rom)
  if [ -z "$ROM" ]; then
    for a in rom/*.7z rom/*.zip ./*.7z ./*.zip; do
      [ -f "$a" ] && { say "Extracting $a"; extract "$a"; ROM=$(find_rom); break; }
    done
  fi
fi
[ -n "$ROM" ] && [ -f "$ROM" ] || die "no game found: put your decrypted Pokémon Sun dump (.3ds / .cci, or a .7z / .zip of it) in ./rom"
say "Game: $ROM"

# ---- Build Azahar's libraries and the cycle-count helper -------------------------------------------
mem_gb=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
JOBS=${JOBS:-$(nproc)}
max_jobs=$(( mem_gb > 2 ? mem_gb * 3 / 2 : 2 ))   # ~0.7 GB per compiler at worst
(( JOBS > max_jobs )) && JOBS=$max_jobs

mkdir -p "$BUILD"
if [ ! -x "$BUILD/tickcount" ]; then
  clang++ -std=c++20 -O1 -Iexternal/azahar/src tools/tickcount/main.cpp \
    external/azahar/src/core/arm/dynarmic/arm_tick_counts.cpp -o "$BUILD/tickcount"
fi

say "Translating the game's ARM code to C++"
python3 -I -c "import sys; sys.path.insert(0, 'tools'); from recomp.__main__ import main; main()" \
  "$ROM" "$BUILD/gen" --tickcount "$BUILD/tickcount"

# Development files that are often missing although the library itself is installed: give
# pkg-config what it needs from headers we carry (the libraries are loaded at run time)
shim=$BUILD/shim
rm -rf "$shim" && mkdir -p "$shim/pkgconfig" "$shim/include" "$shim/lib"
if ! pkg-config --exists egl 2>/dev/null; then
  cp -r external/azahar/externals/sdl2/SDL/src/video/khronos/EGL external/azahar/externals/sdl2/SDL/src/video/khronos/KHR "$shim/include/"
  printf 'Name: egl\nDescription: EGL headers (from SDL)\nVersion: 1.5\nCflags: -I%s/include\nLibs:\n' "$shim" > "$shim/pkgconfig/egl.pc"
fi
if ! pkg-config --exists libdecor-0 2>/dev/null; then
  # Window title bars on GNOME (Wayland): needed to move the window between monitors
  decor=$(ldconfig -p 2>/dev/null | awk '/libdecor-0\.so\.0 .*x86-64/ {print $NF; exit}')
  if [ -n "$decor" ]; then
    cp cmake/shim/libdecor.h "$shim/include/"
    ln -sf "$decor" "$shim/lib/libdecor-0.so"
    printf 'Name: libdecor\nDescription: libdecor header (vendored)\nVersion: 0.2.2\nCflags: -I%s/include\nLibs: -L%s/lib -ldecor-0\n' "$shim" "$shim" > "$shim/pkgconfig/libdecor-0.pc"
  else
    echo "   note: libdecor is not installed; on GNOME the game window will have no title bar (sudo apt install libdecor-0-0)"
  fi
fi
export PKG_CONFIG_PATH="$shim/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
sdl_args=()
# SDL stops on incomplete X11 development files; without them the game uses Wayland only
pkg-config --exists x11 xext 2>/dev/null || sdl_args+=(-DSDL_X11=OFF)

# Reconfigure when the configuration recipe changes (the compiled objects are kept)
CONFIG_VERSION=2
if [ -f "$BUILD/azahar/CMakeCache.txt" ] && [ "$(cat "$BUILD/azahar/.psn-config" 2>/dev/null)" != "$CONFIG_VERSION" ]; then
  rm -f "$BUILD/azahar/CMakeCache.txt"
fi
if [ ! -f "$BUILD/azahar/CMakeCache.txt" ]; then
  say "Configuring"
  cmake -S external/azahar -B "$BUILD/azahar" "${generator[@]}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DENABLE_QT=OFF -DENABLE_WEB_SERVICE=OFF -DENABLE_SCRIPTING=OFF -DENABLE_GDBSTUB=OFF \
    -DENABLE_TESTS=OFF -DENABLE_ROOM=OFF -DENABLE_LTO=OFF -DCITRA_WARNINGS_AS_ERRORS=OFF \
    -DPSN_SOURCE_DIR="$ROOT" -DPSN_GEN_DIR="$BUILD/gen" "${sdl_args[@]}" ${PSN_CMAKE_ARGS:-}
  echo "$CONFIG_VERSION" > "$BUILD/azahar/.psn-config"
fi

sdl_config=$(find "$BUILD/azahar/externals/sdl2" -name SDL_config.h -path '*include-config*' 2>/dev/null | head -1)
if [ -n "$sdl_config" ] && ! grep -qE 'define SDL_VIDEO_DRIVER_(X11|WAYLAND) 1' "$sdl_config"; then
  rm -rf "$BUILD/azahar"
  die "SDL found neither X11 nor Wayland development files, so the game could not open a window.
  Ubuntu / Debian: sudo apt install libx11-dev libxext-dev libwayland-dev libxkbcommon-dev libegl-dev
  then run ./build.sh again."
fi

say "Compiling with $JOBS jobs (first build: a few minutes on a fast CPU, longer otherwise)"
cmake --build "$BUILD/azahar" --target pokemon-sun -j "$JOBS"
EXE=$(find "$BUILD/azahar/bin" -name pokemon-sun -type f -perm -u+x | head -1)
[ -n "$EXE" ] || die "build finished but the executable was not found"

# ---- Install for this user ------------------------------------------------------------------------
say "Installing the launcher"
mkdir -p "$DATA/bin" "$BIN_DIR" "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
install -m 755 "$EXE" "$DATA/bin/pokemon-sun"
python3 -I tools/extract_icon.py "$ROM" "$DATA/icon.png" || true
cat > "$BIN_DIR/pokemon-sun" <<EOF
#!/bin/sh
# Pokémon Sun (PokemonSunNative). Options: --windowed
# On laptops with switchable graphics, render on the NVIDIA GPU.
export __NV_PRIME_RENDER_OFFLOAD=1 __VK_LAYER_NV_optimus=NVIDIA_only
exec "$DATA/bin/pokemon-sun" "\$@" "$ROM"
EOF
chmod +x "$BIN_DIR/pokemon-sun"
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/pokemon-sun-native.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pokémon Sun
Comment=Pokémon Sun, natively on Linux (PokemonSunNative)
Exec=$BIN_DIR/pokemon-sun
Icon=$DATA/icon.png
Terminal=false
Categories=Game;
EOF

say "Done. Start it from your applications menu, or run: pokemon-sun"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "   ($BIN_DIR is not in your PATH; run $BIN_DIR/pokemon-sun)";; esac
