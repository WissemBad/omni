# omni — sources multiples → Garry's Mod

Convertit des jeux (007 First Light d'abord) en contenu Garry's Mod : props, playermodels, textures, sons.
**Tout se fait dans l'interface web** ; la ligne de commande reste disponible pour les scripts.

## Lancer

Double-cliquer **`Omni.cmd`** à la racine de `007_IMPORTATION` (ou `./omni.sh` sous Git Bash / Linux / macOS).
Au premier lancement il installe les dépendances (`uv sync`), construit l'interface web (bun) et le cœur Rust
WebAssembly (cargo) si besoin, puis ouvre le navigateur sur http://127.0.0.1:8770. S'il tourne déjà, il ouvre simplement la page.

## L'interface

| Page | Contenu |
|---|---|
| **Accueil** | état de la source (catalogues, convertis, exportés), exports globaux annulables avec temps restant, addon GMod (lier, .gma), santé des outils et du cœur Rust, travaux récents |
| **Modèles** | Props et Personnages, même disposition : liste · vue 3D · inspecteur (résumé, matériaux avec les **textures brutes du jeu**, données). Conversion, playermodels (bodygroups/skins) |
| **Textures** | les 31 775 textures du jeu : recherche, dossiers, format, rôle, taille, usage ; aperçu par canal, plein écran, export PNG ; **qui l'utilise** (matériaux + emplacements, modèles → liens vers Modèles) |
| **Visionneuse** | ce que GMod chargera (MDL/VVD/VTX/PHY/VMT/VTF recompilés) : squelette, hitboxes, collision, comparaison avec l'original, matériaux reliés à leur matériau et leurs textures d'origine. Fonctionne aussi sur un addon décompilé |
| **Sons** | export (Rust, parallèle, tags), explorateur avec écoute, langues, noms des événements et de la musique |
| **Réglages** | qualité des textures (défaut : *maximum*, résolution du jeu sauf normales en 2K), effort DXT, collision, parallélisme, sons (format, langues, tags), chemin de GMod, stockage et maintenance |

Les réglages sont stockés côté serveur (`workspace/settings.json`) : l'interface, les travaux et la CLI appliquent les mêmes.

## Architecture

```
sources/base.py        contrat d'une source (capacités props / characters / textures / sounds)
sources/glacier        007 First Light (Hitman : même moteur, autre profil)      → core/ir.py (neutre)
core/                  config, settings, catalogue des props, catalogue des textures (texture → matériau → modèle)
targets/source         VMT/VTF, SMD/QC, collision, StudioMDL, playermodels, déploiement
targets/audio          export des sons (moteur Rust), index, tags
ui/                    API FastAPI : routes_models, routes_textures, routes_sounds, output (visionneuse), jobs
web/                   interface Nuxt 4 + Wissem UI (voir web/README.md)
native/                cœur Rust (voir plus bas)
```

Ajouter un jeu : une classe qui hérite de `sources.base.Source`, déclarée dans `sources/registry.py`. Les pages
n'affichent que les ateliers des capacités déclarées.

## Cœur Rust (`native/`)

Le même crate est compilé de deux façons :

- **module CPython natif** (`maturin`, features `python` + `parallel`) : le plus rapide, multi-thread ;
- **WebAssembly** (`omni/omni_core.wasm`, exécuté par wasmtime) : portable, isolé, jamais bloqué par Windows.
  `omni/native.py` expose `R.<fonction>` : le natif quand le module chargé l'a, sinon le WebAssembly.

Contenu : textures (TEXT/TEXD → mips, décodage BCn, **chaîne de mips** en lumière linéaire / normales renormalisées /
couverture alpha préservée, DXT, VTF avec réflectivité, lecture VTF, PNG), **audio** (Wwise Vorbis → Ogg sans
réencodage avec granules exacts et tags — portage de ww2ogg —, Platinum ADPCM, FLAC sans perte avec MD5, WAV),
**banques Wwise** (hiérarchie HIRC : événements, états de musique, arbres de décision), collision PhysX (ALOC),
skinning, SMD, entités.

Construire : `uv run python -m omni native --build` (WebAssembly + natif) ou `--build --wasm`. Sous Windows avec
*Smart App Control* actif, un module natif fraîchement compilé est refusé par le système : le précédent reste utilisé
et le WebAssembly fournit les nouvelles fonctions (aucune action nécessaire). Pour retrouver les performances natives
complètes, désactiver Smart App Control (Sécurité Windows → Contrôle des applications) puis relancer la construction.

Vérifications : `uv run pytest` ; `uv run python tools/check_audio_native.py 200` compare le moteur Rust à l'ancien
export (PCM identique) ; `uv run python tools/bench_sounds.py <dossier>` mesure le débit.

## Sons

- `auto` : le Vorbis du jeu est ré-emballé en `.ogg` **sans réencodage** (audio identique), l'ADPCM décodé en `.flac` sans perte.
- Noms : chemin du jeu > dialogue (`voices/<langue>/<conversation>/<nom Wwise>`) > événement
  (`events/<préfixe>/<événement>/…`). La musique et les sons des banques sont nommés par la **hiérarchie Wwise** :
  `events/mx/MX_Music_SW_Play/Campaign/M01_CLOVER/S04a_CentralCamp/B02a_…/Combat_NL/…` (états de musique retrouvés
  par hash FNV-1 depuis les ressources WSGB/WSWB) ; noms des banques retrouvés de la même façon.
- Tags dans chaque fichier : titre (nom Wwise), album (événement / conversation), artiste (locuteur), langue, genre, source.
- Amorces de musique streamée stockées dans les banques ignorées (la version complète est exportée).
- ~1 400 sons/s (16 processus) : tout le jeu en quelques minutes ; reprise automatique, annulable.

## Ligne de commande (facultative)

```powershell
uv run python -m omni ui                       # l'interface (ce que fait Omni.cmd)
uv run python -m omni index                    # catalogue des props
uv run python -m omni convert --hash 010034F5BFC0DFF2
uv run python -m omni batch --match props --limit 500 --workers 8
uv run python -m omni pm-outfit outfit_clover_grunt_arrowhead_male_reg
uv run python -m omni sounds [--format flac] [--force --clean]
uv run python -m omni deploy [--remove] | gma | native [--build [--wasm]]
uv run pytest
```

## Où sont les choses
- `workspace/addons/omni_007fl/` : l'addon généré (lié dans `GarrysMod/garrysmod/addons` par jonction).
- `workspace/audio/007fl/` : les sons (`index.csv` avec titres, albums, langues).
- `workspace/textures_007fl.sqlite`, `catalog_007fl.sqlite` : catalogues ; `workspace/cache/` : index de l'archive, liste des sons, personnages.
- `workspace/preview/` : aperçus 3D et miniatures (vidables depuis Réglages).
- `third_party/` : StudioMDL-CE x64, Crowbar ; `tools/` : sondes de formats et vérifications.

## Notes de format (007 First Light) — vérifiées sur les données
- `material_id` d'un sous-mesh indexe la liste COMPLÈTE des refs du `.meta` (BORG compris).
- Textures : TEXT + TEXD, mips LZ4 un par un ; BC1/BC3 copiés tels quels dans le VTF, BC7/BC5 réencodés (Source ne les lit pas).
- SRM : R = spéculaire, G = rugosité, B = métal. Les rôles des textures viennent du nom (`normal_a`), de l'indice
  d'usage du jeu (`(ascolormap)`) ou de l'emplacement du matériau.
- Banques Wwise v150 : `HIRC` ; Sound (2) → média, MusicTrack (11) → médias ; conteneurs → enfants ; arbres de
  décision des Music Switch / Dialogue Events (nœuds de 12 octets) ; seules les actions Play nomment un son.
