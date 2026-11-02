"""Rollendes Prüffenster und verzögerte Bereinigung geschützter Referenzen."""

from datetime import timedelta

from sqlalchemy import func, or_, select

from .db import aware, utcnow
from .models import (
    LearningRestoreSample,
    ModelVersion,
    TrainingFetchRecord,
    TrainingSample,
)

LIMIT = 50


def automatic_open_count(db, camera_id):
    return (
        db.scalar(
            select(func.count())
            .select_from(TrainingSample)
            .where(
                TrainingSample.camera_id == camera_id,
                or_(
                    TrainingSample.window_managed.is_(True),
                    TrainingSample.origin.in_(["camera", "ftp"]),
                ),
                TrainingSample.queue_state == "pending",
                TrainingSample.discarded_at.is_(None),
                TrainingSample.approved.is_(False),
            )
        )
        or 0
    )


def _sample_time(sample):
    return aware(sample.captured_at or sample.created_at)


def rebalance_window(db, camera_id):
    """Hält lokal exakt die neuesten verfügbaren 50 automatischen Referenzen offen."""
    from .ml_service import sample_path

    cutoff = utcnow() - timedelta(hours=168)
    rows = list(
        db.scalars(
            select(TrainingSample).where(
                TrainingSample.camera_id == camera_id,
                or_(
                    TrainingSample.window_managed.is_(True),
                    TrainingSample.origin.in_(["camera", "ftp"]),
                ),
                TrainingSample.approved.is_(False),
                TrainingSample.discarded_at.is_(None),
                TrainingSample.queue_state.in_(["pending", "deferred"]),
            )
        )
    )
    available = [
        sample
        for sample in rows
        if _sample_time(sample) >= cutoff and sample_path(sample).is_file()
    ]
    available.sort(key=lambda sample: (_sample_time(sample), sample.id), reverse=True)
    selected = {sample.id for sample in available[:LIMIT]}
    for sample in rows:
        sample.window_managed = True
        state = "pending" if sample.id in selected else "deferred"
        if sample.queue_state != state:
            sample.queue_state = state
        record = db.scalar(
            select(TrainingFetchRecord).where(
                TrainingFetchRecord.sample_id == sample.id
            )
        )
        if record:
            record.state = state
    cleanup_deferred(db, camera_id)
    return len(selected)


def oldest_open_time(db, camera_id):
    rows = list(
        db.scalars(
            select(TrainingSample).where(
                TrainingSample.camera_id == camera_id,
                TrainingSample.window_managed.is_(True),
                TrainingSample.queue_state == "pending",
                TrainingSample.approved.is_(False),
                TrainingSample.discarded_at.is_(None),
            )
        )
    )
    return min((_sample_time(sample) for sample in rows), default=None)


def _protected_ids(db, camera_id):
    jobs = list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera_id,
                ModelVersion.status.in_(["queued", "running"]),
            )
        )
    )
    protected = {row["id"] for job in jobs for row in job.dataset_json}
    protected.update(
        db.scalars(
            select(LearningRestoreSample.sample_id)
            .join(
                TrainingSample,
                TrainingSample.id == LearningRestoreSample.sample_id,
            )
            .where(TrainingSample.camera_id == camera_id)
        )
    )
    return protected


def cleanup_discarded(db, camera_id):
    from .ml_service import sample_path

    protected = _protected_ids(db, camera_id)
    for sample in db.scalars(
        select(TrainingSample).where(
            TrainingSample.camera_id == camera_id,
            TrainingSample.discarded_at.is_not(None),
        )
    ):
        if sample.id not in protected:
            sample_path(sample).unlink(missing_ok=True)


def cleanup_deferred(db, camera_id):
    """Entfernt nur Referenzen, die nicht mehr ins 168-Stunden-Fenster nachrücken."""
    from .ml_service import sample_path

    protected = _protected_ids(db, camera_id)
    cutoff = utcnow() - timedelta(hours=168)
    for sample in db.scalars(
        select(TrainingSample).where(
            TrainingSample.camera_id == camera_id,
            TrainingSample.queue_state == "deferred",
            TrainingSample.window_managed.is_(True),
            TrainingSample.origin == "ftp",
        )
    ):
        if sample.id not in protected and _sample_time(sample) < cutoff:
            sample_path(sample).unlink(missing_ok=True)


def collection_summary(db, camera):
    from .models import TrainingCollection
    from .ftp_rejections import rejection_summary

    state = db.get(TrainingCollection, camera.id)
    count = automatic_open_count(db, camera.id)
    open_rows = list(
        db.scalars(
            select(TrainingSample).where(
                TrainingSample.camera_id == camera.id,
                TrainingSample.window_managed.is_(True),
                TrainingSample.queue_state == "pending",
                TrainingSample.approved.is_(False),
                TrainingSample.discarded_at.is_(None),
            )
        )
    )
    deferred = (
        db.scalar(
            select(func.count())
            .select_from(TrainingSample)
            .where(
                TrainingSample.camera_id == camera.id,
                TrainingSample.window_managed.is_(True),
                TrainingSample.queue_state == "deferred",
                TrainingSample.discarded_at.is_(None),
            )
        )
        or 0
    )
    return {
        "open_count": count,
        "limit": LIMIT,
        "deferred_count": deferred,
        "newest_at": max((_sample_time(row) for row in open_rows), default=None).isoformat()
        if open_rows
        else None,
        "oldest_at": min((_sample_time(row) for row in open_rows), default=None).isoformat()
        if open_rows
        else None,
        "rejections": rejection_summary(db, camera.id),
        "status": "unconfigured"
        if not camera.ftp_connection_id
        else "paused"
        if not camera.learning_enabled
        else "full"
        if count >= LIMIT
        else state.status
        if state
        else "waiting",
        "error": state.error if state else None,
    }
