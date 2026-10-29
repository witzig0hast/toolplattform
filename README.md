# Tools-Plattform (Nginx Proxy Manager + Authentik)

`tools.example.de` ist komplett hinter deinem Authentik-Login. Jedes Tool läuft unter einem eigenen Pfad (`/home-assistant-fix/`) oder einer eigenen Subdomain (`proxy-settings.tools.example.de`) und ist nur für Mitglieder einer bestimmten Authentik-Gruppe sichtbar. Admins können Tools zeitlich begrenzt ohne Login teilen.

```
Browser ──► Nginx Proxy Manager ──(fragt)──► Authentik: "ist der eingeloggt?"
               │ ja: Name + Gruppen weiterreichen
               │ nein: erst Plattform fragen "gültige Freigabe-Sitzung?" – sonst Login
               ▼
           tools-web (nginx) ── fragt tools-api: "darf der das?" ──► Dateien in sites/<pfad>/
                                                                  └► oder weiter an einen Tool-Container
```

## Einmalige Einrichtung

### 1. Container starten
```
cp .env.example .env     # NPM_NETWORK, TOOLS_DOMAIN, TOOLS_UID/TOOLS_GID anpassen (Hinweise stehen in der Datei)
openssl rand -hex 32     # erzeugt ein Geheimnis -> in .env bei TOOLS_SECRET eintragen
mkdir -p data
docker compose up -d --build
```

### 2. Authentik
1. **Applications → Providers → Create** → *Proxy Provider*
   - Name: `tools`, Modus: **Forward auth (domain level)**
   - Authentication URL: `https://tools.example.de`, Cookie domain: `example.de`
2. **Applications → Create**: Name `Tools`, Slug `tools`, Provider `tools`.
3. **Applications → Outposts** → *authentik Embedded Outpost* → bearbeiten → Anwendung `Tools` hinzufügen.
4. **Directory → Groups → Create**: Gruppe `tools-admin` (oder wie `ADMIN_GROUP`) anlegen und dich hinzufügen.

### 3. Nginx Proxy Manager
**Proxy Host** `tools.example.de` → Scheme `http`, Forward Hostname `tools-web`, Port `8080`
- Tab *Details*: *Block Common Exploits* an, **Cache Assets aus** (sonst umgeht der NPM den Login für CSS/JS), *Websockets Support* an
- Tab *SSL*: Let's-Encrypt-Zertifikat, *Force SSL*, *HTTP/2*, *HSTS*
- Tab *Advanced*: Inhalt von `npm/advanced.conf` – darin `AUTHENTIK_IP` und `DEIN_TOOLS_SECRET` ersetzen
- Tab *Custom locations*, zwei Einträge, jeweils `http`, `tools-web`, `8080`, dann Zahnrad ⚙:
  - Location `/` → Inhalt von `npm/location-root.conf`
  - Location `/s/` → Inhalt von `npm/location-share.conf` (für Freigabe-Links, absichtlich ohne Login)

  In beiden `DEIN_TOOLS_SECRET` durch den Wert aus der `.env` ersetzen.

Optional für Tools mit eigener Subdomain: siehe unten.

### Docker-Aufbau
| Container | Image | Aufgabe | Erreichbar |
|---|---|---|---|
| `tools-web` | `toolplattform-web` (nginx, Portal eingebaut) | Portal, Dateien-Tools, Weiterleitung an Tool-Container. Port 8080 = Haupt-Domain, 8082 = Tool-Subdomains | nur über den NPM |
| `tools-api` | `toolplattform-api` (Python, ohne Fremdpakete) | Tool-Liste, Rechte, Freigabe-Links, Protokoll, Admin-API | nur intern, kein Internet |

Beide haben Healthchecks (`docker compose ps` zeigt `healthy`) und laufen read-only ohne Zusatzrechte. Von außen eingehängt sind nur `./sites` (Tool-Dateien) und `./data` (Tool-Liste, Freigabe-Links, Protokoll). **Backup:** `./sites` und `./data` sichern.

## Benutzung
- `https://tools.example.de/` – **Portal**: deine Tools als Kacheln, nach Kategorien gruppiert, mit Suche.
- `https://tools.example.de/_platform/admin/` – **Verwaltung** (nur `tools-admin`): Tools, Freigabe-Links, Protokoll, Export.

### Neues Tool
In der Verwaltung **+ Neues Tool**:
- **Kurzname** (Teil der Adresse), **Name**, **Beschreibung**, **Kategorie**, **Symbol**, **Authentik-Gruppe**
- **Art**: *Dateien* (Webseite in `sites/<kurzname>/`) oder *Weiterleitung* an einen laufenden Dienst
- **Aufruf über**: *Pfad* (`tools.example.de/<kurzname>/`) oder *eigene Subdomain* (`<kurzname>.tools.example.de`)
- **Wartungsmodus**: sperrt das Tool vorübergehend (Nutzer und Gäste sehen eine Wartungsseite, Admins kommen weiter hinein)

