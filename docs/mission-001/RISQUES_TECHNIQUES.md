# RISQUES TECHNIQUES — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Matrice des risques

### Risques critiques (probabilité × impact élevés)

| # | Risque | Probabilité | Impact | Mitigation existante | Action requise |
|---|--------|-------------|--------|---------------------|----------------|
| R1 | `IBIG_SECRET_KEY` trop courte ou changée sans procédure | Moyen | Critique | Vérification au démarrage (≥ 32 chars) | Documenter la procédure de rotation |
| R2 | PostgreSQL non configuré (SQLite par défaut) | Moyen | Critique | Valeur par défaut sqlite dans config.py | Vérifier DATABASE_URL en prod |
| R3 | Secrets chiffrés illisibles après rotation de clé | Faible | Critique | Message d'erreur explicite (VaultError) | Procédure de migration des secrets |
| R4 | Panne VPS sans détection externe | Moyen | Élevé | Heartbeat optionnel | Activer surveillance externe |

### Risques élevés

| # | Risque | Probabilité | Impact | Mitigation existante | Action requise |
|---|--------|-------------|--------|---------------------|----------------|
| R5 | Tâche planifiée silencieusement en échec | Moyen | Élevé | `tracked()` + alertes après N échecs consécutifs | Vérifier seuil N et destinataire alerte |
| R6 | Réponses WhatsApp hors fenêtre 24h bloquées | Moyen | Élevé | Refus explicite si template non configuré | Configurer `whatsapp_reply_template` |
| R7 | Boîte mail sans secret → triage messagerie en échec | Moyen | Élevé | `RuntimeError` explicite | Auditer tous les `password_env` |
| R8 | Budget IA dépassé ($150/mois) → panne silencieuse des agents | Faible | Élevé | Alerte à 80%, tracking `ai_usage` | Vérifier que l'alerte parvient bien |
| R9 | Collecteur écosystème non installé → état serveur absent | Moyen | Moyen | Message dans l'UI si absent | Installer et activer le collecteur |

### Risques moyens

| # | Risque | Probabilité | Impact | Mitigation existante | Action requise |
|---|--------|-------------|--------|---------------------|----------------|
| R10 | Table `eco_checks` grossissant sans purge | Élevé | Moyen | Aucune purge auto actuellement | Ajouter tâche planifiée purge > 90j |
| R11 | Clé SARA exposée dans les logs | Faible | Moyen | Jamais affichée dans les réponses | Vérifier les niveaux de log |
| R12 | YAML config invalide → démarrage en échec | Faible | Élevé | Erreur au démarrage (fail fast) | Ajouter validation YAML au CI |
| R13 | `valideur_defaut` vide → notifications non envoyées | Moyen | Moyen | Aucune vérification au démarrage | Vérifier en prod |
| R14 | Absence de 2FA pour admin/direction | Élevé | Moyen | Rate-limiting + sessions 12h | Évaluer ajout 2FA (P3) |

### Risques faibles

| # | Risque | Probabilité | Impact | Mitigation existante | Action requise |
|---|--------|-------------|--------|---------------------|----------------|
| R15 | pgvector non installé → SemanticIndex en échec | Faible | Faible | Erreur explicite à l'initialisation | Vérifier extension en prod |
| R16 | Un seul conteneur (pas de haute disponibilité) | — | Moyen | Architecture assumée | Documenter procédure de redémarrage |
| R17 | Pas de backup automatique de la base documenté | Moyen | Élevé | `state/sauvegarde.json` (deploy/sauvegarde.sh) | Vérifier fréquence et destination |

## Risques liés à la gouvernance IA

| # | Risque | Mitigation |
|---|--------|-----------|
| R18 | Agent envoie un message sans validation (niveau 2 bypassé) | Impossible par architecture : Governor est transversal à tous les agents |
| R19 | Killswitch non activé en cas de dysfonctionnement | `set_stopped(True)` accessible depuis le dashboard (admin/direction) |
| R20 | Action inconnue exécutée automatiquement | Par défaut → niveau 3 (toujours humain) dans le Governor |

## Risques résiduels acceptés

- Architecture mono-instance (pas de HA) : assumé pour la taille actuelle de l'entreprise.
- SQLite par défaut : acceptable pour les tests, dangereux seulement si mal configuré en prod.
- Pas de chiffrement des données personnelles en base (hors secrets) : acceptable pour usage interne uniquement.
