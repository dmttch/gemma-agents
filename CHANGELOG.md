# Changelog

## Non publié — préparation de la version 1.0

- Verrou interprocessus du workspace et exclusion des restaurations concurrentes.
- Codes de sortie fiables, mode JSON et diagnostic des prérequis.
- Sélection persistante d'un modèle installé compatible avec les outils.
- Découverte des exécutables uv/Git hors du workspace.
- Migrations SQLite ordonnées avec sauvegarde préalable et refus des schémas futurs.
- Sauvegarde/restauration, révocation des permissions restaurées et maintenance des sessions/audits.
- Résultats d'outils distinguant explicitement un échec métier d'un succès.
- Contrats de modèles injectables, tests de concurrence et récupération.
- Documentation Sphinx, référence CLI/Python et configuration Read the Docs.
- CI, installation du wheel en environnement propre et évaluations répétées.
- Licence Apache-2.0.

### Changements à prendre en compte

Le modèle par défaut n'est plus un nom propre à la machine de développement :
utiliser `setup`, `--model` ou `AGENT_MODEL`. Une seconde instance sur le même
workspace est refusée, même avec un stockage différent. Un script doit maintenant
traiter les codes non nuls de `run` et `tasks run`.

## 0.4.0

Interventions pendant le travail, recherche de projet, revue partielle,
permissions expirantes et correction après échec des validations.

## 0.3.0

Interface terminal, streaming, checkpoints, journal des éditions et validations.

## 0.2.0

Runtime local, mémoire, skills, tâches persistantes, cron et API locale.
