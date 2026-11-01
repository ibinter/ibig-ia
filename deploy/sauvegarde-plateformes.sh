#!/bin/sh
# Sauvegarde quotidienne de TOUTES les bases des plateformes hébergées sur ce VPS (Scolaby,
# Santarex, Secretis, Lokativo, IMMO TRUST, Anouanzê, Gestmoney…), en une archive chiffrée.
# Complète deploy/sauvegarde.sh, qui ne sauvegarde que l'agent IA.
#
# À lancer en root par cron sur le VPS :
#   45 2 * * * /opt/ibig-ia/deploy/sauvegarde-plateformes.sh >> /var/log/ibig-sauvegarde-plateformes.log 2>&1
#
# Ce qui est sauvegardé (découvert à chaque passage, donc une nouvelle base est incluse seule) :
#   - toutes les bases PostgreSQL du serveur ;
#   - tous les conteneurs PostgreSQL (hors ceux de l'agent IA, déjà couverts) ;
#   - les volumes Docker d'applications ARRÊTÉES (aucun conteneur ne les utilise) ;
#   - MySQL si le fichier d'accès /root/.ibig-mysql.cnf existe (lecture seule recommandée).
# Chaque dump est contrôlé avant d'être archivé. Chiffrement : la phrase de l'agent
# (/root/.ibig-sauvegarde.key). Conservation locale : 14 jours.
# L'état est écrit dans state/sauvegarde-plateformes.json : l'agent alerte si elle manque.
# Restauration d'une base : gpg -d archive.gpg | tar xz, puis pg_restore ou psql.
set -eu

DEST="${IBIG_PF_BACKUP_DIR:-/var/backups/ibig-plateformes}"
KEY="${IBIG_BACKUP_KEY_FILE:-/root/.ibig-sauvegarde.key}"
STATE_DIR="${IBIG_STATE_DIR:-/opt/ibig-ia/state}"
KEEP_DAYS="${IBIG_PF_KEEP_DAYS:-14}"
MYSQL_CNF="${IBIG_MYSQL_CNF:-/root/.ibig-mysql.cnf}"
umask 077

STAMP="$(date +%Y%m%d-%H%M)"
NAME="ibig-plateformes-$STAMP.tar.gz.gpg"
WORK="$(mktemp -d)"
OKS=0
FAILS=""
NOTES=""
FINAL=""

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

ok() { OKS=$((OKS + 1)); echo "ok      $1"; }
fail() { FAILS="$FAILS $1"; echo "ECHEC   $1 : $2"; }
note() { NOTES="$NOTES $1;"; echo "note    $1"; }

status() {  # $1 = ok | partiel | echec
  size=0
  [ -n "$FINAL" ] && [ -f "$FINAL" ] && size=$(wc -c < "$FINAL" | tr -d ' ')
  mkdir -p "$STATE_DIR"
  msg=$(printf '%s %s' "$FAILS" "$NOTES" | tr '"\\' "''" | tr '\n' ' ')
  cat > "$STATE_DIR/sauvegarde-plateformes.json" <<EOF
{"date": "$(date -Iseconds)", "etat": "$1", "fichier": "$(basename "${FINAL:-$NAME}")",
 "taille": $size, "bases": $OKS, "message": "$msg"}
EOF
  chmod 644 "$STATE_DIR/sauvegarde-plateformes.json"
}

[ -s "$KEY" ] || { FAILS=" phrase-de-chiffrement-absente"; status echec; echo "Phrase absente : $KEY"; exit 1; }
mkdir -p "$DEST"

# 1. PostgreSQL installé sur le serveur
if command -v psql >/dev/null 2>&1 && id postgres >/dev/null 2>&1; then
  for db in $(sudo -u postgres psql -At -c "select datname from pg_database where not datistemplate and datname <> 'postgres'" 2>/dev/null); do
    out="$WORK/pg-$db.dump"
    if sudo -u postgres pg_dump -Fc "$db" > "$out" 2>"$WORK/err" && pg_restore -l "$out" >/dev/null 2>&1; then
      ok "pg:$db"
    else
      fail "pg:$db" "$(head -c 120 "$WORK/err" | tr '\n' ' ')"; rm -f "$out"
    fi
  done
fi

# 2. Conteneurs PostgreSQL (hors agent IA)
for c in $(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | awk '$2 ~ /postgres|pgvector/ && $1 !~ /^ibig-ia/ {print $1}'); do
  out="$WORK/docker-$c.sql.gz"
  if docker exec "$c" sh -c 'pg_dumpall -U "$POSTGRES_USER"' 2>"$WORK/err" | gzip > "$out" \
     && [ "$(gzip -dc "$out" | head -c 2000 | wc -c)" -gt 100 ]; then
    ok "docker:$c"
  else
    fail "docker:$c" "$(head -c 120 "$WORK/err" | tr '\n' ' ')"; rm -f "$out"
  fi
done

# 3. Volumes de données d'applications arrêtées (aucun conteneur actif ne les utilise)
for v in $(docker volume ls -q 2>/dev/null | grep -E '_(postgres|minio|db)_data$|_pgdata$' | grep -v '^ibig-ia_'); do
  if [ -n "$(docker ps -q --filter "volume=$v" 2>/dev/null)" ]; then
    continue   # volume utilisé : couvert par le dump du conteneur
  fi
  src="/var/lib/docker/volumes/$v/_data"
  out="$WORK/volume-$v.tar.gz"
  if [ -d "$src" ] && tar czf "$out" -C "$src" . 2>"$WORK/err"; then
    ok "volume:$v"
  else
    fail "volume:$v" "$(head -c 120 "$WORK/err" | tr '\n' ' ')"; rm -f "$out"
  fi
done

# 4. MySQL (uniquement si un accès en lecture a été préparé)
if command -v mysqldump >/dev/null 2>&1 && [ -f "$MYSQL_CNF" ]; then
  out="$WORK/mysql-all.sql.gz"
  if mysqldump --defaults-extra-file="$MYSQL_CNF" --all-databases --single-transaction --quick \
       --no-tablespaces --routines --events --triggers 2>"$WORK/err" | gzip > "$out" \
     && [ "$(gzip -dc "$out" | grep -c 'CREATE TABLE')" -gt 0 ]; then
    ok "mysql:toutes"
  else
    fail "mysql" "$(head -c 120 "$WORK/err" | tr '\n' ' ')"; rm -f "$out"
  fi
else
  note "mysql-non-sauvegarde(pas-de-$MYSQL_CNF)"
fi

if [ "$OKS" -eq 0 ]; then
  status echec; echo "Aucune base sauvegardée."; exit 1
fi

tar czf "$WORK/all.tar.gz" -C "$WORK" $(cd "$WORK" && ls | grep -E '^(pg-|docker-|volume-|mysql-)')
FINAL="$DEST/$NAME"
gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$KEY" \
    --symmetric --cipher-algo AES256 -o "$FINAL" "$WORK/all.tar.gz"
# Relecture : l'archive chiffrée doit se déchiffrer et se lister, sinon elle ne compte pas.
if ! gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$KEY" -d "$FINAL" 2>/dev/null | tar tz >/dev/null 2>&1; then
  rm -f "$FINAL"; FAILS="$FAILS archive-illisible"; status echec; echo "Archive illisible, supprimée."; exit 1
fi

find "$DEST" -name 'ibig-plateformes-*.gpg' -mtime +"$KEEP_DAYS" -delete
if [ -n "$FAILS" ]; then status partiel; else status ok; fi
echo "Sauvegarde terminée : $FINAL ($OKS élément(s))"
