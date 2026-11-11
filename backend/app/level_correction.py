"""Füllstandskorrektur am aktuellen Kamerabild: Messwert und Lernlabel in einem Schritt."""

from fastapi import HTTPException
from sqlalchemy import select

from .models import (
    AnalysisResult,
    Camera,
    Container,
    ImageBuffer,
    Measurement,
    TrainingSample,
)
from .services import image_path, latest_image, update_rates
from .training_access import training_camera


def create_manual_measurement(
    db, container: Container, camera: Camera, image: ImageBuffer, fill_level: float,
    image_mode: str, user_id: str,
) -> Measurement:
    measurement = Measurement(
        container_id=container.id,
        camera_id=camera.id,
        source_import_id=image.import_id,
        measured_at=image.captured_at,
        raw_fill_level=None,
        corrected_fill_level=fill_level,
        effective_fill_level=fill_level,
        absolute_level=container.capacity * fill_level / 100,
        capacity_snapshot=container.capacity,
        capacity_unit=container.capacity_unit,
        image_mode=image_mode,
        timestamp_source=image.timestamp_source,
        created_by=user_id,
    )
    db.add(measurement)
    db.flush()
    update_rates(db, measurement)
    return measurement


def _learn(db, camera: Camera, image: ImageBuffer, fill_level: float, user) -> bool:
    from .learning_api import apply_confirmation
    from .ml_service import capture_review_sample, sample_path
    from .vision import transform_key

    if not image.training_sample_id and image_path(image).is_file():
        capture_review_sample(db, camera, image, explicit=True)
    sample = (
        db.get(TrainingSample, image.training_sample_id)
        if image.training_sample_id
        else None
    )
    # Verworfene oder abgelaufene Bilder und alte Kameradrehungen nicht lernen.
    if (
        sample is None
        or sample.discarded_at is not None
        or sample.queue_state == "expired"
        or not sample_path(sample).is_file()
        or sample.transform_key != transform_key(camera.image_rotation, camera.image_flip)
    ):
        return False
    apply_confirmation(db, camera, sample, fill_level, user)
    return True


def correct_level(db, container_id: str, image_id: str, fill_level: float, user) -> dict:
    from .ml_service import create_analysis_measurement

    container = db.get(
        Container, container_id, with_for_update=True, populate_existing=True
    )
    if container is None:
        raise HTTPException(404, "Behälter nicht gefunden.")
    camera_id = db.scalar(select(Camera.id).where(Camera.container_id == container.id))
    if camera_id is None:
        raise HTTPException(404, "Kamera nicht gefunden.")
    camera = training_camera(db, camera_id, user, lock=True)
    image = latest_image(db, camera.id)
    if image is None or image.id != image_id:
        raise HTTPException(409, "Neues Kamerabild verfügbar. Ansicht aktualisieren.")
    learned = _learn(db, camera, image, fill_level, user)
    results = list(
        db.scalars(
            select(AnalysisResult)
            .where(
                AnalysisResult.source_import_id == image.import_id,
                AnalysisResult.status != "failed",
                AnalysisResult.discarded_at.is_(None),
            )
            .order_by(AnalysisResult.created_at.desc(), AnalysisResult.id.desc())
        )
    )
    measurement = db.scalar(
        select(Measurement).where(Measurement.source_import_id == image.import_id)
    )
    if measurement is not None:
        # Bewusste Korrektur eines bereits übernommenen Messwerts des aktuellen Bildes.
        measurement.corrected_fill_level = fill_level
        measurement.effective_fill_level = fill_level
        measurement.absolute_level = measurement.capacity_snapshot * fill_level / 100
        if not measurement.analysis_method.startswith("manual"):
            measurement.analysis_method = "manual_review_v1"
        db.flush()
        update_rates(db, measurement)
    else:
        source = next((r for r in results if r.raw_fill_level is not None), None)
        if source is not None:
            measurement = create_analysis_measurement(
                db, source, camera, fill_level, user.id
            )
        else:
            measurement = create_manual_measurement(
                db, container, camera, image, fill_level, image.image_mode, user.id
            )
    for result in results:
        result.status = "reviewed"
        result.measurement_id = measurement.id
        if learned and result.review_sample_id is None:
            result.review_sample_id = image.training_sample_id
    return {"measurement": measurement, "learned": learned}
