# Passe UI/UX PySide6 — 5 octobre 2026

Checkout : `refactor/pyside6-mvc`. La référence de comparaison est le contenu présent au début de cette passe, déjà modifié localement, et non HEAD. Les changements antérieurs ont été conservés. Aucun commit, push, packaging ou ajout de dépendance.

## 1. Surfaces auditées

Tagging moderne et legacy, Tag Browser, Entity Profiles et grille de témoins, Wiki Editor, Wiki Audit, Image Finder, autocomplete, Tagging standalone, DataTable, SuggestionTable, miniatures et PageStatus. Audit complémentaire des menus/doubles-clics de Wiki Browser/Organization, Similar Artists, Folder Artists et Image Analysis. Les contrôleurs, chargeurs et traitements ont été lus pour identifier leurs signaux existants.

Inspection native Windows des six surfaces principales en EN/FR et clair/sombre, avec sélection active/inactive, menus tags avec/sans canonical, menus post et autocomplete, et miniatures en cours de chargement. Les autres surfaces ont fait l’objet d’un audit du code et de vérifications ciblées ; aucune validation externe de compte ou de galerie réelle.

## 2. Bugs effectivement reproduits

- Filtres actifs : zone vide cachée, puis croissance verticale à l’ajout des chips. À 1200 × 850, la zone remplie montait à 120 px et le début du panneau Bulk passait de Y=204 à Y=330.
- Entity Profiles, style natif `windows11` : texte noir sur fond de sélection sombre en thème clair, même avec une palette HighlightedText correcte.
- Recoloration à chaud : certaines brosses de cellules et certains fonds QSS conservaient la palette précédente. La vérification compare désormais aussi le rendu chaud à celui d’un tableau créé sous la palette courante.
- Actions communes absentes ou différentes entre suggestions, chips, autocomplete, grille de posts et liens ; doubles-clics non découvrables au clic droit.
- Wiki Audit : chargement sans total initial avec barre déterminée vide ; Image Finder et grille de témoins sans compteur compact utilisant le signal réel des miniatures.

Les régressions introduites pendant le développement ont été reproduites puis corrigées : création anticipée des popups entraînant un arrêt de Qt à la destruction collective, observateurs globaux de popup ralentissant les tests et feuille de style globale retenant une ancienne palette. Le branchement final se fait uniquement dans le helper d’affichage de l’autocomplete, avec un récepteur QObject possédé par le completer. Le style partagé reste local aux vues.

## 3. Bugs historiques déjà corrigés, laissés intacts

Le tri par nom est distinct du tri par ID dans le checkout de départ. La catégorie et le canonical de l’autocomplete sont déjà portés par des rôles structurés. Les couleurs des suggestions, le cache de miniatures, les grilles réutilisées et les règles de navigation Entity Profiles disposent déjà de corrections validées. Aucune nouvelle résolution d’alias, correction de comparateur ou refonte de ces mécanismes.

## 4. Menus contextuels harmonisés

Tags : Tag Browser, suggestions Tagging, chips de requête/Bulk/filtres, saisie manuelle et popups, Entity Profiles, Wiki, Wiki Audit, Organization et Similar Artists. Les actions propres à chaque panneau restent présentes : Keep/Ignore/Remove, inspection, exclusion globale, lecture Wiki, analyse d’artiste et navigation locale.

Posts : résultats Tagging, témoins Entity Profiles, exemples Organization et provenances de Similar Artists. Image Finder offre les actions d’un lien d’œuvre et conserve le téléchargement. Liens : aperçus Wiki/Wiki Audit, détails Image Finder/Similar Artists et définition Organization. Folder Artists et Image Analysis exposent explicitement leur ouverture locale existante.

## 5. Actions communes

Tag : ouvrir sur le site, ouvrir le canonical seulement s’il est distinct et connu, copier le tag brut, ouvrir/créer le profil d’entité lorsque Gelbooru et le contexte le permettent.

Post : ouvrir, copier l’ID, copier l’URL ; ouvrir/copier la source seulement lorsqu’elle existe. Lien : ouvrir et copier l’URL. Aucun téléchargement ou traitement n’est déclenché par une simple copie.

Le routeur reconnaît les hôtes exacts `gelbooru.com` et `www.gelbooru.com` et utilise le BrowserLauncher configuré. e621, Pixiv, fichiers et autres destinations utilisent leur ouverture système. Les liens internes Wiki conservent leur callback. Le navigateur interne de publication Wiki conserve son contrat.

## 6. Helpers

`context_actions.py` contient des builders tag/post/link composables, le routeur et de petits branchements pour cartes, liens et autocomplete. Les actions spécifiques sont ajoutées par les pages. Les callbacks sont exécutés après retour du menu.

