"""Zeitlich verteilte FTP-Referenzen ohne produktiven Import oder Messwerte."""

import hashlib
import json
import os
import posixpath
import time
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import settings
from .db import aware, utcnow
from .ftp_errors import ImportFailure, import_failure
from .ftp_scan import (
    RemoteFile,
    ScanBudget,
    detect_layout,
    root_context,
    validate_pattern,
    remote_time,
    scan,
    stat,
)
from .importer import connect, prepare_image
from .ftp_rejections import reject, resolve, retry_allowed
from .models import (
    AnalysisResult,
    Camera,
    FTPConnection,
    ImportRecord,
    TrainingCollection,
    TrainingFetchRecord,
    TrainingSample,
)
from .training_store import (
    LIMIT,
    automatic_open_count,
    cleanup_discarded,
    oldest_open_time,
    rebalance_window,
)
from .vision import image_features, transform_key


def training_paths(pattern, timezone, now, root):
    pattern = validate_pattern(pattern)
    start = (now - timedelta(hours=168)).astimezone(ZoneInfo(timezone)).date()
    end = now.astimezone(ZoneInfo(timezone)).date()
    year, month = root_context(root)
    if "YYYY" not in pattern.split("/") and (
        year is None or "MM" not in pattern.split("/") and month is None
    ):
        raise ImportFailure(
            "training_layout", "Datumsmuster und FTP-Root passen nicht zusammen."
        )
    paths = []
    for offset in range((end - start).days + 1):
        day = end - timedelta(days=offset)
        if "YYYY" not in pattern.split("/") and day.year != year:
            continue
        if "MM" not in pattern.split("/") and day.month != month:
            continue
        paths.append(
            pattern.replace("YYYY", str(day.year))
            .replace("MM", f"{day.month:02}")
            .replace("TT", f"{day.day:02}")
        )
    return paths


