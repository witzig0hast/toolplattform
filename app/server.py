"""Kleiner Backend-Dienst der Tools-Plattform (nur Python-Standardbibliothek).

- Verwaltet die Tool-Liste (data/tools.json) und Freigabe-Links (data/shares.json)
- Entscheidet für nginx (auth_request), wer welches Tool sehen darf und wohin die Anfrage geht
- Liefert die API für Portal- und Admin-Seite
- Schreibt ein Protokoll (data/audit.log) über Admin-Aktionen und geöffnete Freigabe-Links

Vertraut den Headern X-Authentik-Username / X-Authentik-Groups nur zusammen mit dem gemeinsamen
Geheimnis (TOOLS_SECRET), das nur der Nginx Proxy Manager kennt. Darf ausschließlich über den
tools-web-Container erreichbar sein (siehe docker-compose.yml).
"""
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

DATA_FILE = Path(os.environ.get("TOOLS_DATA", "/data/tools.json"))
SITES_DIR = Path(os.environ.get("TOOLS_SITES", "/srv/sites"))
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "tools-admin")
# Basis-Domain für Tools mit eigener Subdomain (<slug>.TOOLS_DOMAIN), z. B. tools.example.de
TOOLS_DOMAIN = os.environ.get("TOOLS_DOMAIN", "").strip().strip(".").lower()
# Gemeinsames Geheimnis zwischen NPM und tools-web: ohne es gelten Authentik-Header nicht
TOOLS_SECRET = os.environ.get("TOOLS_SECRET", "")
# Interner nginx-Server (nur im Container erreichbar), der die statischen Tool-Dateien ausliefert
STATIC_UPSTREAM = "http://127.0.0.1:8081"
MAINTENANCE_TARGET = STATIC_UPSTREAM + "/_maintenance"
COOKIE_NAME = "tools_last"      # zuletzt genutztes Tool (angemeldete Nutzer, absolute Pfade)
SHARE_COOKIE = "tools_share"    # Freigabe-Sitzung (Gäste ohne Login)
NO_REDIRECT = ("/_none_/", "/_none_/")  # Platzhalter: nginx proxy_redirect ohne Wirkung

# Öffentliche Freigabe-Links (/s/<token>/...)
SHARES_FILE = DATA_FILE.parent / "shares.json"
SHARE_MIN_SECONDS = 60
SHARE_MAX_SECONDS = 365 * 24 * 3600
SHARE_MAX_ACTIVE = 200
SHARE_MAX_USES = 100000
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")

# Protokoll
AUDIT_FILE = DATA_FILE.parent / "audit.log"
AUDIT_MAX_BYTES = 1024 * 1024

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RESERVED_SLUGS = {"api", "admin", "assets", "s"}
MAX_BODY = 8 * 1024
_lock = threading.Lock()


# ---------- Speicher ----------
def _load_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600 if path == SHARES_FILE else 0o644)
    os.replace(tmp, path)


def load_tools():
    return _load_json(DATA_FILE)


def save_tools(tools):
    _save_json(DATA_FILE, tools)


def load_shares():
    return _load_json(SHARES_FILE)


def save_shares(shares):
    _save_json(SHARES_FILE, shares)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- Protokoll ----------
_audit_lock = threading.Lock()


def audit(action, actor, **details):
    """Eine Zeile JSON ins Protokoll schreiben (rotiert bei 1 MB nach audit.log.1)."""
    entry = {"ts": round(time.time(), 3), "action": action, "actor": actor or "", **details}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _audit_lock:
        try:
            AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > AUDIT_MAX_BYTES:
                os.replace(AUDIT_FILE, AUDIT_FILE.with_name(AUDIT_FILE.name + ".1"))
            fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # Protokoll darf den Betrieb nie blockieren


def read_audit(limit=200):
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------- Eingaben prüfen ----------
def clean_text(value, field, max_len, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{field}: Text erwartet")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} darf nicht leer sein")
    if len(value) > max_len:
        raise ValueError(f"{field} ist zu lang (max. {max_len} Zeichen)")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"{field} enthält ungültige Zeichen")
    return value


