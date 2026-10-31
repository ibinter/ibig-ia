#!/bin/sh
# Sauvegarde quotidienne (section 13) : base PostgreSQL + configuration non versionnée
# (.env, boîtes mail), réunies dans une seule archive, chiffrée, copiée hors du serveur.
#
# À lancer depuis le dossier du projet, par cron sur le VPS :
#   15 2 * * * cd /opt/ibig-ia && ./deploy/sauvegarde.sh >> /var/log/ibig-sauvegarde.log 2>&1
#
# Première fois : ./deploy/sauvegarde.sh --init   (crée la phrase de chiffrement)
#
# Réglages (lignes IBIG_BACKUP_* du fichier .env, toutes facultatives) :
#   IBIG_BACKUP_DIR        dossier local            (défaut /var/backups/ibig-agent)
#   IBIG_BACKUP_KEY_FILE   phrase de chiffrement    (défaut /root/.ibig-sauvegarde.key)
#   IBIG_BACKUP_RCLONE     copie hors serveur par rclone, ex. « b2:ibig-sauvegardes »
#   IBIG_BACKUP_SSH        copie hors serveur par scp,    ex. « sauve@autre-serveur:/sauvegardes »
#   IBIG_BACKUP_REMOTE_DAYS  durée de conservation de la copie distante (défaut 90 jours)
#
# Conservation locale : 30 jours + la sauvegarde du 1er de chaque mois pendant 12 mois.
# Restauration : ./deploy/restauration.sh <archive>
set -eu

env_value() {  # lit IBIG_BACKUP_xxx dans .env sans exécuter le fichier
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | tail -n 1 | sed 's/^["'\'']//; s/["'\'']$//'
}

DEST="${IBIG_BACKUP_DIR:-$(env_value IBIG_BACKUP_DIR)}"; DEST="${DEST:-/var/backups/ibig-agent}"
KEY="${IBIG_BACKUP_KEY_FILE:-$(env_value IBIG_BACKUP_KEY_FILE)}"; KEY="${KEY:-/root/.ibig-sauvegarde.key}"
RCLONE_TARGET="${IBIG_BACKUP_RCLONE:-$(env_value IBIG_BACKUP_RCLONE)}"
SSH_TARGET="${IBIG_BACKUP_SSH:-$(env_value IBIG_BACKUP_SSH)}"
REMOTE_DAYS="${IBIG_BACKUP_REMOTE_DAYS:-$(env_value IBIG_BACKUP_REMOTE_DAYS)}"; REMOTE_DAYS="${REMOTE_DAYS:-90}"
STATE_DIR="state"                     # lu par le tableau de bord (page Santé des tâches)
umask 077

if [ "${1:-}" = "--init" ]; then
  if [ -s "$KEY" ]; then
    echo "La phrase de chiffrement existe déjà ($KEY) : elle n'est pas remplacée."
    exit 0
  fi
  mkdir -p "$(dirname "$KEY")"
  head -c 32 /dev/urandom | base64 | tr -d '\n=' > "$KEY"
  chmod 600 "$KEY"
  echo "Phrase de chiffrement créée : $KEY"
  echo
  echo "    $(cat "$KEY")"
  echo
  echo "RECOPIEZ-LA MAINTENANT hors du serveur (coffre de la direction, gestionnaire de mots"
  echo "de passe). Sans elle, aucune sauvegarde ne pourra être relue si le serveur est perdu."
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M)"
NAME="ibig-$STAMP.tar.gz"
WORK="$(mktemp -d)"
mkdir -p "$DEST" "$STATE_DIR"
chmod 755 "$STATE_DIR"          # lisible par le conteneur (utilisateur « agent »)
FINAL=""; ENCRYPTED=false; OFFSITE=""; OFFSITE_OK=false

