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
3. Tab **Custom locations** → *Add location*: `/`, http, `tools-web`, `8080` → Zahnrad ⚙ → Inhalt von `npm/location-root.conf` einfügen. Speichern.

### Docker-Aufbau
| Container | Image | Aufgabe | Erreichbar |
|---|---|---|---|
| `tools-web` | `toolplattform-web` (nginx, Portal eingebaut) | liefert Portal und Tools aus, fragt `tools-api` nach den Rechten | nur über den NPM |
| `tools-api` | `toolplattform-api` (Python) | Tool-Liste, Rechte, Admin-API | nur intern, kein Internet |

Beide haben Healthchecks (`docker compose ps` zeigt `healthy`). Von außen eingehängt sind nur `./sites` (deine Tool-Dateien) und `./data` (die Tool-Liste). Nach Änderungen am Portal/nginx: `docker compose up -d --build`. Backup: `./sites` und `./data` sichern.

## Benutzung
- `https://tools.example.de/` – **Portal**: Kacheln mit allen Tools, die du nutzen darfst.
- `https://tools.example.de/admin/` – **Verwaltung** (nur Gruppe `tools-admin`): Tools anlegen, bearbeiten, entfernen und je einer Authentik-Gruppe zuordnen.

### Neues Tool
1. In `/admin/` auf **+ Neues Tool**: Slug, Name, Beschreibung, Symbol, Gruppe.
2. In Authentik die Gruppe anlegen (falls neu) und Nutzer hinzufügen.
3. Deine Dateien in `sites/<slug>/` legen (`index.html` wird beim Anlegen als Platzhalter erzeugt).
Kein Neustart nötig. Rechte-Änderungen in Authentik gelten ab dem nächsten Seitenaufruf.

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
- Der NPM überschreibt die Identitäts-Header, Clients können sie nicht fälschen.
- Wichtig: `tools-web` nicht in weitere Docker-Netze hängen, in denen andere Container sind, denen du nicht traust (sie könnten Header selbst setzen).
- Eine eigene Authentik-Application pro Slash geht bei Forward-Auth nicht (Authentik ordnet nach Host, nicht Pfad). Darum: eine Application + eine Gruppe pro Tool.
- Hinweis: Backend, Rechte-Logik und UI sind lokal getestet. Die nginx-/NPM-/Docker-Konfiguration wurde noch nicht in einer laufenden Umgebung getestet.
