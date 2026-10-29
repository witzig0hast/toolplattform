"""End-to-end-Test (vorher tests/e2e/up.sh starten): Browser -> NPM-Nachbau (:8443, echte Snippets) -> tools-web (echte nginx-Konfig) -> tools-api."""
import http.client, json, sys, time
N = ("127.0.0.1", 8443)
LOGIN = "authsess=1"
fails = 0

def req(method, path, cookie="", headers=None, body=None, host="tools.test"):
    c = http.client.HTTPConnection(*N, timeout=5)
    h = {"Host": host}
    if cookie: h["Cookie"] = cookie
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    return r.status, r.getheader("Location") or "", r.getheader("Set-Cookie") or "", data

def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   [{info}]"))
    if not cond: fails += 1

def app(data):
    try: return json.loads(data)
    except Exception: return {}

def api(method, path, body=None, extra=None):
    time.sleep(0.06)  # wie ein Mensch in der Oberfläche - nicht in die Ratenbegrenzung laufen
    h = {"Content-Type": "application/json", "Origin": "http://tools.test"}
    h.update(extra or {})
    s, _, _, d = req(method, "/_platform/api" + path, LOGIN, h, json.dumps(body) if body is not None else None)
    return s, app(d)

def is_login(status, loc):
    return status == 302 and "/outpost.goauthentik.io/start" in loc

def share(tool, **kw):
    s, d = api("POST", "/admin/shares", {"tool": tool, "expires_in": kw.pop("secs", 600), **kw})
    assert s == 201, (s, d)
    return d

print("== Gast mit Freigabe-Link auf eine App mit absoluten Pfaden (Stromrechner)")
sh = share("strom")
s, loc, ck, _ = req("GET", sh["path"])
check("Link öffnen -> Weiterleitung auf / mit Sitzungs-Cookie", s == 302 and loc == "/" and ck.startswith("tools_share="), (s, loc, ck))
check("Cookie ist HttpOnly, Secure, SameSite=Lax", all(x in ck for x in ("HttpOnly", "Secure", "SameSite=Lax")), ck)
cookie = ck.split(";")[0]
s, loc, _, d = req("GET", "/", cookie)
check("/ zeigt die App (nicht Portal, nicht Login)", s == 200 and app(d).get("app") == "Strom-App" and app(d).get("path") == "/", (s, loc, d[:80]))
s, loc, _, d = req("GET", "/api/daten?x=1", cookie)
check("App lädt /api/daten (absoluter Pfad) ohne SSO", s == 200 and app(d).get("path") == "/api/daten?x=1", (s, loc))
s, loc, _, d = req("POST", "/api/speichern", cookie, {"Content-Type": "application/json"}, "{}")
check("POST an die App funktioniert", s == 200 and app(d).get("path") == "/api/speichern", (s, loc))
s, loc, _, _ = req("GET", "/go-login", cookie)
check("Weiterleitung der App (/login) bleibt in der App", s == 302 and loc == "/login", (s, loc))
d = app(req("GET", "/", cookie)[3])
check("App sieht keinen Benutzer und kein Geheimnis", d.get("user") is None and d.get("secret") is None, d)
s, loc, _, _ = req("GET", "/_platform/api/me", cookie)
check("Gast kommt NICHT in die Plattform-API (-> Login)", is_login(s, loc), (s, loc))
s, loc, _, _ = req("GET", "/_platform/admin/", cookie)
check("Gast kommt NICHT in die Verwaltung (-> Login)", is_login(s, loc), (s, loc))
s, loc, _, _ = req("GET", "/api/daten")
check("Ohne Sitzung -> Login", is_login(s, loc), (s, loc))
s, loc, _, _ = req("GET", "/api/daten", "tools_share=gefaelschtgefaelschtgefaelscht123")
check("Gefälschtes Sitzungs-Cookie -> Login", is_login(s, loc), (s, loc))

