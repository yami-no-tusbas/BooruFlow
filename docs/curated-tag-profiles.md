# Curated Tag Profiles — MVP

Implémentation dans le checkout `refactor/pyside6-mvc`, le 7 octobre 2026.
Les modifications déjà présentes ont été conservées. Aucun commit, push,
packaging, appel réseau Gelbooru ou migration de catalogue n'est requis.

## Architecture et stockage

Trois sources restent distinctes : WD14 conserve ses observations et scores ;
Entity Profiles conserve ses statistiques et décisions SQLite ; Curated Tag
Profiles fournit une connaissance sémantique déclarative, validée localement.

- `domain/curated_profiles.py` : profils, sous-groupes, tags locaux, index
  canonical et identifiants de représentation virtuelle.
- `application/curated_profiles.py` : découverte, validation JSON, résolution
  d'alias, validation locale, fusion et filtrage. Aucun stockage SQLite propre
  aux packs.
- `presentation/pyside6/curated_tagging.py` : état provisoire par item,
  provenance, références aux suggestions existantes et commandes de confirmation.
- `SuggestionTable` : quelques lignes virtuelles dans la table existante,
  sans nouveau composant d'arbre ni sous-groupes récursifs.
- `ImageAnalysisRepository.apply_review_observation_bundle` : une transaction
  pour les observations finales et pour chaque Undo/Redo du paquet.

Le chargement s'effectue lors de la construction de la fenêtre principale,
avant la création paresseuse de Tagging. Le contrôleur reçoit l'index préparé.
Un contrôleur autonome peut également charger son index. L'activation d'un
catalogue après le bootstrap recharge la connaissance locale. Une navigation
entre images ou groupes ne relit jamais les JSON et ne contacte pas Gelbooru.

## Emplacement et configuration

| Source | Répertoire |
| --- | --- |
| Bundled | `<resource_root>/resources/curated_tag_profiles/*.json` |
| Utilisateur | `<application_root>/config/curated_tag_profiles/*.json` |
| Externe optionnel | Répertoires de `curated_tag_profile_directories` |

Les chemins externes relatifs sont résolus depuis la racine de l'application.
Dans le portable, les ressources passent par `resource_root()` et les packs
utilisateur restent à côté de l'exécutable, dans `config`. La recette principale
PyInstaller et celle de Tagging autonome incluent désormais les packs. Aucune
recette n'a été exécutée pour produire un build.

Configuration facultative dans les paramètres JSON existants :

```json
{
  "curated_tag_profiles_min_post_count": 25,
  "curated_tag_profiles_top_count": 5,
  "curated_tag_profile_directories": ["data/my_curated_profiles"]
}
```

La modification des packs nécessite un redémarrage ; aucun éditeur ou mécanisme
de synchronisation n'a été ajouté.

## Format JSON version 1

```json
{
  "format_version": 1,
  "pack": {"id": "my-pack", "name": "My profiles"},
  "profiles": [
    {
      "trigger": "panties",
      "top_count": 5,
      "min_post_count": 25,
      "groups": [
        {
          "id": "shape",
          "label": {"en": "Shape", "fr": "Forme"},
          "tags": ["thong", "highleg_panties"]
        },
        {
          "id": "color_pattern",
          "label": {"en": "Color / Pattern", "fr": "Couleur / motif"},
          "tags": ["white_panties", "striped_panties"]
        }
      ]
    }
  ]
}
```

`format_version`, `pack.id`, `profiles`, `trigger`, `groups`, `group.id` et
`group.tags` sont requis. `pack.name` est descriptif. `label` est facultatif.
`top_count` et `min_post_count` sont des entiers positifs ou nuls, facultatifs ;
les booléens, nombres négatifs et nombres fractionnaires sont invalides.
`top_count: 0` permet un profil ne présentant que les sous-groupes.

