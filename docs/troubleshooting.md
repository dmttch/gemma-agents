# Diagnostic et dépannage

| Symptôme | Vérification et action |
| --- | --- |
| Aucun modèle choisi | `gemma-agents setup`, puis `doctor` |
| Ollama inaccessible | Vérifier le serveur et `OLLAMA_HOST` ; `ollama list` doit fonctionner |
| Modèle sans outils | Choisir un modèle dont `/api/show` annonce `tools` |
| `sandbox_apply: Operation not permitted` | Lancer depuis un terminal macOS ordinaire ; un sandbox parent peut empêcher Seatbelt |
| `uv` introuvable | Installer uv, vérifier `command -v uv`, puis redémarrer le runtime |
| Dépendances absentes hors ligne | Exécuter `uv sync --locked` dans le projet depuis votre terminal |
| Workspace occupé | Fermer l'autre terminal, serveur ou worker ; un crash libère le verrou automatiquement |
| Schéma futur | Installer un runtime compatible, ou restaurer un snapshot compatible avec l'ancienne version |
| Tâche bloquée | Lire les outils et permissions ; accorder uniquement la commande exacte voulue |
| Tâche `running` après un crash | Inspecter les fichiers, puis `tasks retry IDENTIFIANT` |
| Contexte trop volumineux | Réduire les références, utiliser un checkpoint, ajuster prudemment `AGENT_CONTEXT_CHARS` |
| Revue ou undo refusé | Le fichier a changé ; comparer manuellement, préserver les modifications externes |
| API `409` | Attendre la fin du tour en cours ; un seul tour par runtime |
| API `401` | Vérifier le Bearer et l'absence d'en-tête Origin |

`AGENT_SANDBOX=off` est une désactivation explicite, réservée aux environnements
de confiance. Elle ne résout pas une qualification de sécurité : `doctor` continue
de signaler l'absence d'isolation. Ne l'utilisez pas comme solution automatique
à une erreur Seatbelt.

Pour signaler un problème, joindre la version, macOS/architecture, le modèle,
le diagnostic et une reproduction minimale. Retirez les contenus privés des
historiques, chemins et sorties. Voir [le guide de sécurité](security.md)
pour les signalements de sécurité.
