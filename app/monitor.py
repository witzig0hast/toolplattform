"""Überwachung der Weiterleitungs-Tools (eigener Container "tools-monitor").

- Prüft alle HEALTH_INTERVAL Sekunden, ob das Ziel jedes Weiterleitungs-Tools antwortet.
- Schreibt das Ergebnis nach data/status/health.json (liest tools.json nur).
- Meldet Ausfälle und Wiederherstellungen per ntfy, Telegram und/oder Webhook.
  Ein Ausfall gilt erst nach HEALTH_FAILS Fehlversuchen in Folge (keine Fehlalarme bei kurzen Aussetzern).
- Eine Test-Benachrichtigung wird über die Datei data/status/test-notify angestoßen (vom Backend angelegt).

Läuft ohne Geheimnis und ohne Schreibrecht auf die Tool-Liste.
"""
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_FILE = Path(os.environ.get("TOOLS_DATA", "/data/tools.json"))
STATUS_DIR = DATA_FILE.parent / "status"
HEALTH_FILE = STATUS_DIR / "health.json"
TEST_TRIGGER = STATUS_DIR / "test-notify"
INTERVAL = max(5, int(os.environ.get("HEALTH_INTERVAL", "60")))
FAILS = max(1, int(os.environ.get("HEALTH_FAILS", "2")))
TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT", "5"))
PLATFORM_NAME = os.environ.get("TOOLS_DOMAIN", "") or "Tools-Plattform"

NTFY_URL = os.environ.get("NOTIFY_NTFY_URL", "").strip()
NTFY_TOKEN = os.environ.get("NOTIFY_NTFY_TOKEN", "").strip()
TG_TOKEN = os.environ.get("NOTIFY_TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("NOTIFY_TELEGRAM_CHAT", "").strip()
WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # eine Weiterleitung zählt bereits als "antwortet"


_opener = urllib.request.build_opener(_NoRedirect)
_direct = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def check(upstream):
    """(ok, detail, ms). ok = das Ziel antwortet mit einem Status < 500."""
    start = time.monotonic()
    req = urllib.request.Request(upstream + "/", method="GET", headers={"User-Agent": "tools-monitor"})
    try:
        with _direct.open(req, timeout=TIMEOUT) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # Verbindung abgelehnt, Zeitüberschreitung, DNS ...
        reason = getattr(e, "reason", e)
        return False, str(reason)[:120], None
    ms = int((time.monotonic() - start) * 1000)
    return code < 500, f"HTTP {code}", ms


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with _opener.open(req, timeout=10) as r:
        r.read()


def notify(title, message):
    """An alle eingerichteten Ziele senden. Gibt eine Liste (Ziel, ok, Fehler) zurück."""
    results = []
    if NTFY_URL:
        try:
            h = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Tags": "warning"}
            if NTFY_TOKEN:
                h["Authorization"] = "Bearer " + NTFY_TOKEN
            _post(NTFY_URL, message.encode(), h)
            results.append(("ntfy", True, ""))
        except Exception as e:
            results.append(("ntfy", False, str(e)[:120]))
    if TG_TOKEN and TG_CHAT:
        try:
            body = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": f"{title}\n{message}"}).encode()
            _post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", body,
                  {"Content-Type": "application/x-www-form-urlencoded"})
            results.append(("telegram", True, ""))
        except Exception as e:
            results.append(("telegram", False, str(e)[:120].replace(TG_TOKEN, "***")))
    if WEBHOOK_URL:
        try:
            _post(WEBHOOK_URL, json.dumps({"title": title, "message": message, "text": f"{title}: {message}"}).encode(),
                  {"Content-Type": "application/json"})
            results.append(("webhook", True, ""))
        except Exception as e:
            results.append(("webhook", False, str(e)[:120]))
    return results


def _load(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def run_once(state):
    tools = _load(DATA_FILE)
    now = time.time()
    health = {}
    for slug, tool in sorted(tools.items()):
        if tool.get("type") != "proxy":
            continue
        prev = state.get(slug, {"state": "unknown", "fails": 0, "since": now})
        if not tool.get("enabled", True):
            health[slug] = {"state": "maintenance", "checked": now, "since": prev.get("since", now), "detail": "Wartungsmodus"}
            state[slug] = {**prev, "fails": 0}
            continue
        ok, detail, ms = check(tool["upstream"])
        fails = 0 if ok else prev.get("fails", 0) + 1
        new_state = "up" if ok else ("down" if fails >= FAILS else prev["state"] if prev["state"] != "unknown" else "unknown")
        since = prev.get("since", now) if new_state == prev["state"] else now
        if new_state != prev["state"] and prev["state"] in ("up", "down"):
            name = tool.get("name", slug)
            if new_state == "down":
                notify(f"🔴 {name} ist nicht erreichbar", f"{PLATFORM_NAME}: /{slug}/ → {tool['upstream']} ({detail})")
            else:
                notify(f"🟢 {name} ist wieder erreichbar", f"{PLATFORM_NAME}: /{slug}/ antwortet wieder ({detail})")
        state[slug] = {"state": new_state, "fails": fails, "since": since}
        health[slug] = {"state": new_state, "checked": now, "since": since, "detail": detail, "ms": ms}
    for slug in [s for s in state if s not in tools]:
        del state[slug]
    _save(HEALTH_FILE, {"updated": now, "interval": INTERVAL, "tools": health,
                        "notify": {"ntfy": bool(NTFY_URL), "telegram": bool(TG_TOKEN and TG_CHAT), "webhook": bool(WEBHOOK_URL)}})


def handle_test_trigger():
    if not TEST_TRIGGER.exists():
        return
    try:
        TEST_TRIGGER.unlink()
    except OSError:
        return
    results = notify("✅ Test-Benachrichtigung", f"{PLATFORM_NAME}: Benachrichtigungen funktionieren.")
    _save(STATUS_DIR / "test-result.json", {"ts": time.time(),
                                            "results": [{"target": t, "ok": ok, "error": err} for t, ok, err in results]})


def main():
    state = {}
    old = _load(HEALTH_FILE).get("tools", {})
    for slug, h in old.items():  # Zustand nach Neustart übernehmen (keine doppelten Meldungen)
        state[slug] = {"state": h.get("state", "unknown"), "fails": 0, "since": h.get("since", time.time())}
    next_run = 0
    while True:
        if time.time() >= next_run:
            try:
                run_once(state)
            except OSError as e:
                print("Monitor-Fehler:", e, flush=True)
            next_run = time.time() + INTERVAL
        handle_test_trigger()
        time.sleep(2)


if __name__ == "__main__":
    main()
