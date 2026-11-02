import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.db import SessionLocal
from app.learning import adopt_ready_models
from app.main import app
from app.ml_service import analyze_buffer, model_path
from app.models import (
    AnalysisResult,
    Camera,
    LearningRestorePoint,
    Measurement,
    ModelVersion,
    TrainingSample,
)
from app.vision import GEOMETRY_FEATURE_VERSION, image_features
from conftest import authenticate
from test_training import product_image, train_ready, upload


def test_geometry_features_rectify_quad_and_normalize_free_polygon(tmp_path):
    pixels = np.zeros((140, 180, 3), dtype=np.uint8)
    pixels[:70] = [55, 80, 65]
    pixels[70:] = [155, 120, 70]
    path = tmp_path / "scene.png"
    Image.fromarray(pixels).save(path)
    quad = {
        "points": [
            {"x": 0.2, "y": 0.05},
            {"x": 0.8, "y": 0.15},
            {"x": 0.95, "y": 0.95},
            {"x": 0.05, "y": 0.85},
        ]
    }
    free = {
        "points": [
            {"x": 0.25, "y": 0.05},
            {"x": 0.75, "y": 0.1},
            {"x": 0.92, "y": 0.55},
            {"x": 0.72, "y": 0.95},
            {"x": 0.12, "y": 0.8},
        ]
    }
    quad_features = image_features(path, quad, GEOMETRY_FEATURE_VERSION)[0]
    free_features = image_features(path, free, GEOMETRY_FEATURE_VERSION)[0]
    assert quad_features.shape == free_features.shape == (273,)
    assert np.isfinite(quad_features).all() and np.isfinite(free_features).all()
    # Die Merkmalsfolge ist von oben nach unten orientiert und erkennt die Kante.
    top_brightness = quad_features[0]
    bottom_brightness = quad_features[23 * 6]
    assert bottom_brightness > top_brightness


def test_calibrated_model_can_reach_75_percent(admin, container):
    model, _ = train_ready(admin, container)
    assert model["metrics_json"]["mode_quality"]["day"]["validation_samples"] >= 6
    assert model["metrics_json"]["mode_quality"]["day"]["estimated_reliability"] > 0.75
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    assert (
        admin.put(
            f"/api/training/cameras/{container['camera']['id']}/config",
            json={
                "roi": {"x": 0, "y": 0, "width": 1, "height": 1},
                "min_confidence": 0.75,
                "recognition_requested": True,
            },
        ).status_code
        == 200
    )
    product_image(container, 60, 300)
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        assert analyze_buffer(db, camera) == 1
        result = db.scalar(select(AnalysisResult))
        assert result.status == "accepted"
        assert result.confidence >= 0.75
        assert result.estimated_error is not None
        assert db.scalar(select(Measurement)).analysis_method == "local_geometry_v2"


def test_reset_restore_and_roles_preserve_measurements(admin, container):
    model, samples = train_ready(admin, container)
    camera_id = container["camera"]["id"]
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    product_image(container, 60, 310)
    with SessionLocal() as db:
        assert analyze_buffer(db, db.get(Camera, camera_id)) == 1
        old_generation = db.get(Camera, camera_id).learning_generation
        measurement_count = db.scalar(select(func.count()).select_from(Measurement))
    created = admin.post(f"/api/training/cameras/{camera_id}/restore-points")
    assert created.status_code == 201, created.text
    point_id = created.json()["id"]
    reset = admin.post(f"/api/training/cameras/{camera_id}/reset")
    assert reset.status_code == 200, reset.text
    newer = upload(admin, container, 30, 991, label=False)
    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        assert camera.learning_generation != old_generation
        assert not camera.analysis_enabled
        assert not db.scalar(
            select(func.count())
            .select_from(ModelVersion)
            .where(ModelVersion.camera_id == camera_id, ModelVersion.active.is_(True))
        )
        assert db.scalar(select(func.count()).select_from(Measurement)) == measurement_count
        assert db.scalar(
            select(func.count())
            .select_from(TrainingSample)
            .where(TrainingSample.camera_id == camera_id, TrainingSample.approved.is_(True))
        ) == 0
    restored = admin.post(
        f"/api/training/cameras/{camera_id}/restore-points/{point_id}/restore"
    )
    assert restored.status_code == 200, restored.text
    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        assert camera.learning_generation == old_generation
        assert camera.analysis_enabled
        assert db.get(TrainingSample, newer["id"]).approved is False
        assert all(db.get(TrainingSample, row["id"]).approved for row in samples)
        assert db.scalar(select(func.count()).select_from(Measurement)) == measurement_count
        active = db.scalar(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera_id, ModelVersion.active.is_(True)
            )
        )
        artifact = json.loads(model_path(active).read_text())
        assert artifact["schema"] == 2
        assert db.scalar(
            select(func.count())
            .select_from(LearningRestorePoint)
            .where(LearningRestorePoint.camera_id == camera_id)
        ) >= 3
    with TestClient(app) as client:
        user = authenticate(client, "user@example.test", "Leser-passwort-123")
        assert user.get(f"/api/training/cameras/{camera_id}/restore-points").status_code == 403
        assert user.post(f"/api/training/cameras/{camera_id}/reset").status_code == 403


def test_failed_challenger_does_not_replace_active_model(admin, container):
    model, _ = train_ready(admin, container)
    camera_id = container["camera"]["id"]
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    with SessionLocal() as db:
        active = db.get(ModelVersion, model["id"])
        candidate = ModelVersion(
            camera_id=camera_id,
            name="Schlechter Herausforderer",
            version="challenger",
            managed=True,
            learning_generation=active.learning_generation,
            algorithm="local_geometry_v2",
            image_mode="all",
            status="ready",
            active=False,
            config_json=active.config_json,
            dataset_json=active.dataset_json,
            metrics_json={
                **active.metrics_json,
                "challenger_passed": False,
                "mode_quality": {
                    "day": {
                        **active.metrics_json["mode_quality"]["day"],
                        "ready": True,
                        "challenger_safe": False,
                    }
                },
            },
            artifact_hash=active.artifact_hash,
            training_sample_count=active.training_sample_count,
            created_by=active.created_by,
        )
        db.add(candidate)
        db.flush()
        Path(model_path(candidate)).parent.mkdir(parents=True, exist_ok=True)
        Path(model_path(candidate)).write_bytes(model_path(active).read_bytes())
        db.commit()
        candidate_id = candidate.id
    adopt_ready_models()
    with SessionLocal() as db:
        assert db.get(ModelVersion, model["id"]).active
        assert not db.get(ModelVersion, candidate_id).active
