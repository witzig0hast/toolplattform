#!/usr/bin/env bash
# Nutzung: ./new-tool.sh <slug> [authentik-gruppe]
# Legt sites/<slug>/ und nginx/tools.d/<slug>.conf an. Gruppe default: tool-<slug>
set -euo pipefail
slug="${1:?Slug fehlt}"
[[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "Ungültiger Slug (a-z, 0-9, -)"; exit 1; }
group="${2:-tool-$slug}"
[[ "$group" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Ungültiger Gruppenname (A-Z a-z 0-9 _ -)"; exit 1; }
cd "$(dirname "$0")"
[[ -e "nginx/tools.d/$slug.conf" ]] && { echo "Tool existiert schon"; exit 1; }
mkdir -p "sites/$slug"
[[ -e "sites/$slug/index.html" ]] || printf '<!doctype html><meta charset=utf-8><title>%s</title><h1>%s</h1>\n' "$slug" "$slug" > "sites/$slug/index.html"
cat > "nginx/tools.d/$slug.conf" <<EOT
location = /$slug { return 301 /$slug/; }
location /$slug/ {
	if (\$http_x_authentik_groups !~ "(^|[|])$group([|]|\$)") { return 403; }
	try_files \$uri \$uri/ =404;
}
EOT
echo "Tool /$slug angelegt. Benötigte Authentik-Gruppe: $group"
echo "Übernehmen: docker compose exec tools-web nginx -s reload"
