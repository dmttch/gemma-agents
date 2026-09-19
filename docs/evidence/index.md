# Rapports de qualification

Cette page décrit ce qui a réellement été exécuté avant d'étiqueter la version
1.0.0, sur quelle machine, avec quel modèle, et ce que les mesures ne prouvent
pas. Les rapports bruts sont conservés à côté d'elle, échecs compris.

## Matériel et logiciels mesurés

| Élément | Valeur |
| --- | --- |
| Machine | Apple Silicon (`arm64`), 24 Gio de mémoire |
| Système | macOS 27.0 |
| Python | 3.14.3 |
| Ollama | 0.34.2 |
| Modèle | `gemma4:12b-mlx`, empreinte `ded7a2735003…` |
| Paquet | `gemma-agents` 1.0.0 |
| Sources mesurées | `source_sha256` `6c3030607148…` |

Le champ `source_sha256` de chaque rapport couvre tous les fichiers `.py` et
`.md` du paquet. Il est identique dans les deux passages de qualification :
c'est la même implémentation qui a été mesurée deux fois. Le champ `package` y
vaut `0.4.0`, les passages ayant été exécutés avant le changement du numéro de
version ; ce numéro ne figure pas dans les sources et n'a donc aucun effet sur
le comportement mesuré.

## Passages de qualification

Chaque passage exécute les six scénarios trois fois, soit dix-huit essais, sans
aucune intervention humaine. Les deux passages totalisent trente-six essais.

| Rapport | Début (UTC) | Résultat | Durée cumulée | Refus | Corrections |
| --- | --- | --- | --- | --- | --- |
| [`acceptance-gemma4-12b-mlx-run1.json`](acceptance-gemma4-12b-mlx-run1.json) | 2026-09-19 15:37 | 18/18 | 13,6 min | 1 | 1 |
| [`acceptance-gemma4-12b-mlx-run2.json`](acceptance-gemma4-12b-mlx-run2.json) | 2026-09-19 15:51 | 18/18 | 15,6 min | 0 | 2 |

Durées observées par scénario, à titre indicatif :

| Scénario | Médiane | Minimum | Maximum | Appels d'outils |
| --- | --- | --- | --- | --- |
| `repair` | 31,6 s | 29,6 s | 34,6 s | 6 |
| `multiple_files` | 18,9 s | 16,7 s | 22,7 s | 4 à 5 |
| `interrupted` | 25,3 s | 22,8 s | 28,9 s | 5 à 7 |
| `invalid_tool` | 15,6 s | 12,8 s | 18,9 s | 2 à 3 |
| `python_project` | 172,1 s | 111,7 s | 234,4 s | 10 à 18 |
| `long_conversation` | 29,9 s | 27,0 s | 34,4 s | 3 à 4 |

Ces durées dépendent de la charge de la machine. Au cours d'une session
d'évaluation continue de plusieurs heures, elles ont doublé sur des scénarios
identiques : ne les lisez pas comme une performance garantie.

## Cas particuliers présents dans les rapports

Un essai de `python_project` se termine avec `status: blocked` et
`verification: passed`, un refus consigné dans `refusals`. Le modèle avait tenté
une commande hors permission, le refus a été respecté, le travail a été terminé
avec les outils autorisés et les commandes de validation de l'opérateur ont
réussi. Le runtime conserve `blocked` volontairement : un refus reste visible
pour l'opérateur, même lorsque le livrable est correct. Le banc juge le livrable
et consigne le refus, comme l'explique [le guide d'évaluation](../evaluation.md).

Trois essais de `python_project` ont demandé une correction automatique après
l'échec des validations. Dans chaque cas la boucle de correction a réparé le
code du projet et les mêmes commandes ont ensuite réussi.

## Diagnostic : ce que les passages ratés ont révélé

Ces rapports sont conservés parce qu'ils ont servi à corriger le produit. Ils ne
portent pas les mêmes sources que les passages de qualification ; leur
`source_sha256` le montre.

| Rapport | Sources | Résultat | Défaut révélé |
| --- | --- | --- | --- |
| [`01-checks-skipped-after-refusal.json`](diagnostic/01-checks-skipped-after-refusal.json) | `1bcc8cecf659…` | 17/18 | Une action refusée pendant le tour annulait les commandes de validation de l'opérateur, qui possèdent pourtant leur propre permission. La tâche renvoyait `not_run` au lieu du résultat observé. |
| [`02-repairs-skipped-after-refusal.json`](diagnostic/02-repairs-skipped-after-refusal.json) | `6cbf810a39b7…` | 16/18 | Les validations s'exécutaient, mais la correction automatique restait désactivée après un refus sans rapport avec le code. Le banc comptait aussi comme un échec un livrable correct obtenu après un refus récupéré. |
| [`03-decoded-view-and-step-budget.json`](diagnostic/03-decoded-view-and-step-budget.json) | `20914b200ca1…` | 14/18 | La vue décodée des lectures de fichiers entourait l'extrait de prose explicative, que le modèle a recopiée dans un fichier à créer. Le budget d'étapes par défaut, partagé avec les corrections, s'épuisait avant qu'une correction aboutisse. |

Le détail de chaque correction figure dans l'historique Git et dans
[le changelog](https://github.com/dmttch/gemma-agents/blob/main/CHANGELOG.md).

## Ce que ces rapports ne prouvent pas

Ils mesurent six scénarios sur de petits projets, avec un modèle et une machine
précis. Ils ne mesurent pas une capacité générale à traiter un dépôt quelconque,
ne garantissent pas l'absence d'hallucinations et ne remplacent pas la revue du
code produit. Les résultats ne se transposent pas à un autre modèle, à une autre
quantification ni à une autre machine : refaites le banc chez vous avant de vous
appuyer dessus, comme décrit dans [le guide d'évaluation](../evaluation.md).

Ils ne disent rien non plus de la sécurité face à du code hostile. Le sandbox
repose sur `sandbox-exec`, un mécanisme Apple déprécié qui n'a pas fait l'objet
d'un audit pour ce projet. Voir [le guide de sécurité](../security.md).