status() {  # $1 = ok | echec, $2 = message
  size=0
  [ -n "$FINAL" ] && [ -f "$FINAL" ] && size=$(wc -c < "$FINAL" | tr -d ' ')
  msg=$(printf '%s' "$2" | tr '"\\' "''" | tr '\n' ' ')
  cat > "$STATE_DIR/sauvegarde.json" <<EOF
{"date": "$(date -Iseconds)", "etat": "$1", "fichier": "$(basename "${FINAL:-$NAME}")",
 "taille": $size, "chiffree": $ENCRYPTED, "hors_serveur": "$OFFSITE",
 "copie_ok": $OFFSITE_OK, "message": "$msg"}
EOF
  chmod 644 "$STATE_DIR/sauvegarde.json"
}

on_exit() {
  rc=$?
  rm -rf "$WORK"
  if [ "$rc" -ne 0 ]; then
    status echec "La sauvegarde du $(date '+%d/%m/%Y à %H:%M') a échoué (code $rc) : voir /var/log/ibig-sauvegarde.log"
    echo "$(date -Iseconds) ÉCHEC de la sauvegarde (code $rc)" >&2
  fi
}
trap on_exit EXIT

# 1. Base de données, relue avant d'aller plus loin (une sauvegarde jamais relue n'en est pas une)
docker compose exec -T db pg_dump -U ibig --format=custom ibig_agent > "$WORK/base.dump"
docker compose exec -T db pg_restore --list < "$WORK/base.dump" > /dev/null

# 2. Une seule archive : base + secrets et configuration non versionnés
for f in .env config/mailboxes.yaml; do
  [ -f "$f" ] && mkdir -p "$WORK/$(dirname "$f")" && cp "$f" "$WORK/$f"
done
tar -czf "$WORK/$NAME" -C "$WORK" base.dump $( [ -f "$WORK/.env" ] && echo .env ) \
  $( [ -d "$WORK/config" ] && echo config )

# 3. Chiffrement (AES-256) avec la phrase gardée hors de l'archive
if [ -s "$KEY" ]; then
  command -v gpg > /dev/null || { echo "gpg est absent : apt install gnupg" >&2; exit 3; }
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$KEY" \
      --symmetric --cipher-algo AES256 -o "$DEST/$NAME.gpg" "$WORK/$NAME"
  FINAL="$DEST/$NAME.gpg"; ENCRYPTED=true
else
  cp "$WORK/$NAME" "$DEST/$NAME"; FINAL="$DEST/$NAME"
  echo "ATTENTION : sauvegarde NON chiffrée (créer la phrase : ./deploy/sauvegarde.sh --init)" >&2
fi

# 4. Copie hors du serveur : un serveur perdu emporte ses propres sauvegardes
if [ -n "$RCLONE_TARGET" ]; then
  OFFSITE="$RCLONE_TARGET"
  if rclone copy "$FINAL" "$RCLONE_TARGET"; then
    OFFSITE_OK=true
    rclone delete "$RCLONE_TARGET" --min-age "${REMOTE_DAYS}d" --include 'ibig-*' || true
  fi
elif [ -n "$SSH_TARGET" ]; then
  OFFSITE="$SSH_TARGET"
  scp -q -o BatchMode=yes "$FINAL" "$SSH_TARGET/" && OFFSITE_OK=true
fi

# 5. Conservation locale (nouvelles archives et anciens fichiers base-/config-)
for pattern in 'ibig-*' 'base-*.dump' 'config-*.tar.gz'; do
  find "$DEST" -name "$pattern" -mtime +30 ! -name '*-??????01-*' -delete
done
find "$DEST" -name '*-????????-*' -mtime +365 -delete

if [ -n "$OFFSITE" ] && [ "$OFFSITE_OK" = false ]; then
  status echec "Archive locale faite, mais la copie vers $OFFSITE a échoué"
  echo "$(date -Iseconds) sauvegarde locale OK, copie hors serveur en ÉCHEC : $OFFSITE" >&2
  trap - EXIT; rm -rf "$WORK"; exit 4
fi
status ok "Sauvegarde du $(date '+%d/%m/%Y à %H:%M')"
echo "$(date -Iseconds) sauvegarde OK : $FINAL${OFFSITE:+ (copiée vers $OFFSITE)}"
