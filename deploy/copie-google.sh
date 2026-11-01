#!/usr/bin/env bash
# Copie CHIFFRÉE de toutes les sauvegardes du serveur vers Google Drive (hors du VPS).
#
# À lancer chaque nuit par cron, après les sauvegardes (qui finissent vers 3 h) :
#   30 3 * * * root /opt/ibig-ia/deploy/copie-google.sh >> /var/log/ibig-copie-google.log 2>&1
#
# Principe : pour chaque jeu de sauvegardes, les fichiers de moins de 36 h sont chiffrés (gpg AES-256, même
# phrase que la sauvegarde commune : /root/.ibig-sauvegarde.key, jamais envoyée) puis copiés avec rclone dans
# gdrive:ibig-sauvegardes/<jeu>/. Google ne voit que des fichiers illisibles. Les copies de plus de 14 jours
# sont supprimées côté Drive. Santarex a sa propre copie (santarex-offsite.sh) : elle n'est pas refaite ici.
# L'état est écrit dans state/copie-google.json : l'agent alerte si la copie manque ou échoue.
#
# Restauration : rclone copyto gdrive:ibig-sauvegardes/<jeu>/<fichier>.gpg ./f.gpg
#                gpg --batch --pinentry-mode loopback --passphrase-file CLE -d f.gpg > fichier
set -u

REMOTE="${IBIG_GDRIVE_REMOTE:-gdrive:ibig-sauvegardes}"
KEY="${IBIG_BACKUP_KEY_FILE:-/root/.ibig-sauvegarde.key}"
STATE_DIR="${IBIG_STATE_DIR:-/opt/ibig-ia/state}"
MAX_MIN="${IBIG_COPIE_MINUTES:-2160}"      # 36 h
KEEP="${IBIG_COPIE_JOURS:-14d}"
WORK="$(mktemp -d /var/tmp/copie-google.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# nom | dossier | motif
SETS="agent|/var/backups/ibig-agent|*.gpg
plateformes|/var/backups/ibig-plateformes|*.gpg
lokativo|/var/backups/lokativo|*.sql.gz
scolaby|/opt/backups|scolaby-*.sql.gz
fleet360|/var/www/ibig/storage/app/backups|*.sql.gz
anouanze|/var/backups/anouanze|*"

REPORT=""
add() { REPORT="${REPORT}${REPORT:+,}{\"jeu\":\"$1\",\"envoyes\":$2,\"octets\":$3,\"erreur\":\"$4\"}"; }
status() {
  mkdir -p "$STATE_DIR"
  FREE="$(rclone about "${REMOTE%%:*}:" --json 2>/dev/null | sed -n 's/.*"free":\([0-9]*\).*/\1/p')"
  cat > "$STATE_DIR/copie-google.json" <<EOF
{"date": "$(date -Iseconds)", "etat": "$1", "libre_octets": ${FREE:-null}, "jeux": [$REPORT]}
EOF
  chmod 644 "$STATE_DIR/copie-google.json"
}

command -v rclone >/dev/null 2>&1 || { add "-" 0 0 "rclone-absent"; status echec; echo "rclone absent"; exit 1; }
[ -s "$KEY" ] || { add "-" 0 0 "phrase-absente"; status echec; echo "Phrase de chiffrement absente"; exit 1; }
rclone lsd "${REMOTE%%:*}:" >/dev/null 2>&1 || { add "-" 0 0 "google-injoignable"; status echec; echo "Google Drive injoignable"; exit 1; }

echo "$SETS" | while IFS='|' read -r name dir pattern; do
  [ -d "$dir" ] || continue
  out="$WORK/$name"; mkdir -p "$out"; n=0; err=""
  for f in $(find "$dir" -maxdepth 1 -type f -name "$pattern" -mmin -"$MAX_MIN" 2>/dev/null); do
    b="$(basename "$f")"
    case "$b" in
      *.gpg) cp "$f" "$out/$b" ;;
      *) gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$KEY" \
             --symmetric --cipher-algo AES256 -o "$out/$b.gpg" "$f" || { err="chiffrement-$b"; continue; } ;;
    esac
    n=$((n + 1))
  done
  bytes="$(du -sb "$out" | cut -f1)"
  if [ "$n" -gt 0 ]; then
    rclone copy "$out" "$REMOTE/$name" --transfers 2 --retries 3 2>"$WORK/err" \
      || err="envoi-$(head -c 80 "$WORK/err" | tr '\n"' '  ')"
  fi
  rclone delete "$REMOTE/$name" --min-age "$KEEP" >/dev/null 2>&1
  echo "$name|$n|$bytes|$err" >> "$WORK/result"
done

FAILED=0; SENT=0
if [ -s "$WORK/result" ]; then
  while IFS='|' read -r name n bytes err; do
    add "$name" "$n" "$bytes" "$err"
    [ -n "$err" ] && FAILED=1
    SENT=$((SENT + n))
  done < "$WORK/result"
fi

if [ "$FAILED" -eq 1 ]; then status partiel; echo "Copie Google terminée avec des erreurs"; exit 1; fi
status ok
echo "Copie Google terminée : $SENT fichier(s) chiffré(s) envoyés"
