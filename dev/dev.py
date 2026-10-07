"""Lokale Vorschau ohne Docker/nginx/Authentik: python3 dev/dev.py
Simuliert den Login über Umgebungsvariablen:
  DEV_USER (default: karim), DEV_GROUPS (default: tools-admin), PORT (default: 8099)
Daten liegen in dev/data/ (nicht in data/)."""
import mimetypes
import os
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("TOOLS_DATA", str(ROOT / "dev" / "data" / "tools.json"))
os.environ.setdefault("TOOLS_SITES", str(ROOT / "sites"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402


class Dev(server.Handler):
    def parse_request(self):
        ok = super().parse_request()
        if ok:
            for k, v in (("X-Authentik-Username", os.environ.get("DEV_USER", "karim")),
                         ("X-Authentik-Name", os.environ.get("DEV_USER", "karim")),
                         ("X-Authentik-Groups", os.environ.get("DEV_GROUPS", "tools-admin"))):
                del self.headers[k]
                self.headers[k] = v
        return ok

    def serve(self, base, rel):
        f = (base / rel).resolve()
        if base.resolve() not in f.parents and f != base.resolve():
            return self.send_error(404)
        if f.is_dir():
            f = f / "index.html"
        if not f.is_file():
            return self.send_error(404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self, method):
        path = self.path.split("?")[0]
        _, groups = self.user()
        if path.startswith("/api/") or path.startswith("/authz") or path == "/healthz":
            return super().handle_request(method)
        if path == "/":
            return self.serve(ROOT / "portal", "index.html")
        if path.startswith("/assets/"):
            return self.serve(ROOT / "portal", path[1:])
        if path.startswith("/admin"):
            if not server.is_admin(groups):
                return self.send_error(403)
            return self.serve(ROOT / "portal", path[1:])
        if server.authorize_path(path, groups):
            return self.serve(ROOT / "sites", path[1:])
        self.send_error(403)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8099"))
    print(f"Vorschau: http://127.0.0.1:{port}/  (Gruppen: {os.environ.get('DEV_GROUPS', 'tools-admin')})")
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
