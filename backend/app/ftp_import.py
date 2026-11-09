"""Importsteuerung: erkannte Tagesordner, Kontrollscans und Fortschritt."""

import logging
import posixpath
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import aware, utcnow
from .ftp_errors import ImportFailure, import_failure
from .ftp_rejections import reject, resolve, retry_allowed
from .ftp_control import control_paths
from .ftp_scan import (
    RemoteFile,
    ScanBudget,
    detect_layout,
    latest_path,
    recent_paths,
    remote_time,
    scan,
    stat,
)
from .importer import connect, ingest, reconcile
from .models import Camera, FTPConnection, ImageBuffer, ImportRecord

logger = logging.getLogger("fillens.importer")
Progress = Callable[[str, int, int], None]


def import_camera(
    db: Session,
    camera: Camera,
    first_seen: dict[tuple[str, str], datetime],
    progress: Progress | None = None,
) -> int:
    total_entries, imported, attempted = 0, 0, 0

    def report(phase: str, entries: int | None = None) -> None:
        if progress:
            progress(
                phase,
                total_entries if entries is None else total_entries + entries,
                imported,
            )

    report("cleanup")
    try:
        reconcile(db, camera)
    except OSError as exc:
        raise import_failure(exc, "storage") from exc
    connection = db.get(FTPConnection, camera.ftp_connection_id)
    if connection is None:
        return 0
    report("connect")
    ftp = connect(connection)
    failures: list[ImportFailure] = []
    observed: set[tuple[str, str]] = set()
    try:
        root = posixpath.normpath(
            "/" + posixpath.join(connection.root_path, camera.ftp_root).lstrip("/")
        )
        try:
            ftp.cwd(root)
        except Exception as exc:
            raise import_failure(exc, "root") from exc
        discovery = ScanBudget(on_progress=lambda count: report("discover", count))
        pattern = (
            camera.date_pattern
            if camera.scan_mode == "date"
            else camera.detected_date_pattern
        )
        if camera.scan_mode == "auto" and not pattern:
            report("discover")
            layout = detect_layout(ftp, root, discovery)
            if layout:
                pattern, camera.last_date_path = layout
                camera.detected_date_pattern = pattern
                db.commit()
        total_entries += discovery.entries
        targeted = camera.scan_mode != "recursive" and pattern is not None
        camera.last_scan_kind = "date" if targeted else "recursive"
        camera.last_scan_entries = 0
        db.commit()

        def process(files: list[RemoteFile]) -> None:
            nonlocal imported, attempted
            for remote in sorted(
                files,
                key=lambda f: (f.modified, f.path),
                reverse=True,
            ):
                if db.scalar(
                    select(ImportRecord.id).where(
                        ImportRecord.camera_id == camera.id,
                        ImportRecord.fingerprint == remote.fingerprint,
                    )
                ):
                    continue
                if not retry_allowed(db, camera.id, remote):
                    continue
                modified = remote_time(remote.modified)
                if modified and modified < utcnow() - timedelta(days=camera.image_retention_days):
                    # Auch eine länger stille Kamera behält ihre neueste Aufnahme.
                    latest = db.scalar(select(ImportRecord.remote_modified).join(
                        ImageBuffer, ImageBuffer.import_id == ImportRecord.id
                    ).where(ImageBuffer.camera_id == camera.id).order_by(
                        ImageBuffer.captured_at.desc(), ImageBuffer.imported_at.desc()
                    ).limit(1))
                    if latest and remote_time(latest) and modified <= remote_time(latest):
                        continue
                key = (camera.id, remote.fingerprint)
                observed.add(key)
                since = first_seen.setdefault(key, utcnow())
                timestamp = modified or since
                if (
                    utcnow() - timestamp
                ).total_seconds() < settings().ftp_settle_seconds:
                    continue
                if attempted >= settings().max_imports_per_cycle:
                    break
                attempted += 1
                try:
                    if remote.size <= 0 or remote.size > settings().max_image_bytes:
                        raise ImportFailure(
                            "image_size",
                            f"Bildgröße liegt außerhalb des Limits von {settings().max_image_bytes // 1024 // 1024} MiB.",
                        )
                    report("download")
                    before = stat(ftp, remote.path)
                    if before.size != remote.size or (
                        before.modified
                        and remote.modified
                        and before.modified != remote.modified
                    ):
                        continue
                    chunks = bytearray()

                    def receive(block: bytes) -> None:
                        if len(chunks) + len(block) > settings().max_image_bytes:
                            raise ImportFailure(
                                "download_limit",
                                "Bilddownload überschreitet das Größenlimit; Transfer wurde abgebrochen.",
                            )
                        chunks.extend(block)
                        report("download")

                    try:
                        ftp.retrbinary("RETR " + remote.path, receive)
                    except Exception as exc:
                        raise import_failure(exc, "download") from exc
                    if stat(ftp, remote.path) != before:
                        continue
                    report("store")
                    try:
                        imported += int(ingest(db, camera, remote, bytes(chunks)))
                        resolve(db, camera.id, remote)
                        db.commit()
                    except OSError as exc:
                        raise import_failure(exc, "storage") from exc
                    if camera.analysis_enabled:
                        try:
                            from .ml_service import analyze_buffer

                            analyze_buffer(db, camera, lambda phase, *_: report(phase))
                        except Exception as exc:
                            db.rollback()
                            logger.warning(
                                "event=analysis_deferred camera=%s type=%s",
                                camera.id,
                                type(exc).__name__,
                            )
                except Exception as exc:
                    db.rollback()
                    failure = import_failure(exc)
                    logger.warning(
                        "event=image_rejected camera=%s code=%s type=%s",
                        camera.id,
                        failure.code,
                        type(exc).__name__,
                    )
                    if failure.code not in {
                        "image_size",
                        "image_invalid",
                        "image_incomplete",
                    }:
                        raise failure from exc
                    reject(db, camera.id, remote, failure.code)
                    db.commit()
                    failures.append(failure)

        if targeted:
            report("scan")
            budget = ScanBudget(on_progress=lambda count: report("scan", count))
            paths = recent_paths(
                pattern, camera.timezone, camera.recent_days, utcnow(), root
            )
            if camera.last_date_path and camera.last_date_path not in paths:
                paths.append(camera.last_date_path)
            files: dict[str, RemoteFile] = {}
            accessible = 0
            latest_seen_path = None
            for path in paths:
                try:
                    found = list(scan(ftp, posixpath.join(root, path), budget))
                    accessible += 1
                    files.update((f.path, f) for f in found)
                    if found and latest_seen_path is None:
                        latest_seen_path = path
                except ImportFailure as exc:
                    if exc.code != "root_unavailable":
                        raise
            if latest_seen_path:
                camera.last_date_path = latest_seen_path
            if not files:
                fallback = latest_path(ftp, root, pattern, budget)
                if fallback and fallback not in paths:
                    files.update(
                        (f.path, f)
                        for f in scan(ftp, posixpath.join(root, fallback), budget)
                    )
                    camera.last_date_path = fallback
                    accessible += 1
                if not accessible:
                    raise ImportFailure(
                        "date_directories_unavailable",
                        "Keine zugänglichen Datumsordner gefunden. Datumsmuster, Kamera-Zeitzone, Unterverzeichnis und Leserechte prüfen.",
                    )
            total_entries += budget.entries
            process(list(files.values()))
            last_attempt = camera.last_full_scan_attempt_at
            if last_attempt is None:
                camera.last_full_scan_attempt_at = utcnow()
            elif utcnow() - aware(last_attempt) >= timedelta(
                hours=camera.full_scan_hours
            ):
                camera.last_full_scan_attempt_at = utcnow()
                db.commit()
                report("control")
                control = ScanBudget(on_progress=lambda count: report("control", count))
                try:
                    targets = control_paths(
                        ftp,
                        root,
                        pattern,
                        control,
                        aware(last_attempt),
                        utcnow(),
                        camera.full_scan_hours,
                        set(paths),
                    )
                    for target in targets:
                        process(list(scan(ftp, posixpath.join(root, target), control)))
                    camera.last_full_scan_at = utcnow()
                    camera.last_scan_kind = "date_control"
                except ImportFailure as exc:
                    failures.append(
                        ImportFailure(
                            exc.code,
                            "Aktuelle Tagesordner geprüft; Struktur-Kontrollscan: "
                            + exc.message,
                        )
                    )
                total_entries += control.entries
        else:
            report("scan")
            budget = ScanBudget(on_progress=lambda count: report("scan", count))
            process(list(scan(ftp, root, budget)))
            total_entries += budget.entries
            camera.last_full_scan_at = camera.last_full_scan_attempt_at = utcnow()
        for key in list(first_seen):
            if key[0] == camera.id and key not in observed:
                del first_seen[key]
        camera.last_scan_at = utcnow()
        camera.last_scan_entries = total_entries
        camera.last_error_code = failures[0].code if failures else None
        camera.last_error = (
            (f"{len(failures)} Problem(e): " + failures[0].message)
            if failures
            else None
        )
        db.commit()
        report("scan")
        return imported
    finally:
        ftp.close()
