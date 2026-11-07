# Propositions pour la base de connaissances (à relire avant usage)

Relevé fait le 30/10/2026 sur les **sites publics d'IBIG**. Ce fichier est volontairement hors
de `knowledge/` : l'agent ne l'utilise pas. Une réponse de `knowledge/faq/` part AUTOMATIQUEMENT
aux clients dès qu'elle ne contient plus « À COMPLÉTER » : chaque information ci-dessous doit
donc être relue par le responsable du pôle, puis copiée à la main dans le bon fichier.

Règle suivie : rien n'est inventé. Tout ce qui n'est pas écrit sur les sites est marqué
**non précisé** (donc à décider par IBIG).

## 1. Points à trancher avant de publier (incohérences trouvées)

| Sujet | Constat | Décision à prendre |
|---|---|---|
| Nombre de solutions | Le cahier des charges parle de **14** solutions ; ibigsoft.com en présente **12** | Quelles sont les 2 autres, ou corriger « 14 » |
| Essais et paliers gratuits | **Aucun** essai ni palier gratuit n'est indiqué sur ibigsoft.com ni sur la page tarifs | Durées d'essai réelles par solution |
| Tarifs des solutions | Aucun montant public : « Le prix dépend du type de solution » ; renvoi vers des devis | Prix publics ou « sur devis » par solution |
| Formation « Intelligence Artificielle pour Professionnels » | **95 000 FCFA** dans « Prochaines sessions », mais **« Gratuit »** dans le calendrier | Quel est le bon prix |
| Sessions en double | DAF, Responsable Commercial & Marketing, Transport/Logistique, Comptable→Chef Comptable apparaissent 2 fois (identifiants différents) | Doublon ou deux vraies sessions |
| Modalités | « Présentiel » ou « hybride » selon l'endroit pour certaines formations | Modalité exacte de chaque session |
| Certification | « Attestation ou certificat selon le programme » ; non rattachée à une formation précise | Certification de chaque formation |
| WhatsApp du groupe | Non précisé sur intermark-business.com | Numéro officiel du groupe |

### Essais gratuits : ce que le code de PARTNERS laisse entrevoir (à confirmer)

Le catalogue publié (API) ne mentionne **aucun** essai gratuit. Le code source d'IBIG PARTNERS
(`sync-soft`) en cite pour 5 formules seulement sur 111, ce qui ne permet pas de remplir le
catalogue des 12 solutions : CONSTRUIRO ERP « Solo » 14 jours ; SANTAREX ERP « Pharmacie
autonome » 30 jours ; AGRIFRIK « Basique » 1 mois ; IBIG FactPro « Starter » 7 jours ; SECRETIS ERP
« Découverte » 14 jours. Ce code peut différer de ce qui est en ligne : **à confirmer par IBIG**
avant que l'agent n'en parle. Tant que ce n'est pas fait, il ne promet aucun essai.

## 2. Contacts officiels (pour `knowledge/contacts.md`)

Source : intermark-business.com et ibig-eduform.com.

- **Groupe IBIG SARL** — Abidjan, Cocody Riviera Palmeraie. Mail : contact@intermark-business.com.
  Téléphones : +225 27 22 27 60 14 · +225 07 78 88 25 92 · +225 05 56 21 76 16.
  Horaires : lundi–vendredi 8 h 30–18 h (GMT) ; samedi 9 h–16 h (GMT).
- **IBIG EDUFORM** — Mail : formation@ibig-eduform.com. Téléphones : +225 07 78 88 25 92 ·
  +225 05 65 90 47 79 · +225 01 53 59 55 44. WhatsApp : +225 07 78 88 25 92. Siège à Abidjan
  (Cocody) ; adresse précise non précisée.
- Numéros Mobile Money : **non précisés** (à fournir par IBIG ; aucun ne doit être publié sans
  figurer dans `contacts.md`).

## 3. Catalogue IBIG SOFT (pour `knowledge/catalogue-ibig-soft.md`)

