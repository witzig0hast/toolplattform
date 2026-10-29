# Tools-Plattform (Nginx Proxy Manager + Authentik)

`tools.example.de` ist komplett hinter deinem Authentik-Login. Jedes Tool läuft unter einem eigenen Pfad (`/home-assistant-fix/`) oder einer eigenen Subdomain (`proxy-settings.tools.example.de`) und ist nur für Mitglieder einer bestimmten Authentik-Gruppe sichtbar. Admins können Tools zeitlich begrenzt ohne Login teilen (optional mit PIN, IP-Beschränkung und Zeitfenster).

**Funktionen im Überblick:** Portal mit Kategorien, Favoriten, „Zuletzt benutzt“ und Suche · Tools als Dateien (mit Upload/ZIP) oder Weiterleitung an Container · Pfad oder eigene Subdomain · Freigabe-Links (Ablauf, Einmal-Links, PIN, IP, Zeitfenster) · Wartungsmodus · 2FA-Pflicht pro Tool · Überwachung mit Benachrichtigung (ntfy, Telegram, Webhook) · Nutzungsstatistik · Protokoll · Export/Import.

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
cp .env.example .env     # NPM_NETWORK, TOOLS_DOMAIN, TOOLS_UID/TOOLS_GID, NOTIFY_… anpassen (Hinweise stehen in der Datei)
openssl rand -hex 32     # erzeugt ein Geheimnis -> in .env bei TOOLS_SECRET eintragen
docker compose up -d --build   # legt data/ und sites/ selbst mit den richtigen Rechten an
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
| `tools-api` | `toolplattform-api` (Python, ohne Fremdpakete) | Tool-Liste, Rechte, Freigabe-Links, Protokoll, Statistik, Datei-Upload, Admin-API | nur intern, kein Internet |
| `tools-monitor` | `toolplattform-api` | prüft die Weiterleitungs-Ziele, schickt Benachrichtigungen; liest die Tool-Liste nur, kennt das Geheimnis nicht | erreicht nur Tool-Container und das Internet, nicht das Backend |

Beide haben Healthchecks (`docker compose ps` zeigt `healthy`) und laufen read-only ohne Zusatzrechte. Von außen eingehängt sind nur `./sites` (Tool-Dateien) und `./data` (Tool-Liste, Freigabe-Links, Protokoll, Statistik, Favoriten, Status). **Backup:** `./sites` und `./data` sichern.

## Benutzung
- `https://tools.example.de/` – **Portal**: deine Tools als Kacheln, nach Kategorien gruppiert, mit Suche. Immer erreichbar unter `https://tools.example.de/_platform/portal` (siehe App-Modus).
- `https://tools.example.de/` – Stern ☆ an einer Kachel = **Favorit** (pro Person). „Zuletzt benutzt“ erscheint automatisch.
- `https://tools.example.de/_platform/admin/` – **Verwaltung** (nur `tools-admin`), oben mit Sprungleiste: Tools · Freigaben · Nutzung · Überwachung · Protokoll.

### Neues Tool
In der Verwaltung **+ Neues Tool**:
- **Kurzname** (Teil der Adresse), **Name**, **Beschreibung**, **Kategorie**, **Symbol**, **Authentik-Gruppe**
- **Art**: *Dateien* (Webseite in `sites/<kurzname>/`) oder *Weiterleitung* an einen laufenden Dienst
- **Aufruf über**: *Pfad* (`tools.example.de/<kurzname>/`) oder *eigene Subdomain* (`<kurzname>.tools.example.de`)
- **Wartungsmodus**: sperrt das Tool vorübergehend (Nutzer und Gäste sehen eine Wartungsseite, Admins kommen weiter hinein)
- **Zwei-Faktor-Anmeldung erforderlich**: nur wer mit zweitem Faktor angemeldet ist, kommt hinein – auch Admins (Einrichtung in Authentik siehe unten)

