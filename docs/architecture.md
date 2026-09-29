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

## Agent Contenus web (section 11)

Le 1er et le 15 du mois (2 articles par mois et par site actif), pour chaque site de
`config/sites.yaml` dont la technologie est connue et la fiche pôle complète :

1. **Matière** : questions réelles des clients (résumés du tri des mails, sans nom ni
   coordonnées, mails suspects exclus) et FAQ du pôle ; mots-clés déjà traités écartés.
2. **Rédaction** (modèle avancé, sortie structurée) : mot-clé, question, titre, meta
   description, contenu HTML, sources de chaque chiffre.
3. **Contrôles** : HTML nettoyé par liste blanche (ni script, ni attribut `on…`, liens
   http(s) seulement) ; prix, contacts, liens et pourcentages absents de la base signalés.
4. **Validation en un clic** (niveau 2) ; le valideur peut corriger le HTML, qui est
   nettoyé à nouveau avant l'envoi.
5. **Dépôt en brouillon** sur le site ; un humain relit et met en ligne.

| Technologie | Raccordement |
|---|---|
| WordPress | API REST `wp/v2/posts`, `status: draft`, compte « Auteur » + mot de passe d'application |
| PHP maison | `integrations/php/ibig-article-inbox.php` : HMAC-SHA256, anti-rejeu, stockage hors racine web |
| Sans back-office | Fichier HTML dans `exports/articles/<site>/` |

## Agent Veille et reporting

Lecture seule : il mesure et alerte, n'agit sur aucun canal. Chaque action de l'agent
liée à un mail porte la référence de ce mail (`ref` dans le journal), ce qui permet de
mesurer les délais de réponse.

**Indicateurs** (page « Indicateurs », `ibig-agent indicateurs`, envoi à la direction
chaque lundi) :

| Indicateur (section 3) | Mesure | Cible |
|---|---|---|
| Ne rien laisser passer | Mails lus et classés | 100 % |
| Répondre vite | Médiane du délai de première réponse, en heures ouvrées (lun.–ven. 8 h–18 h), accusés de réception exclus | < 2 h |
| Publier régulièrement | Publications par pôle et par semaine | 3 minimum |
| Convertir | Prospects transmis | à fixer |
| Rester fiable | Contenus envoyés malgré une alerte factuelle | 0 |
| Garder la main | Actions de niveau 2 ou 3 exécutées sans décision humaine | 0 |
| R-08 | Brouillons validés sans modification | 80 % |

**Alertes** (toutes les heures, une fois par type et par élément, à la direction et au
valideur du pôle, toujours visibles au tableau de bord) :

* pic de messages : au moins 10 mails dans l'heure et 3 fois la moyenne horaire de la semaine ;
* message mécontent (sentiment négatif, hors spam et mails suspects) ;
* mail sans réponse au-delà de 2 h ouvrées (prospects, clients, support).

Seuils réglables : `IBIG_BUSINESS_OPEN_HOUR`, `IBIG_BUSINESS_CLOSE_HOUR`,
`IBIG_RESPONSE_TARGET_HOURS`, `IBIG_SPIKE_MIN_MESSAGES`, `IBIG_SPIKE_FACTOR`.

## Agent Commercial

Autonomie : validation en un clic. Seules les alertes internes aux commerciaux partent
sans validation (elles ne touchent aucun canal extérieur).

| Étape | Fonctionnement | Niveau |
|---|---|---|
| Qualification (toutes les 15 min) | Modèle léger : score 0–100, chaud / tiède / froid, solution visée, prochaine étape, à partir des résumés du tri (jamais le texte brut) et de la base de connaissances | journal |
| Prospect chaud (score ≥ 70) | Alerte immédiate au commercial du pôle (`commercial` dans `poles.yaml`, à défaut le valideur) | interne |
| Relances (semaine, heures ouvrées) | Brouillon si pas de réponse à notre dernier message : J+3, J+7, J+14 ; arrêt dès que le prospect écrit ; « sans suite » 7 jours après la 3e | 2 |
| Fin d'essai | Brouillon de relance 3 jours avant la fin + alerte au commercial | 2 |
| Démonstration | Rappel au commercial la veille | interne |
| Gagné / perdu / stop | Décision au tableau de bord (page Prospects), tracée au journal | humain |

