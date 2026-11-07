# Raccorder Mailaby à l'Agent IA IBIG

Mailaby (`ibigroupsarl.com/admin/mailaby`) envoie les campagnes : l'agent rédige, un humain
valide, puis la campagne part par Mailaby au lieu de Brevo. Étiquettes de contacts =
listes de l'agent. Désinscription, suivi des ouvertures et rejets restent ceux de Mailaby.

## Ce que le point d'entrée permet (et interdit)

`api_agent.php` n'accepte que le jeton de l'agent (en-tête `X-Agent-Token`). Il ne donne
accès ni aux contacts, ni aux mots de passe des comptes d'envoi, ni à la clé de chiffrement.
Il sait seulement : lister les étiquettes (nom + effectif) et les comptes d'envoi (nom +
adresse), et créer une campagne. Elle est lancée immédiatement ou planifiée, mais l'agent
ne la demande qu'**après validation humaine**.

## Installation (une fois)

1. **Copier** `api_agent.php` dans `admin/mailaby/` sur le serveur.
2. **Créer un jeton** (long, aléatoire) et calculer son empreinte SHA-256, par exemple :

   ```bash
   jeton=$(openssl rand -hex 32); echo "$jeton"          # à coller dans l'agent (Services)
   printf '%s' "$jeton" | openssl dgst -sha256 | awk '{print $NF}'   # empreinte
   ```

3. **Déposer l'empreinte** (64 caractères, rien d'autre) dans le fichier
   `admin/mailaby/storage/agent_api.hash`. Le dossier `storage/` est fermé au web. Sans ce
   fichier, le point d'entrée répond « non activé » : supprimer le fichier le coupe aussitôt.
4. **Vérifier** qu'un compte d'envoi existe dans Mailaby (menu *Comptes*) : c'est lui qui
   envoie, retrouvé par son adresse.
5. **Dans l'agent** : menu **Services → Mailaby** : adresse du module
   (`https://ibigroupsarl.com/admin/mailaby`), jeton, nom de l'expéditeur et adresse du compte
   d'envoi Mailaby. Enregistrer, puis **Tester la connexion**.

Pour revenir à Brevo : bouton *Débrancher Mailaby* (ou supprimer `agent_api.hash`).

## Après une mise à jour de Mailaby

Rouvrir `install.php` et cliquer *Mettre à jour*. Tant que le schéma n'est pas à jour, le
point d'entrée répond 503.
