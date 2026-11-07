# CARTOGRAPHIE DES ROUTES API — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Sécurité transversale

- **Authentification** : cookie `ibig_session` (PBKDF2-SHA256, 12h, httponly, samesite=strict)
- **CSRF** : vérification Origin/Referer sur tous les POST/PUT/DELETE
- **CSP** : Content-Security-Policy strict (défini dans `dashboard/app.py`)
- **Rate-limiting** : 5 échecs/utilisateur, 20/IP → verrouillage 15 min
- **Rôles** : `admin` > `direction` > `valideur` (certaines pages refusent `valideur`)

## Modules de routes (12 modules)

### 1. Authentification
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/connexion` | Page de connexion |
| POST | `/connexion` | Traitement login |
| GET/POST | `/deconnexion` | Déconnexion |
| GET/POST | `/changer-mot-de-passe` | Changement MDP |
| GET | `/action/{token}` | Lien d'action 48h (validation email) |

### 2. Dashboard & Ma journée
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/` | Redirection vers `/dashboard` |
| GET | `/dashboard` | Page d'arrivée post-connexion |
| GET | `/ma-journee` | Page d'ouverture direction (état, priorités, argent, ventes) |

### 3. Messagerie
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/messagerie` | Liste des messages |
| POST | `/messagerie/{id}/traiter` | Marquer traité |
| POST | `/messagerie/{id}/repondre` | Répondre (niveau 2 Governor) |

### 4. Validation (actions en attente)
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/validation` | Liste des actions en attente |
| POST | `/validation/{id}/approuver` | Approuver une action |
| POST | `/validation/{id}/refuser` | Refuser une action |

### 5. Commercial & CRM
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/prospects` | Liste des prospects |
| GET/POST | `/prospects/nouveau` | Créer un prospect |
| GET | `/prospects/{id}` | Fiche prospect |
| POST | `/prospects/{id}/activite` | Ajouter une activité |

### 6. Support
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/support` | Liste des tickets |
| GET | `/support/{id}` | Détail ticket |
| POST | `/support/{id}/repondre` | Répondre au ticket |

### 7. Missions & Autopilot
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/missions` | Liste des missions |
| GET | `/missions/{id}` | Détail mission |
| POST | `/missions/{id}/valider` | Valider une étape |
| POST | `/missions/{id}/annuler` | Annuler la mission |

### 8. Catalogue & Devis
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/catalogue` | Catalogue des offres |
| GET | `/devis` | Liste des devis |
| POST | `/devis/nouveau` | Créer un devis |
| GET | `/devis/{id}` | Détail devis |

### 9. Finance & Commissions
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/finance` | Tableau de bord financier |
| GET | `/commissions` | Suivi des commissions |

### 10. Écosystème (direction/admin uniquement)
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/ecosysteme` | Centre de contrôle |
| GET | `/ecosysteme/live` | Partial HTMX (rafraîchissement 30s) |
| POST | `/ecosysteme/sonder` | Lancer une mesure manuelle |
| POST | `/ecosysteme/diagnostiquer` | Diagnostic IA global |
| POST | `/ecosysteme/mission-globale` | Créer mission de remise en état |
| POST | `/ecosysteme/actif` | Ajouter un actif |
| POST | `/ecosysteme/import` | Import en lot |
| POST | `/ecosysteme/actif/{id}/{action}` | suivre / ignorer |
| POST | `/ecosysteme/incident/{id}/{action}` | mission / analyser / pris / resolu / rouvrir |
| POST | `/ecosysteme/ftp` | Enregistrer identifiants FTP |
| POST | `/ecosysteme/ftp/{id}/verifier` | Lancer vérification FTP |
| POST | `/ecosysteme/ftp/{id}/supprimer` | Supprimer identifiants FTP |
| POST | `/ecosysteme/battement` | Configurer surveillance externe |
| POST | `/ecosysteme/inventaire` | Recharger inventaire YAML |

### 11. Administration
| Méthode | URL | Rôle |
|---------|-----|------|
| GET | `/admin` | Page d'administration (admin uniquement) |
| POST | `/admin/utilisateur` | Créer/modifier utilisateur |
| POST | `/admin/parametres` | Mettre à jour les paramètres |

### 12. API REST externe
| Méthode | URL | Auth | Rôle |
|---------|-----|------|------|
| POST | `/api/sara/question` | Bearer token (`IBIG_SARA_API_KEY`) | API SARA (serveurs solutions) |
| POST | `/webhooks/whatsapp` | Signature Meta (HMAC-SHA256) | Webhook WhatsApp Business |
| GET | `/webhooks/whatsapp` | Token de vérification Meta | Validation webhook Meta |

## Observations

- Le pattern HTMX est utilisé pour les mises à jour partielles (live, dashboard).
- `/ecosysteme/live` est appelé toutes les 30 secondes par le navigateur (polling HTMX).
- Les actions des agents (répondre, envoyer) passent toutes par le Governor avant exécution.
- Il n'y a pas d'API REST publique pour les actions métier : tout passe par le dashboard HTML.
