# omni

Converts the assets of a game into Garry's Mod content: props, player models, textures and sounds. Windows desktop application, no game data included.

[![CI](https://github.com/Wissem-Industries/omni/actions/workflows/ci.yml/badge.svg)](https://github.com/Wissem-Industries/omni/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Wissem-Industries/omni?sort=semver)](https://github.com/Wissem-Industries/omni/releases)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

omni reads the resources of **your own copy** of a game, on your PC, and writes a ready-to-use Garry's Mod addon (MDL, VMT, VTF) plus audio files (Ogg, FLAC, MP3). Nothing is uploaded and no game file is shipped with omni.

The first source is *007 First Light* (Glacier engine). Sources are plugins: Hitman (same engine) and others can be added without touching the interface.

Built with Python, Rust (native core), Nuxt 4, Nuxt UI 4 and [Wissem UI](https://github.com/Wissem-Industries/ui). The window is the system's Edge WebView2, not a bundled browser.

## Install

Download `Omni-Setup-x.y.z.exe` (or the portable `Omni-x.y.z-windows.zip`) from the [releases](https://github.com/Wissem-Industries/omni/releases) and start Omni.

The first launch opens the setup:

1. **Game**: omni finds the game in your Steam libraries (or you pick its folder) and extracts only the resources it uses into its own data folder.
2. **Names**: the readable resource paths come from [Bond-Hashes](https://github.com/glacier-modding/Bond-Hashes) (MIT), downloaded once.
3. **Garry's Mod**: detected in Steam; its player animations and addons folder are used.
4. **Compiler**: [StudioMDL-CE](https://github.com/DeadZoneLuna/StudioMDL-CE) is downloaded and verified against pinned SHA-256 sums.

Requirements: Windows 10 or 11 with the WebView2 runtime (included in Windows 11), a copy of the game, Garry's Mod for model conversion, and roughly 40 GB of free space for the extracted resources. [ffmpeg](https://ffmpeg.org) is only needed for MP3 and for re-encoding non-Vorbis sounds to Ogg.

Closing the window while a job runs keeps omni working in the notification area (toast when it ends); omni checks GitHub for a newer release at launch (a read token is needed while the repository is private).

Data lives in `%LOCALAPPDATA%\omni` (override with `OMNI_HOME`). Uninstalling leaves it in place.

## Use

| Page | What it does |
| --- | --- |
| Home | State of the source, global exports with progress, GMod addon (link, `.gma`), tools and Rust core health |
| Models | Props and characters with one layout: list, 3D view, inspector with materials and the game's raw textures; conversion and playermodel builds |
| Textures | Every texture of the game, filters, channel preview, who uses it (materials, models) |
| Viewer | The compiled result as Garry's Mod loads it, with debug layers; also opens a decompiled addon |
| Sounds | Parallel export (Ogg, FLAC, WAV, MP3) with tags, browser and player |
| Jobs (panel) | The queue: one heavy job at a time, live progress, failures grouped by cause, retry / resume, history |
| Games | The library: add a game by its folder, omni identifies the engine; games stay separate |
| Settings | Quality, collision, parallelism, sounds, paths, updates, storage, maintenance |

Everything is available from the command line too: `Omni.exe --help`.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), [Bun](https://bun.sh), a Rust toolchain (rustup) and a token with `read:packages` in your user `.npmrc` for `@wissem-industries/ui`.

```bash
uv sync
uv run python -m omni native --build      # Rust core (omni_native)
cd web && bun install && bun run build    # interface
uv run python -m omni app                 # window (or `omni ui` for the browser)
uv run pytest                             # tests (the ones needing game data skip themselves)
cargo test --manifest-path native/Cargo.toml --no-default-features
```

Package locally: `uv run --no-sync pyinstaller packaging/omni.spec --noconfirm`, then compile `packaging/omni.iss` with Inno Setup for the installer.

Layout and conventions are in [docs/architecture.md](docs/architecture.md) and [AGENTS.md](AGENTS.md); contributions in [CONTRIBUTING.md](CONTRIBUTING.md).

## Release

Versions follow Semantic Versioning and are listed in [CHANGELOG.md](CHANGELOG.md).

```bash
uv run python scripts/release.py 0.4.0    # bumps every version file and the changelog
```

Merge the release pull request, then push the `v0.4.0` tag: the pipeline builds the installer and publishes the release.

## Legal

omni contains no asset of any game. It reads files you own, locally. *007 First Light*, *Garry's Mod* and the other names are trademarks of their owners; omni is not affiliated with them.