Danach in Authentik die Gruppe anlegen (falls neu) und Nutzer hinzufügen. Kein Neustart nötig.

### Tool mit Weiterleitung (Proxy)
Der Ziel-Container muss mit `tools-web` im Docker-Netz `toolplattform_tools` hängen:
```yaml
services:
  mein-tool:
    image: ...
    networks: [toolplattform]
networks:
  toolplattform:
    name: toolplattform_tools
    external: true
```
Ziel-Adresse dann `http://mein-tool:<port>` (Port **im** Container). Jede andere IP im Netz geht auch, z. B. `http://192.168.1.50:3000`. Nicht erlaubt sind `localhost`/Loopback, Link-Local (`169.254.x.x`) sowie `tools-web`/`tools-api`.

- **Pfad-Präfix entfernen** (Standard an): Der Dienst sieht `/` statt `/<kurzname>/`. Ausschalten, wenn der Dienst selbst eine Base-URL `/<kurzname>` hat.
- **Absolute Pfade** (`/api/...`, `/static/...`): Für angemeldete Nutzer erkennt die Plattform, aus welchem Tool die Anfrage kommt (Referer, sonst Merk-Cookie `tools_last`). Weiterleitungen wie `Location: /login` bekommen das Präfix.
- Der Dienst bekommt `X-Authentik-Username`, `X-Authentik-Groups`, `X-Authentik-Email`, `X-Authentik-Name` (für eigenes Single Sign-on).

