# Audit du stockage des modèles — 2026-09-03

Cet audit est en lecture seule. Aucun modèle installé n'a été supprimé ou déplacé.

## Mesure actuelle

Windows et certains analyseurs de dossiers masquent `.git`. Cela explique les deux chiffres
observés pour le même dossier :

- `var/models` sans le `.git` caché de Hydra : environ **2,37 Gio** ;
- tous les fichiers, y compris les objets Git LFS cachés : **4 599 155 901 octets**, soit
  **4,28 Gio / 4 386,1 Mio**.

Le clone Hydra contient des copies Git LFS physiques distinctes des fichiers extraits. Ce ne sont
pas des liens physiques NTFS : le stockage logique et physique est donc réellement dupliqué.

| Modèle ou stockage | Taille | Fonction | Obligatoire |
|---|---:|---|---|
| `wd-vit-tagger-v3/model.onnx` | 361,00 Mio | tagging général WD14 via ONNX Runtime | oui, seulement si WD14 est activé |
| `hydra-3.5.safetensors` | 1 015,21 Mio | tagging e621/furry (8 886 tags) | oui, seulement si Hydra est activé |
| `jtp-3-hydra.safetensors` | 956,14 Mio | ancienne variante Hydra 3 | non ; BooruFlow ne la sélectionne pas |
| `.git/lfs/objects/...hydra-3.5...` | 1 015,21 Mio | copie interne Git LFS du poids Hydra 3.5 | non au runtime |
| `.git/lfs/objects/...jtp-3...` | 956,14 Mio | copie interne Git LFS de l'ancien poids | non au runtime |
| `jtp-3-hydra-val.csv` et sa copie LFS | 40,20 Mio × 2 | jeu de validation du projet Hydra | non au runtime |
| code, tags et métadonnées Hydra | environ 2,1 Mio | chargeur et catalogue de tags Hydra | oui avec Hydra, mais très léger |

## Origine et utilisation

- WD14 était déjà présent avant le dernier patch (horodatage local du poids : 31 août 2026).
  `booruflow.cli.wd14_model` sait télécharger les trois artefacts requis à la demande.
- Hydra a été ajouté le 3 septembre 2026 par le lot non validé qui introduit
  `infrastructure/hydra.py`, les arguments du worker et les réglages `image_analysis_hydra_*`.
- Le dépôt `https://huggingface.co/RedRocket/Hydra` a été cloné intégralement avec Git LFS dans
  `var/models`. Aucun clone ni téléchargement automatique de Hydra n'existe dans le code
  BooruFlow actuel : le clone a donc été effectué par l'installation/patch, pas par le démarrage
  normal de l'application.
- Le chemin configuré par BooruFlow vise uniquement `hydra-3.5.safetensors`. L'ancien
  `jtp-3-hydra.safetensors`, le jeu de validation et toutes leurs copies LFS ne sont jamais
  sélectionnés.
- WD14 et Hydra ne sont pas nécessaires simultanément pour chaque image : WD14 traite les sources
  générales ; Hydra est filtré aux éléments e621 et n'est chargé qu'à la première analyse e621.
  Ils peuvent néanmoins être activés ensemble dans un worker qui reçoit les deux types de sources.
- Les modèles expérimentaux d'embeddings (OpenCLIP et Author_ID) ne sont pas présents sous
  `var/models` dans cette mesure. OpenCLIP peut utiliser le cache Hugging Face ailleurs si un essai
  a été lancé ; il n'est pas un poids livré par ce dossier.

## Architecture retenue dans ce lot

- L'installation de base ne contient toujours aucun poids de modèle.
- Hydra possède maintenant un extra de dépendances distinct : `image-analysis-hydra`.
- Hydra est désactivé par défaut pour une nouvelle configuration. Une configuration existante garde
  sa valeur persistée ; aucun workflow existant n'est réécrit.
- La page Nettoyage/Maintenance inventorie `WD14`, `Embeddings`, `e621 / furry` et `Autres`, et
  permet d'ouvrir `var/models`.
- L'inventaire distingue explicitement les poids actifs, la variante inactive, les données de
  développement et le stockage du clone Git LFS.

La suppression depuis l'interface n'est pas activée dans ce lot. Elle doit arriver avec un
installateur Hydra capable de retélécharger uniquement la version choisie et avec une confirmation
qui nomme la fonctionnalité désactivée. Afficher un bouton destructif avant cette symétrie rendrait
la promesse de réinstallation incomplète.

## Mesures avant/après des optimisations

| Scénario | Avant | Après estimé | Gain | État |
|---|---:|---:|---:|---|
| Lot actuel, aucun fichier supprimé | 4 386,1 Mio | 4 386,1 Mio | 0 | appliqué |
| Installation Hydra propre, artefact 3.5 seul (sans `.git`, ancien poids, validation) | 4 386,1 Mio | ~1 378 Mio | ~3 008 Mio | proposé, non appliqué |
| Utilisateur WD14 uniquement | 4 386,1 Mio | ~362 Mio | ~4 024 Mio | possible après gestionnaire de téléchargement |
| Utilisateur sans analyseur IA | 4 386,1 Mio | 0 | ~4 386 Mio | architecture de base visée |

Le chiffre « après » de l'installation Hydra propre inclut WD14, Hydra 3.5 et les quelques
mégabytes de code/métadonnées nécessaires. Il ne doit pas être obtenu en nettoyant le dossier réel
sans confirmation : il décrit le contenu d'un futur téléchargement sélectif.

## Répartition globale mesurée

| Catégorie | Taille logique actuelle |
|---|---:|
| `.venv` complet | 5,30 Gio |
| modèles visibles (hors `.git` caché Hydra) | ~2,37 Gio |
| modèles réels (avec Git LFS caché) | 4,28 Gio |
| state | 520,3 Mio |
| BrowserProfiles | 195,1 Mio |
| cache | 154,4 Mio |
| results + resultsE621 | 44,4 Mio |
| `_backups` | 969,2 Mio |
| `data` | 649,9 Mio |

La taille logique réelle de `var` est donc actuellement **5,18 Gio**, et non 3,21 Gio lorsque le
répertoire `.git` interne caché est inclus.
