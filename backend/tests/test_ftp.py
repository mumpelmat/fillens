import logging
import os
import time
from threading import Event, Thread

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from sqlalchemy import select

from app.db import SessionLocal
from app.importer import import_camera
from app.models import Camera, FTPConnection, ImageBuffer, ImportRecord
from app.security import encrypt
from test_portal import jpeg


@pytest.mark.parametrize("legacy", [False, True], ids=["MLSD", "NLST-Fallback"])
def test_recursive_ftp_import_and_repeated_scans(container, tmp_path, legacy):
    remote = tmp_path / "remote"
    nested = remote / "2026" / "10" / "31" / "events"
    nested.mkdir(parents=True)
    for i in range(5):
        (nested / f"image-{i}.jpg").write_bytes(jpeg(20 + i * 30))
    (nested / "same-content.jpg").write_bytes(jpeg(20))
    (nested / "invalid.jpg").write_bytes(b"not an image")
    (nested / "damaged.jpg").write_bytes(jpeg(99)[:100])
    (nested / "ignore.txt").write_text("ignore")
    # Gleiche FTP-Zeitstempel verhindern einen zufälligen Sekundenwechsel
    # zwischen Dateierzeugungen und halten den Deduplizierungsfall stabil.
    stamp = time.time()-60
    for path in nested.iterdir():
        os.utime(path, (stamp, stamp))
    authorizer = DummyAuthorizer()
    authorizer.add_user("camera", "test-password", str(remote), perm="elr")

    class Handler(FTPHandler):
        def ftp_MLSD(self, path):
            if legacy:
                self.respond("500 MLSD unsupported")
            else:
                super().ftp_MLSD(path)

    Handler.authorizer = authorizer
    logging.getLogger("pyftpdlib").setLevel(logging.CRITICAL)
    server = FTPServer(("127.0.0.1", 0), Handler)
    port = server.socket.getsockname()[1]
    stop = Event()

    def serve():
        try:
            while not stop.is_set():
                server.ioloop.loop(timeout=0.05, blocking=False)
        finally:
            server.close_all()

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        with SessionLocal() as db:
            connection = FTPConnection(
                name="Local test",
                host="127.0.0.1",
                port=port,
                username="camera",
                encrypted_password=encrypt("test-password"),
            )
            db.add(connection)
            db.flush()
            camera = db.get(Camera, container["camera"]["id"])
            camera.ftp_connection_id = connection.id
            db.commit()
            assert import_camera(db, camera, {}) == 5
            assert (
                camera.last_error is not None
            )  # Ungültiges Bild meldet einen Fehler, ohne gute Bilder zu blockieren.
            assert len(list(db.scalars(select(ImageBuffer)))) == 5
            assert len(list(db.scalars(select(ImportRecord)))) == 6
            assert import_camera(db, camera, {}) == 0
            assert len(list(db.scalars(select(ImageBuffer)))) == 5
    finally:
        stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