Les noms doivent être non vides, sans espaces, avec normalisation conservative
existante. `group:` et `curated:` sont réservés aux lignes virtuelles.
Un sous-groupe contenant `groups` est rejeté : la profondeur maximale est
racine → profil → sous-groupe → vrais tags.

Les fichiers sont en UTF-8, BOM accepté. Une version inconnue ou un fichier
illisible/invalide est signalé et ignoré. Les profils, groupes et tags invalides
n'empêchent pas les frères valides de charger. Les avertissements identifient
le fichier/profil ou le tag problématique.

Le pack fourni, `resources/curated_tag_profiles/core.json`, contient `panties`,
`censored` et `gyaru`. Les propositions de ce dernier sont des éléments de
contexte à vérifier ; aucune relation de sous-type n'est supposée.

## Validation locale, filtres et Top

Le loader utilise exclusivement les chemins configurés des catalogues Gelbooru.
Les connexions utilisent `mode=ro`. Il ne crée pas de base manquante, ne lance
pas l'importeur et n'ajoute aucun tag. Les aliases actifs sont lus en une requête,
les chaînes sont résolues en mémoire, et les cycles/profondeurs dangereuses
sont ignorés avec avertissement.

Les vrais tags et triggers canonical sont recherchés par lots via `exact_tags`.
Les tags inconnus ou dépréciés sont ignorés. Catégorie et `post_count` proviennent
de cette base. Une base indisponible désactive les profils proprement.

Les tags runtime sont l'union canonical dédupliquée des groupes. Le seuil est
inclusif : `post_count >= min_post_count`, par défaut 25. Les tags sous le seuil
restent dans le JSON. Les groupes sans candidat disparaissent ; un profil sans
candidat n'est pas indexé.

Les Top sont automatiquement triés par `post_count` décroissant, puis nom
canonical croissant pour départager les égalités, et limités à `top_count`, par
défaut 5. Ils restent également présents dans leurs sous-groupes. Les candidats
devenus source ou acceptés sont masqués dynamiquement ; une entrée Top masquée
ne provoque pas un recalcul de popularité en base pendant la review.

## Fusion de plusieurs packs

L'ordre est : bundled, utilisateur, répertoires externes dans l'ordre configuré.
Dans chaque répertoire, les fichiers sont triés par nom. Les mêmes chemins
configurés plusieurs fois sont dédupliqués.

Pour un trigger canonical commun :

1. les groupes de même `id` sont fusionnés ; les tags sont réunis par canonical ;
2. les labels par locale du dernier profil valide remplacent les précédents,
   tout en conservant les locales absentes du nouveau profil ;
3. les paramètres du dernier profil valide (`top_count`, `min_post_count`, ou
   leurs valeurs par défaut) prévalent sur ceux des profils précédents ;
4. le filtre final s'applique à l'union fusionnée ;
5. les provenances des packs sont conservées sur le profil et sur chaque tag.

Les sous-groupes gardent l'ordre de leur première apparition ; les tags sont
triés de façon déterministe. Il n'y a ni dépendances entre packs ni moteur
d'override de taxonomie.

## Workflow Tagging

Un trigger apparaît uniquement comme observation WD14 éligible encore non
revue. Les tags déjà source ou définitivement acceptés/rejetés ne rouvrent pas
leur profil. La racine reste un vrai tag ; `group:<trigger>` n'entre jamais
dans les observations, les seeds, les tags finaux ou la publication.

Droite ouvre le groupe sélectionné, Gauche le ferme. Sur un enfant, Gauche
revient au parent ; Haut/Bas garde la navigation Qt habituelle. Le clic et
Espace permettent de choisir un raffinement ; A le sélectionne provisoirement,
R le désélectionne. Les champs texte gardent leurs touches et raccourcis.

Un enfant non sélectionné est marqué `○`, un pending refinement `●` et en gras.
Le tooltip donne son état provisoire, sa provenance et son nombre de posts.
Les deux représentations Top/sous-groupe partagent le même état par canonical.
Un pending reste visible comme choix sélectionné et désélectionnable, sans
constituer une seconde proposition indépendante.
La colonne de tag conserve une largeur lisible, même si la première présentation
de la table avait eu lieu avant l'arrivée des suggestions.

