# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-10-05

### Added

- **Job queue.** Heavy jobs (conversions, exports, indexing, setup steps) run one at a time and wait in a visible,
  reorderable queue; an identical job already queued or running is refused. Jobs, logs and every result are stored in
  `<workspace>/jobs.sqlite`: the history survives a restart and a job cut short is *interrupted* and can be resumed.
- Live job list over server-sent events (no polling), results paged from the database, failures grouped by cause
  with "Réessayer ceux-là", "Réessayer les échecs", "Reprendre", "Relancer tout", copyable report.
- **Desktop shell.** One instance (a second launch brings the window to the front), instant splash, window size and
  position remembered, notification-area icon: closing the window while a job runs keeps omni working in the
  background and a toast tells when it is done. A native confirmation replaces the browser's `confirm()`.
- **Garry's Mod.** "Ouvrir dans Garry's Mod" starts the game on a map with the model ready to spawn (`omni_spawn`
  Lua in the addon); a warning in the jobs panel when GMod is open while models are written.
- **Updates.** The latest GitHub release is checked at launch (read token for a private repository); one click
  downloads the installer, verifies its SHA-256 and installs.
- **Game library** (`/games`): add a game by its folder, omni identifies the engine and game (Glacier: 007 First Light,
  HITMAN; Unreal: project and engine version) and offers the ones found in Steam. Unsupported engines are listed
  as "bientôt".
- "Tout installer" on the setup page: extraction, names, model compiler and Garry's Mod as one resumable job.
- Faster models: the SMD writer of props runs in Rust (parallel chunks, byte-identical output) and the
  overlapping-triangle removal is vectorised (no per-triangle Python tuples).
- Convert "everything matching a filter" server-side (no more 50,000 keys through the browser).
- Installer fetches the WebView2 runtime when it is missing.
- CI: packaged-app smoke test (`scripts/smoke_package.py`) on release and on pull requests touching the packaging;
  Node 24 action versions; Bun pinned and cached.

### Changed

- A second batch, export or setup step is queued behind the running one instead of competing with it.
- Messages of the API are in French; the property selection survives a restart; the home page does not refresh a
  hidden window.

### Fixed

- three.js memory: environment map, checker texture, grid and glTF files dropped while loading are released, and the
  WebGL context is freed when the viewer closes.
- Pages no longer fail silently (sound list, preview deletion, selecting all props); a character preview stops
  polling when the page is left; the setup page no longer polls twice.


## [0.3.1] - 2026-10-05

### Added

- Biome lint and format for the web interface (`bun run lint`, part of CI).
- Log files: `<workspace>/logs/omni.log` (rotating), one file per worker process, crash dumps of native faults;
  tracebacks of failed jobs and uncaught thread exceptions are recorded. `GET /api/diagnostic` returns a report to
  attach to a bug.
- Native Windows folder dialog (no Tk): works in the packaged app and in the browser mode.
- Confirmation before closing the window or stopping omni while a job runs.
- A status `SKIPPED` for models with nothing to convert (decal-only meshes), kept apart from failures.

### Changed

- Props of the catalog get a unique output path: models that shared a readable path (truncated names, `^…_dynamic`
  variants, the same leaf in two folders) now carry the end of their hash. The catalog rebuilds itself once.
- studiomdl timeout grows with the mesh (60 s + 1 s per 1,500 triangles, 10 minutes at most) and a timeout on a big
  mesh is no longer retried without collision.
- One heavy job at a time: a second batch, export or setup step is refused with a clear message.
- The Rust core reports a panic as `NativeError` (an ordinary exception) instead of PyO3's `BaseException`.

### Fixed

- About 1,300 converted models were silently overwritten by another asset, and 268 of the 839 failed models of the
  last full run came from workers sharing one work folder (access violations, permission errors).
- A stale `.phy`/`.vvd` of an earlier build could be copied next to a model compiled without collision; the
  playermodel materials are kept until the new model compiled.
- The names database could stay empty forever after an interrupted build, and updating the names failed on Windows
  while a source held the file; several workers building it at once failed (now built once, then swapped in).
- The batch survives a worker that crashes (the culprit asset is FAILED, the others carry on), cancellation stops the
  workers and the studiomdl they started, and the number of workers is bounded (Windows refuses more than 61).
- The mesh-to-templates index is built once before the workers start; the property dictionary covers every material
  (it stopped at 8,000, dropping colour overrides for 407 parameters).
- Catalog rebuilds swap a finished table in one step; concurrent requests no longer open two catalogs.
- `chunk10` sorted before `chunk2` when two chunks held the same resource.
- The local API refuses other websites (Host and Origin checks): a web page could stop omni or clear previews.
- A corrupt `.rpkg` header could make the process allocate hundreds of gigabytes and abort; sizes are checked against
  the file. Other malformed inputs (empty images, an undefined Vorbis mode, a wrapping chunk size) are errors, not
  panics.
- Slot names such as `mapGREEN_DIRT_Tex_Basecolor` resolve to their role, and the wind slots deliberately left out
  are no longer reported as unknown.
- Settings: concurrent saves no longer lose keys, numbers are bounded.
- The folder picker of the packaged app, the leftover `.part` files of cancelled downloads, texts that cited
  `Omni.cmd` and `uv run`.

## [0.3.0] - 2026-10-05

First portable release.

### Added

- Windows application: the interface in its own WebView2 window (`omni app`, default), installer and portable archive.
- First-run setup: game detection in the Steam libraries, native extraction of the resources omni uses (Rust RPKG reader, resumable, patch aware), names from Bond-Hashes, StudioMDL-CE downloaded and verified by SHA-256, Garry's Mod detection.
- Data folder under `%LOCALAPPDATA%\omni` (`OMNI_HOME` to move it); locations of the game, assets, Garry's Mod, StudioMDL, ffmpeg and Blender in the settings.
- Textures workbench: every game texture, filters, channel preview, materials and models using it.
- Models workbench with one layout for props and characters, game materials and raw textures in both inspectors.
- Home page with global exports, progress and cancellation; server-side settings used by every conversion and export.
- Rust core (native module and WebAssembly): Wwise audio to Ogg/FLAC/WAV with tags, Wwise bank hierarchy for naming music and bank sounds, texture mips, DXT and VTF writing, RPKG extraction.
- Sound export to separate Ogg and MP3 folders, about 1,300 sounds per second.
- `Source` contract (`omni/sources/base.py`) with the capabilities props, characters, textures and sounds.

### Changed

- Texture quality defaults to the game's own resolution (normal maps capped at 2K).
- Character previews are built in a scratch addon and no longer touch the real one.
- The old single-file page and the ww2ogg/vgmstream audio chain are gone.

### Fixed

- The viewer was covered by an empty placeholder on wide screens until a model was in the URL.
- A character with a part that has no skeleton failed the whole build; the part is skipped with a note.
