# Tools-Plattform (Nginx Proxy Manager + Authentik)

`tools.example.de` ist komplett hinter deinem Authentik-Login. Jeder Slash (z. B. `/home-assistant-fix`) ist ein Ordner und nur für Mitglieder einer bestimmten Authentik-Gruppe sichtbar.

```
Browser ──► Nginx Proxy Manager ──(fragt)──► Authentik: "ist der eingeloggt?"
               │ ja: Name+Gruppen weiterreichen
               ▼
           tools-web (dieser Container) ── prüft Gruppe pro Slash ──► Dateien in sites/<slug>/
```

## Einmalige Einrichtung

### 1. Container starten
```
cp .env.example .env     # NPM_NETWORK anpassen (siehe Kommentar in der Datei)
docker compose up -d
```

### 2. Authentik: Anwendung für die Subdomain
1. Authentik → **Applications → Providers → Create** → *Proxy Provider*
   - Name: `tools`
   - Modus: **Forward auth (domain level)**
   - Authentication URL: `https://tools.example.de`
   - Cookie domain: `example.de`
2. **Applications → Create**: Name `Tools`, Slug `tools`, Provider `tools`.
3. **Applications → Outposts** → *authentik Embedded Outpost* → bearbeiten → Anwendung `Tools` hinzufügen.

### 3. Nginx Proxy Manager: Proxy Host
1. **Hosts → Proxy Hosts → Add**
   - Domain: `tools.example.de`, Scheme `http`, Forward Hostname `tools-web`, Port `8080`
   - *Block Common Exploits* an
   - Tab **SSL**: neues Let's-Encrypt-Zertifikat, *Force SSL*, *HTTP/2*, *HSTS* an
2. Tab **Advanced**: Inhalt von `npm/advanced.conf` einfügen (vorher `AUTHENTIK_IP` ersetzen).
3. Tab **Custom locations** → *Add location*: `/`, http, `tools-web`, `8080` → Zahnrad ⚙ → Inhalt von `npm/location-root.conf` einfügen. Speichern.

## Neues Tool anlegen
1. `./new-tool.sh mein-tool` (nutzt Gruppe `tool-mein-tool`)
2. Deine Dateien in `sites/mein-tool/` legen (mindestens `index.html`).
3. In Authentik: **Directory → Groups → Create** `tool-mein-tool`, Nutzer hinzufügen.
4. `docker compose exec tools-web nginx -s reload`

Erreichbar unter `https://tools.example.de/mein-tool/`.
Dateien in einem bestehenden Tool ändern braucht keinen Reload.

## Sicherheit
- Alles hinter Login. Keine passende Gruppe → 403, unbekannter Pfad → 404.
- `tools-web` hat keinen veröffentlichten Port; ohne Authentik-Header liefert er nichts (401).
- Der NPM überschreibt die Identitäts-Header, Clients können sie nicht fälschen.
- Wichtig: `tools-web` nicht in weitere Docker-Netze hängen, in denen andere Container sind, denen du nicht traust (sie könnten Header selbst setzen).
- Eine eigene Authentik-Application pro Slash geht bei Forward-Auth nicht (Authentik ordnet nach Host, nicht Pfad). Darum: eine Application + eine Gruppe pro Tool.
- Hinweis: Konfiguration wurde noch nicht in einer laufenden Umgebung getestet.
