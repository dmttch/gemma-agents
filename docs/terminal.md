# Interface terminal

## Interface terminal

### Intervenir pendant le travail

Pendant un tour, une vue de travail affiche le flux du modèle et les outils, avec
une zone de saisie indépendante. Écris une précision puis Entrée : elle sera
intégrée entre deux actions, avec un accusé de prise en compte. Les appels restants
d'un lot déjà généré sont laissés non exécutés si une précision attend ; le modèle
peut alors adapter la suite. Le résultat final est rendu en Markdown dans le
défilement normal du terminal, avec les outils du tour consultables via `/last`.

| Pendant l'exécution | Effet |
| --- | --- |
| Une précision + Entrée | Ajouter une consigne au tour courant |
| `/pause` | Suspendre avant la prochaine action ; l'action en cours peut finir |
| `/continue` | Reprendre après une pause |
| `/stop` ou Ctrl+C | Annuler le tour ; conserver son historique |
| Tab | Passer entre saisie et journal ; flèches/PageUp pour parcourir le journal |
| Alt+Entrée ou Ctrl+J | Nouvelle ligne dans la saisie |
| `/once`, `/allow`, `/deny` | Répondre à une approbation en attente |

La pause ne gèle pas les processus OS. L'annulation coupe une commande ponctuelle
et est vérifiée pendant le streaming ; un appel réseau sans réponse peut attendre
son timeout. Les processus lancés explicitement en arrière-plan gardent leur cycle
de vie propre. Une précision arrivée pendant la validation finale d'une tâche
est enregistrée et la tâche passe `limited` pour permettre sa reprise. Le mode
`--plain` conserve l'interface séquentielle, sans saisie pendant le travail.

Les sessions reçoivent un titre depuis leur première demande. Commandes disponibles :

| Commande | Usage |
| --- | --- |
| `/rename Mon titre` | Renommer la session |
| `/sessions texte` | Rechercher dans les titres et conversations du workspace |
| `/preview IDENTIFIANT` | Aperçu des derniers échanges avant reprise |
| `/fork` | Dupliquer la conversation et son plan ; le workspace reste partagé |
| `/export conversation.md` | Export Markdown dans le workspace, sans écraser un fichier |
| `/model` | Modèle du prochain tour et modèles installés |
| `/model NOM` | Choisir un modèle installé pour cette session, sans téléchargement |
| `/allow uv run pytest -q` | Autoriser cette commande exacte pendant 8 heures |
| `/permissions` | Voir les permissions actives du workspace et leurs expirations |
| `/permissions revoke ID` | Révoquer une permission |
| `/review on`, `/review off` | Activer / désactiver la préparation des éditions |
| `/review` | Choisir une édition, puis les blocs à appliquer ou annuler |

Une duplication copie les messages, le plan, le point de reprise, le titre et
le choix du modèle. Elle ne copie ni les permissions ni le journal d'annulation,
et ne crée pas de branche ou de copie des fichiers. Ne travaille pas simultanément
dans les deux conversations sur les mêmes fichiers.

### Recherche et références de fichiers

Dans un message, utilise `@src/module.py`, `@src/module.py:20-40`, ou
`@"dossier avec espaces/module.py:20-40"`. Tab complète les chemins lors de la
saisie initiale. Au plus cinq références sont jointes, dans la limite de 16000
caractères du message enrichi. Une référence explicite peut viser un fichier
ignoré ; la recherche automatique respecte les `.gitignore` imbriqués.

Les outils `find_files`, `search_text` (texte littéral) et `project_instructions`
permettent au modèle de cibler son exploration. Les recherches sont bornées,
ignorent les liens symboliques et les répertoires de dépendances/caches usuels.
Le `AGENTS.md` racine est chargé au début du tour. Lors du premier accès à une cible
avec des instructions applicables, le gateway les présente avant d'exécuter
l'appel. Les conventions du sous-dossier ne s'appliquent qu'à ce sous-dossier ;
elles n'accordent jamais de permissions et les consignes utilisateur priment.

### Revue des modifications

`/review on` fait préparer les éditions des outils fichiers sans les appliquer.
Les commandes et écritures Git sont refusées dans ce mode ; des processus déjà
actifs doivent être arrêtés avant de l'activer. Le mode est propre à la session
dans le runtime courant et doit être réactivé après redémarrage.

`/review` affiche une sélection des éditions, puis les blocs modifiés. Tu peux
appliquer une proposition, la rejeter, ou annuler des blocs d'une édition appliquée.
Le contenu actuel doit toujours correspondre au contenu attendu : les changements
utilisateur intervenus entre-temps provoquent un refus. Après application partielle,
la proposition est consommée ; les blocs non sélectionnés ne sont pas appliqués.
Une nouvelle proposition est nécessaire pour les appliquer plus tard.

