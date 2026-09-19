# Gemma Agents

Agent de développement local pour **macOS Apple Silicon**, piloté par un modèle
Ollama installé. Il explore un projet, prépare ou applique des modifications,
exécute des validations approuvées et conserve les sessions dans SQLite.

**État : préparation de la version 1.0.** Le paquet conserve la version 0.4.0
pendant la qualification. Les résultats mesurés et les limites sont documentés ;
la présence d'une CI et d'une configuration Read the Docs ne signifie pas qu'une
release a déjà été publiée.

## Installation

Prérequis : macOS Apple Silicon, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Python ≥ 3.14.3, Git et un serveur [Ollama](https://docs.ollama.com/quickstart)
local en cours d'exécution. Le modèle choisi doit être installé et déclarer le
support des outils. Le besoin en RAM dépend du modèle ; aucun modèle n'est téléchargé
par Gemma Agents.

```sh
git clone https://github.com/dmttch/gemma-agents.git
cd gemma-agents
uv sync --locked
uv run gemma-agents setup
uv run gemma-agents doctor
uv run gemma-agents
```

`setup` vérifie et conserve le choix du modèle. `doctor` contrôle la plateforme,
les exécutables, le lancement réel du sandbox et la compatibilité du modèle ;
un prérequis manquant produit un code de sortie non nul.

Pour installer le paquet dans un environnement isolé :

```sh
uv build
uv tool install --python 3.14 ./dist/gemma_agents-0.4.0-py3-none-any.whl
gemma-agents setup
gemma-agents doctor
```

Les exemples suivants utilisent cette installation. Depuis les sources, préfixez
les commandes `gemma-agents` par `uv run`.

## Première utilisation

```sh
mkdir -p ~/Projects/gemma-demo
gemma-agents --workspace ~/Projects/gemma-demo run \
  "Crée hello.txt contenant Bonjour, puis relis-le pour vérifier."
```

`--workspace`, `--model` et `--json` sont des options globales : elles se placent
avant la sous-commande. Le workspace par défaut est `./workspace` ; il doit être
distinct du code du runtime, du stockage et des skills.

En mode interactif, `/help` présente les commandes. `/review on` prépare les
éditions avant application, `/review` permet de sélectionner les changements,
`/diff` les inspecte et `/undo` annule une édition si le fichier n'a pas changé.
Ce mode de revue doit être réactivé après redémarrage.

Une seule instance peut ouvrir un workspace. Le verrou fonctionne entre terminal,
worker et serveur, même si leurs stockages diffèrent, et se libère après un crash.
Les éditeurs et autres programmes ne prennent pas ce verrou.

## Tâches vérifiées

Préinstallez les dépendances du projet avec `uv sync --locked` dans votre terminal.
Les commandes de l'agent sont hors ligne et n'utilisent pas votre cache uv personnel.

```sh
gemma-agents --workspace ~/Projects/mon-projet tasks add \
  "Corrige le parseur" --check "uv run pytest -q"
gemma-agents --workspace ~/Projects/mon-projet tasks run IDENTIFIANT
```

Remplacez `IDENTIFIANT` par l'identifiant retourné. Les validations sont exécutées
après la réponse du modèle. Les corrections automatiques partagent le budget du
tour initial. Sans critères, `completed` signifie seulement que le tour est terminé.

Pour un script :

```sh
gemma-agents --json --workspace ~/Projects/mon-projet run "Inspecte le projet"
```

Le résultat JSON va sur stdout, la progression sur stderr. Aucune approbation
interactive n'est demandée en mode JSON. Les codes principaux sont `0` (terminé),
`1` (échec), `2` (syntaxe), `3` (bloqué ou occupé), `4` (limite), `130` (interruption).
Les permissions persistées autorisent uniquement un programme et ses arguments
exacts, pendant leur durée de validité.

## Fonctions disponibles

- Conversation terminal, streaming, interventions pendant le travail et reprise.
- Exploration du projet, références `@fichier`, instructions `AGENTS.md` et skills.
- Éditions journalisées, revue par blocs et annulation avec contrôle des conflits.
- Plans, checkpoints, tâches, validations, permissions expirantes et planification cron.
- Mémoire sémantique facultative, processus bornés et outils Git contrôlés.
- API HTTP/WebSocket locale avec jeton Bearer ; recherche Web facultative via SearXNG.
- Sauvegarde/restauration SQLite, export/suppression de sessions et purge explicite des audits.

## Sécurité et données

Le sandbox macOS refuse le réseau des processus et limite leurs accès aux fichiers.
Les actions sensibles requièrent une approbation ou une permission exacte.
L'API écoute sur `127.0.0.1` et ne doit pas être exposée à Internet.

Le modèle peut modifier les fichiers du workspace ; une commande approuvée peut
les supprimer. Le journal d'annulation ne couvre pas les effets des commandes ou
de Git. `sandbox-exec` est un mécanisme Apple déprécié, sans audit de sécurité du
projet contre du code hostile. Utilisez un workspace de confiance et conservez
vos sauvegardes. Voir [SECURITY.md](SECURITY.md) et [le guide de sécurité](docs/security.md).

Le stockage est `~/.local/share/gemma-agents`, ou `AGENT_STORAGE` explicite ;
un ancien stockage adjacent au paquet reste reconnu. SQLite n'est pas chiffré.
Les migrations préservent les données, sauvegardent avant mise à niveau et
refusent les schémas futurs.

```sh
mkdir -p ~/Backups/gemma
gemma-agents storage backup ~/Backups/gemma/avant-maj.db
# Fermer toutes les instances utilisant ce stockage avant restauration.
gemma-agents storage restore ~/Backups/gemma/avant-maj.db
```

Une sauvegarde SQLite ne contient ni les fichiers du projet, ni les skills, ni
`config.toml`. Une restauration révoque les permissions enregistrées et conserve
une copie de secours de la base remplacée. Voir [maintenance des données](docs/storage.md).

## Documentation

La documentation Sphinx comprend guides, configuration, commandes générées depuis
Click, architecture et référence Python générée depuis les docstrings.

| Besoin | Guide |
| --- | --- |
| Installer et démarrer | [Installation](docs/installation.md) |
| Utiliser la conversation et la revue | [Terminal](docs/terminal.md) |
| Mémoire, skills, tâches et cron | [Tâches](docs/tasks.md) |
| Configurer les limites et services | [Configuration](docs/configuration.md) |
| Intégrer un client local | [API HTTP/WebSocket](docs/api.md) |
| Comprendre les protections | [Sécurité](docs/security.md) |
| Sauvegarder, restaurer, supprimer | [Stockage](docs/storage.md) |
| Comprendre et contribuer au code | [Architecture](docs/architecture.md), [développement](docs/development.md) |
| Évaluer et livrer | [Évaluation](docs/evaluation.md), [release](docs/release.md) |
| Résoudre un problème | [Dépannage](docs/troubleshooting.md) |

```sh
uv sync --locked --group docs
uv run --locked --group docs sphinx-build -W --keep-going -b html docs docs/_build/html
open docs/_build/html/index.html
```

La configuration [Read the Docs](.readthedocs.yaml) utilise uv et le groupe `docs`.
L'activation du projet hébergé est expliquée dans le guide de release ; aucune URL
publique n'est annoncée tant que le service n'a pas été raccordé.

## Développement et validation

```sh
uv run --locked pytest -q
uv run --locked ruff check src tests examples scripts docs/conf.py
uv build
uv run --locked scripts/smoke_wheel.py dist/gemma_agents-0.4.0-py3-none-any.whl
uv run --locked examples/evaluate_release.py --model gemma4:12b-mlx \
  --repetitions 3 --output /tmp/gemma-acceptance.json
```

Le nom `gemma4:12b-mlx` est celui du modèle de développement, pas une valeur imposée.
Les tests déterministes n'utilisent pas de modèle. Les tests d'isolation s'exécutent
réellement sur macOS ; la CI Linux vérifie seulement le code portable et les docs.
Le banc réel mesure six scénarios répétés et conserve les échecs dans son rapport.

Consultez [CONTRIBUTING.md](CONTRIBUTING.md), [le changelog](CHANGELOG.md) et
[les références officielles](docs/sources.md).

## Licence

[Apache-2.0](LICENSE), choisie par le mainteneur. Les modèles et dépendances
conservent leurs propres licences.
