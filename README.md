# Gemma Agents

<!-- Versions : .python-version et uv.lock ; contrainte uv_build dans pyproject.toml. -->
[![Python 3.14.3](https://img.shields.io/badge/Python-3.14.3-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3143/)[![Click 8.5.0](https://img.shields.io/badge/Click-8.5.0-blue)](https://pypi.org/project/click/8.5.0/)[![croniter 6.2.4](https://img.shields.io/badge/croniter-6.2.4-blue)](https://pypi.org/project/croniter/6.2.4/)[![FastAPI 0.141.1](https://img.shields.io/badge/FastAPI-0.141.1-009688?logo=fastapi&logoColor=white)](https://pypi.org/project/fastapi/0.141.1/)[![HTTPX 0.28.1](https://img.shields.io/badge/HTTPX-0.28.1-blue)](https://pypi.org/project/httpx/0.28.1/)[![Ollama 0.6.2](https://img.shields.io/badge/Ollama-0.6.2-000000?logo=ollama&logoColor=white)](https://pypi.org/project/ollama/0.6.2/)[![pathspec 1.1.1](https://img.shields.io/badge/pathspec-1.1.1-blue)](https://pypi.org/project/pathspec/1.1.1/)[![prompt-toolkit 3.0.53](https://img.shields.io/badge/prompt--toolkit-3.0.53-blue)](https://pypi.org/project/prompt-toolkit/3.0.53/)[![Pydantic 2.13.5](https://img.shields.io/badge/Pydantic-2.13.5-E92063?logo=pydantic&logoColor=white)](https://pypi.org/project/pydantic/2.13.5/)[![Rich 15.0.0](https://img.shields.io/badge/Rich-15.0.0-blue)](https://pypi.org/project/rich/15.0.0/)[![rich-click 1.9.9](https://img.shields.io/badge/rich--click-1.9.9-blue)](https://pypi.org/project/rich-click/1.9.9/)[![Uvicorn standard 0.53.0](https://img.shields.io/badge/Uvicorn%5Bstandard%5D-0.53.0-blue)](https://pypi.org/project/uvicorn/0.53.0/)[![pytest 9.1.1](https://img.shields.io/badge/pytest-9.1.1-0A9EDC?logo=pytest&logoColor=white)](https://pypi.org/project/pytest/9.1.1/)[![Ruff 0.16.8](https://img.shields.io/badge/Ruff-0.16.8-D7FF64?logo=ruff&logoColor=black)](https://pypi.org/project/ruff/0.16.8/)[![uv_build >=0.12.15,<0.13.0](https://img.shields.io/static/v1?label=uv_build&message=%3E%3D0.12.15%2C%3C0.13.0&color=DE5FE9&logo=uv&logoColor=white)](https://pypi.org/project/uv-build/)
[![Documentation](https://readthedocs.org/projects/gemma-agents/badge/?version=latest)](https://gemma-agents.readthedocs.io)

Agent de développement local pour **macOS Apple Silicon**, piloté par un modèle
Ollama installé. Il explore un projet, prépare ou applique des modifications,
exécute des validations approuvées et conserve les sessions dans SQLite.

**[Documentation complète → gemma-agents.readthedocs.io](https://gemma-agents.readthedocs.io)**

## Fonctionnalités

- Interface terminal interactive, streaming et reprise des conversations.
- Exploration du projet, instructions `AGENTS.md`, skills et mémoire facultative.
- Éditions journalisées, revue par blocs et annulation avec contrôle des conflits.
- Plans, tâches persistantes, validations et planification cron.
- Outils Git contrôlés, permissions expirantes et sandbox macOS.
- API HTTP/WebSocket locale et sauvegarde/restauration du stockage SQLite.

## Démarrage rapide

Prérequis : macOS Apple Silicon, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Python ≥ 3.14.3, Git et un serveur [Ollama](https://docs.ollama.com/quickstart)
local en cours d'exécution, avec un modèle installé prenant en charge les outils.
Aucun modèle n'est téléchargé par Gemma Agents.

```sh
git clone https://github.com/dmttch/gemma-agents.git
cd gemma-agents
uv sync --locked
uv run gemma-agents setup
uv run gemma-agents doctor
uv run gemma-agents
```

`setup` conserve le choix du modèle ; `doctor` vérifie les prérequis et le sandbox.
Pour lancer une première tâche dans un workspace dédié :

```sh
mkdir -p ~/Projects/gemma-demo
uv run gemma-agents --workspace ~/Projects/gemma-demo run \
  "Crée hello.txt contenant Bonjour, puis relis-le pour vérifier."
```

Le workspace par défaut est `./workspace`. Il doit être distinct du code du runtime,
du stockage et des skills. Les options globales comme `--workspace` se placent avant
la sous-commande. Dans le terminal interactif, `/help` présente les commandes et
`/review on` active la revue des éditions avant application.

## Documentation et contribution

La [documentation en ligne](https://gemma-agents.readthedocs.io) regroupe les guides
d'installation et d'utilisation, la configuration, les références CLI et API,
la sécurité, la gestion des données, le dépannage et les rapports de qualification.
Elle décrit aussi l'architecture et les procédures de développement et de validation.

Pour contribuer, consultez [CONTRIBUTING.md](CONTRIBUTING.md).
Les évolutions sont recensées dans le [changelog](CHANGELOG.md).

## Sécurité et licence

Le modèle peut modifier les fichiers du workspace et une commande approuvée peut
les supprimer : conservez vos sauvegardes. Le sandbox macOS repose sur
`sandbox-exec`, déprécié par Apple ; le projet n'a pas fait l'objet d'un audit de
sécurité contre du code hostile. L'API locale ne doit pas être exposée à Internet.
Consultez [SECURITY.md](SECURITY.md) et la documentation avant utilisation.

Distribué sous licence [Apache-2.0](LICENSE). Les modèles et dépendances conservent
leurs propres licences.
