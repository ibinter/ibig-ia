# Lot 0 — Audit de l'existant et matrice des écarts

Cahier des charges : « IBIG IA — renforcement de la plateforme existante » (version graphique
1.1, 30 octobre 2026). Audit du 30/10/2026 sur le code (dépôt `ibinter/ibig-ia`) et le serveur de
production (`agents.ibigsoft.com`). **Aucune modification, aucune migration, aucune suppression
n'a été faite pour cet audit.** La sécurité (accès, mots de passe, secrets, dépendances) a fait
l'objet d'un audit séparé : [audit-2026-09-30.md](audit-2026-09-30.md).

Légende (section 2 du cahier des charges) : **A** existante et satisfaisante · **B** existante mais
à renforcer · **C** absente · **D** non prioritaire ou hors périmètre.

## 1. Synthèse

L'intention du cahier des charges (agents qui trouvent, qualifient, relancent, avec la Direction qui
valide) est **déjà à moitié en place** : la gouvernance, les canaux, la base commerciale et le
catalogue existent. Ce qui manque est **la prospection active, la notion de mission, le cycle
devis → commande → paiement, et le pilotage financier**. Rien ne justifie une reconstruction.

| Bloc du cahier des charges | État | Verdict |
|---|---|---|
| Gouvernance, validation humaine, journal, arrêt d'urgence | en production | **A** |
| Canaux e-mail, WhatsApp, réseaux sociaux, emailing | en production | **A / B** |
| Catalogue des produits (prix, formations) | importé, 2 928 produits, mis à jour chaque semaine | **B** |
| CRM (fiche prospect, relances, essais, démonstrations) | existe, simple | **B** |
| Missions de prospection | n'existe pas | **C** |
| Recherche active de prospects, sources, déduplication | n'existe pas (les prospects arrivent par mail, WhatsApp ou import) | **C** |
| Devis, commandes, paiements, rapprochement, commissions | n'existe pas | **C** |
| Rôles et droits | 3 rôles (le cahier en demande 8) | **B** |
| Quotas et coûts | plafond mensuel global seulement | **B** |
| API `/api/v1` | une seule route publique (SARA) | **C** |
| Reporting commercial et financier | indicateurs de service, pas de chiffre d'affaires | **B / C** |

## 2. Inventaire de l'existant (matrice de la section 2)

