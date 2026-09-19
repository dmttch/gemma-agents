# Préparer et publier une version

## Contrôles avant publication

1. Exécuter la CI et les six scénarios réels répétés sur un Mac pris en charge.
2. Examiner les rapports, migrations, changements de comportement et limites.
3. Relire README, guides, aide CLI, référence Python et changelog après construction.
4. Vérifier l'installation du wheel dans un environnement propre avec
   `uv run scripts/smoke_wheel.py CHEMIN_DU_WHEEL`.
5. Tester sauvegarde, mise à niveau depuis les schémas antérieurs et restauration.
6. Aligner version du paquet et changelog ; la CLI et l'API lisent la version du paquet.

Le workflow manuel `release.yml` construit wheel, sdist et documentation après les
contrôles. Il conserve les artefacts ; il ne publie rien automatiquement. Une
version candidate peut être distribuée avant qualification, explicitement comme
telle. Ne pas annoncer une version stable si une étape de qualification manque.

## Publication du paquet

Le mainteneur doit posséder le projet PyPI et choisir son mécanisme d'authentification.
Après validation des artefacts :

```sh
uv publish dist/gemma_agents-VERSION-py3-none-any.whl dist/gemma_agents-VERSION.tar.gz
```

Remplacer `VERSION` par la version contrôlée. Une publication de version sur PyPI
est une action externe ; les identifiants restent hors du dépôt. Publier ensuite
les notes de version et le tag correspondant depuis la même révision validée.

## Read the Docs

Le dépôt fournit `.readthedocs.yaml` : Python 3.14, installation native avec uv et
groupe `docs`, puis construction Sphinx stricte. `uv.lock` est utilisé. Aucune
installation Ollama ni variable secrète n'est nécessaire à la documentation.

Dans le compte Read the Docs du mainteneur, importer le dépôt GitHub, autoriser
son accès si nécessaire, lancer un premier build puis sélectionner la branche et
les tags à exposer. Confirmer l'URL réelle avant d'ajouter un badge ou un lien
public dans le README. Activer `stable` seulement pour un tag qualifié.

Le build local valide les mêmes sources ; il ne prouve pas qu'un projet a été
créé ou publié chez Read the Docs. La connexion du service reste une opération
distincte, exigeant l'accès au compte et au dépôt.

## Gestion d'une régression

Arrêter les runtimes et conserver les fichiers du projet. Sauvegarder le stockage
actuel. Restaurer un snapshot compatible avant réinstallation d'une ancienne
version, puis vérifier `doctor` et une tâche minimale. Les écritures de l'agent
demandent une inspection du workspace ; revenir au binaire précédent ne les annule pas.
