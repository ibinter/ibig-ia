# CARTOGRAPHIE DE LA BASE DE DONNÉES — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Informations générales

- **SGBD** : PostgreSQL avec extension pgvector
- **ORM** : SQLAlchemy 2.x (DeclarativeBase, Mapped)
- **Migrations** : Alembic (19 versions, 0001 → 0019)
- **Base de test** : SQLite (in-memory, via `init_db()`)
- **Accès** : `open_db()` applique les migrations en attente au démarrage

## Tables (35+)

### Noyau journalisation & gouvernance
| Table | Modèle | Description |
|-------|--------|-------------|
| `journal` | `JournalEntry` | Journal de toutes les actions de l'agent |
| `pending_actions` | `PendingAction` | Actions en attente de validation humaine (niveau 2) |
| `channel_state` | `ChannelState` | État de chaque canal (WhatsApp, email) |
| `processed_messages` | `ProcessedMessage` | Messages déjà traités (déduplication) |
| `agent_controls` | `AgentControl` | Killswitch par agent |

### Utilisateurs & sessions
| Table | Modèle | Description |
|-------|--------|-------------|
| `users` | `User` | Utilisateurs (login, hash MDP, rôle, dernière connexion) |
| `job_status` | `JobStatus` | Santé des tâches planifiées (succès/échec APScheduler) |

### CRM & prospection
| Table | Modèle | Description |
|-------|--------|-------------|
| `prospects` | `Prospect` | Prospects commerciaux |
| `prospect_activities` | `ProspectActivity` | Activités/historique CRM |

### Catalogue & commercial
| Table | Modèle | Description |
|-------|--------|-------------|
| `catalog_offers` | `CatalogOffer` | Offres du catalogue validé |
| `catalog_price_history` | `CatalogPriceHistory` | Historique des prix |
| `quotes` | `Quote` | Devis |
| `quote_lines` | `QuoteLine` | Lignes de devis |

### Finance
| Table | Modèle | Description |
|-------|--------|-------------|
| `payments` | `Payment` | Paiements reçus |
| `commission_rates` | `CommissionRate` | Barèmes de commission |
| `commissions` | `Commission` | Commissions calculées |

### Support
| Table | Modèle | Description |
|-------|--------|-------------|
| `tickets` | `Ticket` | Tickets support client |

### WhatsApp & contacts
| Table | Modèle | Description |
|-------|--------|-------------|
| `whatsapp_contacts` | `WhatsAppContact` | Contacts WhatsApp (fenêtre 24h) |
| `whatsapp_accounts` | `WhatsAppAccountRow` | Comptes WA (jetons chiffrés) |

### Messagerie & communication
| Table | Modèle | Description |
|-------|--------|-------------|
| `mailbox_accounts` | `MailboxAccount` | Boîtes mail (secrets chiffrés via vault) |

### Contenu & publication
| Table | Modèle | Description |
|-------|--------|-------------|
| `scheduled_posts` | `ScheduledPost` | Posts sociaux planifiés |
| `social_comments` | `SocialComment` | Commentaires des réseaux sociaux |
| `social_account_rows` | `SocialAccountRow` | Comptes des réseaux sociaux |
| `media_assets` | `MediaAsset` | Médias (images, vidéos) |
| `directives` | `Directive` | Consignes de la direction |
| `service_settings` | `ServiceSetting` | Paramètres des services (clé/valeur) |

### Base de connaissances (RAG)
| Table | Modèle | Description |
|-------|--------|-------------|
| `kb_embeddings` | `KbEmbedding` | Embeddings pgvector (colonne `vec` non-ORM) |
| `knowledge_edits` | `KnowledgeEdit` | Historique des modifications KB |

### IA & budget
| Table | Modèle | Description |
|-------|--------|-------------|
| `ai_usage` | `AiUsage` | Consommation tokens/coût par appel LLM |

### Missions & autopilot
| Table | Modèle | Description |
|-------|--------|-------------|
| `missions` | `Mission` | Missions (manuelles ou autopilot) |
| `mission_events` | `MissionEvent` | Historique des événements de mission |
| `mission_tasks` | `MissionTask` | Tâches autopilot (étapes de mission IA) |
| `workflow_defs` | `WorkflowDef` | Définitions de workflows réutilisables |

### Écosystème (surveillance)
| Table | Modèle | Description |
|-------|--------|-------------|
| `eco_assets` | `EcoAsset` | Sites/logiciels surveillés |
| `eco_checks` | `EcoCheck` | Résultats des sondes (historique 90j, HTTP status, ms, cert_days) |
| `eco_incidents` | `EcoIncident` | Incidents détectés (severity: info/warning/critical, status: open/ack/resolved) |
| `eco_ftp_creds` | `EcoFtpCred` | Identifiants FTP chiffrés |
| `eco_ftp_results` | `EcoFtpResult` | Résultats des contrôles FTP |

## Chronologie des migrations

| Version | Contenu |
|---------|---------|
| 0001 | Schéma initial (journal, pending_actions, channel_state, processed_messages, prospects, users, ai_usage) |
| 0002 | Résumé des mails |
| 0003 | Suivi commercial des prospects |
| 0004 | Tickets support |
| 0005 | Contacts WhatsApp |
| 0006 | Configuration tableau de bord |
| 0007 | Services extérieurs |
| 0008 | Publication automatique et WhatsApp |
| 0009 | Recherche vectorielle et photos |
| 0010 | WhatsApp et comptes sociaux |
| 0011 | Santé des tâches et sessions |
| 0012 | Catalogue validé et fil d'activités |
| 0013 | Missions et contrôle des agents |
| 0014 | Devis |
| 0015 | Paiements et commissions |
| 0016 | Tâches autopilot (MissionTask) |
| 0017 | Workflows (WorkflowDef) |
| 0018 | Centre de contrôle (EcoAsset, EcoCheck, EcoIncident, eco_server_state, eco_heartbeat) |
| 0019 | FTP checks (EcoFtpCred, EcoFtpResult) |

## Points d'attention

- La colonne `vec` de `kb_embeddings` est gérée hors ORM (pgvector natif) ; la fonction `include_object()` empêche Alembic de la détecter comme différence.
- Les secrets (mots de passe boîtes, jetons WA) sont chiffrés en base (Fernet) via `vault.py`.
- La table `eco_checks` conserve 90 jours d'historique : prévoir une purge périodique.
- Pas de soft-delete généralisé : les suppressions sont définitives sauf pour `eco_incidents` (statut `resolved`).
