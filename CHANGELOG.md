# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
