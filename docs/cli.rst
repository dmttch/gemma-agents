Référence CLI
=============

Cette page est produite à partir des commandes Click du paquet installé.
Les descriptions de référence des commandes sont en anglais, comme les docstrings ;
les guides et messages opérateur sont en français.

Sorties et codes de retour
------------------------------

``--json`` produit un objet JSON par résultat sur stdout. La progression va sur
stderr. Aucune approbation n'est demandée en mode JSON : seules les permissions
exactes déjà accordées peuvent autoriser une action sensible. Le worker continu
émet une ligne JSON par passage ; utilisez ``worker --once`` pour un seul passage.

.. list-table::
   :header-rows: 1

   * - Code
     - Signification pour ``run`` et ``tasks run``
   * - 0
     - Tour terminé ; consulter aussi ``verification``.
   * - 1
     - Échec ou erreur opérationnelle.
   * - 2
     - Erreur de syntaxe CLI.
   * - 3
     - Action bloquée ou workspace déjà occupé.
   * - 4
     - Budget épuisé ou absence de progrès.
   * - 130
     - Interruption du runtime ou Ctrl+C pendant une commande.

Les erreurs de syntaxe traitées directement par Click restent sur stderr, même
avec ``--json``. Ctrl+C suit la gestion d'interruption de Click ; une conversation
interactive conserve sa session. ``completed`` signifie que le tour est terminé,
pas que le code produit est correct. Les critères explicites d'une tâche sont
exécutés après la réponse du modèle.

.. click:: gemma_agents.main:main
   :prog: gemma-agents
   :nested: full
