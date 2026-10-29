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

print("== Protokoll")
s, log = api("GET", "/admin/audit")
acts = {e["action"] for e in log}
check("Protokoll enthält Aktionen", s == 200 and {"share.create", "share.open", "share.revoke", "share.blocked", "tool.update"} <= acts, acts)
s, d = api("GET", "/admin/export")
check("Export liefert Tool-Liste", s == 200 and "strom" in d.get("tools", {}), s)

print(f"\n{'ALLES OK' if not fails else str(fails) + ' FEHLER'}")
sys.exit(1 if fails else 0)
