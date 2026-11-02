"""Persistente, begrenzte Wiederholungen für stabile defekte FTP-Dateien."""

from datetime import timedelta
from pathlib import PurePosixPath

from sqlalchemy import func, select

from .db import aware, utcnow
from .models import FTPRejection


def retry_allowed(db, camera_id, remote) -> bool:
    row = db.scalar(
        select(FTPRejection).where(
            FTPRejection.camera_id == camera_id,
            FTPRejection.fingerprint == remote.fingerprint,
            FTPRejection.resolved_at.is_(None),
        )
    )
    if not row:
        return True
    if row.blocked:
        return False
    return row.retry_at is None or aware(row.retry_at) <= utcnow()


def reject(db, camera_id, remote, code: str) -> FTPRejection:
    row = db.scalar(
        select(FTPRejection).where(
            FTPRejection.camera_id == camera_id,
            FTPRejection.fingerprint == remote.fingerprint,
        )
    )
    now = utcnow()
    if not row:
        row = FTPRejection(
            camera_id=camera_id,
            fingerprint=remote.fingerprint,
            remote_path=remote.path,
            remote_size=remote.size,
            remote_modified=remote.modified[:64],
            error_code=code,
            first_seen_at=now,
            last_attempt_at=now,
        )
        db.add(row)
    row.remote_path = remote.path
    row.remote_size = remote.size
    row.remote_modified = remote.modified[:64]
    row.error_code = code[:40]
    row.resolved_at = None
    row.attempts = (row.attempts or 0) + 1
    row.last_attempt_at = now
    if row.attempts == 1:
        row.retry_at = now + timedelta(minutes=5)
    elif row.attempts == 2:
        row.retry_at = now + timedelta(minutes=30)
    else:
        row.retry_at = None
        row.blocked = True
    return row


def resolve(db, camera_id, remote) -> None:
    now = utcnow()
    for row in db.scalars(
        select(FTPRejection).where(
            FTPRejection.camera_id == camera_id,
            FTPRejection.remote_path == remote.path,
            FTPRejection.resolved_at.is_(None),
        )
    ):
        row.resolved_at = now
        row.retry_at = None
        row.blocked = False


def rejection_summary(db, camera_id) -> dict:
    count = (
        db.scalar(
            select(func.count())
            .select_from(FTPRejection)
            .where(
                FTPRejection.camera_id == camera_id,
                FTPRejection.resolved_at.is_(None),
            )
        )
        or 0
    )
    latest = db.scalar(
        select(FTPRejection)
        .where(
            FTPRejection.camera_id == camera_id,
            FTPRejection.resolved_at.is_(None),
        )
        .order_by(FTPRejection.last_attempt_at.desc())
        .limit(1)
    )
    return {
        "count": count,
        "latest_name": PurePosixPath(latest.remote_path).name if latest else None,
        "latest_code": latest.error_code if latest else None,
        "latest_attempts": latest.attempts if latest else 0,
        "latest_blocked": bool(latest and latest.blocked),
        "latest_retry_at": aware(latest.retry_at).isoformat()
        if latest and latest.retry_at
        else None,
    }
