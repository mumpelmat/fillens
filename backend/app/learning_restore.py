"""Geprüfte Wiederherstellungspunkte für lokale Lernstände."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .config import settings
from .models import (
    Camera,
    LearningRestorePoint,
    LearningRestoreSample,
    ModelVersion,
    TrainingSample,
)


def restore_directory(point: LearningRestorePoint) -> Path:
    return settings().ml_data_dir / "restore_points" / point.camera_id / point.id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_idle(db, camera_id: str) -> None:
    if db.scalar(
        select(ModelVersion.id).where(
            ModelVersion.camera_id == camera_id,
            ModelVersion.status.in_(["queued", "running"]),
        )
    ):
        raise ValueError(
            "Reset und Wiederherstellung sind während eines laufenden oder geplanten Trainings gesperrt."
        )


def _config(camera: Camera) -> dict:
    return {
        "roi": camera.roi_json,
        "rotation": camera.image_rotation,
        "flip": camera.image_flip,
        "recognition_requested": camera.recognition_requested,
        "analysis_enabled": camera.analysis_enabled,
        "learning_enabled": camera.learning_enabled,
        "min_confidence": camera.min_confidence,
        "learning_generation": camera.learning_generation,
    }


def create_restore_point(db, camera: Camera, user_id: str, reason="manual"):
    """Sichert DB-Zustand und aktive Artefakte vor einer Lernstandsänderung."""
    ensure_idle(db, camera.id)
    active = list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera.id,
                ModelVersion.active.is_(True),
            )
        )
    )
    approved = list(
        db.scalars(
            select(TrainingSample).where(
                TrainingSample.camera_id == camera.id,
                TrainingSample.approved.is_(True),
                TrainingSample.discarded_at.is_(None),
                TrainingSample.target_fill_level.is_not(None),
            )
        )
    )
    point = LearningRestorePoint(
        camera_id=camera.id,
        reason=reason,
        config_json=_config(camera),
        active_models_json=[],
        artifact_hashes_json={},
        approved_count=len(approved),
        created_by=user_id,
    )
    db.add(point)
    db.flush()
    directory = restore_directory(point)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        from .ml_service import model_path, sample_path

        model_rows, hashes = [], {}
        for model in active:
            source = model_path(model)
            if not source.is_file() or _sha256(source) != model.artifact_hash:
                raise ValueError(
                    "Ein aktives Modellartefakt fehlt oder hat eine ungültige Prüfsumme."
                )
            target = directory / f"{model.id}.json"
            shutil.copyfile(source, target)
            with target.open("rb") as handle:
                os.fsync(handle.fileno())
            copied_hash = _sha256(target)
            if copied_hash != model.artifact_hash:
                raise ValueError("Modellartefakt konnte nicht verlässlich gesichert werden.")
            hashes[model.id] = copied_hash
            model_rows.append(
                {
                    "id": model.id,
                    "artifact_hash": model.artifact_hash,
                    "learning_generation": model.learning_generation,
                    "algorithm": model.algorithm,
                    "image_mode": model.image_mode,
                }
            )
        point.active_models_json = model_rows
        point.artifact_hashes_json = hashes
        for sample in approved:
            path = sample_path(sample)
            if not path.is_file() or _sha256(path) != sample.file_hash:
                raise ValueError(
                    "Ein bestätigtes Trainingsbild fehlt oder hat eine ungültige Prüfsumme."
                )
            db.add(
                LearningRestoreSample(
                    restore_point_id=point.id,
                    sample_id=sample.id,
                    file_hash=sample.file_hash,
                    target_fill_level=sample.target_fill_level,
                    image_mode=sample.image_mode,
                    series_name=sample.series_name,
                    transform_key=sample.transform_key,
                    window_managed=sample.window_managed,
                    created_by=sample.created_by,
                )
            )
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "point": point.id,
                    "camera": camera.id,
                    "models": model_rows,
                    "approved_count": len(approved),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return point
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        db.delete(point)
        db.flush()
        raise


def point_data(point: LearningRestorePoint) -> dict:
    from .db import aware

    return {
        "id": point.id,
        "reason": point.reason,
        "approved_count": point.approved_count,
        "active_model_count": len(point.active_models_json or []),
        "created_at": aware(point.created_at).isoformat(),
    }


def reset_learning(db, camera: Camera, user_id: str):
    ensure_idle(db, camera.id)
    backup = create_restore_point(db, camera, user_id, "before_reset")
    for model in db.scalars(
        select(ModelVersion).where(ModelVersion.camera_id == camera.id)
    ):
        model.active = False
    for sample in db.scalars(
        select(TrainingSample).where(
            TrainingSample.camera_id == camera.id,
            TrainingSample.discarded_at.is_(None),
        )
    ):
        sample.approved = False
        sample.target_fill_level = None
        sample.created_by = None
        sample.queue_state = "deferred"
        sample.window_managed = True
    camera.learning_generation = str(uuid4())
    camera.analysis_enabled = False
    camera.learning_dirty = False
    from .training_store import rebalance_window

    rebalance_window(db, camera.id)
    return backup


def restore_learning(db, camera: Camera, point: LearningRestorePoint, user_id: str):
    ensure_idle(db, camera.id)
    if point.camera_id != camera.id:
        raise ValueError("Wiederherstellungspunkt gehört zu einer anderen Kamera.")
    samples = list(
        db.scalars(
            select(LearningRestoreSample).where(
                LearningRestoreSample.restore_point_id == point.id
            )
        )
    )
    from .ml_service import model_path, sample_path

    for snapshot in samples:
        sample = db.get(TrainingSample, snapshot.sample_id)
        if (
            not sample
            or sample.file_hash != snapshot.file_hash
            or not sample_path(sample).is_file()
            or _sha256(sample_path(sample)) != snapshot.file_hash
        ):
            raise ValueError(
                "Wiederherstellung nicht möglich: Ein gesichertes Trainingsbild fehlt oder wurde verändert."
            )
    directory = restore_directory(point)
    model_rows = point.active_models_json or []
    for saved in model_rows:
        source = directory / f"{saved['id']}.json"
        expected = (point.artifact_hashes_json or {}).get(saved["id"])
        model = db.get(ModelVersion, saved["id"])
        if not model or not source.is_file() or _sha256(source) != expected:
            raise ValueError(
                "Wiederherstellung nicht möglich: Ein Modellartefakt fehlt oder wurde verändert."
            )
    before = create_restore_point(db, camera, user_id, "before_restore")
    for sample in db.scalars(
        select(TrainingSample).where(
            TrainingSample.camera_id == camera.id,
            TrainingSample.discarded_at.is_(None),
        )
    ):
        sample.approved = False
        sample.target_fill_level = None
        sample.created_by = None
        sample.queue_state = "deferred"
        sample.window_managed = True
    for snapshot in samples:
        sample = db.get(TrainingSample, snapshot.sample_id)
        sample.approved = True
        sample.target_fill_level = snapshot.target_fill_level
        sample.image_mode = snapshot.image_mode
        sample.series_name = snapshot.series_name
        sample.transform_key = snapshot.transform_key
        sample.window_managed = snapshot.window_managed
        sample.created_by = snapshot.created_by
        sample.queue_state = "confirmed"
    for model in db.scalars(
        select(ModelVersion).where(ModelVersion.camera_id == camera.id)
    ):
        model.active = False
    for saved in model_rows:
        model = db.get(ModelVersion, saved["id"])
        source = directory / f"{model.id}.json"
        target = model_path(model)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".restore.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        model.artifact_hash = saved["artifact_hash"]
        model.active = True
        model.status = "ready"
    config = point.config_json
    camera.roi_json = config["roi"]
    camera.image_rotation = config["rotation"]
    camera.image_flip = config["flip"]
    camera.recognition_requested = config["recognition_requested"]
    camera.learning_enabled = config["learning_enabled"]
    camera.min_confidence = config["min_confidence"]
    camera.learning_generation = config["learning_generation"]
    camera.analysis_enabled = bool(model_rows and camera.recognition_requested)
    camera.learning_dirty = False
    from .training_store import rebalance_window

    rebalance_window(db, camera.id)
    return before


def delete_restore_point(db, point: LearningRestorePoint) -> Path:
    directory = restore_directory(point)
    for snapshot in list(
        db.scalars(
            select(LearningRestoreSample).where(
                LearningRestoreSample.restore_point_id == point.id
            )
        )
    ):
        db.delete(snapshot)
    db.delete(point)
    db.flush()
    return directory