### Dateien hochladen
Bei Dateien-Tools gibt es den Knopf **Dateien**: Dateien auswählen (mehrere gehen), optional einen Unterordner angeben, **Hochladen**. ZIP-Dateien werden auf Wunsch entpackt (ein gemeinsamer Oberordner im ZIP wird weggelassen, vorhandene Dateien überschrieben). Grenzen: 100 MB pro Datei/ZIP, entpackt 500 MB, 5000 Dateien. Versteckte Dateien (`.env`, `.git` …) werden nie angenommen, ZIPs mit Ausbruchsversuchen (`../`) komplett abgelehnt. In der Liste kannst du Dateien öffnen und löschen.

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

- **App läuft auf der Hauptadresse** (Standard an, „App-Modus“): Wer das Tool öffnet (Kachel, Link oder Lesezeichen auf `/<kurzname>/…`), dessen Browser merkt sich „aktive App“ und wechselt auf die Hauptadresse (`/<kurzname>/settings` → `/settings`). Ab dann gehören alle Pfade dieser App – wie auf einer eigenen Domain. Damit funktionieren absolute Links, Single-Page-Apps mit eigenem Router (z. B. Nginx Proxy Manager), Neuladen, „Home“, Zurück-Taste und Lesezeichen.
  - **Zurück zum Portal:** der kleine blaue Knopf unten links in jeder App (abschaltbar pro Tool unter „Zurück-Knopf zum Portal einblenden“), oder `https://tools.example.de/_platform/portal`. Damit verlässt du die App; danach zeigt auch `/` wieder das Portal. Der Knopf wird nur für angemeldete Nutzer in HTML-Seiten eingeblendet (nicht für Gäste mit Freigabe-Link); er steckt in einem eigenen Shadow-DOM und beeinflusst die App nicht.
  - Solange du eine App benutzt, zeigt `https://tools.example.de/` diese App (auch beim Neuladen). Nach 1 Stunde ohne Nutzung endet die App-Sitzung von selbst.
  - **Eine App pro Browser gleichzeitig.** Öffnest du eine zweite App (Kachel), wechselt die aktive App – ein anderer Tab mit der ersten App funktioniert dann nicht mehr richtig. Für mehrere Apps parallel: „Eigene Subdomain“.
  - Ausschalten nur, wenn die App selbst für den Unterpfad `/<kurzname>/` eingerichtet ist (Base-URL); dann läuft sie unter `/<kurzname>/`.
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
- **Apps, die auf „/“ laufen** (Weiterleitung mit „App läuft auf der Hauptadresse“ oder eigene Subdomain): Der Gast wird auf `/` geschickt und nutzt die App dort wie auf einer eigenen Domain. Damit funktionieren auch Apps mit absoluten Pfaden (`/api/...`) und Single-Page-Apps.
- **Dateien-Tools und Apps mit eigener Base-URL**: laufen unter `/s/<token>/`.

Zusätzliche Absicherung pro Link (alles optional, auch nachträglich über **Ändern**):
- **PIN oder Passwort** (4–64 Zeichen): Gäste sehen erst eine Eingabeseite. Nach 5 Fehlversuchen ist der Link 10 Minuten gesperrt (in der Liste „gesperrt“, mit **Entsperren**). Eine neue PIN beendet alle laufenden Sitzungen. Schicke die PIN getrennt vom Link.
- **Nur von bestimmten IP-Adressen** (z. B. `203.0.113.7, 192.168.1.0/24`). Die IP kommt vom NPM; steht vor dem NPM noch ein weiterer Proxy (z. B. Cloudflare), siehst du dessen IP.
- **Nur zu bestimmten Zeiten**: Wochentage + Uhrzeit (Zeitzone aus `TZ`, Standard Europe/Berlin), auch über Mitternacht (22:00–06:00). Außerhalb ist der Link gesperrt, auch für Gäste mitten in der Sitzung.

