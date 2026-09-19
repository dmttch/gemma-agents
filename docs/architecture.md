# Architecture et contrats

## Responsabilités

| Composant | Responsabilité |
| --- | --- |
| `main`, `terminal`, `api` | Adapter commandes, saisie et transport aux services |
| `Runtime` | Assembler les dépendances, posséder les ressources et orchestrer un tour ou une tâche |
| `AgentLoop` | Alterner modèle et outils dans un budget borné |
| `ContextBuilder` | Construire un contexte borné et conserver les séquences outils/résultats |
| `ToolRegistry` | Décrire et valider les fonctions disponibles |
| `ToolGateway` | Appliquer politique, approbations et audit avant exécution |
| `SandboxRunner` | Exécuter, borner et nettoyer les processus |
| `WorkspaceLease` | Coordonner les processus indépendamment du stockage choisi |
| `Database`, `migrations` | Transactions et évolution ordonnée du schéma |
| `storage.Retention` | Maintenance explicitement demandée par l'opérateur |
| `diagnostics` | Sonder les prérequis sans lancer de tâche modèle |
| `OllamaLLM` | Adapter les appels réseau, métadonnées et streaming |

Les contrats `ChatModel` et `EmbeddingModel` séparent les capacités nécessaires.
`Runtime` accepte un modèle injecté : les tests ne dépendent pas d'un serveur.
Le streaming reste une capacité optionnelle. Les outils sont des fonctions
enregistrées, extensibles sans modifier la boucle. Les services de sauvegarde et
de suppression ne sont jamais exposés au modèle.

Ces choix appliquent les principes SOLID par des responsabilités ciblées, des
interfaces réduites et l'injection de dépendances. `Runtime` reste volontairement
le point de composition ; il ne faut pas multiplier les abstractions sans besoin.

## Chemin d'exécution

1. Acquérir le workspace et un verrou partagé du stockage.
2. Vérifier la session et prendre le verrou de tour du runtime.
3. Valider le modèle installé, charger instructions, plan et contexte.
4. Demander une réponse au modèle ; valider chaque appel d'outil.
5. Appliquer les restrictions et obtenir l'approbation éventuellement nécessaire.
6. Exécuter, enregistrer le résultat et l'audit, puis réitérer.
7. Pour une tâche, exécuter les checks fixés par l'opérateur et tenter les
   corrections dans le budget initial.
8. Finaliser le statut, nettoyer les processus et libérer les ressources à la fermeture.

## Concurrence et durabilité

Le verrou de workspace est un `flock` sur le répertoire lui-même. Il n'est pas
contourné en changeant `AGENT_STORAGE`, ni en supprimant un fichier de verrou.
Les autres logiciels ne participent pas à ce verrou consultatif. Le filesystem
local est requis ; les comportements des verrous réseau ne sont pas qualifiés.

Le stockage est partagé entre workspaces. Les migrations SQLite sérialisent les
écritures avec `BEGIN IMMEDIATE`. La restauration exige un verrou exclusif du
répertoire de stockage. Les prises de tâches et créations d'occurrences cron
utilisent des transactions pour empêcher une double attribution.

Une édition est journalisée avant remplacement atomique du fichier. SQLite et le
filesystem ne forment pas une transaction unique : une interruption peut laisser
une édition `pending`. Les annulations comparent le contenu actuel à celui
enregistré avant de restaurer. Les commandes et Git ne sont pas annulables par ce journal.

## Contrats de résultats

`RunResult` sépare le statut de tour et le résultat de validation. Un résultat
`completed` sans checks n'est pas une preuve fonctionnelle. Une `ToolFailure`
conserve un message textuel exploitable sans transformer un refus métier en
succès d'exécution. Les erreurs corrigées d'un outil peuvent être levées après un
appel réussi ; une approbation refusée reste bloquante pour le tour. Ce blocage
n'annule pas les validations fixées par l'opérateur : elles conservent leur
permission propre, s'exécutent une fois et leur résultat observé est rapporté
sans transformer le tour bloqué en succès.

Les modules Python documentés dans la référence sont des API internes. Pour
intégrer le produit, privilégiez la CLI ou l'API HTTP. Une modification de ces
contrats externes doit être accompagnée d'une note de migration et de tests.
