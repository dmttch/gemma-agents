# Configuration


| Variable | Valeur par défaut |
| --- | --- |
| `AGENT_MODEL` | modèle de `config.toml` enregistré par `setup`, sinon vide |
| `OLLAMA_HOST` | `http://localhost:11434` |
| `AGENT_WORKSPACE` | `./workspace` |
| `AGENT_STORAGE` | ancien stockage V1 s'il existe, sinon `~/.local/share/gemma-agents` |
| `AGENT_SKILLS` | `$AGENT_STORAGE/skills` |
| `AGENT_EMBEDDING_MODEL` | `embeddinggemma` |
| `AGENT_MAX_STEPS` | `20`, partagés avec les corrections automatiques |
| `AGENT_REPAIR_ATTEMPTS` | `2` |
| `AGENT_COMMAND_TIMEOUT` | `120` secondes |
| `AGENT_KEEP_ALIVE` | `15m` |
| `AGENT_CONTEXT_CHARS` | `60000` caractères JSON, estimation et non tokens |
| `AGENT_SANDBOX` | `required` (`off` est explicite) |
| `AGENT_SEARXNG_URL` | vide : outil Web absent |
| `AGENT_API_TOKEN` | vide : démarrage API refusé |

Les appels et arguments d'outils sont journalisés dans la table `audit` avec leur
résultat (`executed`, `denied`, `error`, `deferred`). `executed` indique que la fonction a été
appelée, pas qu'un sous-processus est sorti avec succès. L'historique et les audits
peuvent contenir des données de projet ; SQLite n'est pas chiffré.

Priorité du modèle : choix de session (`/model`), puis option globale `--model`,
puis `AGENT_MODEL`, puis `$AGENT_STORAGE/config.toml`. `setup` ne télécharge aucun
modèle. Un nom absent ou un modèle sans capacité `tools` est refusé avant le tour.

`AGENT_MAX_STEPS` et `AGENT_COMMAND_TIMEOUT` doivent être positifs ;
`AGENT_REPAIR_ATTEMPTS` est compris entre 0 et 5 ; `AGENT_CONTEXT_CHARS` doit être
au moins 4000. Les permissions sont limitées à 1–168 heures (8 par défaut).

`OLLAMA_HOST` désigne un service auquel les prompts et extraits de fichiers sont
envoyés. Gardez-le local pour préserver l'usage local annoncé ; une adresse distante
transmet ces données à ce serveur. Un serveur local peut lui-même utiliser un
modèle hébergé : vérifiez sa configuration. Le sandbox des sous-processus n'isole
pas les requêtes HTTP du runtime vers Ollama ou SearXNG.
