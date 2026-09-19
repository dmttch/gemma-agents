# Évaluation avec un vrai modèle

## Banc d'acceptation

```sh
uv sync --locked
uv run --locked examples/evaluate_release.py \
  --model gemma4:12b-mlx --repetitions 3 \
  --output /tmp/gemma-acceptance.json
```

Le nom ci-dessus est celui du modèle de développement ; il doit être remplacé
par le nom exact installé sur votre machine. Aucun téléchargement n'a lieu.
Les métadonnées `tools` sont vérifiées avant le travail. Le rapport décrit la
machine, sa mémoire, Python, le paquet, le digest du modèle, le serveur et les
empreintes du code. Un résultat par scénario est écrit immédiatement afin de
conserver les mesures même si un scénario ultérieur échoue.

| Scénario | Critère observé |
| --- | --- |
| `repair` | Corriger une addition, conserver les tests et obtenir pytest vert |
| `multiple_files` | Produire deux fichiers dont le contenu est contrôlé |
| `interrupted` | Reprendre après annulation sans dupliquer l'édition initiale |
| `invalid_tool` | Se remettre d'un appel aux arguments invalides |
| `python_project` | Corriger un paquet CSV/Decimal, préserver les tests et documenter le format |
| `long_conversation` | Utiliser les fichiers actuels après une longue conversation condensée |

Les scénarios utilisent des workspaces et stockages temporaires. Seule la commande
de test exacte reçoit une permission temporaire. Les métriques incluent durée,
appels d'outils et corrections automatiques. Il n'y a pas d'intervention humaine
pendant l'essai. Un échec retourne un code non nul et reste présent dans le rapport.

Pour diagnostiquer un cas isolé, ajoutez `--scenario python_project --repetitions 1`.
Les scripts historiques `evaluate.py` et `evaluate_v4.py` restent disponibles pour
comparer les scénarios précédents ; le banc de release constitue le contrôle complet.

## Critères de qualification

Avant une version stable, exiger un rapport complet, sans échec, sur toutes les
répétitions et les six scénarios, plus la suite déterministe et les sondes macOS.
Archiver aussi les échecs lors du diagnostic, sans sélectionner uniquement les
essais favorables. Une augmentation de budget doit être déclarée dans le rapport.

Ce banc est limité à de petits projets. Il ne mesure pas une capacité générale à
résoudre tous les dépôts, ne garantit pas l'absence d'hallucinations et ne remplace
pas la revue du code produit. Les résultats ne se transposent pas automatiquement
à un autre modèle ou à une autre configuration.

Les rapports de qualification conservés dans `docs/evidence/` et leur synthèse
indiquent précisément ce qui a été exécuté pour cette livraison.