| Élément | Constat vérifié |
|---|---|
| **Application** | Python 3.11+, FastAPI, Jinja2, SQLAlchemy 2, Alembic, APScheduler. 13 300 lignes (src), 27 pages, 87 routes de tableau de bord, 324 tests. Hébergement : VPS Docker (agent, PostgreSQL + pgvector, Caddy en HTTPS). |
| **Agents** (14 modules) | Chef (rapport, alertes), Messagerie (tri et réponses aux mails), WhatsApp, Commercial (qualification, relances J+3/J+7/J+14, essais, démonstrations), Support (guides, tickets, SARA), Communication (calendrier éditorial), Contenus web, Campagnes, Commentaires, Veille (indicateurs, alertes), Plan, Revue, Notifications, Validation WhatsApp. Ils travaillent **par fonction avec un paramètre « pôle »**, pas un agent par branche. |
| **Base de données** | PostgreSQL, 20 tables (journal, actions à valider, prospects, tickets, contacts WhatsApp, utilisateurs, usage IA, comptes de boîtes et de réseaux, publications programmées, état des tâches…), 11 migrations Alembic, testées sur SQLite et PostgreSQL. |
| **Connecteurs** | IMAP/SMTP (LWS), Gmail (API), WhatsApp Business (API Cloud Meta, modèles, STOP), publication sociale (Facebook, Instagram, Threads, LinkedIn, X, + relais Metricool/Buffer), emailing (Brevo, Mailaby), WordPress/PHP, Voyage AI (recherche par le sens), widget SARA. Aucun connecteur de paiement, de calendrier ni de téléphonie. |
| **CRM / catalogue** | Table `prospects` (e-mail unique, pôle, source, besoin, statut, score, température, prochaine étape, essais, démonstrations). Catalogue : fichiers Markdown générés depuis IBIG PARTNERS (`knowledge/produits`), mise à jour automatique le dimanche. |
| **Sécurité** | 3 rôles (admin, direction, valideur), valideurs cloisonnés par pôle, 3 niveaux d'action (automatique, validation, humain seul), secrets chiffrés, signatures des webhooks, limites de débit. Détail : audit du 30/09. |
| **Coûts** | Plafond mensuel IA (150 USD), alerte à 80 %, modèle léger pour le tri. Pas de quota par agent ni par mission. |
| **Sauvegarde** | Sauvegarde chiffrée programmée à 2 h 15 ; une sauvegarde a été faite et relue le 28/10. **À corriger avant toute migration** : le diagnostic du serveur signale « pas de copie hors du serveur » (l'audit du 30/09 la disait en place) ; la restauration n'a pas été rejouée dans cet audit. |

## 3. Matrice des écarts par fonction

| § | Fonction demandée | Existant | Classe | Réutiliser / à faire |
|---|---|---|---|---|
| 3 | Tableau de bord « Sales Center » | Accueil, indicateurs, validations, rapports | **B** | Ajouter prospects par étape, missions et alertes à l'accueil existant |
| 4 | Centre de gestion des agents (carte : mission, statut, dernier résultat, consommation, quota) | Page « Agents IA » : statut et lancement manuel | **B** | Compléter par consommation, quota restant, suspension par agent, journal filtré |
| 4 | Action « Tester » sans communication externe | Mode démonstration global (données fictives) | **B** | Simulation par agent et par mission |
| 5 | Missions de prospection (formulaire, brouillon → simulation → approbation → lancement) | Aucune notion de mission (tâches planifiées seulement) | **C** | Nouvelle entité, réutilisant la validation à trois niveaux existante |
| 6 | Recherche de prospects par sources autorisées | Aucune ; les prospects viennent des mails, de WhatsApp et d'un import CSV | **C** | Décider d'abord des sources autorisées (voir 5) |
| 6 | Score explicable, données manquantes | Score et température (agent Commercial) | **B** | Ajouter la justification et la liste des manques |
| 6/7 | Déduplication | E-mail unique | **B** | Étendre à organisation + site + téléphone ; ne jamais supprimer l'historique |
| 6 | Arrêt des relances sur refus ou désinscription | Fait (STOP, désinscription, `stop_followups`) | **A** | Conserver |
| 7 | Pipeline : nouveau, qualifié, contacté, intéressé, démo, devis, négociation, gagné, payé | 7 statuts (nouveau, qualifié, en discussion, relance, sans suite, gagné, perdu) | **B** | Ajouter devis, négociation, payé et issues (injoignable, invalide, exclu) |
| 7 | « Payé » issu d'une confirmation financière, pas d'un glisser-déposer | Sans objet (pas de finance) | **C** | Lié au paiement (§10) |
| 7 | Fiche : origine (URL, date, vérification), affectation (agent, commercial), activités | Source en texte libre ; historique dans le journal | **B** | Champs d'origine et d'affectation ; fil d'activités |
| 8 | Catalogue source de vérité, avec version, statut, date, responsable | Importé et rafraîchi, mais **sans version, statut, ni validateur**, prix non historisés | **B** | Statut et date de mise à jour par offre, historique des prix |
| 8 | Garde-fou : ne jamais inventer un prix | Contrôle des prix par nom de produit (livré le 30/10) | **A** | Conserver |
| 9 | E-mail : brouillons, envoi autorisé, réponses, désinscription | Fait (LWS, Gmail, Brevo, Mailaby) | **A** | Conserver |
| 9 | WhatsApp Business (API officielle, modèles, règles Meta) | Fait ; modèle hors 24 h livré, **à créer chez Meta** | **A / B** | Faire approuver les modèles |
| 9 | Site / chatbot | Widget SARA prêt, **non installé sur les sites** | **B** | Installer sur les sites |
| 9 | LinkedIn / Facebook : interactions autorisées | Publication ; veille des commentaires Facebook | **B** | Étendre selon les API permises |
| 9 | Boîte de réception unifiée | Boîtes séparées par compte | **C** | Vue unifiée liée au prospect |
| 9 | Téléphone | Aucun | **D** | Reporter (scripts seulement) |
| 10 | Propositions, devis versionnés, commandes | Aucun | **C** | À concevoir avec la Direction (remises, validations) |
| 10 | Paiements, webhooks vérifiés, rapprochement, anti-doublon | Aucun connecteur de paiement | **C** | Choisir le prestataire (GeniusPay est utilisé par IBIG PARTNERS) |
| 10 | Commissions | Calculées par IBIG PARTNERS, hors de cette plateforme | **C** | Se raccorder à PARTNERS, **ne pas dupliquer** |
| 11 | Agents spécialisés par branche | Agents par fonction + paramètre « pôle » | **B** | Mapper les rôles sur l'existant ; pas de nouvel agent sans besoin prouvé |
| 11 | Prospecteur B2B | Aucun | **C** | Un seul agent à ajouter, après la décision sur les sources |
| 11 | Business Intelligence | Indicateurs de service et de coût | **B** | Ajouter conversion et chiffre d'affaires rapproché |
| 12 | Modèle de données (missions, sources, activités, opportunités, devis, commandes, paiements) | 20 tables ; prospects, journal, actions à valider | **B / C** | Migrations additives uniquement (§12.1) |
| 13 | API `/api/v1` | `POST /api/sara/question` et webhooks | **C** | Ajouter en respectant les conventions actuelles |
| 14 | 8 rôles | 3 rôles | **B** | Ajouter responsable de branche, commercial, analyste, partenaire |
| 14 | Moindre privilège des agents | Niveaux d'action par type, arrêt par canal | **A / B** | Droits d'outils par agent |
| 14 | Arrêt d'urgence global, suspension par agent ou mission | Arrêt par canal | **B** | Suspension par agent et par mission |
| 15 | Quotas par agent, budget par mission, pause sur seuil d'erreurs | Plafond mensuel global | **B** | Quotas fins ; pause automatique sur échecs répétés |
| 16 | Restauration testée | Script de restauration livré, pas rejoué dans cet audit | **B** | Test de restauration sur copie avant le Lot 1 |

## 4. Risques et décisions requises de la Direction

Le cahier des charges exige une **autorisation écrite** pour toute migration. Aucun des points
ci-dessous n'est engagé.

1. **Sources autorisées de prospection.** La recherche de prospects touche aux données
   personnelles (Côte d'Ivoire et pays visés). Il faut décider des sources licites (sites officiels,
   annuaires publics, LinkedIn par ses API) avant tout développement du Prospecteur. Sans cette
   décision, le Lot 3 ne peut pas démarrer.
2. **Prestataire de paiement et règles de commission.** Aucun connecteur de paiement n'existe ;
   les commissions vivent dans IBIG PARTNERS. À arbitrer : se raccorder à PARTNERS ou à un
   prestataire commun, et la politique de remise et de commission.
3. **Sauvegarde hors serveur** : la configurer et rejouer une restauration avant la première
   migration (exigence §12.1).
4. **Qualité du catalogue.** 50 produits ont un tarif ambigu ou absent (23 chez EDUFORM, 27 dans
   les autres marques) et les essais gratuits ne sont pas publiés : le catalogue devra porter un statut et un responsable de validation (§8).
5. **Rôles** : désigner qui tient chacun des 8 rôles.

## 5. Phasage proposé (délais et coûts à fixer avec la Direction)

| Lot | Contenu réellement nécessaire | Effort relatif |
|---|---|---|
| 0 | Ce document, sauvegarde hors serveur, test de restauration | petit |
| 1 | Catalogue : statut, date, responsable, historique des prix. CRM : étapes manquantes, origine, affectation, fil d'activités | moyen |
| 2 | Missions, quotas par agent et mission, suspension, rôles supplémentaires | moyen |
| 3 | Prospecteur B2B, sources, déduplication, score explicable | moyen à fort (dépend de la décision n° 1) |
| 4 | Boîte unifiée, devis et conversion, installation de SARA et des modèles WhatsApp | moyen |
| 5 | Paiements, rapprochement, commissions, tableaux de bord financiers | fort (dépend de la décision n° 2) |

## 6. Ce qui n'est pas vérifié dans cet audit

- La restauration d'une sauvegarde (non rejouée).
- Les performances sous charge réelle (volumes de prospects et de mails inconnus).
- Le contenu de la base de production (aucune donnée de prospect n'a été lue).
- La conformité juridique de la prospection (avis juridique à prévoir).

## 7. Avancement du Lot 1 (catalogue et CRM) — 30/10/2026

Autorisation de démarrer donnée par la Direction le 30/10/2026. Méthode : sauvegarde vérifiée,
migration additive et réversible, tests, déploiement, contrôle en production.

**Avant la migration.** Sauvegarde chiffrée faite, déchiffrée et **restaurée dans une base
temporaire** : mêmes nombres de lignes que la production (journal 274, actions 114, utilisateurs 1),
schéma 0011 ; base temporaire supprimée. La table des prospects était vide en production.

**Migration 0012** (additive, aller-retour testé) : tables `catalog_offers`,
`catalog_price_history`, `prospect_activities` ; colonnes d'origine et d'affectation sur
`prospects`. Appliquée en production (schéma 0011 → 0012), données inchangées.

| Fonction (§) | Classe avant | Livré |
|---|---|---|
| Catalogue : identifiant stable, version, statut, date, responsable, historique des tarifs (§8) | B | Registre en base ; 2 928 offres (2 878 actives, 50 à confirmer) ; page « Catalogue » (statut, tarif, validation) ; une décision humaine n'est jamais défaite par un import ; offre absente = archivée, jamais supprimée |
| Pipeline CRM : étapes manquantes et issues (§7) | B | Intéressé, démo, devis, négociation, à recontacter ; injoignable, invalide, exclu. « Payé / livré » **refusé** en saisie manuelle (réservé au Lot 5). « Exclu » jamais purgé |
| Fiche prospect : origine, vérification, affectation, activités (§7) | B | Page `/prospects/{id}` : origine (adresse, date, vérification), affectation agent et commercial, notes, fil d'activités, raisons du score, informations manquantes |

**Corrigé en route.** (1) `knowledge/` est en lecture seule dans le conteneur : les fichiers du
catalogue sont désormais générés dans `exports/catalogue/` (inscriptible), ce qui corrige aussi la
tâche du dimanche, qui aurait échoué. (2) Un tarif fixé par la Direction était défait à chaque
import : la source et le tarif retenu sont maintenant mémorisés séparément.

**Tests.** 322 réussis ; 16 échecs préexistants et inchangés (support, PHP local, `bash`).

**Reste pour clore le Lot 1** : décisions de la Direction sur les 50 offres au tarif ambigu
(page Catalogue), désignation des responsables de validation, copie de sauvegarde hors serveur.

## 8. Avancement du Lot 2 (missions, quotas, supervision) — 30/10/2026

Autorisation de démarrer donnée par la Direction le 30/10/2026 (« Lot 2 »). Même méthode :
sauvegarde, migration additive et réversible, tests, déploiement, contrôle en production.

**Migration 0013** (additive, aller-retour testé) : tables `agent_controls`, `missions`,
`mission_events`. Aucune table ni colonne existante modifiée.

| Fonction (§) | Classe avant | Livré |
|---|---|---|
| Missions : formulaire, brouillon → simulation → approbation → lancement, pause, arrêt (§5, §6) | C | Service et pages `/missions` ; chaque changement d'état est daté, attribué et journalisé ; pas de saut d'étape ; une modification renvoie en brouillon |
| Simulation sans contact externe (§4) | B | Vérifie l'agent, l'arrêt d'urgence, les plafonds ; ne communique avec personne |
| Suspension d'un agent (§4, §14) | B | Page `/supervision` ; appliquée au point de passage unique `Governor.submit`, donc à toutes les actions ; une validation ne contourne pas la suspension |
| Quotas par agent et budget par mission (§15) | B | Plafonds d'actions et de coût d'IA par jour et par agent ; budget, volume total et volume par jour par mission ; alerte à 80 %, pause à 100 % |
| Pause automatique après échecs (§15) | C | Seuil d'échecs de suite par mission ; un succès remet le compte à zéro |
| Supervision (§4) | B | État, consommation du jour, quota restant, dernière action de chaque agent |

**Limite assumée.** Les missions de prospection ne cherchent encore personne : l'agent
Prospecteur arrive au Lot 3 et se branche dans `missions.RUNNERS`. Une mission lancée reste « en
cours, en attente d'un agent » (dit une seule fois au journal, et affiché sur les pages). Ce lot
livre le cadre : cycle de vie, limites, pauses, supervision.

**Reste pour la Direction** : rôles supplémentaires (responsable de branche, commercial, analyste,
partenaire), à désigner avant d'être ajoutés ; le tableau de bord actuel ne connaît que
administrateur, direction et valideur.

**Tests.** 342 réussis ; mêmes 16 échecs préexistants (support, PHP local, `bash`).