print("== Widerruf beendet die Sitzung sofort")
s, _ = api("DELETE", "/admin/shares/" + sh["id"])
s2, loc, _, _ = req("GET", "/api/daten", cookie)
check("Nach Widerruf -> Login", s == 200 and is_login(s2, loc), (s, s2, loc))
s, loc, _, d = req("GET", sh["path"])
check("Widerrufener Link -> Seite 'Kein Zugriff' (403)", s == 403 and b"Kein Zugriff" in d, (s, loc))

print("== Ablauf")
sh = share("strom", secs=60)
ck = req("GET", sh["path"])[2].split(";")[0]
s, d = api("PUT", "/admin/shares/" + sh["id"], {"expires_at": time.time() + 61})
check("Link verlängern/ändern per API", s == 200 and d.get("expires", 0) > time.time() + 59, (s, d))

print("== Einmal-Link (max. 1 Öffnung)")
sh = share("strom", max_uses=1)
s, loc, ck, _ = req("GET", sh["path"])
a = ck.split(";")[0]
check("Gast A öffnet", s == 302 and a.startswith("tools_share="), (s, loc))
s, loc, ck2, d = req("GET", sh["path"])
check("Gast B (anderer Browser) wird abgewiesen", s == 403 and not ck2, (s, loc, ck2))
s, _, _, d = req("GET", "/api/daten", a)
check("Gast A kann weiterarbeiten", s == 200 and app(d).get("app") == "Strom-App", s)
s, loc, _, _ = req("GET", sh["path"], a)
check("Gast A darf den Link erneut öffnen", s == 302 and loc == "/", (s, loc))
lst = api("GET", "/admin/shares")[1]
mine = [x for x in lst if x["id"] == sh["id"]][0]
check("Zähler: 1 von 1 Öffnungen, Link inaktiv", mine["uses"] == 1 and mine["maxUses"] == 1 and mine["active"] is False, mine)

print("== Datei-Tool und App mit eigener Base-URL (Pfad-Modus)")
sh = share("datei-tool")
s, loc, ck, d = req("GET", sh["path"])
check("Datei-Tool lädt unter /s/<token>/", s == 200 and b"Datei-Tool" in d and ck.startswith("tools_share="), (s, loc))
s, _, _, d = req("GET", sh["path"] + "style.css", ck.split(";")[0])
check("Relative Datei (style.css) lädt", s == 200 and b"green" in d, s)
s, loc, _, _ = req("GET", sh["path"][:-1])
check("/s/<token> ohne Schrägstrich -> 302 (kein 301, wird nicht gecacht)", s == 302 and loc == sh["path"], (s, loc))
sh = share("basisapp")
s, loc, ck, d = req("GET", sh["path"] + "x")
check("App mit Base-URL bekommt /basisapp/x", s == 200 and app(d).get("path") == "/basisapp/x", (s, d[:80]))

print("== Angemeldete Nutzer (unverändert)")
s, _, _, d = req("GET", "/", LOGIN)
check("Portal", s == 200 and b"<title>Tools</title>" in d, s)
s, _, _, d = req("GET", "/strom/", LOGIN)
check("Tool über Pfad", s == 200 and app(d).get("user") == "karim", s)
s, _, _, d = req("GET", "/api/daten", LOGIN, {"Referer": "http://tools.test/strom/"})
check("Absoluter Pfad über Referer", s == 200 and app(d).get("app") == "Strom-App", s)
s, loc, _, d = req("GET", "/", LOGIN + "; " + a)
check("Angemeldet + Freigabe-Cookie -> trotzdem Portal", s == 200 and b"<title>Tools</title>" in d, s)

