# Gelbooru Wiki : export à partir des catalogues BooruFlow

L'exporteur utilise désormais les **catalogues normaux configurés dans BooruFlow**. Il actualise uniquement ceux dont la dernière réussite est inconnue, interrompue ou vieille d'au moins 24 heures, puis fusionne des copies SQLite cohérentes. Deux catalogues récents permettent un export entièrement local, sans identifiants ni requête réseau.

## Utilisation

Depuis `D:\python\BooruFlow` :

```powershell
python tools/export_wiki_db.py
```

Options :

```powershell
python tools/export_wiki_db.py --force-update
python tools/export_wiki_db.py --output "D:\Exports\gelbooru_wiki_tags.db"
```

Le fichier final par défaut reste :

**`D:\python\BooruFlow\var\exports\gelbooru_wiki_tags.db`**.

`--force-update` ignore la limite de 24 heures pour les deux sources. Il conserve les mécanismes normaux d'actualisation ; il ne transforme pas l'ajout des nouveaux tags en reconstruction complète ou en rafraîchissement de tous les compteurs historiques.

La commande affiche séparément l'état et le volume de chaque source, la nécessité d'une mise à jour, la progression des synchronisations, les copies validées, le début/fin de la fusion, les volumes exportés et le chemin final. Une opération complète longue est annoncée avant son lancement.

Python 3.12 ou plus récent suffit. La commande utilise la bibliothèque standard et le code du dépôt, sans dépendance supplémentaire ni démarrage de Qt.

## 1. Analyse des sources et mécanismes réutilisés

Les chemins sont lus dans `config/booruflow_settings.json` avec les fonctions existantes de `booruflow.application.database_paths` :

- Tags : `gelbooru_tag_database`, avec compatibilité de lecture de l'ancienne clé `gelbooru_database`.
- Alias : `gelbooru_alias_database`. Si cette clé manque, la règle de migration existante fournit le chemin normal `data/databases/gelbooru_aliases.db`, uniquement en mémoire.
- Les chemins relatifs et le préfixe portable `@app/` sont résolus depuis la racine du projet. Aucun réglage n'est enregistré ou migré sur disque. Si aucun catalogue Tags n'est configuré, la commande demande de le configurer au lieu de choisir un fichier par scan du disque.

Audit en lecture seule du checkout le **9 octobre 2026** :

| Source | Chemin configuré | Métadonnée de réussite disponible |
|---|---|---|
| Tags | `D:\python\BooruFlow\data\databases\g_tags_260810.db` | `completed_at = 2026-08-10T18:17:22+02:00`, `completed = 1`, `after_id = 1957563` |
| Alias | `D:\python\BooruFlow\data\databases\gelbooru_aliases.db` | `last_full_sync = 2026-09-02T20:00:39+02:00`, `last_incremental_sync = 2026-09-21T10:37:53+02:00` |

Ce sont des valeurs constatées, pas des constantes ajoutées au programme. Les anciens appels de mise à jour incrémentale des tags n'enregistraient pas leur date de réussite : la vieille date de reconstruction ne prouve donc pas qu'aucun nouvel ID n'a été ajouté depuis. Elle conduit néanmoins à une actualisation conservatrice au prochain lancement réel.

Les composants réutilisés sont ceux invoqués par l'application et ses commandes de mise à jour :

- `gelbooru_tag_importer.update_database` : mise à jour de la base finale depuis son checkpoint `after_id`, avec transactions et checkpoints par page.
- `gelbooru_tag_importer.rebuild_database` : initialisation complète d'un catalogue absent ; base de reconstruction intermédiaire, validations et activation existantes.
- `GelbooruAliasSynchronizer.incremental`, `initial_import` et `full_reconciliation` : synchronisation des alias avec checkpoints, recouvrement des pages et gestion des changements de pagination.
- `fetch_page`, `fetch_maximum_id`, `fetch_alias_html`, `AliasPageParser`, `pager_geometry` et les reprises réseau existantes. Aucun second téléchargement/importateur n'a été créé.
- `catalog_operation_lock` : les mêmes verrous de catalogue que les producteurs normaux ; un propriétaire actif provoque un refus explicite, sans lancement d'une seconde mise à jour.
- `JsonSettingsRepository.load()` : lecture des réglages et des identifiants sans écriture.