UPSTREAM_RE = re.compile(r"^https?://([A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(:([0-9]{1,5}))?$")
BLOCKED_HOSTS = {"localhost", "tools-web", "tools-api", "metadata.google.internal"}


def validate_upstream(value):
    value = clean_text(value, "Ziel-Adresse", 255, required=True)
    m = UPSTREAM_RE.match(value)
    if not m:
        raise ValueError("Ziel-Adresse muss so aussehen: http://name-oder-ip:port (ohne Pfad)")
    host, port = m.group(1).lower(), m.group(4)
    if port and not 1 <= int(port) <= 65535:
        raise ValueError("Ungültiger Port")
    if host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise ValueError("Diese Ziel-Adresse ist nicht erlaubt")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return value  # Hostname, kein IP-Literal
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError("Diese Ziel-Adresse ist nicht erlaubt")
    return value


def validate_bool(body, key, default):
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} muss true oder false sein")
    return value


def validate_tool(body):
    group = clean_text(body.get("group", ""), "Gruppe", 64, required=True)
    if "|" in group:
        raise ValueError("Gruppe darf kein '|' enthalten")
    kind = body.get("type", "static")
    if kind not in ("static", "proxy"):
        raise ValueError("Typ muss 'static' oder 'proxy' sein")
    tool = {
        "name": clean_text(body.get("name", ""), "Name", 60, required=True),
        "description": clean_text(body.get("description", ""), "Beschreibung", 200),
        "icon": clean_text(body.get("icon", ""), "Symbol", 8),
        "category": clean_text(body.get("category", ""), "Kategorie", 40),
        "group": group,
        "type": kind,
        "access": body.get("access", "path"),
        "enabled": validate_bool(body, "enabled", True),
    }
    if tool["access"] not in ("path", "subdomain"):
        raise ValueError("Aufruf muss 'path' oder 'subdomain' sein")
    if tool["access"] == "subdomain" and not TOOLS_DOMAIN:
        raise ValueError("Für Subdomains muss TOOLS_DOMAIN in der .env gesetzt sein")
    if kind == "proxy":
        tool["upstream"] = validate_upstream(body.get("upstream"))
        tool["strip_prefix"] = validate_bool(body, "strip_prefix", True)
    return tool


def validate_expiry(body, now):
    """Ablaufzeitpunkt aus expires_in (Sekunden) oder expires_at (Unix-Sekunden)."""
    if "expires_at" in body:
        at = body["expires_at"]
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            raise ValueError("Ungültiger Ablaufzeitpunkt")
        seconds, expires = int(at - now), float(at)
    else:
        seconds = body.get("expires_in")
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            raise ValueError("Ungültige Gültigkeit")
        expires = now + seconds
    if not SHARE_MIN_SECONDS <= seconds <= SHARE_MAX_SECONDS:
        raise ValueError("Gültigkeit muss zwischen 1 Minute und 365 Tagen liegen")
    return expires


def validate_max_uses(body):
    value = body.get("max_uses")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= SHARE_MAX_USES:
        raise ValueError("Maximale Öffnungen: leer lassen oder eine Zahl ab 1")
    return value


# ---------- Rechte & Ziele ----------
def is_admin(groups):
    return ADMIN_GROUP in groups


def can_see(tool, groups):
    return is_admin(groups) or tool["group"] in groups


def parse_cookie(header, name):
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def clean_uri(raw_uri):
    """Rohe URL (Pfad + Query) prüfen. Rückgabe (raw_path, suffix, segments) oder None bei unsauberen Pfaden."""
    if not raw_uri.startswith("/") or "#" in raw_uri:
        return None
    raw_path, _, query = raw_uri.partition("?")
    # Kodierte Schrägstriche/Backslashes/Null-Bytes nie zulassen (Segmentierung wäre uneindeutig)
    if re.search(r"%2f|%5c|%00", raw_path, re.I):
        return None
    path = unquote(raw_path)
    segments = path.split("/")
    # Pfad muss sauber sein: kein '..', '.', '//' oder Null-Byte (sonst Umgehung via Normalisierung)
    if "\0" in path or "\\" in path:
        return None
    if any(seg in ("", ".", "..") for seg in segments[1:-1]) or segments[-1] in (".", ".."):
        return None
    return raw_path, ("?" + query) if query else "", segments