print("== Wartungsmodus")
tools = {t["slug"]: t for t in api("GET", "/admin/tools")[1]}
t = dict(tools["strom"]); t["enabled"] = False
for k in ("slug", "url"): t.pop(k)
s, d = api("PUT", "/admin/tools/strom", t)
check("Tool auf Wartung setzen", s == 200 and d.get("enabled") is False, (s, d))
s, _, _, d = req("GET", "/strom/", "authsess=1")
check("Admin kommt trotzdem ins Tool", s == 200 and app(d).get("app") == "Strom-App", s)
sh = share("strom", max_uses=1)
s, loc, ck, d = req("GET", sh["path"])
check("Gast mit Link bekommt Wartungsseite (503)", s == 503 and "gewartet".encode() in d and not ck, (s, ck))
t["enabled"] = True; api("PUT", "/admin/tools/strom", t)
s, loc, ck, d = req("GET", sh["path"])
check("Nach der Wartung: Einmal-Link noch nicht verbraucht", s == 302 and ck.startswith("tools_share="), (s, loc))
sh2 = share("strom"); ck = req("GET", sh2["path"])[2].split(";")[0]
t["enabled"] = False; api("PUT", "/admin/tools/strom", t)
s, _, _, d = req("GET", "/api/daten", ck)
check("Laufende Gast-Sitzung bekommt bei Wartung 503", s == 503, s)
s, _, _, d = req("GET", "/strom/", "authsess=1")
check("Nicht-Admin? (hier Admin) - Admin weiter im Tool", s == 200, s)
t["enabled"] = True; api("PUT", "/admin/tools/strom", t)

print("== Normale Nutzerin (anna, nur Gruppe tool-strom)")
ANNA = "authsess=2"
s, _, _, d = req("GET", "/strom/", ANNA)
check("anna darf Strom", s == 200 and app(d).get("user") == "anna", s)
s, _, _, d = req("GET", "/s/", ANNA)
s, _, _, d = req("GET", "/datei-tool/", ANNA)
check("anna darf Datei-Tool nicht (403)", s == 403, s)
s, _, _, d = req("GET", "/_platform/admin/", ANNA)
check("anna kommt nicht in die Verwaltung", s == 403, s)
s, _, _, d = req("GET", "/_platform/api/admin/tools", ANNA)
check("anna bekommt keine Admin-API", s == 403, s)
t["enabled"] = False; api("PUT", "/admin/tools/strom", t)
s, _, _, d = req("GET", "/strom/", ANNA)
check("anna bekommt bei Wartung die Wartungsseite", s == 503, s)
lst = json.loads(req("GET", "/_platform/api/tools", ANNA)[3])
check("Portal von anna blendet Tool in Wartung aus", all(x["slug"] != "strom" for x in lst), lst)
lst = json.loads(req("GET", "/_platform/api/tools", LOGIN)[3])
check("Admin sieht Tool in Wartung (markiert)", any(x["slug"] == "strom" and x["enabled"] is False for x in lst), lst)
t["enabled"] = True; api("PUT", "/admin/tools/strom", t)

print("== Sicherheit")
s, d = api("POST", "/admin/shares", {"tool": "strom", "expires_in": 600}, {"Sec-Fetch-Site": "cross-site"})
check("Admin-Aktion von fremder Seite (Sec-Fetch-Site) abgelehnt", s == 403, (s, d))
s, d = api("POST", "/admin/shares", {"tool": "strom", "expires_in": 600}, {"Origin": "http://evil.example"})
check("Fremder Origin abgelehnt", s == 403, (s, d))
s, d = api("POST", "/admin/tools", {"slug": "s", "name": "x", "group": "g"})
check("Kurzname 's' ist reserviert", s == 400, (s, d))
s, d = api("POST", "/admin/shares", {"tool": "strom", "expires_in": 600, "max_uses": 0})
check("max_uses=0 abgelehnt", s == 400, (s, d))
s, loc, _, _ = req("GET", "/s/../_platform/api/me")
check("Pfad-Trick /s/../_platform abgelehnt", s in (400, 403) or is_login(s, loc), (s, loc))
s, _, _, d = req("GET", "/strom/", "", {"X-authentik-username": "admin", "X-authentik-groups": "tools-admin"})
check("Gefälschte Login-Header vom Client wirken nicht", s == 302, s)
codes = [req("GET", "/s/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/")[0] for _ in range(450)]
check("Ratenbegrenzung greift bei Massen-Anfragen (429)", 429 in codes, sorted(set(codes)))