Les pending refinements vivent seulement en mémoire, par item et trigger. Ils
ne créent aucune observation ni seed, ne déclenchent aucun profil dérivé et
n'entrent pas dans les tags finaux. Un nouveau site ou une réanalyse réinitialise
l'état concerné. Une navigation entre deux items distincts garde leur isolation.

Accepter la racine confirme en une transaction la racine et ses raffinements.
Une suggestion WD14 existante est réutilisée, son score reste intact ; une
suggestion Entity est représentée par le même nom logique et confirmée par une
observation manuelle normale. Seuls les tags sans observation existante créent
une nouvelle observation manuelle, sans confidence artificielle. L'ajout manuel
d'une racine WD14 déjà proposée passe également par cette confirmation.

Rejeter la racine abandonne les raffinements sans en créer d'observation. Dans
les deux cas, le groupe disparaît pour cet item. La commande de paquet stocke
les décisions et noms revus antérieurs ainsi que l'état pending. Undo restaure
les observations antérieures et l'accès au groupe ; Redo réapplique le paquet.
Les observations créées par le paquet deviennent rejetées lors d'Undo, comme
les ajouts manuels existants, et sont réutilisées par Redo. Un échec de transaction
ne laisse aucune confirmation partielle.

Les confirmations finales deviennent ensuite des seeds du workflow Entity
Profiles existant. Les suggestions Entity seules et les pending refinements ne
deviennent jamais des seeds. Aucun pack ni décision Curated n'est écrit dans
`entity_profiles.sqlite`.

Les noms canonical partagent une identité logique, même si WD14, Entity et
Curated les proposent ensemble. Les tags source conservent leurs noms et
identifiants persistés ; leur canonical sert uniquement à la comparaison et à
la déduplication de présentation. La provenance Curated apparaît dans le match
et le tooltip, avec la racine et les packs ; WD14 et ses scores restent visibles.
Après confirmation, la navigation du groupe disparaît mais la provenance Curated
reste visible sur les tags concernés tant que le contexte racine est accepté.

## I18N

Les labels des packs utilisent : locale courante (base `fr` pour `fr_BE`), puis
anglais, puis `group.id`. Aucune clé globale n'est générée pour ces labels.
Un changement de langue sur une review en cours met également à jour les labels
et tooltips Curated en conservant l'expansion, les pending refinements et le
focus d'un champ texte. Les quatre nouvelles clés EN/FR sont `curated.pending`, `curated.candidate`,
`curated.posts` et `curated.group_tip`.

## Fichiers de cette implémentation

Créés :

- `src/booruflow/domain/curated_profiles.py`
- `src/booruflow/application/curated_profiles.py`
- `src/booruflow/presentation/pyside6/curated_tagging.py`
- `resources/curated_tag_profiles/core.json`
- `tests/unit/test_curated_profiles.py`
- `tests/unit/test_curated_tagging_ui.py`
- `docs/curated-tag-profiles.md`

Modifiés :

- `src/booruflow/application/tagging.py` : reconnaissance du token Curated.
- `src/booruflow/infrastructure/image_analysis_repository.py` : transaction de paquet.
- `src/booruflow/presentation/pyside6/main_window.py` : chargement/wiring et activation des catalogues.
- `src/booruflow/presentation/pyside6/tagging_controller.py` : état Curated et wiring review/Undo/Redo.
- `src/booruflow/presentation/pyside6/tagging_page.py` : Espace local, état d'action, largeur lisible.
- `src/booruflow/presentation/pyside6/tagging_suggestions.py` : présentation et clavier.
- `resources/i18n/en.json`, `resources/i18n/fr.json` : quatre chaînes génériques.
- `tools/booruflow.spec`, `tools/build_tagging_standalone.ps1` : déclaration des ressources.

