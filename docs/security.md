# Sécurité et périmètre de confiance

## Commandes, processus et sandbox

Les outils `run_command` et `start_process` partagent le même exécuteur sans shell.
Utilise `uv run pytest`, `uv run script.py`, etc. `start_process` retourne un
identifiant utilisable avec `process_status` et `stop_process`.

Les processus persistent **entre les tours du même runtime**, pas après son arrêt.
Ils sont limités à quatre actifs par session ; le runtime conserve les derniers
40 000 octets de sortie et arrête les groupes de processus à sa fermeture. Ceux
d'une tâche en file sont arrêtés à la fin de cette tâche. La commande ponctuelle
est bornée par `AGENT_COMMAND_TIMEOUT`.

Par défaut, les processus et les outils Git passent par `sandbox-exec` sur macOS :

- lecture/écriture du workspace et d'un répertoire temporaire privé au runtime ;
- lecture des bibliothèques système, outils Homebrew et environnements Python ;
- réseau refusé et environnement minimal, sans transmettre les variables secrètes ;
- exécution refusée si le sandbox manque, sans repli automatique ;
- hooks Git, fsmonitor, diff externe et textconv désactivés pour les outils Git.

Préinstalle les dépendances du projet depuis ton terminal avec `uv sync` : les
processus de l'agent sont hors ligne et n'ont pas accès au cache uv personnel.
Les métadonnées du système de fichiers sont lisibles ; il ne s'agit pas d'un système
masquant l'existence de tous les chemins.

**Limite :** Seatbelt / `sandbox-exec` est déprécié par Apple. Cet adaptateur est
expérimental, testé sur le Mac de développement, et ne constitue pas une frontière
de sécurité auditée face à du code hostile. Les chemins résolus des outils fichiers
bloquent les sorties simples et les liens symboliques ; ils ne garantissent pas
l'absence de courses avec un autre processus modifiant les liens. Utilise des
workspaces de confiance. Une VM serait préférable pour exécuter du code non fiable.

Le runtime résout `uv`, Git et les commandes autorisées depuis les emplacements
de l'opérateur, notamment `~/.local/bin`, Homebrew et le PATH initial. Les chemins
résolus dans le workspace sont refusés. Les exécutables retenus reçoivent un droit
de lecture explicite dans le profil Seatbelt. Leur remplacement par un autre
programme externe pendant le travail n'est pas couvert par le modèle de menace.

Le verrou de workspace coordonne les instances Gemma ; il n'empêche pas un éditeur
ou un programme externe de changer les fichiers. Il protège contre une seconde
instance accidentelle, sans transformer un workspace hostile en environnement sûr.
Les commandes approuvées gardent la capacité de modifier ou supprimer le projet.

`AGENT_SANDBOX=off` désactive explicitement l'isolation OS pour un environnement de
confiance ; les approbations restent requises. `doctor` permet de tester le lancement
sandboxé sans lancer le modèle.

## Recherche Web

Configure un serveur SearXNG dont le format JSON est activé :

```sh
export AGENT_SEARXNG_URL=http://localhost:8080
gemma-agents
```

L'outil `web_search` est alors proposé au modèle. Chaque recherche requiert une
approbation car la requête quitte le runtime. Le serveur est fixé par l'utilisateur ;
le modèle ne choisit pas l'hôte. Les réponses sont bornées et traitées comme des
données non fiables. Pas de navigateur automatisé ni de lecture arbitraire d'URL
à ce stade. Les tâches planifiées ne peuvent pas approuver une recherche Web.

## Signaler une vulnérabilité

Contactez en privé **dmttch@proton.me**, sans joindre de secrets ni de données
personnelles. Indiquez la version, macOS, les étapes de reproduction et l'impact
observé. Aucun délai de résolution garanti n'est annoncé. Avant la première
version stable, seule la branche de développement est maintenue.
