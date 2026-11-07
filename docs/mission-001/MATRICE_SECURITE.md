# MATRICE DE SÉCURITÉ — IBIG-IA
*Mission 001 — Audit technique, 1er novembre 2026*

## Authentification & sessions

| Mécanisme | Détail | Statut |
|-----------|--------|--------|
| Hachage mot de passe | PBKDF2-SHA256, 310 000 itérations | ✅ Conforme OWASP 2024 |
| Longueur minimale MDP | 10 caractères (`MIN_PASSWORD = 10`) | ✅ |
| Durée de session | 12h (`SESSION_SECONDS = 43200`) | ✅ |
| Cookie | `ibig_session`, httponly, samesite=strict | ✅ |
| Liens d'action | Valides 48h (`ACTION_LINK_SECONDS = 172800`) | ✅ |
| Rate-limiting login | 5 échecs/utilisateur, 20/IP, verrouillage 15 min | ✅ |

## Protection CSRF & headers

| Mécanisme | Détail | Statut |
|-----------|--------|--------|
| CSRF | Vérification Origin/Referer sur non-GET | ✅ |
| Content-Security-Policy | Strict (défini dans `app.py`) | ✅ |
| Proxies de confiance | `127.0.0.1,::1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16` | ✅ |

## Chiffrement des secrets

| Secret | Mécanisme | Stockage |
|--------|-----------|---------|
| Mots de passe boîtes mail | Fernet (AES-128-CBC + HMAC-SHA256) via `vault.py` | Colonne `password_enc` en base |
| Jetons WhatsApp | Fernet via `vault.py` | Colonne `token_value` chiffrée |
| Identifiants FTP | Fernet via `vault.py` | Colonne `password_enc` en base |
| Clé de dérivation | SHA-256 de `"ibig-vault:" + IBIG_SECRET_KEY` | Variable d'environnement uniquement |
| Clé session dashboard | `IBIG_SECRET_KEY` | Variable d'environnement uniquement |

> ⚠️ Changer `IBIG_SECRET_KEY` invalide **tous** les secrets chiffrés en base : obligation de les ressaisir.

## Contrôle d'accès

| Rôle | Périmètre |
|------|-----------|
| `admin` | Accès complet (administration, tous les modules) |
| `direction` | Accès complet sauf gestion des utilisateurs |
| `valideur` | Lecture + validation uniquement ; `/ecosysteme` interdit |

## Gouvernance IA (Governor)

| Niveau | Déclencheur | Actions concernées |
|--------|-------------|-------------------|
| 1 | Automatique | Lecture, analyse, brouillons internes, synthèses |
| 2 | Clic humain requis | Envoi emails, publications, réponses clients préparées |
| 3 | Toujours humain | Paiements, suppressions, messages directs, déploiements |
| — | Action inconnue | Par défaut → niveau 3 |
| Killswitch | `set_stopped(True)` | Bloque niveaux 1 et 2 immédiatement |

## API externes

| Point d'entrée | Authentification | Niveau risque |
|---------------|-----------------|---------------|
| `/api/sara/question` | Bearer token `IBIG_SARA_API_KEY` | Moyen (lecture-réponse IA) |
| `/webhooks/whatsapp` (POST) | Signature HMAC-SHA256 Meta + `whatsapp_app_secret` | Élevé (données clients) |
| `/webhooks/whatsapp` (GET) | `whatsapp_verify_token` | Faible (vérification Meta) |

## Rétention des données personnelles

- Conservation configurée : **12 mois** (`retention_months = 12`)
- Fuseau horaire : `Africa/Abidjan`
- Pas de chiffrement des données personnelles en base (prospects, contacts WA) en dehors des secrets

## Points à surveiller

| Risque | Niveau | Recommandation |
|--------|--------|----------------|
| `IBIG_SECRET_KEY` partagée entre vault et sessions | Moyen | Envisager des clés séparées |
| Longueur min MDP 10 chars (pas de complexité imposée) | Faible | Acceptable, peut être renforcé |
| Pas de 2FA | Moyen | Prévoir pour les rôles admin/direction |
| Données prospects en clair en base | Faible | Acceptable pour usage interne |
| Rotation des sessions sans révocation explicite | Faible | La durée 12h limite l'exposition |
| WhatsApp app_secret en env var | Faible | Pratique standard, protégé par VPS isolé |
