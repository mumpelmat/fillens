from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import aware
from .models import (
    Camera,
    Container,
    ImageBuffer,
    Measurement,
    AnalysisResult,
    AlertState,
)


def image_path(image: ImageBuffer) -> Path:
    from .config import settings

    return settings().data_dir / "buffer" / image.camera_id / image.filename


def latest_image(db: Session, camera_id: str) -> ImageBuffer | None:
    return db.scalar(
        select(ImageBuffer)
        .where(ImageBuffer.camera_id == camera_id)
        .order_by(ImageBuffer.captured_at.desc(), ImageBuffer.imported_at.desc())
        .limit(1)
    )


def measurement_data(measurement: Measurement) -> dict:
    return {
        key: (aware(value).isoformat() if isinstance(value, datetime) else value)
        for key in (
            "id",
            "container_id",
            "camera_id",
            "measured_at",
            "raw_fill_level",
            "corrected_fill_level",
            "effective_fill_level",
            "confidence",
            "absolute_level",
            "capacity_snapshot",
            "capacity_unit",
            "change_rate_percent",
            "change_rate_absolute",
            "operating_rate_percent",
            "operating_rate_absolute",
            "rate_revision",
            "rate_cycle",
            "cycle_break",
            "analysis_method",
            "model_version",
            "image_mode",
            "timestamp_source",
            "created_at",
        )
        for value in [getattr(measurement, key)]
    }


def update_rates(db: Session, measurement: Measurement) -> None:
    """Verspätete Erfassung korrigiert auch die direkte zeitliche Nachfolge."""
    from .rates import request_rebuild

    request_rebuild(db, measurement.container_id)
    next_time = db.scalar(
        select(func.min(Measurement.measured_at)).where(
            Measurement.container_id == measurement.container_id,
            Measurement.measured_at > measurement.measured_at,
        )
    )
    targets = [measurement]
    if next_time:
        targets.extend(
            db.scalars(
                select(Measurement).where(
                    Measurement.container_id == measurement.container_id,
                    Measurement.measured_at == next_time,
                )
            )
        )
    for target in targets:
        previous = db.scalar(
            select(Measurement)
            .where(
                Measurement.container_id == target.container_id,
                Measurement.measured_at < target.measured_at,
            )
            .order_by(Measurement.measured_at.desc(), Measurement.created_at.desc())
            .limit(1)
        )
        target.change_rate_percent = target.change_rate_absolute = None
        if previous:
            hours = (
                aware(target.measured_at) - aware(previous.measured_at)
            ).total_seconds() / 3600
            target.change_rate_percent = (
                target.effective_fill_level - previous.effective_fill_level
            ) / hours
            if (
                target.capacity_snapshot == previous.capacity_snapshot
                and target.capacity_unit == previous.capacity_unit
            ):
                target.change_rate_absolute = (
                    target.absolute_level - previous.absolute_level
                ) / hours

    from .alerts import evaluate

    evaluate(db, db.get(Container, measurement.container_id))