Les modifications préexistantes du moteur Entity Profiles, de la normalisation
HTML, des importeurs, des transports et de leurs tests ont été préservées. Aucun
changement du moteur WD14 ni du moteur statistique Entity Profiles n'a été
nécessaire.

## Validation

| Contrôle final | Résultat |
| --- | --- |
| Nouveaux tests, Qt Windows natif, captures activées | **27 passed**, 18,12 s |
| Tests nouveaux + tests unitaires Tagging | **49 passed**, 12,07 s |
| Première couverture Qt/workflows ciblée élargie | **236 passed, 12 subtests passed** |
| Suite complète sur l'implémentation finale | **1 422 passed, 2 skipped, 14 subtests passed**, 196,28 s |
| Ruff ciblé sur les fichiers Python créés/modifiés | **Réussi** |
| Ruff de la recette PyInstaller | **Réussi**, hors F821 pour les globals injectés par PyInstaller |
| compileall sur `src` et les deux nouveaux fichiers de tests | **Réussi** |
| `git diff --check` | **Réussi** |
| SHA-256 des quatre bases/cache actifs protégés | **Tous inchangés** |

La suite complète a été exécutée avec Qt offscreen et les protections SQLite
existantes. Le contrôle natif séparé a imposé Qt `windows` et des fenêtres exposées.
Des régressions de doubles de test et une troncature réelle de la colonne ont
été détectées, corrigées puis revérifiées. Les warnings de conversion CRLF/LF
de Git ne constituent pas des erreurs du contrôle de diff.
Les deux tests ignorés concernent les vrais modèles d'embedding, activables
explicitement par `BOORUFLOW_RUN_MODEL_TESTS=1` ; ils n'ont pas été lancés pour ce MVP.

27 nouveaux cas paramétrés couvrent le loader et le workflow Qt. Le contrôle
des bases actives utilise une empreinte SHA-256 prise avant les modifications
et comparée après les tests. Les quatre fichiers protégés sont
`var/state/image_analysis.sqlite`, `var/cache/image_analysis_cache.db`,
`var/state/entity_profiles.sqlite` et `var/cache/post_metadata.sqlite`.

La couverture ajoutée vérifie la lecture de fichiers valides/invalides,
versions, erreurs par frère, alias/cycles, immuabilité des catalogues, seuils,
tri et Top, fusion/provenance/fallback, groupes vides/non récursifs, clavier et
champs texte, sélection partagée, absence de seeds provisoires, coexistence
Entity/WD14/Curated, bundles avec zéro/deux enfants, rejet, Undo/Redo, rollback,
scores WD14, aliases runtime, acceptation manuelle et isolation entre items.

Les captures de l'inspection native sont dans `var/tmp/curated-native`. Les
tests de capture exigent explicitement la plateforme Qt `windows` et une fenêtre
exposée. Ils utilisent un petit pack synthétique contenant panties, censored et
gyaru, des catalogues temporaires, des images colorées locales et un backend
WD14 synthétique. Aucun compte Gelbooru ni modèle téléchargé n'est utilisé.

## Limites volontaires

- Pas de persistence des sélections provisoires entre redémarrages.
- Pas de hot reload des JSON, d'éditeur, téléchargement ou génération de packs.
- Pas d'arbre récursif, d'apprentissage statistique ni score fusionné.
- Les Top masqués par l'état de review ne sont pas remplis par des tags moins populaires.
- Une sélection multiple mélangeant des racines Curated et des entrées ordinaires
  peut utiliser plusieurs commandes existantes ; chaque paquet racine/raffinements
  reste une commande atomique et ne peut être partiellement annulé.
- Les builds CPU/CUDA ne sont pas produits ; seules leurs déclarations de ressources
  ont changé. Le comportement frozen/portable n'est pas prétendu exécuté.
- L'inspection native valide la présentation et le workflow sur des données
  synthétiques, pas un échange réel avec Gelbooru ni une inférence WD14 réelle.
