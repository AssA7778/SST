#!/usr/bin/env bash
set -Eeuo pipefail

REPO_RAW="${SST_REPO_RAW:-https://raw.githubusercontent.com/AssA7778/SST/main}"
INSTALL_DIR="${SST_INSTALL_DIR:-/opt/sst}"
SERVICE="sst-bot"
VERSION="2.1.0"

R=$'\033[1;31m'; G=$'\033[1;32m'; Y=$'\033[1;33m'
C=$'\033[1;36m'; W=$'\033[1;37m'; D=$'\033[2m'; NC=$'\033[0m'

info()  { printf '%s  %s%s\n' "$C" "$1" "$NC"; }
ok()    { printf '%s  ✓ %s%s\n' "$G" "$1" "$NC"; }
warn()  { printf '%s  ! %s%s\n' "$Y" "$1" "$NC"; }
die()   { printf '%s  ✗ %s%s\n' "$R" "$1" "$NC" >&2; exit 1; }
rule()  { printf '%s──────────────────────────────────────────────%s\n' "$W" "$NC"; }

trap 'die "installation failed at line $LINENO"' ERR

banner() {
  clear || true
  printf '%s' "$C"
  cat <<'ART'
══════════════════════════════════════════════

  ███████╗███████╗████████╗
  ██╔════╝██╔════╝╚══██╔══╝
  ███████╗███████╗   ██║
  ╚════██║╚════██║   ██║
  ███████║███████║   ██║
  ╚══════╝╚══════╝   ╚═╝

  Secure Shell over Telegram  v2.1.0
══════════════════════════════════════════════
ART
  printf '%s\n' "$NC"
}

if [[ "${1:-}" == "uninstall" ]]; then
  [[ $EUID -eq 0 ]] || die "run as root"
  info "Removing SST..."
  if [[ -f "$INSTALL_DIR/config.json" ]]; then
    TOKEN=$(python3 -c "import json;print(json.load(open('$INSTALL_DIR/config.json'))['bot_token'])" 2>/dev/null || true)
    [[ -n "${TOKEN:-}" ]] && curl -s --max-time 10 \
      "https://api.telegram.org/bot${TOKEN}/deleteWebhook" >/dev/null 2>&1 || true
  fi
  systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/${SERVICE}.service" /etc/cron.d/sst-cert-renew /usr/local/bin/sst
  systemctl daemon-reload || true
  rm -rf "$INSTALL_DIR"
  ok "SST removed"
  exit 0
fi

banner
rule
printf '%s  Installing SST v%s%s\n' "$G" "$VERSION" "$NC"
rule
echo

[[ $EUID -eq 0 ]] || die "Please run as root (sudo -i)"

info "[1/7] Installing dependencies..."
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 curl openssl ca-certificates >/dev/null
  NEED_CERTBOT_PKG="certbot"
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 curl openssl ca-certificates >/dev/null
  NEED_CERTBOT_PKG="certbot"
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 curl openssl ca-certificates >/dev/null
  NEED_CERTBOT_PKG="certbot"
else
  die "Unsupported package manager (need apt, dnf or yum)"
fi

PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)' \
  || die "Python 3.8+ required (found $PYV)"
ok "python $PYV, curl, openssl"

echo
rule
printf '%s  Telegram bot token%s\n' "$G" "$NC"
rule
printf '%s  Open @BotFather → /newbot → copy the token%s\n\n' "$D" "$NC"
while :; do
  read -rp "  Bot token: " BOT_TOKEN
  BOT_TOKEN="${BOT_TOKEN//[[:space:]]/}"
  [[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ ]] && break
  warn "That does not look like a bot token (123456:ABC-DEF...)"
done

echo
rule
printf '%s  Your Telegram user ID%s\n' "$G" "$NC"
rule
printf '%s  Open @userinfobot → copy the numeric ID%s\n\n' "$D" "$NC"
while :; do
  read -rp "  User ID: " USER_ID
  USER_ID="${USER_ID//[[:space:]]/}"
  [[ "$USER_ID" =~ ^[0-9]+$ ]] && (( USER_ID > 0 )) && break
  warn "User ID must be a positive number"
