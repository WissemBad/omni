# Architecture

```
omni/sources/base.py     contract of a source (capabilities props, characters, textures, sounds)
omni/sources/glacier     007 First Light (Hitman: same engine, other profile)  ->  omni/core/ir.py (neutral)
omni/core/               config, settings, setup, catalogs (props, textures), naming, Windows helpers
omni/targets/source      VMT/VTF, SMD/QC, collision, StudioMDL, playermodels, deployment
omni/targets/audio       sound export (Rust core), index, tags
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

Everything the user owns or derives lives outside the repository, in the data folder (`%LOCALAPPDATA%\omni`, `OMNI_HOME`):

| Path | Content |
| --- | --- |
| `Assets/Sorted/chunkN/<TYPE>/<HASH>.<TYPE>(.meta)` | resources extracted from the game packages (setup step 1) |
| `workspace/names/` | the readable resource paths and their lookup database (step 2) |
| `workspace/addons/omni_<source>/` | the generated Garry's Mod addon, linked into `garrysmod/addons` |
| `workspace/audio/<source>/<format>/` | exported sounds and `index.csv` |
| `workspace/cache`, `catalog_*.sqlite`, `textures_*.sqlite` | derived data, rebuilt on demand |
| `workspace/settings.json` | settings |
| `tools/studiomdl-ce/` | the model compiler (step 4) |

## Native core

One crate built by maturin as a CPython module (features `python` and `parallel`); it is required (no Python or WebAssembly fallback). `omni/native.py` exposes it as `N`, a Rust panic surfacing as `NativeError`. `cargo test --no-default-features` tests the modules without Python; `native/src/fuzz.rs` feeds every parser random and mutated files.

Modules: textures (TEXT/TEXD, BCn, mips, DXT, VTF, PNG), audio (Wwise Vorbis to Ogg, Platinum ADPCM, FLAC, WAV), Wwise banks (HIRC hierarchy), RPKG reader and extractor, PhysX collision (ALOC), skinning, SMD, entities.

## Extraction

`native/src/rpkg.rs` reads RPKG v1 (`GKPR`) and v2 (`2KPR`) packages and their patches: offset table, resource headers (with or without the "states size" field, detected by the table size), references, XOR scrambling and LZ4. It writes the layout `sources/glacier/meta.py` reads and keeps existing files of the right size, so an interrupted extraction resumes. Only the resource types listed in `omni/core/setup.py` (`NEEDED_TYPES`) are written.

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
