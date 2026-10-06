# Omni : mode d'emploi

Omni transforme les fichiers de **ta** copie d'un jeu en contenu pour Garry's Mod (props, playermodels, textures), en modèles glTF pour Blender et en sons. Rien n'est envoyé nulle part.

## Démarrer

1. Lance Omni (installeur ou archive portable ; depuis les sources : `Omni.cmd`).
2. Page **Jeux** : choisis le dossier du jeu (ou prends celui que Steam a trouvé). Omni reconnaît le moteur et prépare tout le reste tout seul : outils, noms, index, catalogues. Attends la fin du travail dans le panneau des jobs.
3. Les pages du jeu s'ouvrent : **Modèles**, **Textures**, **Visionneuse**, **Sons**, **Réglages**.

Garry's Mod est nécessaire pour convertir des modèles (Omni le trouve dans Steam).

## Où sont mes fichiers ?

Un seul dossier, `Documents\Omni`, deux parties :

```
Omni\
  exports\
    <jeu>\
      garrysmod-addon\   l'addon Garry's Mod (relié à garrysmod\addons par Omni)
      gltf\              un .glb par modèle, pour Blender
      sounds\            les sons exportés
      textures\          les textures exportées en PNG
      blend\             les fichiers Blender (option)
      omni_<jeu>.gma     l'archive pour l'atelier
  workspace\             à ne pas toucher : réglages, catalogues, caches, outils, journaux
```

Tout ce qui t'intéresse est dans `exports`. Rien d'autre n'est écrit ailleurs (l'application elle-même ne garde aucune donnée).

**Changer d'emplacement** : Réglages, carte *Dossiers*, bouton *Déplacer…* (ou `Omni.exe home set D:\Omni`). Le déplacement se fait au prochain démarrage ; ferme Garry's Mod avant. Les exports seuls peuvent aller sur un autre disque (champ *Exports*). Évite un dossier synchronisé (OneDrive) : les exports pèsent des dizaines de Go.

**Reprendre d'anciennes données** : l'ancien dossier d'Omni est repris automatiquement. Pour un autre emplacement : `Omni.exe migrate "C:\ancien\dossier"` (rien n'est supprimé ni écrasé ; `--dry-run` montre ce qui bougerait).

## Convertir

- **Modèles** : coche des props (ou filtre puis « tout »), *Convertir*. L'addon est mis à jour et relié à Garry's Mod. *Ouvrir dans GMod* lance le jeu sur le modèle.
- **Playermodels** : onglet *Personnages*, construis une famille ou toutes.
- **glTF** : option dans les Réglages (*un .glb par modèle*) ou bouton dans l'inspecteur. Les modèles sont exportés en parallèle.
- **Textures** : parcours toutes les textures du jeu, aperçu par canal, export PNG pleine résolution.
- **Sons** : *Exporter* (ogg sans réencodage et flac, ou le format choisi). Le dossier suit les noms du jeu, avec tags et `index.csv`.

Un seul gros travail tourne à la fois, les autres attendent dans la file. Fermer la fenêtre pendant un travail laisse Omni tourner dans la zone de notification. Les échecs sont regroupés par cause avec un bouton *Réessayer*.

## En ligne de commande

`Omni.exe <commande>` (depuis les sources : `uv run --no-sync python -m omni <commande>`) :

```powershell
Omni.exe home                     # où sont mes fichiers
Omni.exe batch --limit 20         # convertir quelques props
Omni.exe sounds --format auto     # exporter les sons
Omni.exe deploy                   # relier l'addon à Garry's Mod (--remove pour délier)
Omni.exe gma                      # construire le .gma
Omni.exe native                   # état du cœur Rust
```

`OMNI_HOME=<dossier>` fait travailler Omni dans un dossier jetable : pratique pour essayer sans toucher aux vrais exports.

## En cas de souci

- Garry's Mod doit être fermé pour reconvertir ou déplacer l'addon (fichiers verrouillés).
- Un chemin trop long (plus de 260 caractères) fait échouer StudioMDL : garde le dossier Omni près de la racine d'un disque si ça arrive.
- Réglages, *Stockage et maintenance* : vider les aperçus, reconstruire les catalogues. Le journal est dans `workspace\logs`.
