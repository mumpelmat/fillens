"""Admin-Training: explizite Bilder, ROI, Jobs und Modellaktivierung."""

import hashlib
import os
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field, model_validator
from sqlalchemy import func, select, update

from .config import settings
from .db import aware
from .ftp_scan import RemoteFile
from .importer import prepare_image
from .learning import learning_summary
from .learning_api import router as learning_router
from .ml_service import (
    capture_review_sample,
    compatible,
    create_analysis_measurement,
    enqueue_model,
    learning_status,
    model_path,
    sample_path,
)
from .models import (
    AnalysisResult,
    Camera,
    ImageBuffer,
    Measurement,
    ModelVersion,
    LearningRestoreSample,
    TrainingSample,
)
from .roi import PolygonROI
from .schemas import Input
from .security import DB, Admin, CurrentUser
from .services import image_path
from .training_access import training_camera
from .training_store import collection_summary, latest_image_data, retention_preview
from .vision import image_features, transform_key

router = APIRouter(prefix="/api")
router.include_router(learning_router)


class ROI(Input):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(ge=0.01, le=1)
    height: float = Field(ge=0.01, le=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Bildbereich liegt außerhalb des Bildes")
        return self


class TrainingConfig(Input):
    roi: ROI | PolygonROI
    recognition_requested: bool | None = None
    analysis_enabled: bool = False
    min_confidence: float = Field(default=0.75, ge=0.5, le=1)


class SampleUpdate(Input):
    target_fill_level: float | None = Field(default=None, ge=0, le=100)
    image_mode: Literal["day", "night_ir", "unknown"] = "unknown"
    series_name: str = Field(min_length=1, max_length=100)
    approved: bool = True


class TrainInput(Input):
    camera_id: str
    name: str = Field(default="Füllstandsmodell", min_length=1, max_length=120)
    image_mode: Literal["all", "day", "night_ir"] = "all"
    managed: bool = False


class ReviewInput(Input):
    fill_level: float = Field(ge=0, le=100)
    series_name: str | None = Field(default=None, min_length=1, max_length=100)


class LearningInput(Input):
    enabled: bool | None = None
    training_mode: Literal["automatic", "manual"] | None = None
    image_retention_days: int | None = Field(default=None, ge=1, le=365)


def require(db, cls, key):
    value = db.get(cls, key)
    if not value:
        raise HTTPException(404, "Eintrag nicht gefunden.")
    return value


def sample_data(sample: TrainingSample) -> dict:
    return {
        key: getattr(sample, key)
        for key in (
            "id",
            "camera_id",
            "original_name",
            "size_bytes",
            "target_fill_level",
            "rejection_reason",
            "image_mode",
            "series_name",
            "approved",
            "queue_state",
            "transform_key",
        )
    } | {"created_at": aware(sample.created_at).isoformat()}


def model_data(model: ModelVersion, camera: Camera) -> dict:
    return {
        key: getattr(model, key)
        for key in (
            "id",
            "camera_id",
            "name",
            "version",
            "algorithm",
            "image_mode",
            "status",
            "active",
            "metrics_json",
            "error",
            "training_sample_count",
            "trigger",
        )
    } | {
        "created_at": aware(model.created_at).isoformat(),
        "compatible": compatible(camera, model),
        "finished_at": aware(model.finished_at).isoformat()
        if model.finished_at
        else None,
    }


@router.get("/training/cameras/{camera_id}")
def training_overview(camera_id: str, db: DB, user: CurrentUser):
    camera = training_camera(db, camera_id, user)
    total, size = db.execute(
        select(
            func.count(), func.coalesce(func.sum(TrainingSample.size_bytes), 0)
        ).where(TrainingSample.camera_id == camera.id)
    ).one()
    approved = db.scalar(
        select(func.count())
        .select_from(TrainingSample)
        .where(
            TrainingSample.camera_id == camera.id,
            TrainingSample.approved.is_(True),
            TrainingSample.discarded_at.is_(None),
            TrainingSample.transform_key
            == transform_key(camera.image_rotation, camera.image_flip),
        )
    )
    return {
        "camera_id": camera.id,
        "collection": collection_summary(db, camera),
        "roi": camera.roi_json,
        "learning_enabled": camera.learning_enabled,
        "training_mode": camera.training_mode,
        "image_retention_days": camera.image_retention_days,
        "latest_image": latest_image_data(db, camera),
        "recognition_requested": camera.recognition_requested,
        "summary": learning_summary(db, camera),
        "learning_status": learning_status(db, camera) if user.role == "admin" else {},
        "series": [
            {"name": name, "sample_count": count}
            for name, count in db.execute(
                select(TrainingSample.series_name, func.count())
                .where(TrainingSample.camera_id == camera.id)
                .group_by(TrainingSample.series_name)
                .order_by(TrainingSample.series_name)
            )
        ],
        "analysis_enabled": camera.analysis_enabled,
        "min_confidence": camera.min_confidence,
        "sample_count": total,
        "approved_count": approved,
        "storage_bytes": size,
        "transform_key": transform_key(camera.image_rotation, camera.image_flip),
        "upload_limit_bytes": settings().max_image_bytes,
        "image_count_limit": None,
        "models": [
            model_data(model, camera)
            for model in db.scalars(
                select(ModelVersion)
                .where(ModelVersion.camera_id == camera.id)
                .order_by(ModelVersion.created_at.desc())
            )
        ] if user.role == "admin" else [],
    }


@router.put("/training/cameras/{camera_id}/config")
def set_training_config(camera_id: str, data: TrainingConfig, db: DB, _admin: Admin):
    camera = db.get(Camera, camera_id, with_for_update=True)
    if not camera:
        raise HTTPException(404, "Kamera nicht gefunden.")
    changed = camera.roi_json != data.roi.model_dump()
    if data.recognition_requested is not None:
        camera.recognition_requested = data.recognition_requested
    elif "analysis_enabled" in data.model_fields_set:
        camera.recognition_requested = data.analysis_enabled
    if changed:
        for model in db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera.id, ModelVersion.active.is_(True)
            )
        ):
            model.active = False
        camera.learning_dirty = True
    camera.roi_json, camera.min_confidence = data.roi.model_dump(), data.min_confidence
    db.flush()
    has_active = any(
        compatible(camera, m)
        for m in db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera.id, ModelVersion.active.is_(True)
            )
        )
    )
    camera.analysis_enabled = camera.recognition_requested and has_active
    if (
        not changed
        and data.recognition_requested is None
        and data.analysis_enabled
        and not has_active
    ):
        raise HTTPException(409, "Zuerst ein passendes Modell aktivieren.")
    db.commit()
    return training_overview(camera_id, db, _admin)


