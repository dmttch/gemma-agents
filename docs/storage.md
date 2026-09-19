# Données, sauvegarde et récupération

## Ce qui est conservé

SQLite contient les sessions, messages, plans, checkpoints, tâches, échéances,
permissions, validations, audits et versions avant/après des éditions. Les fichiers
du workspace et les fichiers de skills sont séparés. Le fichier `config.toml`
contient le modèle sélectionné par `setup`.

Le stockage est `~/.local/share/gemma-agents`, sauf `AGENT_STORAGE` explicite ou
ancien `storage/agent.db` adjacent au paquet. Une nouvelle arborescence de stockage
est créée privée (`0700`), la base et les snapshots utilisent `0600`.
La base n'est pas chiffrée. Les sauvegardes peuvent contenir du code, des secrets
présents dans une conversation et d'anciennes versions de fichiers.

## Sauvegarder

```sh
mkdir -p ~/Backups/gemma
gemma-agents storage backup ~/Backups/gemma/avant-maj.db
```

Le répertoire destination doit exister et être hors du workspace sélectionné.
Un fichier existant n'est jamais remplacé. L'API de sauvegarde SQLite inclut les
transactions validées encore présentes dans le WAL. Une sauvegarde peut se faire
pendant qu'un runtime travaille ; elle représente un instant cohérent de la base,
pas une transaction commune avec les fichiers du projet.

Sauvegardez séparément vos workspaces, skills et `config.toml`. Aucun mécanisme
n'effectue une sauvegarde automatique permanente de l'ensemble de ces données.

## Restaurer

```sh
gemma-agents storage restore ~/Backups/gemma/avant-maj.db
```

La commande demande confirmation. Pour un script :

```sh
gemma-agents --json storage restore ~/Backups/gemma/avant-maj.db --yes
```

Fermez **toutes** les instances utilisant ce stockage, même celles d'autres
workspaces : un verrou exclusif refuse la restauration sinon. La restauration
concerne toute la base. Elle vérifie l'intégrité, conserve une copie de secours de
la base actuelle, prépare le remplacement puis le publie atomiquement.

Les permissions restaurées sont supprimées. Les tâches et runs restés `running`
deviennent `interrupted`. Les planifications restent configurées : inspectez-les
avant de relancer un worker. Les fichiers du projet ne sont pas remis à leur état
historique ; inspectez leurs effets avant toute reprise.

## Versions de schéma

Les migrations ordonnées 1 à 4 reconstruisent les schémas historiques ; la version
5 ajoute des index. Une ancienne base sans numéro, contenant les sessions/messages,
est prise en charge. Toute version future est refusée sans réécriture du numéro.
Chaque mise à niveau est transactionnelle et laisse un fichier
`agent.db.vVERSION-IDENTIFIANT.backup` avant modification. En cas d'échec, la
transaction revient à son état antérieur. Conservez les snapshots jusqu'à la
validation de la mise à jour ; leur suppression relève de l'opérateur.

Pour revenir à un ancien runtime, restaurez d'abord un snapshot compatible avec
son schéma. Ne forcez jamais manuellement `PRAGMA user_version`.

## Suppression et conservation

```sh
gemma-agents session export IDENTIFIANT ~/Backups/gemma/conversation.md
gemma-agents session delete IDENTIFIANT
gemma-agents prune-audit --days 90
gemma-agents prune-audit --days 90 --apply
```

La suppression de session retire sa conversation, ses éditions enregistrées,
validations, tâches associées et permissions. Elle préserve les fichiers et les
souvenirs sémantiques du workspace. Les sessions dont l'exécution est signalée
active sont refusées : inspectez et reprenez-les avant suppression.

`prune-audit` compte par défaut ; `--apply` supprime seulement les audits anciens
du workspace sélectionné. Il n'existe pas de purge automatique des conversations
ou éditions, afin de préserver la reprise et l'annulation. La suppression est
logique : elle n'efface pas les anciennes sauvegardes ni les blocs du disque.

## Après un crash

Le noyau libère automatiquement les verrous lorsque le processus meurt. Il n'y a
pas de fichier de verrou à effacer. Les commandes déjà effectuées peuvent avoir
modifié le projet. Consultez historique, diff, checkpoints et fichiers actuels,
puis reprenez explicitement la session ou utilisez `tasks retry IDENTIFIANT`.
Les effets inconnus ne sont jamais rejoués automatiquement par le runtime.
