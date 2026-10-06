# Architecture

```
omni/sources/base.py     contract of a source (capabilities props, characters, textures, sounds)
omni/sources/glacier     007 First Light, HITMAN 3 (hitman.py, hm3.py; store.py reads .rpkg in place) -> core/ir.py
omni/sources/unreal      any Unreal Engine 5 game (game.py: Oodle + mappings, adapter.py, prepare.py)
omni/core/               config, settings, setup, catalogs (props, textures), naming, Windows helpers
omni/targets/source      VMT/VTF, SMD/QC, collision, StudioMDL, playermodels, deployment
omni/targets/audio       sound export (Rust core), index, tags
omni/targets/gltf        glTF 2.0 (.glb) per model: PBR, skeleton, skin weights
omni/ui/                 FastAPI: routes_models, routes_textures, routes_sounds, routes_setup, output (viewer), jobs
omni/app.py              the desktop window (pywebview / WebView2): splash, one instance, remembered geometry, tray
omni/games.py            game library: identify a game from its folder (engine, profile, version)
omni/ui/jobs.py          persistent job queue (SQLite), one heavy job at a time, SSE, retry
omni/ui/security.py      Host / Origin checks: the local API answers only omni's own window
omni/core/log.py         log files, crash capture (the packaged app has no console)
web/                     Nuxt 4 interface (static build served by the API)
native/                  Rust core (CPython module omni_native)
packaging/               PyInstaller spec and Inno Setup script
```

## Data

The application holds no user data. Everything the user owns or derives lives in the **Omni folder** (`Config.home`, `Documents\Omni` by default; `OMNI_HOME`, `omni home set <dir>` or `%LOCALAPPDATA%\omni\home.json` choose another). `core/config.py` is the only place that knows the layout; code asks `CONFIG.addon_dir(id)`, `CONFIG.sounds_dir(id)`, `CONFIG.game_dir(id)`... and never builds a path by hand.

```
Omni/
  exports/<game>/          what the user takes away (setting paths.exports moves it alone)
    garrysmod-addon/       the addon, linked into garrysmod/addons by a junction
    gltf/ sounds/ textures/ blend/    models for Blender, audio (+ index.csv), raw textures
    omni_<game>.gma
  workspace/               what omni manages
    config/                settings.json, games.json, viewer_roots.json, window.json
    state/                 jobs.sqlite (queue and history), run.json, migrated.json
    games/<game>/          catalog.sqlite, textures.sqlite, per-game caches (Unreal: Oodle, mappings, state.json)
    tools/                 StudioMDL-CE, Oodle (downloaded, SHA-256 pinned)
    names/ cache/ sandbox/ preview/ logs/ reports/ updates/ webview/
    assets/Sorted/         resources of a game extracted from its packages (optional, 007 only; setting paths.assets)
```

`core/migrate.py` moves data of the earlier layouts (one data root with a mixed `workspace/` and `tools/`) into this one: nothing is deleted or overwritten, same-volume moves are renames, the junction in `garrysmod/addons` follows the addon. It runs at start for `%LOCALAPPDATA%\omni`, and by hand with `omni migrate <old root>`.

## Native core

One crate built by maturin as a CPython module (features `python` and `parallel`); it is required (no Python or WebAssembly fallback). `omni/native.py` exposes it as `N`, a Rust panic surfacing as `NativeError`. `cargo test --no-default-features` tests the modules without Python; `native/src/fuzz.rs` feeds every parser random and mutated files.

Modules: textures (TEXT/TEXD, BCn, mips, DXT, VTF, PNG, JPEG), audio (Wwise Vorbis to Ogg, Platinum ADPCM, FLAC, WAV), Wwise banks (HIRC hierarchy), RPKG reader and extractor, PhysX collision (ALOC), skinning, SMD, entities, the glTF writer (`gltf.rs`) and the bulk readers (`scan.rs`).

`gltf.rs` builds every `.glb` (`N.Glb`: textures, materials, meshes, skeleton and skin, atomic save). Python decides what a model contains; Rust checks the arrays (lengths, index range, finite values), converts the frame, normalises tangents and weights, encodes the images without the GIL and writes the file. Props are exported by worker processes (`pipeline.run_gltf_batch`), so a native crash costs one model.

`scan.rs` reads tens of thousands of small files in parallel (PRIM headers, `.meta` references, Wwise labels, whole files): opening a file costs milliseconds under a real-time antivirus, which made the catalog and the sound list take minutes when read one by one.

## Extraction

`native/src/eta.rs` holds the remaining-time estimators (`N.Eta`: items with weights, cost model learnt from the finished ones; `N.Rate`: speed of the last seconds); `ui/jobs.py` feeds them (`plan`, `result`, `count`, `stage`/`stager`) and puts `eta`, `eta_at` and the current `stage` in each running job's view.

`native/src/rpkg.rs` reads RPKG v1 (`GKPR`) and v2 (`2KPR`) packages and their patches: offset table, resource headers (with or without the "states size" field, detected by the table size), references, XOR scrambling and LZ4. It writes the layout `sources/glacier/meta.py` reads and keeps existing files of the right size, so an interrupted extraction resumes. Only the resource types listed in `omni/core/setup.py` (`NEEDED_TYPES`) are written.

## Animations in the viewer

