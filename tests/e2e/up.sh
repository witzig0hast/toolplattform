#!/bin/bash
# Baut aus den echten Repo-Dateien eine Testumgebung: NPM-Nachbau (:8443) -> tools-web (:8080/:8082) -> tools-api (:8000)
# Nutzung (als root, braucht nginx + python3):  tests/e2e/up.sh && python3 tests/e2e/test.py; tests/e2e/down.sh
set -e
H=$(cd "$(dirname "$0")" && pwd); R=$(cd "$H/../.." && pwd); T=${E2E_DIR:-/tmp/toolplattform-e2e}
# Test-Apps brauchen "normale" IPs (Loopback-Ziele verbietet die Plattform zu Recht)
for i in 2 3 4 5; do ip addr add 10.99.0.$i/32 dev lo 2>/dev/null || true; done
"$H/down.sh" 2>/dev/null || true
rm -rf $T; mkdir -p $T/sites/datei-tool $T/data $T/ngx/logs
echo '<!doctype html><title>Datei</title><link rel=stylesheet href="style.css"><h1>Datei-Tool</h1>' > $T/sites/datei-tool/index.html
echo 'h1{color:green}' > $T/sites/datei-tool/style.css
python3 - "$T" <<'PY'
import json, sys
json.dump({
 'strom': {'name':'Strom','description':'','icon':'','group':'tool-strom','type':'proxy','upstream':'http://10.99.0.3:9001','strip_prefix':True,'access':'path'},
 'npm': {'name':'NPM','description':'','icon':'','group':'tool-npm','type':'proxy','upstream':'http://10.99.0.2:9001','strip_prefix':True,'access':'subdomain'},
 'basisapp': {'name':'Basis','description':'','icon':'','group':'tool-strom','type':'proxy','upstream':'http://10.99.0.4:9001','strip_prefix':False,'access':'path'},
 'spa': {'name':'SPA','description':'Single-Page-App','icon':'','group':'tool-strom','type':'proxy','upstream':'http://10.99.0.5:9001','strip_prefix':True,'access':'path'},
 'datei-tool': {'name':'Datei','description':'','icon':'','group':'tool-datei','access':'path'},
}, open(sys.argv[1] + '/data/tools.json', 'w'))
PY
# tools-web
sed -e "s#/etc/nginx/snippets/tool-proxy.conf#$T/tool-proxy.conf#" -e "s#/srv/portal#$R/portal#g" -e "s#/srv/sites#$T/sites#g" \
    -e "s#tools-api:8000#127.0.0.1:8000#g" -e "s#listen 8080 #listen 127.0.0.1:8080 #" -e "s#listen 8082 #listen 127.0.0.1:8082 #" $R/nginx/default.conf > $T/tools-web.conf
cp $R/nginx/tool-proxy.conf $T/tool-proxy.conf
# NPM-Nachbau mit den echten Snippets
sed -e "s#http://AUTHENTIK_IP:9000#http://127.0.0.1:9100#" -e "s#tools-web:8080#127.0.0.1:8080#g" -e "s#DEIN_TOOLS_SECRET#geheim#g" $R/npm/advanced.conf > $T/npm-adv.conf
sed -e "s#DEIN_TOOLS_SECRET#geheim#g" $R/npm/location-root.conf > $T/npm-root.conf
sed -e "s#DEIN_TOOLS_SECRET#geheim#g" $R/npm/location-share.conf > $T/npm-share.conf
cat > $T/npm.conf <<NPM
server {
  listen 127.0.0.1:8443;
  server_name tools.test;
  include $T/npm-adv.conf;
  # wie NPM "Custom location" /
  location / {
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Scheme \$scheme;
    proxy_set_header X-Forwarded-Proto  \$scheme;
    proxy_set_header X-Forwarded-For    \$remote_addr;
    proxy_set_header X-Real-IP          \$remote_addr;
    proxy_pass http://127.0.0.1:8080;
    include $T/npm-root.conf;
  }
  # wie NPM "Custom location" /s/
  location /s/ {
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Scheme \$scheme;
    proxy_set_header X-Forwarded-Proto  \$scheme;
    proxy_set_header X-Forwarded-For    \$remote_addr;
    proxy_set_header X-Real-IP          \$remote_addr;
    proxy_pass http://127.0.0.1:8080;
    include $T/npm-share.conf;
  }
}
NPM
cat > $T/nginx.conf <<NG
user root;
worker_processes 1;
error_log $T/ngx/logs/error.log warn;
pid $T/ngx/nginx.pid;
events {}
http {
  include /etc/nginx/mime.types;
  access_log off;
  client_body_temp_path $T/ngx/cb; proxy_temp_path $T/ngx/px; fastcgi_temp_path $T/ngx/fc; uwsgi_temp_path $T/ngx/uw; scgi_temp_path $T/ngx/sc;
  include $T/tools-web.conf;
  include $T/npm.conf;
}
NG
nginx -t -c $T/nginx.conf -p $T/ngx 2>&1 | grep -v "syntax is ok\|test is successful" || true
cd "$T"
python3 $H/mockapp.py 10.99.0.3 Strom-App >/dev/null 2>&1 &
python3 $H/mockapp.py 10.99.0.2 NPM-App >/dev/null 2>&1 &
python3 $H/mockapp.py 10.99.0.4 Basis-App >/dev/null 2>&1 &
python3 $H/mock_spa.py 10.99.0.5 >/dev/null 2>&1 &
python3 $H/mock_authentik.py >/dev/null 2>&1 &
TOOLS_DATA=$T/data/tools.json TOOLS_SITES=$T/sites TOOLS_SECRET=geheim TOOLS_DOMAIN=tools.test \
  TOOLS_API_SELF=http://127.0.0.1:8000 python3 $R/app/server.py >$T/api.log 2>&1 &
python3 $H/mock_webhook.py $T/notifications.log >/dev/null 2>&1 &
mkdir -p $T/data/status
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TOOLS_DATA=$T/data/tools.json HEALTH_INTERVAL=3 HEALTH_FAILS=1 HEALTH_TIMEOUT=2 \
  NOTIFY_WEBHOOK_URL=http://127.0.0.1:9200/hook python3 $R/app/monitor.py >$T/monitor.log 2>&1 &
sleep 1
nginx -c $T/nginx.conf -p $T/ngx && echo "Testumgebung läuft"