done

echo
rule
printf '%s  Connection mode%s\n' "$G" "$NC"
rule
printf '%s  1) Webhook  instant, needs a domain + open port\n' "$W"
printf '              includes the terminal Mini App\n'
printf '  2) Polling  no domain, no open port, ~1s slower\n'
printf '              chat only, NO Mini App%s\n\n' "$NC"
printf '%s  The Mini App (full terminal + file browser) needs a\n' "$D"
printf '  domain with a real certificate, so it only works in\n'
printf '  webhook mode. Polling is the safer choice otherwise.%s\n\n' "$NC"
read -rp "  Choose [1/2] (default 2): " MODE
MODE="${MODE:-2}"

DOMAIN=""; PORT=88; UPLOAD_CERT="false"
if [[ "$MODE" == "1" ]]; then
  SST_MODE="webhook"
  SERVER_IP=$(curl -s --max-time 8 -4 https://api.ipify.org 2>/dev/null || echo "YOUR_SERVER_IP")
  echo
  printf '%s  DNS setup, create an A record:%s\n' "$C" "$NC"
  printf '%s    Type: A     Name: bot     Value: %s     TTL: auto\n' "$W" "$SERVER_IP"
  printf '    If you use Cloudflare, set it to "DNS only" (grey cloud).%s\n\n' "$NC"
  while :; do
    read -rp "  Domain (e.g. bot.example.com): " DOMAIN
    DOMAIN="${DOMAIN//[[:space:]]/}"; DOMAIN="${DOMAIN#http*://}"; DOMAIN="${DOMAIN%%/*}"
    [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] && break
    warn "Invalid domain"
  done
  echo
  printf '%s  Telegram only accepts webhooks on ports 443, 80, 88, 8443.%s\n\n' "$D" "$NC"
  read -rp "  Port (default 88): " PORT
  PORT="${PORT:-88}"
  case "$PORT" in 443|80|88|8443) ;; *) die "Port must be 443, 80, 88 or 8443" ;; esac
else
  SST_MODE="polling"
fi

echo
info "[2/7] Creating $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/certs"
chmod 700 "$INSTALL_DIR" "$INSTALL_DIR/certs"
ok "$INSTALL_DIR (mode 0700)"

