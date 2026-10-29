"""Dateien von Dateien-Tools verwalten (sites/<slug>/): auflisten, hochladen, ZIP entpacken, löschen.

Sicherheit:
- Nur einfache, sichtbare Namen (keine "..", keine versteckten Dateien, keine absoluten Pfade).
- Jeder Zielpfad wird nach dem Auflösen (inkl. Symlinks) geprüft: Er muss im Tool-Ordner liegen.
- ZIP: keine Symlinks, Größen- und Anzahl-Grenzen; tatsächlich geschriebene Bytes werden gezählt
  (Schutz gegen ZIP-Bomben mit falschen Größenangaben).
"""
import os
import re
import secrets
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_UPLOAD = 100 * 1024 * 1024       # eine Datei bzw. ein ZIP
MAX_ZIP_TOTAL = 500 * 1024 * 1024    # entpackt
MAX_ZIP_ENTRIES = 5000
MAX_LIST = 3000
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+,=@-]{0,120}$")
CHUNK = 64 * 1024


def safe_rel(path_str, allow_empty=False):
    """Relativen Pfad prüfen ("a/b/c.html"). Wirft ValueError bei allem Verdächtigen."""
    if not isinstance(path_str, str):
        raise ValueError("Pfad erwartet")
    p = path_str.strip().strip("/")
    if not p:
        if allow_empty:
            return PurePosixPath()
        raise ValueError("Dateiname fehlt")
    parts = p.split("/")
    if len(parts) > 12:
        raise ValueError("Pfad ist zu tief verschachtelt")
    for part in parts:
        if not NAME_RE.match(part) or part.endswith("."):
            raise ValueError(f"Ungültiger Name: {part!r} (erlaubt: Buchstaben, Ziffern, . _ - Leerzeichen; nicht mit Punkt beginnen)")
    return PurePosixPath(*parts)


def tool_dir(sites_dir, slug):
    base = Path(sites_dir) / slug
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def inside(base, path):
    """Zielpfad auflösen und sicherstellen, dass er im Tool-Ordner liegt."""
    resolved = Path(os.path.realpath(path))
    if resolved != base and base not in resolved.parents:
        raise ValueError("Pfad liegt außerhalb des Tool-Ordners")
    return resolved


def list_files(base):
    out = []
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(root, d)))
        for name in sorted(files):
            full = os.path.join(root, name)
            if name.startswith(".") or os.path.islink(full):
                continue
            st = os.stat(full)
            out.append({"path": os.path.relpath(full, base).replace(os.sep, "/"), "size": st.st_size,
                        "mtime": int(st.st_mtime)})
            if len(out) >= MAX_LIST:
                return out, True
    return out, False


def _copy_body(rfile, length, dest_file):
    remaining = length
    with open(dest_file, "wb") as f:
        while remaining > 0:
            chunk = rfile.read(min(CHUNK, remaining))
            if not chunk:
                raise ValueError("Upload unvollständig")
            f.write(chunk)
            remaining -= len(chunk)


def save_upload(base, rel_str, rfile, length, unzip=False):
    """Hochgeladene Datei speichern bzw. ZIP entpacken. Gibt die Anzahl geschriebener Dateien zurück."""
    if length <= 0:
        raise ValueError("Leere Datei")
    if length > MAX_UPLOAD:
        raise ValueError(f"Datei zu groß (max. {MAX_UPLOAD // 1024 // 1024} MB)")
    rel = safe_rel(rel_str)
    dest_dir = inside(base, base / rel.parent)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = inside(base, dest_dir)
    tmp = dest_dir / f".upload-{secrets.token_hex(8)}"
    try:
        _copy_body(rfile, length, tmp)
        if unzip:
            return extract_zip(tmp, base, dest_dir)
        target = inside(base, dest_dir / rel.name)
        if target.is_dir():
            raise ValueError("Unter diesem Namen gibt es bereits einen Ordner")
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        return 1
    finally:
        if tmp.exists():
            tmp.unlink()


def _zip_members(zf):
    """Brauchbare Einträge (ohne Ordner, Symlinks, macOS-Reste, versteckte Dateien)."""
    members = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("__MACOSX/"):
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            continue
        # Zuerst auf Ausbruchsversuche prüfen (sonst würde ".." als "versteckt" nur übersprungen)
        if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
            raise ValueError(f"Unsicherer Pfad im ZIP: {name!r}")
        if any(seg.startswith(".") for seg in name.split("/") if seg):
            continue
        members.append((info, name))
    return members


def extract_zip(zip_path, base, dest_dir):
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise ValueError("Keine gültige ZIP-Datei")
    with zf:
        members = _zip_members(zf)
        if not members:
            raise ValueError("ZIP enthält keine Dateien")
        if len(members) > MAX_ZIP_ENTRIES:
            raise ValueError(f"ZIP enthält zu viele Dateien (max. {MAX_ZIP_ENTRIES})")
        if sum(info.file_size for info, _ in members) > MAX_ZIP_TOTAL:
            raise ValueError(f"ZIP ist entpackt zu groß (max. {MAX_ZIP_TOTAL // 1024 // 1024} MB)")
        # Ein gemeinsamer Oberordner ("meine-seite/index.html") wird weggelassen
        firsts = {name.split("/")[0] for _, name in members}
        strip = len(firsts) == 1 and all("/" in name for _, name in members)
        written = 0
        total = 0
        for info, name in members:
            rel = safe_rel(name.split("/", 1)[1] if strip else name)
            target = inside(base, dest_dir / rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            inside(base, target.parent)
            tmp = target.parent / f".unzip-{secrets.token_hex(8)}"
            try:
                with zf.open(info) as src, open(tmp, "wb") as out:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_ZIP_TOTAL:
                            raise ValueError("ZIP ist entpackt zu groß")
                        out.write(chunk)
                if target.is_dir():
                    raise ValueError(f"{rel} ist bereits ein Ordner")
                os.chmod(tmp, 0o644)
                os.replace(tmp, target)
                written += 1
            finally:
                if tmp.exists():
                    tmp.unlink()
        return written


def delete_path(base, rel_str):
    rel = safe_rel(rel_str)
    target = inside(base, base / rel)
    if target == base:
        raise ValueError("Der Tool-Ordner selbst kann nicht gelöscht werden")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    else:
        raise FileNotFoundError("Datei nicht gefunden")
