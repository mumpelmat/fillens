import fcntl
import logging
import signal
import time
from datetime import datetime
from threading import Event, Thread

from sqlalchemy import select, text, update

from .alerts import evaluate_current_levels, process_outbox
from .config import settings
from .db import SessionLocal, engine, utcnow
from .ftp_errors import import_failure
from .importer import import_camera
from .learning import adopt_ready_models
from .ml_service import (
    analyze_buffer,
    preserve_review_references,
    queue_learning_jobs,
    run_training_job,
)
from .models import Camera, Container, ModelVersion, WorkerStatus
from .rates import run_rate_jobs
from .worker_control import begin_run, finish_run, pending_run, recover_control

logger = logging.getLogger("fillens.worker")
running = True


def stop(_signum, _frame):
    global running
    running = False


class ProgressReporter:
    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.last_phase = ""
        self.last_update = 0.0
        self.entries = 0

    def __call__(self, phase: str, entries: int, imported: int) -> None:
        self.entries = entries
        now = time.monotonic()
        if phase == self.last_phase and now - self.last_update < 0.5:
            return
        with SessionLocal() as status_db:
            status_db.execute(
                update(WorkerStatus)
                .where(WorkerStatus.id == "importer")
                .values(
                    heartbeat_at=utcnow(),
                    phase=phase,
                    active_camera_id=self.camera_id,
                    scanned_entries=entries,
                    imported_count=imported,
                )
            )
            status_db.commit()
        self.last_phase, self.last_update = phase, now


def heartbeat(stop_event: Event) -> None:
    while not stop_event.wait(5):
        try:
            with SessionLocal() as db:
                db.execute(
                    update(WorkerStatus)
                    .where(WorkerStatus.id == "importer")
                    .values(heartbeat_at=utcnow())
                )
                db.commit()
        except Exception as exc:
            logger.error("event=heartbeat_failed type=%s", type(exc).__name__)


def cycle(first_seen: dict[tuple[str, str], datetime]) -> None:
    with SessionLocal() as db:
        if db.get(WorkerStatus, "importer") is None:
            db.add(WorkerStatus(id="importer"))
            db.commit()
        db.execute(
            update(WorkerStatus)
            .where(WorkerStatus.id == "importer")
            .values(
                heartbeat_at=utcnow(),
                phase="idle",
                active_camera_id=None,
                last_error=None,
            )
        )
        db.commit()
        queue_learning_jobs()
        run_training_job(ProgressReporter)
        adopt_ready_models()
        db.execute(
            update(WorkerStatus)
            .where(WorkerStatus.id == "importer")
            .values(phase="idle", active_camera_id=None)
        )
        db.commit()
        cameras = list(
            db.scalars(
                select(Camera)
                .join(Container, Container.id == Camera.container_id)
                .where(
                    Camera.active.is_(True),
                    Container.active.is_(True),
                )
            )
        )
        cycle_error = None
        for camera in cameras:
            db.execute(
                update(WorkerStatus)
                .where(WorkerStatus.id == "importer")
                .values(
                    phase="connect",
                    active_camera_id=camera.id,
                    operation_started_at=utcnow(),
                    scanned_entries=0,
                    imported_count=0,
                )
            )
            db.commit()
            reporter = ProgressReporter(camera.id)
            try:
                preserve_review_references(db, camera)
                imported = (
                    import_camera(db, camera, first_seen, reporter)
                    if camera.ftp_connection_id
                    else 0
                )
                cycle_error = cycle_error or camera.last_error
                logger.info(
                    "event=scan_finished camera=%s imported=%d entries=%d kind=%s",
                    camera.id,
                    imported,
                    camera.last_scan_entries,
                    camera.last_scan_kind,
                )
            except Exception as exc:
                db.rollback()
                failure = import_failure(exc)
                camera.last_scan_at = utcnow()
                camera.last_error, camera.last_error_code = (
                    failure.message,
                    failure.code,
                )
                camera.last_scan_entries = reporter.entries
                cycle_error = cycle_error or failure.message
                logger.error(
                    "event=scan_failed camera=%s code=%s type=%s",
                    camera.id,
                    failure.code,
                    type(exc).__name__,
                )
            finally:
                db.commit()
                try:
                    analyze_buffer(db, camera, reporter)
                except Exception as exc:
                    db.rollback()
                    cycle_error = (
                        cycle_error
                        or "Bildanalyse fehlgeschlagen. Modell und Bildpuffer prüfen."
                    )
                    logger.error(
                        "event=analysis_cycle_failed camera=%s type=%s",
                        camera.id,
                        type(exc).__name__,
                    )
                db.commit()
                from .training_collection import run_collection

                try:
                    run_collection(camera.id)
                except Exception as exc:
                    logger.error(
                        "event=training_collection_failed camera=%s type=%s",
                        camera.id,
                        type(exc).__name__,
                    )
                db.execute(
                    update(WorkerStatus)
                    .where(WorkerStatus.id == "importer")
                    .values(
                        heartbeat_at=utcnow(),
                        phase="idle",
                        active_camera_id=None,
                        last_error=cycle_error,
                    )
                )
                db.commit()

    with SessionLocal() as status_db:
        status_db.execute(
            update(WorkerStatus)
            .where(WorkerStatus.id == "importer")
            .values(phase="rates", active_camera_id=None)
        )
        status_db.commit()
    try:
        run_rate_jobs()
        evaluate_current_levels()
        with SessionLocal() as status_db:
            status_db.execute(
                update(WorkerStatus)
                .where(WorkerStatus.id == "importer")
                .values(phase="email")
            )
            status_db.commit()
        process_outbox()
    finally:
        with SessionLocal() as status_db:
            status_db.execute(
                update(WorkerStatus)
                .where(WorkerStatus.id == "importer")
                .values(phase="idle", heartbeat_at=utcnow())
            )
            status_db.commit()


def run_once(first_seen):
    if not begin_run():
        return
    failed = True
    try:
        cycle(first_seen)
        failed = False
    finally:
        finish_run(failed)


def wait_for_next(started):
    while running and time.monotonic() - started < settings().ftp_poll_seconds:
        if pending_run():
            return
        time.sleep(1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s level=%(levelname)s %(message)s"
    )
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    settings().data_dir.mkdir(parents=True, exist_ok=True)
    # Dateisperre für SQLite-Entwicklung, zusätzlich DB-Sperre für PostgreSQL/mehrere Hosts.
    with (
        (settings().data_dir / ".worker.lock").open("w") as lock,
        engine.connect() as guard,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if engine.dialect.name == "postgresql":
            if not guard.scalar(text("SELECT pg_try_advisory_lock(629415813)")):
                raise RuntimeError("Ein Import-Worker ist bereits aktiv")
            guard.commit()
        with SessionLocal() as recovery:
            recover_control(recovery)
            recovery.execute(
                update(ModelVersion)
                .where(ModelVersion.status == "running")
                .values(status="queued")
            )
            recovery.commit()
        first_seen: dict[tuple[str, str], datetime] = {}
        stop_event = Event()
        pulse = Thread(target=heartbeat, args=(stop_event,), daemon=True)
        pulse.start()
        try:
            while running:
                started = time.monotonic()
                try:
                    run_once(first_seen)
                except Exception as exc:
                    logger.error(
                        "event=worker_cycle_failed type=%s", type(exc).__name__
                    )
                wait_for_next(started)
        finally:
            stop_event.set()
            pulse.join(timeout=6)
            if engine.dialect.name == "postgresql":
                guard.execute(text("SELECT pg_advisory_unlock(629415813)"))
                guard.commit()


if __name__ == "__main__":
    main()
