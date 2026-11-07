# ÉTAT DES FONCTIONNALITÉS — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Légende
- ✅ Déployé et opérationnel (code complet + routes + templates)
- 🔧 Présent dans le code, déploiement à confirmer
- ⚠️ Partiel ou nécessite configuration externe
- ❌ Absent ou non implémenté

## Tableau de bord & navigation

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Page "Ma journée" (direction) | ✅ | Synthèse : état, priorités, argent, ventes, consignes |
| Page d'arrivée post-connexion | ✅ | |
| Authentification (login/logout) | ✅ | |
| Changement de mot de passe | ✅ | |
| Liens d'action par email (48h) | ✅ | |
| Gestion des utilisateurs (admin) | ✅ | |

## Agents et automatisations

| Agent | Fonctionnalité | État | Notes |
|-------|---------------|------|-------|
| MessagerieAgent | Triage emails entrants | ✅ | |
| MessagerieAgent | Réponse automatique brouillon | ✅ | Niveau 2 |
| WhatsAppAgent | Réception messages WA | ✅ | Webhook Meta |
| WhatsAppAgent | Réponse dans fenêtre 24h | ✅ | |
| WhatsAppAgent | Réponse hors fenêtre (template) | ⚠️ | Nécessite `whatsapp_reply_template` configuré |
| WhatsAppValidation | Validation messages hors fenêtre | ✅ | |
| CommercialAgent | Suivi prospects + relances | ✅ | |
| SupportAgent | Gestion tickets | ✅ | |
| CampaignWriter | Rédaction campagnes email | ✅ | Via Brevo/Mailaby |
| ContenusWebAgent | Articles WordPress | ⚠️ | Nécessite `wp_password_env` par site |
| VeilleAgent | Alertes indicateurs | ✅ | Notification direction |
| ChefAgent | Synthèse quotidienne | ✅ | |
| WeeklyPlanner | Planification hebdomadaire | ✅ | |
| MonthlyReviewer | Revue mensuelle | ✅ | |
| CommentWatcher | Surveillance commentaires | ✅ | |
| CommunicationAgent | Communication inter-pôles | ✅ | |
| ValidatorNotifier | Notifications aux valideurs | ✅ | |

## CRM & commercial

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Fiche prospect + activités | ✅ | |
| Import prospects (fichiers) | ⚠️ | Nécessite `prospect_dir` accessible |
| Devis (création, lignes) | ✅ | |
| Catalogue produits | ✅ | |
| Historique des prix | ✅ | |

## Finance

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Suivi paiements | ✅ | |
| Commissions (barèmes + calcul) | ✅ | |
| Tableau de bord financier | ✅ | |

## Base de connaissances & IA

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Base de connaissances (fichiers texte) | ✅ | Répertoire `knowledge/` |
| Recherche sémantique (pgvector RAG) | ✅ | SemanticIndex + KbEmbedding |
| Suivi budget IA (ai_usage) | ✅ | Budget mensuel $150, alerte à 80% |
| Modèles LLM | ✅ | claude-haiku-4-5 (triage + rédaction) |

## Missions & autopilot

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Missions manuelles | ✅ | |
| Missions autopilot (MissionTask) | ✅ | |
| Workflows réutilisables | ✅ | WorkflowDef |
| Suivi étapes et événements | ✅ | |

## Écosystème (infrastructure)

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Surveillance sites HTTP (sondes) | ✅ | Historique 90j, eco_checks |
| Incidents (détection, ack, résolution) | ✅ | |
| Diagnostic IA global | ✅ | Via ClaudeClient |
| Mission de remise en état | ✅ | Génération automatique depuis incidents |
| Contrôles FTP | ✅ | FTPS/FTP, quota, dossiers |
| État serveur (collecteur) | ⚠️ | Nécessite `deploy/collecteur-ecosysteme.py` installé sur VPS |
| Surveillance externe (heartbeat) | ⚠️ | Nécessite URL de surveillance externe configurée |

## Planificateur (28 tâches APScheduler)

| Catégorie | Nombre | État |
|-----------|--------|------|
| Messagerie (poll IMAP) | Plusieurs | ✅ |
| Sondes écosystème | Plusieurs | ✅ |
| Rapport quotidien / hebdo / mensuel | Plusieurs | ✅ |
| Nettoyage (purge données, rétention) | Plusieurs | ✅ |
| WhatsApp (contrôle fenêtre 24h) | Plusieurs | ✅ |
| Catalogue & prospection | Plusieurs | ✅ |

## Publication sociale

| Fonctionnalité | État | Notes |
|---------------|------|-------|
| Posts planifiés | ✅ | |
| Commentaires sociaux | ✅ | |
| Comptes sociaux (multi) | ✅ | |
| Heure de publication configurable | ✅ | `social_publish_hour = 10` |
