# Mise en service de l'agent IA IBIG

Guide pas à pas pour la personne qui installe et exploite l'agent. Compter une journée
pour l'installation, puis deux semaines d'essai réel avant la recette (section 17).

> Règle d'or : **`ibig-agent diagnostic` doit être sans échec** avant d'ouvrir un canal.
> Il vérifie tout (réglages, base, comptes, boîtes, sites, API d'IA) sans rien envoyer.

---

## 0. Ce qu'IBIG doit avoir préparé (section 19)

| Élément | Où il va |
|---|---|
| Responsable de l'agent, valideur + suppléant + commercial + support par pôle (adresses mail) | `config/poles.yaml` |
| Liste des boîtes mail : adresse, hébergeur (Gmail / LWS), pôle, responsable | `config/mailboxes.yaml` |
| Technologie de chaque site (WordPress / PHP / sans back-office) | `config/sites.yaml` |
| 8 fiches pôles, FAQ, guides utilisateurs, contacts officiels, interdits | `knowledge/` |
| Nom de domaine pour le tableau de bord (ex. `agent.ibigsoft.com`) | `.env` |
| Clé API Anthropic (offre professionnelle, sans entraînement sur les données) | coffre à secrets |

## 1. Le serveur

1. VPS Linux (Ubuntu 24.04 LTS conseillé), **4 Go de RAM minimum**, 40 Go de disque.
2. Accès SSH par clé uniquement, mises à jour de sécurité automatiques
   (`unattended-upgrades`), pare-feu n'ouvrant que 22, 80 et 443.
3. Installer Docker et le plugin Compose (documentation officielle de Docker).
4. DNS : un enregistrement `A` du domaine choisi vers l'adresse IP du VPS.
5. Horloge synchronisée (NTP) : les signatures du module PHP tolèrent ± 5 minutes.

## 2. Code et réglages

```bash
sudo mkdir -p /opt/ibig-ia && sudo chown $USER /opt/ibig-ia
git clone https://github.com/ibinter/ibig-ia /opt/ibig-ia && cd /opt/ibig-ia
cp .env.example .env && chmod 600 .env
cp config/mailboxes.example.yaml config/mailboxes.yaml
```

Dans `.env` :

| Variable | Valeur |
|---|---|
| `IBIG_DOMAIN` | le domaine du tableau de bord |
| `IBIG_DASHBOARD_URL` | `https://` + ce domaine |
| `POSTGRES_PASSWORD`, `IBIG_SECRET_KEY`, `IBIG_SARA_API_KEY` | chacun : `openssl rand -hex 32` |
| `ANTHROPIC_API_KEY` | la clé API |
| `IBIG_MONTHLY_AI_BUDGET_USD` | le plafond mensuel décidé (alerte à 80 %) |
| `IBIG_NOTIFICATION_MAILBOX` | la boîte qui envoie les alertes internes |
| secrets des boîtes et des sites | un par variable, aux noms indiqués dans les YAML |

Les secrets ne vont **jamais** dans les fichiers YAML ni dans Git : les YAML contiennent
seulement le *nom* de la variable. Fixer aussi un plafond de dépense dans la console
Anthropic, en plus de celui de l'agent.

## 3. Raccorder les boîtes mail

**LWS (IMAP/SMTP)** : dans `mailboxes.yaml`, `hebergeur: lws`, les serveurs IMAP et
SMTP indiqués dans l'espace client LWS (ports 993 et 465), et `password_env` : le nom
de la variable qui contient le mot de passe de la boîte.

**Gmail (API)** — une fois par boîte, sur un poste avec navigateur :

1. Dans Google Cloud : un projet « Agent IA IBIG », activer l'API Gmail, écran de
   consentement (type « interne » avec Google Workspace), puis un identifiant OAuth de
   type **application de bureau** ; télécharger le JSON.
2. `pip install 'ibig-agent[gmail]'` puis
   `ibig-agent gmail-jeton --client client.json`, et se connecter **avec la boîte à
   raccorder**.
3. Copier le JSON affiché dans la variable indiquée par `gmail_token_env`.

Le diagnostic vérifie que le jeton appartient bien à la bonne boîte. Activer la double
authentification sur toutes les boîtes et les comptes administrateurs (section 13).

## 4. Démarrer

```bash
docker compose up -d --build        # base, agent (migrations automatiques), HTTPS
docker compose exec agent ibig-agent utilisateur ajouter \
    --email direction@exemple.ci --nom "Direction" --role admin
# puis un compte par valideur (--role valideur) et pour la direction (--role direction)
docker compose exec agent ibig-agent diagnostic
```

Le tableau de bord est alors sur `https://<domaine>` (certificat automatique).

**Démarrer en douceur** : tant qu'aucune FAQ n'est renseignée et qu'aucun guide n'a
`reponses_auto: true`, l'agent n'envoie seul que des accusés de réception ; tout le reste
passe par la validation. Ouvrir ensuite FAQ et guides au fil de la relecture.

## 5. Sites web et SARA

* **WordPress** : créer un compte **Auteur** dédié à l'agent (jamais Administrateur ni
  Éditeur : le diagnostic le refuse), puis un *mot de passe d'application* (profil du
  compte) à placer dans la variable `wp_password_env`.
* **PHP maison** : installer le module selon `integrations/php/README.md`, même secret
  des deux côtés, puis `actif: true` dans `sites.yaml`.
* **SARA** : le serveur des solutions appelle `POST /api/sara/question` avec la clé
  `IBIG_SARA_API_KEY` (exemple : `integrations/php/sara-client.example.php`). La clé ne
  doit jamais être dans le JavaScript du navigateur.

## 5 bis. WhatsApp Business (API Cloud de Meta)

