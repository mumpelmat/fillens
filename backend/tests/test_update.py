import json
import urllib.error

import pytest
from app import update_api
from app.config import settings
from app.version import APP_VERSION
from conftest import authenticate


def bump(version, minor=1):
    major, mid, patch = map(int, version.split("."))
    return f"{major}.{mid + minor}.0"


NEWER = bump(APP_VERSION)


@pytest.fixture
def remote(monkeypatch):
    calls = []
    state = {"release": {"tag_name": f"v{NEWER}", "name": f"Fillens {NEWER}", "body": "- Neu", "html_url": "https://github.com/mumpelmat/fillens/releases/tag/v" + NEWER, "published_at": "2026-11-20T10:00:00Z"}, "updater": None}

    def fake(request, timeout):
        calls.append(request)
        if request.full_url == settings().release_api_url:
            if isinstance(state["release"], Exception):
                raise state["release"]
            return state["release"]
        if isinstance(state["updater"], Exception):
            raise state["updater"]
        if request.full_url.endswith("/status"):
            return {"state": "idle", "steps": [], "secret": "nicht weitergeben"}
        return {"state": "running", "version": json.loads(request.data)["version"], "steps": []}

    monkeypatch.setattr(update_api, "_json", fake)
    monkeypatch.setattr(settings(), "updater_url", "http://updater:8080")
    monkeypatch.setattr(settings(), "updater_token", "x" * 40)
    update_api._checked.clear()
    return state, calls


def test_health_reports_version(client):
    assert client.get("/api/health").json() == {"status": "ok", "version": APP_VERSION}


def test_only_admins(client, remote):
    authenticate(client, "user@example.test", "Leser-passwort-123")
    assert client.get("/api/system/update").status_code == 403
    assert client.post("/api/system/update/check").status_code == 403
    assert client.post("/api/system/update/install", json={"version": NEWER}).status_code == 403


def test_csrf_required(admin, remote):
    del admin.headers["x-csrf-token"]
    assert admin.post("/api/system/update/check").status_code == 403


def test_state_without_updater(admin, monkeypatch):
    monkeypatch.setattr(settings(), "updater_url", "")
    body = admin.get("/api/system/update").json()
    assert body == {"current_version": APP_VERSION, "updater": "missing", "status": None}
    assert admin.post("/api/system/update/install", json={"version": NEWER}).status_code == 409


def test_state_filters_updater_status(admin, remote):
    body = admin.get("/api/system/update").json()
    assert body["updater"] == "ready"
    assert "secret" not in body["status"]
    _, calls = remote
    assert calls[-1].headers["Authorization"] == "Bearer " + "x" * 40


def test_check_and_install(admin, remote):
    state, calls = remote
    release = admin.post("/api/system/update/check").json()
    assert release["version"] == NEWER and release["update_available"] is True
    assert release["current_version"] == APP_VERSION
    response = admin.post("/api/system/update/install", json={"version": NEWER})
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "running"
    assert json.loads(calls[-1].data) == {"version": NEWER}


def test_install_requires_fresh_check_of_same_version(admin, remote):
    assert admin.post("/api/system/update/install", json={"version": NEWER}).status_code == 409
    admin.post("/api/system/update/check")
    other = bump(APP_VERSION, 2)
    assert admin.post("/api/system/update/install", json={"version": other}).status_code == 409


@pytest.mark.parametrize("version", ["latest", "v1.0.0", "1.0", "1.0.0;rm"])
def test_install_rejects_invalid_versions(admin, remote, version):
    admin.post("/api/system/update/check")
    assert admin.post("/api/system/update/install", json={"version": version}).status_code == 422


def test_no_update_for_same_version(admin, remote):
    state, _ = remote
    state["release"]["tag_name"] = "v" + APP_VERSION
    assert admin.post("/api/system/update/check").json()["update_available"] is False
    assert admin.post("/api/system/update/install", json={"version": APP_VERSION}).status_code == 409


def test_github_errors(admin, remote):
    state, _ = remote
    state["release"] = OSError("offline")
    assert admin.post("/api/system/update/check").status_code == 502
    state["release"] = {"tag_name": "nightly"}
    assert admin.post("/api/system/update/check").status_code == 502
    state["release"] = ValueError("kein JSON")
    assert "ungültige Antwort" in admin.post("/api/system/update/check").json()["detail"]
    state["release"] = ["liste"]
    assert admin.post("/api/system/update/check").status_code == 502


def test_updater_errors(admin, remote):
    state, _ = remote
    admin.post("/api/system/update/check")
    state["updater"] = OSError("weg")
    assert admin.post("/api/system/update/install", json={"version": NEWER}).status_code == 502
    assert admin.get("/api/system/update").json()["updater"] == "unreachable"

    class Body:
        def read(self, n):
            return b'{"detail": "Dieses Update \\u00e4ndert die compose.yaml"}'

        def close(self):
            pass

    state["updater"] = urllib.error.HTTPError("http://updater:8080/update", 409, "x", {}, Body())
    response = admin.post("/api/system/update/install", json={"version": NEWER})
    assert response.status_code == 409
    assert "compose.yaml" in response.json()["detail"]