def container_data(db: Session, container: Container) -> dict:
    camera = db.scalar(select(Camera).where(Camera.container_id == container.id))
    image = latest_image(db, camera.id) if camera else None
    recent = list(
        db.scalars(
            select(Measurement)
            .where(Measurement.container_id == container.id)
            .order_by(Measurement.measured_at.desc(), Measurement.created_at.desc())
            .limit(2)
        )
    )
    current = recent[0] if recent else None
    latest_analysis = (
        db.scalar(
            select(AnalysisResult)
            .where(AnalysisResult.camera_id == camera.id)
            .order_by(
                AnalysisResult.measured_at.desc(), AnalysisResult.created_at.desc()
            )
            .limit(1)
        )
        if camera
        else None
    )
    # Auswertung genau des neuesten Bildes: zeigt, ob es übernommen wurde.
    image_analysis = (
        db.scalar(
            select(AnalysisResult)
            .where(
                AnalysisResult.source_import_id == image.import_id,
                AnalysisResult.status != "failed",
            )
            .order_by(AnalysisResult.created_at.desc(), AnalysisResult.id.desc())
            .limit(1)
        )
        if image
        else None
    )
    rate_percent = current.change_rate_percent if current else None
    rate_absolute = (
        current.change_rate_absolute
        if current
        and current.capacity_snapshot == container.capacity
        and current.capacity_unit == container.capacity_unit
        else None
    )
    # Aktuelle Kapazität für die KPI; historischer Messwert bleibt unverändert.
    absolute = (
        container.capacity * current.effective_fill_level / 100 if current else None
    )
    state = db.get(AlertState, container.id)
    ready = container.rate_revision == container.calculated_revision and (
        current is None or current.rate_revision == container.calculated_revision
    )
    return {
        "rate_time_unit": container.rate_time_unit,
        "smoothing_minutes": container.smoothing_minutes,
        "event_threshold": container.event_threshold,
        "rate_revision": container.rate_revision,
        "calculated_revision": container.calculated_revision,
        "rate_status": "ready" if ready else "pending",
        "operating_rate_percent": current.operating_rate_percent
        if current and ready
        else None,
        "operating_rate_absolute": (
            current.operating_rate_absolute
            * (1000 if current.capacity_unit == "m3" else 1)
            / (1000 if container.capacity_unit == "m3" else 1)
        )
        if current and ready and current.operating_rate_absolute is not None
        else None,
        "alert_severity": state.severity
        if state and state.mode == container.display_mode and container.active
        else 0,
        "id": container.id,
        "site_id": container.site_id,
        "name": container.name,
        "description": container.description,
        "capacity": container.capacity,
        "capacity_unit": container.capacity_unit,
        "display_mode": container.display_mode,
        "active": container.active,
        "camera": {
            "id": camera.id,
            "name": camera.name,
            "timezone": camera.timezone,
            "ftp_connection_id": camera.ftp_connection_id,
            "ftp_root": camera.ftp_root,
            "image_rotation": camera.image_rotation,
            "image_flip": camera.image_flip,
            "active": camera.active,
            "scan_mode": camera.scan_mode,
            "date_pattern": camera.date_pattern,
            "recent_days": camera.recent_days,
            "full_scan_hours": camera.full_scan_hours,
            "detected_date_pattern": camera.detected_date_pattern,
            "analysis_enabled": camera.analysis_enabled,
            "learning_enabled": camera.learning_enabled,
            "min_confidence": camera.min_confidence,
            "roi": camera.roi_json,
        }
        if camera
        else None,
        "current_image": {
            "id": image.id,
            "captured_at": aware(image.captured_at).isoformat(),
            "timestamp_source": image.timestamp_source,
            "image_mode": image.image_mode,
            "has_measurement": db.scalar(
                select(Measurement.id).where(
                    Measurement.source_import_id == image.import_id
                )
            )
            is not None,
            "analysis": {
                "raw_fill_level": image_analysis.raw_fill_level,
                "confidence": image_analysis.confidence,
                "status": image_analysis.status,
                "reason": image_analysis.reason,
            }
            if image_analysis
            else None,
        }
        if image
        else None,
        "current_measurement": measurement_data(current) if current else None,
        "latest_analysis": {
            "id": latest_analysis.id,
            "raw_fill_level": latest_analysis.raw_fill_level,
            "confidence": latest_analysis.confidence,
            "status": latest_analysis.status,
            "reason": latest_analysis.reason,
            "measured_at": aware(latest_analysis.measured_at).isoformat(),
        }
        if latest_analysis
        else None,
        "absolute_level": absolute,
        "remaining_capacity": container.capacity - absolute
        if absolute is not None
        else None,
        "change_rate_percent": rate_percent,
        "change_rate_absolute": rate_absolute,
    }
