import logging
import os
import socket
import ssl
from datetime import UTC, datetime, timedelta
from ftplib import error_perm, error_temp
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import select
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from app.config import settings
from app.db import SessionLocal, utcnow
from app.ftp_errors import ImportFailure, import_failure
from app.ftp_scan import ScanBudget, recent_paths, validate_pattern
from app.importer import import_camera
from app.models import Camera, FTPConnection, ImageBuffer, WorkerStatus
from app.security import encrypt
from app.worker import cycle, ProgressReporter
from test_portal import jpeg


@pytest.fixture
def ftp_tree(tmp_path):
    root = tmp_path / "ftp"
    root.mkdir()
    calls = []
    authorizer = DummyAuthorizer()
    authorizer.add_user("camera", "test-password", str(root), perm="elr")

    class Handler(FTPHandler):
        def ftp_MLSD(self, path):
            calls.append(Path(path).relative_to(root).as_posix())
            return super().ftp_MLSD(path)

    Handler.authorizer = authorizer
    logging.getLogger("pyftpdlib").setLevel(logging.CRITICAL)
    server = FTPServer(("127.0.0.1", 0), Handler)
    port = server.socket.getsockname()[1]
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
        yield root, port, calls
    finally:
        stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive()


def image(root, path, value, time):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(jpeg(value))
    os.utime(target, (time.timestamp(), time.timestamp()))


def camera_connection(db, container, port, **config):
    connection = FTPConnection(
        name="Test",
        host="127.0.0.1",
        port=port,
        username="camera",
        encrypted_password=encrypt("test-password"),
    )
    db.add(connection)
    db.flush()
    camera = db.get(Camera, container["camera"]["id"])
    camera.ftp_connection_id = connection.id
    for key, value in config.items():
        setattr(camera, key, value)
    db.commit()
    return camera


def test_auto_detection_caches_prefix_and_skips_archive(
    container, ftp_tree, monkeypatch
):
    root, port, calls = ftp_tree
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    monkeypatch.setattr("app.ftp_import.utcnow", lambda: now)
    for i in range(4):
        image(
            root,
            f"camera/events/2026/10/31/{i}.jpg",
            30 + i * 30,
            now - timedelta(minutes=10 - i),
        )
    for i in range(100):
        image(root, f"camera/events/2020/01/01/{i}.jpg", 10, now - timedelta(days=2000))
    phases = []
    with SessionLocal() as db:
        camera = camera_connection(db, container, port)
        assert (
            import_camera(db, camera, {}, lambda phase, *_: phases.append(phase)) == 4
        )
        assert camera.detected_date_pattern == "camera/events/YYYY/MM/TT"
        assert "camera/events/2020/01/01" not in calls
        assert {"discover", "download", "store"} <= set(phases)
        calls.clear()
        image(
            root, "camera/events/2026/10/31/new.jpg", 190, now - timedelta(seconds=60)
        )
        assert import_camera(db, camera, {}) == 1
        assert "." not in calls and "camera/events" not in calls
        assert "camera/events/2020/01/01" not in calls
        assert len(list(db.scalars(select(ImageBuffer)))) == 5


def test_year_rollover_and_explicit_suffix(container, ftp_tree, monkeypatch):
    root, port, calls = ftp_tree
    clock = [datetime(2026, 12, 31, 12, tzinfo=UTC)]
    monkeypatch.setattr("app.ftp_import.utcnow", lambda: clock[0])
    image(root, "frames/2026/12/31/images/one.jpg", 30, clock[0] - timedelta(minutes=5))
    with SessionLocal() as db:
        camera = camera_connection(
            db,
            container,
            port,
            scan_mode="date",
            date_pattern="frames/YYYY/MM/TT/images",
        )
        assert import_camera(db, camera, {}) == 1
        clock[0] = datetime(2027, 1, 1, 12, tzinfo=UTC)
        image(
            root,
            "frames/2027/01/01/images/new.jpg",
            90,
            clock[0] - timedelta(minutes=5),
        )
        assert import_camera(db, camera, {}) == 1
        assert "frames/2027/01/01/images" in calls


@pytest.mark.parametrize("camera_root,pattern", [("2026", "MM/TT"), ("2026/10", "TT")])
def test_date_detection_when_root_contains_year(
    container, ftp_tree, monkeypatch, camera_root, pattern
):
    root, port, calls = ftp_tree
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    monkeypatch.setattr("app.ftp_import.utcnow", lambda: now)
    image(root, "2026/10/31/current.jpg", 90, now - timedelta(minutes=5))
    for index in range(100):
        image(root, f"2026/10/01/{index}.jpg", 30, now - timedelta(days=30))
    with SessionLocal() as db:
        camera = camera_connection(db, container, port, ftp_root=camera_root)
        assert import_camera(db, camera, {}) == 1
        assert camera.detected_date_pattern == pattern
        assert "2026/10/01" not in calls


def test_partial_pattern_respects_fixed_root():
    assert validate_pattern("MM/DD") == "MM/TT"
    now = datetime(2027, 1, 1, 12, tzinfo=UTC)
    with pytest.raises(ImportFailure) as error:
        recent_paths("MM/TT", "Europe/Berlin", 3, now, "/2026")
    assert error.value.code == "date_root_outdated"
    assert recent_paths("MM/TT", "Europe/Berlin", 3, now, "/2027") == ["01/01"]


