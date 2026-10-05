# Architecture

```
omni/sources/base.py     contract of a source (capabilities props, characters, textures, sounds)
omni/sources/glacier     007 First Light (Hitman: same engine, other profile)  ->  omni/core/ir.py (neutral)
omni/core/               config, settings, setup, catalogs (props, textures), naming, Windows helpers
omni/targets/source      VMT/VTF, SMD/QC, collision, StudioMDL, playermodels, deployment
omni/targets/audio       sound export (Rust core), index, tags
omni/ui/                 FastAPI: routes_models, routes_textures, routes_sounds, routes_setup, output (viewer), jobs
omni/app.py              the desktop window (pywebview / WebView2) and the local server
web/                     Nuxt 4 interface (static build served by the API)
native/                  Rust core (CPython module and WebAssembly)
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

One crate, two builds. The CPython module (`maturin`, features `python` and `parallel`) is the fastest; the WebAssembly build (`--target wasm32-unknown-unknown --no-default-features`, run by wasmtime) needs no native module and is the fallback. `omni/native.py` exposes `R.<function>`: native when the loaded module has the function, WebAssembly otherwise. File-system code (RPKG extraction) is native only.

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