`theme.py` centralise les rôles de sélection et le rafraîchissement des widgets existants. `thumbnail_feedback.py` adapte le signal de progression existant à un affichage compact, avec regroupement des mises à jour intermédiaires sur 100 ms et affichage immédiat des changements de vague/fin.

## 7. Contreparties explicites des doubles-clics

Ouverture du tag/post dans le navigateur, recherche d’un tag dans Tagging, navigation vers un tag récurrent, ouverture de la référence Similar Artists, ouverture locale dans Image Analysis et ouverture Everything dans Folder Artists. Les doubles-clics existants sont conservés. Les lignes de groupe de suggestions ne sont pas traitées comme des tags.

## 8. Active Filters

Une ligne réservée, toujours visible, libellée EN/FR. Les chips défilent horizontalement et n’enveloppent plus sur plusieurs lignes. La mesure native est identique vide/remplie : **54 px, début Bulk Y=264**. Le test couvre 35 chips, suppression/clear, absence de décalage et retour du défilement à zéro. Seul cet éditeur utilise le mode monoligne ; les autres usages conservent leur présentation.

## 9. Tri sort-name

Non reproduit. Tests de colonnes nom/ID et règles de tri existantes conservés. Aucun changement aux comparateurs ni au tri initial par fréquence d’Entity Profiles.

## 10. Palette et sélection

Styles communs utilisant `Highlight`/`HighlightedText`, avec rôles de fond/texte de la vue. Les brosses sémantiques ordinaires restent disponibles hors sélection. DataTable, suggestions, autocomplete et listes concernées partagent le même comportement. Aucun remplacement par des RGB fixes.

## 11. Clair/sombre

Rendu natif inspecté pour les deux thèmes, avec/sans focus et EN/FR. Tests de pixels du texte sélectionné utilisant HighlightedText, avec tolérance pour l’anticrénelage et le dégradé de focus Fusion/offscreen, et comparaison du fond d’une vue rafraîchie à une vue neuve sous la même palette. Cette dernière évite une assertion incorrecte sur l’AlternateBase translucide du style Windows 11.

## 12. Changement à chaud

Rafraîchissement des styles existants avant les assets/brosses dérivés de la palette. Entity Profiles et standalone suivent les changements de palette. Les modèles, cartes, résultats, sélection, filtres, ordre de tri et widgets restent en place. Les tests attendent la propagation différée de Qt avant d’inspecter le rendu. Aucun fetch, inference, changement de sélection ou invalidation de cache ajouté.

## 13. Miniatures

Image Finder utilise son PageStatus existant ; la grille de témoins affiche un petit compteur EN/FR ; standalone expose son compteur de footer. Progression issue du chargeur existant : réussites, total et échecs. Les tests couvrent début, intermédiaire, extension de la même vague et fin.

Inspection native avec PNG synthétiques servis uniquement sur loopback : témoins à 1/6 puis 6/6. La limite de 100, les preview_url, le chargement à la demande, les choix du cache et les workers restent inchangés.

## 14. Traitements longs

Wiki Audit affiche une activité indéterminée tant qu’il ne connaît pas le total, puis les comptes réels ; son PageStatus est branché au footer global et conserve les erreurs. Image Finder expose une activité indéterminée durant l’opération. Les signaux métier, annulations et compteurs existants sont réutilisés. Pas de pourcentage inventé, de timer simulant le traitement ou de modification des pipelines.

## 15. Fichiers de cette passe

Sous `src/booruflow/presentation/pyside6/` :

`context_actions.py` (nouveau), `entity_profiles.py`, `folder_artists_page.py`, `image_analysis_page.py`, `image_finder_page.py`, `main_window.py`, `organization_page.py`, `similar_artists_page.py`, `tag_browser_page.py`, `tag_presentation.py`, `tagging_legacy_page.py`, `tagging_page.py`, `tagging_standalone.py`, `tagging_suggestions.py`, `theme.py`, `thumbnail_feedback.py` (nouveau), `ui_components.py`, `wiki_audit_page.py`, `wiki_page.py`.

Autres : `resources/i18n/en.json`, `resources/i18n/fr.json`, les cinq tests ci-dessous, `tools/ui_ux_native_probe.py` et ce rapport. La comparaison au snapshot initial exclut les modifications préexistantes visibles dans le diff Git global.

## 16. i18n

Dix nouvelles clés dans chaque langue : `context.open`, `context.copy_url`, `context.open_post`, `context.copy_post_id`, `context.copy_post_url`, `context.open_source`, `context.copy_source_url`, `context.remove_chip`, `context.search_tag`, `tagging.active_filters`.