Source : ibigsoft.com et ibigsoft.com/tarifs.php. Palier gratuit, durée d'essai et prix :
**non précisés** pour toutes les solutions (laisser « À COMPLÉTER » tant qu'IBIG ne les donne pas).

| N° | Solution | Adresse | Public | En une phrase (d'après le site) |
|---|---|---|---|---|
| 1 | IBIG Fleet 360 | https://ibigfleet360.com | Gestionnaires de flottes et de parcs automobiles | ERP tout-en-un pour gérer le cycle de vie des véhicules d'une flotte |
| 2 | Scolaby | https://scolaby.com | Établissements scolaires (maternelle au supérieur) | Gestion scolaire sur web et mobile |
| 3 | Zelivry | https://zelivry.com | Entreprises de livraison | Commandes, clients, livreurs, stocks et encaissements |
| 4 | Lokativo | https://lokativo.com | Agences immobilières, propriétaires, syndics | SaaS panafricain de gestion locative |
| 5 | GESCOMXEL | https://ibigsoft.com/gescomxel.php | PME, commerces, pharmacies, distributeurs | Gestion commerciale, facturation et CRM |
| 6 | STOCKFLOW ERP | https://stockflow.ibigsoft.com | PME, boutiques, distributeurs, multi-sites | ERP commercial SaaS multi-tenant |
| 7 | CONSTRUIRO ERP | https://construiro.com | Entreprises du BTP | Projets, chantiers, RH, stock, finance, comptabilité |
| 8 | SANTAREX ERP | https://santarex.ibigsoft.com | Structures de santé | Gestion hospitalière : dossiers patients, pharmacie, laboratoire, facturation |
| 9 | AGRIFRIK | https://agrifrik.ibigsoft.com | Exploitations agricoles, agro-business | ERP agricole : cultures, élevage, stocks, exportation, comptabilité SYSCOHADA |
| 10 | GESTMONEY | https://gestmoney.ibigsoft.com | Réseaux Mobile Money | Transactions, float, commissions et KYC |
| 11 | ANOUANZÊ ERP | https://anouanze.ibigsoft.com | Associations, ONG | Membres, cotisations, dons, projets, comptabilité SYCEBNL |
| 12 | QR CODE EXPRESS | https://qrcode-express.com | Non précisé | Création et gestion de QR codes, codes-barres et étiquettes |

Offres génériques du site (tarif non précisé) : **Démarrage** (site vitrine, outil Excel
automatisé), **Gestion** (application ou logiciel métier, formation et support), **Plateforme**
(ERP/SaaS, modules évolutifs, multi-utilisateurs, hébergement et maintenance).

## 4. Catalogue des formations (pour `knowledge/catalogue-formations.md`)

**Correction (30/10/2026)** : le catalogue complet compte **2 295 formations en 25 domaines**
(page `catalogue-formations.php` du site). Le tableau ci-dessous ne donne que les **10 sessions
mises en avant** sur la page des sessions, pas le catalogue. La liste des 25 domaines et de leurs
effectifs est dans `knowledge/catalogue-formations.md`.

Source : ibig-eduform.com (page sessions, relevée le 30/10/2026). Prix en FCFA.

| Formation | Durée | Modalité | Prochaine date | Prix |
|---|---|---|---|---|
| Intelligence Artificielle pour Professionnels | 7 h | présentiel / hybride | 31/10/2026 | 95 000 **ou « Gratuit »** (à trancher) |
| DAF – Directeur Administratif & Financier | 60 h | en ligne ou présentiel | 02/11/2026 | 400 000 en ligne ; 450 000 présentiel |
| Responsable Commercial & Marketing | 30 h | en ligne ou présentiel | 02/11/2026 | 200 000 en ligne ; 250 000 présentiel |
| Transformation Digitale des PME | 25 h | en ligne ou présentiel | 05/11/2026 | 200 000 en ligne ; 250 000 présentiel |
| Responsable HSE / QHSE | 25 h | hybride | 12/10/2026 (passée) | à partir de 250 000 |
| Transport, Logistique & Supply Chain Management | 30 h | hybride | 19/10/2026 (passée) | à partir de 200 000 |
| De Comptable à Chef Comptable | 30 h | hybride | 26/10/2026 (passée) | à partir de 200 000 |
| Odoo – initiation ERP | 2 samedis (12 h) | présentiel | 07/11/2026 | Gratuit |
| Gestion Immobilière Professionnelle | 25 h | hybride | 09/11/2026 | à partir de 200 000 |
| Gestion de Projets Humanitaires & ONG | 25 h | hybride | 09/11/2026 | à partir de 200 000 |

**Les dates passent vite** : un catalogue publié par l'agent avec des dates dépassées serait faux.
Mieux vaut y mettre les formations et les prix, et renvoyer au site pour le calendrier.

Inscription : formulaire de préinscription du site, puis validation, puis paiement. **La
préinscription ne garantit pas la place**, confirmée après validation et paiement.
Parrainage : remise de 10 % sur la prochaine formation pour chaque personne recommandée qui
s'inscrit. Modalités de paiement : non précisées.

## 5. Brouillons de FAQ — uniquement ce que les sites affirment

À copier dans `knowledge/faq/…` **après relecture**. Les autres questions restent « À COMPLÉTER ».

**EDUFORM — Comment m'inscrire à une formation ?**
Vous remplissez le formulaire de préinscription sur ibig-eduform.com. Notre équipe valide votre
dossier, puis vous réglez la formation. Attention : la préinscription ne garantit pas la place,
qui est confirmée après validation et paiement. Contact : formation@ibig-eduform.com.

**EDUFORM — Y a-t-il une remise pour un proche que je recommande ?**
Oui : pour chaque personne que vous recommandez et qui s'inscrit, vous bénéficiez de 10 % de
remise sur votre prochaine formation.

**EDUFORM — Comment contacter l'équipe ?**
Par mail à formation@ibig-eduform.com, par téléphone au +225 07 78 88 25 92, +225 05 65 90 47 79
ou +225 01 53 59 55 44, ou par WhatsApp au +225 07 78 88 25 92.

**SOFT — Quelles solutions proposez-vous ?**
IBIG SOFT édite des solutions de gestion : IBIG Fleet 360 (flottes), Scolaby (établissements
scolaires), Zelivry (livraison), Lokativo (gestion locative), GESCOMXEL (gestion commerciale),
STOCKFLOW ERP (ERP commercial), CONSTRUIRO ERP (BTP), SANTAREX ERP (santé), AGRIFRIK
(agriculture), GESTMONEY (Mobile Money), ANOUANZÊ ERP (associations et ONG) et QR CODE EXPRESS.
Le détail est sur ibigsoft.com. *(À relire : le cahier des charges mentionne 14 solutions.)*

**SOFT — Combien coûte une solution ?**
Le prix dépend du type de solution. Pour un tarif adapté à votre besoin, demandez un devis via la
page de contact d'ibigsoft.com.

**GROUPE — Quels sont vos horaires et où êtes-vous ?**
Nous sommes à Abidjan, Cocody Riviera Palmeraie. Nous vous accueillons du lundi au vendredi de
8 h 30 à 18 h (GMT) et le samedi de 9 h à 16 h (GMT). Contact : contact@intermark-business.com.

## 6. Ce que seules les équipes d'IBIG peuvent fournir

Durées d'essai et paliers gratuits des solutions ; prix publics ou « sur devis » ; réponses sur
la protection et la sauvegarde des données ; garanties et délais de support ; certification de
chaque formation ; numéros Mobile Money ; WhatsApp du groupe ; ton de chaque pôle et exemples
de formulations. L'agent ne répondra jamais seul sur ces points tant qu'ils restent « À COMPLÉTER ».
