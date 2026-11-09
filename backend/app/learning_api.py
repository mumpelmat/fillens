from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from .learning_queue import queue_data
from .models import (
    AnalysisResult,
    Camera,
    ImageBuffer,
    Measurement,
    TrainingFetchRecord,
    TrainingSample,
    LearningRestorePoint,
)
from .schemas import Input
from .security import DB, Admin, CurrentUser
from .training_access import training_camera

router = APIRouter(prefix="/training")


class Confirm(Input):
    fill_level: float = Field(ge=0, le=100)
    image_mode: Literal["day", "night_ir", "unknown"] | None = None
    series_name: str | None = Field(default=None, min_length=1, max_length=100)


class Unusable(Input):
    reason: Literal["lid_closed", "obstructed", "unusable"]
    image_mode: Literal["day", "night_ir", "unknown"] | None = None
    series_name: str | None = Field(default=None, min_length=1, max_length=100)


@router.post("/cameras/{camera_id}/queue/{item_id}/unusable")
def learn_unusable(camera_id: str, item_id: str, data: Unusable, db: DB, user: CurrentUser):
    from .ml_service import capture_review_sample, sample_path
    from .services import image_path
    from .training_store import rebalance_window
    from .vision import transform_key

    camera = training_camera(db, camera_id, user, lock=True)
    if user.role != "admin" and (data.image_mode is not None or data.series_name is not None):
        raise HTTPException(403, "Bildmodus und Aufnahmegruppe dürfen nur Admins ändern.")
    kind, _, key = item_id.partition("_")
    sample = db.get(TrainingSample, key) if kind == "sample" else None
    legacy = db.get(AnalysisResult, key) if kind == "analysis" else None
    if legacy and legacy.camera_id == camera_id and legacy.review_visible:
        image = db.scalar(select(ImageBuffer).where(ImageBuffer.import_id == legacy.source_import_id))
        if not image or not image_path(image).is_file():
            raise HTTPException(410, "Referenzbild ist nicht mehr vorhanden.")
        sample = capture_review_sample(db, camera, image, explicit=True)
        legacy.review_sample_id = sample.id if sample else None
    if not sample or sample.camera_id != camera_id:
        raise HTTPException(404, "Bild nicht gefunden.")
    if sample.discarded_at or not sample_path(sample).is_file():
        raise HTTPException(410, "Referenzbild wurde entfernt.")
    if sample.transform_key != transform_key(camera.image_rotation, camera.image_flip):
        raise HTTPException(409, "Bild passt nicht zur aktuellen Kameradrehung.")
    if data.series_name is not None:
        if not data.series_name.strip():
            raise HTTPException(422, "Aufnahmegruppe darf nicht leer sein.")
        sample.series_name = data.series_name.strip()
    if data.image_mode is not None:
        sample.image_mode = data.image_mode
    sample.target_fill_level = None
    sample.rejection_reason = data.reason
    sample.approved = True
    sample.queue_state = "confirmed"
    sample.created_by = user.id
    camera.learning_dirty = True
    unchanged = False
    for result in db.scalars(select(AnalysisResult).where(AnalysisResult.review_sample_id == sample.id)):
        existing = db.scalar(select(Measurement.id).where(Measurement.source_import_id == result.source_import_id))
        unchanged = unchanged or bool(existing)
        result.status = "reviewed" if existing else "discarded"
        result.reason = "Als ungeeignetes Lernbild bestätigt."
    record = db.scalar(select(TrainingFetchRecord).where(TrainingFetchRecord.sample_id == sample.id))
    if record:
        record.state = "confirmed"
    db.flush()
    rebalance_window(db, camera_id)
    db.commit()
    return {"ok": True, "measurement_unchanged": unchanged}


def camera_for_update(db, key):
    camera = db.get(Camera, key, with_for_update=True, populate_existing=True)
    if not camera:
        raise HTTPException(404, "Kamera nicht gefunden.")
    return camera


@router.get("/cameras/{camera_id}/restore-points")
def restore_points(camera_id: str, db: DB, _admin: Admin):
    from .learning_restore import point_data

    if not db.get(Camera, camera_id):
        raise HTTPException(404, "Kamera nicht gefunden.")
    return {
        "items": [
            point_data(point)
            for point in db.scalars(
                select(LearningRestorePoint)
                .where(LearningRestorePoint.camera_id == camera_id)
                .order_by(
                    LearningRestorePoint.created_at.desc(),
                    LearningRestorePoint.id.desc(),
                )
            )
        ]
    }


