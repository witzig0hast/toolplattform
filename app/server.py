"""Kleiner Backend-Dienst der Tools-Plattform (nur Python-Standardbibliothek).

- Verwaltet die Tool-Liste (data/tools.json)
- Entscheidet für nginx (auth_request), wer welchen Slash sehen darf
- Liefert die API für Portal- und Admin-Seite

Vertraut den Headern X-Authentik-Username / X-Authentik-Groups. Darf deshalb nur
über den tools-web-Container erreichbar sein (siehe docker-compose.yml).
"""
import html
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

DATA_FILE = Path(os.environ.get("TOOLS_DATA", "/data/tools.json"))
SITES_DIR = Path(os.environ.get("TOOLS_SITES", "/srv/sites"))
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "tools-admin")

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


def save_tools(tools):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_FILE.parent, prefix=".tools-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, DATA_FILE)


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


def validate_tool(body):
    group = clean_text(body.get("group", ""), "Gruppe", 64, required=True)
    if "|" in group:
        raise ValueError("Gruppe darf kein '|' enthalten")
    return {
        "name": clean_text(body.get("name", ""), "Name", 60, required=True),
        "description": clean_text(body.get("description", ""), "Beschreibung", 200),
        "icon": clean_text(body.get("icon", ""), "Symbol", 8),
        "group": group,
    }


# ---------- Rechte ----------
def is_admin(groups):
    return ADMIN_GROUP in groups


def can_see(tool, groups):
    return is_admin(groups) or tool["group"] in groups


def authorize_path(raw_uri, groups):
    """Darf jemand mit diesen Gruppen den Pfad aus der Original-URL aufrufen?"""
    path = unquote(urlsplit(raw_uri).path)
    segments = path.split("/")
    # Pfad muss sauber sein: kein '..', '.', '//' oder Null-Byte (sonst Umgehung via Normalisierung)
    if not path.startswith("/") or "\0" in path or "\\" in path:
        return False
    if any(s in ("", ".", "..") for s in segments[1:-1]) or segments[-1] in (".", ".."):
        return False
    slug = segments[1] if len(segments) > 1 else ""
    tool = load_tools().get(slug)
    return bool(tool) and can_see(tool, groups)


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

    def send_json(self, status, payload=None):
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
        if not username:
            return self.send_json(401, {"error": "nicht angemeldet"})
        path = urlsplit(self.path).path

        if method == "GET" and path == "/authz":
            ok = authorize_path(self.headers.get("X-Original-URI", ""), groups)
            return self.send_json(200 if ok else 403)
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
        if parts[:4] != ["", "api", "admin", "tools"] or len(parts) > 5:
            return self.send_json(404, {"error": "nicht gefunden"})
        slug = parts[4] if len(parts) == 5 else None
        try:
            if method == "GET" and slug is None:
                tools = load_tools()
                return self.send_json(200, [{"slug": s, **t} for s, t in sorted(tools.items())])
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