Wichtig:
- Der Link wird **nur einmal angezeigt**, gespeichert ist nur ein Hash.
- Wer den Link hat, kann alles, was das Tool erlaubt. Teile nur Tools, die für Fremde unbedenklich sind.
- Zum Ausprobieren ein **privates Fenster** nehmen: Bist du angemeldet, siehst du auf `/` dein Portal, nicht die geteilte App.
- Ein Gast hat immer nur eine Freigabe-Sitzung; ein zweiter Link im selben Browser ersetzt die erste.

### Überwachung und Benachrichtigungen
Der Container `tools-monitor` prüft alle 60 Sekunden (`HEALTH_INTERVAL`), ob das Ziel jedes Weiterleitungs-Tools antwortet (jede HTTP-Antwort unter 500 zählt als erreichbar). Nach 2 Fehlversuchen in Folge (`HEALTH_FAILS`) gilt es als ausgefallen.
- Die Verwaltung zeigt unter **Überwachung** Status, seit wann und Antwortzeit; in der Tool-Liste steht ein grüner/roter Punkt. Im Portal steht bei ausgefallenen Tools „Gerade nicht erreichbar“.
- Bei Ausfall und Wiederherstellung kommt eine Nachricht – einrichten in der `.env`, dann `docker compose up -d`:
  - **ntfy** (Handy-App): `NOTIFY_NTFY_URL=https://ntfy.sh/ein-langes-geheimes-thema` (oder eigener Server, ggf. `NOTIFY_NTFY_TOKEN`)
  - **Telegram**: Bot bei @BotFather anlegen → `NOTIFY_TELEGRAM_TOKEN`; Chat-ID (z. B. über @userinfobot) → `NOTIFY_TELEGRAM_CHAT`
  - **Webhook**: `NOTIFY_WEBHOOK_URL` bekommt `{"title","message","text"}` (z. B. für Home Assistant oder Discord)
- **Test-Benachrichtigung** in der Verwaltung zeigt sofort, ob alles ankommt.
- Dateien-Tools werden nicht überwacht (sie liegen auf derselben Plattform).

### Nutzung
**Nutzung** zeigt pro Tool die Aufrufe (7/30/90 Tage) als Verlauf, wie viele Personen es nutzen und wer am meisten. Gezählt werden nur echte Seitenaufrufe angemeldeter Nutzer, keine Bilder, Skripte oder API-Anfragen und keine Gäste. Gespeichert werden 90 Tage (`data/stats.json`).

### Protokoll, Export und Import
Das **Protokoll** (`data/audit.log`, rotiert bei 1 MB) zeigt angelegte/geänderte/entfernte Tools, Freigabe-Links (erstellt, geändert, widerrufen, geöffnet mit IP, abgewiesen, falsche PIN), Datei-Uploads, Importe und Test-Benachrichtigungen.
- **Exportieren** lädt die Tool-Liste als JSON herunter.
- **Importieren** liest eine solche Datei wieder ein: neue Tools kommen dazu, gleichnamige werden überschrieben, alle anderen bleiben. Vor dem Import kommt eine Rückfrage mit Anzahl; jeder Eintrag wird wie beim Anlegen geprüft. (Die Dateien in `sites/` sind nicht Teil des Exports – die sicherst du separat.)

### Zwei-Faktor-Pflicht (Authentik einrichten)
Die Plattform lässt Tools mit 2FA-Pflicht nur zu, wenn Authentik den Header `X-Authentik-Mfa: 1` mitschickt. Den gibt es nicht von selbst – so richtest du ihn ein (**ungetestet**, da die Authentik-Doku hier nicht erreichbar war; bitte einmal ausprobieren):
1. Authentik → *Customization → Property Mappings → Create → Scope Mapping*, Name `tools-mfa`, Scope-Name `ak_proxy`, Ausdruck:
   ```python
   from authentik.stages.authenticator import devices_for_user
   has_mfa = any(True for _ in devices_for_user(request.user))
   return {"ak_proxy": {"user_attributes": {"additionalHeaders": {"X-Authentik-Mfa": "1" if has_mfa else "0"}}}}
   ```