Libellé existant `entity.analyze_menu` rendu explicite pour l’ouverture/création du profil. Catalogues vérifiés : 1361 clés chacun, mêmes clés, pas de doublon.

## 17. Tests

Nouveau `test_ui_harmonization.py` : actions/canonical/copie brute, routage exact, menus Qt et doubles-clics, carte réutilisée, popup paresseux et alias, filtres monoligne, sélection peinte, hot theme/state, Wiki Audit busy/menu, Image Finder, Entity menus/raccourcis, progression et fin des miniatures. Paramétrage EN/FR lorsque pertinent. Nettoyage des fenêtres créées par ces tests.

Adaptations : `test_entity_profiles_ui.py`, `test_pyside6_workflows.py`, `test_tag_browser_ui.py`, `test_tagging_presentation_regressions.py` suivent le helper partagé, conservent les comportements spécifiques et vérifient la route système e621. Les tests Entity A/R/Space/B, relations, grille, tri et BrowserLauncher restent exécutés.

## 18. Validation

- Suite complète offscreen : **1350 passed, 2 skipped, 14 subtests passed**, 96,95 s. Rapport JUnit hors dépôt : `full-suite-final.xml`.
- Tests ciblés avec le plugin Qt **windows** : **103 passed, 2 subtests passed**, 14,70 s. Ils couvrent cette passe, Entity Profiles, routage backend/navigateur, thème, Tag Browser et régressions Tagging.
- Nouveau module d’harmonisation : **44 passed** sous offscreen ; également inclus dans le run Windows.
- Sonde visuelle : plugin **windows**, style **windows11**, six surfaces en clair/sombre et EN/FR. Menus post et autocomplete inspectés ; témoins capturés à 1/6 et 6/6. Géométrie des filtres vide/remplie : 54 px, Y=264.
- Ruff ciblé sur tous les fichiers Python de cette passe : **pass**. L’avertissement de tri d’imports préexistant dans `icons.py`, inchangé par rapport au snapshot initial, reste hors périmètre.
- `compileall` sur `src`, `tests` et la sonde : **pass**.
- `git diff --check` : **pass** ; les avertissements de normalisation LF/CRLF du checkout n’entraînent aucune erreur de whitespace.
- JSON EN/FR : **1361 clés chacun**, parité exacte, absence de doublons.

Les captures temporaires et le diff limité à cette passe sont hors dépôt : `C:\Users\Yami\.codex\visualizations\2026\10\05\01a10c19-bd3b-73d1-9bc0-8eb893049acd`. `task-files.json` recense 28 fichiers de cette passe, rapport et sonde compris. `task-changes.patch` compare sources, ressources et tests au snapshot initial, et non aux modifications antérieures du checkout.

Quelques preuves visuelles : [sélection claire sans focus](C:/Users/Yami/.codex/visualizations/2026/10/05/01a10c19-bd3b-73d1-9bc0-8eb893049acd/final/entity-en-Light-inactive.png), [autocomplete FR](C:/Users/Yami/.codex/visualizations/2026/10/05/01a10c19-bd3b-73d1-9bc0-8eb893049acd/final/autocomplete-menu-fr.png), [menu post FR](C:/Users/Yami/.codex/visualizations/2026/10/05/01a10c19-bd3b-73d1-9bc0-8eb893049acd/final/post-menu-fr.png), [miniatures en cours](C:/Users/Yami/.codex/visualizations/2026/10/05/01a10c19-bd3b-73d1-9bc0-8eb893049acd/final/witnesses-fr-loading.png), [miniatures terminées](C:/Users/Yami/.codex/visualizations/2026/10/05/01a10c19-bd3b-73d1-9bc0-8eb893049acd/final/witnesses-fr-loaded.png).

## 19. Limites et future passe

Certaines descriptions/détails historiques utilisent encore des mots fixes ou des tokens de catégorie non traduits, par exemple `unknown`. Les boutons de traitement, les grilles et actions propres aux écrans ne sont pas uniformisés au prix d’une refonte. Les objets internes Image Analysis et les fichiers Everything conservent leur action locale, sans prétendre être des posts Gelbooru.

Les documents/checkouts réels, comptes externes, configuration de navigateur personnelle et galeries actives n’ont pas été manipulés. System/Dedicated/Custom sont préservés par passage du BrowserLauncher configuré et ses tests existants ; aucune fenêtre authentifiée n’a été ouverte pour cette validation. Les comportements réseau/cache/WD14/publication, bases actives et pipelines ne sont pas modifiés.