def tool_url(slug, tool):
    if tool.get("access") == "subdomain" and TOOLS_DOMAIN:
        return f"https://{slug}.{TOOLS_DOMAIN}/"
    return f"/{slug}/"


def result(target, redir=NO_REDIRECT, cookie=""):
    return {"target": target, "redir": redir, "cookie": cookie}


def maintenance():
    return result(MAINTENANCE_TARGET)


def tool_enabled(tool):
    return tool.get("enabled", True)


def redirect_to(location, cookie=""):
    # location wird nur aus geprüften Werten gebaut (Slug bzw. Token): nur [A-Za-z0-9_/-]
    return result(STATIC_UPSTREAM + "/_redirect?to=" + location, cookie=cookie)


def share_session_mode(tool):
    """Apps, die auf "/" laufen, bekommen beim Freigabe-Link eine Sitzung auf der Wurzel statt eines Pfads."""
    return tool.get("type") == "proxy" and (tool.get("strip_prefix", True) or tool.get("access") == "subdomain")


def share_cookie(token, expires):
    max_age = max(0, int(expires - time.time()))
    return f"{SHARE_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Lax"


def find_share(token, tools):
    """Gültigen (nicht abgelaufenen) Share zum Token samt Tool liefern, sonst (None, None, None)."""
    if not TOKEN_RE.fullmatch(token or ""):
        return None, None, None
    key = hash_token(token)
    share = load_shares().get(key)
    if not share or share["expires"] <= time.time():
        return None, None, None
    tool = tools.get(share["tool"])
    return (key, share, tool) if tool else (None, None, None)


def open_share(key, client_ip):
    """Neue Freigabe-Sitzung zählen. False, wenn das Limit für Öffnungen erreicht ist."""
    with _lock:
        shares = load_shares()
        share = shares.get(key)
        if not share:
            return False
        if share.get("max_uses") and share.get("uses", 0) >= share["max_uses"]:
            audit("share.blocked", "", share=key[:12], tool=share["tool"], ip=client_ip, reason="max_uses")
            return False
        share["uses"] = share.get("uses", 0) + 1
        share["last_used"] = time.time()
        save_shares(shares)
    audit("share.open", "", share=key[:12], tool=share["tool"], ip=client_ip, uses=share["uses"])
    return True


def resolve_share_path(raw_path, suffix, segments, tools, cookie_header, client_ip):
    """Freigabe-Link /s/<token>/... (ohne Login, zeitlich begrenzt)."""
    token = segments[2] if len(segments) > 2 else ""
    key, share, tool = find_share(token, tools)
    if not share:
        return None
    prefix = "/s/" + token
    if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
        return None
    if not tool_enabled(tool):
        return maintenance()  # vor dem Zählen: ein Besuch während der Wartung verbraucht keine Öffnung
    cookie = ""
    if parse_cookie(cookie_header, SHARE_COOKIE) != token:
        # Neuer Besucher: zählen (und ggf. am Limit scheitern), dann Sitzungs-Cookie setzen
        if not open_share(key, client_ip):
            return None
        cookie = share_cookie(token, share["expires"])
    if share_session_mode(tool):
        # Die App läuft auf "/" - also die Sitzung auf der Wurzel starten (wie bei einer eigenen Domain)
        return redirect_to("/", cookie)
    if raw_path == prefix:
        return redirect_to(prefix + "/", cookie)
    rest = raw_path[len(prefix):]
    slug = share["tool"]
    if tool.get("type", "static") == "proxy":  # App mit eigener Base-URL /<slug>/
        return result(tool["upstream"] + "/" + slug + rest + suffix, ("/" + slug + "/", prefix + "/"), cookie)
    return result(STATIC_UPSTREAM + "/" + slug + rest + suffix, ("/" + slug + "/", prefix + "/"), cookie)