@router.post("/cameras/{camera_id}/restore-points", status_code=201)
def create_point(camera_id: str, db: DB, admin: Admin):
    from .learning_restore import create_restore_point, point_data

    camera = camera_for_update(db, camera_id)
    try:
        point = create_restore_point(db, camera, admin.id)
        db.commit()
        db.refresh(point)
        return point_data(point)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


@router.post("/cameras/{camera_id}/reset")
def reset(camera_id: str, db: DB, admin: Admin):
    from .learning_restore import point_data, reset_learning

    camera = camera_for_update(db, camera_id)
    try:
        backup = reset_learning(db, camera, admin.id)
        db.commit()
        db.refresh(backup)
        return {"ok": True, "backup": point_data(backup)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


@router.post("/cameras/{camera_id}/restore-points/{point_id}/restore")
def restore_point(camera_id: str, point_id: str, db: DB, admin: Admin):
    from .learning_restore import point_data, restore_learning

    camera = camera_for_update(db, camera_id)
    point = db.get(LearningRestorePoint, point_id)
    if not point or point.camera_id != camera_id:
        raise HTTPException(404, "Wiederherstellungspunkt nicht gefunden.")
    try:
        backup = restore_learning(db, camera, point, admin.id)
        db.commit()
        db.refresh(backup)
        return {"ok": True, "backup": point_data(backup)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


@router.delete("/cameras/{camera_id}/restore-points/{point_id}", status_code=204)
def remove_point(camera_id: str, point_id: str, db: DB, _admin: Admin):
    import shutil

    from .learning_restore import delete_restore_point

    camera_for_update(db, camera_id)
    point = db.get(LearningRestorePoint, point_id)
    if not point or point.camera_id != camera_id:
        raise HTTPException(404, "Wiederherstellungspunkt nicht gefunden.")
    directory = delete_restore_point(db, point)
    db.commit()
    shutil.rmtree(directory, ignore_errors=True)


@router.get("/cameras/{camera_id}/queue")
def queue(
    camera_id: str,
    db: DB,
    user: CurrentUser,
    tab: Literal["pending", "confirmed"] = "pending",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    exclude: str = Query(default="", max_length=8000),
    focus: str | None = Query(default=None, max_length=50),
):
    training_camera(db, camera_id, user)
    return queue_data(
        db, camera_id, tab, offset, limit, [s for s in exclude.split(",") if s], focus
    )


@router.post("/cameras/{camera_id}/queue/{item_id}/confirm")
def confirm(camera_id: str, item_id: str, data: Confirm, db: DB, user: CurrentUser):
    from .ml_service import (
        capture_review_sample,
        create_analysis_measurement,
        sample_path,
    )
    from .services import image_path

    camera = training_camera(db, camera_id, user, lock=True)
    if user.role != "admin" and (
        data.image_mode is not None or data.series_name is not None
    ):
        raise HTTPException(
            403, "Bildmodus und Aufnahmegruppe dürfen nur Admins ändern."
        )
    kind, _, key = item_id.partition("_")
    sample = db.get(TrainingSample, key) if kind == "sample" else None
    legacy = db.get(AnalysisResult, key) if kind == "analysis" else None
    if (
        (sample and sample.camera_id != camera_id)
        or (legacy and legacy.camera_id != camera_id)
        or (not sample and not legacy)
    ):
        raise HTTPException(404, "Bild nicht gefunden.")
    if legacy:
        if not legacy.review_visible:
            raise HTTPException(404, "Bild nicht gefunden.")
        image = db.scalar(
            select(ImageBuffer).where(ImageBuffer.import_id == legacy.source_import_id)
        )
        if not image or not image_path(image).is_file():
            raise HTTPException(
                410, "Das frühere Referenzbild ist nicht mehr vorhanden."
            )
        sample = capture_review_sample(db, camera, image, explicit=True)
        legacy.review_sample_id = sample.id
    if sample is None or sample.discarded_at:
        raise HTTPException(410, "Bild wurde verworfen.")
    if not sample_path(sample).is_file():
        raise HTTPException(410, "Referenzbild ist nicht mehr vorhanden.")
    from .vision import transform_key

    if sample.transform_key != transform_key(camera.image_rotation, camera.image_flip):
        raise HTTPException(
            409,
            "Bild passt nicht zur aktuellen Kameradrehung. Passende Bilder hinzufügen.",
        )
    old = (
        sample.target_fill_level,
        sample.approved,
        sample.image_mode,
        sample.series_name,
    )
    if sample.rejection_reason:
        camera.learning_dirty = True
    sample.rejection_reason = None
    sample.target_fill_level = data.fill_level
    sample.approved = True
    sample.queue_state = "confirmed"
    sample.created_by = user.id
    if data.image_mode is not None:
        sample.image_mode = data.image_mode
    if data.series_name is not None:
        if not data.series_name.strip():
            raise HTTPException(422, "Aufnahmegruppe darf nicht leer sein.")
        sample.series_name = data.series_name.strip()
    if old != (
        sample.target_fill_level,
        sample.approved,
        sample.image_mode,
        sample.series_name,
    ):
        camera.learning_dirty = True
    results = list(
        db.scalars(
            select(AnalysisResult)
            .where(
                AnalysisResult.review_sample_id == sample.id,
                AnalysisResult.review_visible.is_(True),
            )
            .order_by(AnalysisResult.created_at.desc(), AnalysisResult.id.desc())
        )
    )
    created = False
    unchanged = False
    for result in results:
        existing = db.scalar(
            select(Measurement).where(
                Measurement.source_import_id == result.source_import_id
            )
        )
        if existing:
            unchanged = True
            result.measurement_id = existing.id
            if result.status in ("pending", "accepted"):
                result.status = (
                    "reviewed" if result.status == "accepted" else "superseded"
                )
        elif result.status in ("pending", "discarded") and result.discarded_at is None:
            create_analysis_measurement(db, result, camera, data.fill_level, user.id)
            result.status = "reviewed"
            created = True
    record = db.scalar(
        select(TrainingFetchRecord).where(TrainingFetchRecord.sample_id == sample.id)
    )
    if record:
        record.state = "confirmed"
    from .training_store import rebalance_window

    rebalance_window(db, camera_id)
    db.commit()
    return {
        "ok": True,
        "measurement_created": created,
        "measurement_unchanged": unchanged,
        "learning_enabled": camera.learning_enabled,
    }


@router.post("/cameras/{camera_id}/retry", status_code=202)
def retry(camera_id: str, db: DB, admin: Admin):
    from .ml_service import enqueue_model

    camera = camera_for_update(db, camera_id)
    if not camera.learning_enabled:
        raise HTTPException(409, "Zuerst Weiter lernen einschalten.")
    try:
        enqueue_model(
            db,
            camera,
            "Gemeinsamer Lernstand",
            "all",
            admin.id,
            managed=True,
            force=True,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.commit()
    return {"ok": True}


@router.post("/cameras/{camera_id}/queue/{item_id}/discard")
def discard(camera_id: str, item_id: str, db: DB, user: CurrentUser):
    from sqlalchemy import update

    from .db import utcnow
    from .learning_queue import queue_query
    from .training_store import automatic_open_count, cleanup_discarded, rebalance_window

    camera = training_camera(db, camera_id, user, lock=True)
    kind, _, key = item_id.partition("_")
    sample = db.get(TrainingSample, key) if kind == "sample" else None
    legacy = db.get(AnalysisResult, key) if kind == "analysis" else None
    if not (
        sample
        and sample.camera_id == camera_id
        or legacy
        and legacy.camera_id == camera_id
    ):
        raise HTTPException(404, "Bild nicht gefunden.")
    if sample and not sample.discarded_at:
        pending = queue_query(camera_id, "pending")
        if not db.scalar(
            select(pending.c.id).where(
                pending.c.kind == "sample", pending.c.id == sample.id
            )
        ):
            raise HTTPException(
                409, "Bestätigte Bilder werden nicht über Verwerfen entfernt."
            )
        sample.discarded_at = utcnow()
        sample.queue_state = "discarded"
        camera.learning_dirty = True
        record = db.scalar(
            select(TrainingFetchRecord).where(
                TrainingFetchRecord.sample_id == sample.id
            )
        )
        if record:
            record.state = "discarded"
        db.execute(
            update(AnalysisResult)
            .where(AnalysisResult.review_sample_id == sample.id)
            .values(
                review_visible=False,
                discarded_at=sample.discarded_at,
                status="discarded",
            )
        )
    if legacy:
        if legacy.discarded_at is None:
            from .ml_service import capture_review_sample
            from .services import image_path

            image = db.scalar(
                select(ImageBuffer).where(
                    ImageBuffer.import_id == legacy.source_import_id
                )
            )
            if image and image_path(image).is_file():
                reference = capture_review_sample(db, camera, image, explicit=True)
                if reference:
                    reference.discarded_at = utcnow()
                    reference.queue_state = "discarded"
                    legacy.review_sample_id = reference.id
                    camera.learning_dirty = True
        legacy.review_visible = False
        legacy.status = "discarded"
        legacy.discarded_at = utcnow()
    db.flush()
    rebalance_window(db, camera_id)
    cleanup_discarded(db, camera_id)
    count = automatic_open_count(db, camera_id)
    db.commit()
    return {"ok": True, "open_count": count}
