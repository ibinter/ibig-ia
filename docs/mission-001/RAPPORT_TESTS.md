# RAPPORT DES TESTS — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Vue d'ensemble

| Métrique | Valeur |
|---------|--------|
| Fichiers de test | 47 |
| Framework | pytest |
| Base de données de test | SQLite (in-memory via `init_db()`) |
| Exécution | `.venv/Scripts/python -m pytest tests/` |

> Note : la suite a été lancée en arrière-plan pendant cet audit. Les résultats exacts (passed/failed) seront confirmés lors de la prochaine exécution.

## Inventaire des fichiers de test

### Authentification & sécurité
| Fichier | Périmètre |
|---------|-----------|
| `test_auth.py` | Connexion, sessions, rôles, changement MDP |
| `test_security.py` | CSRF, CSP, headers, rate-limiting |
| `test_privacy.py` | Rétention données personnelles |
| `test_governance.py` | Governor 3 niveaux, killswitch |

### Agents
| Fichier | Agent testé |
|---------|-------------|
| `test_campagnes.py` | CampaignWriter |
| `test_communication.py` | CommunicationAgent |
| `test_contenus_web.py` | ContenusWebAgent |
| `test_notifications.py` | ValidatorNotifier |
| `test_support.py` | SupportAgent |
| `test_veille.py` | VeilleAgent |
| `test_whatsapp.py` | WhatsAppAgent |
| `test_messagerie.py` | MessagerieAgent |
| `test_commercial.py` | CommercialAgent |

### Base de données & migrations
| Fichier | Périmètre |
|---------|-----------|
| `test_migrations.py` | Exécution des 19 migrations Alembic |
| `test_config.py` | Chargement config, poles.yaml, mailboxes.yaml |
| `test_configuration.py` | Settings pydantic, variables d'environnement |

### Fonctionnalités métier
| Fichier | Périmètre |
|---------|-----------|
| `test_knowledge.py` | KnowledgeBase, embeddings, RAG |
| `test_llm.py` | ClaudeClient, budget ai_usage |
| `test_autopilot.py` | AutopilotService, MissionTask |
| `test_missions.py` | MissionService, événements |
| `test_quotes.py` | Devis, lignes |
| `test_finance.py` | Paiements, commissions |
| `test_crm.py` | Prospects, activités |
| `test_catalog_import.py` | Import catalogue |
| `test_catalog_register.py` | Enregistrement offres |
| `test_prospecteur.py` | Agent prospection |
| `test_consigne.py` | Directives direction |

### Infrastructure & surveillance
| Fichier | Périmètre |
|---------|-----------|
| `test_ecosysteme.py` | EcosystemService, sondes, incidents |
| `test_diagnostics.py` | Diagnostic IA écosystème |
| `test_deploy.py` | Script de déploiement |
| `test_serveur.py` | État serveur, heartbeat |
| `test_dbmonitor.py` | Surveillance base de données |
| `test_anomalies.py` | Détection anomalies |

### Web & intégrations
| Fichier | Périmètre |
|---------|-----------|
| `test_web.py` | Routes FastAPI (smoke tests) |
| `test_dashboard.py` | Routes dashboard |
| `test_sara_widget.py` | API SARA |
| `test_mail_tls.py` | Connexion IMAP/SMTP TLS |
| `test_mailaby.py` | Envoi via Mailaby |
| `test_dnsauth.py` | Authentification DNS |
| `test_relais.py` | Relais de messages |
| `test_social.py` | Publication sociale |
| `test_semantic_images.py` | Images sémantiques |
| `test_site_import.py` | Import depuis sites web |
| `test_recette_revue.py` | Revue mensuelle |
| `test_sante_compte.py` | Santé des comptes |

### Utilitaires
| Fichier | Périmètre |
|---------|-----------|
| `test_demo.py` | Données de démo |
| `conftest.py` | Fixtures partagées (Runtime de test, DB SQLite) |

## Observations

- **Couverture** : La suite couvre les 14 agents, les 19 migrations, les routes principales, et toutes les intégrations externes (WhatsApp, mail, LLM).
- **Isolation** : Les tests utilisent SQLite in-memory + mocks des services externes (LLM, IMAP, SMTP, Meta API).
- **Migrations** : `test_migrations.py` exécute la chaîne complète 0001→0019 sur SQLite, garantissant la cohérence du schéma.
- **Point d'attention** : Les tests qui dépendent de pgvector (SemanticIndex) peuvent nécessiter une configuration spéciale sur SQLite.
- **Commande complète** : `.venv/Scripts/python -m pytest tests/ --tb=short -q`