print("== Freigabe-Link mit PIN")
FORM = {"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://tools.test"}
sh = share("strom", pin="4711geheim")
s, loc, ck, d = req("GET", sh["path"])
check("Link mit PIN zeigt Abfrage statt App", s == 200 and "Geschützter Link".encode() in d and not ck, (s, d[:60]))
s, loc, ck, d = req("POST", sh["path"], "", FORM, "pin=falsch")
check("Falsche PIN wird abgewiesen", s == 403 and b"Falsche PIN" in d and not ck, s)
s, loc, ck, d = req("POST", sh["path"], "", {"Content-Type": "application/x-www-form-urlencoded", "Origin": "http://evil.example"}, "pin=4711geheim")
check("PIN-Formular von fremder Seite abgelehnt", s == 403 and not ck, s)
s, loc, ck, d = req("POST", sh["path"], "", FORM, "pin=4711geheim")
check("Richtige PIN -> Sitzung + Weiterleitung auf /", s == 303 and loc == "/" and ck.startswith("tools_share="), (s, loc))
pin_cookie = ck.split(";")[0]
s, _, _, d = req("GET", "/api/daten", pin_cookie)
check("Mit PIN-Sitzung läuft die App", s == 200 and app(d).get("app") == "Strom-App", s)
token = sh["path"].split("/")[2]
s, loc, _, _ = req("GET", "/api/daten", "tools_share=" + token)
check("Nur den Link kennen reicht nicht: selbstgebautes Cookie -> Login", is_login(s, loc), (s, loc))
s, loc, _, _ = req("GET", "/api/daten", "tools_share=" + token + ".deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
check("Gefälschte Signatur -> Login", is_login(s, loc), (s, loc))
for _ in range(5):
    req("POST", sh["path"], "", FORM, "pin=raten")
s, _, ck, d = req("POST", sh["path"], "", FORM, "pin=4711geheim")
check("Nach 5 Fehlversuchen gesperrt (auch richtige PIN)", s == 429 and not ck, s)
s, d = api("PUT", "/admin/shares/" + sh["id"], {"unlock": True})
s2, loc, ck, _ = req("POST", sh["path"], "", FORM, "pin=4711geheim")
check("Admin entsperrt -> PIN geht wieder", s == 200 and s2 == 303, (s, s2))
s, d = api("PUT", "/admin/shares/" + sh["id"], {"pin": "neuepin99"})
s2, loc, _, _ = req("GET", "/api/daten", pin_cookie)
check("Neue PIN beendet laufende Sitzungen", s == 200 and is_login(s2, loc), (s, s2, loc))
s, d = api("POST", "/admin/shares", {"tool": "strom", "expires_in": 600, "pin": "12"})
check("Zu kurze PIN abgelehnt", s == 400, (s, d))
sh = share("datei-tool", pin="abcd")
s, loc, ck, _ = req("POST", sh["path"], "", FORM, "pin=abcd")
s2, _, _, d = req("GET", sh["path"], ck.split(";")[0])
check("PIN auch für Datei-Tools (Pfad-Modus)", s == 303 and loc == sh["path"] and s2 == 200 and b"Datei-Tool" in d, (s, loc, s2))

print("== IP-Beschränkung")
sh = share("strom", ips="10.1.0.0/16, 192.168.5.7")
s, _, ck, d = req("GET", sh["path"])
check("Fremde IP -> 'Gerade nicht freigegeben'", s == 403 and b"Gerade nicht freigegeben" in d and not ck, s)
s, _, ck, d = req("GET", sh["path"], "", {"X-Real-IP": "10.1.2.3", "X-Forwarded-For": "10.1.2.3"})
check("Gefälschte IP-Header helfen nicht (NPM setzt die echte IP)", s == 403, s)
sh = share("strom", ips=["127.0.0.0/8"])
s, loc, ck, _ = req("GET", sh["path"])
check("Erlaubte IP -> Zugriff", s == 302 and ck.startswith("tools_share="), s)
s, d = api("POST", "/admin/shares", {"tool": "strom", "expires_in": 600, "ips": "999.1.1.1"})
check("Ungültige IP abgelehnt", s == 400, (s, d))

print("== Zeitfenster")
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo("Europe/Berlin"))
fmt = lambda t: t.strftime("%H:%M")
inside_win = {"days": [0, 1, 2, 3, 4, 5, 6], "from": fmt(now - timedelta(minutes=30)), "to": fmt(now + timedelta(minutes=30))}
outside_win = {"days": [0, 1, 2, 3, 4, 5, 6], "from": fmt(now + timedelta(hours=2)), "to": fmt(now + timedelta(hours=3))}
other_day = {"days": [(now.weekday() + 3) % 7], "from": "00:00", "to": "23:59"}
s, loc, ck, _ = req("GET", share("strom", schedule=inside_win)["path"])
check("Im Zeitfenster -> Zugriff", s == 302 and ck, (s, inside_win))
s, _, ck, d = req("GET", share("strom", schedule=outside_win)["path"])
check("Außerhalb des Zeitfensters -> gesperrt", s == 403 and b"Gerade nicht freigegeben" in d, (s, outside_win))
s, _, _, _ = req("GET", share("strom", schedule=other_day)["path"])
check("Anderer Wochentag -> gesperrt", s == 403, s)
s, d = api("POST", "/admin/shares", {"tool": "strom", "expires_in": 600, "schedule": {"days": [], "from": "08:00", "to": "09:00"}})
check("Zeitfenster ohne Tage abgelehnt", s == 400, (s, d))
sh = share("strom", schedule=inside_win)
ck = req("GET", sh["path"])[2].split(";")[0]
api("PUT", "/admin/shares/" + sh["id"], {"schedule": outside_win})
s, _, _, d = req("GET", "/api/daten", ck)
check("Laufende Sitzung endet, wenn das Zeitfenster vorbei ist", s == 403 and b"Gerade nicht freigegeben" in d, s)

