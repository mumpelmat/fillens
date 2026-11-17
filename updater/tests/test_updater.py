import hashlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater as u  # noqa: E402

COMPOSE = b"name: fillens\nservices: {}\n"


class FakeDocker:
    def __init__(self, root, fail=None):
        self.root = root
        self.calls = []
        self.fail = fail
        self.healthy = True

    def context(self):
        return {"host_dir": "/volume1/docker/fillens", "project": "fillens", "files": [self.root / "compose.yaml"]}

    def compose(self, *args, stdin=None, stdout=None, env=None, timeout=None):
        self.calls.append((args, env))
        if self.fail and args[0] == self.fail:
            raise u.UpdateError(f"{args[0]} fehlgeschlagen")
        if args[:4] == ("exec", "-T", "postgres", "pg_dump"):
            stdout.write(b"PGDMP-test")
        if args[:4] == ("exec", "-T", "postgres", "pg_restore"):
            return "; Archiv\n"
        return ""

    def services(self):
        health = "healthy" if self.healthy else "starting"
        return {name: {"State": "running", "Health": health} for name in u.APP_SERVICES}

    def recreate_self_detached(self):
        self.calls.append((("recreate-self",), None))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "PROJECT", tmp_path)
    (tmp_path / "compose.yaml").write_bytes(COMPOSE)
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=geheim\r\nFILLENS_VERSION=0.8.0\r\nPORT=3000\r\n")
    return tmp_path


def manifest(version, compose=COMPOSE):
    return {"version": version, "compose_sha256": hashlib.sha256(compose).hexdigest()}


def make(project, fail=None, running="0.8.0", after="0.8.1", release=None):
    docker = FakeDocker(project, fail)
    state = {"current": running}

    def version():
        return state["current"]

    def compose(*args, **kwargs):
        result = FakeDocker.compose(docker, *args, **kwargs)
        if args[:2] == ("up", "-d") and "frontend" in args:
            state["current"] = after
        return result

    docker.compose = compose
    fetch = (lambda url: release) if release is not None else (lambda url: manifest(url.split("/v")[-1].split("/")[0]))
    return u.Updater(docker=docker, status=u.Status(project), fetch=fetch, version=version, sleep=lambda s: None), docker


def test_set_env_version_replaces_and_keeps_rest():
    text = "A=1\r\nFILLENS_VERSION=0.7.0\r\n# Kommentar\r\nB=2"
    assert u.set_env_version(text, "0.8.0") == "A=1\r\nFILLENS_VERSION=0.8.0\r\n# Kommentar\r\nB=2"
    assert u.set_env_version("A=1", "0.8.0") == "A=1\nFILLENS_VERSION=0.8.0\n"
    assert u.env_version('FILLENS_VERSION="0.7.1" # fest') == "0.7.1"
    with pytest.raises(u.UpdateError):
        u.set_env_version("", "0.8.0\nPOSTGRES_PASSWORD=x")


@pytest.mark.parametrize("value", ["latest", "0.8", "0.8.0-rc1", "../0.8.0", "", None, "1.2.3 "])
def test_invalid_versions(value):
    with pytest.raises(u.UpdateError):
        u.parse_version(value)


def test_successful_update(project):
    updater, docker = make(project)
    data = updater.status.start("0.8.1", "0.8.0")
    updater.run(data)
    status = updater.status.read()
    assert status["state"] == "succeeded", status
    assert (project / ".env").read_bytes().decode() == "POSTGRES_PASSWORD=geheim\r\nFILLENS_VERSION=0.8.1\r\nPORT=3000\r\n"
    backup = project / status["backup_dir"]
    assert (backup / "fillens.dump").read_bytes() == b"PGDMP-test"
    assert (backup / ".env").exists() and (backup / "compose.yaml").exists()
    commands = [c[0][0] for c in docker.calls]
    assert commands.index("pull") < commands.index("exec") < commands.index("stop") < commands.index("up")
    pull = next(c for c in docker.calls if c[0][0] == "pull")
    assert pull[1] == {"FILLENS_VERSION": "0.8.1"}
    assert docker.calls[-1][0] == ("recreate-self",)


def test_pull_failure_changes_nothing(project):
    updater, docker = make(project, fail="pull")
    data = updater.status.start("0.8.1", "0.8.0")
    updater.run(data)
    status = updater.status.read()
    assert status["state"] == "failed"
    assert "unverändert" in status["error"]
    assert status["error"].startswith("Images für 0.8.1 konnten nicht geladen werden")
    assert "FILLENS_VERSION=0.8.0" in (project / ".env").read_text()
    assert not any(c[0][0] == "stop" for c in docker.calls)


def test_dump_failure_aborts_before_version_change(project):
    updater, docker = make(project, fail="exec")
    updater.run(updater.status.start("0.8.1", "0.8.0"))
    status = updater.status.read()
    assert status["state"] == "failed"
    assert status["error"].startswith("Datenbanksicherung fehlgeschlagen")
    assert "FILLENS_VERSION=0.8.0" in (project / ".env").read_text()
    assert not any(c[0][0] == "stop" for c in docker.calls)


def test_unhealthy_after_change_fails_with_backup_hint(project, monkeypatch):
    monkeypatch.setattr(u, "HEALTH_TIMEOUT", 0)
    updater, _ = make(project, after="0.8.0")
    updater.run(updater.status.start("0.8.1", "0.8.0"))
    status = updater.status.read()
    assert status["state"] == "failed"
    assert "UPDATE.md" in status["error"]
    assert "FILLENS_VERSION=0.8.1" in (project / ".env").read_text()


def test_preflight_rules(project):
    updater, _ = make(project)
    assert updater.preflight("0.8.1") == "0.8.0"
    with pytest.raises(u.UpdateError, match="nicht neuer"):
        updater.preflight("0.8.0")
    changed, _ = make(project, release=manifest("0.9.0", b"anders"))
    with pytest.raises(u.UpdateError, match="compose.yaml"):
        changed.preflight("0.9.0")

    def missing(url):
        raise urllib.error.HTTPError(url, 404, "nicht gefunden", {}, None)

    gone = u.Updater(docker=FakeDocker(project), status=u.Status(project), fetch=missing, version=lambda: "0.8.0")
    with pytest.raises(u.UpdateError, match="update.json"):
        gone.preflight("0.8.1")


def test_http_api_requires_token(project):
    updater, _ = make(project)
    token = "t" * 40
    server = ThreadingHTTPServer(("127.0.0.1", 0), u.make_handler(updater, token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, auth=token):
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        assert call("/status", auth="falsch")[0] == 401
        assert call("/update", {"version": "0.8.1"}, auth="")[0] == 401
        assert call("/update", {"version": "latest"})[0] == 409
        status, body = call("/update", {"version": "0.8.1"})
        assert status == 202 and body["state"] == "running"
        for _ in range(100):
            if updater.status.read()["state"] != "running":
                break
            threading.Event().wait(0.02)
        assert call("/status")[1]["state"] == "succeeded"
    finally:
        server.shutdown()
