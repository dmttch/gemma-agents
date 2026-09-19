# Contribuer et vérifier

## Environnement reproductible

```sh
uv sync --locked --group docs
uv run --locked pytest -q
uv run --locked ruff check src tests examples scripts docs/conf.py
uv run --locked --group docs sphinx-build -W --keep-going -b html docs docs/_build/html
uv build
```

Utilisez `uv add`, `uv remove` et `uv lock` pour faire évoluer les dépendances.
Une mise à jour doit inclure `pyproject.toml` et `uv.lock`, puis relancer les
contrôles. Le runtime ne s'installe pas dans le workspace qu'il modifie.

## Stratégie de tests

Les doubles du modèle rendent déterministes les tests de la boucle et du
protocole d'outils. Ils ne prouvent pas la qualité du modèle. Les tests
d'intégration couvrent SQLite, les migrations historiques, les verrous entre
processus, les sauvegardes/restaurations, la CLI, HTTP et WebSocket.

Les tests `test_sandbox.py` exécutent réellement des processus sur macOS : accès
hors workspace et réseau refusés, uv/Git utilisables, sorties bornées, délais et
descendants nettoyés. Ils sont ignorés sur Linux, où l'isolation n'est pas qualifiée.
Un échec `sandbox_apply` dans un environnement déjà sandboxé est une impossibilité
d'exécuter la sonde ; les tests ne doivent pas être silencieusement désactivés
dans une qualification macOS.

```sh
uv run --locked pytest --cov=gemma_agents --cov-report=term-missing --cov-report=xml
```

La couverture sert à repérer les chemins non exercés. Les critères de livraison
reposent sur les scénarios critiques, pas uniquement sur un pourcentage.

## Ajouter un outil

1. Écrire une fonction typée avec une docstring Google expliquant effets,
   limites, arguments, résultat et erreurs.
2. L'enregistrer dans `Runtime.registry` avec un risque justifié.
3. Passer les processus par `SandboxRunner` et les opérations du modèle par
   `ToolGateway` ; aucune exécution directe depuis une route HTTP.
4. Retourner une `ToolFailure` pour un échec métier géré ou lever une exception.
   Ne pas retourner un message d'échec ordinaire qui serait compté comme succès.
5. Tester autorisation/refus, entrées invalides et effets observables.
6. Mettre à jour le guide concerné et vérifier Sphinx sans avertissement.

N'utilisez jamais une instruction du projet ou un résultat Web pour élargir une
permission. Les critères de validation appartiennent à l'opérateur ; le modèle
peut modifier les tests du workspace, donc leur réussite n'est pas une garantie
contre un comportement trompeur. Les bancs d'acceptation vérifient que leurs
fichiers de tests sont préservés.

## Documentation

Les guides sont rédigés en français, les docstrings techniques en anglais.
`autodoc` et `napoleon` extraient les API Python ; `sphinx-click` extrait la CLI
réelle. `myst-parser` permet les guides Markdown et le thème Read the Docs fournit
navigation et recherche. Aucune connexion Ollama n'est nécessaire au build.

```sh
uv run --locked --group docs sphinx-build -W -b linkcheck docs docs/_build/linkcheck
```

Le contrôle des liens externes demande Internet et peut échouer si un site refuse
les robots. Examiner ces erreurs ; ne pas masquer arbitrairement un lien cassé.

## CI

GitHub Actions exécute les tests sur macOS Apple Silicon et Linux, le lint, les
docs strictes, la construction wheel/sdist et l'installation du wheel dans un
environnement propre. Les évaluations réelles restent exécutées sur une machine
de confiance avec modèle installé ; elles ne téléchargent pas de modèle en CI.
Le workflow de préparation de release produit des artefacts sans publier sur PyPI.