def resolve_anonymous(raw_uri, cookie_header, client_ip=""):
    """Gast ohne Login: nur Freigabe-Links (/s/...) und laufende Freigabe-Sitzungen (Cookie)."""
    cleaned = clean_uri(raw_uri)
    if not cleaned:
        return None
    raw_path, suffix, segments = cleaned
    tools = load_tools()
    if len(segments) > 1 and segments[1] == "s":
        return resolve_share_path(raw_path, suffix, segments, tools, cookie_header, client_ip)
    _, share, tool = find_share(parse_cookie(cookie_header, SHARE_COOKIE), tools)
    if not share or not share_session_mode(tool):
        return None
    if not tool_enabled(tool):
        return maintenance()
    return result(tool["upstream"] + raw_path + suffix)


def resolve_subdomain(raw_uri, groups, host):
    """Zugriff über <slug>.TOOLS_DOMAIN: das ganze Host-Verzeichnis gehört dem Tool (kein Pfad-Präfix)."""
    cleaned = clean_uri(raw_uri)
    host = host.split(":")[0].lower()
    if not cleaned or not TOOLS_DOMAIN or not host.endswith("." + TOOLS_DOMAIN):
        return None
    raw_path, suffix, _ = cleaned
    slug = host[: -len(TOOLS_DOMAIN) - 1]
    if not SLUG_RE.match(slug):
        return None
    tool = load_tools().get(slug)
    if not tool or tool.get("access") != "subdomain" or not can_see(tool, groups):
        return None
    if not tool_enabled(tool) and not is_admin(groups):
        return maintenance()
    if tool.get("type", "static") == "proxy":
        return result(tool["upstream"] + raw_path + suffix)
    return result(STATIC_UPSTREAM + "/" + slug + raw_path + suffix, ("/" + slug + "/", "/"))


def resolve_request(raw_uri, groups, referer="", host="", cookie_header="", client_ip=""):
    """Entscheidet über einen Zugriff angemeldeter Nutzer und liefert das Ziel für nginx.

    raw_uri ist $request_uri von nginx (Pfad + Query, so wie der Client ihn geschickt hat).
    Rückgabe: None (verboten) oder dict(target, redir, cookie)
      target  vollständige Ziel-URL (interner Datei-Server oder Weiterleitungs-Adresse)
      redir   (von, nach): Location-Header des Ziels, die mit "von" beginnen, werden auf "nach" umgeschrieben
      cookie  Set-Cookie-Wert oder ""
    """
    cleaned = clean_uri(raw_uri)
    if not cleaned:
        return None
    raw_path, suffix, segments = cleaned
    tools = load_tools()
    slug = segments[1] if len(segments) > 1 else ""

    if slug == "s":
        return resolve_share_path(raw_path, suffix, segments, tools, cookie_header, client_ip)

    tool = tools.get(slug)
    if tool:
        if not can_see(tool, groups):
            return None
        # Der Slug muss im Rohpfad wörtlich (nicht kodiert) vorkommen
        prefix = "/" + slug
        if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
            return None
        if not tool_enabled(tool) and not is_admin(groups):
            return maintenance()
        if raw_path == prefix:  # /name -> /name/ (relative Links brauchen den Schrägstrich)
            return redirect_to(prefix + "/")
        if tool.get("type", "static") == "proxy":
            strip = tool.get("strip_prefix", True)
            rest = raw_path[len(prefix):] if strip else raw_path
            cookie = ""
            if strip and parse_cookie(cookie_header, COOKIE_NAME) != slug:
                cookie = f"{COOKIE_NAME}={slug}; Path=/; Max-Age=43200; HttpOnly; Secure; SameSite=Lax"
            return result(tool["upstream"] + (rest or "/") + suffix,
                          ("/", prefix + "/") if strip else NO_REDIRECT, cookie)
        return result(STATIC_UPSTREAM + raw_path + suffix)

    # Unbekannter erster Pfadteil: Viele Apps rufen absolute Pfade wie /api/x oder /static/app.js auf.
    # Dann das zuletzt benutzte Tool nehmen - erst nach Referer, dann nach Merk-Cookie.
    candidates = []
    if referer:
        ref = urlsplit(referer)
        if ref.netloc == host:
            ref_parts = unquote(ref.path).split("/")
            if len(ref_parts) > 1:
                candidates.append(ref_parts[1])
    candidates.append(parse_cookie(cookie_header, COOKIE_NAME))
    for cand in candidates:
        t = tools.get(cand)
        if t and t.get("type") == "proxy" and t.get("strip_prefix", True) and can_see(t, groups):
            if not tool_enabled(t) and not is_admin(groups):
                return maintenance()
            return result(t["upstream"] + raw_path + suffix, ("/", "/" + cand + "/"))
    return None


