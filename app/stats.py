"""Nutzungsstatistik und persönliche Einstellungen (Favoriten, zuletzt benutzt).

Aufrufe werden nur für echte Seitenaufrufe gezählt (Browser-Navigation, nicht jede Bild- oder
API-Anfrage). Sie landen zuerst im Speicher und werden alle paar Sekunden gebündelt gespeichert.
"""
import json
import os
import tempfile
import threading
import time
from datetime import date, timedelta

KEEP_DAYS = 90
RECENT_MAX = 8
FAVORITES_MAX = 50
FLUSH_SECONDS = 15

_pending = []          # (ts, user, slug)
_pending_lock = threading.Lock()


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
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Stats:
    def __init__(self, data_dir, lock):
        self.stats_file = data_dir / "stats.json"
        self.prefs_file = data_dir / "prefs.json"
        self.lock = lock  # gemeinsame Schreibsperre des Backends

    # --- Erfassen
    def record_visit(self, user, slug):
        if user and slug:
            with _pending_lock:
                _pending.append((time.time(), user, slug))

    def flush(self):
        with _pending_lock:
            batch = _pending[:]
            _pending.clear()
        if not batch:
            return
        with self.lock:
            stats = _load(self.stats_file)
            prefs = _load(self.prefs_file)
            for ts, user, slug in batch:
                day = date.fromtimestamp(ts).isoformat()
                per_tool = stats.setdefault(day, {}).setdefault(slug, {})
                per_tool[user] = per_tool.get(user, 0) + 1
                p = prefs.setdefault(user, {})
                recent = [r for r in p.get("recent", []) if r[0] != slug]
                p["recent"] = ([[slug, ts]] + recent)[:RECENT_MAX]
            cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
            for day in [d for d in stats if d < cutoff]:
                del stats[day]
            _save(self.stats_file, stats)
            _save(self.prefs_file, prefs)

    def start_flusher(self):
        def loop():
            while True:
                time.sleep(FLUSH_SECONDS)
                try:
                    self.flush()
                except OSError:
                    pass
        threading.Thread(target=loop, daemon=True).start()

    # --- Persönliches
    def prefs(self, user):
        self.flush()
        p = _load(self.prefs_file).get(user, {})
        return {"favorites": p.get("favorites", []), "recent": [r[0] for r in p.get("recent", [])]}

    def set_favorites(self, user, favorites):
        with self.lock:
            prefs = _load(self.prefs_file)
            prefs.setdefault(user, {})["favorites"] = favorites[:FAVORITES_MAX]
            _save(self.prefs_file, prefs)

    # --- Auswertung für Admins
    def summary(self, days=30):
        self.flush()
        stats = _load(self.stats_file)
        today = date.today()
        since = (today - timedelta(days=days - 1)).isoformat()
        since7 = (today - timedelta(days=6)).isoformat()
        out = {}
        for day, tools in stats.items():
            if day < since:
                continue
            for slug, users in tools.items():
                t = out.setdefault(slug, {"total": 0, "last7": 0, "users": {}, "days": {}})
                n = sum(users.values())
                t["total"] += n
                t["days"][day] = t["days"].get(day, 0) + n
                if day >= since7:
                    t["last7"] += n
                for u, c in users.items():
                    t["users"][u] = t["users"].get(u, 0) + c
        result = {}
        for slug, t in out.items():
            top = sorted(t["users"].items(), key=lambda kv: -kv[1])[:5]
            series = [t["days"].get((today - timedelta(days=i)).isoformat(), 0) for i in range(days - 1, -1, -1)]
            result[slug] = {"total": t["total"], "last7": t["last7"], "uniqueUsers": len(t["users"]),
                            "topUsers": [{"user": u, "count": c} for u, c in top], "series": series}
        return {"days": days, "tools": result}