### Tools mit eigener Subdomain (für Apps, die nur auf „/“ laufen)
Single-Page-Apps wie der Nginx Proxy Manager, Portainer oder Grafana ohne Base-URL funktionieren unter einem Pfad nicht („Oops, you've just found an error page“, „Dashboard“ springt ins Portal). Für sie **Aufruf über → Eigene Subdomain** wählen.

Einmalig einrichten:
1. `.env`: `TOOLS_DOMAIN=tools.example.de`, dann `docker compose up -d --build`.
2. **DNS**: Wildcard-Eintrag `*.tools.example.de` auf deinen Server.
3. **Zertifikat** im NPM für `*.tools.example.de` (Let's Encrypt mit **DNS Challenge**).
4. **NPM Proxy Host** `*.tools.example.de` → `tools-web`, **Port `8082`**, gleiche Einstellungen wie oben, Advanced = `npm/advanced.conf`, Custom location `/` (Port **8082**) = `npm/location-root.conf`. Keine `/s/`-Location.
5. Authentik: nichts zu tun (domain level, Cookie-Domain `example.de` gilt für alle Subdomains).

Statt Wildcard gehen auch einzelne Subdomains (alle mit Port `8082`).

### Öffentliche Freigabe-Links
Bei einem Tool **Teilen** (oder **+ Freigabe-Link**):
- **Gültigkeit**: Vorgaben (1 Std. bis 30 Tage), *eigene Dauer* (Minuten/Stunden/Tage) oder *bis zu einem Datum*. 1 Minute bis 365 Tage.
- **Maximale Öffnungen** (optional): wie viele verschiedene Browser den Link öffnen dürfen. **1 = Einmal-Link.** Wer ihn schon offen hat, kann weiterarbeiten.
- Bestehende Links kannst du **ändern** (verlängern, kürzen, Limit) oder **widerrufen** – das wirkt sofort, auch für Gäste, die gerade mitten in der App sind.
- Die Liste zeigt pro Link Restlaufzeit, Öffnungen und wann er zuletzt geöffnet wurde.

So funktioniert es für den Gast: Der Link `https://tools.example.de/s/<token>/` startet eine **Freigabe-Sitzung** (Cookie `tools_share`, HttpOnly, nur für diesen Browser).
- **Apps, die auf „/“ laufen** (Weiterleitung mit „Pfad-Präfix entfernen“ oder eigene Subdomain): Der Gast wird auf `/` geschickt und nutzt die App dort wie auf einer eigenen Domain. Damit funktionieren auch Apps mit absoluten Pfaden (`/api/...`) und Single-Page-Apps.
- **Dateien-Tools und Apps mit eigener Base-URL**: laufen unter `/s/<token>/`.

Wichtig:
- Der Link wird **nur einmal angezeigt**, gespeichert ist nur ein Hash.
- Wer den Link hat, kann alles, was das Tool erlaubt. Teile nur Tools, die für Fremde unbedenklich sind.
- Zum Ausprobieren ein **privates Fenster** nehmen: Bist du angemeldet, siehst du auf `/` dein Portal, nicht die geteilte App.
- Ein Gast hat immer nur eine Freigabe-Sitzung; ein zweiter Link im selben Browser ersetzt die erste.

### Protokoll und Export
Die Verwaltung zeigt ein **Protokoll** (`data/audit.log`, rotiert bei 1 MB): angelegte/geänderte/entfernte Tools (mit geänderten Feldern), erstellte/geänderte/widerrufene Freigabe-Links, jede neue Öffnung eines Freigabe-Links mit IP-Adresse und abgewiesene Öffnungen. **Konfiguration exportieren** lädt die Tool-Liste als JSON herunter.

## Update einer bestehenden Installation
```
git pull
docker compose up -d --build
```
**Mit diesem Update ändern sich die NPM-Snippets** (sonst hängen Freigabe-Links weiter am Login):
1. Tab *Advanced*: `npm/advanced.conf` komplett neu einfügen (neu: `recursive_error_pages on;` und der Block `@tools_maybe_share` – darin `DEIN_TOOLS_SECRET` ersetzen).
2. Custom location `/`: `npm/location-root.conf` neu einfügen (neu: `error_page 401 = @tools_maybe_share;`).
3. Gibt es die Custom location `/s/` noch nicht: anlegen (siehe Einrichtung).
4. Gilt auch für einen Wildcard-Host `*.tools…`, falls vorhanden (dort nur Advanced + `/`).

## Sicherheit
- **Login**: Alles außer Freigabe-Links liegt hinter Authentik. Keine passende Gruppe → „Kein Zugriff“ (403).
- **Gemeinsames Geheimnis** (`TOOLS_SECRET`): Das Backend glaubt Login-Headern nur, wenn der NPM das Geheimnis mitschickt. Ein Container im selben Docker-Netz kann so keine Identität vortäuschen. Vom Client mitgeschickte Login-Header werden im NPM überschrieben bzw. entfernt. Das Geheimnis geht nie an Tools.
- **Netze**: `tools-web` ohne veröffentlichten Port; `tools-api` nur im internen Netz ohne Internet. Container read-only, ohne Capabilities, `no-new-privileges`.
- **Pfade**: Jede Anfrage wird vom Backend geprüft; Tricks wie `..`, `%2e%2e`, `%2f`, `//` werden abgelehnt.
- **Freigabe-Links**: 256-Bit-Zufalls-Token, nur als Hash gespeichert (Datei `0600`), Ablauf und Limits werden bei jeder Anfrage geprüft, Widerruf wirkt sofort. Sitzungs-Cookie `HttpOnly; Secure; SameSite=Lax`. Gäste kommen nie in Portal, Verwaltung oder Plattform-API. Nicht indexierbar (`X-Robots-Tag`), Token geht nicht per Referer an Dritte (`Referrer-Policy: same-origin`).
- **Ratenbegrenzung**: Gäste 50 Anfragen/s (Burst 300) pro IP, Plattform-API 10/s (Burst 30) → sonst 429.
- **Admin-Aktionen**: nur JSON, `Origin` muss passen, `Sec-Fetch-Site` muss `same-origin` sein (Schutz gegen CSRF). Alles wird protokolliert.
- **Header**: HSTS, CSP (Portal/Verwaltung streng, ohne Inline-Skripte), `X-Frame-Options: DENY`, `nosniff`, COOP/CORP, `Permissions-Policy`, `Cache-Control: no-store`.
- **Ziel-Adressen**: Loopback, Link-Local und die Plattform-Container sind als Weiterleitungsziel gesperrt. Nur Admins können Ziele anlegen.
- Tool-Container im Netz `toolplattform_tools` können sich gegenseitig erreichen; hänge nur Dienste hinein, denen du vertraust. `tools-web` nicht in weitere fremde Netze hängen.
- Weiterleitungen der Plattform sind `302` (nicht `301`), damit Browser sie nicht dauerhaft speichern.

## Tests
Ende-zu-Ende-Tests mit echtem nginx: nachgebauter NPM (mit den echten Snippets aus `npm/`), nachgebauter Authentik-Outpost, `tools-web` mit der echten Konfiguration, `tools-api` und Test-Apps. Geprüft werden u. a. Login, Gruppen, Freigabe-Links samt Sitzung, Einmal-Link, Widerruf, Wartung, Pfad-Tricks, gefälschte Header, CSRF, Ratenbegrenzung und Protokoll.
```
sudo tests/e2e/up.sh && python3 tests/e2e/test.py; sudo tests/e2e/down.sh
```
Braucht `nginx` und `python3` (legt Test-IPs `10.99.0.2-4` auf `lo` an).

Ohne Docker/nginx gibt es eine schnelle UI-Vorschau mit simuliertem Login: `python3 dev/dev.py` → http://localhost:8099 (`DEV_GROUPS="tool-a|tool-b"` für normale Nutzer).

## Grenzen
- Eine eigene Authentik-*Application* pro Tool geht bei Forward-Auth nicht (Authentik ordnet nach Host). Darum: eine Application + eine Gruppe pro Tool.
- Freigabe-Links gibt es nur über die Haupt-Domain, nicht über Tool-Subdomains (dort ist ohnehin alles hinter Login).
- Nicht getestet: der echte Nginx Proxy Manager, der echte Authentik-Outpost und das Wildcard-Zertifikat. Die Tests nutzen Nachbauten mit denselben Snippets.
