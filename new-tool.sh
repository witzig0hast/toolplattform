#!/usr/bin/env bash
# Nutzung: ./new-tool.sh <slug> [authentik-gruppe]
# Legt sites/<slug>/ und access/<slug>.caddy an. Gruppe default: tool-<slug>
set -euo pipefail
slug="${1:?Slug fehlt}"
[[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "Ungültiger Slug (a-z, 0-9, -)"; exit 1; }
group="${2:-tool-$slug}"
cd "$(dirname "$0")"
[[ -e "access/$slug.caddy" ]] && { echo "Tool existiert schon"; exit 1; }
mkdir -p "sites/$slug"
[[ -e "sites/$slug/index.html" ]] || printf '<!doctype html><meta charset=utf-8><title>%s</title><h1>%s</h1>\n' "$slug" "$slug" > "sites/$slug/index.html"
cat > "access/$slug.caddy" <<EOT
handle /$slug/* {
	@allowed header_regexp grp X-Authentik-Groups (^|\\|)$group(\\||\$)
	handle @allowed {
		root * /srv/sites
		file_server
	}
	respond "Forbidden" 403
}
EOT
echo "Tool /$slug angelegt. Authentik-Gruppe: $group"
echo "Reload: docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile"
