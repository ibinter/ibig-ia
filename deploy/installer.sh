#!/usr/bin/env bash
# Installation de l'agent IA IBIG sur un serveur Ubuntu / Debian neuf, en une commande :
#
#   curl -fsSL https://raw.githubusercontent.com/ibinter/ibig-ia/claude/construire-github-9ct4l2/deploy/installer.sh -o installer.sh
#   sudo bash installer.sh
#
# Le script peut être relancé sans risque : il ne remplace jamais un .env existant.
# Il installe Docker, le pare-feu et les mises à jour de sécurité, télécharge l'agent,
# génère les secrets, démarre l'agent en HTTPS, crée le premier compte et lance le
# diagnostic. Il ne touche pas à la configuration SSH (pour ne jamais vous bloquer dehors).
set -euo pipefail
# Tout le script est entre accolades : bash le lit en entier avant de l'exécuter, ce qui
# le protège de la mise à jour du dépôt (étape 4) quand il est lancé depuis /opt/ibig-ia.
{

REPO="${IBIG_REPO:-https://github.com/ibinter/ibig-ia.git}"
BRANCH="${IBIG_BRANCH:-claude/construire-github-9ct4l2}"
DIR="${IBIG_DIR:-/opt/ibig-ia}"

bleu() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
ok() { printf '\033[32m✔ %s\033[0m\n' "$*"; }
stop() { printf '\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }
question() { local r; read -r -p "$1 " r </dev/tty; printf '%s' "$r"; }
set_env() {  # remplace ou ajoute VAR=valeur dans .env sans afficher la valeur
  if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
# Programmes (hors Docker) qui écoutent sur un port TCP donné
ecoute() { ss -Hltnp "sport = :$1" 2>/dev/null | grep -v docker-proxy \
  | grep -oE 'users:\(\("[^"]+' | cut -d'"' -f2 | sort -u | xargs || true; }

[ "$(id -u)" -eq 0 ] || stop "À lancer en administrateur : sudo bash installer.sh"
. /etc/os-release 2>/dev/null || stop "Système non reconnu (Ubuntu ou Debian attendu)"
case "${ID:-}" in ubuntu|debian) ;; *) stop "Système ${ID:-inconnu} : Ubuntu ou Debian attendu" ;; esac

bleu "1/7 Vérification du serveur"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
DISK_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "Système : ${PRETTY_NAME:-?} · mémoire : ${MEM_MB} Mo · disque libre : ${DISK_GB} Go"
[ "$MEM_MB" -ge 3500 ] || echo "ATTENTION : moins de 4 Go de mémoire, l'agent risque d'être lent."
[ "$DISK_GB" -ge 10 ] || stop "Moins de 10 Go libres sur le disque."
IP=$(curl -fsS4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
ok "Adresse publique : $IP"

bleu "2/7 Mises à jour, pare-feu, mises à jour de sécurité automatiques"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git openssl ufw unattended-upgrades >/dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
ufw allow 22/tcp >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
if ufw status | grep -q "Status: active"; then
  ok "Pare-feu déjà actif : 22, 80 et 443 autorisés, les autres règles sont conservées"
else
  # Ne jamais couper un service existant (autre port SSH, base distante, panneau...)
  AUTRES=$(ss -Hltn | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|\[::ffff:127\.)' \
    | sed -E 's/.*:([0-9]+)$/\1/' | grep -vxE '22|80|443' | sort -un | xargs || true)
  if [ -n "$AUTRES" ]; then
    echo "ATTENTION : pare-feu NON activé, d'autres services écoutent sur : $AUTRES"
    echo "  Autorisez ceux qui doivent rester joignables (ufw allow PORT/tcp) puis : ufw enable"
  else
    ufw --force enable >/dev/null
    ok "Pare-feu actif : seuls SSH (22), HTTP (80) et HTTPS (443) sont ouverts"
  fi
fi

bleu "3/7 Docker"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
ok "$(docker --version)"

bleu "4/7 Téléchargement de l'agent dans $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" \
    && git -C "$DIR" pull -q --ff-only origin "$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
ok "Version : $(git log -1 --format='%h %s')"

bleu "5/7 Réglages (.env)"
if [ -f .env ]; then
  ok ".env existant conservé (supprimez-le pour tout régénérer)"
  DOMAIN=$(grep -E '^IBIG_DOMAIN=' .env | cut -d= -f2-)
else
  echo "Nom de domaine du tableau de bord (ex. agent.ibigsoft.com), dont l'enregistrement DNS"
  echo "de type A pointe vers $IP. Laissez vide pour une adresse temporaire gratuite."
  DOMAIN=$(question "Domaine :")
  if [ -z "$DOMAIN" ]; then
    DOMAIN="$(echo "$IP" | tr '.' '-').sslip.io"
    echo "Adresse temporaire : $DOMAIN (à remplacer plus tard par votre domaine)"
  fi
  echo "Clé API Anthropic (console.anthropic.com). Laissez vide pour l'ajouter plus tard :"
  read -r -s -p "Clé : " API_KEY </dev/tty; echo
  gen() { openssl rand -hex 32; }
  cp .env.example .env
  chmod 600 .env
  set_env IBIG_DOMAIN "$DOMAIN"
  set_env IBIG_DASHBOARD_URL "https://$DOMAIN"
  set_env POSTGRES_PASSWORD "$(gen)"
  set_env IBIG_SECRET_KEY "$(gen)"
  set_env IBIG_SARA_API_KEY "$(gen)"
  set_env IBIG_WHATSAPP_VERIFY_TOKEN "$(openssl rand -hex 16)"
  set_env ANTHROPIC_API_KEY "${API_KEY:-}"
  ok ".env créé (secrets générés, lisible par root seulement)"
fi
[ -f config/mailboxes.yaml ] || printf 'mailboxes: []\n' > config/mailboxes.yaml
mkdir -p exports && chmod 777 exports

bleu "6/7 Démarrage (base de données, agent, HTTPS)"
# HTTPS : Caddy intégré si les ports 80/443 sont libres ; si un nginx existant les occupe
# (autres sites sur le serveur), c'est lui qui sert l'agent et on n'y touche pas sinon.
WEB=$( { ecoute 80; ecoute 443; } | xargs -n1 | sort -u | xargs)
case "$WEB" in
  "") MODE=caddy; set_env COMPOSE_PROFILES caddy ;;
  nginx) MODE=nginx; set_env COMPOSE_PROFILES ""
         docker compose --profile caddy rm -sf caddy >/dev/null 2>&1 || true
         ok "nginx sert déjà d'autres sites : l'agent passera par lui (sites existants intacts)" ;;
  *) stop "Les ports 80/443 sont pris par : $WEB. Configurez ce programme pour qu'il relaie
  https://$DOMAIN vers 127.0.0.1:${IBIG_LOCAL_PORT:-18000}, ou libérez les ports." ;;
