# Test-App: wie eine typische Web-App mit absoluten Pfaden (/api/...) und Redirect auf /login
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith('/go-login'):
            self.send_response(302); self.send_header('Location', '/login'); self.end_headers(); return
        body = json.dumps({'app': sys.argv[2], 'path': self.path, 'user': self.headers.get('X-Authentik-Username'),
                           'secret': self.headers.get('X-Tools-Secret')}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    do_POST = do_GET
ThreadingHTTPServer((sys.argv[1], 9001), H).serve_forever()