Les schémas de production restent `tags(id, name, post_count, category, ambiguous)` avec `import_state`, et `gelbooru_aliases(...)` avec `alias_sync_state`. Les nouvelles informations de réussite sont des clés dans ces tables existantes, pas un second registre externe de fraîcheur.

## 2. Incrémental et reconstruction complète

### Tags

Pour une base compatible existante, la commande appelle **`update_database`**, comme la mise à jour normale de BooruFlow. Elle récupère les ID au-delà du checkpoint. Elle conserve les transactions par page, la gestion des collisions et la reprise après interruption.

**Cette opération ne relit pas l'ensemble des anciens tags. Elle ne garantit donc pas l'actualité de tous les compteurs, catégories, renommages ou suppressions historiques.** La date de réussite indique la réussite de cette opération incrémentale. Le mode et la date de reconstruction complète restent distincts dans les métadonnées.

Si le catalogue est absent, l'importateur existant effectue une initialisation complète au vrai chemin configuré, ce qui peut demander plusieurs heures. L'opération est annoncée avant de commencer.

Si une base existante ne possède pas le schéma/version/checkpoint requis, la commande échoue avec une indication explicite de reconstruction complète nécessaire via BooruFlow. Elle ne remplace pas silencieusement ce catalogue par une reconstruction longue. Une reconstruction complète manuelle reste la méthode normale pour rafraîchir tout l'historique.

### Alias

Une base existante est actualisée par `incremental`. Une base absente est initialisée avec `initial_import`. Si le synchroniseur indique qu'un checkpoint manque, qu'aucun recouvrement n'est trouvé ou que la pagination est incohérente, la commande annonce puis appelle `full_reconciliation`, déjà fourni par BooruFlow.

Le mode incrémental suit le checkpoint existant ; il ne constitue pas une réconciliation exhaustive de toutes les anciennes relations. La validation des seuls alias pending reste une opération distincte de l'application et ne renouvelle plus artificiellement la date de réussite globale du catalogue.

## 3. Vérification indépendante des 24 heures

La comparaison utilise des dates ISO avec fuseau, converties en UTC :

```text
récent = réussite valide ET catalogue non vide ET opération terminée
         ET 0 <= maintenant - dernière_réussite < 24 heures
```

À exactement 24 heures, une synchronisation est nécessaire. Une date absente, invalide, sans fuseau ou située dans le futur est traitée de façon conservatrice. Le `mtime` du fichier, une modification locale ou la date de capture d'un checkpoint ne servent jamais de preuve de réussite.

Pour les tags :

- `import_state.last_successful_update_at` et `last_successful_update_mode` sont enregistrés par les producteurs existants après réussite ; modes `incremental_new_ids` ou `full_rebuild`.
- Pour les bases anciennes, repli sur `completed_at`, seulement avec `completed = 1`.
- `update_in_progress = 1` et l'absence de `completed = 1` empêchent de considérer une mise à jour échouée ou interrompue comme récente. Le marqueur est écrit avant le téléchargement incrémental ; il est effacé uniquement après réussite et contrôle d'intégrité.
- `completed_at` conserve son sens de date de reconstruction complète ; une mise à jour incrémentale ne la remplace pas.

Pour les alias :

- `alias_sync_state.last_successful_sync_at` et `last_successful_sync_mode` sont enregistrés à la fin des opérations globales réussies.
- Pour les bases anciennes, repli sur la plus récente des dates valides `last_full_sync` / `last_incremental_sync`.
- `sync_in_progress` ou `initial_in_progress` empêchent de sauter une opération interrompue. Les dates de réussite précédentes sont conservées en cas d'échec.
- `last_pending_sync` est séparée et ne prouve pas une actualisation globale.

