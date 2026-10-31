# Procédure de reprise (v2, phase 5)

Que faire quand quelque chose se passe mal. Du plus léger au plus lourd. Rien de ce qui suit
n'envoie de message à un client.

## 1. Arrêter vite (moins d'une minute)
- Page **Arrêt d'urgence** : couper un canal (mail, WhatsApp…) ou un département entier. Les actions
  sont bloquées, les validations refusées, les missions du département se mettent en pause.
- Un agent précis : page **Supervision**, le suspendre.

## 2. Comprendre
- **Centre de commande** : incidents des 3 derniers jours, tâches planifiées en échec, coûts du mois.
- **Journal** : chaque action est tracée avec l'agent, la décision et la personne.
- **Rapport de mission** : sur la page d'une mission, tâches faites, bloquées, essais, coût, preuves.

## 3. Reprendre
- Levée de l'arrêt depuis la même page. Les missions en pause reprennent à l'état conservé.
- Une tâche en échec peut être relancée (3 essais maximum) ou annulée.
- L'IA indisponible (crédit épuisé, plafond atteint) n'arrête pas le reste : le message est mis
  « à traiter à la main » et une seule alerte part. Recharger le crédit puis « Retraiter » dans la boîte.

## 4. Revenir en arrière (code)
Sur le serveur, dans `/opt/ibig-ia` :
1. `git log --oneline -5` pour repérer le dernier commit sain.
2. `git checkout <commit>` puis `docker compose up -d --build agent`.
3. Vérifier : conteneurs « Up », aucune ligne « Traceback » dans `docker compose logs agent`, site 200.
Les migrations sont additives : un retour arrière du code reste compatible avec la base.

## 5. Restaurer les données
- Sauvegardes chiffrées dans `/var/backups/ibig-agent/` (`deploy/sauvegarde.sh`).
- Déchiffrer puis restaurer sur une COPIE d'abord ; ne jamais restaurer sur la base vive sans
  accord écrit de la direction.
- Copie hors serveur : `docs/sauvegarde-externe.md`.

## 6. Après l'incident
Noter ce qui s'est passé, la cause, le temps d'arrêt et le correctif. Le centre de commande compte
les incidents ; une cause qui revient est à corriger dans le code, pas à contourner.
