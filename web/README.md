# omni web

Interface d'omni : **Nuxt 4 + [Wissem UI](https://github.com/Wissem-Industries/ui)** (Nuxt layer), rendue en fichiers
statiques que `omni ui` sert à côté de l'API. Elle ne contient aucune logique de conversion : tout passe par
l'API (`omni/ui/`).

## Lancer

Le plus simple : `Omni.cmd` à la racine du projet (construit l'interface au premier lancement).

```bash
cd web && bun install && bun run build      # -> web/.output/public, après une modification de l'interface
uv run python -m omni ui                    # depuis omni/ : http://127.0.0.1:8770
```

Mode développement (rechargement à chaud, l'API doit tourner sur le port 8770) :

```bash
uv run python -m omni ui --no-open          # terminal 1
cd web && bun run dev                       # terminal 2 : http://localhost:3000, /api est relayé vers 8770
```

Le paquet `@wissem-industries/ui` vient de GitHub Packages : un token `read:packages` dans le `~/.npmrc` utilisateur
suffit (la portée est déjà déclarée dans `web/.npmrc`).

## Écrans

| Écran | URL | Contenu |
| --- | --- | --- |
| Accueil | `/<source>` | chiffres de la source, exports globaux (props, playermodels, sons) avec progression/annulation, addon GMod, outils, cœur Rust |
| Modèles | `/<source>/models?tab=props\|characters` | une disposition commune : dossiers · liste · vue 3D · inspecteur. Props : résumé, matériaux et **textures brutes du jeu**, données. Personnages : apparence (préréglages, skins, bodygroups), matériaux, export GMod |
| Textures | `/<source>/textures` | toutes les textures du jeu, filtres (format, rôle, usage, taille), aperçu par canal, plein écran, PNG ; matériaux et modèles qui l'utilisent |
| Visionneuse | `/<source>/viewer` | sortie compilée (ou dossier externe) : contrôles, variantes, matériaux reliés à leur origine, os, données ; calques de debug |
| Sons | `/<source>/sounds` | export (Rust, tags, langues, tout réécrire) et explorateur avec écoute |
| Réglages | `/<source>/settings` | réglages serveur (qualité, collision, parallélisme, sons, GMod), stockage, maintenance, arrêt |

Les anciennes adresses `/<source>/props` et `/<source>/characters` redirigent vers Modèles. Les filtres, l'élément
ouvert et l'onglet de l'inspecteur sont dans l'URL ; les liens entre ateliers (`?k=<clé>`, `?m=<modèle>`) ouvrent
directement l'élément.

## Plusieurs sources

L'interface ne connaît aucun jeu : elle lit `/api/sources` (id, titre, capacités) et n'affiche que les ateliers que la
source déclare. Ajouter un jeu = une classe `omni/sources/base.py:Source` enregistrée dans `omni/sources/registry.py`.

## Organisation

```
app/app.vue                    coquille : WNavbar, sélecteur de source, liaison GMod, panneau des travaux
app/pages/[source]/            index (accueil) · models · textures · viewer · sounds · settings
app/components/
  OPropsBench / OCharactersBench   ateliers de Modèles (même disposition)
  OPropInspector, OTextureInspector, OOutputInspector   inspecteurs
  OGameMaterials, OMaterialList     matériaux du jeu / convertis (reliés à leur origine)
  OTextureViewer                    plein écran commun (textures du jeu ou VTF convertis)
  OViewer                           viewport three.js (rendu à la demande)
app/composables/useOmni.ts     sources, travaux (annulation), réglages serveur, système, startJob
app/utils/                     api, format, types, textures, inspect (entrées du visualiseur), output, gmod
```