Ces métadonnées sont maintenant également écrites par les mêmes producteurs lorsqu'ils sont appelés depuis BooruFlow. Les réponses API invalides et les réponses HTML globales vides non reconnues ne sont pas marquées comme des réussites. Le décodage UTF-8 des alias échoue sur des octets invalides au lieu de fabriquer des caractères de remplacement.

| État des deux catalogues | Action avant copie |
|---|---|
| Deux bases récentes | Aucune synchronisation, aucun réseau |
| Une seule base périmée ou date inconnue | Actualiser uniquement cette base |
| Deux bases périmées | Actualiser les deux, séquentiellement |
| Une base absente | Initialiser cette base ; traiter l'autre selon sa fraîcheur |
| Deux bases absentes | Initialiser les deux à leurs chemins normaux |
| `--force-update` | Exécuter les mécanismes normaux sur les deux sources |

Le contrôle des 24 heures ne supprime jamais la génération de l'export : même avec deux sources récentes, un nouvel export est construit et validé à chaque commande.

## 4. Copies SQLite et isolation de la fusion

Après les actualisations nécessaires :

1. Créer un dossier `.gelbooru-wiki-*` unique à côté du fichier final.
2. Prendre les verrous normaux des deux sources dans un ordre stable, puis vérifier à nouveau leurs métadonnées de réussite. Aucun verrou de source n'est imbriqué dans une fonction de synchronisation qui prend déjà le sien.
3. Ouvrir les sources en lecture seule et utiliser **`sqlite3.Connection.backup()`** vers `tags.db` et `aliases.db` dans le dossier temporaire. Cette méthode inclut les transactions validées encore présentes dans les WAL ; aucune simple copie des fichiers ou de leurs sidecars n'est utilisée.
4. Exécuter `PRAGMA integrity_check` sur chaque copie et relire ses propres métadonnées de réussite.
5. Relâcher les verrous des originaux. Construire l'export exclusivement depuis ces deux copies.

Les bases normales bénéficient des mises à jour ; elles ne servent jamais de destination au filtrage ou à la fusion. Les écrivains habituels de BooruFlow utilisent les mêmes verrous. SQLite fournit une copie cohérente de chaque fichier ; les deux sources restent des catalogues indépendants, sans transaction distante globale commune.

## 5. Fusion, noms et schéma conservés

La fusion reprend `_build_export`, `_resolve_aliases`, `validate_export` et le schéma existants :

```sql
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    post_count INTEGER NOT NULL CHECK(post_count >= 0),
    category INTEGER NOT NULL CHECK(category IN (0,1,3,4)),
    ambiguous INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE aliases (
    alias TEXT PRIMARY KEY,
    canonical_tag TEXT NOT NULL REFERENCES tags(name),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status='active'),
    chain_length INTEGER NOT NULL CHECK(chain_length > 0)
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX idx_tags_category_count ON tags(category, post_count DESC);
CREATE INDEX idx_tags_post_count ON tags(post_count DESC);
CREATE INDEX idx_aliases_canonical ON aliases(canonical_tag);
```

Les noms disposent également des index uniques de leurs contraintes. Les comparaisons dans l'export restent exactes, avec la collation BINARY par défaut.

Règles conservées : General 0, Artist 1, Copyright 3, Character 4 ; exclusion de Metadata, Deprecated et des catégories invalides des canoniques ; aucun seuil de posts ; préservation des zéros, noms exacts, Unicode et ID présents dans la source. Les sources d'alias actifs ne restent pas simultanément des canoniques utilisables.

