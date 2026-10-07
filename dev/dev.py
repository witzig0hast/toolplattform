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
os.environ.setdefault("TOOLS_SECRET", "dev-secret")
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402


class Dev(server.Handler):
    def parse_request(self):
        ok = super().parse_request()
        if ok:
            for k, v in (("X-Authentik-Username", os.environ.get("DEV_USER", "karim")),
                         ("X-Authentik-Name", os.environ.get("DEV_USER", "karim")),
                         ("X-Tools-Secret", os.environ["TOOLS_SECRET"]),
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
        if path.startswith("/_platform/api/"):
            self.path = self.path[len("/_platform"):]
            return super().handle_request(method)
        if path.startswith("/authz") or path == "/healthz":
            return super().handle_request(method)
        if path == "/":
            return self.serve(ROOT / "portal", "index.html")
        if path.startswith("/_platform/assets/"):
            return self.serve(ROOT / "portal", path[1:])
        if path.startswith("/_platform/admin"):
            if not server.is_admin(groups):
                return self.send_error(403)
            return self.serve(ROOT / "portal", path[1:])
        res = server.resolve_request(self.path, groups, self.headers.get("Referer", ""),
                                     self.headers.get("Host", ""), self.headers.get("Cookie", ""))
        if not res:
            return self.send_error(403)
        target = res["target"]
        if "/_redirect?to=" in target:
            self.send_response(301)
            self.send_header("Location", target.split("to=", 1)[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if target.startswith(server.STATIC_UPSTREAM):
            return self.serve(ROOT / "sites", path[1:])
        body = f"(Vorschau) Hier würde nach {target} weitergeleitet.".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8099"))
    print(f"Vorschau: http://127.0.0.1:{port}/  (Gruppen: {os.environ.get('DEV_GROUPS', 'tools-admin')})")
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
