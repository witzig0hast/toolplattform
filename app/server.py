"""Kleiner Backend-Dienst der Tools-Plattform (nur Python-Standardbibliothek).

- Verwaltet die Tool-Liste (data/tools.json) und Freigabe-Links (data/shares.json)
- Entscheidet für nginx (auth_request), wer welches Tool sehen darf und wohin die Anfrage geht
- Liefert die API für Portal- und Admin-Seite
- Schreibt ein Protokoll (data/audit.log) über Admin-Aktionen und geöffnete Freigabe-Links
- Freigabe-Links mit PIN, IP-Beschränkung und Zeitfenster; Nutzungsstatistik, Favoriten,
  Import/Export, Datei-Upload für Dateien-Tools, Anzeige des Überwachungs-Status (monitor.py)

Vertraut den Headern X-Authentik-Username / X-Authentik-Groups nur zusammen mit dem gemeinsamen
Geheimnis (TOOLS_SECRET), das nur der Nginx Proxy Manager kennt. Darf ausschließlich über den
tools-web-Container erreichbar sein (siehe docker-compose.yml).
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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import files as filestore
from stats import Stats

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
except Exception:  # ohne Zeitzonen-Daten: UTC
    TZ = timezone.utc

DATA_FILE = Path(os.environ.get("TOOLS_DATA", "/data/tools.json"))
SITES_DIR = Path(os.environ.get("TOOLS_SITES", "/srv/sites"))
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "tools-admin")
# Basis-Domain für Tools mit eigener Subdomain (<slug>.TOOLS_DOMAIN), z. B. tools.example.de
TOOLS_DOMAIN = os.environ.get("TOOLS_DOMAIN", "").strip().strip(".").lower()
# Gemeinsames Geheimnis zwischen NPM und tools-web: ohne es gelten Authentik-Header nicht
TOOLS_SECRET = os.environ.get("TOOLS_SECRET", "")
# Interner nginx-Server (nur im Container erreichbar), der die statischen Tool-Dateien ausliefert
STATIC_UPSTREAM = "http://127.0.0.1:8081"
MAINTENANCE_TARGET = STATIC_UPSTREAM + "/_maintenance"
PORTAL_TARGET = STATIC_UPSTREAM + "/_portal"
MFA_TARGET = STATIC_UPSTREAM + "/_mfa"                    # Seite "2FA erforderlich"
SHARE_BLOCKED_TARGET = STATIC_UPSTREAM + "/_share_blocked"  # Seite "Link gerade nicht freigegeben"
# So erreicht tools-web dieses Backend (für die PIN-Abfrage bei Freigabe-Links)
TOOLS_API_SELF = os.environ.get("TOOLS_API_SELF", "http://tools-api:8000").rstrip("/")
APP_COOKIE = "tools_app"        # aktive App im App-Modus (angemeldete Nutzer): "<kurzname>:<letzte Nutzung>"
APP_IDLE_SECONDS = 3600         # App-Sitzung endet nach 1 Stunde ohne Nutzung (dann zeigt "/" wieder das Portal)
APP_REFRESH_SECONDS = 300       # bei Nutzung wird die Sitzung höchstens alle 5 Minuten verlängert
SHARE_COOKIE = "tools_share"    # Freigabe-Sitzung (Gäste ohne Login)
NO_REDIRECT = ("/_none_/", "/_none_/")  # Platzhalter: nginx proxy_redirect ohne Wirkung

# Öffentliche Freigabe-Links (/s/<token>/...)
SHARES_FILE = DATA_FILE.parent / "shares.json"
SHARE_MIN_SECONDS = 60
SHARE_MAX_SECONDS = 365 * 24 * 3600
SHARE_MAX_ACTIVE = 200
SHARE_MAX_USES = 100000
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")
PIN_MIN, PIN_MAX = 4, 64
PIN_ITERATIONS = 200_000
PIN_MAX_FAILS = 5              # danach Sperre
PIN_LOCK_SECONDS = 10 * 60
SHARE_MAX_IPS = 20
# Signatur-Schlüssel für Freigabe-Sitzungen (abgeleitet vom gemeinsamen Geheimnis)
COOKIE_KEY = hmac.new(TOOLS_SECRET.encode(), b"tools-share-session-v1", hashlib.sha256).digest()

# Überwachung (geschrieben vom Container tools-monitor)
STATUS_DIR = DATA_FILE.parent / "status"
HEALTH_FILE = STATUS_DIR / "health.json"

# Protokoll
AUDIT_FILE = DATA_FILE.parent / "audit.log"
AUDIT_MAX_BYTES = 1024 * 1024

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RESERVED_SLUGS = {"api", "admin", "assets", "s"}
MAX_BODY = 8 * 1024
MAX_IMPORT = 1024 * 1024
_lock = threading.Lock()
STATS = Stats(DATA_FILE.parent, _lock)


# ---------- Speicher ----------
def _load_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def load_tools():
    return _load_json(DATA_FILE)


def save_tools(tools):
    _save_json(DATA_FILE, tools)


def load_shares():
    return _load_json(SHARES_FILE)


def save_shares(shares):
    _save_json(SHARES_FILE, shares)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- Protokoll ----------
_audit_lock = threading.Lock()


def audit(action, actor, **details):
    """Eine Zeile JSON ins Protokoll schreiben (rotiert bei 1 MB nach audit.log.1)."""
    entry = {"ts": round(time.time(), 3), "action": action, "actor": actor or "", **details}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _audit_lock:
        try:
            AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > AUDIT_MAX_BYTES:
                os.replace(AUDIT_FILE, AUDIT_FILE.with_name(AUDIT_FILE.name + ".1"))
            fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # Protokoll darf den Betrieb nie blockieren


def read_audit(limit=200):
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---------- Eingaben prüfen ----------
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
    except ValueError:
        return value  # Hostname, kein IP-Literal
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError("Diese Ziel-Adresse ist nicht erlaubt")
    return value


def validate_bool(body, key, default):
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} muss true oder false sein")
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
        "category": clean_text(body.get("category", ""), "Kategorie", 40),
        "group": group,
        "type": kind,
        "access": body.get("access", "path"),
        "enabled": validate_bool(body, "enabled", True),
        "require_mfa": validate_bool(body, "require_mfa", False),
    }
    if tool["access"] not in ("path", "subdomain"):
        raise ValueError("Aufruf muss 'path' oder 'subdomain' sein")
    if tool["access"] == "subdomain" and not TOOLS_DOMAIN:
        raise ValueError("Für Subdomains muss TOOLS_DOMAIN in der .env gesetzt sein")
    if kind == "proxy":
        tool["upstream"] = validate_upstream(body.get("upstream"))
        tool["strip_prefix"] = validate_bool(body, "strip_prefix", True)
    return tool


def validate_expiry(body, now):
    """Ablaufzeitpunkt aus expires_in (Sekunden) oder expires_at (Unix-Sekunden)."""
    if "expires_at" in body:
        at = body["expires_at"]
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            raise ValueError("Ungültiger Ablaufzeitpunkt")
        seconds, expires = int(at - now), float(at)
    else:
        seconds = body.get("expires_in")
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            raise ValueError("Ungültige Gültigkeit")
        expires = now + seconds
    if not SHARE_MIN_SECONDS <= seconds <= SHARE_MAX_SECONDS:
        raise ValueError("Gültigkeit muss zwischen 1 Minute und 365 Tagen liegen")
    return expires


def validate_max_uses(body):
    value = body.get("max_uses")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= SHARE_MAX_USES:
        raise ValueError("Maximale Öffnungen: leer lassen oder eine Zahl ab 1")
    return value


def hash_pin(pin, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PIN_ITERATIONS).hex()
    return {"salt": salt, "hash": digest}


def check_pin(pin, stored):
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(stored["salt"]), PIN_ITERATIONS).hex()
    return hmac.compare_digest(digest, stored["hash"])


def validate_pin(value):
    if not isinstance(value, str) or not PIN_MIN <= len(value) <= PIN_MAX or any(ord(c) < 32 for c in value):
        raise ValueError(f"PIN/Passwort: {PIN_MIN} bis {PIN_MAX} Zeichen")
    return hash_pin(value)


def validate_ips(value):
    """Liste aus IPs/Netzen ("192.168.1.0/24", "203.0.113.7"). Leere Liste = keine Beschränkung."""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[\s,;]+", value) if v]
    if not isinstance(value, list) or len(value) > SHARE_MAX_IPS:
        raise ValueError(f"Höchstens {SHARE_MAX_IPS} IP-Adressen/Netze")
    out = []
    for v in value:
        try:
            out.append(str(ipaddress.ip_network(str(v).strip(), strict=False)))
        except ValueError:
            raise ValueError(f"Ungültige IP-Adresse oder Netz: {v!r}")
    return out


TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def validate_schedule(value):
    """Zeitfenster {"days": [0..6] (0 = Montag), "from": "08:00", "to": "18:00"} oder None."""
    if value in (None, {}, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("Ungültiges Zeitfenster")
    days = value.get("days", [])
    if not isinstance(days, list) or not days or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days):
        raise ValueError("Zeitfenster: mindestens einen Wochentag wählen")
    start, end = value.get("from", ""), value.get("to", "")
    if not TIME_RE.match(str(start)) or not TIME_RE.match(str(end)) or start == end:
        raise ValueError("Zeitfenster: Uhrzeiten im Format HH:MM, Beginn ungleich Ende")
    return {"days": sorted(set(days)), "from": start, "to": end}


def in_schedule(schedule, now=None):
    """Liegt der Zeitpunkt im Zeitfenster? Fenster über Mitternacht (22:00-06:00) zählen zum Starttag."""
    if not schedule:
        return True
    t = datetime.fromtimestamp(now or time.time(), TZ)
    hm = t.strftime("%H:%M")
    if schedule["from"] < schedule["to"]:
        return t.weekday() in schedule["days"] and schedule["from"] <= hm < schedule["to"]
    if hm >= schedule["from"]:
        return t.weekday() in schedule["days"]
    return (t.weekday() - 1) % 7 in schedule["days"] and hm < schedule["to"]


def ip_allowed(allowed, client_ip):
    if not allowed:
        return True
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(net) for net in allowed)


# ---------- Rechte & Ziele ----------
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


def clean_uri(raw_uri):
    """Rohe URL (Pfad + Query) prüfen. Rückgabe (raw_path, suffix, segments) oder None bei unsauberen Pfaden."""
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
    return raw_path, ("?" + query) if query else "", segments


def tool_url(slug, tool):
    if tool.get("access") == "subdomain" and TOOLS_DOMAIN:
        return f"https://{slug}.{TOOLS_DOMAIN}/"
    return f"/{slug}/"


def result(target, redir=NO_REDIRECT, cookie="", slug=""):
    return {"target": target, "redir": redir, "cookie": cookie, "slug": slug}


def maintenance():
    return result(MAINTENANCE_TARGET)


def tool_enabled(tool):
    return tool.get("enabled", True)


def redirect_to(location, cookie=""):
    # location wird nur aus geprüften Werten gebaut (Slug bzw. Token): nur [A-Za-z0-9_/-]
    return result(STATIC_UPSTREAM + "/_redirect?to=" + location, cookie=cookie)


def share_session_mode(tool):
    """Apps, die auf "/" laufen, bekommen beim Freigabe-Link eine Sitzung auf der Wurzel statt eines Pfads."""
    return tool.get("type") == "proxy" and (tool.get("strip_prefix", True) or tool.get("access") == "subdomain")


def _session_sig(token, share):
    # "nonce" ändert sich bei neuer PIN -> alte Sitzungen werden ungültig
    msg = (token + "|" + share.get("nonce", "")).encode()
    return hmac.new(COOKIE_KEY, msg, hashlib.sha256).hexdigest()[:40]


def share_cookie(token, share):
    """Signiertes Sitzungs-Cookie: Wer nur den Link kennt, kann es nicht selbst bauen (wichtig bei PIN)."""
    max_age = max(0, int(share["expires"] - time.time()))
    return f"{SHARE_COOKIE}={token}.{_session_sig(token, share)}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Lax"


def session_token(cookie_header):
    """Token aus dem Sitzungs-Cookie (noch ungeprüft)."""
    return parse_cookie(cookie_header, SHARE_COOKIE).rpartition(".")[0]


def session_ok(cookie_header, token, share):
    value = parse_cookie(cookie_header, SHARE_COOKIE)
    tok, _, sig = value.rpartition(".")
    return bool(sig) and tok == token and hmac.compare_digest(sig, _session_sig(token, share))


def share_access(share, client_ip):
    """IP-Beschränkung und Zeitfenster prüfen. Gibt None (erlaubt) oder den Grund zurück."""
    if not ip_allowed(share.get("ips"), client_ip):
        return "ip"
    if not in_schedule(share.get("schedule")):
        return "schedule"
    return None


def find_share(token, tools):
    """Gültigen (nicht abgelaufenen) Share zum Token samt Tool liefern, sonst (None, None, None)."""
    if not TOKEN_RE.fullmatch(token or ""):
        return None, None, None
    key = hash_token(token)
    share = load_shares().get(key)
    if not share or share["expires"] <= time.time():
        return None, None, None
    tool = tools.get(share["tool"])
    return (key, share, tool) if tool else (None, None, None)


def open_share(key, client_ip):
    """Neue Freigabe-Sitzung zählen. False, wenn das Limit für Öffnungen erreicht ist."""
    with _lock:
        shares = load_shares()
        share = shares.get(key)
        if not share:
            return False
        if share.get("max_uses") and share.get("uses", 0) >= share["max_uses"]:
            audit("share.blocked", "", share=key[:12], tool=share["tool"], ip=client_ip, reason="max_uses")
            return False
        share["uses"] = share.get("uses", 0) + 1
        share["last_used"] = time.time()
        save_shares(shares)
    audit("share.open", "", share=key[:12], tool=share["tool"], ip=client_ip, uses=share["uses"])
    return True


def resolve_share_path(raw_path, suffix, segments, tools, cookie_header, client_ip):
    """Freigabe-Link /s/<token>/... (ohne Login, zeitlich begrenzt, optional PIN/IP/Zeitfenster)."""
    token = segments[2] if len(segments) > 2 else ""
    key, share, tool = find_share(token, tools)
    if not share:
        return None
    prefix = "/s/" + token
    if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
        return None
    if not tool_enabled(tool):
        return maintenance()  # vor dem Zählen: ein Besuch während der Wartung verbraucht keine Öffnung
    if share_access(share, client_ip):
        return result(SHARE_BLOCKED_TARGET)
    cookie = ""
    if not session_ok(cookie_header, token, share):
        if share.get("pin"):
            # PIN-Abfrage (Formular vom Backend). Erst nach richtiger PIN wird gezählt und das Cookie gesetzt.
            return result(f"{TOOLS_API_SELF}/_gate/{token}")
        # Neuer Besucher: zählen (und ggf. am Limit scheitern), dann Sitzungs-Cookie setzen
        if not open_share(key, client_ip):
            return None
        cookie = share_cookie(token, share)
    return share_destination(raw_path, suffix, token, share, tool, cookie)


def share_destination(raw_path, suffix, token, share, tool, cookie=""):
    prefix = "/s/" + token
    if share_session_mode(tool):
        # Die App läuft auf "/" - also die Sitzung auf der Wurzel starten (wie bei einer eigenen Domain)
        return redirect_to("/", cookie)
    if raw_path == prefix:
        return redirect_to(prefix + "/", cookie)
    rest = raw_path[len(prefix):]
    slug = share["tool"]
    if tool.get("type", "static") == "proxy":  # App mit eigener Base-URL /<slug>/
        return result(tool["upstream"] + "/" + slug + rest + suffix, ("/" + slug + "/", prefix + "/"), cookie)
    return result(STATIC_UPSTREAM + "/" + slug + rest + suffix, ("/" + slug + "/", prefix + "/"), cookie)


def resolve_anonymous(raw_uri, cookie_header, client_ip=""):
    """Gast ohne Login: nur Freigabe-Links (/s/...) und laufende Freigabe-Sitzungen (Cookie)."""
    cleaned = clean_uri(raw_uri)
    if not cleaned:
        return None
    raw_path, suffix, segments = cleaned
    tools = load_tools()
    if len(segments) > 1 and segments[1] == "s":
        return resolve_share_path(raw_path, suffix, segments, tools, cookie_header, client_ip)
    token = session_token(cookie_header)
    _, share, tool = find_share(token, tools)
    if not share or not share_session_mode(tool) or not session_ok(cookie_header, token, share):
        return None
    if not tool_enabled(tool):
        return maintenance()
    if share_access(share, client_ip):
        return result(SHARE_BLOCKED_TARGET)
    return result(tool["upstream"] + raw_path + suffix)


def resolve_subdomain(raw_uri, groups, host, mfa=False):
    """Zugriff über <slug>.TOOLS_DOMAIN: das ganze Host-Verzeichnis gehört dem Tool (kein Pfad-Präfix)."""
    cleaned = clean_uri(raw_uri)
    host = host.split(":")[0].lower()
    if not cleaned or not TOOLS_DOMAIN or not host.endswith("." + TOOLS_DOMAIN):
        return None
    raw_path, suffix, _ = cleaned
    slug = host[: -len(TOOLS_DOMAIN) - 1]
    if not SLUG_RE.match(slug):
        return None
    tool = load_tools().get(slug)
    if not tool or tool.get("access") != "subdomain" or not can_see(tool, groups):
        return None
    if not tool_enabled(tool) and not is_admin(groups):
        return maintenance()
    if tool.get("require_mfa") and not mfa:
        return result(MFA_TARGET)
    if tool.get("type", "static") == "proxy":
        return result(tool["upstream"] + raw_path + suffix, slug=slug)
    return result(STATIC_UPSTREAM + "/" + slug + raw_path + suffix, ("/" + slug + "/", "/"), slug=slug)


def same_host(url, host):
    """Gehört die URL (Referer) zu unserem Host? Vergleich ohne Port (Proxys geben den Host teils ohne Port weiter)."""
    ref_host = (urlsplit(url).hostname or "").lower()
    return bool(ref_host) and ref_host == host.rsplit(":", 1)[0].lower()


def app_mode(tool):
    """App-Modus: Weiterleitungs-Tools, die auf "/" laufen, laufen auf der Hauptadresse wie auf einer eigenen Domain."""
    return tool.get("type") == "proxy" and tool.get("strip_prefix", True)


def app_cookie(slug):
    return (f"{APP_COOKIE}={slug}:{int(time.time())}; Path=/; Max-Age={APP_IDLE_SECONDS}; "
            "HttpOnly; Secure; SameSite=Lax")


def current_app(cookie_header):
    slug, _, ts = parse_cookie(cookie_header, APP_COOKIE).partition(":")
    return slug, (int(ts) if ts.isdigit() else 0)


def go_to(location, cookie="", slug=""):
    """Weiterleitung auf einen beliebigen Pfad dieser Domain (über das Backend, damit Sonderzeichen sicher bleiben)."""
    return result(f"{TOOLS_API_SELF}/_go?to={quote(location, safe='')}", cookie=cookie, slug=slug)


def app_target(tool, raw_path, suffix, groups, mfa, slug="", last_used=0):
    if not tool_enabled(tool) and not is_admin(groups):
        return maintenance()
    if tool.get("require_mfa") and not mfa:
        return result(MFA_TARGET)
    # Sitzung bei Nutzung verlängern (nicht bei jeder Anfrage, nur alle paar Minuten)
    refresh = app_cookie(slug) if slug and time.time() - last_used > APP_REFRESH_SECONDS else ""
    return result(tool["upstream"] + raw_path + suffix, cookie=refresh)


def resolve_request(raw_uri, groups, referer="", host="", cookie_header="", client_ip="", mfa=False, navigate=True):
    """Entscheidet über einen Zugriff angemeldeter Nutzer und liefert das Ziel für nginx.

    raw_uri ist $request_uri von nginx (Pfad + Query, so wie der Client ihn geschickt hat).
    Rückgabe: None (verboten) oder dict(target, redir, cookie)
      target  vollständige Ziel-URL (interner Datei-Server oder Weiterleitungs-Adresse)
      redir   (von, nach): Location-Header des Ziels, die mit "von" beginnen, werden auf "nach" umgeschrieben
      cookie  Set-Cookie-Wert oder ""
    """
    cleaned = clean_uri(raw_uri)
    if not cleaned:
        return None
    raw_path, suffix, segments = cleaned
    tools = load_tools()
    slug = segments[1] if len(segments) > 1 else ""

    # Aktive App (App-Modus) aus dem Cookie - Rechte werden bei jeder Anfrage neu geprüft
    cur_slug, cur_ts = current_app(cookie_header)
    cur_tool = tools.get(cur_slug)
    cur_ok = (bool(cur_tool) and app_mode(cur_tool) and can_see(cur_tool, groups)
              and time.time() - cur_ts < APP_IDLE_SECONDS)

    if raw_path == "/":
        # Solange eine App benutzt wird, gehört "/" ihr (Neuladen, "Home"-Link, Tab-Wechsel).
        # Portal: ohne aktive App, aus der Verwaltung heraus oder unter /_platform/portal (beendet die App).
        from_platform = bool(referer) and same_host(referer, host) and urlsplit(referer).path.startswith("/_platform/")
        if cur_ok and not from_platform:
            return app_target(cur_tool, raw_path, suffix, groups, mfa, cur_slug, cur_ts)
        return result(PORTAL_TARGET)
    if slug == "s":
        return resolve_share_path(raw_path, suffix, segments, tools, cookie_header, client_ip)

    tool = tools.get(slug)
    if tool and app_mode(tool):
        # Pfad der aktiven App, der zufällig wie ein anderes Tool heißt (z. B. /api/... und ein Tool "api")?
        if not navigate and cur_ok and cur_slug != slug:
            return app_target(cur_tool, raw_path, suffix, groups, mfa, cur_slug, cur_ts)
        if not can_see(tool, groups):
            return None
        prefix = "/" + slug
        if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
            return None
        if not tool_enabled(tool) and not is_admin(groups):
            return maintenance()
        if tool.get("require_mfa") and not mfa:
            return result(MFA_TARGET)
        # App öffnen: als aktive App merken und auf die Hauptadresse wechseln (/<kurzname>/x -> /x)
        return go_to((raw_path[len(prefix):] or "/") + suffix, app_cookie(slug), slug=slug)
    if tool:
        if not can_see(tool, groups):
            return None
        # Der Slug muss im Rohpfad wörtlich (nicht kodiert) vorkommen
        prefix = "/" + slug
        if not raw_path.startswith(prefix) or (len(raw_path) > len(prefix) and raw_path[len(prefix)] != "/"):
            return None
        if not tool_enabled(tool) and not is_admin(groups):
            return maintenance()
        if tool.get("require_mfa") and not mfa:
            return result(MFA_TARGET)
        if raw_path == prefix:  # /name -> /name/ (relative Links brauchen den Schrägstrich)
            return redirect_to(prefix + "/")
        if tool.get("type", "static") == "proxy":  # App mit eigener Base-URL /<kurzname>/
            return result(tool["upstream"] + raw_path + suffix, slug=slug)
        return result(STATIC_UPSTREAM + raw_path + suffix, slug=slug)

    # Jeder andere Pfad (/settings, /api/..., /static/...) gehört der aktiven App
    if cur_ok:
        return app_target(cur_tool, raw_path, suffix, groups, mfa, cur_slug, cur_ts)
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


def load_health():
    try:
        return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


GATE_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Geschützter Link</title><style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f6f7f9;color:#14181f;margin:0;min-height:100vh;display:grid;place-items:center}}
@media (prefers-color-scheme:dark){{body{{background:#0d1017;color:#e9ecf2}}.box{{background:#151a23!important;border-color:#262d3a!important}}input{{background:#0d1017!important;color:#e9ecf2!important;border-color:#262d3a!important}}}}
.box{{background:#fff;border:1px solid #e2e5eb;border-radius:16px;padding:28px;width:min(360px,calc(100vw - 32px));box-sizing:border-box}}
h1{{font-size:1.2rem;margin:0 0 6px}}p{{color:#5d6676;margin:0 0 16px;font-size:.95rem}}
input{{width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid #e2e5eb;border-radius:10px;font:inherit;margin-bottom:12px}}
button{{width:100%;padding:11px;border:0;border-radius:10px;background:#3b5bdb;color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.err{{color:#c92a2a;font-size:.9rem;margin:-4px 0 12px}}</style></head><body>
<form class="box" method="post" autocomplete="off"><h1>🔒 Geschützter Link</h1><p>Bitte gib die PIN bzw. das Passwort ein, das du zusammen mit dem Link bekommen hast.</p>
{error}<input type="password" name="pin" aria-label="PIN oder Passwort" autofocus required maxlength="64">
<button type="submit">Öffnen</button></form></body></html>"""


