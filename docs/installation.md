# Installation et première tâche

## Périmètre pris en charge

La cible de livraison est **macOS Apple Silicon**, sur un disque local, pour un
utilisateur unique. Linux sert à construire la documentation et à tester le code
portable en CI ; cela ne constitue pas une prise en charge du sandbox Linux.
Mac Intel et Windows ne font pas partie du périmètre qualifié.

Prérequis : `uv`, Python ≥ 3.14.3, Git et un serveur Ollama local proposant un
modèle **installé** dont les métadonnées déclarent la capacité `tools`. Aucune
commande de Gemma Agents ne télécharge automatiquement un modèle.

Consultez les procédures officielles pour [uv](https://docs.astral.sh/uv/getting-started/installation/)
et [Ollama](https://docs.ollama.com/quickstart). Le besoin en RAM dépend du modèle,
de sa quantification et du contexte. Les mesures disponibles sont dans
[Évaluation](evaluation.md) ; elles ne constituent pas une garantie pour un autre modèle.

## Depuis les sources

```sh
git clone https://github.com/dmttch/gemma-agents.git
cd gemma-agents
uv sync --locked
uv run gemma-agents setup
uv run gemma-agents doctor
uv run gemma-agents
```

`setup` liste les modèles installés, vérifie leur support des outils puis conserve
le choix dans `$AGENT_STORAGE/config.toml`. Le modèle d'embeddings reste facultatif
si vous n'utilisez pas la mémoire sémantique. `doctor` lance une sonde bénigne dans
le sandbox et vérifie la présence des outils. Son code de sortie vaut `1` si un
prérequis obligatoire manque ; la présence du binaire `sandbox-exec` ne suffit pas.

## Installer le paquet construit

```sh
uv build
uv tool install --python 3.14 ./dist/gemma_agents-0.4.0-py3-none-any.whl
gemma-agents --version
gemma-agents setup
gemma-agents doctor
```

Adaptez le nom du wheel à la version construite. L'installation avec `uv tool`
isole le runtime du projet sur lequel vous travaillez. Les exemples suivants
utilisent `gemma-agents` ; depuis les sources, préfixez par `uv run`.

La publication sur PyPI et l'hébergement Read the Docs demandent une opération
du mainteneur. La présence de leur configuration dans le dépôt n'implique pas
qu'une version soit déjà disponible sur ces services.

## Première tâche sans exécution de code

```sh
mkdir -p ~/Projects/gemma-demo
gemma-agents --workspace ~/Projects/gemma-demo run \
  "Crée hello.txt contenant Bonjour, puis relis-le pour vérifier."
```

Le workspace est le répertoire autorisé pour les fichiers du modèle. Les options
globales `--workspace`, `--model` et `--json` se placent avant la sous-commande.
Le workspace doit être distinct du runtime, de son stockage et des skills.

Pour commencer par approuver les éditions, lancez l'interface interactive,
entrez `/review on`, puis votre demande. `/review` permet d'appliquer les blocs
proposés. Ce mode doit être réactivé à chaque nouveau runtime.

## Faire exécuter les tests d'un projet

Installez vous-même les dépendances du projet avant le travail de l'agent : les
commandes sandboxées sont hors ligne et n'utilisent pas votre cache uv personnel.

```sh
cd ~/Projects/mon-projet
uv sync --locked
gemma-agents --workspace "$PWD" tasks add "Corrige le bug du parseur" \
  --check "uv run pytest -q"
gemma-agents --workspace "$PWD" tasks run IDENTIFIANT
```

Remplacez `IDENTIFIANT` par l'identifiant retourné. Une approbation concerne les
arguments exacts affichés. L'outil peut modifier les fichiers du workspace ;
conservez une copie ou un commit préalable de votre travail.

## Mise à jour

Fermez les runtimes, sauvegardez la base, installez le nouveau wheel avec
`uv tool install --force --python 3.14 CHEMIN_DU_WHEEL`, puis lancez `doctor`.
Les migrations conservent une sauvegarde avant changement de schéma. Une base
plus récente que le runtime est refusée. Voir [maintenance du stockage](storage.md).
