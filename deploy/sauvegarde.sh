#!/bin/sh
# Sauvegarde quotidienne (section 13) : base PostgreSQL + configuration non versionnée.
# À lancer depuis le dossier du projet, par cron sur le VPS :
#   15 2 * * * cd /opt/ibig-ia && ./deploy/sauvegarde.sh >> /var/log/ibig-sauvegarde.log 2>&1
# Conservation : 30 jours de sauvegardes quotidiennes + celle du 1er de chaque mois 12 mois.
set -eu

DEST="${IBIG_BACKUP_DIR:-/var/backups/ibig-agent}"
STAMP="$(date +%Y%m%d-%H%M)"
mkdir -p "$DEST"
umask 077

docker compose exec -T db pg_dump -U ibig --format=custom ibig_agent > "$DEST/base-$STAMP.dump"
tar -czf "$DEST/config-$STAMP.tar.gz" config/mailboxes.yaml .env 2>/dev/null \
  || tar -czf "$DEST/config-$STAMP.tar.gz" config

# Vérifie que la sauvegarde est lisible (une sauvegarde jamais relue n'en est pas une).
docker compose exec -T db pg_restore --list < "$DEST/base-$STAMP.dump" > /dev/null

find "$DEST" -name 'base-*.dump' -mtime +30 ! -name 'base-??????01-*' -delete
find "$DEST" -name 'config-*.tar.gz' -mtime +30 ! -name 'config-??????01-*' -delete
find "$DEST" -name '*-????????-*' -mtime +365 -delete

echo "$(date -Iseconds) sauvegarde OK : $DEST/base-$STAMP.dump"
# Copier aussi hors du VPS (autre hébergeur ou stockage objet) : un serveur perdu
# emporte ses propres sauvegardes.
