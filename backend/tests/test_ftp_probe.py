import multiprocessing
import ssl
import time
from contextlib import contextmanager
from threading import Event, Thread

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.ftp_probe import _probe
from app.models import FTPConnection, ImageBuffer, ImportRecord, Measurement, Camera
from conftest import authenticate


@contextmanager
def ftp_server(tmp_path, legacy=False):
    root = tmp_path / "remote"
    camera = root / "base" / "camera"
    (camera / "nested").mkdir(parents=True)
    (camera / "folder.jpg").mkdir()
    (root / "base" / "empty").mkdir()
    for name in ["one.JPG", "two.jpeg", "three.PNG", "four.WeBp", "other.txt"]:
        (camera / name).write_bytes(b"Only filenames are counted, never downloaded")
    (camera / "nested" / "deep.jpg").write_bytes(b"not counted")
    (root / "base" / "root.jpg").write_bytes(b"one root image")
    authorizer = DummyAuthorizer()
    authorizer.add_user("probe", "probe-secret", str(root), perm="elr")
    downloads = []

    class Handler(FTPHandler):
        auth_failed_timeout = 0.01

        def ftp_MLSD(self, path):
            if legacy:
                self.respond("500 MLSD unsupported")
            else:
                super().ftp_MLSD(path)

        def ftp_RETR(self, file):
            downloads.append(file)
            self.respond("550 Downloads forbidden during test")

    Handler.authorizer = authorizer
    server = FTPServer(("127.0.0.1", 0), Handler)
    stop = Event()

    def serve():
        try:
            while not stop.is_set():
                server.ioloop.loop(timeout=0.02, blocking=False)
        finally:
            server.close_all()

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield {
            "name": "Probe", "host": "127.0.0.1", "port": server.socket.getsockname()[1],
            "username": "probe", "password": "probe-secret", "protocol": "ftp", "root_path": "/base",
        }, downloads
    finally:
        stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def snapshot():
    with SessionLocal() as db:
        return {
            "connections": [(c.id, c.host, c.root_path, c.encrypted_password) for c in db.scalars(select(FTPConnection))],
            "cameras": [(c.id, c.ftp_connection_id, c.ftp_root, c.last_scan_at, c.last_error) for c in db.scalars(select(Camera))],
            "counts": [db.scalar(select(func.count()).select_from(model)) for model in (ImageBuffer, ImportRecord, Measurement)],
        }


@pytest.mark.parametrize("legacy", [False, True], ids=["MLSD", "NLST"])
def test_first_level_with_saved_password_and_camera_path(admin, container, tmp_path, legacy):
    with ftp_server(tmp_path, legacy) as (data, downloads):
        stored = admin.post("/api/ftp-connections", json=data).json()
        before = snapshot()
        # Current form values override the saved root; omitted password uses the saved secret.
        response = admin.post("/api/ftp-connections/test", json={
            **data, "password": None, "connection_id": stored["id"], "root_path": "/", "ftp_root": "base/camera",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["path"] == "/base/camera"
        assert result["connected"] and result["listed"]
        assert (result["folders"], result["images"]) == (2, 4)
        assert "probe-secret" not in response.text
        assert snapshot() == before
        assert downloads == []


def test_unsaved_connection_root_empty_and_errors(admin, tmp_path, monkeypatch):
    with ftp_server(tmp_path) as (data, downloads):
        before = snapshot()
        root = admin.post("/api/ftp-connections/test", json=data).json()
        assert (root["folders"], root["images"]) == (2, 1)
        empty = admin.post("/api/ftp-connections/test", json={**data, "ftp_root": "empty"}).json()
        assert empty["listed"] and (empty["folders"], empty["images"]) == (0, 0)
        for override, code in [({"password": "wrong-secret"}, "authentication"), ({"ftp_root": "missing"}, "root_unavailable")]:
            response = admin.post("/api/ftp-connections/test", json={**data, **override})
            result = response.json()
            assert result["error_code"] == code
            assert result["listed"] is False
            assert result["images"] is None and result["folders"] is None
            assert "wrong-secret" not in response.text
        monkeypatch.setattr(settings(), "max_scan_files", 1)
        limited = admin.post("/api/ftp-connections/test", json=data).json()
        assert limited["error_code"] == "scan_limit"
        assert limited["connected"] and not limited["listed"]
        assert limited["images"] is None
        assert snapshot() == before
        assert downloads == []


def test_test_route_roles_csrf_and_input_validation(admin):
    data = {"name": "Probe", "host": "127.0.0.1", "username": "probe", "password": "secret"}
    token = admin.headers.pop("x-csrf-token")
    assert admin.post("/api/ftp-connections/test", json=data).status_code == 403
    admin.headers["x-csrf-token"] = token
    for override in [{"ftp_root": "../outside"}, {"ftp_root": "/absolute"}, {"root_path": "/../outside"}, {"port": 0}, {"password": None}]:
        response = admin.post("/api/ftp-connections/test", json={**data, **override})
        assert response.status_code == 422
        assert "secret" not in response.text
    authenticate(admin, "user@example.test", "Leser-passwort-123")
    assert admin.post("/api/ftp-connections/test", json=data).status_code == 403


def test_hard_deadline_terminates_child(admin, monkeypatch):
    from app import ftp_probe
    monkeypatch.setattr(ftp_probe, "TEST_TIMEOUT", 0.05)
    children = {p.pid for p in multiprocessing.active_children()}
    start = time.monotonic()
    response = admin.post("/api/ftp-connections/test", json={
        "name": "Deadline", "host": "127.0.0.1", "port": 1, "username": "test", "password": "secret",
    })
    assert time.monotonic() - start < 3
    result = response.json()
    assert result["error_code"] == "timeout"
    assert result["folders"] is None and result["images"] is None
    assert {p.pid for p in multiprocessing.active_children()} == children


def test_tls_failure_is_safe_and_incomplete(monkeypatch):
    from app import importer
    class Sender:
        def __init__(self): self.messages = []
        def send(self, value): self.messages.append(value)
        def close(self): pass
    def fail(_connection):
        raise ssl.SSLCertVerificationError("secret raw server details")
    monkeypatch.setattr(importer, "connect", fail)
    sender = Sender()
    _probe(sender, {"protocol": "ftps"}, "/camera", 100)
    result = sender.messages[-1]
    assert result["error_code"] == "tls_certificate"
    assert not result["connected"] and not result["listed"]
    assert "secret" not in str(result)


def test_unclassifiable_nlst_entry_is_not_reported_as_an_image():
    from ftplib import error_perm
    from app.ftp_errors import ImportFailure
    from app.ftp_scan import ScanBudget, listing
    class FTP:
        def cwd(self, path):
            if path != "/camera": raise error_perm("550 denied")
        def retrlines(self, command, callback):
            if command == "MLSD": raise error_perm("500 unsupported")
            callback("unreadable-folder.jpg")
        def voidcmd(self, command): pass
        def size(self, path): raise error_perm("550 denied")
    with pytest.raises(ImportFailure, match="Keine vollständige Zählung"):
        listing(FTP(), "/camera", ScanBudget(), strict_types=True)