def share_info(key, share, tools, now):
    return {
        "id": key[:12], "tool": share["tool"], "toolName": tools.get(share["tool"], {}).get("name", share["tool"]),
        "label": share.get("label", ""), "created": share["created"], "expires": share["expires"],
        "createdBy": share.get("created_by", ""), "uses": share.get("uses", 0), "maxUses": share.get("max_uses"),
        "lastUsed": share.get("last_used"), "hasPin": bool(share.get("pin")), "ips": share.get("ips", []),
        "schedule": share.get("schedule"), "locked": share.get("locked_until", 0) > now,
        "active": share["expires"] > now and share["tool"] in tools
                  and not (share.get("max_uses") and share.get("uses", 0) >= share["max_uses"]),
    }


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

    def client_ip(self):
        return self.headers.get("X-Real-IP", "")[:64]

    def mfa(self):
        # Von Authentik per Property-Mapping gesetzt (siehe README), vom NPM durchgereicht
        return self.headers.get("X-Authentik-Mfa", "").strip().lower() in ("1", "true", "yes")

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

    def check_same_origin(self):
        """Schutz gegen CSRF: schreibende Anfragen nur von der eigenen Seite."""
        origin = (urlsplit(self.headers.get("Origin", "")).hostname or "").lower()
        host = self.headers.get("Host", "").rsplit(":", 1)[0].lower() if "]" not in self.headers.get("Host", "") \
            else self.headers.get("Host", "").lower()
        # Vergleich ohne Port: Proxys geben den Host teils ohne Port weiter (nginx $host)
        if not origin or origin != host:
            raise PermissionError("Ungültiger Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site != "same-origin":
            raise PermissionError("Anfrage nicht von dieser Seite")

    def send_html(self, status, page, extra=None):
        body = page.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, max_bytes=MAX_BODY):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ValueError("Content-Type muss application/json sein")
        self.check_same_origin()
        length = int(self.headers.get("Content-Length") or 0)
        if length > max_bytes:
            raise ValueError("Anfrage zu groß")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet")
        return data

    # --- Routing
    def handle_request(self, method):
        url = urlsplit(self.path)
        path = url.path
        if method == "GET" and path == "/healthz":
            return self.send_json(200, {"ok": True})
        if path == "/_go":
            # Weiterleitung innerhalb dieser Domain (vom Backend selbst in /authz vorgegeben)
            to = parse_qs(url.query).get("to", ["/"])[0]
            if not to.startswith("/") or to.startswith("//") or "\\" in to or any(ord(c) < 32 for c in to):
                to = "/"
            self.send_response(302)
            self.send_header("Location", quote(to, safe="/%?=&:;,+@!$'()*~-._#"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/_gate/"):
            # PIN-Abfrage für Freigabe-Links. tools-web leitet nur hierher, wenn das Backend selbst
            # (in /authz) dieses Ziel vorgegeben hat; das Geheimnis wird an Tools nie weitergegeben.
            return self.share_gate(method, path[len("/_gate/"):])
        if not secret_ok(self.headers.get("X-Tools-Secret", "")):
            return self.send_json(403, {"error": "Anfrage nicht über den Proxy gekommen"})
        username, groups = self.user()
        # Von tools-web gesetzt: "path" (tools.example.de/<slug>/) oder "subdomain" (<slug>.tools.example.de)
        mode = self.headers.get("X-Tools-Mode", "path")

        if method == "GET" and path == "/authz":
            raw_uri = self.headers.get("X-Original-URI", "")
            cookies = self.headers.get("Cookie", "")
            if not username:
                # Gäste: nur Freigabe-Links und Freigabe-Sitzungen, und nur auf der Haupt-Domain
                res = resolve_anonymous(raw_uri, cookies, self.client_ip()) if mode == "path" else None
            elif mode == "subdomain":
                res = resolve_subdomain(raw_uri, groups, self.headers.get("Host", ""), mfa=self.mfa())
            else:
                res = resolve_request(raw_uri, groups, referer=self.headers.get("Referer", ""),
                                      host=self.headers.get("Host", ""), cookie_header=cookies,
                                      client_ip=self.client_ip(), mfa=self.mfa(),
                                      navigate=self.headers.get("Sec-Fetch-Mode", "navigate") == "navigate")
            # Nutzung zählen: nur echte Seitenaufrufe (Navigation), nicht jede Datei/API-Anfrage
            if (res and username and res.get("slug") and self.headers.get("Sec-Fetch-Mode") == "navigate"
                    and self.headers.get("Sec-Fetch-Dest", "document") == "document"):
                STATS.record_visit(username, res["slug"])
            if not res:
                # Gäste: 401 -> der NPM schickt zum Login. Ungültiger Freigabe-Link: 403 -> Seite "Kein Zugriff".
                return self.send_json(401 if not username and not raw_uri.startswith("/s/") else 403)
            return self.send_json(200, extra={
                "X-Tool-Target": res["target"], "X-Tool-Redir-From": res["redir"][0],
                "X-Tool-Redir-To": res["redir"][1], "X-Tool-Cookie": res["cookie"],
            })

        if not username:
            return self.send_json(401, {"error": "nicht angemeldet"})
        if method == "GET" and path == "/authz-admin":
            return self.send_json(200 if is_admin(groups) else 403)

        if method == "GET" and path == "/api/me":
            return self.send_json(200, {
                "username": username,
                "name": self.headers.get("X-Authentik-Name", "") or username,
                "isAdmin": is_admin(groups),
                "toolsDomain": TOOLS_DOMAIN,
                "mfa": self.mfa(),
                **STATS.prefs(username),
            })
        if method == "PUT" and path == "/api/me/favorites":
            try:
                body = self.read_body()
                favs = body.get("favorites")
                if not isinstance(favs, list) or any(not isinstance(f, str) or not SLUG_RE.match(f) for f in favs):
                    raise ValueError("Ungültige Favoriten")
            except PermissionError as e:
                return self.send_json(403, {"error": str(e)})
            except (ValueError, json.JSONDecodeError) as e:
                return self.send_json(400, {"error": str(e)})
            STATS.set_favorites(username, list(dict.fromkeys(favs)))
            return self.send_json(200, STATS.prefs(username))
        if method == "GET" and path == "/api/tools":
            admin = is_admin(groups)
            health = load_health().get("tools", {})
            tools = [
                {"slug": s, "name": t["name"], "description": t["description"], "icon": t["icon"],
                 "category": t.get("category", ""), "enabled": tool_enabled(t), "url": tool_url(s, t),
                 "health": health.get(s, {}).get("state"), "requireMfa": bool(t.get("require_mfa"))}
                for s, t in sorted(load_tools().items(), key=lambda kv: kv[1]["name"].lower())
                if can_see(t, groups) and (tool_enabled(t) or admin)
            ]
            return self.send_json(200, tools)

        if path.startswith("/api/admin/"):
            if not is_admin(groups):
                return self.send_json(403, {"error": "keine Admin-Rechte"})
            try:
                return self.admin(method, path, url.query, username)
            except PermissionError as e:
                return self.send_json(403, {"error": str(e)})
            except (ValueError, json.JSONDecodeError) as e:
                return self.send_json(400, {"error": str(e)})
        self.send_json(404, {"error": "nicht gefunden"})

    def admin(self, method, path, query, actor):
        parts = path.split("/")  # ['', 'api', 'admin', <bereich>, <id>]
        area = parts[3] if len(parts) > 3 else ""
        item = parts[4] if len(parts) == 5 else None
        if len(parts) > 5:
            return self.send_json(404, {"error": "nicht gefunden"})
        if area == "tools":
            return self.admin_tools(method, item, actor)
        if area == "shares":
            return self.admin_shares(method, item, actor)
        if area == "files" and item:
            return self.admin_files(method, item, query, actor)
        if area == "stats" and method == "GET" and item is None:
            days = parse_qs(query).get("days", ["30"])[0]
            return self.send_json(200, STATS.summary(min(max(int(days) if days.isdigit() else 30, 1), 90)))
        if area == "health" and method == "GET" and item is None:
            data = load_health()
            try:
                data["test"] = json.loads((STATUS_DIR / "test-result.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                data["test"] = None
            return self.send_json(200, data)
        if area == "notify-test" and method == "POST" and item is None:
            self.check_same_origin()
            STATUS_DIR.mkdir(parents=True, exist_ok=True)
            (STATUS_DIR / "test-notify").write_text(str(time.time()), encoding="utf-8")
            audit("notify.test", actor)
            return self.send_json(202, {"ok": True})
        if area == "import" and method == "POST" and item is None:
            return self.admin_import(actor)
        if area == "audit" and method == "GET" and item is None:
            limit = parse_qs(query).get("limit", ["200"])[0]
            return self.send_json(200, read_audit(min(max(int(limit) if limit.isdigit() else 200, 1), 1000)))
        if area == "export" and method == "GET" and item is None:
            audit("config.export", actor)
            return self.send_json(200, {"exported": time.time(), "tools": load_tools()}, extra={
                "Content-Disposition": 'attachment; filename="tools-export.json"'})
        return self.send_json(404, {"error": "nicht gefunden"})

    def admin_tools(self, method, slug, actor):
        if method == "GET" and slug is None:
            tools = load_tools()
            return self.send_json(200, [{"slug": s, "type": "static", "access": "path", "category": "",
                                         "enabled": True, **t, "url": tool_url(s, t)}
                                        for s, t in sorted(tools.items())])
        if method == "POST" and slug is None:
            body = self.read_body()
            new_slug = body.get("slug", "")
            if not isinstance(new_slug, str) or not SLUG_RE.match(new_slug) or new_slug in RESERVED_SLUGS:
                raise ValueError("Ungültiger Kurzname (a-z, 0-9, '-', max. 41 Zeichen)")
            tool = validate_tool(body)
            with _lock:
                tools = load_tools()
                if new_slug in tools:
                    raise ValueError("Kurzname existiert bereits")
                tools[new_slug] = tool
                if tool["type"] == "static":
                    ensure_site_dir(new_slug, tool["name"])
                save_tools(tools)
            audit("tool.create", actor, tool=new_slug, type=tool["type"], group=tool["group"],
                  upstream=tool.get("upstream", ""))
            return self.send_json(201, {"slug": new_slug, **tool})
        if method == "PUT" and slug:
            tool = validate_tool(self.read_body())
            with _lock:
                tools = load_tools()
                if slug not in tools:
                    return self.send_json(404, {"error": "Tool nicht gefunden"})
                old = tools[slug]
                tools[slug] = tool
                if tool["type"] == "static":
                    ensure_site_dir(slug, tool["name"])
                save_tools(tools)
            changed = sorted(k for k in set(old) | set(tool) if old.get(k) != tool.get(k))
            audit("tool.update", actor, tool=slug, changed=changed)
            return self.send_json(200, {"slug": slug, **tool})
        if method == "DELETE" and slug:
            self.check_same_origin()
            with _lock:
                tools = load_tools()
                if slug not in tools:
                    return self.send_json(404, {"error": "Tool nicht gefunden"})
                del tools[slug]  # Dateien in sites/<slug>/ bleiben liegen
                save_tools(tools)
            audit("tool.delete", actor, tool=slug)
            return self.send_json(200, {"ok": True})
        return self.send_json(405, {"error": "Methode nicht erlaubt"})

    def admin_shares(self, method, share_id, actor):
        now = time.time()
        if method == "GET" and share_id is None:
            tools = load_tools()
            out = [share_info(k, sh, tools, now) for k, sh in load_shares().items()]
            return self.send_json(200, sorted(out, key=lambda x: -x["created"]))
        if method == "POST" and share_id is None:
            body = self.read_body()
            tools = load_tools()
            tool_slug = body.get("tool")
            if not isinstance(tool_slug, str) or tool_slug not in tools:
                raise ValueError("Tool nicht gefunden")
            label = clean_text(body.get("label", ""), "Bezeichnung", 60)
            expires = validate_expiry(body, now)
            max_uses = validate_max_uses(body)
            pin = validate_pin(body["pin"]) if body.get("pin") else None
            ips = validate_ips(body.get("ips"))
            schedule = validate_schedule(body.get("schedule"))
            token = secrets.token_urlsafe(32)
            with _lock:
                # Abgelaufene Links nach 7 Tagen aufräumen
                shares = {h: sh for h, sh in load_shares().items() if sh["expires"] > now - 7 * 86400}
                if sum(1 for sh in shares.values() if sh["expires"] > now) >= SHARE_MAX_ACTIVE:
                    raise ValueError("Zu viele aktive Freigabe-Links")
                shares[hash_token(token)] = {
                    "tool": tool_slug, "label": label, "created": now, "expires": expires,
                    "created_by": actor, "max_uses": max_uses, "uses": 0,
                    "pin": pin, "ips": ips, "schedule": schedule, "nonce": secrets.token_hex(8),
                }
                save_shares(shares)
            audit("share.create", actor, share=hash_token(token)[:12], tool=tool_slug, expires=expires,
                  max_uses=max_uses, pin=bool(pin), ips=ips, schedule=schedule)
            # Der Token wird nur hier einmal ausgegeben; gespeichert ist nur sein Hash
            return self.send_json(201, {"id": hash_token(token)[:12], "path": f"/s/{token}/", "expires": expires,
                                        "sessionMode": share_session_mode(tools[tool_slug])})
        if method == "PUT" and share_id:
            body = self.read_body()
            with _lock:
                shares = load_shares()
                match = [h for h in shares if h[:12] == share_id]
                if not match:
                    return self.send_json(404, {"error": "Link nicht gefunden"})
                share = shares[match[0]]
                if "label" in body:
                    share["label"] = clean_text(body["label"], "Bezeichnung", 60)
                if "expires_in" in body or "expires_at" in body:
                    share["expires"] = validate_expiry(body, now)
                if "max_uses" in body:
                    share["max_uses"] = validate_max_uses(body)
                if body.get("pin"):
                    share["pin"] = validate_pin(body["pin"])
                    share["nonce"] = secrets.token_hex(8)  # neue PIN: laufende Sitzungen beenden
                elif body.get("remove_pin"):
                    share["pin"] = None
                if "ips" in body:
                    share["ips"] = validate_ips(body["ips"])
                if "schedule" in body:
                    share["schedule"] = validate_schedule(body["schedule"])
                if body.get("unlock"):
                    share["fails"], share["locked_until"] = 0, 0
                save_shares(shares)
            audit("share.update", actor, share=share_id, expires=share["expires"], max_uses=share.get("max_uses"),
                  pin=bool(share.get("pin")), ips=share.get("ips", []), schedule=share.get("schedule"))
            return self.send_json(200, share_info(match[0], share, load_tools(), now))
        if method == "DELETE" and share_id:
            self.check_same_origin()
            with _lock:
                shares = load_shares()
                match = [h for h in shares if h[:12] == share_id]
                if not match:
                    return self.send_json(404, {"error": "Link nicht gefunden"})
                tool = shares.pop(match[0])["tool"]
                save_shares(shares)
            audit("share.revoke", actor, share=share_id, tool=tool)
            return self.send_json(200, {"ok": True})
        return self.send_json(405, {"error": "Methode nicht erlaubt"})

    # --- PIN-Abfrage für Freigabe-Links
    def share_gate(self, method, token):
        tools = load_tools()
        key, share, tool = find_share(token, tools)
        if not share or not share.get("pin"):
            return self.send_html(403, GATE_PAGE.format(error='<p class="err">Dieser Link ist ungültig oder abgelaufen.</p>'))
        ip = self.client_ip()
        if not tool_enabled(tool) or share_access(share, ip):
            return self.send_html(403, GATE_PAGE.format(error='<p class="err">Dieser Link ist gerade nicht freigegeben.</p>'))
        now = time.time()
        if method != "POST":
            return self.send_html(200, GATE_PAGE.format(error=""))
        try:
            self.check_same_origin()
        except PermissionError:
            return self.send_html(403, GATE_PAGE.format(error='<p class="err">Ungültige Anfrage.</p>'))
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2048:
            return self.send_html(413, GATE_PAGE.format(error='<p class="err">Eingabe zu lang.</p>'))
        pin = parse_qs(self.rfile.read(length).decode("utf-8", "replace")).get("pin", [""])[0]
        with _lock:
            shares = load_shares()
            current = shares.get(key)
            if not current:
                return self.send_html(403, GATE_PAGE.format(error='<p class="err">Dieser Link ist ungültig oder abgelaufen.</p>'))
            if current.get("locked_until", 0) > now:
                minutes = int((current["locked_until"] - now) // 60) + 1
                return self.send_html(429, GATE_PAGE.format(
                    error=f'<p class="err">Zu viele Fehlversuche. Bitte in {minutes} Min. erneut versuchen.</p>'))
            ok = PIN_MIN <= len(pin) <= PIN_MAX and check_pin(pin, current["pin"])
            if not ok:
                current["fails"] = current.get("fails", 0) + 1
                if current["fails"] >= PIN_MAX_FAILS:
                    current["locked_until"], current["fails"] = now + PIN_LOCK_SECONDS, 0
                save_shares(shares)
        if not ok:
            audit("share.pin_failed", "", share=key[:12], tool=share["tool"], ip=ip)
            return self.send_html(403, GATE_PAGE.format(error='<p class="err">Falsche PIN.</p>'))
        if current.get("fails"):
            with _lock:
                shares = load_shares()
                if key in shares:
                    shares[key]["fails"] = 0
                    save_shares(shares)
        if not open_share(key, ip):
            return self.send_html(403, GATE_PAGE.format(error='<p class="err">Dieser Link wurde bereits so oft geöffnet wie erlaubt.</p>'))
        location = "/" if share_session_mode(tool) else "/s/" + token + "/"
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Set-Cookie", share_cookie(token, share))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- Import einer exportierten Konfiguration
    def admin_import(self, actor):
        body = self.read_body(MAX_IMPORT)
        incoming = body.get("tools")
        mode = body.get("mode", "merge")
        if mode not in ("merge", "replace"):
            raise ValueError("mode muss 'merge' oder 'replace' sein")
        if not isinstance(incoming, dict) or not incoming:
            raise ValueError("Keine Tools in der Datei")
        if len(incoming) > 500:
            raise ValueError("Zu viele Tools (max. 500)")
        clean = {}
        for slug, tool in incoming.items():
            if not isinstance(slug, str) or not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
                raise ValueError(f"Ungültiger Kurzname: {slug!r}")
            if not isinstance(tool, dict):
                raise ValueError(f"Ungültiger Eintrag: {slug}")
            try:
                clean[slug] = validate_tool(tool)
            except ValueError as e:
                raise ValueError(f"{slug}: {e}")
        with _lock:
            tools = {} if mode == "replace" else load_tools()
            added = sum(1 for s in clean if s not in tools)
            tools.update(clean)
            for slug, tool in clean.items():
                if tool["type"] == "static":
                    ensure_site_dir(slug, tool["name"])
            save_tools(tools)
        audit("config.import", actor, mode=mode, tools=sorted(clean), added=added)
        return self.send_json(200, {"imported": len(clean), "added": added, "updated": len(clean) - added, "mode": mode})

    # --- Dateien von Dateien-Tools
    def admin_files(self, method, slug, query, actor):
        tool = load_tools().get(slug)
        if not tool:
            return self.send_json(404, {"error": "Tool nicht gefunden"})
        if tool.get("type", "static") != "static":
            raise ValueError("Dateien gibt es nur bei Dateien-Tools")
        base = filestore.tool_dir(SITES_DIR, slug)
        q = parse_qs(query)
        rel = q.get("path", [""])[0]
        if method == "GET":
            entries, truncated = filestore.list_files(base)
            return self.send_json(200, {"files": entries, "truncated": truncated,
                                        "maxUpload": filestore.MAX_UPLOAD})
        if method == "POST":
            self.check_same_origin()
            length = int(self.headers.get("Content-Length") or 0)
            unzip = q.get("unzip", ["0"])[0] == "1"
            try:
                count = filestore.save_upload(base, rel, self.rfile, length, unzip=unzip)
            except ValueError:
                self.close_connection = True  # Rest des Uploads nicht mehr lesen
                raise
            audit("files.upload", actor, tool=slug, path=rel, unzip=unzip, files=count)
            return self.send_json(201, {"written": count})
        if method == "DELETE":
            self.check_same_origin()
            try:
                filestore.delete_path(base, rel)
            except FileNotFoundError as e:
                return self.send_json(404, {"error": str(e)})
            audit("files.delete", actor, tool=slug, path=rel)
            return self.send_json(200, {"ok": True})
        return self.send_json(405, {"error": "Methode nicht erlaubt"})

    def do_GET(self): self.handle_request("GET")
    def do_POST(self): self.handle_request("POST")
    def do_PUT(self): self.handle_request("PUT")
    def do_DELETE(self): self.handle_request("DELETE")


if __name__ == "__main__":
    STATS.start_flusher()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