En mode simple, les équivalents sont `/review accept ID [BLOC…]`,
`/review reject ID` et `/review undo ID [BLOC…]`. Sans liste de blocs, l'action
concerne toute l'édition. `/diff` permet d'inspecter les changements avant cette
commande ; les identifiants de blocs sont affichés dans la revue interactive.

### Permissions et corrections automatiques

Une permission correspond à un programme et ses arguments exacts, jamais à un
préfixe de commande. Elle est partagée entre `run_command` et `run_check`, mais
n'autorise pas `start_process`, Git ou le Web. Elle reste soumise à la politique
et au sandbox. Les permissions expirent au bout de 8 heures par défaut, même si
la conversation est reprise. Leur révocation prend effet au prochain appel.

Pour autoriser explicitement des commandes d'une tâche ou d'une planification :

```sh
gemma-agents tasks add "Corrige le parseur" \
  --check "uv run pytest -q" --allow-command "uv run pytest -q"
gemma-agents schedule add "0 9 * * *" "Lance les tests et résume les échecs" \
  --allow-command "uv run pytest -q" --grant-hours 24
```

`--grant-hours` accepte 1 à 168 heures. La permission d'une planification est
consultée lors de chaque appel de ses tâches générées : la révoquer concerne
aussi les occurrences déjà en attente. Sans permission applicable, un worker
continue de refuser les actions sensibles.

Après échec des checks déclarés, le runtime transmet les erreurs au modèle,
puis relance les mêmes commandes. `AGENT_REPAIR_ATTEMPTS` limite les corrections
supplémentaires (2 par défaut, de 0 à 5). Les tours de correction partagent le
budget initial de `AGENT_MAX_STEPS`. Les refus ne déclenchent pas de correction
automatique, et une nouvelle validation en échec sans changement du projet arrête
la boucle. Trois appels d'outil identiques consécutifs arrêtent aussi le tour avant
la troisième exécution. Un appel invalide corrigé avec succès ne bloque plus à lui
seul la conclusion du tour.

`/checks` affiche `current`, `stale` ou `unknown` en plus du résultat observé.
L'empreinte couvre les fichiers non ignorés, jusqu'à 10000 fichiers / 50 Mo ;
elle devient inconnue au-delà ou si la lecture échoue. Ce contrôle ne détecte pas
les changements des fichiers ignorés, des dépendances ou des services externes.
Un check qui modifie lui-même des fichiers est signalé comme obsolète. Un statut
`passed` décrit sa sortie observée ; il ne remplace pas cette indication de validité.

Lance `gemma-agents` dans un terminal interactif. La conversation terminée
reste dans le défilement normal du terminal. Les réponses finales sont rendues en
Markdown, avec le code coloré ; la vue de travail montre leur génération en direct.
Les outils affichent une ligne d'activité et un extrait de résultat. Les demandes
d'approbation montrent les arguments exacts de l'appel avant validation.

| Raccourci | Action |
| --- | --- |
| Entrée | Envoyer la saisie |
| Alt+Entrée (ou Échap puis Entrée), Ctrl+J | Insérer une nouvelle ligne |
| Collage multiligne | Conserver les lignes avant envoi (bracketed paste) |
| Tab | Compléter une commande ou un identifiant après `/resume ` |
| Haut / Bas, Ctrl+R | Parcourir / rechercher l'historique de saisie |
| Ctrl+C pendant la saisie | Annuler la saisie |
| Ctrl+C pendant le travail | Interrompre le tour, conserver la session |
| Ctrl+D sur une saisie vide | Quitter |

Sur macOS, si Option+Entrée n'est pas transmis comme Alt+Entrée, utilise
Échap puis Entrée, ou Ctrl+J. L'historique de saisie est rechargé depuis les
messages de la session, sans fichier d'historique global entre projets.

| Commande | Usage |
| --- | --- |
| `/help` | Toutes les commandes |
| `/new`, `/sessions`, `/resume IDENTIFIANT` | Créer, lister, reprendre une session |
| `/session`, `/history` | Identifiant et derniers messages |
| `/plan`, `/checkpoint` | Étapes et point de reprise durable |
| `/diff` | Journal des éditions de la session, avec identifiants |
| `/undo [IDENTIFIANT]` | Annuler une édition ; la dernière par défaut |
| `/check uv run pytest -q`, `/checks` | Exécuter une validation, consulter les résultats |
| `/tasks`, `/task IDENTIFIANT` | Lister et exécuter les tâches, puis ouvrir leur session |
| `/retry IDENTIFIANT` | Remettre en attente une tâche inspectée avant reprise |
| `/tools`, `/last` | Activer les détails / lire les sorties du dernier tour |
| `/clear`, `/quit` | Effacer l'écran / quitter sans supprimer l'historique |

