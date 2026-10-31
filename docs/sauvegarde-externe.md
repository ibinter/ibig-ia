# Copie externe des sauvegardes (Google Drive)

Les sauvegardes chiffrées (`/var/backups/ibig-agent/*.gpg`) restent aujourd'hui sur le serveur. Ce
mode d'emploi les copie chaque nuit dans un dossier Google Drive. Les fichiers partent déjà
chiffrés : Google ne peut pas les lire. **Conservez la phrase de chiffrement
(`/root/.ibig-sauvegarde.key`) ailleurs aussi** : sans elle, une sauvegarde ne se restaure pas.

## Mise en place (une fois)

1. Sur un ordinateur avec navigateur : installer rclone (https://rclone.org/install/), puis
   `rclone config` → *New remote* → nom `gdrive` → type `drive` → portée `drive.file` →
   se connecter avec le compte Google qui recevra les copies.
2. Envoyer la configuration obtenue (`rclone config file` donne son emplacement) sur le serveur,
   dans `/root/.config/rclone/rclone.conf`, droits `600`.
3. Sur le serveur : installer rclone (`curl https://rclone.org/install.sh | bash`).
4. Tester : `./deploy/sauvegarde.sh && ./deploy/copie-externe.sh`.
5. Planifier après la sauvegarde nocturne (`crontab -e`) :
   `30 2 * * * cd /opt/ibig-ia && ./deploy/copie-externe.sh >> /var/log/ibig-copie-externe.log 2>&1`

Le script vérifie que la copie en ligne a la même taille que le fichier local et conserve les
14 copies les plus récentes (réglable avec `IBIG_KEEP_REMOTE`).
