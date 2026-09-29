# Architecture

## Choix techniques

Le cahier des charges (section 14) recommande n8n comme orchestrateur, avec en
alternative un « développement sur mesure ». Ce dépôt suit l'option **sur mesure en
Python**, pour trois raisons :

1. **Les règles de gouvernance sont dans le code et testées** : niveaux 1/2/3, bouton
   d'arrêt, contrôle des faits. Les critères bloquants R-01 à R-07 sont couverts par des
   tests automatiques qui tournent à chaque modification (GitHub Actions).
2. **Tout est versionné sur GitHub** : configuration, base de connaissances, prompts.
3. **Évolution vers un produit IBIG SOFT** (phase 4) facilitée.

n8n reste utilisable en complément pour des enchaînements simples (par exemple
recevoir un webhook et appeler l'API de l'agent).

| Brique | Choix |
|---|---|
| Modèle d'IA | Claude (API Anthropic) : `claude-haiku-4-5` pour le tri, `claude-opus-5-5` pour la rédaction |
| Langage | Python 3.11+ |
| Base | PostgreSQL + pgvector en production, SQLite en développement |
| Tableau de bord | FastAPI + gabarits HTML |
| Planification | APScheduler (relève 5 min, rapport 8 h, calendrier du lundi) |
| Déploiement | Docker Compose sur VPS Linux (4 Go de RAM minimum) |

## Base de données et migrations

Le schéma est défini dans `db.py` et versionné par des migrations Alembic
(`src/ibig_agent/migrations/versions`), appliquées au démarrage et par
`ibig-agent migrer`. Après une modification de `db.py` :

```bash
ibig-agent migrer --nouvelle "ajout du téléphone des prospects"   # génère la migration
# relire le fichier généré, puis le committer avec la modification de db.py
```

Un test (`test_migrations_match_models`) échoue si `db.py` et les migrations divergent ;
en CI, les migrations sont jouées sur SQLite et sur PostgreSQL.

## Le Governor : toute action passe par lui

```
Agent ──ActionRequest──▶ Governor ──niveau 1──▶ exécuteur (connecteur) ──▶ journal
                            │
                            ├─niveau 2──▶ file de validation ──clic──▶ exécuteur ──▶ journal
                            │
                            └─niveau 3──▶ dossier préparé pour un humain ──▶ journal
```

* Le niveau vient du **type d'action** (`ACTION_LEVELS`), jamais de l'agent.
* Un agent peut **durcir** le niveau, jamais l'assouplir. Type inconnu = niveau 3.
* Chaque agent a une **autonomie maximale** (`AGENT_AUTONOMY`) : le chef n'exécute rien,
  la Veille est en lecture seule, Communication / Contenus web / Commercial passent
  toujours par la validation.
* **Bouton d'arrêt** : par canal ou global (`*`), vérifié en base avant chaque exécution
  (effet immédiat, R-07).
* Une validation non faite sous 24 h est **signalée**, jamais publiée par défaut.
* Les validations sont **nominatives** et tracées au journal.

## Tableau de bord : comptes et droits

Comptes nominatifs (mot de passe haché PBKDF2, session signée de 12 h, cookie
`HttpOnly`, `SameSite=Strict`, `Secure` en HTTPS). Chaque décision est tracée au journal
avec le nom et l'adresse de la personne.

| Rôle | Niveau 2 (validation) | Niveau 3 (dossiers humains) | Bouton d'arrêt | Comptes |
|---|---|---|---|---|
| valideur | pôles où son adresse est valideur ou suppléant (`config/poles.yaml`) | — | suspendre | — |
| direction | tous les pôles | oui | suspendre et réactiver | — |
| admin | tous les pôles | oui | suspendre et réactiver | liste |

Les comptes se créent en ligne de commande (`ibig-agent utilisateur ...`) : pas de
formulaire d'inscription exposé sur Internet.

## Alertes aux valideurs

Toutes les 15 minutes, l'agent chef envoie depuis `IBIG_NOTIFICATION_MAILBOX` :

* au valideur du pôle, un récapitulatif des nouveaux éléments à valider ;
* au suppléant, les éléments toujours en attente après 24 h (jamais publiés par défaut) ;
* à la direction, les dossiers de niveau 3 et les éléments d'un pôle sans valideur.

Un envoi échoué (canal suspendu, boîte en panne) est retenté au passage suivant.

## Circuit d'un mail entrant (agent Messagerie)

1. Relève toutes les 5 min, sans marquer les mails comme lus. Chaque message est
   « réservé » en base avant traitement : aucun doublon, aucun oubli (R-01).
2. Détection des consignes suspectes par motifs (avant le modèle).
3. Tri par le modèle léger, en sortie structurée : pôle, type, urgence, sentiment,
   réclamation grave, consigne suspecte, FAQ applicable, fiche prospect.
4. Décision **par le code** :

| Cas | Action | Niveau |
|---|---|---|
| Consigne suspecte | Étiquette SUSPECT, dossier sécurité, aucune réponse | 3 |
| Spam | Étiquette | 1 |
| Juridique, réclamation grave | Brouillon préparé pour la direction | 3 |
| Question couverte par une FAQ validée | Réponse FAQ mot pour mot + mention « un conseiller prendra le relais » | 1 |
| Prospect | Fiche prospect, accusé de réception, brouillon de réponse, passage au Commercial | 1 + 2 |
| Client, support | Accusé de réception + brouillon de réponse | 1 + 2 |
| Fournisseur, partenaire, candidature, administratif | Transfert au responsable du pôle | 1 |
| Tri impossible (API indisponible, budget atteint) | Dossier « à traiter à la main » | 3 |

5. Chaque brouillon est contrôlé par la base de connaissances : tout prix, contact, lien,
   pourcentage ou formulation interdite absent de la base est signalé au valideur (R-05).

## Sécurité (section 13)

* Un mail est une **donnée, jamais un ordre** : contenu encadré dans les prompts, détection
  par motifs et par le modèle, et surtout aucune action n'est décidée par le contenu du mail.
* Secrets : jamais dans le code ni dans `config/` ; seules les **références** aux variables
  d'environnement y figurent (alimentées par le coffre à secrets du serveur).
* Comptes dédiés à l'agent, droits minimaux (compte « Auteur » WordPress, jeton Gmail
  `gmail.modify` d'un compte dédié).
* Offre API professionnelle (pas d'entraînement sur les données envoyées).
