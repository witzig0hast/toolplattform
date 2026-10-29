# Tools-Plattform (Nginx Proxy Manager + Authentik)

`tools.example.de` ist komplett hinter deinem Authentik-Login. Jeder Slash (z. B. `/home-assistant-fix`) ist ein Ordner und nur für Mitglieder einer bestimmten Authentik-Gruppe sichtbar.

```
Browser ──► Nginx Proxy Manager ──(fragt)──► Authentik: "ist der eingeloggt?"
               │ ja: Name+Gruppen weiterreichen
               ▼
           tools-web (nginx) ── fragt tools-api: "Gruppe passt?" ──► Dateien in sites/<slug>/
```

## Einmalige Einrichtung

### 1. Container starten
```
cp .env.example .env     # NPM_NETWORK, TOOLS_UID/TOOLS_GID anpassen (Hinweise stehen in der Datei)
openssl rand -hex 32     # erzeugt ein Geheimnis -> in .env bei TOOLS_SECRET eintragen
mkdir -p data
docker compose up -d --build   # baut beide Images und startet sie
```

### 2. Authentik: Anwendung für die Subdomain
1. Authentik → **Applications → Providers → Create** → *Proxy Provider*
   - Name: `tools`
   - Modus: **Forward auth (domain level)**
   - Authentication URL: `https://tools.example.de`
   - Cookie domain: `example.de`
2. **Applications → Create**: Name `Tools`, Slug `tools`, Provider `tools`.
3. **Applications → Outposts** → *authentik Embedded Outpost* → bearbeiten → Anwendung `Tools` hinzufügen.
4. **Directory → Groups → Create**: Gruppe `tools-admin` (oder wie in `ADMIN_GROUP`) anlegen und dich hinzufügen.

### 3. Nginx Proxy Manager: Proxy Host
1. **Hosts → Proxy Hosts → Add**
   - Domain: `tools.example.de`, Scheme `http`, Forward Hostname `tools-web`, Port `8080`
   - *Block Common Exploits* an
   - **Cache Assets AUS lassen** (sonst umgeht der NPM den Login für .css/.js-Dateien: Seite ohne Design, 401 bei `/assets/…`)
   - Tab **SSL**: neues Let's-Encrypt-Zertifikat, *Force SSL*, *HTTP/2*, *HSTS* an
2. Tab **Advanced**: Inhalt von `npm/advanced.conf` einfügen (vorher `AUTHENTIK_IP` ersetzen).
3. Tab **Custom locations** → *Add location*: `/`, http, `tools-web`, `8080` → Zahnrad ⚙ → Inhalt von `npm/location-root.conf` einfügen. Dort `DEIN_TOOLS_SECRET` durch den Wert von `TOOLS_SECRET` aus der `.env` ersetzen. Speichern.
4. Tab **Details**: **Cache Assets muss aus** sein (sonst fehlt bei CSS/JS der Login). **Websockets Support** anschalten, falls ein weitergeleitetes Tool WebSockets braucht.

### Docker-Aufbau
| Container | Image | Aufgabe | Erreichbar |
|---|---|---|---|
| `tools-web` | `toolplattform-web` (nginx, Portal eingebaut) | liefert Portal und Dateien-Tools aus, leitet an Proxy-Tools weiter, fragt `tools-api` nach den Rechten. Port 8080 = Haupt-Domain, Port 8082 = Tool-Subdomains | nur über den NPM |
| `tools-api` | `toolplattform-api` (Python) | Tool-Liste, Rechte, Admin-API | nur intern, kein Internet |

Beide haben Healthchecks (`docker compose ps` zeigt `healthy`). Von außen eingehängt sind nur `./sites` (deine Tool-Dateien) und `./data` (die Tool-Liste). Nach Änderungen am Portal/nginx: `docker compose up -d --build`. Backup: `./sites` und `./data` sichern.

## Benutzung
- `https://tools.example.de/` – **Portal**: Kacheln mit allen Tools, die du nutzen darfst.
- `https://tools.example.de/_platform/admin/` – **Verwaltung** (nur Gruppe `tools-admin`): Tools anlegen, bearbeiten, entfernen und je einer Authentik-Gruppe zuordnen.

