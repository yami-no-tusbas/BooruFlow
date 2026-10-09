# Wiki Audit — entrée universelle et liaison depuis Tag Browser

Intervention du 8 octobre 2026 dans le checkout `refactor/pyside6-mvc`.

## Architecture réutilisée

Le chargement d’un post passe toujours par `GelbooruDapiClient`, avec les mêmes
identifiants configurés. `GelbooruWikiAuditService.audit_post()` récupère ses tags
par la normalisation Gelbooru existante, puis délègue à `audit_tags()`.

Ce moteur unique conserve les recherches exactes et l’enrichissement d’alias par
lots (`exact_tags`, `enrich_tag_rows`), le cache partagé `WikiPageCache`, la lecture
et le rendu de wiki, ainsi que les distinctions entre absence, erreur de lecture
et erreur réseau. Les filtres, tris, colonnes et actions de profils d’entités sont
conservés.

## Entrées et rafraîchissement

- Une entrée uniquement numérique charge un véritable post. Les URL de posts
  déjà acceptées restent prises en charge.
- Un nom unique audite ce tag. Les virgules, LF et CRLF séparent une liste.
- Le nettoyage retire les entrées vides et les espaces périphériques ; les
  doublons sont éliminés sans réordonner la source, en conservant la première
  graphie. Les noms Unicode ne sont pas décodés une seconde fois.
- Le champ compact utilise un éditeur de texte permettant un collage multiligne
  sans la limite de longueur du précédent champ. Entrée lance le chargement ;
  Maj+Entrée insère une nouvelle ligne.
- `Load post` devient `Load` / `Charger`. Random post reste une source de véritable
  post et rétablit le champ normal.
- `Refresh all` reprend la source active en mémoire avec `force=True`. Une liste
  directe n’entraîne aucune récupération de post.

Un résultat de liste a `post_id=None` et une URL de post vide. Il n’existe ni faux
post, ni historique de listes, ni nouvelle table de sessions. Seul le cache wiki
habituel conserve les pages obtenues. Le tri de résultats existant reste actif.

## Communication entre pages

Le menu de Tag Browser conserve ses actions et utilise le helper commun
`select_context_row`. Un clic sur une ligne sélectionnée conserve la sélection
multiple ; un clic extérieur traite uniquement cette ligne. Les noms proviennent
de `RAW_NAME_ROLE`, dans l’ordre visuel courant, jamais des noms décorés.

L’action émet `wiki_audit_requested` avec la liste de noms. `MainWindow` navigue
vers la page existante et utilise `_run_when_ready` pour son initialisation
paresseuse, puis appelle `WikiAuditPage.load_tags()`. Si plusieurs demandes arrivent
avant que la page soit prête, seule la dernière est lancée.

Le champ vide affiche `From Tag Browser — X tags` comme indication de provenance.
Cette indication n’est pas une valeur saisie et ne passe jamais dans le parseur.
La liste reste séparément en mémoire. L’action ouvre toujours l’audit Gelbooru ;
les catégories et alias d’un autre site ne sont pas transmis au moteur.

## Sessions et progression

Chaque audit reçoit un numéro de génération. Une nouvelle source interrompt les
anciens workers, efface les résultats et la lecture précédents, puis ignore leurs
retours de progression, succès, erreur et Random post. Un ancien signal de fin ne
peut pas terminer le nouvel audit.

Les requêtes wiki sont soumises progressivement avec le nombre de workers existant,
sans mettre toute une grande sélection en attente dans le pool. L’annulation
arrête les soumissions restantes. Les requêtes HTTP déjà en cours terminent dans
le worker, selon leurs délais de transport. La fermeture reste différée et
asynchrone jusqu’à la fin des workers, sans destruction d’un QThread actif.

La barre de progression locale et le message local `Checking X/Y` sont supprimés.
`PageStatus` alimente uniquement la barre globale existante. À la fin, sa progression
et son message temporaire sont libérés. Le résumé permanent et les erreurs restent
visibles dans la page ; une erreur globale propre à cette page reste affichée
jusqu’à la prochaine session. Le résumé vide est masqué pendant le chargement.
Le contrôleur de barre d’état partagé n’a pas été modifié.

## Fichiers concernés

- `src/booruflow/infrastructure/wiki_audit.py` : parseur universel, source directe,
  moteur commun, annulation et soumission bornée.
- `src/booruflow/presentation/pyside6/wiki_audit_page.py` : champ, provenance,
  sources en mémoire, workers et progression.
- `src/booruflow/presentation/pyside6/tag_browser_page.py` : sélection et action.
- `src/booruflow/presentation/pyside6/main_window.py` : signal, navigation et fermeture.
- `resources/i18n/en.json`, `resources/i18n/fr.json` : libellés.
- `tests/unit/test_wiki_audit_sources.py` : 34 nouveaux cas de validation.
- `tests/unit/test_entity_profiles_ui.py` : adaptation du double de fenêtre et
  vérification du nouveau signal, en conservant le test du profil d’entité.

Les modifications déjà présentes dans le checkout sont conservées, notamment la
normalisation Gelbooru dans le service et les autres évolutions de MainWindow et
des traductions. Aucun commit, push, packaging ni ajout de dépendance.

## Validation et limites

La suite pertinente comprend Wiki Audit, Tag Browser, le shell PySide6, les
workflows, les profils d’entités et la normalisation HTML Gelbooru : **372 tests
réussis et 14 sous-tests réussis**. Elle couvre les ID et URL, Random post, CSV,
multiligne, alias, doublons, tags absents du catalogue, wikis absents, cache et
rafraîchissement, remplacement de session et fermeture pendant le chargement.
Des cas dédiés vérifient 2 500 tags au moteur, 1 500 lignes sélectionnées dans le
menu et 4 000 tags collés dans le champ.

Ruff passe sur tous les fichiers Python concernés. La compilation de `src` et
`tests`, ainsi que `git diff --check`, passent. Le contrôle Ruff global trouve
120 défauts hors périmètre : 3 dans les sources/tests inchangés (`icons.py` et
`test_gelbooru_tag_importer.py`) et 117 dans les outils existants.

Une ouverture native Windows a également été exécutée avec un projet temporaire,
128 tags et un transport simulé, sans accès réseau. Les captures et assertions
vérifient le basculement depuis Tag Browser, la provenance, l’absence de widgets
de progression locaux et le retour de la barre globale à l’état normal.
Les preuves se trouvent dans `var/tmp/wiki-audit-native-20261008/`.

Les bases et caches de validation sont temporaires ; les données actives ne sont
pas utilisées pour ces audits. La validation ne constitue pas un nouvel audit en
ligne de Gelbooru. Une requête HTTP déjà lancée reste soumise au délai du transport
existant ; l’annulation ne coupe pas sa connexion au milieu de la requête.