`native/src/source_anim.rs` reads compiled Source models (v44-49) and their `.ani`: skeleton, sequences and the per-frame bone transforms of one animation (raw 48/64-bit quaternions, half-float positions, run-length encoded streams scaled per bone, sections and external animation blocks). `ui/animation.py` lists the playable sequences of a model and of the models it includes (`targets/source/gmodanim.py` extracts `m_anm`, `f_anm`, `z_anm` once from Garry's Mod's VPK into `workspace/cache/gmod_anims`) and retargets a sequence onto the viewed skeleton by bone name: rotations on top of the model's own rest pose, only the pelvis moves, scaled by the height ratio; a movement sequence plays its most dynamic blend. `/api/<game>/output/animations` and `/animation` feed `OViewer.vue`, which drives the bones of the skinned GLB (`ui/output.build_glb` skins models whose vertices follow several bones).

## Sources

A source subclasses `Source`, declares its `capabilities` and is registered in `omni/sources/registry.py`. The API reads nothing else about it: `/api/sources` lists the workbenches, the pages show only those.

## Format notes (007 First Light, verified on data)

- A sub-mesh's `material_id` indexes the complete reference list of the `.meta`, BORG included.
- Vertices: int16x4 position, an 8-byte skin stream when weighted, then a 12 + 4 x uvSets bytes record (normal, tangent, bitangent, UVs). Z up, metres. Triangle order is already right for Source.
- Textures: TEXT + TEXD, one LZ4 block per mip; BC1/BC3 are copied unchanged into the VTF, BC7/BC5 are re-encoded (Source cannot read them).
- SRM: R specular, G roughness, B metallic. Slot names depend on the material class.
- Outfits: BIN1 templates (TEMP/TBLU), property ids are CRC32 of the property names; a family's variations become bodygroups and skins.
- Audio: WWES (streamed), WWEM (memory), WWEV (events), WBNK (banks: HIRC, DIDX, DATA). Wwise ids are FNV-1 of lower-cased names. Only Play actions name a sound; music switches and dialogue events carry 12-byte decision-tree nodes (key, node or children, weight, probability).

## Jobs

`omni/ui/jobs.py`. Heavy jobs (props, sounds, setup, textures, maintenance) wait in a FIFO queue and run one at a time;
light ones (one playermodel) start at once. An identical job already queued or running is refused (409). Everything
(jobs, log tail, every result) is written to `<workspace>/jobs.sqlite` by a flusher thread; at start a job that was
queued or running becomes *interrupted*. `GET /api/jobs/stream` pushes the list (server-sent events) whenever the
`version` counter moves; `/api/jobs/{id}/results` pages the results, `/report` groups failures by cause, `/retry`
replays a job (`failed`, `remaining`, `all`) through the starter registered by the route (`jobs.starters`).

## Shell and security

The window is single-instance (named mutex + `run.json`; a second launch posts `/api/focus`). Closing it while jobs
run hides it and keeps omni in the notification area; a toast tells when a job ends. The local API refuses any `Host`
that is not loopback (DNS rebinding) and any write whose `Origin` is another site (`omni/ui/security.py`). Errors,
tracebacks and native crashes go to `<workspace>/logs` (`GET /api/diagnostic` bundles them for a bug report).

## Output paths

A model's output path comes from its game path (`AssetInfo.rel_path`); paths shared by several resources (truncated
names, `^…_dynamic` variants, same leaf in two folders) get the end of their hash (`GlacierSource.rel_path`), so two
assets never write the same file. The catalog (`core/catalog.py`, versioned by `user_version`) stores the final path.

## Games and preparation

`omni/games.py` identifies a folder (Glacier: `Runtime/chunk*.rpkg` + the executable name; Unreal: `<Project>/Content/Paks`
with `.utoc` and a `*-Shipping.exe`, engine version read from it) and keeps the library (`workspace/games.json`).
`sources/registry.py` turns every supported library game into a source (`ENGINES`), 007 stays built-in. Adding a game
starts a *preparation* job (`games.prepare` -> `sources/unreal/prepare.py` or `sources/glacier/hitman_prepare.py`); its
state (`workspace/games/<id>/state.json`) carries the signature of the game files, so an update is detected and the
game prepared again at launch. `/api/sources` reports `ready` per game; the interface opens a game's pages only then.

## Unreal Engine 5 (native/src/unreal)

`iostore.rs` (TOC, blocks, partitions, directory index, container header), `oodle.rs` (the DLL is loaded at run time),
`zen.rs` (zen packages, script objects), `reflect.rs` (property layouts read from the executable: UHT registration
tables -> `Z_Construct_*` -> `FClassParams` / `FStructParams` / `FEnumParams`, type-specific pointer offset calibrated
per executable; own properties first, then the super's; `from_usmap` reads community `.usmap` files, the fallback
used by `sources/unreal/game.py` when the executable yields nothing: a local `.usmap`, else the matching folder of
TheNaeem/Unreal-Mappings-Archive), `props.rs` (unversioned properties, native structs),
`assets.rs` (bulk data, Texture2D, SoundWave), `mesh.rs` / `skel.rs` (render data), `../binka.rs` (Bink Audio).
`py_unreal.rs` exposes `UnrealGame`. Geometry stays in Unreal units; `sources/unreal/adapter.py` mirrors Y (left- to
right-handed), converts to metres and turns characters to face +Y. Humanoid bones of any rig get canonical names in
`targets/source/humanoid.py`.
