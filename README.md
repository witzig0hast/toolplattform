# Tools-Plattform

`tools.example.de` mit Authentik-Login (Caddy Forward-Auth). Jeder Slash (`/home-assistant-fix`) ist ein Ordner unter `sites/` und nur für Mitglieder einer Authentik-Gruppe erreichbar.

## Einrichtung (einmalig)
1. In Authentik: **Proxy Provider** → *Forward auth (domain level)*, Authentication URL `https://tools.example.de`, Cookie-Domain `example.de`. Dazu eine **Application** "Tools" und den Provider im (Embedded) Outpost zuweisen.
2. `cp .env.example .env` und anpassen, dann `docker compose up -d`.

## Neues Tool
1. `./new-tool.sh mein-tool` (Gruppe default `tool-mein-tool`)
2. Dateien nach `sites/mein-tool/` legen.
3. In Authentik Gruppe `tool-mein-tool` anlegen, Nutzer zuweisen (optional: Policy auf die Application).
4. `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`

## Sicherheit
- Alles hinter Login; ohne passende Gruppe → 403, unbekannter Pfad → 404.
- Identity-Header vom Client werden verworfen, nur Authentik-Werte zählen.
- HSTS, CSP, no-sniff, X-Frame-Options DENY; Container read-only, ohne Capabilities.
- Hinweis: Eine eigene Authentik-*Application pro Slash* geht mit Forward-Auth nicht (Authentik matcht nach Host, nicht Pfad). Stattdessen: eine Application + eine Gruppe pro Tool.