### Neues Tool
1. In `/_platform/admin/` auf **+ Neues Tool**: Pfad, Name, Beschreibung, Symbol, Gruppe und die **Art**:
   - **Dateien**: deine Webseite liegt in `sites/<pfad>/` (`index.html` wird als Platzhalter erzeugt).
   - **Weiterleitung**: ein laufender Dienst, z. B. `http://mein-container:8080` (siehe unten).
2. In Authentik die Gruppe anlegen (falls neu) und Nutzer hinzufügen.
Kein Neustart nötig. Rechte-Änderungen in Authentik gelten ab dem nächsten Seitenaufruf.

### Tool mit Weiterleitung (Proxy)
Die Plattform leitet `/<pfad>/` an einen anderen Container weiter - mit Login und Gruppenprüfung davor. Der Ziel-Container muss mit `tools-web` in einem gemeinsamen Docker-Netzwerk sein. Dafür gibt es `toolplattform_tools` (wird beim Start angelegt). In der Compose-Datei deines Tools:
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
Als Ziel-Adresse trägst du dann `http://mein-tool:<port>` ein (Container-Name aus der Compose-Datei, Port **im** Container). Alternativ geht jede IP im Netzwerk, z. B. `http://192.168.1.50:3000`.

- **Apps mit absoluten Pfaden** (`/api/...`, `/assets/...`, `/static/...`): Viele Apps rufen ihre Daten unter Pfaden ab, die mit `/` beginnen und den Tool-Pfad nicht enthalten. Die Plattform erkennt das: Anfragen an einen unbekannten Pfad gehen an das Tool, aus dem sie kommen (per Referer) bzw. das du zuletzt geöffnet hast (Merk-Cookie `tools_last`, 12 Stunden). Das gilt für Tools mit „Pfad-Präfix entfernen“. Absolute Weiterleitungen (`Location: /login`) bekommen automatisch das Präfix. Grenzen: Wer zwei verschiedene Tools gleichzeitig in zwei Tabs nutzt, kann bei Seiten ohne Referer vertauscht werden; ein Tool, das selbst `https://tools.example.de/...` fest in den Code schreibt, bleibt eine Ausnahme. Für solche Apps ist eine eigene Subdomain die sauberste Lösung.
- **Pfad-Präfix entfernen** (Standard an): Der Dienst sieht `/`, nicht `/<pfad>/`. Das passt für Dienste, die auf der Wurzel laufen. Läuft der Dienst selbst unter einem Unterpfad (Base-URL `/<pfad>`), schalte es aus. Viele Apps (z. B. Home Assistant) funktionieren unter einem Unterpfad nur eingeschränkt; dann ist eine eigene Subdomain sauberer.
- Der Dienst bekommt die Header `X-Authentik-Username`, `X-Authentik-Groups`, `X-Authentik-Email` und `X-Authentik-Name`, kann sich also darauf für Single Sign-on stützen.
- Nicht erlaubte Ziele: `localhost`/Loopback, Link-Local (`169.254.x.x`) sowie `tools-web`/`tools-api`.

