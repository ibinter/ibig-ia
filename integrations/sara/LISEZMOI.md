# SARA sur les sites des solutions

SARA est l'assistant de discussion des sites IBIG. Il répond d'après la base de connaissances
et les guides de l'agent, avec les sources citées ; s'il n'est pas sûr, il **transmet à un
conseiller** (un ticket est créé, visible dans « Support »).

```
navigateur ── sara-widget.js ──▶ sara-relay.php (serveur du site, détient la clé)
                                       └──▶ POST /api/sara/question (Agent IA IBIG)
```

La clé de l'agent n'est **jamais** dans le navigateur : seul le relais PHP la connaît.

## Installation sur un site (5 minutes)

1. **Côté agent**, une seule fois : définir `IBIG_SARA_API_KEY` (32 caractères au minimum) dans
   le `.env` du VPS, puis redémarrer l'agent. Le diagnostic doit afficher « API SARA : activée ».
2. **Sur le site**, dans un dossier `sara/` : copier `sara-relay.php` et `sara-widget.js`, puis
   copier `sara-config.example.php` en `sara-config.php` et le remplir (adresse de l'agent,
   la même clé, le pôle du site, l'adresse exacte du site dans `allowed_origins`).
3. **Dans les pages** (ou le gabarit commun), avant `</body>` :

   ```html
   <script src="/sara/sara-widget.js" data-relay="/sara/sara-relay.php"
           data-title="SARA" data-color="#1f6feb" defer></script>
   ```

4. **Tester** : ouvrir le site, cliquer sur la bulle, poser une question. Une question hors
   base doit donner « Je transmets votre question à un conseiller » et un ticket.

## Ce qui est déjà protégé

- Clé uniquement côté serveur du site ; `sara-config.php` n'est pas affiché (c'est du PHP).
- Origine vérifiée, 20 questions par 10 minutes et par visiteur (plus les plafonds de l'agent).
- Réponse affichée en texte brut (aucun HTML injecté) ; panne de l'agent : SARA passe la main.
- SARA ne répond seul que si les guides sont validés (`reponses_auto: true`) ; sinon il transmet.

## Choisir le pôle d'un site

Une solution = un pôle dans `sara-config.php` (`SOFT` pour les 14 solutions, `EDUFORM`,
`IMMOTRUST`, `MARKET`, `DIGITAL`…). SARA ne s'appuie que sur les guides de ce pôle.
