# Entity Profiles — seconde passe de review

## Politique de filtrage

La même politique de domaine est appliquée aux tables du profil et aux suggestions injectées dans Tagging. Elle filtre les résultats sans modifier les compteurs, statuts Keep/Review/Ignore, IDs déjà vus ou témoins stockés.

- Source `character` ou `copyright` : les relations de catégorie `artist` sont masquées.
- Source `artist` : les relations `character` et `copyright` restent visibles ; les autres artistes restent également disponibles.
- `anonymous` : toujours masqué, y compris dans le filtre des exclusions, quelle que soit la catégorie source.
- Les autres catégories de relations, notamment species/lore, conservent le comportement existant.

La catégorie source vient de l'action d'ouverture du profil ; dans Tagging, elle vient des métadonnées source et du catalogue local existant, ce dernier étant prioritaire. Les lignes locales déjà chargées sont réutilisées pendant la review. Aucune nouvelle colonne ou migration n'est nécessaire. Une catégorie source inconnue n'est pas devinée.

Les exclusions automatiques de composition couvrent les nombres positifs suivis de `girl/girls`, `boy/boys`, `other/others`, `futa/futas`, avec le suffixe `+` éventuel. Cela inclut `1girl` à `6+girls` et les familles équivalentes demandées, ainsi que les nombres supérieurs. Sont également exclus `solo`, `solo_focus`, `multiple_girls`, `multiple_boys`, `multiple_others`, `multiple_futa`, `multiple_futas` et leurs variantes singulières girl/boy/other. La catégorie `meta`/`metadata` est exclue automatiquement.

Ces exclusions automatiques ne réécrivent pas `global_exclusions`. Elles masquent les suggestions ordinaires, même lorsqu'une ancienne ligne avait été conservée. Dans le dialogue, leurs statistiques restent consultables via le filtre « Exclusions ». Les exclusions globales manuelles existantes et leur restauration restent disponibles. Les vêtements, accessoires, hairclip/hair ornaments, brooch, tattoo, scar, armes et footwear ne sont pas filtrés par leur nom.

## Review et tri

Les deux tables disposent de raccourcis limités au widget de table et à ses enfants : `A` conserve, `R` ignore, `Espace` inspecte les témoins, `B` ajoute une exclusion globale via `global_exclusions`. Les champs de texte ne déclenchent aucune de ces actions.

Après Keep ou Ignore, la sélection passe à la prochaine ligne dans l'ordre visible capturé avant la décision. Cela fonctionne avec un tri manuel et un filtre qui retire la ligne traitée. Avec une sélection multiple, l'avancement part de la dernière ligne sélectionnée. À la fin, la dernière ligne reste sélectionnée si elle est encore visible ; sinon la sélection devient vide, sans retour au début.

Les actions principales sont Keep, Ignore, Blacklist et Show matching posts, avec indication des touches. Review reste le statut initial, un filtre et une action secondaire du menu contextuel.

Attributes et Relations démarrent avec la colonne fréquence décroissante. Les valeurs stockées dans les cellules restent numériques ; un delegate affiche le pourcentage. Le choix manuel du tri survit aux filtres, décisions, refresh et changements de dataset pendant la session. Une nouvelle fenêtre retrouve le tri par fréquence.

## Témoins en grille

L'inspection réutilise `ResultCard` et `CollapsibleResultGroup` de Tagging, dans un `QScrollArea`. Les cartes ont des miniatures de 150 × 135 et l'ID sous l'image. Le conteneur accepte de rétrécir pour permettre au layout partagé de recalculer les colonnes.

Le `ThumbnailLoader` existant et le cache mémoire partagé restent utilisés, avec deux requêtes simultanées au maximum. Leur création et leur lancement se font uniquement lors de l'inspection. Seules les URL preview des témoins sont utilisées, sans récupération des originaux. La limite reste de 100 posts. Le double-clic de la carte délègue l'URL du bon post au BrowserLauncher fourni au dialogue. Le loader est arrêté même si l'inspection se termine par une exception.

## Périmètre des fichiers de cette seconde passe

- `src/booruflow/domain/entity_profiles.py` : politiques contextuelle et composition/meta.
- `src/booruflow/infrastructure/entity_profile_repository.py` : filtrage des suggestions avec les catégories sources fournies par le consommateur.
- `src/booruflow/presentation/pyside6/tagging_controller.py` : passage des catégories sources aux deux points d'injection existants.
- `src/booruflow/presentation/pyside6/entity_profiles.py` : filtrage de présentation, raccourcis/avancement, toolbar, tri numérique et grille réutilisée.
- `resources/i18n/en.json`, `resources/i18n/fr.json` : clés Blacklist et Space/Espace.
- `tests/unit/test_entity_profiles.py` : politique directionnelle, familles de composition, attributs préservés, historique inchangé et limite des témoins.
- `tests/unit/test_entity_profiles_ui.py` : présentation, tri, raccourcis filtrés/triés et dernière ligne, association miniature/ID, redimensionnement, ouverture et injection Tagging.
- `docs/entity-profiles-review-pass2.md` : présent compte rendu.

Le moteur d'analyse/refresh, les transactions, le schéma SQLite, WD14 et ses scores, la provenance, l'anti-récursion, BrowserLauncher et le cache de métadonnées partagé n'ont pas été réécrits. Les autres changements présents dans le checkout ont été conservés.

## Validation du 5 octobre 2026

- Ciblée, avec les tests Entity Profiles, BrowserLauncher et helper Tagging autonome : **91 réussis**.
- Suite complète offscreen : **1 306 réussis, 2 ignorés, 14 sous-tests réussis**, en **78,64 s**.
- Fichier de tests Qt Entity Profiles exécuté avec le plugin natif **windows** : **34 réussis**, en **4,97 s**.
- Ruff sur les six fichiers Python de cette passe : réussi.
- `compileall` sur `src` et `tests` : réussi.
- `git diff --check` : réussi.
- Vérification visuelle native Qt Windows : Attributes, Relations et grille, en EN et FR, plus grille étroite dans les deux langues. Huit captures inspectées dans `var/tmp/entity-pass2-render/`. Données temporaires et images synthétiques déjà placées dans le cache mémoire ; aucun appel réseau n'était nécessaire. La grille montre trois colonnes à 760 px et deux à 430 px dans cet environnement Windows.

Aucune validation n'a ouvert les bases actives pour y analyser une galerie. Aucun commit, push ou packaging.
