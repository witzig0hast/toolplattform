"""Kleiner Backend-Dienst der Tools-Plattform (nur Python-Standardbibliothek).

- Verwaltet die Tool-Liste (data/tools.json)
- Entscheidet für nginx (auth_request), wer welchen Slash sehen darf
- Liefert die API für Portal- und Admin-Seite

Vertraut den Headern X-Authentik-Username / X-Authentik-Groups. Darf deshalb nur
über den tools-web-Container erreichbar sein (siehe docker-compose.yml).
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
from urllib.parse import unquote, urlsplit

DATA_FILE = Path(os.environ.get("TOOLS_DATA", "/data/tools.json"))
SITES_DIR = Path(os.environ.get("TOOLS_SITES", "/srv/sites"))
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "tools-admin")
# Gemeinsames Geheimnis zwischen NPM und tools-web: ohne es gelten Authentik-Header nicht
TOOLS_SECRET = os.environ.get("TOOLS_SECRET", "")
# Interner nginx-Server (nur im Container erreichbar), der die statischen Tool-Dateien ausliefert
STATIC_UPSTREAM = "http://127.0.0.1:8081"
COOKIE_NAME = "tools_last"
NO_REDIRECT = ("/_none_/", "/_none_/")  # Platzhalter: nginx proxy_redirect ohne Wirkung

# Öffentliche Freigabe-Links (/s/<token>/...)
SHARES_FILE = DATA_FILE.parent / "shares.json"
SHARE_MIN_SECONDS = 60
SHARE_MAX_SECONDS = 90 * 24 * 3600
SHARE_MAX_ACTIVE = 200

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RESERVED_SLUGS = {"api", "admin", "assets"}
MAX_BODY = 8 * 1024
_lock = threading.Lock()


# ---------- Registry ----------
def load_tools():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
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


def save_tools(tools):
    _save_json(DATA_FILE, tools)


def load_shares():
    try:
        data = json.loads(SHARES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def save_shares(shares):
    _save_json(SHARES_FILE, shares)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def find_share(token, tools):
    """Gültigen (nicht abgelaufenen) Share zum Token samt Tool liefern, sonst (None, None)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        return None, None
    share = load_shares().get(hash_token(token))
    if not share or share["expires"] <= time.time():
        return None, None
    tool = tools.get(share["tool"])
    return (share, tool) if tool else (None, None)


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
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise ValueError("Diese Ziel-Adresse ist nicht erlaubt")
    except ValueError as e:
        if "nicht erlaubt" in str(e):
            raise
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
        "group": group,
        "type": kind,
    }
    if kind == "proxy":
        tool["upstream"] = validate_upstream(body.get("upstream"))
        strip = body.get("strip_prefix", True)
        if not isinstance(strip, bool):
            raise ValueError("strip_prefix muss true oder false sein")
        tool["strip_prefix"] = strip
    return tool


# ---------- Rechte ----------
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