Les chaînes actives sont aplaties vers un canonique existant et autorisé. Les relations pending/missing, cycles, destinations absentes/filtrées et collisions ambiguës sont exclues. Un ancien nom Deprecated peut rester comme alias valide. Les doublons canoniques actuellement présents entraînent un refus conservateur de l'export. Les anciennes tables d'audit `import_collisions` / `import_rejections` ne sont plus interprétées comme la preuve que les lignes actuelles sont encore invalides : les lignes du snapshot elles-mêmes sont contrôlées.

Les alias copiés ne sont ni renormalisés ni devinés. Pour les nouvelles synchronisations lancées par l'exporteur, l'option de parseur existante préserve les noms après un seul décodage URL. La comparaison des checkpoints normalise les deux côtés de la même manière, ce qui permet ensuite de retrouver les checkpoints contenant des noms avec casse, `+` ou `%` sans imposer une réconciliation complète.

Les métadonnées de l'export conservent sa version 1, son marqueur, `generated_at`, les comptes et raisons de rejet. Elles indiquent maintenant aussi :

- Les vrais chemins `source_tags_database` / `source_aliases_database`.
- Les réussites indépendantes `source_tags_sync_at` / `source_aliases_sync_at`.
- Leurs modes `source_tags_sync_mode` / `source_aliases_sync_mode`.
- Les dates de synchronisation complète `source_tags_full_sync_at` / `source_aliases_full_sync_at`.
- Le checkpoint des tags, les volumes copiés, le nombre de pages d'alias si connu et `source_snapshot_method = sqlite_backup`.
- Les heures de préparation de l'export, les URL sans authentification et la version de l'importateur.

Aucun identifiant n'est enregistré dans l'export ou affiché. Les identifiants ne sont nécessaires que pour les opérations de tags qui accèdent au réseau. Les deux variables d'environnement `GELBOORU_USER_ID` / `GELBOORU_API_KEY` ont priorité lorsqu'elles sont renseignées ensemble ; sinon, la commande lit les identifiants Gelbooru déjà enregistrés dans `config/booruflow_credentials.json`.

Aucun ancien fichier du projet ChatGPT Wikis n'était accessible lors de la première implémentation. Le schéma final de cette implémentation est ici conservé sans modification.

## 6. Validation, échecs et activation

Les copies subissent un contrôle d'intégrité. Le seuil de sécurité existant de 100 000 tags source est également vérifié sur la copie, même lorsqu'aucune synchronisation n'a été nécessaire ; il détecte un catalogue tronqué et ne filtre aucun tag selon son nombre de posts. L'export construit en transaction subit `integrity_check`, `foreign_key_check`, les contrôles de catégories, de volumes, de destinations, de collisions alias/canonique et de chaînes résiduelles. Les données de fusion proviennent uniquement des chemins des deux copies attendues ; les métadonnées de réussite sont relues dans ces copies.

L'export validé est fermé, synchronisé puis activé par `os.replace` sur le même système de fichiers. L'ancien export reste intact en cas d'erreur réseau, d'interruption, d'échec de copie, de validation ou d'activation. Les copies temporaires sont supprimées à la sortie du contexte, y compris sur interruption normale au clavier.

Les destinations correspondant aux catalogues ou aux répertoires de données de production restent refusées. Un fichier final existant inconnu, un lien symbolique/physique ou une destination possédant des journaux SQLite actifs n'est pas remplacé. Les exports précédents de cet outil sont reconnus par leur marqueur dans `metadata`.

Les mises à jour normales conservent leur sémantique : les pages de tags validées et leurs checkpoints peuvent rester enregistrés si une page suivante échoue ; une initialisation peut garder son fichier normal de reprise `.rebuild.part.sqlite`. Ces données ne sont pas présentées comme une réussite et l'export s'arrête. Ce fichier normal de reprise est distinct des copies temporaires de fusion, qui sont nettoyées. Une coupure brutale de la machine peut laisser un dossier temporaire, jamais réutilisé comme source ou cache.

## 7. Fichiers modifiés et tests

Fichiers de cette adaptation :

