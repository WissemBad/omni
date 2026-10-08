# Contributing

Bug reports, fixes and support for more games are welcome. For anything larger than a fix, open an issue first so the approach can be agreed on before you spend time on it.

## Setup

Follow [Development](README.md#development) in the README. The interface depends on `@wissem-industries/ui`, published on GitHub Packages: GitHub requires a token with `read:packages` in your user `.npmrc` even for public packages.

`omni_native` (the Rust core) is installed by maturin outside the lockfile: use `uv sync --inexact` and `uv run --no-sync`, otherwise uv removes it.

## How the code is organised

- `omni/sources/` reads a game. `base.py` is the contract of a source (capabilities `props`, `characters`, `textures`, `sounds`); a source produces the neutral model of `omni/core/ir.py`.
- `omni/targets/` writes Garry's Mod, glTF and Blender files from that model and knows no game. `targets/shading.py` is how every target reads a material.
- `native/` is the Rust core: parsers, codecs, the formats omni writes and the bulk readers. Python orchestrates jobs and serves the API. Every parser of a game file checks sizes before allocating and is covered by `native/src/fuzz.rs`.
- `web/` is the interface (Nuxt 4, Nuxt UI 4, Wissem UI). It only talks to the API and only shows the tools the current source declares.

More detail in [docs/architecture.md](docs/architecture.md).

## Rules

- No game file, extracted asset or user data in the repository. Paths come from `omni/core/config.py`; never build one by hand.
- Windows only. Do not add cross-platform code without discussing it first.
- Interface text is in French.
- Before changing an output format, convert a batch of props with `OMNI_HOME` pointing at a throwaway folder.

## Checks

All of these must pass before a pull request is merged:

```powershell
uv run ruff check
uv run pytest
cargo test --manifest-path native/Cargo.toml --no-default-features
cd web; bun run check
```

Tests that need game data skip themselves when the game is not installed.

## Pull requests

- Branch from `main` as `feat/…`, `fix/…` or `chore/…`, and use [Conventional Commits](https://www.conventionalcommits.org/) for the title.
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) for every change a user can see.
- Pull requests are squash-merged once the checks are green.