`gemma-agents --plain` désactive l'éditeur enrichi et les animations.
Ce mode est également choisi automatiquement si l'entrée ou la sortie n'est
pas un terminal. `NO_COLOR=1` désactive les couleurs Rich.

### Sessions et compatibilité des données

```sh
gemma-agents sessions
gemma-agents --resume IDENTIFIANT
gemma-agents run --resume IDENTIFIANT "Continue le travail"
```

Le schéma SQLite est étendu sans supprimer les sessions ni les messages existants.
Si `src/gemma_agents/storage/agent.db` existe, ce stockage V1 est repris par défaut.
Sinon, le stockage est `~/.local/share/gemma-agents`. Pour une autre installation V1 :

```sh
export AGENT_STORAGE=/chemin/vers/ancien/storage
export AGENT_WORKSPACE=/chemin/vers/ancien/workspace
gemma-agents --resume IDENTIFIANT
```

Une session ne peut être reprise que dans son workspace d'origine. L'historique
complet reste en base. Les résultats d'outils trop longs sont bornés pour le modèle ;
les anciens tours sont remplacés dans le contexte par des extraits bornés.
Dans un tour long, les anciens lots d'appels terminés peuvent être condensés en
conservant la demande et le dernier lot complet d'appels/résultats. Une demande
ou un dernier lot encore trop volumineux s'arrête explicitement.
Les appels d'outils interrompus sont signalés comme « résultat inconnu » à la reprise,
sans les réexécuter automatiquement.

Le modèle dispose de `save_checkpoint(objective, decisions, next_steps)` et
`get_checkpoint` pour conserver une synthèse de travail. Le plan et ce point de
reprise sont rechargés à chaque étape. Les extraits automatiques restent des
extraits, et les points de reprise des déclarations du modèle : ni l'un ni l'autre
ne prouve qu'une action a réussi. Le budget affiché est en caractères JSON,
pas en tokens. Les exécutions ont un journal persistant de début/fin ; un arrêt
brutal reste visible comme exécution inachevée à la prochaine ouverture.

## Éditions et validations

`read_file` accepte `start_line` et `end_line` (lignes inclusives, numérotées à
partir de 1 ; 200 lignes par défaut, au plus 500). Les résultats sont bornés pour
éviter de remplir le contexte avec un fichier entier. Le résultat destiné au modèle
sépare le contenu brut et les métadonnées de navigation en JSON, pour éviter de
recopier accidentellement des numéros de ligne. Les espaces et fins de ligne du
contenu sont préservés ; `partial_line` signale une ligne coupée par la limite.

Les outils `write_file`, `replace_text` et `apply_patch` enregistrent les contenus
avant/après dans SQLite et écrivent par remplacement atomique. `apply_patch`
accepte un diff unifié exact sur un fichier existant, sans renommage ni recherche
approximative. `/diff` affiche ces éditions, indépendamment des changements Git.

`/undo` restaure le contenu précédent seulement si le contenu actuel est encore
identique au résultat enregistré. Si quelqu'un a modifié le fichier, l'annulation
est refusée. Une création annulée supprime le fichier créé ; les répertoires
créés restent présents. Les changements effectués par des commandes, Git ou des
processus ne sont **pas** couverts. Une édition laissée `pending` par un arrêt
brutal doit être inspectée ; elle n'est pas annulée automatiquement. Ces contrôles
ne protègent pas contre les courses de modifications concurrentes : conserve une
seule instance d'écriture par workspace.

`run_check` utilise le même sandbox, la même politique et les mêmes approbations
que `run_command`, et conserve le code de sortie observé. `/checks` montre les
résultats à leur date d'exécution ; une modification ultérieure peut les rendre
obsolètes. Pour une tâche, fournis des critères explicites :

```sh
gemma-agents tasks add "Corrige le parseur CSV" \
  --check "uv run pytest -q" --check "uv run ruff check src"
gemma-agents tasks run IDENTIFIANT
```

Ces commandes sont exécutées après la conclusion du modèle. Après épuisement des
corrections autorisées, un code non nul ou un timeout entraîne `failed`, une
approbation et une permission absentes `blocked`. Le résultat expose
`verification` (`passed`, `failed`, `blocked`, `not_run` ou `not_requested`).
Sans critères, `completed` signifie seulement que le tour du modèle est terminé.
Les checks planifiés nécessitent une permission explicite pour passer sans approbateur.