def test_control_scan_finds_late_file(container, ftp_tree, monkeypatch):
    root, port, calls = ftp_tree
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    monkeypatch.setattr("app.ftp_import.utcnow", lambda: now)
    image(root, "2026/10/31/one.jpg", 30, now - timedelta(minutes=5))
    with SessionLocal() as db:
        camera = camera_connection(db, container, port, scan_mode="date")
        assert import_camera(db, camera, {}) == 1
        image(root, "2020/01/01/late.jpg", 130, now - timedelta(minutes=1))
        camera.last_full_scan_attempt_at = now - timedelta(hours=25)
        db.commit()
        assert import_camera(db, camera, {}) == 1
        assert "2020/01/01" in calls
        assert (
            camera.last_full_scan_at is not None
            and camera.last_scan_kind == "date_control"
        )


def test_control_limit_preserves_fast_import(container, ftp_tree, monkeypatch):
    root, port, calls = ftp_tree
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    monkeypatch.setattr("app.ftp_import.utcnow", lambda: now)
    monkeypatch.setattr(settings(), "max_scan_files", 25)
    image(root, "2026/10/31/current.jpg", 80, now - timedelta(minutes=5))
    for i in range(30):
        image(root, f"2020/01/01/{i}.jpg", 20, now - timedelta(days=1000))
    with SessionLocal() as db:
        camera = camera_connection(
            db,
            container,
            port,
            scan_mode="date",
            last_full_scan_attempt_at=now - timedelta(hours=25),
        )
        assert import_camera(db, camera, {}) == 1
        assert camera.last_error_code == "scan_limit"
        assert "Kontrollscan" in camera.last_error
        assert db.scalar(select(ImageBuffer.id))
        calls.clear()
        assert import_camera(db, camera, {}) == 0
        assert "2020/01/01" not in calls


def test_control_does_not_walk_entire_unchanged_image_archive(
    container, ftp_tree, monkeypatch
):
    root, port, calls = ftp_tree
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    monkeypatch.setattr("app.ftp_import.utcnow", lambda: now)
    for i in range(3):
        image(root, f"2026/10/31/{i}.jpg", 60 + i * 30, now - timedelta(minutes=10 - i))
    archive_dirs = set()
    for day in range(1, 5):
        path = f"2026/01/{day:02}"
        archive_dirs.add(path)
        for i in range(20):
            image(root, f"{path}/{i}.jpg", 20, now - timedelta(days=200))
        stamp = (now - timedelta(days=100)).timestamp()
        os.utime(root / path, (stamp, stamp))
    with SessionLocal() as db:
        camera = camera_connection(db, container, port, scan_mode="date")
        assert import_camera(db, camera, {}) == 3
        camera.last_full_scan_attempt_at = now - timedelta(hours=25)
        db.commit()
        calls.clear()
        assert import_camera(db, camera, {}) == 0
        assert len(set(calls) & archive_dirs) == 3
        assert camera.last_error is None


@pytest.mark.parametrize(
    "exc,operation,code",
    [
        (socket.gaierror("secret-server-response"), "connect", "dns"),
        (
            ConnectionRefusedError("secret-server-response"),
            "connect",
            "connection_refused",
        ),
        (TimeoutError("secret-server-response"), "download", "timeout"),
        (
            ssl.SSLCertVerificationError("secret-server-response"),
            "connect",
            "tls_certificate",
        ),
        (error_perm("530 secret-server-response"), "connect", "authentication"),
        (error_perm("550 secret-server-response"), "root", "root_unavailable"),
        (error_perm("550 secret-server-response"), "download", "file_unavailable"),
        (error_temp("425 secret-server-response"), "download", "data_connection"),
    ],
)
def test_error_codes_do_not_echo_server_text(exc, operation, code):
    result = import_failure(exc, operation)
    assert result.code == code and "secret-server-response" not in result.message


def test_pattern_validation_and_timezone_new_year():
    assert validate_pattern("frames/YYYY/MM/DD") == "frames/YYYY/MM/TT"
    for pattern in ["../YYYY/MM/TT", "/YYYY/MM/TT", "YYYY/TT/MM", "YYYY/MM"]:
        with pytest.raises(ValueError):
            validate_pattern(pattern)
    assert recent_paths(
        "YYYY/MM/TT", "Europe/Berlin", 2, datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    ) == ["2027/01/01", "2026/12/31"]


def test_progress_is_visible_and_stale_work_is_not_busy(admin, container):
    with SessionLocal() as db:
        db.add(WorkerStatus(id="importer"))
        db.commit()
    reporter = ProgressReporter(container["camera"]["id"])
    reporter("download", 42, 2)
    status = admin.get("/api/system/status").json()
    assert status["worker_busy"] is True and status["worker_phase"] == "download"
    assert status["scanned_entries"] == 42 and status["imported_count"] == 2
    with SessionLocal() as db:
        status_row = db.get(WorkerStatus, "importer")
        status_row.heartbeat_at = utcnow() - timedelta(hours=1)
        db.commit()
    status = admin.get("/api/system/status").json()
    assert status["worker_busy"] is False and status["worker_phase"] == "offline"


def test_worker_records_root_failure_and_returns_idle(container, ftp_tree):
    _, port, _ = ftp_tree
    with SessionLocal() as db:
        camera_connection(db, container, port, ftp_root="not-existing")
    cycle({})
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        status = db.get(WorkerStatus, "importer")
        assert camera.last_error_code == "root_unavailable"
        assert status.phase == "idle" and status.active_camera_id is None
        assert "550" in status.last_error
