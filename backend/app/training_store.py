"""Unbegrenzte Lernliste mit zeitlicher Aufbewahrung und geschützten Referenzen."""

from datetime import timedelta
from sqlalchemy import func, select, update
from .db import aware, utcnow
from .models import Camera, ImageBuffer, LearningRestoreSample, ModelVersion, TrainingFetchRecord, TrainingSample, AnalysisResult


def automatic_open_count(db, camera_id):
    return db.scalar(select(func.count()).select_from(TrainingSample).where(
        TrainingSample.camera_id == camera_id,
        TrainingSample.origin.in_(["camera", "ftp"]),
        TrainingSample.queue_state == "pending",
        TrainingSample.discarded_at.is_(None), TrainingSample.approved.is_(False),
    )) or 0


def _protected_ids(db, camera_id, *, latest_preview=True):
    protected = {row["id"] for job in db.scalars(select(ModelVersion).where(
        ModelVersion.camera_id == camera_id, ModelVersion.status.in_(["queued", "running"])
    )) for row in job.dataset_json}
    protected.update(db.scalars(select(LearningRestoreSample.sample_id).join(
        TrainingSample, TrainingSample.id == LearningRestoreSample.sample_id
    ).where(TrainingSample.camera_id == camera_id)))
    # Die neueste Aufnahme bleibt auch bei längerer Kamerapause bewertbar.
    latest = db.scalar(select(ImageBuffer.training_sample_id).where(
        ImageBuffer.camera_id == camera_id
    ).order_by(ImageBuffer.captured_at.desc(), ImageBuffer.imported_at.desc()).limit(1))
    if latest and latest_preview:
        protected.add(latest)
    return protected


def expiry_query(camera_id, days):
    return select(TrainingSample).where(
        TrainingSample.camera_id == camera_id,
        TrainingSample.origin.in_(["camera", "ftp"]),
        TrainingSample.approved.is_(False), TrainingSample.discarded_at.is_(None),
        TrainingSample.queue_state.in_(["pending", "deferred"]),
        TrainingSample.created_at < utcnow() - timedelta(days=days),
    )


def retention_preview(db, camera_id, days):
    query = expiry_query(camera_id, days).where(TrainingSample.id.not_in(_protected_ids(db, camera_id)))
    return db.scalar(select(func.count()).select_from(query.subquery())) or 0


def rebalance_window(db, camera_id):
    """Alte zurückgestellte Referenzen wieder öffnen; keine Anzahlbegrenzung."""
    from .ml_service import sample_path
    camera = db.get(Camera, camera_id)
    cutoff = utcnow() - timedelta(days=camera.image_retention_days)
    for sample in db.scalars(select(TrainingSample).where(
        TrainingSample.camera_id == camera_id, TrainingSample.queue_state == "deferred",
        TrainingSample.discarded_at.is_(None), TrainingSample.approved.is_(False),
        (TrainingSample.created_at >= cutoff) | (TrainingSample.origin == "upload"),
    ).execution_options(yield_per=200)):
        if sample_path(sample).is_file():
            sample.queue_state = "pending"
            db.execute(update(TrainingFetchRecord).where(TrainingFetchRecord.sample_id == sample.id).values(state="pending"))
    db.flush()
    return automatic_open_count(db, camera_id)


def cleanup_discarded(db, camera_id):
    from .ml_service import sample_path
    protected = _protected_ids(db, camera_id, latest_preview=False)
    # Ausdrückliches Verwerfen darf auch die neueste Lernreferenz entfernen;
    # die unabhängige Kameravorschau bleibt vorhanden.
    for sample in db.scalars(select(TrainingSample).where(
        TrainingSample.camera_id == camera_id, TrainingSample.discarded_at.is_not(None)
    ).execution_options(yield_per=200)):
        if sample.id not in protected:
            sample_path(sample).unlink(missing_ok=True)
            sample.size_bytes = 0


def cleanup_deferred(db, camera_id):
    from .ml_service import sample_path
    camera = db.get(Camera, camera_id)
    for sample in db.scalars(expiry_query(camera_id, camera.image_retention_days).where(
        TrainingSample.id.not_in(_protected_ids(db, camera_id))
    ).limit(500)):
        sample_path(sample).unlink(missing_ok=True)
        sample.queue_state = "expired"
        sample.size_bytes = 0
        db.execute(update(TrainingFetchRecord).where(TrainingFetchRecord.sample_id == sample.id).values(state="expired"))
        db.execute(update(AnalysisResult).where(AnalysisResult.review_sample_id == sample.id).values(review_visible=False))
    db.flush()


def collect_buffer(db, camera, limit=100):
    """Vorhandene lokale Bilder unabhängig von ROI und Modellerkennung sichern."""
    from .ml_service import capture_review_sample
    from .services import image_path
    camera = db.get(Camera, camera.id, with_for_update=True, populate_existing=True)
    if not camera.learning_enabled:
        return 0
    count = 0
    for image in db.scalars(select(ImageBuffer).where(
        ImageBuffer.camera_id == camera.id, ImageBuffer.learning_collected.is_(False)
    ).order_by(ImageBuffer.captured_at.desc(), ImageBuffer.id).limit(limit)):
        if not image_path(image).is_file():
            continue
        capture_review_sample(db, camera, image)
        image.learning_collected = True
        count += 1
    rebalance_window(db, camera.id)
    db.flush()
    return count


def latest_image_data(db, camera):
    image = db.scalar(select(ImageBuffer).where(ImageBuffer.camera_id == camera.id).order_by(
        ImageBuffer.captured_at.desc(), ImageBuffer.imported_at.desc()).limit(1))
    if not image:
        return None
    sample = db.get(TrainingSample, image.training_sample_id) if image.training_sample_id else None
    state = ("discarded" if sample.discarded_at else "unusable" if sample.rejection_reason
             else "confirmed" if sample.approved else sample.queue_state) if sample else "waiting"
    return {"id": image.id, "content_url": f"/images/{image.id}/content",
            "captured_at": aware(image.captured_at).isoformat(),
            "imported_at": aware(image.imported_at).isoformat(), "state": state,
            "sample_id": sample.id if sample else None}


def collection_summary(db, camera):
    from .models import TrainingCollection
    from .ftp_rejections import rejection_summary
    state = db.get(TrainingCollection, camera.id)
    newest, oldest = db.execute(select(func.max(TrainingSample.captured_at), func.min(TrainingSample.captured_at)).where(
        TrainingSample.camera_id == camera.id, TrainingSample.queue_state == "pending",
        TrainingSample.discarded_at.is_(None), TrainingSample.approved.is_(False),
    )).one()
    return {
        "open_count": automatic_open_count(db, camera.id), "limit": None,
        "deferred_count": 0,
        "newest_at": aware(newest).isoformat() if newest else None,
        "oldest_at": aware(oldest).isoformat() if oldest else None,
        "last_import_at": aware(camera.last_import_at).isoformat() if camera.last_import_at else None,
        "scan_at": aware(camera.last_scan_at).isoformat() if camera.last_scan_at else None,
        "live_error": camera.last_error,
        "retention_days": camera.image_retention_days,
        "rejections": rejection_summary(db, camera.id),
        "status": "paused" if not camera.learning_enabled else "unconfigured" if not camera.ftp_connection_id else state.status if state else "waiting",
        "error": state.error if state else None,
    }
