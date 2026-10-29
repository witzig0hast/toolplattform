# Nachgebauter Authentik-Outpost: eingeloggt = Cookie "authsess=1"
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith('/outpost.goauthentik.io/auth/nginx'):
            if 'authsess=2' in (self.headers.get('Cookie') or ''):
                self.send_response(200)
                self.send_header('X-authentik-username', 'anna')
                self.send_header('X-authentik-groups', 'tool-strom')
                self.end_headers(); return
            if 'authsess=1' in (self.headers.get('Cookie') or ''):
                self.send_response(200)
                self.send_header('X-authentik-username', 'karim')
                self.send_header('X-authentik-groups', 'tools-admin|tool-strom')
                self.end_headers(); return
            self.send_response(401); self.end_headers(); return
        if self.path.startswith('/outpost.goauthentik.io/start'):
            self.send_response(302); self.send_header('Location', 'https://auth.example/LOGIN'); self.end_headers(); return
        self.send_response(404); self.end_headers()
ThreadingHTTPServer(('127.0.0.1', 9100), H).serve_forever()