2. Provider `tools` → *Advanced protocol settings → Scopes*: `tools-mfa` zusätzlich auswählen.
3. Im Anmelde-Flow sollte der 2FA-Schritt Pflicht sein, sobald jemand einen zweiten Faktor eingerichtet hat (Standard bei Authentik). Dann bedeutet „2FA eingerichtet“ auch „mit 2FA angemeldet“.
4. Ab- und wieder anmelden (die Werte werden beim Login übernommen). Ob es klappt, siehst du unter `https://tools.example.de/_platform/api/me` (`"mfa": true`).

## Update einer bestehenden Installation
```
git pull
# .env um die neuen Einträge aus .env.example ergänzen (TZ, HEALTH_…, NOTIFY_…)
docker compose up -d --build
```
Tipp: `sh npm/ausgeben.sh 192.168.1.20:9000` (Adresse deines Authentik) gibt alle drei NPM-Texte fertig ausgefüllt aus – mit dem Geheimnis aus der `.env`. Dann nur noch kopieren und einfügen.

**Die NPM-Snippets haben sich geändert** – alle drei neu einfügen (jeweils `DEIN_TOOLS_SECRET` ersetzen):
1. Tab *Advanced*: `npm/advanced.conf` (u. a. `recursive_error_pages on;` und der Block `@tools_maybe_share`).
2. Custom location `/`: `npm/location-root.conf` (u. a. `error_page 401 = @tools_maybe_share;` und weitere Authentik-Header inkl. 2FA).
3. Custom location `/s/`: `npm/location-share.conf` (anlegen, falls noch nicht vorhanden).
4. Einen Wildcard-Host `*.tools…` ebenso (dort nur Advanced + `/`, Port 8082).

Bestehende Freigabe-Links bleiben gültig; Gäste müssen den Link nach dem Update einmal neu öffnen (die Sitzungen sind jetzt signiert).

## Sicherheit
- **Login**: Alles außer Freigabe-Links liegt hinter Authentik. Keine passende Gruppe → „Kein Zugriff“ (403).
- **Gemeinsames Geheimnis** (`TOOLS_SECRET`): Das Backend glaubt Login-Headern nur, wenn der NPM das Geheimnis mitschickt. Ein Container im selben Docker-Netz kann so keine Identität vortäuschen. Vom Client mitgeschickte Login-Header werden im NPM überschrieben bzw. entfernt. Das Geheimnis geht nie an Tools.
- **Netze**: `tools-web` ohne veröffentlichten Port; `tools-api` nur im internen Netz ohne Internet. Container read-only, ohne Capabilities, `no-new-privileges`.
- **Pfade**: Jede Anfrage wird vom Backend geprüft; Tricks wie `..`, `%2e%2e`, `%2f`, `//` werden abgelehnt.
- **Freigabe-Links**: 256-Bit-Zufalls-Token, nur als Hash gespeichert (Datei `0600`), Ablauf, Limits, IP und Zeitfenster werden bei jeder Anfrage geprüft, Widerruf wirkt sofort. Sitzungs-Cookie `HttpOnly; Secure; SameSite=Lax` und **signiert** (HMAC): Wer nur den Link kennt, kann die PIN nicht umgehen. PIN als PBKDF2-Hash (200 000 Runden) mit Sperre nach 5 Fehlversuchen. Gäste kommen nie in Portal, Verwaltung oder Plattform-API. Nicht indexierbar (`X-Robots-Tag`), Token geht nicht per Referer an Dritte (`Referrer-Policy: same-origin`).
- **Hinter Cloudflare**: `npm/advanced.conf` übernimmt die echte Besucher-IP aus `CF-Connecting-IP` (für Protokoll, IP-Beschränkung und Ratenbegrenzung). Prüfen: Freigabe-Link öffnen, im Protokoll muss deine echte IP stehen, keine Cloudflare-IP (`104.x`, `172.64–71.x`, `162.158.x` …).
- **Ratenbegrenzung**: Gäste 50 Anfragen/s (Burst 300) pro IP, Plattform-API 20/s (Burst 80) → sonst 429.
- **Admin-Aktionen**: nur JSON, `Origin` muss passen, `Sec-Fetch-Site` muss `same-origin` sein (Schutz gegen CSRF). Alles wird protokolliert.
- **Header**: HSTS, CSP (Portal/Verwaltung streng, ohne Inline-Skripte), `X-Frame-Options: DENY`, `nosniff`, COOP/CORP, `Permissions-Policy`, `Cache-Control: no-store`.
- **Ziel-Adressen**: Loopback, Link-Local und die Plattform-Container sind als Weiterleitungsziel gesperrt. Nur Admins können Ziele anlegen – auch per Import wird jeder Eintrag geprüft.
- **Datei-Upload**: nur für Admins, nur in den Ordner des jeweiligen Tools; jeder Pfad wird nach dem Auflösen geprüft; keine versteckten Dateien, keine Symlinks, Schutz gegen Zip-Slip und ZIP-Bomben (gezählte Bytes statt Angaben im ZIP).
- **2FA-Pflicht**: Der 2FA-Header kommt ausschließlich aus der Authentik-Antwort; vom Browser mitgeschickte Werte überschreibt bzw. entfernt der NPM.
- **Überwachung**: eigener Container ohne Geheimnis und ohne Schreibrecht auf die Tool-Liste; erreicht das Backend nicht. Telegram-Token taucht in Fehlermeldungen nicht auf.
- Tool-Container im Netz `toolplattform_tools` können sich gegenseitig erreichen; hänge nur Dienste hinein, denen du vertraust. `tools-web` nicht in weitere fremde Netze hängen.
- Weiterleitungen der Plattform sind `302` (nicht `301`), damit Browser sie nicht dauerhaft speichern.

