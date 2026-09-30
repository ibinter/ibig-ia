# Conformité au cahier des charges v1.0

Pour chaque section du cahier des charges : où la trouver dans le tableau de bord, et son
état. **Fait** = en place et testé ; **Partiel** = en place avec une limite indiquée ;
**À faire** = pas encore construit (voir aussi `feuille-de-route.md`).

| § | Exigence | Où la voir | État |
|---|---|---|---|
| 1, 5 | Un agent chef qui coordonne 6 agents, une base de connaissances commune | Menu **Agents IA** | Fait |
| 5 | Les humains n'interviennent qu'au tableau de bord : objectifs, validation, suivi | **Objectifs**, **Validations**, **Accueil** | Fait |
| 2 | 8 pôles | Partout (filtres, fiches, valideurs) | Fait |
| 3 | Indicateurs de réussite et cibles | **Indicateurs** | Fait (heures économisées : à mesurer) |
| 4 | Cartographie des 14 comptes rattachés aux pôles | **Agents IA → Rédiger une publication** (liste des comptes) | Fait |
| 6 | Agent chef : plan de la semaine, rapport quotidien, alertes | **Objectifs** (plan), **Rapports**, **Agents IA** | Fait |
| 6 | Communication : calendrier du lundi, déclinaison par réseau, briefs visuels, publications à la main | **Calendrier éditorial**, **Agents IA** | Fait |
| 6 | Contenus web : articles en brouillon (WordPress, PHP, HTML) | **Agents IA**, **Validations** | Fait (pages produits : à faire) |
| 6 | Messagerie : relève 5 min, classement, FAQ, brouillons, transferts, synthèse | **Boîtes mail**, **Essayer l'agent**, **Journal** | Fait |
| 6 | Commercial : qualification, relances J+3/7/14, démos et essais | **Prospects**, **Agents IA** | Fait |
| 6 | Support + SARA : réponses documentées, tickets | **Support**, **Base de connaissances → Guides** | Fait |
| 6 | Veille : statistiques, alertes pic / avis négatif | **Indicateurs** | Partiel : mails et WhatsApp ; avis sur les réseaux sociaux à faire |
| 7 | Base de connaissances : charte, 8 fiches, catalogues, contacts, FAQ, guides, interdits | **Base de connaissances** (modifiable en ligne) | Fait (bibliothèque de publications : dossier prévu) |
| 8 | Publication automatique sur 11 comptes via un outil multi-comptes | — | À faire : aujourd'hui, une publication validée est remise à l'équipe pour publication à la main |
| 9 | Gmail (API) + LWS (IMAP/SMTP), option A raccordement direct | **Boîtes mail** | Fait |
| 9 | Envois en masse par un service d'emailing (Brevo, Resend) | — | À faire |
| 9 | Signature de la boîte, mention sur les réponses automatiques | **Boîtes mail** (signature) | Fait |
| 10 | WhatsApp Business par l'API officielle de Meta, 24 h, consentement, STOP | **Agents IA → WhatsApp** | Fait (numéros à migrer chez Meta) |
| 10 | Chaînes WhatsApp préparées pour publication manuelle | **Calendrier éditorial** | Fait (chaînes à déclarer dans les comptes) |
| 11 | Articles 2 par mois et par site, un mot-clé, une question réelle, sources | **Agents IA → Contenus web** | Fait (sites à activer) |
| 12 | 3 niveaux, valideur + suppléant par pôle, alerte à 24 h, journal, bouton d'arrêt, revue mensuelle | **Validations**, **Journal**, **Bouton d'arrêt**, **Rapports** | Fait |
| 12 | Validation depuis WhatsApp ou par mail | — | À faire (alerte par mail en place, validation au tableau de bord) |
| 13 | Accès dédiés, coffre à secrets, messages traités comme données, données personnelles (12 mois, accès, suppression) | **Boîtes mail** (coffre), **Essayer l'agent** (message piégé) | Fait |
| 14 | Claude, VPS, PostgreSQL + pgvector, tableau de bord web | Serveur installé | Partiel : recherche par mots-clés, recherche vectorielle à brancher |
| 14 | Visuels Canva / génération d'images | — | À faire : l'agent fournit un brief visuel par publication |
| 16 | Plafond IA mensuel, alerte 80 %, modèle léger pour le tri | **Accueil** (jauge budget) | Fait |
| 17 | Recette R-01 à R-10 | `ibig-agent recette`, **Indicateurs** | Fait (outils) ; à dérouler sur 2 semaines réelles |
| 19 | Prochaines actions IBIG | **Mise en route** | En cours |