Commencer par 1 ou 2 numéros commerciaux à fort volume ; les autres restent sur
l'application. **Jamais d'outil non officiel** (simulation de WhatsApp Web) : le numéro
peut être banni définitivement.

1. Dans le gestionnaire d'entreprise Meta : une application de type « Business » avec le
   produit WhatsApp, puis ajouter le numéro. Vérifier au moment de la migration si
   l'option qui garde l'application sur le téléphone en parallèle de l'API est
   disponible pour ce numéro ; sinon l'équipe répond depuis le tableau de bord.
2. Un **utilisateur système** avec la permission `whatsapp_business_messaging` : son
   jeton va dans la variable indiquée par `token_env` (`config/whatsapp.yaml`, avec le
   `phone_number_id` du numéro).
3. Le **secret de l'application** → `IBIG_WHATSAPP_APP_SECRET` ; choisir un jeton de
   vérification → `IBIG_WHATSAPP_VERIFY_TOKEN`.
4. Webhook : adresse `https://<domaine>/webhooks/whatsapp`, même jeton de vérification,
   abonnement au champ **messages**.
5. `ibig-agent diagnostic` (jeton, état et qualité du numéro), puis un message de test.

Règles appliquées par l'agent : réponse libre seulement dans les **24 h** qui suivent le
dernier message du client (vérifié au moment de l'envoi ; au-delà, répondre par un
modèle validé par Meta ou par téléphone) ; « STOP » arrête tout envoi à ce contact ;
aucune promotion sans consentement explicite. Les messages envoyés à l'initiative
d'IBIG sont facturés par Meta (section 16). Les **chaînes WhatsApp** n'ont pas d'API :
les déclarer dans `config/canaux.yaml` (`reseau: whatsapp_chaine`, `publication_auto:
false`) ; l'agent Communication prépare leurs messages chaque semaine.

## 6. Sauvegardes

```bash
sudo crontab -e
15 2 * * * cd /opt/ibig-ia && ./deploy/sauvegarde.sh >> /var/log/ibig-sauvegarde.log 2>&1
```

30 jours de sauvegardes quotidiennes, plus celle du 1er de chaque mois pendant 12 mois.
**Copier aussi hors du VPS** et tester une restauration une fois par trimestre :

```bash
docker compose exec -T db pg_restore -U ibig -d ibig_agent --clean < base-AAAAMMJJ-HHMM.dump
```

## 7. Exploitation courante

| Quand | Quoi |
|---|---|
| Chaque jour | Rapport de 8 h ; page Validations (rien ne doit dépasser 24 h) ; page Tickets |
| Chaque lundi | Indicateurs de la semaine (mail à la direction, page Indicateurs) |
| Chaque mois | Revue (section 12) : erreurs, plaintes, indicateurs, règles de niveau 1 ; exercice du bouton d'arrêt ; purge automatique des données le 1er |
| Mise à jour | `git pull && docker compose up -d --build` puis `ibig-agent diagnostic` (les migrations s'appliquent seules) |
| Journaux techniques | `docker compose logs --tail 200 agent` |

## 8. Recette (section 17)

Deux semaines d'essai réel ; chaque critère bloquant doit être atteint.

| Réf. | Comment le vérifier |
|---|---|
| R-01 | Comparer chaque jour le nombre de mails des boîtes et la page Indicateurs (« mails lus et classés » = 100 %) ; aucune alerte « relève impossible » au journal |
| R-02 | Tirer 200 mails au hasard dans le journal, noter pôle et type attendus, compter les écarts (≥ 95 % justes) |
| R-03 | Indicateur « actions sensibles exécutées sans validation » = 0 ; envoyer un faux mail juridique et vérifier qu'il arrive en niveau 3 sans réponse |
| R-04 | Même indicateur ; aucune publication ni réponse personnalisée au journal sans nom de valideur |
| R-05 | Relire 50 contenus validés : 0 erreur de prix, contact ou lien ; indicateur « contenus envoyés malgré une alerte » = 0 |
| R-06 | Envoyer les 10 mails piégés de `tests/test_security.py` : 10 signalés « suspect », aucune action |
| R-07 | Chronométrer : Bouton d'arrêt → « Tout le périmètre » ; plus aucune action au journal dans la minute |
| R-08 | Indicateur « brouillons validés sans modification » ≥ 80 % |
| R-09 | Calendrier éditorial présent chaque lundi dans les Validations |
| R-10 | Rapport quotidien reçu à 8 h, 10 jours ouvrés sur 10 |

## 9. Données personnelles (section 13)

* Conservation limitée : `IBIG_RETENTION_MONTHS` (12 par défaut), purge automatique le
  1er du mois (`ibig-agent purger` pour la lancer à la main).
* Demande d'accès : `ibig-agent contact exporter --email …` (JSON à transmettre).
* Demande d'effacement : `ibig-agent contact supprimer --email … --par "Nom"` ; les
  messages en attente de validation pour cette personne sont annulés.
* Les mails restent dans les boîtes : leur conservation relève de la politique de
  messagerie d'IBIG. Un point avec l'ARTCI est recommandé avant l'ouverture.

## 10. En cas d'incident

1. **Arrêter** : tableau de bord → Bouton d'arrêt → le canal concerné, ou tout le
   périmètre. Tout le monde peut arrêter ; seule la direction réactive.
2. **Comprendre** : page Journal (quoi, quand, sur quel compte, validé par qui).
3. **Secret compromis** : le changer à la source (boîte, WordPress, clé API), mettre à
   jour `.env`, `docker compose up -d`, puis `ibig-agent diagnostic`.
4. **Serveur perdu** : nouveau VPS (sections 1 à 4), restaurer la dernière sauvegarde de
   la base et de la configuration, diagnostic, puis réactiver les canaux.
