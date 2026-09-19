# Mémoire et tâches

## Mémoire sémantique

La mémoire utilise les embeddings Ollama et une recherche cosinus dans SQLite.
Aucun service vectoriel externe n'est nécessaire. Installe le modèle d'embeddings
une fois si tu souhaites utiliser cette fonction :

```sh
ollama pull embeddinggemma
gemma-agents memory add "Ce projet utilise Python, uv et pytest."
gemma-agents memory search "Comment lancer les tests Python ?"
gemma-agents memory forget IDENTIFIANT
```

Les outils `remember`, `recall` et `forget` sont accessibles à l'agent. Les souvenirs
sont séparés par workspace **et** modèle d'embeddings. Changer le modèle ne réindexe
pas les anciens souvenirs. Si des souvenirs existent mais que le modèle d'embeddings
est absent, la conversation continue avec un avertissement ; la recherche explicite
renvoie une erreur. La recherche est linéaire et convient à une petite mémoire locale.

## Skills et plans

Les skills sont des instructions Markdown installées par l'utilisateur dans
`$AGENT_STORAGE/skills/<nom>/SKILL.md` (ou `AGENT_SKILLS`). Ils sont à l'extérieur du
workspace et ne peuvent pas accorder de permissions supplémentaires.

Exemple fourni : [examples/skills/python-project/SKILL.md](https://github.com/dmttch/gemma-agents/blob/main/examples/skills/python-project/SKILL.md).
Installation avec le stockage par défaut :

```sh
mkdir -p ~/.local/share/gemma-agents/skills/python-project
cp examples/skills/python-project/SKILL.md \
  ~/.local/share/gemma-agents/skills/python-project/SKILL.md
gemma-agents skills
```

L'agent découvre les skills avec `list_skills`, les lit avec `read_skill`, et suit
son travail avec `set_plan`, `get_plan` et `update_plan`. Les plans persistent par
session. Le planner est guidé par le modèle : il ne lance pas d'autres agents.

## Tâches et planification

```sh
gemma-agents tasks add "Inspecte les fichiers et produis un rapport."
gemma-agents tasks list
gemma-agents tasks run IDENTIFIANT
gemma-agents schedule add "0 9 * * 1-5" \
  "Liste les fichiers du projet et résume son organisation." --timezone Europe/Paris
gemma-agents schedule list
gemma-agents worker
```

`tasks run` est interactif et peut demander une approbation. Le worker traite les
tâches en attente et les échéances sans approbateur : une action sensible refusée
entraîne un statut `blocked`, même si le modèle prétend avoir réussi.

- États : `pending`, `running`, `completed`, `blocked`, `failed`, `limited`, `interrupted`.
- Une tâche est réclamée atomiquement avant exécution ; elle utilise une nouvelle
  session à sa première exécution, dont l'identifiant est enregistré dans la tâche.
  Une relance réutilise cette session et demande au modèle d'inspecter les effets
  précédents avant de poursuivre. Ctrl+C produit `interrupted` et libère le runtime.
- Le worker doit rester ouvert. Aucun cron système, LaunchAgent ou démarrage
  automatique n'est installé sur le Mac.
- Les échéances sont conservées en UTC et calculées dans le fuseau choisi. Après un
  arrêt prolongé, une seule tâche est créée par planification échue, sans rejouer
  toutes les occurrences manquées.
- Après une interruption brutale, une tâche peut rester `running`. Examine ses
  effets avant `gemma-agents tasks retry IDENTIFIANT` : les modifications
  déjà effectuées ne sont pas annulées. La reprise conserve l'historique mais ne
  garantit pas une exécution exactement une fois : le modèle doit inspecter l'état.
- `gemma-agents worker --once` traite la file une fois.
- `gemma-agents schedule pause IDENTIFIANT` suspend une planification ;
  ajouter `--resume` la réactive.

La fin d'un tour sans outil signifie que le modèle estime avoir terminé. Le runtime
n'en fait pas une preuve de correction : les résultats des vérifications restent
à consulter. Une seule instance peut ouvrir le workspace ; un verrou interprocessus refuse la seconde.

