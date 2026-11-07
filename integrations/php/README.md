# Module d'entrée des articles — sites PHP maison

Module réutilisable pour tous les sites PHP d'IBIG (ibigsoft.com, sites des solutions…).
Il reçoit de l'agent IA un article **en brouillon** ; un humain relit puis met en ligne.

## Installation (environ 10 minutes par site)

1. Copier `ibig-article-inbox.php` à la racine publique du site.
2. Copier `ibig-inbox-config.sample.php` en `ibig-inbox-config.php` à côté (ou hors de la
   racine web, avec la variable d'environnement `IBIG_INBOX_CONFIG`), puis renseigner :
   * `secret` : secret partagé de 32 caractères minimum
     (`php -r "echo bin2hex(random_bytes(32)), PHP_EOL;"`) ;
   * `drafts_dir` : dossier des brouillons, **hors de la racine web** ;
   * facultatif `on_draft` : fonction qui enregistre le brouillon dans la base du site.
3. Côté agent, dans `config/sites.yaml` : `technologie: php`, `endpoint_url` (adresse du
   fichier), `secret_env` (nom de la variable qui contient le même secret), `actif: true`.
4. Tester : `ibig-agent article --site https://ibigsoft.com`, valider le brouillon dans le
   tableau de bord, puis vérifier le fichier reçu dans `drafts_dir`.

## Sécurité

| Protection | Détail |
|---|---|
| Authentification | Signature HMAC-SHA256 de `<horodatage>.<corps>` (en-têtes `X-IBIG-Timestamp`, `X-IBIG-Signature`) |
| Anti-rejeu | Horodatage à ± 5 min, chaque signature n'est acceptée qu'une fois |
| Taille | 1 Mo maximum |
| Contenu | Balises et attributs actifs refusés (`script`, `iframe`, `on…=`, `javascript:`) ; l'agent envoie déjà un HTML nettoyé |
| Stockage | Hors racine web, fichiers en `0640` |

Réponses : `201` brouillon reçu, `400` données invalides, `401` signature ou horodatage
invalide, `405` méthode, `409` requête rejouée, `413` trop volumineux, `415` pas du JSON,
`422` contenu actif, `500` configuration.
