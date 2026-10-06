# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **One Omni folder, two parts.** Everything omni writes goes to `Documents\Omni` (movable with `omni home set <folder>`):
  `exports\<game>\` holds what you take away (the Garry's Mod addon, glTF models, sounds, textures, Blender files, the
  `.gma` archive) and `workspace\` what omni manages (settings, queue, catalogs, caches, tools, logs). The application
  itself holds no data. Data of the earlier layouts moves over without deleting or overwriting anything
  (`omni migrate <old folder>`), and the Garry's Mod link follows the addon. The exports folder can also be set apart
  in the settings.
- The glTF writer is part of the Rust core: arrays are validated before writing, images are encoded without the GIL
  and a native crash while exporting costs one model instead of the batch. Props are exported by worker processes.
- The prop catalog (11 s instead of 4 minutes), the texture catalog (12 s instead of 2.5 minutes), the sound list
  (25 s instead of 5 minutes) and the hashing of sounds before an export read the game's files in parallel in the Rust
  core. The VTF writer is the core's too.

### Removed

- The pure-Python texture fallback (`quicktex`), the `pygltflib` runtime dependency and the community files that the
  other repositories do not carry (issue and pull request templates, `CODEOWNERS`, `CONTRIBUTING.md`, `SECURITY.md`).

### Added

- **Unreal Engine 5 games** (any UE 5.x game with IoStore containers; validated on *Bronzebeard's Tavern*, UE 5.1):
  props (static meshes, skeletal meshes as statues), characters (humanoid skeletal meshes as playermodels),
  textures and sounds, all read in place from the game's `.utoc`/`.ucas` by the Rust core: IoStore containers
  (Oodle, Zlib, LZ4, optional AES), zen packages, **property layouts read statically from the game's executable**
  (no injection, no third-party mappings: 4,074 classes/structs and 724 enums in 30 ms; when an executable cannot be
  read, a `.usmap` placed next to the game or fetched from the community Unreal-Mappings-Archive is used instead),
  unversioned properties,
  Texture2D (BCn kept, other formats converted), StaticMesh/SkeletalMesh render data (LODs, sections, tangents,
  UVs, colours, skin weights, reference skeleton), MaterialInstance chains (texture roles from parameter and texture
  names, ORM / roughness / metallic maps, blend mode, two-sided) and **Bink Audio decoded natively** (port of
  vgmstream's decoder, bit-identical within 1 LSB). Bronzebeard's Tavern: 679/679 game props converted (0 failure,
  78 s), 15/15 characters built as playermodels, 337 sounds exported in 9 s.
- **HITMAN World of Assassination** source: the game's `.rpkg` packages are read in place (patch priority,
  deletions, LZ4/XOR) by a new native package store, names from glacier-modding/Hitman-Hashes, HM3 PRIM / MATI /
  TEXT decoders (layouts of the open-source RPKG-Tool), outfits offered as playermodels. Written without a copy of
  the game: covered by synthetic tests, to be validated on the real data.
- **One-folder setup for every game**: add a game folder (or one Steam found) on the *Jeux* page and omni prepares
  everything else in a background job: Oodle (pinned SHA-256), the game's structures, names, indexes, catalogs,
  the shared model compiler. A game updated since its preparation is indexed again at launch. Games stay separate
  (own caches under `workspace/games/<id>`, own addon `omni_<id>`); each game's pages open once it is ready.
- **007 First Light read in place** when the game is installed and nothing was extracted (the extracted
  `Assets/Sorted` tree stays supported and is used first): no 40 GB extraction. Verified identical (models,
  materials, textures, previews) on real resources repacked into packages.
- **glTF 2.0 target**: one `.glb` per model for Blender and other tools (PBR metal/roughness rebuilt from ORM/SRM
  maps, occlusion, alpha mode, skeleton and skin weights for skinned models), as a conversion option, from the prop
  inspector, or with `POST /api/<game>/models/gltf`. Output passes the Khronos validator without warning.
- Generic playermodel builder for sources without outfit templates: humanoid bones recognised in any rig (Unreal
  mannequin, Mixamo, 3ds Max Biped and CAT, Rigify...) and mapped onto ValveBiped, characters turned to face
  forward; 3D preview of the posed character.

### Fixed

- The model compiler downloaded by the setup lacked `celfbxsdk.dll`: `cestudiomdl.exe` exited silently and every
  conversion failed with an empty error. The file is now part of the pinned download, an incomplete compiler is
  reported as missing (and fetched again), and a compiler that cannot start gives a clear error.
- Playermodels of characters without toe bones failed to compile (hitbox on a bone studiomdl had removed).
- Mappings text read from a cache could make the core panic on a malformed line (found by the new fuzz tests).
- **Remaining-time estimates**: a batch that starts with its biggest items showed 40 hours, then dropped to minutes.
  The estimator now lives in the Rust core (`native/src/eta.rs`): it learns while the batch runs what an item costs
  from its weight (mesh size, number of variations) and prices what is left; checked on a real 27,686-prop
  conversion, it stays within about +/-30 % from 3 % of progress on (a plain average was off by a factor of 15).
  Counters without item weights use the speed of the last seconds instead of the average since the start.
- Exports now show **what they are doing**: steps with their own counter and time left (`Étape 2/3 · Export glTF
  (.glb)`), for the .blend and .glb exports that had no information, and for sounds (list, media fingerprints,
  conversion). The same progress block is used on the game page, the sounds page and the jobs panel.

### Changed

- **Playermodel batches run in parallel** (8 worker processes, the same crash-proof pool as props) instead of one
  family after another inside the server: about 7x faster (48 families in 57 s instead of 7 min). The registry
  and the Lua list are updated under a cross-process lock.
- **Faster prop batches**, same output: CoACD convex decompositions and prop variants (template resolution) are
  memoised on disk (`cache/memo_*.sqlite`, keyed by content and by the game data's signature), the largest models
  are started first (no more single big model running alone at the end), numpy's OpenBLAS is limited to the
  worker's share of the cores (16 workers x 16 threads made studiomdl up to 5x slower), name lookups and parsed
  materials are cached per process, sRGB decoding uses a table, archive indexes keep string paths. Measured on 300
  props (16 workers): first conversion 1.49 -> 2.12 props/s, reconversion 3.56 props/s (8.4 props/s without the
  one 80 s model).
- Compiled models are moved (not copied) from the sandbox into the addon and the SMD sources of a successful build
  are deleted (they reached 21 GB); they are kept when a build fails.

### Fixed

- Cancelling a batch ended the job in error (`'NoneType' object has no attribute 'values'`), and the studiomdl
  of a killed worker kept running: each worker now holds its children in a Windows job object closed with it.
- studiomdl's "WARNING: Error with convex elements..." (it then builds a single hull) was taken for an error: the
  playermodel failed, or the prop lost its collision and was compiled twice.
- Two workers writing the same shared texture at once failed with "Access is denied": the rename is retried while
  the other process holds the file, and a reader waits for a file being replaced.

### Removed

- The WebAssembly core and every pure-Python fallback: the Rust module is required (the packaged app ships it), one
  code path for textures, SMD, collisions, skinning and sounds. `wasmtime` and `texture2ddecoder` are no longer
  dependencies; obsolete investigation scripts and dead code are gone.

### Fixed

- Command-line commands now apply the saved settings (asset folder, GMod, compiler).
- The character and sound lists are never cached empty, and are rebuilt when the names change.

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
