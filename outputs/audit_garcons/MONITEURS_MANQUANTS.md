# Moniteurs manquants pour la galerie Garçons

Audit en lecture seule du fichier `D:\0ZGrabber_monitor\monitors.json` (350 moniteurs).

## Déjà couverts

| Requête | Sites actuels | Destination neutre actuelle |
|---|---|---|
| `condom_belt` | Gelbooru + e621 | `Sexual themes/condom_belt` |
| `prostitution` | Gelbooru + e621 | `Professions/prostitution` |
| `public_use` | Gelbooru + e621 | `Sexual themes/Exposure and public/public_use` |
| `pregnant` | Gelbooru + e621 | `pregnant` |
| `bestiality` | Gelbooru + e621 | `bestiality` |

## Ajouts proposés

Les destinations restent neutres et reprennent la profondeur de la galerie `Tags`.
Les indicateurs de composition (`1boy`, `1girl`, etc.) restent produits par l'expression de nommage commune.

| Requête | Sites proposés | Destination | Justification locale |
|---|---|---|---|
| `femdom` | Gelbooru | `Sexual themes/femdom` | 45 404 posts Gelbooru ; tag e621 à zéro |
| `chastity_cage` | Gelbooru + e621 | `Sexual themes/chastity_cage` | 2 940 / 30 264 posts |
| `flat_chastity_cage` | Gelbooru + e621 | `Sexual themes/flat_chastity_cage` | 967 / 2 679 posts |
| `erection_under_clothes` | Gelbooru | `Sexual themes/erection_under_clothes` | 22 642 posts Gelbooru ; tag e621 à zéro |
| `yaoi` | Gelbooru | `Sexual themes/yaoi` | 178 248 posts Gelbooru ; tag e621 à zéro |
| `crossdressing` | Gelbooru + e621 | `crossdressing` | 72 091 / 45 270 posts |
| `trap` | Gelbooru | `trap` | 97 800 posts Gelbooru ; tag e621 à zéro |
| `androgynous` | Gelbooru + e621 | `androgynous` | 70 164 / 55 posts |
| `gynomorph` | e621 | `gynomorph` | actif sur e621 ; catégorie obsolète 6 sur Gelbooru |
| `interracial` | Gelbooru | `interracial & interspecies` | 58 066 posts Gelbooru ; tag e621 à zéro |
| `interspecies` | Gelbooru + e621 | `interracial & interspecies` | 43 073 / 390 508 posts |

## Requêtes volontairement non ajoutées

- `femdom futanari` : sous-ensemble de `femdom`, donc risque de doublons entre moniteurs.
- `interracial & interspecies` : nom de regroupement local, pas une requête valide unique.
- Aucun filtre `1boy`, `1girl`, hétéro ou homo n'est ajouté aux moniteurs. Le tri des images reste manuel.

## État

- Proposition seulement : aucun moniteur ajouté.
- `monitors.json` inchangé.
- Une sauvegarde datée et une validation JSON seront obligatoires avant toute application.