print("== Zwei-Faktor-Pflicht")
t = {k: v for k, v in {x["slug"]: x for x in api("GET", "/admin/tools")[1]}["strom"].items() if k not in ("slug", "url")}
t["require_mfa"] = True
check("Tool auf 2FA-Pflicht setzen", api("PUT", "/admin/tools/strom", t)[0] == 200)
s, _, _, d = req("GET", "/strom/", "authsess=2")
check("Ohne 2FA -> Hinweisseite", s == 403 and b"Zwei-Faktor" in d, s)
s, _, _, d = req("GET", "/api/daten", "authsess=2", {"Referer": "http://tools.test/strom/"})
check("Ohne 2FA auch über absolute Pfade gesperrt", s == 403, s)
s, _, _, d = req("GET", "/strom/", "authsess=3")
check("Mit 2FA -> Zugriff", s == 200 and app(d).get("user") == "mona", s)
s, _, _, d = req("GET", "/strom/", "authsess=2", {"X-authentik-mfa": "1"})
check("Gefälschter 2FA-Header vom Client wirkt nicht", s == 403, s)
t["require_mfa"] = False; api("PUT", "/admin/tools/strom", t)

print("== Nutzung, zuletzt benutzt, Favoriten")
NAV = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
for _ in range(3):
    req("GET", "/strom/", "authsess=2", NAV)
