"""Manuelle Update-Prüfung gegen GitHub-Releases und Übergabe an den Updater-Dienst.

Ausgehende Anfragen entstehen nur auf ausdrücklichen Klick eines Admins.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings
from .security import Admin
from .version import APP_VERSION

router = APIRouter(prefix="/api/system/update")
VERSION_RE = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
CHECK_VALID_SECONDS = 3600
_checked: dict = {}
_checked_lock = threading.Lock()


class InstallInput(BaseModel):
    version: str


def parse(value):
    match = VERSION_RE.fullmatch(value or "")
    return tuple(int(p) for p in match.groups()) if match else None


def _json(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(1024 * 1024))


def fetch_release():
    request = urllib.request.Request(
        settings().release_api_url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "fillens"},
    )
    try:
        data = _json(request, 15)
    except urllib.error.HTTPError as error:
        raise HTTPException(502, f"GitHub antwortet mit Status {error.code}.")
    except OSError:
        raise HTTPException(
            502, "GitHub ist nicht erreichbar. Internetzugang des Servers prüfen."
        )
    except ValueError:
        raise HTTPException(502, "GitHub lieferte eine ungültige Antwort.")
    if not isinstance(data, dict):
        raise HTTPException(502, "GitHub lieferte eine ungültige Antwort.")
    tag = str(data.get("tag_name") or "")
    if not parse(tag):
        raise HTTPException(502, "Das neueste Release hat keine gültige Versionsnummer.")
    url = str(data.get("html_url") or "")
    return {
        "version": tag.removeprefix("v"),
        "name": str(data.get("name") or tag)[:200],
        "published_at": data.get("published_at"),
        "notes": str(data.get("body") or "")[:20000],
        "url": url if url.startswith("https://github.com/") else None,
    }


def updater_call(path, body=None):
    config = settings()
    if not config.updater_url or not config.updater_token:
        raise HTTPException(409, "Kein Updater eingerichtet. Update bitte nach Anleitung.")
    request = urllib.request.Request(
        config.updater_url.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {config.updater_token}",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        return _json(request, 30)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read(65536)).get("detail")
        except (OSError, ValueError, AttributeError):
            detail = None
        if error.code == 401:
            detail = "Updater lehnt den Zugriff ab. UPDATER_TOKEN von Backend und Updater prüfen."
        raise HTTPException(
            409 if error.code in (400, 409) else 502,
            str(detail or f"Updater antwortet mit Status {error.code}."),
        )
    except (OSError, ValueError):
        raise HTTPException(502, "Updater ist nicht erreichbar.")


def status_data(raw):
    if not isinstance(raw, dict):
        return None
    keys = ("state", "version", "from_version", "started_at", "finished_at", "backup_dir", "error")
    data = {key: raw.get(key) for key in keys}
    data["steps"] = [
        {"at": s.get("at"), "text": s.get("text")}
        for s in (raw.get("steps") or [])[-30:]
        if isinstance(s, dict)
    ]
    return data


@router.get("")
def update_state(_admin: Admin):
    config = settings()
    result = {"current_version": APP_VERSION, "updater": "missing", "status": None}
    if config.updater_url and config.updater_token:
        try:
            result["status"] = status_data(updater_call("/status"))
            result["updater"] = "ready"
        except HTTPException as error:
            result["updater"] = "unreachable"
            result["updater_error"] = error.detail
    return result


@router.post("/check")
def check(_admin: Admin):
    release = fetch_release()
    release["current_version"] = APP_VERSION
    release["update_available"] = parse(release["version"]) > parse(APP_VERSION)
    with _checked_lock:
        _checked.clear()
        _checked.update(version=release["version"], at=time.monotonic())
    return release


@router.post("/install", status_code=202)
def install(data: InstallInput, _admin: Admin):
    target = parse(data.version)
    if not target or data.version.startswith("v"):
        raise HTTPException(422, "Ungültige Versionsnummer.")
    with _checked_lock:
        checked = dict(_checked)
    if (
        checked.get("version") != data.version
        or time.monotonic() - checked.get("at", 0) > CHECK_VALID_SECONDS
    ):
        raise HTTPException(409, "Bitte zuerst erneut nach Updates suchen.")
    if target <= parse(APP_VERSION):
        raise HTTPException(409, "Diese Version ist bereits installiert oder älter.")
    return status_data(updater_call("/update", {"version": data.version}))
