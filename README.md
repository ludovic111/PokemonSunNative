# PokemonSunNative

Pokémon Sun as a native Linux program. The game's ARM code is **recompiled ahead of time to
x86-64**, in the spirit of the N64 and Xbox 360 recompilation projects, so no CPU emulation is
involved. It's built for keyboard and mouse.

> **Experimental.** From boot through the intro, naming and cutscenes, the house, and out onto
> Route 1, everything has been played at full speed (30 FPS, the game's native frame rate) on an
> RTX 4080. Most of the game has not been played through yet; see [Status](#status).

**No game files are included.** You need your own decrypted dump of Pokémon Sun. Everything
derived from it (the translated code, the executable) is built on your machine and stays there.

## Requirements

- Linux x86-64 with a Vulkan GPU. Developed on an NVIDIA RTX 4080 (driver 595), Ubuntu 26.04.
- About 8 GB free disk space (4 GB of it is the game itself), 8 GB of RAM or more.
- A decrypted dump of **Pokémon Sun** (`.3ds` / `.cci`, or a `.7z` / `.zip` containing one).
  Tested with *Pokémon Sun (Europe) (En,Ja,Fr,De,Es,It,Zh,Ko)*, v1.0 (the cartridge version).
  Encrypted dumps are refused.
- Build tools and libraries:

  ```sh
  # Ubuntu / Debian
  sudo apt install git python3 clang cmake ninja-build pkg-config 7zip \
      libx11-dev libxext-dev libwayland-dev libxkbcommon-dev libegl-dev \
      libasound2-dev libpulse-dev
  # Fedora
  sudo dnf install git python3 clang cmake ninja-build pkgconf 7zip \
      libX11-devel libXext-devel wayland-devel libxkbcommon-devel mesa-libEGL-devel \
      alsa-lib-devel pulseaudio-libs-devel
  # Arch
  sudo pacman -S git python clang cmake ninja pkgconf 7zip libx11 libxext wayland \
      libxkbcommon libglvnd alsa-lib libpulse
  ```

  CMake 3.25 or newer is needed (`pip install --user cmake` if your distro's is older).

## Build and play

```sh
git clone --recursive https://github.com/ludovic111/PokemonSunNative
cd PokemonSunNative
cp /path/to/your/Pokemon-Sun.3ds rom/      # or the .7z / .zip, it gets extracted
./build.sh
pokemon-sun                                 # or "Pokémon Sun" in your applications menu
```

`build.sh` translates the game (about 30 seconds), then compiles Azahar's libraries and about 3
million translated instructions. That takes about 4 minutes on a 24-thread CPU and proportionally
longer on smaller ones. It installs `~/.local/bin/pokemon-sun` and a desktop entry. Run it again
after `git pull` to rebuild.

The game starts in borderless fullscreen. Use `pokemon-sun --windowed` for a window.

## Controls

| Action | Keyboard | Mouse |
|---|---|---|
| Walk (Circle Pad) | `W` `A` `S` `D` | |
| Talk / confirm (A) | `Space` | |
| Back / cancel, hold to run (B) | `Shift` | right button |
| Menu (X) | `E` or `Esc` | middle button |
| Y | `Q` | |
| D-Pad (menus, lists) | arrow keys | wheel scrolls up / down |
| Touch screen | | left button on the small screen |
| L / R | `Z` / `C` | side buttons |
| Start / Select | `Enter` / `Backspace` | |
| Fast-forward (4×, hold) | `Tab` | |
| Fullscreen | `F11` or `Alt+Enter` | |
| Walk slowly (half tilt) | hold `Caps Lock` | |

The game pauses while its window is in the background (`--keep-running` turns that off). The cursor hides after 3 seconds without
movement.

## Saves

Saves are in `~/.local/share/PokemonSunNative/sdmc/Nintendo 3DS/00000000000000000000000000000000/00000000000000000000000000000000/title/00040000/00164800/data/`.
The layout is the same as Citra's and Azahar's: to continue a save from them, copy their
`.../title/00040000/00164800/data/` folder over this one (back up first).

There are no save states: the game runs as native code, so only the game's own save is
available.

## How it works

```
your .3ds ──► tools/recomp ──► build/gen/*.cpp ──► clang ──► pokemon-sun
               (Python)         C++ for every         native x86-64 game code
                                function              + runtime + Azahar's HLE
```

- **tools/recomp** reads the cartridge (ExeFS code, the `static.crs` description of the main
  executable, and the 115 CRO modules the game loads at run time). It decodes ARMv6K, Thumb and
  VFPv2 and finds every function: call targets, exports, relocations, jump tables, pointers in
  data (including self-relative C++ constructor tables and PC-relative address computations),
  and finally fills any remaining gaps. Each ARM function becomes one C++ function over a small
  register struct; guest memory goes through the 3DS page table.
- **runtime/** runs that code inside [Azahar](https://github.com/azahar-emu/azahar)'s
  reimplementation of the 3DS operating system: kernel, services, file system, the PICA200 GPU
  translated to Vulkan, and DSP audio. Each 3DS thread runs on its own native stack (a fiber) and
  yields to Azahar's scheduler at system calls and when its time slice is used up. Cycle counts
  come from Azahar's ARM11 timing table. Code modules (`Battle.cro`, `FieldRo.cro`, …) are
  matched by name when the game loads them, wherever in memory it puts them.
- **frontend/** is a small SDL2 + Vulkan window built for keyboard and mouse.
- **tests/fuzz** checks the translator against dynarmic, Azahar's ARM JIT. Random ARM, Thumb
  and VFP instructions run on both, and registers, flags and memory are compared
  (`tests/fuzz/run.sh`, after a build).

Only the 3DS OS layer and GPU are reimplemented, as in other recompilation projects (the N64
ones use RT64 for the RDP). The game logic itself runs as native code.

## Status

- Played and working: boot, language selection, saving settings, the intro with its 3D
  cutscenes, the in-game keyboard (with the mouse), the house (all rooms, the bag and hat
  event), leaving home, Route 1.
- Not yet verified: battles, the rest of the story, Festival Plaza, online features (which
  need Nintendo's servers and will not work).
- A crash saying `no translated code there` means the game reached code the analysis didn't
  find. Please open an issue with the lines from
  `~/.local/share/PokemonSunNative/log/azahar_log.txt` that start with `Core.ARM11 <Critical>`.
  The module and address go into `tools/recomp/hints/` and the next build includes them.

## Repository layout

| Path | What |
|---|---|
| `tools/recomp/` | the recompiler (Python, no dependencies) |
| `runtime/` | CPU backend for Azahar that runs the translated code; fibers; helpers used by generated code |
| `frontend/` | the game window, controls, startup |
| `patches/azahar.patch` | small hooks added to Azahar (CPU backend factory, thread and module notifications) |
| `external/azahar` | Azahar, pinned (git submodule) |
| `tests/fuzz/` | differential tester against dynarmic |
| `build.sh` | one-command build and install |

## Credits and license

- [Azahar](https://github.com/azahar-emu/azahar) and the Citra project, whose 3DS OS, GPU and
  audio reimplementation this runs on, and whose SDL frontend the window code started from.
- [dynarmic](https://github.com/azahar-emu/dynarmic), used as the reference CPU in the tests.
- The N64Recomp, XenonRecomp and TriAevum projects showed this approach works.

PokemonSunNative is licensed under the GNU GPL, version 2 or later (see `LICENSE`), like Azahar.

Pokémon is © Nintendo, Creatures Inc. and GAME FREAK inc. This project is not affiliated with or
endorsed by them. It contains no game code or assets. Use it only with a copy of the game you
own.
