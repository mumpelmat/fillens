"""FTP-Import, Deduplizierung und zeitliche Bildaufbewahrung."""

import hashlib
import io
import logging
import os
import ssl
import warnings
from datetime import UTC, datetime, timedelta
from ftplib import FTP, FTP_TLS
from uuid import uuid4
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import aware, utcnow
from .models import Camera, FTPConnection, ImageBuffer, ImportRecord
from .security import decrypt
from .services import image_path
from .vision import transform_key

logger = logging.getLogger("fillens.importer")
Image.MAX_IMAGE_PIXELS = 25_000_000
from .ftp_errors import ImportFailure, import_failure
from .ftp_scan import RemoteFile, remote_time


def connect(connection: FTPConnection, *, deadline=None) -> FTP:
    import time

    timeout = (
        settings().ftp_timeout_seconds
        if deadline is None
        else min(settings().ftp_timeout_seconds, max(0.1, deadline - time.monotonic()))
    )

    class DeadlineMixin:
        def check_budget(self):
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ImportFailure(
                        "training_budget", "Trainingsabruf wird fortgesetzt."
                    )
                self.timeout = min(settings().ftp_timeout_seconds, remaining)
                if self.sock:
                    self.sock.settimeout(self.timeout)

        def putcmd(self, line):
            self.check_budget()
            return super().putcmd(line)

        def getresp(self):
            self.check_budget()
            return super().getresp()

    class BudgetFTP(DeadlineMixin, FTP):
        pass

    class BudgetTLS(DeadlineMixin, FTP_TLS):
        pass

    ftp = (
        BudgetTLS(context=ssl.create_default_context(), timeout=timeout)
        if connection.protocol == "ftps"
        else BudgetFTP(timeout=timeout)
    )
    try:
        ftp.connect(connection.host, connection.port)
        ftp.login(connection.username, decrypt(connection.encrypted_password))
        if isinstance(ftp, FTP_TLS):
            ftp.prot_p()
        ftp.voidcmd("TYPE I")
        return ftp
    except Exception as exc:
        ftp.close()
        raise import_failure(exc, "connect") from exc


def prepare_image(
    raw: bytes, camera: Camera, remote: RemoteFile
) -> tuple[bytes, datetime, str]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Bildformat nicht unterstützt")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            captured = None
            exif = source.getexif()
            original = exif.get(36867)
            if not original:
                try:
                    original = exif.get_ifd(34665).get(36867)
                except (KeyError, TypeError):
                    original = None
            if original:
                try:
                    captured = (
                        datetime.strptime(str(original), "%Y:%m:%d %H:%M:%S")
                        .replace(tzinfo=ZoneInfo(camera.timezone))
                        .astimezone(UTC)
                    )
                except ValueError:
                    pass
            timestamp_source = "exif" if captured else "ftp_mtime"
            captured = captured or remote_time(remote.modified)
            if captured is None:
                captured, timestamp_source = utcnow(), "import_time"
            image = ImageOps.exif_transpose(source).convert("RGB")
            if camera.image_rotation:
                image = image.rotate(-camera.image_rotation, expand=True)
            if camera.image_flip:
                image = ImageOps.mirror(image)
            output = io.BytesIO()
            image.save(
                output, format="JPEG", quality=90
            )  # EXIF und sonstige Metadaten entfernen.
            return output.getvalue(), captured, timestamp_source


def reconcile(db: Session, camera: Camera) -> None:
    """Zeitliche Aufbewahrung; neueste Vorschau unabhängig vom Alter erhalten."""
    from .training_store import collect_buffer, cleanup_deferred, cleanup_discarded

    camera = db.get(Camera, camera.id, with_for_update=True, populate_existing=True)
    collect_buffer(db, camera)
    cutoff = utcnow() - timedelta(days=camera.image_retention_days)
    latest = db.scalar(select(ImageBuffer.id).where(ImageBuffer.camera_id == camera.id).order_by(
        ImageBuffer.captured_at.desc(), ImageBuffer.imported_at.desc()).limit(1))
    for row in db.scalars(select(ImageBuffer).where(
        ImageBuffer.camera_id == camera.id, ImageBuffer.id != latest,
        ImageBuffer.imported_at < cutoff,
        # Noch nicht gesicherte Lernbilder zunächst nachholen.
        ImageBuffer.learning_collected.is_(True) if camera.learning_enabled else True,
    ).limit(500)):
        image_path(row).unlink(missing_ok=True)
        db.delete(row)
    directory = settings().data_dir / "buffer" / camera.id
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_file() and not db.scalar(select(ImageBuffer.id).where(
            ImageBuffer.camera_id == camera.id, ImageBuffer.filename == path.name
        ).limit(1)):
            path.unlink(missing_ok=True)
    for row in db.scalars(select(ImageBuffer).where(ImageBuffer.camera_id == camera.id).execution_options(yield_per=200)):
        if not image_path(row).is_file():
            db.delete(row)
    cleanup_deferred(db, camera.id)
    cleanup_discarded(db, camera.id)
    db.commit()


def ingest(db: Session, camera: Camera, remote: RemoteFile, raw: bytes) -> bool:
    camera = db.get(Camera, camera.id, with_for_update=True, populate_existing=True)
    if db.scalar(
        select(ImportRecord.id).where(
            ImportRecord.camera_id == camera.id,
            ImportRecord.fingerprint == remote.fingerprint,
        )
    ):
        return False
    if len(raw) != remote.size or len(raw) > settings().max_image_bytes:
        raise ImportFailure(
            "image_incomplete",
            "Bilddownload ist unvollständig oder überschreitet das Größenlimit.",
        )
    file_hash = hashlib.sha256(raw).hexdigest()
    duplicate = (
        db.scalar(
            select(ImportRecord.id).where(
                ImportRecord.camera_id == camera.id, ImportRecord.file_hash == file_hash
            )
        )
        is not None
    )
    record = ImportRecord(
        camera_id=camera.id,
        fingerprint=remote.fingerprint,
        remote_path=remote.path,
        remote_size=remote.size,
        remote_modified=remote.modified[:64],
        file_hash=file_hash,
        status="duplicate" if duplicate else "imported",
    )
    if duplicate:
        db.add(record)
        db.commit()
        return False
    try:
        content, captured, source = prepare_image(raw, camera, remote)
    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ImportFailure(
            "image_invalid",
            "Bilddatei ist beschädigt, nicht lesbar oder überschreitet das Pixel-Limit.",
        ) from exc
    directory = settings().data_dir / "buffer" / camera.id
    directory.mkdir(parents=True, exist_ok=True)
    filename = uuid4().hex + ".jpg"
    target = directory / filename
    # Ein einzelner Worker besitzt die Schreibsperre. Abgebrochene Dateien räumt reconcile auf.
    try:
        with target.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        db.add(record)
        db.flush()
        image = ImageBuffer(
                camera_id=camera.id,
                import_id=record.id,
                filename=filename,
                captured_at=captured,
                timestamp_source=source,
                transform_key=transform_key(camera.image_rotation, camera.image_flip),
                size_bytes=len(content),
            )
        db.add(image)
        camera.last_import_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    try:
        from .training_store import collect_buffer

        collect_buffer(db, camera)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("event=learning_capture_deferred camera=%s type=%s", camera.id, type(exc).__name__)
    return True


def import_camera(
    db: Session,
    camera: Camera,
    first_seen: dict[tuple[str, str], datetime],
    progress=None,
) -> int:
    from .ftp_import import import_camera as run_import

    return run_import(db, camera, first_seen, progress)
