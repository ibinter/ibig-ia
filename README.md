# Agent IA IBIG

Agent IA de communication et d'opérations de l'écosystème **IBIG SARL** — implémentation
du *Cahier des charges — Agent IA IBIG, version 1.0*.

> **Principe directeur** : l'IA prépare et exécute, l'humain valide ce qui engage
> l'entreprise. Les tâches répétitives sont automatiques ; les publications, réponses aux
> clients et campagnes passent par une validation en un clic ; l'argent, les contrats et
> le juridique restent humains.

## Où en est-on ?

Ce dépôt contient le **socle de la phase 1** (tri et réponse aux mails Gmail + LWS) et le
début de la phase 2 (calendrier éditorial). Détail et suite : [`docs/feuille-de-route.md`](docs/feuille-de-route.md).

| Brique | État |
|---|---|
| Gouvernance 3 niveaux, journal complet, bouton d'arrêt | ✅ |
| Agent Messagerie : relève, classement pôle/type, urgence, sentiment, FAQ, brouillons, transferts | ✅ |
| Connecteurs mail : IMAP/SMTP (LWS) et API Gmail | ✅ (à tester sur les vraies boîtes) |
| Protection contre les mails piégés | ✅ |
| Base de connaissances + contrôle des faits (prix, contacts, liens) | ✅ (fiches à compléter par IBIG) |
| Tableau de bord de validation, comptes nominatifs, droits par pôle | ✅ |
| Alertes mail aux valideurs, relance du suppléant après 24 h, dossiers niveau 3 à la direction | ✅ |
| Rapport quotidien de 8 h, alerte des validations en retard (24 h) | ✅ |
| Maîtrise des coûts IA (modèle léger pour le tri, plafond, alerte à 80 %) | ✅ |
| Agent Communication : calendrier éditorial hebdomadaire | 🟡 génération + validation ; publication via outil multi-comptes à raccorder |
| Agent Contenus web : articles en brouillon (WordPress, module PHP, export HTML) | ✅ (technologie des sites à renseigner) |
| Agent Veille : indicateurs de réussite (section 3), alertes pic / mécontentement / sans réponse | ✅ (mails et journal ; réseaux sociaux à raccorder) |
| Agent Commercial : qualification, relances J+3/J+7/J+14 à valider, essais et démos, désinscription | ✅ |
| Support (SARA), WhatsApp | ⏳ phase 3 |

## Architecture

```
            Tableau de bord (humains : objectifs, validations, arrêt)
                               │
                          Agent chef ── rapport quotidien, alertes
                               │
   ┌──────────┬──────────┬─────┴─────┬───────────┬──────────┐
Communication Contenus  Messagerie  Commercial  Support    Veille
              web                                (SARA)
   └──────────┴──────────┴─────┬─────┴───────────┴──────────┘
                    Base de connaissances commune
                               │
                 Governor (niveaux 1/2/3, journal, arrêt)
                               │
        Connecteurs : Mails (Gmail, LWS) · Réseaux · Sites · WhatsApp
```

Voir [`docs/architecture.md`](docs/architecture.md).

## Démarrage rapide (développement)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                                # renseigner ANTHROPIC_API_KEY, IBIG_SECRET_KEY
cp config/mailboxes.example.yaml config/mailboxes.yaml   # lister les boîtes (section 19)

# Comptes du tableau de bord (les pôles d'un valideur viennent de config/poles.yaml)
ibig-agent utilisateur ajouter --email direction@exemple.ci --nom "Direction" --role admin
ibig-agent utilisateur ajouter --email valideur.soft@exemple.ci --nom "Valideur SOFT" --role valideur

ibig-agent migrer             # créer / mettre à jour la base (automatique au démarrage)
ibig-agent verifier-base      # état de la base de connaissances
ibig-agent poll               # relever les boîtes une fois
ibig-agent rapport            # rapport quotidien
ibig-agent alertes            # alerter les valideurs maintenant
ibig-agent article --site https://ibigsoft.com   # préparer un article (brouillon à valider)
ibig-agent indicateurs --jours 30                # indicateurs de réussite
ibig-agent veille             # alertes de veille maintenant
ibig-agent commercial         # qualifier et relancer les prospects maintenant
ibig-agent prospects essais.csv   # importer des prospects (essais, démonstrations)
ibig-agent serve              # tableau de bord (http://localhost:8000) + tâches planifiées
pytest                        # tests
```

## Déploiement (VPS)

```bash
cp .env.example .env    # + POSTGRES_PASSWORD
docker compose up -d --build
```

Les migrations de la base sont appliquées automatiquement au démarrage du service
(`ibig-agent migrer` pour les lancer à la main). Le tableau de bord écoute sur `127.0.0.1:8000` : le publier derrière un reverse proxy
HTTPS. Sauvegardes quotidiennes de la base et journaux conservés 12 mois (section 13).

## Organisation du dépôt

| Chemin | Contenu |
|---|---|
| `config/poles.yaml` | Les 8 pôles, valideurs et suppléants |
| `config/canaux.yaml` | Comptes sociaux, pôle rattaché, règles de déclinaison |
| `config/mailboxes.yaml` | Boîtes mail (non versionné — modèle : `mailboxes.example.yaml`) |
| `config/sites.yaml` | Sites web, technologie, rythme d'articles |
| `integrations/php/` | Module d'entrée des articles pour les sites PHP maison |
| `knowledge/` | Base de connaissances : charte, fiches pôles, FAQ, contacts, interdits |
| `src/ibig_agent/governance.py` | Niveaux de validation, journal, bouton d'arrêt |
| `src/ibig_agent/agents/` | Agents chef, Messagerie, Communication |
| `src/ibig_agent/channels/` | Connecteurs (mail) |
| `src/ibig_agent/dashboard/` | Tableau de bord web |
| `docs/` | Architecture, feuille de route, recette |

## Ce qu'IBIG doit fournir pour lancer la phase 1

Voir la section 19 du cahier des charges et la checklist de
[`docs/feuille-de-route.md`](docs/feuille-de-route.md#prérequis-ibig).