## Tests
Ende-zu-Ende-Tests mit echtem nginx (100 Prüfungen): nachgebauter NPM (mit den echten Snippets aus `npm/`), nachgebauter Authentik-Outpost, `tools-web` mit der echten Konfiguration, `tools-api`, `tools-monitor`, Test-Apps und ein Webhook-Empfänger. Geprüft werden u. a. Login, Gruppen, Freigabe-Links (Sitzung, Einmal-Link, PIN inkl. Sperre und gefälschter Cookies, IP, Zeitfenster, Widerruf), Wartung, 2FA-Pflicht, Statistik, Favoriten, Import, Datei-Upload (inkl. Zip-Slip), Überwachung mit Ausfall- und Wiederherstellungsmeldung, Pfad-Tricks, gefälschte Header, CSRF, Ratenbegrenzung und Protokoll.
```
sudo tests/e2e/up.sh && python3 tests/e2e/test.py; sudo tests/e2e/down.sh
```
Braucht `nginx` und `python3` (legt Test-IPs `10.99.0.2-4` auf `lo` an).

Ohne Docker/nginx gibt es eine schnelle UI-Vorschau mit simuliertem Login: `python3 dev/dev.py` → http://localhost:8099 (`DEV_GROUPS="tool-a|tool-b"` für normale Nutzer).

## Grenzen
- Eine eigene Authentik-*Application* pro Tool geht bei Forward-Auth nicht (Authentik ordnet nach Host). Darum: eine Application + eine Gruppe pro Tool.
- Freigabe-Links gibt es nur über die Haupt-Domain, nicht über Tool-Subdomains (dort ist ohnehin alles hinter Login).
- Nicht getestet: der echte Nginx Proxy Manager, der echte Authentik-Outpost (inkl. 2FA-Mapping), das Wildcard-Zertifikat und echte ntfy-/Telegram-Zustellung. Die Tests nutzen Nachbauten mit denselben Snippets.
- Freigabe-Links: Apps, die einen eingeloggten Benutzer erwarten (über die `X-Authentik-*`-Header), sehen beim Gast keinen.
