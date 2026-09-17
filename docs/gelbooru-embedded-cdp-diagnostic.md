# Diagnostic CDP de la publication Embedded

Ce mode sert uniquement à comparer la structure du POST manuel et du POST
Embedded. Il n'envoie rien par lui-même et ne change ni le formulaire, ni le
merge des tags, ni `requestSubmit`, ni la vérification après soumission.

## Démarrage requis

QtWebEngine doit activer son endpoint de débogage avant la création de
l'application. Fermer BooruFlow, puis lancer :

```powershell
.\Lancer-BooruFlow.bat --embedded-cdp-diagnostic
```

Le port par défaut est `9223`. En cas de conflit local :

```powershell
.\Lancer-BooruFlow.bat --embedded-cdp-diagnostic=19333
```

Le point de contrôle est limité à `127.0.0.1`. BooruFlow attache ensuite le
diagnostic au `devToolsId` exact de la page QtWebEngine concernée ; il ne choisit
pas une page par son URL et ne se connecte pas au navigateur CDP externe.

Au démarrage, le journal doit confirmer :

```text
BooruFlow startup: embedded_cdp_diagnostic=true bind=127.0.0.1 port=9223 configured_before_qapplication=true
```

Avant d'autoriser une capture, BooruFlow vérifie successivement
`/json/version`, `/json/list`, la target dont l'identifiant correspond exactement
au `devToolsId`, la connexion WebSocket, `Runtime.enable`, une évaluation locale
de `1+1`, puis `Network.enable`. Les événements CDP intercalés sont identifiés par
leur type et ignorés jusqu'à la réponse portant l'identifiant numérique attendu.

## Premier retest sans POST

1. Fermer complètement BooruFlow et le relancer avec le mode ci-dessus.
2. Ouvrir la session Embedded, sans modifier aucun tag.
3. Cocher seulement « Tracer le prochain POST réel via CDP ».
4. Ne pas cliquer sur « Save changes ».
5. Vérifier la présence de `target_match=true`, `websocket_connected=true`,
   `probe_command=true` et `network_enable=true`.
6. Désarmer la case et arrêter le test.

En cas d'échec, le journal indique désormais la phase exacte, par exemple
`phase=json_version`, `phase=target`, `phase=websocket_connect`,
`phase=runtime_enable`, `phase=runtime_evaluate` ou `phase=network_enable`.

Le composant Python `websocket-client` de l'option `browser-cdp` doit être
installé. Si le mode de démarrage ou cette dépendance manque, l'interface refuse
le diagnostic avant tout POST et demande un redémarrage approprié.

## Capture manuelle, contrôlée par l'utilisateur

1. Ouvrir Options, choisir le backend Embedded, puis ouvrir la session intégrée.
2. Se connecter et ouvrir soi-même la page d'édition du post de test.
3. Renseigner, si utile, les champs « Ajouts HTTP attendus » et « Retraits HTTP
   attendus ». Ces valeurs servent seulement à produire des booléens ; elles ne
   sont jamais écrites dans le formulaire ni affichées dans les logs.
4. Cocher « Tracer le prochain POST réel ».
5. Vérifier que l'état indique que la trace CDP est armée.
6. Cliquer soi-même une seule fois sur « Save changes ». Ce clic réalise le POST
   réel ; BooruFlow ne le déclenche pas.
7. Copier la ligne `Gelbooru outgoing edit request (CDP)` marquée
   `source=manual`.

## Capture Embedded, contrôlée par l'utilisateur

1. Préparer exactement une entrée `pending_publish` dans le Batch.
2. Laisser « Diagnostic formulaire » désactivé et cocher « Tracer le prochain
   POST réel » dans la fenêtre de session.
3. Vérifier les ajouts/retraits attendus et confirmer explicitement la boîte de
   dialogue de publication réelle.
4. Lancer « Publier le lot ». Le chemin normal arme CDP avant son appel existant
   à `requestSubmit`; si `Network.enable` échoue, il s'arrête avant cet appel.
5. Copier la ligne `Gelbooru outgoing edit request (CDP)` marquée
   `source=embedded`.

## Données journalisées

La capture utilise `Network.requestWillBeSent`. Elle lit d'abord
`request.postData`; si ce champ est absent, elle appelle
`Network.getRequestPostData(requestId)`. Le corps brut est immédiatement réduit
à : méthode, chemin, type de contenu, longueur, noms de champs expurgés, nombre
de champs `tags`, nombre de tags et doublons, booléens d'ajouts/retraits, et
compteurs des encodages `+`, `%20`, `%28`, `%29`, CR/LF et `_`.

Les cookies, en-têtes d'authentification, valeurs CSRF, UID, nom d'utilisateur,
`lupdated`, valeurs brutes des tags et corps brut ne sont jamais journalisés.
L'intercepteur Qt reste un témoin séparé de méthode, chemin, type de ressource et
navigation ; son `requestBody()` n'est plus lu et ne sert plus de preuve.
