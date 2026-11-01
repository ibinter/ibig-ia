# PLAN DE STABILISATION — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Objectif

Consolider la plateforme existante sans modifier son architecture ni ses migrations déjà appliquées. Chaque action est réversible et ne touche pas la production sans autorisation explicite.

## Priorités

### P1 — Immédiat (< 1 semaine)

| Action | Fichier/Module | Justification |
|--------|---------------|---------------|
| Vérifier que `IBIG_SECRET_KEY` fait ≥ 32 chars en prod | `vault.py` ligne 21 | Erreur au démarrage sinon |
| Vérifier que `IBIG_DATABASE_URL` pointe vers PostgreSQL en prod | `config.py` | SQLite par défaut = perte de données |
| Confirmer que le collecteur écosystème est actif sur le VPS | `deploy/collecteur-ecosysteme.py` | Sans lui, l'état serveur est absent |
| Lancer la suite de tests sur un PostgreSQL de test | `tests/` | SQLite masque certaines incompatibilités pgvector |
| Vérifier que les 19 migrations sont appliquées en prod | Alembic `alembic current` | S'assurer qu'aucune migration n'est en attente |

### P2 — Court terme (1–2 semaines)

| Action | Module | Justification |
|--------|--------|---------------|
| Configurer `whatsapp_reply_template` si utilisé | `config.py` | Sans cela, les réponses hors 24h sont refusées silencieusement |
| Vérifier les `password_env` de toutes les boîtes mail dans mailboxes.yaml | `config.py`, `vault.py` | Un secret manquant fait échouer le triage messagerie |
| Confirmer que `notification_mailbox` est défini en prod | `config.py` | Sans cela, les notifications aux valideurs échouent |
| Tester le webhook WhatsApp (GET + POST) en prod | `dashboard/app.py` | Critique pour la communication client |
| Vérifier les alertes budget IA (`budget_alert_ratio = 0.8`) | `config.py` | S'assurer que les alertes arrivent bien à la direction |

### P3 — Moyen terme (2–4 semaines)

| Action | Module | Justification |
|--------|--------|---------------|
| Ajouter une purge automatique de `eco_checks` > 90j | `scheduler.py` | La table grossit sans limite actuellement |
| Documenter la procédure de rotation de `IBIG_SECRET_KEY` | `vault.py` | Rotation sans procédure = perte de tous les secrets chiffrés |
| Mettre en place un monitoring externe (heartbeat URL) | `ecosystem.py` | Détecter une panne du VPS de l'extérieur |
| Évaluer l'ajout du 2FA pour admin/direction | `auth.py` | Renforcement sécurité accès tableau de bord |
| Revoir la longueur min MDP (10 → 12) + règle de complexité | `auth.py` | Bonne pratique ANSSI |

## Ce qu'il ne faut PAS faire

- ❌ Ne pas modifier les migrations existantes (0001–0019)
- ❌ Ne pas changer `IBIG_SECRET_KEY` sans procédure de migration des secrets
- ❌ Ne pas déployer en production sans l'accord explicite de l'utilisateur
- ❌ Ne pas toucher à la configuration Nginx ni aux crons existants
- ❌ Ne pas reconstruire de modules déjà fonctionnels

## Commandes de vérification (à exécuter sur le VPS)

```bash
# Migrations en attente ?
docker exec ibig-ia-agent-1 alembic current

# Logs des dernières 50 lignes
docker logs ibig-ia-agent-1 --tail 50

# État des conteneurs
docker compose ps

# Santé du collecteur écosystème
systemctl status collecteur-ecosysteme 2>/dev/null || echo "Collecteur non installé en service systemd"
```

## Critères de succès Mission 001

- [ ] Les 10 rapports docs/mission-001/ sont produits et commités
- [ ] La suite de tests passe sans erreur sur PostgreSQL de test
- [ ] Les 19 migrations sont appliquées en production
- [ ] Le collecteur écosystème envoie des relevés toutes les 2 minutes
- [ ] Aucun incident critique ouvert dans `/ecosysteme` sans mission assignée