esac
PORT=$(grep -E '^IBIG_LOCAL_PORT=' .env | cut -d= -f2-)
PORT=${PORT:-18000}
while [ -n "$(ecoute "$PORT")" ]; do PORT=$((PORT + 1)); done
set_env IBIG_LOCAL_PORT "$PORT"
docker compose up -d --build --remove-orphans
echo -n "Attente du démarrage de l'agent"
for _ in $(seq 1 60); do
  if docker compose exec -T agent python -c \
      "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null; then
    echo; ok "Agent démarré"; break
  fi
  echo -n "."; sleep 3
done
docker compose exec -T agent python -c \
  "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null \
  || { docker compose logs --tail 40 agent; stop "L'agent ne démarre pas (voir ci-dessus)"; }

if [ "$MODE" = nginx ]; then
  SITE=/etc/nginx/sites-available/ibig-agent
  [ -d /etc/nginx/sites-enabled ] || SITE=/etc/nginx/conf.d/ibig-agent.conf
  if [ -f "$SITE" ]; then
    sed -i -E "s|proxy_pass http://127\.0\.0\.1:[0-9]+;|proxy_pass http://127.0.0.1:$PORT;|" "$SITE"
    ok "Site nginx de l'agent déjà présent : $SITE"
  else
    cat > "$SITE" <<NGINX
# Agent IA IBIG (créé par deploy/installer.sh) : relaie $DOMAIN vers l'agent (Docker)
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    client_max_body_size 10m;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "same-origin" always;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX
    [ -d /etc/nginx/sites-enabled ] && ln -sf "$SITE" /etc/nginx/sites-enabled/ibig-agent
    ok "Site nginx créé : $SITE"
  fi
  if ! nginx -t >/dev/null 2>&1; then
    nginx -t || true
    rm -f "$SITE" /etc/nginx/sites-enabled/ibig-agent
    stop "Configuration nginx refusée : site de l'agent retiré, les autres sites sont intacts"
  fi
  systemctl reload nginx
  if grep -q ssl_certificate "$SITE"; then
    ok "Certificat HTTPS déjà en place"
  else
    if ! command -v certbot >/dev/null || ! certbot plugins 2>/dev/null | grep -q nginx; then
      apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
    fi
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
        --register-unsafely-without-email \
      && ok "Certificat HTTPS obtenu pour $DOMAIN" \
      || echo "ATTENTION : certificat HTTPS non obtenu (DNS de $DOMAIN vers $IP ?). Plus tard :
  certbot --nginx -d $DOMAIN --redirect"
  fi
fi

bleu "7/7 Premier compte et diagnostic"
if docker compose exec -T agent ibig-agent utilisateur lister 2>/dev/null | grep -q 'admin'; then
  ok "Un compte administrateur existe déjà"
else
  EMAIL=$(question "Adresse mail du compte de direction :")
  NOM=$(question "Nom affiché :")
  echo "Choisissez son mot de passe (10 caractères minimum) :"
  docker compose exec agent ibig-agent utilisateur ajouter --email "$EMAIL" --nom "$NOM" \
    --role admin
fi
docker compose exec -T agent ibig-agent diagnostic || true

cat <<FIN

$(printf '\033[1;32m')Installation terminée.$(printf '\033[0m')
  Tableau de bord : https://$DOMAIN   (le certificat HTTPS peut prendre 1 à 2 minutes)
  Dossier         : $DIR
  Réglages        : $DIR/.env   (secrets : ne jamais le copier ailleurs que dans une sauvegarde)

Les « ÉCHEC » et « ATTENTION » du diagnostic ci-dessus sont normaux à ce stade : boîtes
mail, valideurs et fiches pôles restent à configurer (docs/mise-en-service.md, section 3).
Sauvegardes quotidiennes : voir docs/mise-en-service.md, section 6.
FIN
exit 0
}