req("GET", "/strom/", "authsess=2", {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})  # API-Anfrage: zählt nicht
req("GET", "/basisapp/x", "authsess=2", NAV)
s, st = api("GET", "/admin/stats?days=30")
strom = st.get("tools", {}).get("strom", {})
check("Statistik zählt nur Seitenaufrufe", s == 200 and strom.get("total") == 3 and strom.get("uniqueUsers") == 1, strom)
check("Statistik nennt Top-Nutzer", strom.get("topUsers", [{}])[0].get("user") == "anna", strom.get("topUsers"))
me = json.loads(req("GET", "/_platform/api/me", "authsess=2")[3])
check("'Zuletzt benutzt' für anna", me.get("recent", [])[:2] == ["basisapp", "strom"], me.get("recent"))
s, _, _, d = req("PUT", "/_platform/api/me/favorites", "authsess=2", {"Content-Type": "application/json", "Origin": "http://tools.test"}, json.dumps({"favorites": ["strom"]}))
me = json.loads(req("GET", "/_platform/api/me", "authsess=2")[3])
check("Favoriten speichern", s == 200 and me.get("favorites") == ["strom"], (s, me.get("favorites")))
s, _, _, _ = req("PUT", "/_platform/api/me/favorites", "authsess=2", {"Content-Type": "application/json", "Origin": "http://tools.test"}, json.dumps({"favorites": ["../x"]}))
check("Ungültige Favoriten abgelehnt", s == 400, s)
me = json.loads(req("GET", "/_platform/api/me", LOGIN)[3])
check("Favoriten sind pro Person", me.get("favorites") == [], me.get("favorites"))

print("== Import")
s, exp = api("GET", "/admin/export")
tools_in = exp["tools"]
tools_in["datei-tool"]["name"] = "Datei (importiert)"
tools_in["neu-importiert"] = {"name": "Neu", "group": "g", "type": "static"}
s, d = api("POST", "/admin/import", {"tools": tools_in, "mode": "merge"})
names = {t["slug"]: t["name"] for t in api("GET", "/admin/tools")[1]}
check("Import (zusammenführen)", s == 200 and d.get("added") == 1 and names.get("datei-tool") == "Datei (importiert)", (s, d))
s, d = api("POST", "/admin/import", {"tools": {"../böse": {"name": "x", "group": "g"}}})
check("Import mit ungültigem Kurznamen abgelehnt", s == 400, (s, d))
s, d = api("POST", "/admin/import", {"tools": {"x": {"name": "x", "group": "g", "type": "proxy", "upstream": "http://127.0.0.1:22"}}})
check("Import prüft Ziel-Adressen (Loopback gesperrt)", s == 400, (s, d))

print("== Datei-Upload")
import io, zipfile
UP = {"Content-Type": "application/octet-stream", "Origin": "http://tools.test"}
def upload(path, data, unzip=False, tool="neu-importiert", extra=None):
    h = dict(UP); h.update(extra or {})
    s, _, _, d = req("POST", f"/_platform/api/admin/files/{tool}?path={path}" + ("&unzip=1" if unzip else ""), LOGIN, h, data)
    return s, app(d)
s, d = upload("index.html", b"<h1>Hochgeladen</h1>")
s2, _, _, d2 = req("GET", "/neu-importiert/", LOGIN)
check("Datei hochladen und ausliefern", s == 201 and b"Hochgeladen" in d2, (s, d, s2))
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("meine-seite/index.html", "<h1>Aus ZIP</h1>")
    z.writestr("meine-seite/css/app.css", "body{}")
    z.writestr("meine-seite/.env", "GEHEIM=1")
    z.writestr("__MACOSX/meine-seite/._index.html", "x")
s, d = upload("site.zip", buf.getvalue(), unzip=True)
s2, _, _, d2 = req("GET", "/neu-importiert/css/app.css", LOGIN)
check("ZIP entpacken (Oberordner wird weggelassen)", s == 201 and d.get("written") == 2 and s2 == 200, (s, d, s2))
files_now = [f["path"] for f in api("GET", "/admin/files/neu-importiert")[1]["files"]]
check("Versteckte Dateien aus dem ZIP werden übersprungen", ".env" not in files_now and "css/app.css" in files_now, files_now)
evil = io.BytesIO()
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("ok.html", "x")
    z.writestr("../../ausgebrochen.txt", "x")