### Tools mit eigener Subdomain (für Apps, die nur auf „/“ laufen)
Manche Apps funktionieren nicht unter einem Pfad wie `tools.example.de/proxy-settings/`. Das betrifft vor allem **Single-Page-Apps** wie den Nginx Proxy Manager, Portainer oder Grafana ohne Base-URL. Sie lesen die Adresse im Browser selbst aus und kennen dann `/proxy-settings/` nicht („Oops, you've just found an error page“). Ihre internen Links („Dashboard“) springen außerdem auf `/`, also zurück ins Portal oder auf fremde Pfade.

Für solche Apps wählst du beim Tool **Aufruf über → Eigene Subdomain**. Das Tool läuft dann unter `<kurzname>.tools.example.de`, z. B. `proxy-settings.tools.example.de`. Dort gehört die ganze Adresse dem Tool, Login und Gruppenprüfung bleiben gleich. Im Portal verlinkt die Kachel automatisch auf die Subdomain.

**Einmalig einrichten** (danach braucht kein Tool mehr einen eigenen NPM-Eintrag):
1. `.env`: `TOOLS_DOMAIN=tools.example.de` setzen, dann `docker compose up -d --build`.
2. **DNS**: einen Wildcard-Eintrag `*.tools.example.de` auf deinen Server (wie `tools.example.de`).
3. **Zertifikat**: im NPM unter *SSL Certificates → Add → Let's Encrypt* ein Zertifikat für `*.tools.example.de` anlegen. Wildcard-Zertifikate gehen nur mit **DNS Challenge** (Haken „Use a DNS Challenge“, deinen DNS-Anbieter wählen, API-Zugangsdaten eintragen).
4. **NPM Proxy Host** neu anlegen:
   - Domain: `*.tools.example.de`, Scheme `http`, Forward Hostname `tools-web`, **Port `8082`**
   - *Cache Assets* aus, *Block Common Exploits* an, *Websockets Support* an
   - SSL: das Wildcard-Zertifikat, *Force SSL*, *HTTP/2*
   - Advanced: wie beim Haupt-Host den Inhalt von `npm/advanced.conf`
   - Custom location `/` → `tools-web`, **Port `8082`**, Zahnrad: Inhalt von `npm/location-root.conf` (mit deinem `TOOLS_SECRET`)
5. **Authentik**: nichts zu tun, solange der Provider `tools` auf *Forward auth (domain level)* mit Cookie-Domain `example.de` steht. Das gilt dann automatisch auch für alle Subdomains.

Hinweise:
- Freigabe-Links funktionieren nur über den Pfad (`tools.example.de/s/...`), nicht für die Subdomain. Bei Apps, die nur auf „/“ laufen, klappt der Freigabe-Link daher meist nicht.
- Statt Wildcard kannst du auch einzelne Subdomains im NPM anlegen (z. B. nur `proxy-settings.tools.example.de`), alle mit Port `8082`.

### Öffentliche Freigabe-Links
Als Admin kannst du ein Tool ohne Login teilen: in `/_platform/admin/` bei einem Tool auf **Teilen** (oder unten **+ Freigabe-Link**). Die Gültigkeit stellst du genau ein:
- Vorgaben: 1 Stunde, 24 Stunden, 7 Tage, 30 Tage
- **Eigene Dauer**: beliebige Zahl in Minuten, Stunden oder Tagen
- **Bis zu einem Datum**: genauer Ablaufzeitpunkt (deine Ortszeit)

Minimum 1 Minute, Maximum 365 Tage. Der Link sieht so aus: `https://tools.example.de/s/<langer-zufälliger-Token>/`.

- **Zeitbegrenzung:** Die Plattform prüft bei jedem Aufruf, ob der Link noch gültig ist. Abgelaufene Links zeigen eine Fehlerseite. Du kannst jeden Link jederzeit **widerrufen**.
- **Der Link wird nur einmal angezeigt.** Gespeichert wird nur ein Hash, nicht der Link selbst (`data/shares.json`).
- Wer den Link kennt, braucht **kein Authentik-Konto** und kann das Tool nutzen. Das Tool sieht dabei keinen Benutzer (keine `X-Authentik-*`-Header).
- Teile nur Tools, die für fremde Personen unbedenklich sind. Tools mit Schreib- oder Löschfunktion sind per Link für jeden benutzbar, der ihn hat.

**Einmalig im NPM einrichten** (sonst führt der Link auf die Anmeldeseite): Proxy Host → Tab **Custom locations** → *Add location*: `/s/`, http, `tools-web`, `8080` → Zahnrad ⚙ → Inhalt von `npm/location-share.conf` einfügen (`DEIN_TOOLS_SECRET` durch den Wert aus der `.env` ersetzen). Diese Location hat **absichtlich kein Authentik-Login**; die Plattform prüft selbst, ob der Link gültig ist. Alles außerhalb von `/s/` bleibt hinter dem Login.

*Alternative über Authentik (ungetestet):* Provider `tools` → *Unauthenticated URLs*. Wie Authentik die Regel im Forward-Auth-Modus abgleicht (nur Pfad oder volle URL), konnte nicht geprüft werden; eine Regel, die beides abdeckt, wäre `^(https?://[^/]+)?/s/.*`. Der NPM-Weg oben ist verlässlicher.

**Was über Freigabe-Links funktioniert:** Dateien-Tools und Weiterleitungs-Tools, die mit relativen Pfaden arbeiten (oder unter einem Unterpfad laufen, „Pfad-Präfix entfernen“ aus). Apps, die ihre Daten unter absoluten Pfaden wie `/api/...` abrufen (z. B. der Stromrechner), funktionieren über einen Freigabe-Link **nicht**, weil diese Anfragen nicht unter `/s/<token>/` laufen und deshalb zum Login führen. Für solche Apps bräuchte es eine eigene öffentliche Subdomain.

### Update einer bestehenden Installation
Die Plattform liegt jetzt komplett unter `/_platform/` (Portal-Startseite bleibt `/`), damit Tools Pfade wie `/api` oder `/assets` selbst nutzen können. Die Admin-Seite erreichst du daher unter `/_platform/admin/`.
```
git pull
# TOOLS_SECRET in .env eintragen (openssl rand -hex 32), dann:
docker compose up -d --build
```
Im NPM bei der Custom location `/` (Zahnrad) die Zeile `proxy_set_header X-Tools-Secret "..."` aus `npm/location-root.conf` mit demselben Wert ergänzen. Solange das fehlt, antwortet die Plattform überall mit 403.

### Vorschau ohne Docker
`python3 dev/dev.py` startet Portal + Admin lokal auf http://localhost:8099 mit simuliertem Login
(`DEV_GROUPS="tool-a|tool-b" python3 dev/dev.py` zum Testen normaler Nutzer; Daten in `dev/data/`).

## Sicherheit
- Alles hinter Login. Keine passende Gruppe → 403, unbekannter Pfad → 404.
- `tools-web` hat keinen veröffentlichten Port; ohne Authentik-Header liefert er nichts (401). `tools-api` hängt nur in einem internen Netz ohne Internetzugang.
- Jede Anfrage auf ein Tool wird vom Backend gegen die Gruppe geprüft; Pfad-Tricks (`..`, `%2e%2e`, `//`) werden abgelehnt.
- Admin-Änderungen verlangen JSON + passenden `Origin` (Schutz gegen CSRF).
- Admins (`ADMIN_GROUP`) sehen alle Tools. „Löschen“ entfernt ein Tool nur aus der Liste, Dateien in `sites/` bleiben liegen.
- Tool-Seiten dürfen Inline-Skripte nutzen (CSP lockerer als bei Portal/Admin), bleiben aber auf die eigene Domain beschränkt.
- Der NPM überschreibt die Identitäts-Header, Clients können sie nicht fälschen. Zusätzlich verlangt das Backend ein gemeinsames Geheimnis (`TOOLS_SECRET`), das nur der NPM kennt - so kann auch ein Container im selben Docker-Netz keine Login-Header vortäuschen. Das Geheimnis wird nie an Tools weitergegeben.
- Freigabe-Links: 256-Bit-Zufalls-Token, nur als Hash gespeichert, zeitlich begrenzt, widerrufbar, nicht indexierbar (`X-Robots-Tag`), ohne Referer an Dritte (`Referrer-Policy: same-origin`). Ohne Login ist ausschließlich `/s/<gültiger Token>/` erreichbar; alles andere antwortet weiterhin mit 401.
- Tool-Container im Netz `toolplattform_tools` können sich gegenseitig erreichen; hänge nur Dienste hinein, denen du vertraust.
- Wichtig: `tools-web` nicht in weitere Docker-Netze hängen, in denen andere Container sind, denen du nicht traust (sie könnten Header selbst setzen).
- Eine eigene Authentik-Application pro Slash geht bei Forward-Auth nicht (Authentik ordnet nach Host, nicht Pfad). Darum: eine Application + eine Gruppe pro Tool.
- Hinweis: Backend, Rechte-Logik und UI sind lokal getestet. Die nginx-/NPM-/Docker-Konfiguration wurde noch nicht in einer laufenden Umgebung getestet.