- `tools/export_wiki_db.py` : chemins configurés, commande locale, `--force-update` et protections de sortie.
- `src/booruflow/infrastructure/gelbooru_wiki_export.py` : fraîcheur indépendante, appels des producteurs normaux, snapshots WAL, fusion depuis les copies et métadonnées des sources.
- `src/booruflow/infrastructure/gelbooru_tag_importer.py` : réussite datée des mises à jour normales, marqueur d'interruption, validation des réponses et intégrité avant réussite.
- `src/booruflow/infrastructure/gelbooru_aliases.py` : réussite globale datée, marqueur d'interruption, séparation pending, comparaison compatible des checkpoints et refus des réponses invalides.
- `tests/unit/test_gelbooru_wiki_export.py` : fixtures adaptées et nouveaux scénarios de fraîcheur, WAL et échecs.
- `docs/gelbooru-wiki-export.md` : présent rapport actualisé.

**132 tests ciblés réussis**, hors réseau, avec les vrais producteurs et des bases temporaires :

```powershell
python -m pytest tests/unit/test_gelbooru_wiki_export.py tests/unit/test_gelbooru_tag_importer.py tests/unit/test_gelbooru_aliases.py tests/unit/test_catalog_process_lock.py tests/unit/test_wiki_import.py tests/unit/test_wiki_aliases.py -q
```

Couverture : deux bases récentes sans réseau/identifiants, chaque combinaison de fraîcheur, une/deux bases absentes, dates inconnues/invalides/futures/sans fuseau, frontière exacte de 24 heures, actualisation forcée puis exécution répétée, échec et interruption des deux synchronisations, conservation des dates précédentes et de l'ancien export, manque de checkpoint, annonce de réconciliation complète, transactions WAL validées mais non checkpointées, origines inchangées pendant la fusion, fusion exclusive des copies, zéros, chaînes/cycles/collisions, noms exacts, schéma et intégrité, échec de copie et nettoyage.

Un sous-processus Python avec `-S` a exécuté **la commande complète sur des catalogues de fixtures récents**, sans Qt, sans paquets tiers, sans identifiants et avec l'accès réseau explicitement interdit. Le seuil de volume y est adapté aux petites fixtures, comme dans les autres tests d'intégration. Une autre vérification couvre l'aide CLI. Ruff et les contrôles de différences passent sur le périmètre modifié.

Les tests utilisent un dossier temporaire unique sous `var/tmp`, le répertoire temporaire Windows habituel étant inaccessible dans cet environnement. Le garde des bases de production de `tests/conftest.py` est resté actif.

## 8. Limites et état de validation

- La fraîcheur de moins de 24 heures signifie qu'une **opération normale** s'est terminée avec succès. Elle ne garantit pas la révision de tous les compteurs et alias historiques ; les modes et dates de synchronisation complète restent visibles.
- Les noms qui auraient été transformés dans d'anciens catalogues ne peuvent pas être reconstruits à partir des copies seules. L'export préserve exactement les noms stockés et ne tente aucune réparation conjecturale.
- Une base Tags existante sans checkpoint exploitable doit être reconstruite via l'action normale de BooruFlow. L'outil l'indique et s'arrête.
- Une base absente, ou une perte de checkpoint d'alias, peut encore nécessiter une opération complète longue, annoncée avant lancement.
- Un changement incohérent des données ou de la pagination distante provoque un échec conservateur, avec maintien de l'ancien export.

**Conformément à la demande, aucun export complet réel ni aucune actualisation des catalogues réels n'a été lancé pendant cette intervention.** Leur audit a été effectué en lecture seule ; les métadonnées constatées avant et après les tests sont restées inchangées. Aucun réseau réel n'a été utilisé dans cette adaptation.

Le checkout reste sur `refactor/pyside6-mvc` ; les modifications préexistantes sont préservées. Aucun commit, push, packaging ni démarrage de l'interface graphique n'a été effectué. La génération réelle reste à lancer par l'utilisateur après validation.
