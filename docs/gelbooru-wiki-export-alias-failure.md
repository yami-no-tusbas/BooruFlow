# Export Wiki : échec réel des alias du 9 octobre 2026

## Cause démontrée

La commande `python` de cette machine utilise `C:\Python314\python.exe` (Python 3.14.7).
L’environnement `.venv` des tests utilise Python 3.12.10.

`AliasPageParser`, dérivé de `html.parser.HTMLParser`, stockait le statut de la ligne
courante dans `self._pending`, un booléen. Le parseur standard Python 3.14 utilise
aussi `_pending`, mais comme liste de morceaux de texte. Après une dernière ligne
`pending`, notre champ valait réellement `True`. La fermeture exécutait alors :

```text
parse_alias_page -> parser.close()
C:\Python314\Lib\html\parser.py:198
self.rawdata += ''.join(self._pending)
TypeError: can only join an iterable
```

Cette valeur et cette pile ont été relevées sur la page publique réelle `pid=23550`
(33 relations), sans synchroniser la base active. La page initiale ne déclenchait
pas cette erreur. Un flux découpé échouait également dans `feed()` avec
`AttributeError: 'bool' object has no attribute 'append'`.

L’exception vient du traitement normal du HTML, pas du gestionnaire d’exceptions
de l’exporteur. Ce dernier ne faisait qu’afficher le message et supprimait la pile
utile au diagnostic. La collision existait déjà dans le parseur avant les récents
changements de l’exporteur ; ces changements ne sont pas à l’origine du nom du champ.

La pagination, les checkpoints, la normalisation des noms et le décodage UTF-8
ont été examinés : le défaut reproduit intervient dans `HTMLParser.close()`, avant
la comparaison du checkpoint. Aucun de ces éléments n’a été contourné pour corriger
l’erreur.

## Correction

- Renommer exclusivement le champ de statut de ligne en `_row_pending` dans le
  parseur partagé, pour conserver le tampon interne de `HTMLParser`.
- Afficher désormais les types d’exceptions, les causes et les emplacements de pile
  dans l’exporteur. Les identifiants et clés connus, y compris leurs formes encodées
  dans les URL, sont expurgés. Aucun contenu de variable locale ni ligne source
  n’est affiché. Les messages de progression expurgent aussi l’identifiant utilisateur.
- Conserver les validations de pagination, de chevauchement, d’intégrité et de
  fraîcheur, ainsi que les transactions et marqueurs existants. Aucun défaut
  artificiel, suppression de marqueur ou changement de checkpoint n’est nécessaire.

## État réel constaté après l’échec

Lecture SQLite avec `mode=ro` exclusivement ; aucun appel aux méthodes de migration
ou de synchronisation sur ces chemins actifs.

| Élément | Tags | Alias |
| --- | --- | --- |
| Fichier sous `data/databases/` | `g_tags_260810.db` | `gelbooru_aliases.db` |
| `PRAGMA integrity_check` | `ok` | `ok` |
| Lignes | 1 465 371 | 23 343 |
| Détail des statuts | — | 23 329 active, 7 pending, 7 missing |
| Version | `2.1-after-id-ascending` | schéma `1`, checkpoint `1` |
| Checkpoint | `after_id=1968302`, égal au maximum des ID | 20 triplets conservés, `observed_last_pid=23350` |
| Pagination enregistrée | — | 468 pages, taille 50 |
| Marqueurs | `completed=1`, `update_in_progress=0` | `sync_in_progress=true`, `initial_in_progress=false`, `initial_next_pid=0` |
| Dernière réussite | `2026-10-09T15:40:18+00:00`, `incremental_new_ids` | `last_incremental_sync=2026-09-21T08:37:53+00:00` |
| Dernière synchronisation complète | `completed_at=2026-08-10T18:17:22+02:00` | `last_full_sync=2026-09-02T18:00:39+00:00` |

Aucun verrou `.operation.lock` n’était présent pour ces deux catalogues. La base de
tags avait un WAL vide et un fichier SHM de 32 768 octets. Le catalogue d’alias ne
possédait pas de nouveau `last_successful_sync_at` : la dernière date de réussite
reste historique. Le marqueur d’interruption suffit à empêcher de considérer cette
base comme récente, même avec une date de réussite encore dans la fenêtre de 24 h.
Les mises à jour de tags réussies sont conservées et restent indépendantes.

## Vérifications

Avant correction, les nouvelles régressions exécutées avec Python 3.14 reproduisent
le `join(True)` et la corruption du tampon sur entrées découpées. Après correction :

- 139 tests ciblés et 8 sous-tests réussis sous Python 3.12 : export Wiki,
  compatibilité du parseur, importeurs de tags et d’alias, verrous, import Wiki
  et résolution des alias.
- 4 tests autonomes réussis avec chacun des interpréteurs Python 3.12 et 3.14,
  option `-S`, hors réseau : lignes actives/en attente, entrée découpée,
  incrémental avec fin de page en attente, export complet après erreur réseau
  ou interruption des alias. Les tags réussis ne sont pas resynchronisés ;
  le précédent export reste conservé pendant l’échec ; un export suivant avec
  catalogues valides fonctionne sans téléchargement.
- Le diagnostic CLI conserve la chaîne d’exceptions et les emplacements tout en
  supprimant clé, clé encodée et identifiant ; les variables locales ne sont pas affichées.
- Une réussite récente accompagnée de `sync_in_progress` ou `initial_in_progress`
  ne peut pas rendre le catalogue récent ; le checkpoint demeure conservé.
- Vérification complémentaire sous Python 3.14 sur une **copie SQLite temporaire du
  catalogue réel** : lecture publique strictement bornée à huit requêtes
  (`0, 23550, 0, 23500, 23450, 23400, 23350, 23300`), aucun parcours complet.
  Le checkpoint a été retrouvé ; l’incrémental est terminé : 233 nouvelles
  relations, 6 modifications, 23 500 active, 67 pending, 9 missing sur la copie.
  Le SHA-256 du fichier actif d’alias était identique avant et après. La copie
  et son verrou ont été supprimés à la fin.

Aucune reconstruction, réconciliation complète réelle, écriture des catalogues
actifs, activation d’un export réel, modification de configuration, commit, push
ou packaging n’a été effectué. Les modifications préexistantes sont conservées.
L’utilisateur relancera lui-même l’export réel après validation.
