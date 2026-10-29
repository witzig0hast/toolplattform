# Nimmt Benachrichtigungen des Monitors entgegen und schreibt sie zeilenweise in eine Datei
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
OUT = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        with open(OUT, 'ab') as f:
            f.write(body + b'\n')
        self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()
ThreadingHTTPServer(('127.0.0.1', 9200), H).serve_forever()