def resolve_request(raw_uri, groups, referer="", host="", cookie_header=""):
    """Entscheidet über einen Zugriff und liefert das Ziel für nginx.

    raw_uri ist $request_uri von nginx (Pfad + Query, so wie der Client ihn geschickt hat).
    Rückgabe: None (verboten) oder dict(target, redir_from, redir_to, cookie)
      target      vollständige Ziel-URL (interner Datei-Server oder Weiterleitungs-Adresse)
      redir_from/to  Location-Header des Ziels, die mit redir_from beginnen, werden auf redir_to umgeschrieben
      cookie      Set-Cookie-Wert (merkt sich das zuletzt genutzte Tool) oder ""
    """
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
    suffix = ("?" + query) if query else ""
    tools = load_tools()
    slug = segments[1] if len(segments) > 1 else ""

    # --- Öffentlicher Freigabe-Link: /s/<token>/...  (kein Login, zeitlich begrenzt)
    if slug == "s":
        token = segments[2] if len(segments) > 2 else ""
        share, tool = find_share(token, tools)
        if not share:
            return None
        prefix = "/s/" + token
        if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
            return None
        if raw_path == prefix:
            return {"target": STATIC_UPSTREAM + "/_redirect?to=" + prefix + "/", "redir": NO_REDIRECT, "cookie": ""}
        rest = raw_path[len(prefix):]
        tool_slug = share["tool"]
        if tool.get("type", "static") == "proxy":
            if tool.get("strip_prefix", True):
                return {"target": tool["upstream"] + rest + suffix, "redir": ("/", prefix + "/"), "cookie": ""}
            return {"target": tool["upstream"] + "/" + tool_slug + rest + suffix,
                    "redir": ("/" + tool_slug + "/", prefix + "/"), "cookie": ""}
        return {"target": STATIC_UPSTREAM + "/" + tool_slug + rest + suffix,
                "redir": ("/" + tool_slug + "/", prefix + "/"), "cookie": ""}

    tool = tools.get(slug)
    if tool:
        if not can_see(tool, groups):
            return None
        # Der Slug muss im Rohpfad wörtlich (nicht kodiert) vorkommen
        prefix = "/" + slug
        if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
            return None
        if raw_path == prefix:  # /name -> /name/ (relative Links brauchen den Schrägstrich)
            return {"target": STATIC_UPSTREAM + "/_redirect?to=" + prefix + "/", "redir": NO_REDIRECT, "cookie": ""}
        if tool.get("type", "static") == "proxy":
            strip = tool.get("strip_prefix", True)
            rest = raw_path[len(prefix):] if strip else raw_path
            cookie = ""
            if strip and parse_cookie(cookie_header, COOKIE_NAME) != slug:
                cookie = f"{COOKIE_NAME}={slug}; Path=/; Max-Age=43200; HttpOnly; Secure; SameSite=Lax"
            return {"target": tool["upstream"] + (rest or "/") + suffix,
                    "redir": ("/", prefix + "/") if strip else NO_REDIRECT, "cookie": cookie}
        return {"target": STATIC_UPSTREAM + raw_path + suffix, "redir": NO_REDIRECT, "cookie": ""}

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
            return {"target": t["upstream"] + raw_path + suffix, "redir": ("/", "/" + cand + "/"), "cookie": ""}
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

    def read_body(self):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ValueError("Content-Type muss application/json sein")
        origin = self.headers.get("Origin", "")
        if not origin or urlsplit(origin).netloc != self.headers.get("Host", ""):
            raise PermissionError("Ungültiger Origin")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Anfrage zu groß")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet")
        return data

    # --- Routing
    def handle_request(self, method):
        if method == "GET" and urlsplit(self.path).path == "/healthz":
            return self.send_json(200, {"ok": True})
        username, groups = self.user()
        if not secret_ok(self.headers.get("X-Tools-Secret", "")):
            return self.send_json(403, {"error": "Anfrage nicht über den Proxy gekommen"})
        path = urlsplit(self.path).path
        # Öffentliche Freigabe-Links (/s/...) brauchen keinen Login; alles andere schon
        public_share = path == "/authz" and self.headers.get("X-Original-URI", "").startswith("/s/")
        if not username and not public_share:
            return self.send_json(401, {"error": "nicht angemeldet"})

        if method == "GET" and path == "/authz":
            res = resolve_request(
                self.headers.get("X-Original-URI", ""), groups,
                referer=self.headers.get("Referer", ""),
                host=self.headers.get("Host", ""),
                cookie_header=self.headers.get("Cookie", ""),
            )
            if not res:
                return self.send_json(403)
            return self.send_json(200, extra={
                "X-Tool-Target": res["target"], "X-Tool-Redir-From": res["redir"][0],
                "X-Tool-Redir-To": res["redir"][1], "X-Tool-Cookie": res["cookie"],
            })
        if method == "GET" and path == "/authz-admin":
            return self.send_json(200 if is_admin(groups) else 403)

        if method == "GET" and path == "/api/me":
            return self.send_json(200, {
                "username": username,
                "name": self.headers.get("X-Authentik-Name", "") or username,
                "isAdmin": is_admin(groups),
            })
        if method == "GET" and path == "/api/tools":
            tools = [
                {"slug": s, "name": t["name"], "description": t["description"], "icon": t["icon"]}
                for s, t in sorted(load_tools().items(), key=lambda kv: kv[1]["name"].lower())
                if can_see(t, groups)
            ]
            return self.send_json(200, tools)

        if path.startswith("/api/admin/"):
            if not is_admin(groups):
                return self.send_json(403, {"error": "keine Admin-Rechte"})
            return self.admin(method, path)
        self.send_json(404, {"error": "nicht gefunden"})

    def admin(self, method, path):
        parts = path.split("/")  # ['', 'api', 'admin', 'tools', <slug>]
        if parts[:4] == ["", "api", "admin", "shares"] and len(parts) <= 5:
            return self.admin_shares(method, parts[4] if len(parts) == 5 else None)
        if parts[:4] != ["", "api", "admin", "tools"] or len(parts) > 5:
            return self.send_json(404, {"error": "nicht gefunden"})
        slug = parts[4] if len(parts) == 5 else None
        try:
            if method == "GET" and slug is None:
                tools = load_tools()
                return self.send_json(200, [{"slug": s, "type": "static", **t} for s, t in sorted(tools.items())])
            if method == "POST" and slug is None:
                body = self.read_body()
                new_slug = body.get("slug", "")
                if not isinstance(new_slug, str) or not SLUG_RE.match(new_slug) or new_slug in RESERVED_SLUGS:
                    raise ValueError("Ungültiger Slug (a-z, 0-9, '-', max. 41 Zeichen)")
                tool = validate_tool(body)
                with _lock:
                    tools = load_tools()
                    if new_slug in tools:
                        raise ValueError("Slug existiert bereits")
                    tools[new_slug] = tool
                    if tool["type"] == "static":
                        ensure_site_dir(new_slug, tool["name"])
                    save_tools(tools)
                return self.send_json(201, {"slug": new_slug, **tool})
            if method == "PUT" and slug:
                tool = validate_tool(self.read_body())
                with _lock:
                    tools = load_tools()
                    if slug not in tools:
                        return self.send_json(404, {"error": "Tool nicht gefunden"})
                    tools[slug] = tool
                    if tool["type"] == "static":
                        ensure_site_dir(slug, tool["name"])
                    save_tools(tools)
                return self.send_json(200, {"slug": slug, **tool})
            if method == "DELETE" and slug:
                self.read_body_check_origin()
                with _lock:
                    tools = load_tools()
                    if slug not in tools:
                        return self.send_json(404, {"error": "Tool nicht gefunden"})
                    del tools[slug]  # Dateien in sites/<slug>/ bleiben liegen
                    save_tools(tools)
                return self.send_json(200, {"ok": True})
        except PermissionError as e:
            return self.send_json(403, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": str(e)})
        self.send_json(405, {"error": "Methode nicht erlaubt"})

    def admin_shares(self, method, share_id):
        try:
            if method == "GET" and share_id is None:
                tools = load_tools()
                now = time.time()
                out = []
                for h, sh in load_shares().items():
                    out.append({
                        "id": h[:12], "tool": sh["tool"], "toolName": tools.get(sh["tool"], {}).get("name", sh["tool"]),
                        "label": sh.get("label", ""), "created": sh["created"], "expires": sh["expires"],
                        "createdBy": sh.get("created_by", ""), "active": sh["expires"] > now and sh["tool"] in tools,
                    })
                return self.send_json(200, sorted(out, key=lambda x: -x["created"]))
            if method == "POST" and share_id is None:
                body = self.read_body()
                tools = load_tools()
                tool_slug = body.get("tool")
                if tool_slug not in tools:
                    raise ValueError("Tool nicht gefunden")
                label = clean_text(body.get("label", ""), "Bezeichnung", 60)
                seconds = body.get("expires_in")
                if not isinstance(seconds, int) or isinstance(seconds, bool) or not SHARE_MIN_SECONDS <= seconds <= SHARE_MAX_SECONDS:
                    raise ValueError("Gültigkeit muss zwischen 1 Minute und 90 Tagen liegen")
                token = secrets.token_urlsafe(32)
                now = time.time()
                with _lock:
                    shares = {h: sh for h, sh in load_shares().items() if sh["expires"] > now - 7 * 86400}
                    if sum(1 for sh in shares.values() if sh["expires"] > now) >= SHARE_MAX_ACTIVE:
                        raise ValueError("Zu viele aktive Freigabe-Links")
                    shares[hash_token(token)] = {
                        "tool": tool_slug, "label": label, "created": now, "expires": now + seconds,
                        "created_by": self.headers.get("X-Authentik-Username", ""),
                    }
                    save_shares(shares)
                # Der Token wird nur hier einmal ausgegeben; gespeichert ist nur sein Hash
                return self.send_json(201, {"id": hash_token(token)[:12], "path": f"/s/{token}/", "expires": now + seconds})
            if method == "DELETE" and share_id:
                self.read_body_check_origin()
                with _lock:
                    shares = load_shares()
                    match = [h for h in shares if h[:12] == share_id]
                    if not match:
                        return self.send_json(404, {"error": "Link nicht gefunden"})
                    del shares[match[0]]
                    save_shares(shares)
                return self.send_json(200, {"ok": True})
        except PermissionError as e:
            return self.send_json(403, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": str(e)})
        self.send_json(405, {"error": "Methode nicht erlaubt"})

    def read_body_check_origin(self):
        origin = self.headers.get("Origin", "")
        if not origin or urlsplit(origin).netloc != self.headers.get("Host", ""):
            raise PermissionError("Ungültiger Origin")

    def do_GET(self): self.handle_request("GET")
    def do_POST(self): self.handle_request("POST")
    def do_PUT(self): self.handle_request("PUT")
    def do_DELETE(self): self.handle_request("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
