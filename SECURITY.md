# Sécurité

La cible prise en charge est un utilisateur unique sur macOS Apple Silicon,
travaillant dans un workspace local de confiance. Les commandes passent par
Seatbelt ; l'API est locale et authentifiée. L'isolation repose sur `sandbox-exec`,
un mécanisme Apple déprécié : elle n'est pas une frontière auditée contre du code
hostile. N'exposez pas l'API à Internet et n'exécutez pas de dépôt inconnu sans
isolation supplémentaire telle qu'une VM.

Le modèle peut lire et modifier les fichiers autorisés du workspace. Les
commandes approuvées peuvent les supprimer. Le journal d'annulation ne couvre
pas les effets des commandes ou de Git. Instructions de projet, pages Web et
contenus des fichiers peuvent être trompeurs ; ils n'accordent aucune permission.

Les détails sont dans [le guide de sécurité](docs/security.md) et
[la maintenance des données](docs/storage.md).

Signalez une vulnérabilité en privé à **dmttch@proton.me**, sans joindre de secrets
ni de données personnelles. Indiquez version, macOS, étapes de reproduction et
impact observé. Aucun délai de résolution garanti n'est annoncé. Les correctifs
de sécurité ciblent la dernière version stable ; avant celle-ci, la branche de
développement est la seule branche maintenue.
