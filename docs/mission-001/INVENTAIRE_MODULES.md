# INVENTAIRE DES MODULES — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Modules du package `src/ibig_agent/`

### Modules noyau

| Fichier | Rôle | Dépendances clés |
|---------|------|-----------------|
| `runtime.py` | Assemblage central : crée et câble tous les services/agents | Tous les modules ci-dessous |
| `config.py` | Settings (pydantic-settings, prefix `IBIG_`) + dataclasses Pole/Mailbox/Site/WhatsAppNumber | pydantic-settings, yaml |
| `db.py` | 35+ modèles SQLAlchemy + `make_engine()` + `open_db()` | SQLAlchemy, Alembic |
| `auth.py` | Authentification (PBKDF2-SHA256), sessions, rôles, liens d'action | hashlib, secrets |
| `vault.py` | Chiffrement Fernet des secrets (mots de passe boîtes mail, jetons WA) | cryptography |
| `governance.py` | Governor 3 niveaux, killswitch, constantes canaux | — |
| `scheduler.py` | 28 tâches planifiées (APScheduler BackgroundScheduler) | APScheduler |

### Services métier

| Fichier | Rôle |
|---------|------|
| `autopilot.py` | AutopilotService : création/suivi de missions IA (MissionTask) |
| `missions.py` | MissionService : gestion des missions manuelles |
| `knowledge.py` | KnowledgeBase + SemanticIndex (pgvector RAG) |
| `llm.py` | ClaudeClient : appels API Anthropic, suivi budget `ai_usage` |
| `ecosystem.py` | EcosystemService : surveillance sites, FTP, incidents, diagnostic IA |
| `consigne.py` | Gestion des directives et consignes de la direction |
| `org.py` | Organisation : chargement poles.yaml, canaux, sites |

### Modules dashboard

| Fichier | Rôle |
|---------|------|
| `dashboard/app.py` | Création app FastAPI, middlewares (CSRF, CSP, sessions, rate-limit) |
| `dashboard/*_routes.py` | 12 modules de routes (voir CARTOGRAPHIE_API.md) |
| `dashboard/templates/` | Templates Jinja2 HTML |

### Migrations

| Répertoire | Contenu |
|-----------|---------|
| `migrations/versions/` | 19 scripts Alembic (0001 à 0019) |
| `migrations/env.py` | Configuration Alembic + `include_object()` pour pgvector |

## Configuration YAML (`config/`)

| Fichier attendu | Contenu |
|----------------|---------|
| `poles.yaml` | Définition des pôles (code, nom, valideur, commercial…) |
| `mailboxes.yaml` | Boîtes mail (IMAP/SMTP, hébergeur, pôle) |
| `canaux.yaml` | Numéros WhatsApp Business |
| `sites.yaml` | Sites web surveillés (WordPress, PHP, statique) |
| `inventaire.yaml` | Actifs de l'écosystème (optionnel, importé dans `eco_assets`) |

## Répertoires de données

| Chemin | Usage | Accès conteneur |
|--------|-------|-----------------|
| `knowledge/` | Base de connaissances (fichiers texte) | Lecture seule |
| `exports/catalogue/` | Catalogue produits généré | Écriture |
| `exports/prospection/` | Fichiers prospects déposés | Écriture |
| `exports/articles/` | Articles WordPress générés | Écriture |
| `state/` | État sauvegarde, heartbeat | Écriture |

## Observations

- L'ensemble du code est dans un seul package Python ; pas de micro-services.
- Le découplage passe par le dataclass `Runtime` (injection de dépendances manuelle).
- Les YAML de configuration ne sont pas validés à chaud par pydantic : un YAML invalide fait planter le démarrage.
