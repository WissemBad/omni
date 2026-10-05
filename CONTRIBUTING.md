# Contributing

Thanks for helping. omni is a Windows application; see [docs/architecture.md](docs/architecture.md) for the layout and [AGENTS.md](AGENTS.md) for the conventions.

## Setup

```bash
uv sync
uv run python -m omni native --build
cd web && bun install && bun run build
```

`@wissem-industries/ui` comes from GitHub Packages: put a token with `read:packages` in your user `.npmrc`.

## Before a pull request

```bash
uv run pytest
uv run ruff check
cargo test --manifest-path native/Cargo.toml --no-default-features
cd web && bun run typecheck
```

Tests that need game data skip themselves. Add a line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).

## Rules

- Never commit game files, extracted resources, exports or personal paths. The repository must stay free of any game data.
- A new game is a new source (`omni/sources/base.py`), not a change to the interface.
- Run Python helper scripts from files, not `python -`: Windows process pools re-import `__main__`.
