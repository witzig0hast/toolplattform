# Test-App wie viele echte Web-Apps (z. B. Nginx Proxy Manager): Single-Page-App mit eigenem Router,
# absoluten Links (/settings, /), Seitenwechsel ohne Neuladen und Daten von /api/...
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>SPA</title></head><body>
<nav><a href="/" id="home">Home</a> <a href="/settings" id="settings">Settings</a> <a href="/nginx/proxy" id="proxy">Proxy</a></nav>
<h1 id="view"></h1><p id="data"></p>
<script>
const routes = {'/': 'Dashboard', '/settings': 'Settings', '/nginx/proxy': 'Proxy Hosts'};
function render() {
  document.getElementById('view').textContent = routes[location.pathname] || "Oops, you've just found an error page";
  fetch('/api/data?p=' + encodeURIComponent(location.pathname)).then(r => r.json()).then(d => {
    document.getElementById('data').textContent = 'API ok: ' + d.ok;
  }).catch(() => { document.getElementById('data').textContent = 'API Fehler'; });
}
document.querySelectorAll('nav a').forEach(a => a.addEventListener('click', e => {
  if (a.id === 'proxy') return;            // dieser Link lädt die Seite komplett neu
  e.preventDefault(); history.pushState({}, '', a.getAttribute('href')); render();
}));
window.addEventListener('popstate', render);
render();
</script></body></html>"""
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith('/api/'):
            body, ctype = json.dumps({'ok': True, 'path': self.path}).encode(), 'application/json'
        else:
            body, ctype = PAGE.encode(), 'text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
ThreadingHTTPServer((sys.argv[1], 9001), H).serve_forever()