s, d = upload("evil.zip", evil.getvalue(), unzip=True)
check("ZIP mit ../ (Zip-Slip) abgelehnt", s == 400, (s, d))
s, d = upload("../ausbruch.html", b"x")
check("Pfad mit .. abgelehnt", s == 400, (s, d))
s, d = upload(".htaccess", b"x")
check("Versteckte Datei abgelehnt", s == 400, (s, d))
s, d = upload("index.html", b"x", tool="strom")
check("Upload nur bei Dateien-Tools", s == 400, (s, d))
s, d = upload("a.html", b"x", extra={"Origin": "http://evil.example"})
check("Upload von fremder Seite abgelehnt", s == 403, (s, d))
s, _, _, d = req("DELETE", "/_platform/api/admin/files/neu-importiert?path=css", LOGIN, {"Origin": "http://tools.test"})
s2, _, _, _ = req("GET", "/neu-importiert/css/app.css", LOGIN)
check("Ordner löschen", s == 200 and s2 == 404, (s, s2))
s, _, _, d = req("GET", "/_platform/api/admin/files/neu-importiert", "authsess=2")
check("Nicht-Admin bekommt keine Dateiliste", s == 403, s)

print("== Überwachung und Benachrichtigungen")
import os, signal, subprocess, time as _t
def health():
    return api("GET", "/admin/health")[1].get("tools", {})
deadline = _t.time() + 15
while _t.time() < deadline and health().get("strom", {}).get("state") != "up":
    _t.sleep(1)
check("Monitor meldet Strom als erreichbar", health().get("strom", {}).get("state") == "up", health().get("strom"))
pids = [p for p in os.listdir("/proc") if p.isdigit() and "mockapp.py" in open(f"/proc/{p}/cmdline", "rb").read().decode(errors="ignore") and "10.99.0.3" in open(f"/proc/{p}/cmdline", "rb").read().decode(errors="ignore")]
for p in pids:
    os.kill(int(p), signal.SIGTERM)
T = os.environ.get("E2E_DIR", "/tmp/toolplattform-e2e")
deadline = _t.time() + 20
while _t.time() < deadline and health().get("strom", {}).get("state") != "down":
    _t.sleep(1)
check("Ausfall wird erkannt", health().get("strom", {}).get("state") == "down", health().get("strom"))
_t.sleep(1)
log = open(T + "/notifications.log", encoding="utf-8").read() if os.path.exists(T + "/notifications.log") else ""
check("Benachrichtigung über Ausfall verschickt", "nicht erreichbar" in log and "Strom" in log, log[-200:])
tl = json.loads(req("GET", "/_platform/api/tools", LOGIN)[3])
check("Portal zeigt den Ausfall", any(x["slug"] == "strom" and x["health"] == "down" for x in tl), [(x["slug"], x["health"]) for x in tl])
subprocess.Popen(["python3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mockapp.py"), "10.99.0.3", "Strom-App"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
deadline = _t.time() + 20
while _t.time() < deadline and health().get("strom", {}).get("state") != "up":
    _t.sleep(1)
_t.sleep(1)
log = open(T + "/notifications.log", encoding="utf-8").read()
check("Wiederherstellung erkannt und gemeldet", health().get("strom", {}).get("state") == "up" and "wieder erreichbar" in log, log[-200:])
s, _ = api("POST", "/admin/notify-test", {})
deadline = _t.time() + 10
while _t.time() < deadline and not (api("GET", "/admin/health")[1].get("test") or {}).get("results"):
    _t.sleep(1)
res = api("GET", "/admin/health")[1].get("test") or {}
check("Test-Benachrichtigung", s == 202 and res.get("results", [{}])[0].get("ok") is True and "Test-Benachrichtigung" in open(T + "/notifications.log", encoding="utf-8").read(), res)

print("== Protokoll")
s, log = api("GET", "/admin/audit")
acts = {e["action"] for e in log}
check("Protokoll enthält Aktionen", s == 200 and {"share.create", "share.open", "share.revoke", "share.blocked", "tool.update",
      "share.pin_failed", "config.import", "files.upload", "files.delete", "notify.test"} <= acts, acts)
s, d = api("GET", "/admin/export")
check("Export liefert Tool-Liste", s == 200 and "strom" in d.get("tools", {}), s)

print(f"\n{'ALLES OK' if not fails else str(fails) + ' FEHLER'}")
sys.exit(1 if fails else 0)
