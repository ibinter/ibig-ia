# ARCHITECTURE ACTUELLE — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Vue d'ensemble

IBIG-IA est une plateforme de pilotage d'entreprise basée sur des agents IA. Elle expose un tableau de bord web (FastAPI + Jinja2) et orchestre 14 agents spécialisés via un bus central (Runtime) et un planificateur de tâches (APScheduler).

## Couches techniques

```
Internet
  │
  ▼
Nginx (reverse proxy, TLS, port 443)
  │
  ▼
Docker Compose — VPS 185.98.139.38
  │
  ├── agent (FastAPI, port 8000 interne)
  │     ├── dashboard/app.py       → Routes HTTP, CSRF, sessions
  │     ├── runtime.py             → Assemblage de tous les services
  │     ├── scheduler.py           → 28 tâches planifiées (APScheduler)
  │     └── agents/ (14 agents)    → Exécution des actions IA
  │
  └── PostgreSQL (pgvector activé)
        └── Base IBIG : 35+ tables, 19 migrations Alembic
```

## Framework et dépendances principales

| Composant | Technologie |
|-----------|-------------|
| API web | FastAPI + Uvicorn |
| Templates | Jinja2 |
| ORM | SQLAlchemy 2.x (DeclarativeBase + Mapped) |
| Migrations | Alembic |
| Planificateur | APScheduler 3.x (BackgroundScheduler) |
| LLM | Anthropic Claude (claude-haiku-4-5) |
| Chiffrement | Fernet (AES-128-CBC + HMAC-SHA256) via cryptography |
| Configuration | pydantic-settings (env prefix `IBIG_`) |
| Vecteurs | pgvector (PostgreSQL extension) |
| Emails (envoi) | Brevo / Mailaby |
| WhatsApp | Meta Cloud API v23.0 |

## Flux de requête HTTP

```
Navigateur → Nginx (TLS) → FastAPI
  ├── Vérification session (cookie ibig_session, 12h)
  ├── Vérification CSRF (Origin/Referer sur non-GET)
  ├── Contrôle rôle (admin | direction | valideur)
  └── Route handler → service/agent → DB → réponse HTML ou JSON
```

## Modèle de déploiement

- Unique instance Docker sur VPS (conteneur `ibig-ia-agent-1`)
- PostgreSQL en service séparé ou sur le même hôte
- Nginx en front (port 443/80) ; le port 8000 n'est exposé qu'en 127.0.0.1
- Répertoires montés en volume : `knowledge/` (lecture seule), `exports/`, `state/`, `config/`
- Script de déploiement : `deploy/deploy.sh` (git pull → docker build → up)

## Points d'entrée publics

| URL | Rôle |
|-----|------|
| `/` → `/dashboard` | Tableau de bord principal |
| `/api/sara/question` | API REST (Bearer token, serveurs solutions) |
| `/webhooks/whatsapp` | Webhook Meta WhatsApp Business |
| `/ecosysteme` | Centre de contrôle (direction/admin uniquement) |

## Gouvernance IA (Governor)

Toutes les actions des agents transitent par le `Governor` à 3 niveaux :
- **Niveau 1** : exécution automatique (lecture, analyse, brouillons internes)
- **Niveau 2** : exécution après validation humaine (clic dans le dashboard)
- **Niveau 3** : toujours humain, jamais automatisé (envoi, publication, paiement, suppression)