info "[3/7] Downloading program files..."
fetch() {
  local name="$1"
  if [[ -f "$(dirname "${BASH_SOURCE[0]}")/$name" && "${SST_LOCAL:-0}" == "1" ]]; then
    cp "$(dirname "${BASH_SOURCE[0]}")/$name" "$INSTALL_DIR/$name"
  else
    curl -fsSL --max-time 60 "$REPO_RAW/$name" -o "$INSTALL_DIR/$name.tmp" \
      || die "download failed: $name"
    mv "$INSTALL_DIR/$name.tmp" "$INSTALL_DIR/$name"
  fi
  [[ -s "$INSTALL_DIR/$name" ]] || die "downloaded $name is empty"
}
for f in bot.py terminal.py miniapp.py webapp.py; do fetch "$f"; done
python3 -m py_compile "$INSTALL_DIR"/*.py \
  || die "downloaded files do not compile, aborting"
chmod 600 "$INSTALL_DIR"/*.py
ok "program files verified"

info "[4/7] Generating secrets..."
WEBHOOK_SECRET=$(openssl rand -hex 32)
WEBHOOK_PATH=$(openssl rand -hex 16)
WEBAPP_KEY=$(openssl rand -hex 24)
ok "webhook secret, secret path and mini app key generated"

info "[5/7] Writing config..."
umask 077
python3 - "$INSTALL_DIR/config.json" "$BOT_TOKEN" "$USER_ID" "$SST_MODE" \
         "$DOMAIN" "$PORT" "$WEBHOOK_SECRET" "$WEBHOOK_PATH" "$(whoami)" \
         "$WEBAPP_KEY" <<'PYEOF'
import json, socket, sys

out, token, uid, mode, domain, port, secret, path, user, appkey = sys.argv[1:11]
cfg = {
    "bot_token": token,
    "user_id": int(uid),
    "server_name": socket.gethostname(),
    "ssh_user": user,
    "ssh_host": domain or socket.gethostname(),
    "mode": mode,
    "port": int(port),
    "webhook_domain": domain,
    "webhook_secret": secret,
    "webhook_path": path,
    "tls": True,
    "cert": "certs/cert.pem",
    "key": "certs/key.pem",
    "upload_certificate": False,
    "ip_filter": True,
    "bind": "0.0.0.0",
    "cols": 80,
    "rows": 55,
    "miniapp": True,
    "webapp_key": appkey,
    "session_timeout": 1800,
    "log_level": "INFO",
}
with open(out, "w") as fh:
    json.dump(cfg, fh, indent=2)
PYEOF
chmod 600 "$INSTALL_DIR/config.json"
ok "config.json written (mode 0600, contains your token)"

info "[6/7] TLS certificate..."
if [[ "$SST_MODE" == "webhook" ]]; then
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true

  if ! command -v certbot >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get install -y -qq "$NEED_CERTBOT_PKG" >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y "$NEED_CERTBOT_PKG" >/dev/null 2>&1 || true
    elif command -v yum >/dev/null 2>&1; then
      yum install -y "$NEED_CERTBOT_PKG" >/dev/null 2>&1 || true
    fi
  fi

  GOT_LE="no"
  if command -v certbot >/dev/null 2>&1; then
    printf '%s  Requesting a Let'"'"'s Encrypt certificate (port 80 used briefly)...%s\n' "$Y" "$NC"
    if certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos \
         --register-unsafely-without-email --no-eff-email >/dev/null 2>&1 \
       && [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
      GOT_LE="yes"
    fi
  fi

  if [[ "$GOT_LE" == "yes" ]]; then
    install -m 600 "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$INSTALL_DIR/certs/cert.pem"
    install -m 600 "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   "$INSTALL_DIR/certs/key.pem"
    ok "Let's Encrypt certificate installed"
    cat > /etc/cron.d/sst-cert-renew <<EOF
# SST certificate renewal
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 3 * * * root certbot renew --quiet --deploy-hook 'install -m 600 /etc/letsencrypt/live/$DOMAIN/fullchain.pem $INSTALL_DIR/certs/cert.pem && install -m 600 /etc/letsencrypt/live/$DOMAIN/privkey.pem $INSTALL_DIR/certs/key.pem && systemctl restart $SERVICE'
EOF
    chmod 644 /etc/cron.d/sst-cert-renew
    ok "auto-renewal installed (/etc/cron.d/sst-cert-renew)"
  else
    warn "Let's Encrypt failed, falling back to a self-signed certificate"
    openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
      -keyout "$INSTALL_DIR/certs/key.pem" -out "$INSTALL_DIR/certs/cert.pem" \
      -subj "/CN=$DOMAIN" -addext "subjectAltName=DNS:$DOMAIN" >/dev/null 2>&1 \
      || die "openssl failed"
    chmod 600 "$INSTALL_DIR/certs/"*.pem
    UPLOAD_CERT="true"
    python3 - "$INSTALL_DIR/config.json" <<'PYEOF'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["upload_certificate"] = True
json.dump(cfg, open(p, "w"), indent=2)
PYEOF
    ok "self-signed certificate created (it will be uploaded to Telegram)"
  fi

  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
    ok "opened port $PORT in ufw"
  fi
else
  ok "skipped (polling mode needs no certificate and no open port)"
fi

info "[7/7] Installing service..."
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=SST - Secure Shell over Telegram
Documentation=https://github.com/AssA7778/SST
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 -u $INSTALL_DIR/bot.py
Restart=always
RestartSec=5
TimeoutStopSec=15
KillMode=mixed
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/sst <<EOF
#!/usr/bin/env bash
set -euo pipefail
DIR="$INSTALL_DIR"; SVC="$SERVICE"
case "\${1:-help}" in
  status)    systemctl status "\$SVC" --no-pager ;;
  start)     systemctl start "\$SVC"   && echo "started" ;;
  stop)      systemctl stop "\$SVC"    && echo "stopped" ;;
  restart)   systemctl restart "\$SVC" && echo "restarted" ;;
  logs)      journalctl -u "\$SVC" -f --no-pager ;;
  config)    \${EDITOR:-nano} "\$DIR/config.json" && systemctl restart "\$SVC" ;;
  webhook)
    TOKEN=\$(python3 -c "import json;print(json.load(open('\$DIR/config.json'))['bot_token'])")
    curl -s "https://api.telegram.org/bot\${TOKEN}/getWebhookInfo" | python3 -m json.tool ;;
  update)
    for f in bot.py terminal.py miniapp.py webapp.py; do
      curl -fsSL "$REPO_RAW/\$f" -o "\$DIR/\$f.new" || { echo "download failed: \$f"; exit 1; }
    done
    python3 -m py_compile "\$DIR"/*.new || { echo "new files do not compile"; exit 1; }
    for f in bot.py terminal.py miniapp.py webapp.py; do mv "\$DIR/\$f.new" "\$DIR/\$f"; done
    chmod 600 "\$DIR"/*.py
    systemctl restart "\$SVC"; echo "updated" ;;
  uninstall) bash <(curl -Ls "$REPO_RAW/setup.sh") uninstall ;;
  *) cat <<'USAGE'
sst - manage the SST Telegram terminal

  sst status      service status
  sst logs        follow the log
  sst restart     restart the bot
  sst start|stop  start / stop the bot
  sst config      edit config.json and restart
  sst webhook     show what Telegram thinks of the webhook
  sst update      pull the latest bot.py and restart
  sst uninstall   remove SST completely
USAGE
  ;;
esac
EOF
chmod 755 /usr/local/bin/sst

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"
sleep 3

echo
if systemctl is-active --quiet "$SERVICE"; then
  printf '%s' "$G"; rule; printf '  ✓ Installation complete%s\n' "$NC"; printf '%s' "$G"; rule; printf '%s' "$NC"
  echo
  printf '%s  Mode      : %s%s\n' "$W" "$SST_MODE" "$NC"
  if [[ "$SST_MODE" == "webhook" ]]; then
    printf '%s  Endpoint  : https://%s:%s/<secret>%s\n' "$W" "$DOMAIN" "$PORT" "$NC"
    sleep 4
    HOOK=$(curl -s --max-time 10 "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" || true)
    ERRMSG=$(printf '%s' "$HOOK" | python3 -c \
      "import sys,json;print(json.load(sys.stdin)['result'].get('last_error_message',''))" 2>/dev/null || true)
    if [[ -n "$ERRMSG" ]]; then
      warn "Telegram reports: $ERRMSG"
      printf '%s    • check DNS:      dig +short %s\n' "$Y" "$DOMAIN"
      printf '    • check the port: ss -lntp | grep %s\n' "$PORT"
      printf '    • Cloudflare users: set the record to DNS-only%s\n' "$NC"
    else
      ok "webhook accepted by Telegram"
    fi
  fi
  echo
  printf '%s  Open Telegram and send /start to your bot.%s\n' "$W" "$NC"
  if [[ "$SST_MODE" == "webhook" && "$UPLOAD_CERT" != "true" ]]; then
    printf '%s  Send /app for the full terminal + file browser.%s\n' "$W" "$NC"
  fi
  echo
  printf '%s  Manage it with:  sst status | sst logs | sst restart | sst uninstall%s\n' "$C" "$NC"
  echo
  printf '%s  Security notes:%s\n' "$Y" "$NC"
  printf '%s    • This bot runs as root. Whoever controls Telegram account\n' "$D"
  printf '      %s controls this server.\n' "$USER_ID"
  printf '    • Turn on two-factor auth in Telegram (Settings → Privacy).\n'
  printf '    • %s/config.json holds your token, keep it 0600.%s\n' "$INSTALL_DIR" "$NC"
  echo
else
  die "service failed to start, run:  journalctl -u $SERVICE -n 50 --no-pager"
fi