**Consentement** : chaque relance se termine par « répondez simplement STOP » (ajouté par
le code si le modèle l'omet). Une réponse de désinscription (« STOP », « ne plus me
contacter », « désinscrire »…) est détectée par l'agent Messagerie et arrête les relances.

**Import** (`ibig-agent prospects fichier.csv`, séparateur `,` ou `;`) : colonnes
`email, nom, pole, solution, besoin, fin_essai, demo, boite` — tant que le moteur de
licences IBIG SOFT n'est pas raccordé, c'est ainsi que l'on renseigne essais et démos.

## Agent Support et SARA

« Automatique si la réponse est dans la base, sinon escalade » (section 6). La base est
découpée en passages (sections `##` des guides, FAQ, catalogue) ; le modèle répond à
partir des passages trouvés et cite chacun **mot pour mot**. Le code vérifie :

| Contrôle | Si échec |
|---|---|
| La question est entièrement couverte (sinon le modèle le dit) | escalade |
| Chaque citation existe mot pour mot dans le passage cité | brouillon (ou escalade si aucune source valide) |
| Aucun prix, contact, lien ou pourcentage absent de la base | brouillon |
| Chaque guide cité porte `reponses_auto: true` (validé par un humain) | brouillon |
| Demande ni suspecte, ni mécontente, ni urgente | brouillon / escalade |
| Canal non suspendu (`mail`, `sara`) | escalade |

* **Mail** : réponse automatique (avec « un conseiller prendra le relais si nécessaire »),
  sinon accusé de réception + brouillon documenté à valider (sources affichées au
  valideur), sinon brouillon libre comme pour les autres mails.
* **SARA** : `POST /api/sara/question` (en-tête `Authorization: Bearer <IBIG_SARA_API_KEY>`,
  20 questions / 10 min par client), corps `{"question", "pole", "contact", "conversation"}`.
  Réponse `{"reponse", "sources", "transmis", "ticket"}` : si la réponse n'est pas
  automatique, SARA répond « Je transmets votre question à un conseiller » et un ticket
  est ouvert (avec la proposition de réponse pour le conseiller). À appeler depuis le
  serveur du site, jamais depuis le navigateur : `integrations/php/sara-client.example.php`.
* **Tickets** : page « Tickets » (chacun voit ses pôles), alerte au `support` du pôle
  (`poles.yaml`, à défaut le valideur).

## WhatsApp Business (API Cloud de Meta)

* **Réception** : `POST /webhooks/whatsapp`, signature `X-Hub-Signature-256` vérifiée
  (sinon 401), réponse immédiate à Meta, traitement en arrière-plan, messages en double
  ignorés (identifiant `wamid`). Vérification de l'abonnement : `GET` avec `hub.challenge`.
* **Traitement** : même tri que les mails (pôle du numéro) ; suspect → niveau 3 sans
  réponse ; « STOP » → désinscription ; FAQ ou réponse documentée du Support → réponse
  automatique ; sinon court accusé de réception (au plus un par 12 h) et brouillon à
  valider ; juridique / réclamation grave → direction ; image, audio… → un humain.
* **Envoi** : `whatsapp.*` passe par le Governor ; l'exécuteur revérifie au moment de
  l'envoi la **fenêtre de 24 h** et la **désinscription**, car une validation peut
  arriver trop tard.
* **Contacts** (`whatsapp_contacts`) : dernier message reçu, dernier accusé, désinscription,
  consentement promotionnel (jamais déduit d'un simple message).
