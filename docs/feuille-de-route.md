# Feuille de route

4 phases d'environ un mois ; on ne passe à la suivante qu'après la recette de la
précédente (section 15). Le découpage ci-dessous est proposé à partir du résumé exécutif
(« commencer par le tri et la réponse aux mails, et le calendrier éditorial ») ; à valider
avec IBIG.

## Prérequis IBIG

Actions de la section 19, qui débloquent la phase 1 :

- [ ] Nommer le responsable de l'agent et un valideur (+ suppléant) par pôle → adresses dans `config/poles.yaml`, comptes avec `ibig-agent utilisateur ajouter`
- [ ] Lister toutes les adresses mail (hébergeur, pôle, responsable) → `config/mailboxes.yaml`
- [ ] Lister les numéros WhatsApp Business et les chaînes ; choisir 1 ou 2 numéros à automatiser
- [ ] Préciser le pôle du groupe Facebook 367494932590382 et compléter les comptes IBIG SOFT → `config/canaux.yaml`
- [ ] Indiquer la technologie de chaque site (WordPress ou PHP maison)
- [ ] Rédiger les 8 fiches pôles → `knowledge/poles/*.md` (retirer `statut: a_completer`)
- [ ] Réunir les FAQ → `knowledge/faq/` et les 20 meilleures publications → `knowledge/publications/`
- [ ] Compléter les contacts officiels → `knowledge/contacts.md`
- [ ] Choisir l'option A (raccordement direct, implémentée) ou B (centralisation) pour les mails
- [ ] Réserver le VPS ; ouvrir les comptes de l'outil multi-comptes et du service d'emailing
- [ ] Créer une clé API Anthropic (offre professionnelle) et fixer le plafond mensuel

## Phase 1 — Messagerie et socle (mois 1)

- [x] Gouvernance 3 niveaux, journal, bouton d'arrêt, alerte 24 h
- [x] Base de connaissances en fichiers + contrôle des faits
- [x] Agent Messagerie (Gmail API + IMAP/SMTP LWS)
- [x] Tableau de bord de validation
- [x] Rapport quotidien de 8 h
- [x] Plafond de dépense IA + alerte à 80 %
- [ ] Raccorder les vraies boîtes et mesurer le point de départ pendant un mois (section 3)
- [x] Comptes nominatifs pour le tableau de bord, droits par pôle et par rôle
- [x] Alertes mail aux valideurs, relance du suppléant après 24 h, niveau 3 à la direction
- [ ] Validation depuis WhatsApp (après la migration vers l'API Cloud, phase 3)
- [ ] Migrations de schéma de base (Alembic) avant la mise en production
- [ ] Recette sur 2 semaines d'essai réel (voir ci-dessous)

## Phase 2 — Communication et contenus web (mois 2)

- [x] Calendrier éditorial hebdomadaire (génération + validation)
- [ ] Raccordement de l'outil multi-comptes (Metricool, Buffer…) pour programmer les posts validés
- [ ] Briefs visuels vers Canva (modèles aux couleurs IBIG)
- [ ] Agent Contenus web : WordPress (API REST, compte « Auteur ») et module d'entrée
      sécurisé réutilisable pour les sites PHP maison ; export HTML pour les sites sans back-office
- [ ] Préparation hebdomadaire des messages des chaînes WhatsApp (publication manuelle)

## Phase 3 — Commercial, Support, WhatsApp, Veille (mois 3)

- [ ] Migration d'1 ou 2 numéros vers la plateforme WhatsApp Business (API Cloud Meta),
      modèles de messages validés par Meta, gestion du consentement
- [ ] Agent Commercial : qualification, séquences de relance, suivi des démos et essais
- [ ] Agent Support : réponses documentées sur les 14 solutions et les formations ;
      SARA comme visage public
- [ ] Agent Veille : statistiques, e-réputation, alertes (avis négatif, pic de messages)
- [ ] Emailing (Brevo ou Resend) avec domaine authentifié SPF / DKIM / DMARC

## Phase 4 — Consolidation et produit (mois 4)

- [ ] Recherche sémantique pgvector derrière `KnowledgeBase.search()`
- [ ] Revue mensuelle outillée (erreurs, plaintes, indicateurs)
- [ ] Étude du passage en produit IBIG SOFT (moteur de licences existant)

## Recette (section 17)

| Réf. | Critère | Bloquant | Couverture automatique |
|---|---|---|---|
| R-01 | 0 mail manqué sur 2 semaines | Oui | `test_no_mail_missed_and_no_duplicate`, `test_broken_mailbox_is_reported` |
| R-02 | 95 % de bons classements (200 mails) | Oui | À mesurer sur échantillon réel |
| R-03 | Aucune action de niveau 3 exécutée | Oui | `test_level3_is_never_executed_by_agent` |
| R-04 | Aucun contenu publié sans validation | Oui | `test_level2_never_executes_without_approval`, `test_calendar_posts_wait_for_validation` |
| R-05 | 0 erreur factuelle sur 50 contenus | Oui | `test_invented_facts_are_caught` + relecture humaine |
| R-06 | 10 mails piégés sur 10 signalés | Oui | `test_security.py` (10 cas) |
| R-07 | Arrêt en moins d'1 minute | Oui | `test_kill_switch_blocks_everything_immediately` |
| R-08 | 80 % des brouillons validés sans modification majeure | Non | Suivi via `modifie_par_valideur` au journal |
| R-09 | Calendrier livré chaque lundi | Non | Tâche planifiée du lundi |
| R-10 | Rapport quotidien à l'heure | Non | Tâche planifiée de 8 h |
