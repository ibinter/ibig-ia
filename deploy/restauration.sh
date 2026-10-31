#!/bin/sh
# Restauration d'une sauvegarde faite par deploy/sauvegarde.sh, depuis le dossier du projet :
#
#   ./deploy/restauration.sh /var/backups/ibig-agent/ibig-20261028-0215.tar.gz.gpg
#   ./deploy/restauration.sh <archive> --avec-config   # remet aussi .env et les boîtes mail
#
# Sur un nouveau serveur : installer l'agent (deploy/installer.sh), recopier la phrase de
# chiffrement dans /root/.ibig-sauvegarde.key (ou la saisir quand elle est demandée),
# puis lancer ce script avec --avec-config.
# Les anciennes sauvegardes (base-AAAAMMJJ-HHMM.dump) sont acceptées aussi.
set -eu

ARCHIVE="${1:-}"
WITH_CONFIG=false
[ "${2:-}" = "--avec-config" ] && WITH_CONFIG=true
KEY="${IBIG_BACKUP_KEY_FILE:-/root/.ibig-sauvegarde.key}"

if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "Usage : $0 <archive> [--avec-config]" >&2
  echo "Sauvegardes disponibles :" >&2
  ls -1t /var/backups/ibig-agent 2>/dev/null | head -10 >&2 || true
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
umask 077

case "$ARCHIVE" in
  *.gpg)
    if [ -s "$KEY" ]; then
      gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$KEY" \
          -d -o "$WORK/archive.tar.gz" "$ARCHIVE"
    else
      echo "Phrase de chiffrement introuvable ($KEY) : saisissez-la."
      gpg --quiet --pinentry-mode loopback -d -o "$WORK/archive.tar.gz" "$ARCHIVE"
    fi
    tar -xzf "$WORK/archive.tar.gz" -C "$WORK" ;;
  *.tar.gz) tar -xzf "$ARCHIVE" -C "$WORK" ;;
  *.dump)   cp "$ARCHIVE" "$WORK/base.dump" ;;
  *) echo "Format inconnu : $ARCHIVE" >&2; exit 2 ;;
esac

[ -s "$WORK/base.dump" ] || { echo "Pas de base dans l'archive." >&2; exit 2; }
docker compose exec -T db pg_restore --list < "$WORK/base.dump" > /dev/null

echo "Archive lisible : $ARCHIVE"
echo "La base actuelle de l'agent va être REMPLACÉE par celle de la sauvegarde."
printf 'Tapez RESTAURER pour continuer : '
read -r answer
[ "$answer" = "RESTAURER" ] || { echo "Abandon : rien n'a été modifié."; exit 1; }

if [ "$WITH_CONFIG" = true ]; then
  stamp="$(date +%Y%m%d-%H%M)"
  for f in .env config/mailboxes.yaml; do
    if [ -f "$WORK/$f" ]; then
      [ -f "$f" ] && cp "$f" "$f.avant-restauration-$stamp"
      mkdir -p "$(dirname "$f")" && cp "$WORK/$f" "$f" && chmod 600 "$f"
      echo "Remis en place : $f"
    fi
  done
fi

docker compose stop agent
docker compose exec -T db pg_restore -U ibig -d ibig_agent --clean --if-exists --no-owner \
  < "$WORK/base.dump"
docker compose up -d agent
echo "$(date -Iseconds) restauration terminée depuis $ARCHIVE"
echo "Vérifier : docker compose exec agent ibig-agent diagnostic"