def secret_ok(header_value):
    return bool(TOOLS_SECRET) and hmac.compare_digest(header_value.encode(), TOOLS_SECRET.encode())


def ensure_site_dir(slug, name):
    folder = SITES_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)
    index = folder / "index.html"
    if not index.exists():
        index.write_text(
            f"<!doctype html><meta charset=utf-8><title>{html.escape(name)}</title>"
            f"<h1>{html.escape(name)}</h1><p>Lege deine Dateien in sites/{slug}/ ab.</p>\n",
            encoding="utf-8",
        )


def share_info(key, share, tools, now):
    return {
        "id": key[:12], "tool": share["tool"], "toolName": tools.get(share["tool"], {}).get("name", share["tool"]),
        "label": share.get("label", ""), "created": share["created"], "expires": share["expires"],
        "createdBy": share.get("created_by", ""), "uses": share.get("uses", 0), "maxUses": share.get("max_uses"),
        "lastUsed": share.get("last_used"),
        "active": share["expires"] > now and share["tool"] in tools
                  and not (share.get("max_uses") and share.get("uses", 0) >= share["max_uses"]),
    }


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "tools"
    sys_version = ""

    def log_message(self, fmt, *args):  # ruhig, keine Header/Cookies loggen
        pass

    # --- Hilfen
    def user(self):
        name = self.headers.get("X-Authentik-Username", "")
        groups = [g for g in self.headers.get("X-Authentik-Groups", "").split("|") if g]
        return name, groups

    def client_ip(self):
        return self.headers.get("X-Real-IP", "")[:64]

    def send_json(self, status, payload=None, extra=None):
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def check_same_origin(self):
        """Schutz gegen CSRF: schreibende Anfragen nur von der eigenen Seite."""
        origin = (urlsplit(self.headers.get("Origin", "")).hostname or "").lower()
        host = self.headers.get("Host", "").rsplit(":", 1)[0].lower() if "]" not in self.headers.get("Host", "") \
            else self.headers.get("Host", "").lower()
        # Vergleich ohne Port: Proxys geben den Host teils ohne Port weiter (nginx $host)
        if not origin or origin != host:
            raise PermissionError("Ungültiger Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site != "same-origin":
            raise PermissionError("Anfrage nicht von dieser Seite")

    def read_body(self):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ValueError("Content-Type muss application/json sein")
        self.check_same_origin()
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Anfrage zu groß")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet")
        return data

    # --- Routing
    def handle_request(self, method):
        url = urlsplit(self.path)
        path = url.path
        if method == "GET" and path == "/healthz":
            return self.send_json(200, {"ok": True})
        if not secret_ok(self.headers.get("X-Tools-Secret", "")):
            return self.send_json(403, {"error": "Anfrage nicht über den Proxy gekommen"})
        username, groups = self.user()
        # Von tools-web gesetzt: "path" (tools.example.de/<slug>/) oder "subdomain" (<slug>.tools.example.de)
        mode = self.headers.get("X-Tools-Mode", "path")

        if method == "GET" and path == "/authz":
            raw_uri = self.headers.get("X-Original-URI", "")
            cookies = self.headers.get("Cookie", "")
            if not username:
                # Gäste: nur Freigabe-Links und Freigabe-Sitzungen, und nur auf der Haupt-Domain
                res = resolve_anonymous(raw_uri, cookies, self.client_ip()) if mode == "path" else None
            elif mode == "subdomain":
                res = resolve_subdomain(raw_uri, groups, self.headers.get("Host", ""))
            else:
                res = resolve_request(raw_uri, groups, referer=self.headers.get("Referer", ""),
                                      host=self.headers.get("Host", ""), cookie_header=cookies,
                                      client_ip=self.client_ip())
            if not res:
                # Gäste: 401 -> der NPM schickt zum Login. Ungültiger Freigabe-Link: 403 -> Seite "Kein Zugriff".
                return self.send_json(401 if not username and not raw_uri.startswith("/s/") else 403)
            return self.send_json(200, extra={
                "X-Tool-Target": res["target"], "X-Tool-Redir-From": res["redir"][0],
                "X-Tool-Redir-To": res["redir"][1], "X-Tool-Cookie": res["cookie"],
            })

        if not username:
            return self.send_json(401, {"error": "nicht angemeldet"})
        if method == "GET" and path == "/authz-admin":
            return self.send_json(200 if is_admin(groups) else 403)

        if method == "GET" and path == "/api/me":
            return self.send_json(200, {
                "username": username,
                "name": self.headers.get("X-Authentik-Name", "") or username,
                "isAdmin": is_admin(groups),
                "toolsDomain": TOOLS_DOMAIN,
            })
        if method == "GET" and path == "/api/tools":
            admin = is_admin(groups)
            tools = [
                {"slug": s, "name": t["name"], "description": t["description"], "icon": t["icon"],
                 "category": t.get("category", ""), "enabled": tool_enabled(t), "url": tool_url(s, t)}
                for s, t in sorted(load_tools().items(), key=lambda kv: kv[1]["name"].lower())
                if can_see(t, groups) and (tool_enabled(t) or admin)
            ]
            return self.send_json(200, tools)

        if path.startswith("/api/admin/"):
            if not is_admin(groups):
                return self.send_json(403, {"error": "keine Admin-Rechte"})
            try:
                return self.admin(method, path, url.query, username)
            except PermissionError as e:
                return self.send_json(403, {"error": str(e)})
            except (ValueError, json.JSONDecodeError) as e:
                return self.send_json(400, {"error": str(e)})
        self.send_json(404, {"error": "nicht gefunden"})

    def admin(self, method, path, query, actor):
        parts = path.split("/")  # ['', 'api', 'admin', <bereich>, <id>]
        area = parts[3] if len(parts) > 3 else ""
        item = parts[4] if len(parts) == 5 else None
        if len(parts) > 5:
            return self.send_json(404, {"error": "nicht gefunden"})
        if area == "tools":
            return self.admin_tools(method, item, actor)
        if area == "shares":
            return self.admin_shares(method, item, actor)
        if area == "audit" and method == "GET" and item is None:
            limit = parse_qs(query).get("limit", ["200"])[0]
            return self.send_json(200, read_audit(min(max(int(limit) if limit.isdigit() else 200, 1), 1000)))
        if area == "export" and method == "GET" and item is None:
            audit("config.export", actor)
            return self.send_json(200, {"exported": time.time(), "tools": load_tools()}, extra={
                "Content-Disposition": 'attachment; filename="tools-export.json"'})
        return self.send_json(404, {"error": "nicht gefunden"})

    def admin_tools(self, method, slug, actor):
        if method == "GET" and slug is None:
            tools = load_tools()
            return self.send_json(200, [{"slug": s, "type": "static", "access": "path", "category": "",
                                         "enabled": True, **t, "url": tool_url(s, t)}
                                        for s, t in sorted(tools.items())])
        if method == "POST" and slug is None:
            body = self.read_body()
            new_slug = body.get("slug", "")
            if not isinstance(new_slug, str) or not SLUG_RE.match(new_slug) or new_slug in RESERVED_SLUGS:
                raise ValueError("Ungültiger Kurzname (a-z, 0-9, '-', max. 41 Zeichen)")
            tool = validate_tool(body)
            with _lock:
                tools = load_tools()
                if new_slug in tools:
                    raise ValueError("Kurzname existiert bereits")
                tools[new_slug] = tool
                if tool["type"] == "static":
                    ensure_site_dir(new_slug, tool["name"])
                save_tools(tools)
            audit("tool.create", actor, tool=new_slug, type=tool["type"], group=tool["group"],
                  upstream=tool.get("upstream", ""))
            return self.send_json(201, {"slug": new_slug, **tool})
        if method == "PUT" and slug:
            tool = validate_tool(self.read_body())
            with _lock:
                tools = load_tools()
                if slug not in tools:
                    return self.send_json(404, {"error": "Tool nicht gefunden"})
                old = tools[slug]
                tools[slug] = tool
                if tool["type"] == "static":
                    ensure_site_dir(slug, tool["name"])
                save_tools(tools)
            changed = sorted(k for k in set(old) | set(tool) if old.get(k) != tool.get(k))
            audit("tool.update", actor, tool=slug, changed=changed)
            return self.send_json(200, {"slug": slug, **tool})
        if method == "DELETE" and slug:
            self.check_same_origin()
            with _lock:
                tools = load_tools()
                if slug not in tools:
                    return self.send_json(404, {"error": "Tool nicht gefunden"})
                del tools[slug]  # Dateien in sites/<slug>/ bleiben liegen
                save_tools(tools)
            audit("tool.delete", actor, tool=slug)
            return self.send_json(200, {"ok": True})
        return self.send_json(405, {"error": "Methode nicht erlaubt"})

    def admin_shares(self, method, share_id, actor):
        now = time.time()
        if method == "GET" and share_id is None:
            tools = load_tools()
            out = [share_info(k, sh, tools, now) for k, sh in load_shares().items()]
            return self.send_json(200, sorted(out, key=lambda x: -x["created"]))
        if method == "POST" and share_id is None:
            body = self.read_body()
            tools = load_tools()
            tool_slug = body.get("tool")
            if not isinstance(tool_slug, str) or tool_slug not in tools:
                raise ValueError("Tool nicht gefunden")
            label = clean_text(body.get("label", ""), "Bezeichnung", 60)
            expires = validate_expiry(body, now)
            max_uses = validate_max_uses(body)
            token = secrets.token_urlsafe(32)
            with _lock:
                # Abgelaufene Links nach 7 Tagen aufräumen
                shares = {h: sh for h, sh in load_shares().items() if sh["expires"] > now - 7 * 86400}
                if sum(1 for sh in shares.values() if sh["expires"] > now) >= SHARE_MAX_ACTIVE:
                    raise ValueError("Zu viele aktive Freigabe-Links")
                shares[hash_token(token)] = {
                    "tool": tool_slug, "label": label, "created": now, "expires": expires,
                    "created_by": actor, "max_uses": max_uses, "uses": 0,
                }
                save_shares(shares)
            audit("share.create", actor, share=hash_token(token)[:12], tool=tool_slug, expires=expires,
                  max_uses=max_uses)
            # Der Token wird nur hier einmal ausgegeben; gespeichert ist nur sein Hash
            return self.send_json(201, {"id": hash_token(token)[:12], "path": f"/s/{token}/", "expires": expires,
                                        "sessionMode": share_session_mode(tools[tool_slug])})
        if method == "PUT" and share_id:
            body = self.read_body()
            with _lock:
                shares = load_shares()
                match = [h for h in shares if h[:12] == share_id]
                if not match:
                    return self.send_json(404, {"error": "Link nicht gefunden"})
                share = shares[match[0]]
                if "label" in body:
                    share["label"] = clean_text(body["label"], "Bezeichnung", 60)
                if "expires_in" in body or "expires_at" in body:
                    share["expires"] = validate_expiry(body, now)
                if "max_uses" in body:
                    share["max_uses"] = validate_max_uses(body)
                save_shares(shares)
            audit("share.update", actor, share=share_id, expires=share["expires"], max_uses=share.get("max_uses"))
            return self.send_json(200, share_info(match[0], share, load_tools(), now))
        if method == "DELETE" and share_id:
            self.check_same_origin()
            with _lock:
                shares = load_shares()
                match = [h for h in shares if h[:12] == share_id]
                if not match:
                    return self.send_json(404, {"error": "Link nicht gefunden"})
                tool = shares.pop(match[0])["tool"]
                save_shares(shares)
            audit("share.revoke", actor, share=share_id, tool=tool)
            return self.send_json(200, {"ok": True})
        return self.send_json(405, {"error": "Methode nicht erlaubt"})

    def do_GET(self): self.handle_request("GET")
    def do_POST(self): self.handle_request("POST")
    def do_PUT(self): self.handle_request("PUT")
    def do_DELETE(self): self.handle_request("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
