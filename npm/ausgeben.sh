#!/bin/sh
# Gibt die drei Texte für den Nginx Proxy Manager fertig ausgefüllt aus (Geheimnis aus .env, Authentik-Adresse).
# Nutzung:  sh npm/ausgeben.sh 192.168.178.140:9000
cd "$(dirname "$0")/.." || exit 1
AUTH="${1:-}"
SECRET=$(grep '^TOOLS_SECRET=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' \r')
if [ -z "$SECRET" ]; then echo "FEHLER: TOOLS_SECRET fehlt in .env"; exit 1; fi
if [ -z "$AUTH" ]; then echo "FEHLER: Authentik-Adresse angeben, z. B.: sh npm/ausgeben.sh 192.168.178.140:9000"; exit 1; fi
show() {
  echo
  echo "################################################################"
  echo "## $1"
  echo "################################################################"
  grep -v '^[[:space:]]*#' "$2" | sed -e "s#DEIN_TOOLS_SECRET#$SECRET#g" -e "s#AUTHENTIK_IP:9000#$AUTH#g" | sed '/./,$!d'
}
show "1) Proxy Host -> Tab 'Advanced'" npm/advanced.conf
show "2) Custom location '/'   (http, tools-web, 8080) -> Zahnrad" npm/location-root.conf
show "3) Custom location '/s/' (http, tools-web, 8080) -> Zahnrad" npm/location-share.conf
echo
