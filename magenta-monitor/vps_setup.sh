#!/usr/bin/env bash
# Εγκατάσταση στο VPS (Ubuntu): nginx με HTTPS πάνω στην IP του VPS, που προωθεί
# στο SSH tunnel (127.0.0.1:5055). Τρέξε:  sudo bash vps_setup.sh
set -euo pipefail
REMOTE_PORT="${REMOTE_PORT:-5055}"
IP="$(curl -4 -s https://ifconfig.me || hostname -I | awk '{print $1}')"
echo ">> IP του VPS: $IP   (θύρα tunnel: $REMOTE_PORT)"

apt-get update -y
apt-get install -y nginx openssl

# Self-signed πιστοποιητικό για την IP (10 χρόνια)
mkdir -p /etc/nginx/magenta
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -keyout /etc/nginx/magenta/key.pem -out /etc/nginx/magenta/cert.pem \
  -subj "/CN=$IP" -addext "subjectAltName=IP:$IP"

cat > /etc/nginx/sites-available/magenta-monitor <<NGINX
server {
    listen 80;
    server_name _;
    return 301 https://\$host\$request_uri;
}
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate     /etc/nginx/magenta/cert.pem;
    ssl_certificate_key /etc/nginx/magenta/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:${REMOTE_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_read_timeout 60s;
        proxy_intercept_errors on;
        error_page 502 504 = @offline;
    }
    location @offline {
        default_type text/html;
        return 503 '<meta charset="utf-8"><h2 style="font-family:sans-serif">Το PC με το Magenta Monitor δεν είναι συνδεδεμένο.</h2><p style="font-family:sans-serif">Πάτα «Άνοιγμα (μέσω VPS)» στο Node-RED.</p>';
    }
}
NGINX
ln -sf /etc/nginx/sites-available/magenta-monitor /etc/nginx/sites-enabled/magenta-monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx || systemctl restart nginx

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
fi
echo ">> Έτοιμο. Άνοιξε https://$IP (θα δεις προειδοποίηση πιστοποιητικού την πρώτη φορά)."
