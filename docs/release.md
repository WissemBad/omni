# Release process

Releases are built and published from a Windows PC with `scripts/build_release.ps1`. The Windows pipeline
(`.woodpecker/windows.yml.off` -> `scripts/ci_windows.ps1` -> the same script) is ready but parked until the Windows
agent described below exists; rename it to `windows.yml` to turn it on.

## Cutting a release

```powershell
uv run --no-sync python scripts/release.py <x.y.z>      # versions, version resource of the exe, CHANGELOG, lockfiles
# commit as "chore(release): vX.Y.Z", open the pull request, wait for the Linux pipeline, merge
git switch main; git pull
git tag -a vX.Y.Z -m vX.Y.Z; git push origin vX.Y.Z
.\scripts\build_release.ps1 <x.y.z> -Publish           # installer, archive, signed checksums, GitHub release
```

`scripts/release.py --check` (run by CI) fails when the versions of `pyproject.toml`, `omni/__init__.py`,
`native/Cargo.toml`, `native/pyproject.toml`, `web/package.json` and `packaging/version.txt` disagree.

## What the Windows pipeline builds

Once enabled, every pull request and every push to `main` builds the real package on Windows (without publishing):
Rust checks on Windows, web build, Rust core, tests with the core required, PyInstaller, `Omni.exe selfcheck` (every source imports,
the core loads), a smoke test that adds the synthetic game and browses it through the API, the installer, the archive
and the checksums. A packaging mistake is found before the tag, not after it.

## Signed updates

The updater installs a release only when `SHA256SUMS.txt` carries a valid ed25519 signature
(`SHA256SUMS.txt.sig`, made with the release key) and the installer's SHA-256 is listed. The public key is
`RELEASE_KEY` in `omni/core/update.py`. The secret key (`scripts/release_sign.py keygen`) is kept offline and passed to
the release script as `OMNI_RELEASE_KEY` (a file path; in the pipeline the secret `omni_release_key` holds its hex).
Losing the key means installed copies refuse every later update: keep a backup.

## The Windows agent

`windows.yml` runs on a Woodpecker agent with the local backend, labelled `platform=windows/amd64`:

- agent setting `WOODPECKER_BACKEND_LOCAL_ISOLATED_HOME=false` (the toolchains live in the user profile);
- installed: git, `uv`, Bun, Rust (rustup, MSVC toolchain, Visual Studio Build Tools "Desktop development with C++"),
  Inno Setup 6, the GitHub CLI, PowerShell 7 (`pwsh`);
- secrets: `github_packages_token` (read:packages, for `@wissem-industries/ui`); for tags `gh_token` (creates the
  release) and `omni_release_key` (hex of the release key).

The agent runs repository code without isolation: enable it for this repository only, and keep pull requests from forks
on the approval list.
