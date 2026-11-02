import io
import json
import os
from datetime import timedelta

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, utcnow
from app.ftp_scan import RemoteFile
from app.importer import ingest
from app.importer import import_camera
from app.ml_service import analyze_buffer, model_path, run_training_job, sample_path
from app.models import (
    AnalysisResult,
    Camera,
    ImageBuffer,
    Measurement,
    ModelVersion,
    TrainingSample,
)
from app.security import csrf_cookie
from conftest import authenticate
from test_scan import ftp_tree, camera_connection

ROI = {"x": 0, "y": 0, "width": 1, "height": 1}


def scene(level, seed=0):
    rng = np.random.default_rng(seed)
    pixels = np.full((96, 96, 3), [60, 85, 65], dtype=np.int16)
    rows = round(level * 96 / 100)
    if rows:
        pixels[-rows:] = [145, 120, 75]
    pixels += rng.integers(-4, 5, size=pixels.shape)
    output = io.BytesIO()
    Image.fromarray(np.uint8(pixels)).save(output, format="JPEG", quality=95)
    return output.getvalue()


def upload(admin, container, level, seed=0, series="Serie 1", label=True):
    data = {
        "camera_id": container["camera"]["id"],
        "series_name": series,
        "image_mode": "auto",
    }
    if label:
        data["target_fill_level"] = str(level)
    response = admin.post(
        "/api/training/samples",
        data=data,
        files={"file": (f"sample-{seed}.jpg", scene(level, seed), "image/jpeg")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def configure(admin, container, **changes):
    response = admin.put(
        f"/api/training/cameras/{container['camera']['id']}/config",
        json={"roi": ROI, "min_confidence": 0.5, **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def train_ready(admin, container, groups=3):
    configure(admin, container)
    samples = []
    for series in range(groups):
        for i, level in enumerate([0, 20, 40, 60, 80, 100]):
            samples.append(
                upload(admin, container, level, series * 20 + i, f"Serie {series}")
            )
    if groups == 1:
        for i, level in enumerate([10, 30, 50, 70, 90, 95]):
            samples.append(upload(admin, container, level, 40 + i, "Serie 0"))
    response = admin.post(
        "/api/models/train",
        json={"camera_id": container["camera"]["id"], "name": "Testmodell"},
    )
    assert response.status_code == 202, response.text
    model = response.json()
    assert model["status"] == "queued" and not model["active"]
    assert run_training_job() is True
    overview = admin.get(f"/api/training/cameras/{container['camera']['id']}").json()
    model = next(m for m in overview["models"] if m["id"] == model["id"])
    assert model["status"] == "ready", model
    return model, samples


def product_image(container, level=60, seed=300, offset=0):
    raw = scene(level, seed)
    timestamp = (utcnow() - timedelta(minutes=5) + timedelta(seconds=offset)).strftime(
        "%Y%m%d%H%M%S"
    )
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        assert ingest(
            db, camera, RemoteFile(f"/product-{seed}.jpg", len(raw), timestamp), raw
        )
        return db.scalar(
            select(ImageBuffer.id)
            .where(ImageBuffer.camera_id == camera.id)
            .order_by(ImageBuffer.captured_at.desc())
            .limit(1)
        )


def test_more_than_three_training_images_and_separate_product_buffer(admin, container):
    samples = [upload(admin, container, i * 10, i) for i in range(6)]
    for i in range(4):
        product_image(container, level=20 + i * 10, seed=200 + i, offset=i)
    overview = admin.get(f"/api/training/cameras/{container['camera']['id']}").json()
    assert overview["sample_count"] == 6 and overview["image_count_limit"] is None
    with SessionLocal() as db:
        assert len(list(db.scalars(select(ImageBuffer)))) == 3
        assert len(list(db.scalars(select(TrainingSample)))) == 6
        assert all(
            sample_path(sample).is_file()
            for sample in db.scalars(select(TrainingSample))
        )
    assert all(
        admin.get(f"/api/training/samples/{sample['id']}/content").status_code == 200
        for sample in samples
    )


def test_new_ftp_batch_is_analyzed_before_three_image_eviction(
    admin, container, ftp_tree
):
    root, port, _ = ftp_tree
    model, _ = train_ready(admin, container)
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    product_image(container, 60, 300)
    for index, level in enumerate([0, 20, 40, 60, 80, 100]):
        path = root / f"new-{index}.jpg"
        path.write_bytes(scene(level, 700 + index))
        stamp = (utcnow() - timedelta(seconds=200 - index * 20)).timestamp()
        os.utime(path, (stamp, stamp))
    with SessionLocal() as db:
        camera = camera_connection(db, container, port, scan_mode="recursive")
        assert import_camera(db, camera, {}) == 6
        assert len(list(db.scalars(select(ImageBuffer)))) == 3
        assert len(list(db.scalars(select(AnalysisResult)))) == 7


def test_training_upload_access_validation_and_duplicate(admin, container, monkeypatch):
    sample = upload(admin, container, 50, 1)
    duplicate = admin.post(
        "/api/training/samples",
        data={"camera_id": container["camera"]["id"]},
        files={"file": ("duplicate.jpg", scene(50, 1), "image/jpeg")},
    )
    assert duplicate.status_code == 409
    bad = admin.post(
        "/api/training/samples",
        data={"camera_id": container["camera"]["id"]},
        files={"file": ("bad.jpg", b"invalid", "image/jpeg")},
    )
    assert bad.status_code == 422
    monkeypatch.setattr(settings(), "max_image_bytes", 1024)
    huge = admin.post(
        "/api/training/samples",
        data={"camera_id": container["camera"]["id"]},
        files={"file": ("large.jpg", b"x" * 1025, "image/jpeg")},
    )
    assert huge.status_code == 413
    authenticate(admin, "user@example.test", "Leser-passwort-123")
    assert admin.get(f"/api/training/samples/{sample['id']}/content").status_code == 200
    assert (
        admin.get(f"/api/training/cameras/{container['camera']['id']}").status_code
        == 200
    )
    assert (
        admin.post(
            "/api/models/train", json={"camera_id": container["camera"]["id"]}
        ).status_code
        == 403
    )


def test_label_roi_and_training_requirements(admin, container):
    sample = upload(admin, container, 50, 1, label=False)
    payload = {
        "series_name": "A",
        "image_mode": "day",
        "approved": True,
        "target_fill_level": None,
    }
    assert (
        admin.put(f"/api/training/samples/{sample['id']}", json=payload).status_code
        == 422
    )
    assert (
        admin.put(
            f"/api/training/samples/{sample['id']}",
            json={**payload, "target_fill_level": 101},
        ).status_code
        == 422
    )
    assert (
        admin.put(
            f"/api/training/cameras/{container['camera']['id']}/config",
            json={"roi": {**ROI, "x": 0.9}},
        ).status_code
        == 422
    )
    assert (
        admin.post(
            "/api/models/train", json={"camera_id": container["camera"]["id"]}
        ).status_code
        == 409
    )
    configure(admin, container)
    assert (
        admin.post(
            "/api/models/train", json={"camera_id": container["camera"]["id"]}
        ).status_code
        == 409
    )
    assert (
        admin.put(
            f"/api/training/samples/{sample['id']}",
            json={**payload, "target_fill_level": 50},
        ).status_code
        == 200
    )


def test_training_group_validation_json_model_and_retention(admin, container):
    model, samples = train_ready(admin, container)
    assert model["metrics_json"]["series_count"] == 3
    assert model["metrics_json"]["validation_samples"] > 0
    assert model["metrics_json"]["mae_percent_points"] is not None
    with SessionLocal() as db:
        row = db.get(ModelVersion, model["id"])
        artifact = json.loads(model_path(row).read_bytes())
        assert artifact["schema"] == 2
        assert artifact["feature_version"] == "roi_geometry_v3"
        assert len(artifact["heads"]["day"]["trees"]) == 128
        assert len(list(db.scalars(select(TrainingSample)))) == 18
        assert all(
            sample_path(sample).is_file()
            for sample in db.scalars(select(TrainingSample))
        )
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    product_image(container)
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        assert analyze_buffer(db, camera) == 1
        result = db.scalar(select(AnalysisResult))
        assert result.status == "accepted", (result.confidence, result.reason)
        measurement = db.scalar(select(Measurement))
        assert measurement.analysis_method == "local_geometry_v2"
        assert (
            measurement.created_by is None and measurement.corrected_fill_level is None
        )
        assert abs(measurement.raw_fill_level - 60) < 20
        assert analyze_buffer(db, camera) == 0
    assert admin.delete(f"/api/training/samples/{samples[0]['id']}").status_code == 200
    with SessionLocal() as db:
        assert model_path(db.get(ModelVersion, model["id"])).exists()


def test_unvalidated_prototype_is_pending_and_review_survives_image_deletion(
    admin, container
):
    model, _ = train_ready(admin, container, groups=1)
    assert model["metrics_json"]["mae_percent_points"] is None
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    first = product_image(container)
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        assert analyze_buffer(db, camera) == 1
        result = db.scalar(select(AnalysisResult))
        result_id = result.id
        raw = result.raw_fill_level
        assert result.status == "pending" and result.confidence <= 0.45
        assert db.scalar(select(Measurement)) is None
    for i in range(4):
        product_image(container, 40 + i, 400 + i, i + 1)
    assert admin.get(f"/api/images/{first}/content").status_code == 404
    response = admin.post(
        f"/api/training/analyses/{result_id}/review", json={"fill_level": 55}
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        measurement = db.scalar(select(Measurement))
        assert (
            measurement.raw_fill_level == raw and measurement.corrected_fill_level == 55
        )
        assert (
            measurement.effective_fill_level == 55
            and measurement.timestamp_source == "ftp_mtime"
        )
    assert (
        admin.post(
            f"/api/training/analyses/{result_id}/review", json={"fill_level": 50}
        ).status_code
        == 409
    )


def test_roi_change_deactivates_model_and_artifact_tampering_is_rejected(
    admin, container
):
    model, _ = train_ready(admin, container)
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 200
    response = admin.put(
        f"/api/training/cameras/{container['camera']['id']}/config",
        json={
            "roi": {"x": 0, "y": 0, "width": 0.8, "height": 1},
            "analysis_enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["analysis_enabled"] is False
    assert not response.json()["models"][0]["active"]
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 409
    configure(admin, container)
    with SessionLocal() as db:
        model_path(db.get(ModelVersion, model["id"])).write_text('{"untrusted":true}')
    assert admin.post(f"/api/models/{model['id']}/activate").status_code == 409


def test_queued_training_protects_sources_and_missing_image_fails_cleanly(
    admin, container
):
    configure(admin, container)
    samples = [
        upload(admin, container, (i % 6) * 20, i, f"Serie {i % 2}") for i in range(12)
    ]
    model = admin.post(
        "/api/models/train", json={"camera_id": container["camera"]["id"]}
    ).json()
    assert admin.delete(f"/api/training/samples/{samples[0]['id']}").status_code == 409
    assert (
        admin.post(
            "/api/models/train", json={"camera_id": container["camera"]["id"]}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        sample_path(db.get(TrainingSample, samples[0]["id"])).unlink()
    assert run_training_job()
    overview = admin.get(f"/api/training/cameras/{container['camera']['id']}").json()
    assert (
        overview["models"][0]["status"] == "failed"
        and not overview["models"][0]["active"]
    )