def collection_context(camera, connection):
    root = posixpath.normpath(
        "/" + posixpath.join(connection.root_path, camera.ftp_root).lstrip("/")
    )
    key = transform_key(camera.image_rotation, camera.image_flip)
    context = hashlib.sha256(
        json.dumps(
            [
                connection.id,
                connection.host,
                connection.port,
                connection.username,
                connection.encrypted_password,
                connection.protocol,
                root,
                camera.scan_mode,
                camera.date_pattern,
                camera.detected_date_pattern,
                camera.timezone,
                key,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return root, key, context


def collect_training(db, camera_id, progress=None):
    camera = db.get(Camera, camera_id, populate_existing=True)
    cleanup_discarded(db, camera_id)
    rebalance_window(db, camera_id)
    if not camera.learning_enabled or not camera.ftp_connection_id:
        return 0
    state = db.get(TrainingCollection, camera_id)
    if not state:
        state = TrainingCollection(camera_id=camera_id)
        db.add(state)
        db.flush()
    connection = db.get(FTPConnection, camera.ftp_connection_id)
    root, key, context = collection_context(camera, connection)
    if state.context_key != context:
        state.candidates_json, state.retry_at = [], None
        state.scanned_at, state.scan_json, state.context_key = None, {}, context
    if state.retry_at and aware(state.retry_at) > utcnow():
        db.commit()
        return 0
    if state.retry_at:
        state.retry_at, state.scanned_at = None, None
    if (
        not state.candidates_json
        and not state.scan_json
        and state.scanned_at
        and utcnow() - aware(state.scanned_at) < timedelta(hours=1)
    ):
        state.status = "full" if automatic_open_count(db, camera_id) >= LIMIT else "exhausted"
        db.commit()
        return 0
    now, cutoff = utcnow(), utcnow() - timedelta(hours=168)
    deadline, ftp, added = time.monotonic() + 30, None, 0

    def tick(*_):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ImportFailure("training_budget", "Trainingsabruf wird im nächsten Durchlauf fortgesetzt.")
        if ftp:
            ftp.timeout = min(settings().ftp_timeout_seconds, remaining)
            if ftp.sock:
                ftp.sock.settimeout(ftp.timeout)
        if progress:
            progress("training_collect", 0, added)

    try:
        state.status, state.error = "collecting", None
        db.commit()
        ftp = connect(connection, deadline=deadline)
        tick()
        try:
            ftp.cwd(root)
        except Exception as exc:
            raise import_failure(exc, "root") from exc

        if not state.scanned_at or now - aware(state.scanned_at) >= timedelta(hours=1):
            budget = ScanBudget(on_progress=tick)
            pattern = camera.date_pattern if camera.scan_mode == "date" else camera.detected_date_pattern
            if not pattern:
                layout = detect_layout(ftp, root, budget)
                pattern = layout[0] if layout else None
            if not pattern:
                raise ImportFailure(
                    "training_layout",
                    "Für die Trainingssammlung wurde keine eindeutige Datumsstruktur erkannt. Datumsmuster und FTP-Root konfigurieren.",
                )
            scan_state = state.scan_json or {}
            paths = list(scan_state.get("paths", training_paths(pattern, camera.timezone, now, root)))
            files = {row["path"]: RemoteFile(**row) for row in scan_state.get("files", [])}
            while paths:
                tick()
                relative = paths[0]
                try:
                    for remote in scan(ftp, posixpath.join(root, relative), budget):
                        stamp = remote_time(remote.modified)
                        if stamp and cutoff <= stamp <= now - timedelta(seconds=settings().ftp_settle_seconds):
                            files[remote.path] = remote
                except ImportFailure as exc:
                    if exc.code != "root_unavailable":
                        raise
                paths.pop(0)
                state.scan_json = {"paths": list(paths), "files": [asdict(row) for row in files.values()]}
                db.commit()
            records = {
                row.fingerprint: row
                for row in db.scalars(
                    select(TrainingFetchRecord).where(
                        TrainingFetchRecord.camera_id == camera_id,
                        TrainingFetchRecord.transform_key == key,
                    )
                )
            }
            known = {
                fingerprint
                for fingerprint, record in records.items()
                if record.state in {"pending", "confirmed", "discarded"}
            }
            known.update(
                db.scalars(
                    select(ImportRecord.fingerprint)
                    .join(AnalysisResult, AnalysisResult.source_import_id == ImportRecord.id)
                    .where(ImportRecord.camera_id == camera_id, AnalysisResult.discarded_at.is_not(None))
                )
            )
            newest = sorted(
                files.values(),
                key=lambda remote: (remote_time(remote.modified) or cutoff, remote.path),
                reverse=True,
            )
            state.candidates_json = [
                asdict(remote)
                for remote in newest
                if remote.fingerprint not in known and retry_allowed(db, camera_id, remote)
            ]
            state.scanned_at, state.scan_json = now, {}
            db.commit()

        candidates = list(state.candidates_json)
        for _ in range(10):
            tick()
            if not candidates:
                break
            remote = RemoteFile(**candidates[0])
            stamp = remote_time(remote.modified)
            oldest = oldest_open_time(db, camera_id)
            if (
                automatic_open_count(db, camera_id) >= LIMIT
                and oldest is not None
                and stamp is not None
                and stamp <= oldest
            ):
                break
            if not retry_allowed(db, camera_id, remote):
                candidates.pop(0)
                state.candidates_json = list(candidates)
                db.commit()
                continue
            content, captured, error_code = None, None, None
            try:
                if not stamp or not cutoff <= stamp <= now:
                    raise ValueError("outside_window")
                if remote.size <= 0 or remote.size > settings().max_image_bytes:
                    raise ValueError("image_size")
                before = stat(ftp, remote.path)
                if before.size != remote.size or (before.modified and remote.modified and before.modified != remote.modified):
                    raise ValueError("file_changed")
                raw = bytearray()

                def receive(block):
                    tick()
                    if len(raw) + len(block) > settings().max_image_bytes:
                        raise ValueError("image_size")
                    raw.extend(block)

                try:
                    ftp.retrbinary("RETR " + remote.path, receive)
                except Exception as exc:
                    raise import_failure(exc, "download") from exc
                if len(raw) != remote.size or stat(ftp, remote.path) != before:
                    raise ValueError("file_changed")
                content, captured, _source = prepare_image(bytes(raw), camera, remote)
                if not cutoff <= captured <= now:
                    raise ValueError("outside_window")
            except ImportFailure as exc:
                if exc.code != "file_unavailable":
                    raise
                error_code = exc.code
            except (OSError, ValueError, SyntaxError) as exc:
                code = str(exc)
                error_code = (
                    code
                    if code
                    in {"outside_window", "image_size", "file_changed"}
                    else "image_invalid"
                )

            db.commit()
            camera = db.get(Camera, camera_id, with_for_update=True, populate_existing=True)
            current_connection = db.get(FTPConnection, camera.ftp_connection_id, populate_existing=True) if camera.ftp_connection_id else None
            if (
                not current_connection
                or collection_context(camera, current_connection)[2] != context
                or not camera.learning_enabled
                or key != transform_key(camera.image_rotation, camera.image_flip)
                or camera.ftp_connection_id != connection.id
            ):
                db.rollback()
                break
            record = db.scalar(
                select(TrainingFetchRecord).where(
                    TrainingFetchRecord.camera_id == camera_id,
                    TrainingFetchRecord.fingerprint == remote.fingerprint,
                    TrainingFetchRecord.transform_key == key,
                )
            )
            if not record:
                record = TrainingFetchRecord(camera_id=camera_id, fingerprint=remote.fingerprint, transform_key=key)
                db.add(record)
            record.remote_path, record.remote_size = remote.path, remote.size
            record.remote_modified, record.captured_at = remote.modified[:64], captured or stamp

            if content:
                digest = hashlib.sha256(content).hexdigest()
                sample = db.scalar(
                    select(TrainingSample).where(
                        TrainingSample.camera_id == camera_id,
                        TrainingSample.file_hash == digest,
                    )
                )
                if not sample:
                    sample = TrainingSample(
                        camera_id=camera_id,
                        filename=uuid4().hex + ".jpg",
                        original_name=f"FTP-Referenz {captured.isoformat()}",
                        file_hash=digest,
                        size_bytes=len(content),
                        origin="ftp",
                        captured_at=captured,
                        image_mode="unknown",
                        series_name=f"Kamera {captured.strftime('%Y-%m-%d')}",
                        transform_key=key,
                        approved=False,
                        queue_state="pending",
                        window_managed=True,
                        source_fingerprint=remote.fingerprint,
                    )
                    db.add(sample)
                    db.flush()
                    added += 1
                if not sample.discarded_at and not sample.approved:
                    from .ml_service import sample_path

                    path = sample_path(sample)
                    if not path.is_file():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("xb") as handle:
                            handle.write(content)
                            handle.flush()
                            os.fsync(handle.fileno())
                    sample.image_mode = image_features(
                        path,
                        camera.roi_json or {"x": 0, "y": 0, "width": 1, "height": 1},
                    )[1]
                    sample.queue_state, sample.window_managed = "pending", True
                    sample.source_fingerprint, sample.captured_at = remote.fingerprint, captured
                elif sample.discarded_at:
                    sample.queue_state = "discarded"
                record.sample_id = sample.id
                record.state = "discarded" if sample.discarded_at else "confirmed" if sample.approved else "pending"
                resolve(db, camera_id, remote)
                rebalance_window(db, camera_id)
                record.state = sample.queue_state
            else:
                rejection = reject(db, camera_id, remote, error_code or "image_invalid")
                record.state = "rejected"
                if rejection.retry_at and (
                    state.retry_at is None or aware(rejection.retry_at) < aware(state.retry_at)
                ):
                    state.retry_at = rejection.retry_at
            candidates.pop(0)
            state.candidates_json = list(candidates)
            db.commit()
        state.status = "full" if automatic_open_count(db, camera_id) >= LIMIT else "waiting" if candidates else "exhausted"
        state.error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        state = db.get(TrainingCollection, camera_id)
        failure = import_failure(exc)
        if isinstance(exc, ImportFailure) and exc.code == "training_budget":
            state.status, state.error = "waiting", None
        else:
            state.status = "blocked" if failure.code in {"scan_limit", "training_layout", "discovery_limit"} else "error"
            state.error = failure.message
            state.retry_at = utcnow() + (timedelta(hours=24) if state.status == "blocked" else timedelta(minutes=5))
        db.commit()
    finally:
        if ftp:
            ftp.close()
    return added


def collection_process(camera_id):
    from .db import SessionLocal
    from .worker import ProgressReporter

    with SessionLocal() as db:
        collect_training(db, camera_id, ProgressReporter(camera_id))


def run_collection(camera_id):
    """Auch DNS-/TLS-/Serverhänger dürfen den normalen Import nicht blockieren."""
    from multiprocessing import get_context

    process = get_context("spawn").Process(target=collection_process, args=(camera_id,))
    process.start()
    process.join(timeout=28)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.5)

        from .db import SessionLocal

        with SessionLocal() as db:
            state = db.get(TrainingCollection, camera_id)
            if state and state.status == "collecting":
                state.status = "error"
                state.error = "Zeitbudget für Trainingsabruf erreicht. Gespeicherter Fortschritt bleibt erhalten."
                state.retry_at = utcnow() + timedelta(minutes=5)
                db.commit()
