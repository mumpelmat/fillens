"""Persistente manuelle Durchläufe; der vorhandene Einzel-Worker bleibt alleiniger Ausführer."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .config import settings
from .db import SessionLocal, aware, utcnow
from .models import WorkerControl, WorkerStatus


def worker_fresh(status):
    return status is not None and (
        utcnow() - aware(status.heartbeat_at)
    ).total_seconds() < max(180, settings().ftp_poll_seconds * 3)


def control_locked(db):
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    db.execute(
        insert(WorkerControl)
        .values(id="importer")
        .on_conflict_do_nothing(index_elements=[WorkerControl.id])
    )
    return db.get(
        WorkerControl, "importer", with_for_update=True, populate_existing=True
    )


def request_run(db):
    control = control_locked(db)
    if not worker_fresh(db.get(WorkerStatus, "importer")):
        return "offline"
    if control.running or db.get(WorkerStatus, "importer").phase != "idle":
        return "running"
    if control.requested > control.completed:
        return "pending"
    control.requested += 1
    control.requested_at = utcnow()
    return "requested"


def pending_run():
    with SessionLocal() as db:
        return (
            db.scalar(
                select(WorkerControl.id).where(
                    WorkerControl.id == "importer",
                    WorkerControl.requested > WorkerControl.completed,
                )
            )
            is not None
        )


def begin_run():
    with SessionLocal() as db:
        control = control_locked(db)
        # main() hält Datei-/Advisory-Sperre. Ein übrig gebliebenes running
        # nach einer fehlgeschlagenen Abschluss-Transaktion darf nicht blockieren.
        control.running = True
        control.claimed = control.requested
        control.started_at = utcnow()
        db.commit()
    return True


def finish_run(failed=False):
    with SessionLocal() as db:
        control = control_locked(db)
        control.completed = control.claimed
        control.running = False
        control.finished_at = utcnow()
        status = db.get(WorkerStatus, "importer")
        if status:
            status.phase = "idle"
            status.active_camera_id = None
            status.heartbeat_at = utcnow()
            if failed:
                status.last_error = "Worker-Durchlauf fehlgeschlagen. Systemstatus und Worker-Protokoll prüfen."
        db.commit()


def recover_control(db):
    # Nach Prozessabbruch bleiben unerledigte Anforderungen erhalten.
    control = control_locked(db)
    control.running = False
