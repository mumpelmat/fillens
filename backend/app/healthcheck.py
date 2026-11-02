from .config import settings
from .db import SessionLocal, aware, utcnow
from .models import WorkerStatus

with SessionLocal() as db:
    status = db.get(WorkerStatus, "importer")
    healthy = status is not None and (
        utcnow() - aware(status.heartbeat_at)
    ).total_seconds() < max(180, settings().ftp_poll_seconds * 3)
    raise SystemExit(0 if healthy else 1)
