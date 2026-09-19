# API HTTP et WebSocket


```sh
export AGENT_API_TOKEN="$(openssl rand -hex 32)"
gemma-agents serve
# Ajouter --scheduler pour traiter aussi les tâches et les échéances.
```

L'API écoute seulement sur `127.0.0.1:8765`. Elle exige un jeton Bearer d'au moins
24 caractères, y compris sur le WebSocket. Les requêtes portant un en-tête Origin
sont refusées : l'API vise des clients locaux en CLI, pas une interface navigateur.
N'expose pas le serveur sur Internet. Il n'a ni utilisateurs multiples ni quotas.

```sh
curl -H "Authorization: Bearer $AGENT_API_TOKEN" \
  -X POST http://127.0.0.1:8765/sessions
curl -H "Authorization: Bearer $AGENT_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Liste les fichiers du workspace."}' \
  http://127.0.0.1:8765/sessions/IDENTIFIANT/messages
```

| Route | Usage |
| --- | --- |
| `GET /health` | Version et état de configuration du sandbox |
| `GET /openapi.json` | Schéma des routes HTTP, authentifié |
| `GET /sessions` | Sessions du workspace |
| `POST /sessions` | Nouvelle session |
| `GET /sessions/{id}/messages` | Historique |
| `POST /sessions/{id}/messages` | Un tour, réponse finale HTTP |
| `WS /sessions/{id}/ws` | Un tour avec événements intermédiaires |
| `GET /approvals` | Appels sensibles en attente |
| `POST /approvals/{id}` | `{"approved": true}` ou `false` |
| `GET /tasks`, `POST /tasks` | Lister / créer avec `{"prompt":"…","checks":["uv run pytest"]}` (`checks` facultatif) |
| `POST /tasks/{id}/run` | Exécuter sans approbation interactive |
| `GET /schedules` | Lire les planifications créées en CLI |

Un appel HTTP de conversation attend sa fin ; s'il demande une approbation, consulte
`/approvals` depuis une seconde requête. Chaque approbation porte sur un seul appel
et expire après 120 secondes. N'approuve que des arguments effectivement inspectés.
Une déconnexion WebSocket refuse les approbations en attente ; un appel Ollama déjà
en cours peut aller jusqu'à son timeout avant l'arrêt du tour.

Le WebSocket reçoit `{"prompt":"…"}` puis émet `step`, `tool_request`, `tool_result`,
`warning`, `context`, `token`, `final` ou `error`, avant fermeture. `token` contient
les fragments de réponse ; `context` indique la taille et les condensations du
contexte. Les clients doivent ignorer les types d’événements inconnus.
Exemple : [client WebSocket](https://github.com/dmttch/gemma-agents/blob/main/examples/ws_client.py).
Un seul tour est accepté à la fois par runtime ; un second appel HTTP reçoit `409`.
Les paramètres invalides donnent `400` ou `422`, une authentification absente `401`.

