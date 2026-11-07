# INVENTAIRE DES AGENTS — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Les 14 agents

| # | Fichier | Classe | Rôle principal |
|---|---------|--------|----------------|
| 1 | `agents/campagnes.py` | `CampaignWriter` | Rédaction des campagnes email marketing |
| 2 | `agents/chef.py` | `ChefAgent` | Coordination générale, synthèse quotidienne |
| 3 | `agents/commentaires.py` | `CommentWatcher` | Surveillance et modération des commentaires sociaux |
| 4 | `agents/commercial.py` | `CommercialAgent` | Suivi des prospects, relances commerciales |
| 5 | `agents/communication.py` | `CommunicationAgent` | Coordination communication inter-pôles |
| 6 | `agents/contenus_web.py` | `ContenusWebAgent` | Génération d'articles WordPress |
| 7 | `agents/messagerie.py` | `MessagerieAgent` | Triage et réponse aux emails entrants |
| 8 | `agents/notifications.py` | `ValidatorNotifier` | Envoi des notifications aux valideurs |
| 9 | `agents/plan.py` | `WeeklyPlanner` | Planification hebdomadaire |
| 10 | `agents/revue.py` | `MonthlyReviewer` | Revue mensuelle (bilan, rapport direction) |
| 11 | `agents/support.py` | `SupportAgent` | Gestion des tickets support client |
| 12 | `agents/validation_whatsapp.py` | `WhatsAppValidation` | Validation des messages WhatsApp hors fenêtre 24h |
| 13 | `agents/veille.py` | `VeilleAgent` | Veille des indicateurs, alertes direction |
| 14 | `agents/whatsapp.py` | `WhatsAppAgent` | Traitement des messages WhatsApp entrants |

## Accès via `Runtime`

Chaque agent est exposé comme propriété lazy du dataclass `Runtime` :

```python
rt.chef          # ChefAgent
rt.messagerie    # MessagerieAgent
rt.whatsapp      # WhatsAppAgent
rt.veille        # VeilleAgent
rt.commercial    # CommercialAgent
rt.support       # SupportAgent
rt.campagnes     # CampaignWriter
rt.contenus_web  # ContenusWebAgent
rt.communication # CommunicationAgent
rt.commentaires  # CommentWatcher
rt.plan          # WeeklyPlanner
rt.revue         # MonthlyReviewer
rt.notifications # ValidatorNotifier
rt.validation_whatsapp  # WhatsAppValidation
```

## Services (non-agents)

| Propriété Runtime | Classe | Rôle |
|------------------|--------|------|
| `rt.autopilot` | `AutopilotService` | Missions IA autonomes (MissionTask) |
| `rt.ecosystem` | `EcosystemService` | Surveillance infrastructure |
| `rt.knowledge` | `KnowledgeBase` | Base de connaissances + RAG |
| `rt.governor` | `Governor` | Contrôle gouvernance des actions |
| `rt.org` | `OrgConfig` | Pôles, boîtes, sites |

## Gouvernance des agents

Tous les agents utilisent `rt.governor.execute(action, level, fn)` :
- **Niveau 1** (automatique) : analyse, lecture, brouillons
- **Niveau 2** (validation humaine requise) : envoi de contenu, publications préparées
- **Niveau 3** (toujours humain) : paiements, suppressions, messages directs aux clients

Le killswitch `rt.governor.set_stopped(True)` bloque toutes les exécutions de niveau 1 et 2.

## Observations

- Aucun agent ne communique directement avec un autre : ils passent tous par `Runtime`.
- Les agents WhatsApp et messagerie sont les plus sensibles (données clients directes).
- `ValidatorNotifier` est un agent de notification pure, sans décision IA.
- `WeeklyPlanner` et `MonthlyReviewer` génèrent des rapports de synthèse destinés à la direction.
