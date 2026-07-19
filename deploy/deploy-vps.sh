#!/usr/bin/env bash
# ============================================================
# Μαέρα & Διόνυσος — VPS deployment script (Ubuntu/Debian)
#
# Installs everything and serves the site + booking API on port 80.
# Safe to re-run: running it again pulls the latest code and restarts.
#
# Usage (as root on the VPS):
#   bash <(curl -fsSL https://raw.githubusercontent.com/diothegreat/ZarosMap/claude/zaros-accommodation-website-gkpbel/deploy/deploy-vps.sh)
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/diothegreat/ZarosMap.git"
BRANCH="claude/zaros-accommodation-website-gkpbel"
APP_DIR="/var/www/zaros"
SERVICE="zaros-booking"
NODE_PORT=3000

echo "==> [1/6] System packages (git, nginx, curl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git nginx curl ca-certificates

echo "==> [2/6] Node.js"
if ! command -v node >/dev/null 2>&1 || [ "$(node -e 'console.log(process.versions.node.split(".")[0])')" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -qq nodejs
fi
echo "    node $(node -v)"

echo "==> [3/6] Fetching site code ($BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "==> [4/6] Booking server dependencies"
cd "$APP_DIR/server"
npm install --omit=dev --no-fund --no-audit --loglevel=error

# Create .env on first run (Stripe keys stay empty until the account exists)
if [ ! -f "$APP_DIR/server/.env" ]; then
  SERVER_IP=$(curl -fsSL -4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  cat > "$APP_DIR/server/.env" <<EOF
SITE_URL=http://${SERVER_IP}
PORT=${NODE_PORT}
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
EOF
  echo "    Created server/.env (fill in Stripe keys later to activate payments)"
fi

echo "==> [5/6] systemd service ($SERVICE)"
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=Zaros booking server (Maera & Dionysos)
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/server
ExecStart=$(command -v node) server.js
Restart=always
RestartSec=5
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
EOF
chown -R www-data:www-data "$APP_DIR"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl restart "$SERVICE"

echo "==> [6/6] nginx reverse proxy (port 80 -> ${NODE_PORT})"
cat > /etc/nginx/sites-available/zaros <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:${NODE_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/zaros /etc/nginx/sites-enabled/zaros
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

sleep 2
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1/api/config" || true)
IP=$(curl -fsSL -4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
echo
echo "============================================================"
if [ "$STATUS" = "200" ]; then
  echo "  ΕΤΟΙΜΟ! Η ιστοσελίδα τρέχει στο: http://${IP}/"
else
  echo "  Η εγκατάσταση ολοκληρώθηκε, αλλά ο έλεγχος επέστρεψε: $STATUS"
  echo "  Δείτε τα logs: journalctl -u ${SERVICE} -n 50"
fi
echo "  - Επανεκκίνηση server:  systemctl restart ${SERVICE}"
echo "  - Ενημέρωση σελίδας:    ξανατρέξτε αυτό το script"
echo "  - Ενεργοποίηση Stripe:  επεξεργαστείτε το ${APP_DIR}/server/.env"
echo "============================================================"
