# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
