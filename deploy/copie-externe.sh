#!/bin/sh
# Copie la dernière sauvegarde CHIFFRÉE (deploy/sauvegarde.sh) hors du serveur, vers Google Drive,
# avec rclone. À lancer chaque nuit après la sauvegarde (voir docs/sauvegarde-externe.md).
#
#   ./deploy/copie-externe.sh
#
# Réglages (variables d'environnement, ou fichier /root/.ibig-copie-externe) :
#   IBIG_RCLONE_REMOTE   nom du « remote » rclone     (défaut : gdrive)
#   IBIG_RCLONE_FOLDER   dossier dans Drive            (défaut : IBIG-sauvegardes)
#   IBIG_KEEP_REMOTE     nombre de copies conservées   (défaut : 14)
# Seuls des fichiers déjà chiffrés (.gpg) partent du serveur ; rien n'est supprimé en local.
set -eu

CONF="${IBIG_COPY_CONF:-/root/.ibig-copie-externe}"
[ -f "$CONF" ] && . "$CONF"
REMOTE="${IBIG_RCLONE_REMOTE:-gdrive}"
FOLDER="${IBIG_RCLONE_FOLDER:-IBIG-sauvegardes}"
KEEP="${IBIG_KEEP_REMOTE:-14}"
DIR="${IBIG_BACKUP_DIR:-/var/backups/ibig-agent}"

command -v rclone >/dev/null 2>&1 || { echo "rclone n'est pas installé" >&2; exit 3; }
LAST="$(ls -1t "$DIR"/*.gpg 2>/dev/null | head -1 || true)"
[ -n "$LAST" ] || { echo "Aucune sauvegarde chiffrée dans $DIR" >&2; exit 2; }

rclone copyto "$LAST" "$REMOTE:$FOLDER/$(basename "$LAST")"
# Vérification : la copie distante a la même taille que le fichier local
LOCAL_SIZE="$(wc -c < "$LAST" | tr -d ' ')"
REMOTE_SIZE="$(rclone size --json "$REMOTE:$FOLDER/$(basename "$LAST")" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')"
if [ "$LOCAL_SIZE" != "$REMOTE_SIZE" ]; then
  echo "Copie externe INCOMPLÈTE ($LOCAL_SIZE octets en local, ${REMOTE_SIZE:-?} en ligne)" >&2
  exit 1
fi

# Rotation : on garde les $KEEP copies les plus récentes
rclone lsf "$REMOTE:$FOLDER" --files-only | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
  rclone deletefile "$REMOTE:$FOLDER/$old"
done
echo "$(date -Iseconds) copie externe OK : $(basename "$LAST") ($LOCAL_SIZE octets)"