@router.get("/training/samples")
def samples(
    camera_id: str,
    db: DB,
    _admin: Admin,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=48, ge=1, le=200),
):
    require(db, Camera, camera_id)
    query = select(TrainingSample).where(TrainingSample.camera_id == camera_id)
    count = db.scalar(
        select(func.count())
        .select_from(TrainingSample)
        .where(TrainingSample.camera_id == camera_id)
    )
    return {
        "items": [
            sample_data(s)
            for s in db.scalars(
                query.order_by(TrainingSample.created_at.desc(), TrainingSample.id)
                .offset(offset)
                .limit(limit)
            )
        ],
        "total_count": count,
    }


@router.post("/training/samples", status_code=201)
def upload_sample(
    db: DB,
    admin: Admin,
    camera_id: str = Form(...),
    file: UploadFile = File(...),
    series_name: str = Form(default="Serie 1", min_length=1, max_length=100),
    image_mode: Literal["auto", "day", "night_ir", "unknown"] = Form(default="auto"),
    target_fill_level: float | None = Form(
        default=None, ge=0, le=100, allow_inf_nan=False
    ),
):
    camera = db.get(Camera, camera_id, with_for_update=True, populate_existing=True)
    if not camera:
        raise HTTPException(404, "Kamera nicht gefunden.")
    try:
        raw = file.file.read(settings().max_image_bytes + 1)
    finally:
        file.file.close()
    if not raw or len(raw) > settings().max_image_bytes:
        raise HTTPException(
            413,
            "Trainingsbild ist leer oder überschreitet das konfigurierte Uploadlimit.",
        )
    try:
        content, _, _ = prepare_image(raw, camera, RemoteFile("training", len(raw), ""))
    except Exception:
        raise HTTPException(
            422,
            "Keine gültige JPEG-, PNG- oder WebP-Bilddatei, oder Pixel-Limit überschritten.",
        )
    digest = hashlib.sha256(content).hexdigest()
    if db.scalar(
        select(TrainingSample.id).where(
            TrainingSample.camera_id == camera_id, TrainingSample.file_hash == digest
        )
    ):
        raise HTTPException(
            409, "Dieses Trainingsbild ist für die Kamera bereits vorhanden."
        )
    original = PurePosixPath((file.filename or "Trainingsbild").replace("\\", "/")).name
    original = "".join(c for c in original if ord(c) >= 32)[:200] or "Trainingsbild"
    sample = TrainingSample(
        camera_id=camera_id,
        filename=uuid4().hex + ".jpg",
        original_name=original,
        file_hash=digest,
        size_bytes=len(content),
        series_name=series_name.strip() or "Serie 1",
        target_fill_level=target_fill_level,
        approved=target_fill_level is not None,
        queue_state="confirmed" if target_fill_level is not None else "pending",
        window_managed=False,
        transform_key=transform_key(camera.image_rotation, camera.image_flip),
        created_by=admin.id,
    )
    path = sample_path(sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        sample.image_mode = (
            image_features(
                path, camera.roi_json or {"x": 0, "y": 0, "width": 1, "height": 1}
            )[1]
            if image_mode == "auto"
            else image_mode
        )
        db.add(sample)
        if sample.approved:
            camera.learning_dirty = True
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return sample_data(sample)


@router.get("/training/samples/{sample_id}/content")
def training_content(sample_id: str, db: DB, user: CurrentUser):
    sample = require(db, TrainingSample, sample_id)
    training_camera(db, sample.camera_id, user)
    if sample.discarded_at:
        raise HTTPException(410, "Bild wurde verworfen.")
    if sample.discarded_at or not sample_path(sample).is_file():
        raise HTTPException(410, "Trainingsbild ist nicht mehr vorhanden.")
    return FileResponse(
        sample_path(sample),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/training/samples/{sample_id}")
def label_sample(sample_id: str, data: SampleUpdate, db: DB, _admin: Admin):
    sample = require(db, TrainingSample, sample_id)
    camera = db.get(
        Camera, sample.camera_id, with_for_update=True, populate_existing=True
    )
    db.refresh(sample)
    previous = (
        sample.target_fill_level,
        sample.approved,
        sample.image_mode,
        sample.series_name,
    )
    if data.approved and data.target_fill_level is None:
        raise HTTPException(
            422, "Für freigegebene Trainingsbilder ist ein Soll-Füllstand erforderlich."
        )
    if not data.series_name.strip():
        raise HTTPException(422, "Aufnahmeserie darf nicht leer sein.")
    if sample.rejection_reason:
        camera.learning_dirty = True
    sample.rejection_reason = None
    for key, value in data.model_dump().items():
        setattr(sample, key, value)
    sample.series_name = sample.series_name.strip()
    sample.created_by = _admin.id
    sample.queue_state = "confirmed" if sample.approved else "pending"
    if previous != (
        sample.target_fill_level,
        sample.approved,
        sample.image_mode,
        sample.series_name,
    ):
        camera.learning_dirty = True
    db.commit()
    return sample_data(sample)


@router.delete("/training/samples/{sample_id}")
def delete_sample(sample_id: str, db: DB, _admin: Admin):
    sample = require(db, TrainingSample, sample_id)
    db.get(Camera, sample.camera_id, with_for_update=True, populate_existing=True)
    jobs = db.scalars(
        select(ModelVersion).where(
            ModelVersion.camera_id == sample.camera_id,
            ModelVersion.status.in_(["queued", "running"]),
        )
    )
    if any(any(row["id"] == sample.id for row in job.dataset_json) for job in jobs):
        raise HTTPException(
            409,
            "Bild wird von einem ausstehenden Training benötigt. Nach Abschluss erneut versuchen.",
        )
    if db.scalar(
        select(LearningRestoreSample.sample_id).where(
            LearningRestoreSample.sample_id == sample.id
        )
    ):
        raise HTTPException(
            409,
            "Bild wird von einem Wiederherstellungspunkt benötigt. Zuerst den Wiederherstellungspunkt löschen.",
        )
    sample_path(sample).unlink(missing_ok=True)
    # Explizit gelöschte Trainingsreferenzen nicht als vermeintliche Altbestände
    # beim nächsten Worker-Lauf erneut aus dem Puffer sammeln.
    db.execute(
        update(AnalysisResult)
        .where(AnalysisResult.review_sample_id == sample.id)
        .values(review_visible=False)
    )
    require(db, Camera, sample.camera_id).learning_dirty = True
    from .db import utcnow
    sample.discarded_at = utcnow()
    sample.queue_state = "discarded"
    sample.size_bytes = 0
    db.commit()
    return {"ok": True}


@router.post("/models/train", status_code=202)
def enqueue_training(data: TrainInput, db: DB, admin: Admin):
    camera = db.get(Camera, data.camera_id, with_for_update=True)
    if not camera:
        raise HTTPException(404, "Kamera nicht gefunden.")
    try:
        if data.managed and not camera.learning_enabled:
            raise ValueError("Zuerst den Lernmodus einschalten.")
        model = enqueue_model(db, camera, "Gemeinsamer Lernstand" if data.managed else data.name,
                              "all" if data.managed else data.image_mode, admin.id, managed=data.managed, trigger="manual")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.commit()
    if model is None:
        raise HTTPException(409, "Dieser bestätigte Datenstand wurde bereits trainiert. Neue Bewertungen hinzufügen oder einen fehlgeschlagenen Lauf erneut versuchen.")
    return model_data(model, camera)


@router.post("/models/{model_id}/activate")
def activate_model(model_id: str, db: DB, _admin: Admin):
    model = require(db, ModelVersion, model_id)
    camera = db.get(Camera, model.camera_id, with_for_update=True)
    if (
        model.status != "ready"
        or not compatible(camera, model)
        or not model_path(model).is_file()
    ):
        raise HTTPException(
            409,
            "Modell ist nicht fertig, fehlt oder passt nicht zur aktuellen ROI/Ausrichtung.",
        )
    if (
        hashlib.sha256(model_path(model).read_bytes()).hexdigest()
        != model.artifact_hash
    ):
        raise HTTPException(409, "Modellintegrität verletzt. Modell neu trainieren.")
    for other in db.scalars(
        select(ModelVersion).where(
            ModelVersion.camera_id == camera.id,
            ModelVersion.image_mode == model.image_mode,
            ModelVersion.active.is_(True),
        )
    ):
        other.active = False
    model.active = True
    camera.analysis_enabled = True
    camera.recognition_requested = True
    db.commit()
    return model_data(model, camera)


@router.post("/models/{model_id}/deactivate")
def deactivate_model(model_id: str, db: DB, _admin: Admin):
    model = require(db, ModelVersion, model_id)
    model.active = False
    db.flush()
    if not db.scalar(
        select(ModelVersion.id).where(
            ModelVersion.camera_id == model.camera_id, ModelVersion.active.is_(True)
        )
    ):
        camera = db.get(
            Camera, model.camera_id, with_for_update=True, populate_existing=True
        )
        camera.analysis_enabled = False
        camera.recognition_requested = False
    db.commit()
    return {"ok": True}


@router.get("/training/analyses")
def analyses(camera_id: str, db: DB, _admin: Admin):
    require(db, Camera, camera_id)
    rows = db.scalars(
        select(AnalysisResult)
        .where(
            AnalysisResult.camera_id == camera_id,
            AnalysisResult.review_visible.is_(True),
        )
        .order_by(AnalysisResult.created_at.desc())
        .limit(100)
    )
    return [
        {
            key: (aware(value).isoformat() if key == "measured_at" else value)
            for key in (
                "id",
                "source_import_id",
                "model_id",
                "measured_at",
                "raw_fill_level",
                "confidence",
                "image_mode",
                "status",
                "reason",
            )
            for value in [getattr(row, key)]
        }
        | {
            "sample_id": row.review_sample_id,
            "has_reference": bool(
                row.review_sample_id
                and sample_path(
                    require(db, TrainingSample, row.review_sample_id)
                ).is_file()
            )
            or db.scalar(
                select(ImageBuffer.id).where(
                    ImageBuffer.import_id == row.source_import_id
                )
            )
            is not None,
            "reviewed_fill_level": db.get(
                Measurement, row.measurement_id
            ).effective_fill_level
            if row.measurement_id
            else None,
        }
        for row in rows
    ]


@router.get("/training/analyses/{analysis_id}/content")
def reference_content(analysis_id: str, db: DB, user: CurrentUser):
    result = require(db, AnalysisResult, analysis_id)
    training_camera(db, result.camera_id, user)
    if result.discarded_at or not result.review_visible:
        raise HTTPException(410, "Referenzbild ist nicht verfügbar.")
    if result.review_sample_id:
        sample = db.get(TrainingSample, result.review_sample_id)
        if sample and not sample.discarded_at and sample_path(sample).is_file():
            return FileResponse(
                sample_path(sample),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
    image = db.scalar(
        select(ImageBuffer).where(ImageBuffer.import_id == result.source_import_id)
    )
    if image and image_path(image).is_file():
        return FileResponse(
            image_path(image),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    raise HTTPException(410, "Das frühere Referenzbild ist nicht mehr vorhanden.")


@router.get("/training/cameras/{camera_id}/retention-preview")
def preview_retention(camera_id: str, db: DB, _admin: Admin, days: int = Query(ge=1, le=365)):
    require(db, Camera, camera_id)
    return {"affected_unreviewed": retention_preview(db, camera_id, days)}


@router.put("/training/cameras/{camera_id}/learning")
def learning_switch(camera_id: str, data: LearningInput, db: DB, _admin: Admin):
    camera = db.get(Camera, camera_id, with_for_update=True)
    if not camera:
        raise HTTPException(404, "Kamera nicht gefunden.")
    if data.enabled is not None:
        camera.learning_enabled = data.enabled
    if data.training_mode is not None:
        camera.training_mode = data.training_mode
        if data.training_mode == "manual":
            from .db import utcnow
            result = db.execute(update(ModelVersion).where(
                ModelVersion.camera_id == camera.id, ModelVersion.status == "queued",
                ModelVersion.trigger == "automatic",
            ).values(status="cancelled", finished_at=utcnow()))
            if result.rowcount:
                camera.learning_dirty = True
    if data.image_retention_days is not None:
        camera.image_retention_days = data.image_retention_days
    db.commit()
    # Bereits lokal vorhandene Bilder sofort sichtbar machen, ohne FTP im Request.
    from .training_store import collect_buffer
    collect_buffer(db, camera)
    db.commit()
    return {"learning_enabled": camera.learning_enabled, "training_mode": camera.training_mode,
            "image_retention_days": camera.image_retention_days}


@router.post("/training/analyses/{analysis_id}/review")
def review_analysis(analysis_id: str, data: ReviewInput, db: DB, admin: Admin):
    result = require(db, AnalysisResult, analysis_id)
    camera = db.get(Camera, result.camera_id, with_for_update=True)
    if result.status not in ("pending", "accepted") or result.raw_fill_level is None:
        raise HTTPException(
            409, "Dieses Analyseergebnis ist nicht mehr zur Freigabe offen."
        )
    sample = (
        db.get(TrainingSample, result.review_sample_id)
        if result.review_sample_id
        else None
    )
    if sample is None and camera.learning_enabled:
        image = db.scalar(
            select(ImageBuffer).where(ImageBuffer.import_id == result.source_import_id)
        )
        if image and image_path(image).is_file():
            sample = capture_review_sample(db, camera, image)
            result.review_sample_id = sample.id if sample else None
    if result.status == "accepted" and sample is None:
        raise HTTPException(410, "Kein Trainingsbild mehr vorhanden.")
    if sample is not None:
        sample.rejection_reason = None
        sample.target_fill_level, sample.approved, sample.created_by = (
            data.fill_level,
            True,
            admin.id,
        )
        sample.queue_state = "confirmed"
        if data.series_name and data.series_name.strip():
            sample.series_name = data.series_name.strip()
        camera.learning_dirty = True
    if result.status == "pending":
        measurement = create_analysis_measurement(
            db, result, camera, data.fill_level, admin.id
        )
    else:
        measurement = db.get(Measurement, result.measurement_id)
        # Bereits übernommene Messwerte werden hier nicht nachträglich geändert.
    result.status = "reviewed"
    db.commit()
    return {
        "measurement_id": measurement.id,
        "status": result.status,
        "sample_id": sample.id if sample else None,
        "learning_queued": bool(camera.learning_enabled and camera.learning_dirty),
    }
