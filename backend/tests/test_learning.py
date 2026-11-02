import numpy as np
import pytest
from app.db import SessionLocal
from app.ml_service import analyze_buffer, queue_learning_jobs
from app.models import AnalysisResult, Camera, Measurement, ModelVersion, TrainingSample
from app.vision import LocalForestAnalyzer, camera_config, config_key, image_features
from conftest import authenticate
from PIL import Image
from sqlalchemy import select
from test_training import configure, product_image, train_ready, upload

POLYGON = {
    "points": [
        {"x": 0.1, "y": 0.1},
        {"x": 0.9, "y": 0.2},
        {"x": 0.75, "y": 0.9},
        {"x": 0.25, "y": 0.85},
    ]
}


def test_polygon_validation_masks_outside_and_supports_legacy(
    admin, container, tmp_path
):
    path = f"/api/training/cameras/{container['camera']['id']}/config"
    assert admin.put(path, json={"roi": POLYGON}).status_code == 200
    bowtie = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 1, "y": 1},
            {"x": 0, "y": 1},
            {"x": 1, "y": 0},
        ]
    }
    assert admin.put(path, json={"roi": bowtie}).status_code == 422
    assert (
        admin.put(path, json={"roi": {"points": [{"x": 0, "y": 0}] * 3}}).status_code
        == 422
    )
    assert (
        admin.put(
            path,
            json={
                "roi": {
                    "points": [{"x": -1, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]
                }
            },
        ).status_code
        == 422
    )
    # Zwei Bilder unterscheiden sich ausschließlich außerhalb des Dreiecks.
    import cv2

    points = np.array([[0, 0], [99, 0], [0, 99]], np.int32)
    mask = np.zeros((100, 100), np.uint8)
    cv2.fillPoly(mask, [points], 255)
    rgb = np.full((100, 100, 3), [80, 100, 70], np.uint8)
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    Image.fromarray(rgb).save(first)
    rgb[mask == 0] = [255, 0, 255]
    Image.fromarray(rgb).save(second)
    roi = {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}]}
    a = image_features(first, roi)
    b = image_features(second, roi)
    assert np.array_equal(a[0], b[0]) and a[1:] == b[1:]
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        assert camera_config(camera)["feature_version"] == "roi_geometry_v3"
        assert config_key(camera_config(camera)) == config_key(camera_config(camera))
    configure(admin, container)  # Rechtecke werden weiterhin akzeptiert.


def test_review_reference_survives_buffer_and_becomes_training_sample(admin, container):
    model, _ = train_ready(admin, container, groups=1)
    admin.post(f"/api/models/{model['id']}/activate")
    first = product_image(container)
    with SessionLocal() as db:
        assert analyze_buffer(db, db.get(Camera, container["camera"]["id"])) == 1
        result = db.scalar(select(AnalysisResult))
        result_id = result.id
        sample = db.get(TrainingSample, result.review_sample_id)
        assert sample and not sample.approved and sample.target_fill_level is None
    for i in range(4):
        product_image(container, 40 + i, 600 + i, i + 1)
    assert admin.get(f"/api/images/{first}/content").status_code == 404
    response = admin.get(f"/api/training/analyses/{result_id}/content")
    assert (
        response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    )
    assert (
        admin.post(
            f"/api/training/analyses/{result_id}/review",
            json={"fill_level": 55, "series_name": "Nachprüfung"},
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        sample = db.get(TrainingSample, sample.id)
        assert (
            sample.approved
            and sample.target_fill_level == 55
            and sample.series_name == "Nachprüfung"
        )
        assert db.scalar(select(Measurement)).effective_fill_level == 55
        assert db.get(Camera, container["camera"]["id"]).learning_dirty
    queue_learning_jobs()
    with SessionLocal() as db:
        assert db.scalar(select(ModelVersion).where(ModelVersion.status == "queued"))
    overview = admin.get(f"/api/training/cameras/{container['camera']['id']}").json()
    assert any(s["name"] == "Nachprüfung" for s in overview["series"])
    assert overview["learning_status"]["state"] == "training"
    authenticate(admin, "user@example.test", "Leser-passwort-123")
    assert admin.get(f"/api/training/analyses/{result_id}/content").status_code == 200


@pytest.mark.parametrize("groups", [1, 3])
def test_learning_off_keeps_inference_but_no_review_images_or_rows(
    admin, container, groups
):
    model, _ = train_ready(admin, container, groups=groups)
    admin.post(f"/api/models/{model['id']}/activate")
    camera_id = container["camera"]["id"]
    assert (
        admin.put(
            f"/api/training/cameras/{camera_id}/learning", json={"enabled": False}
        ).status_code
        == 200
    )
    product_image(container)
    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        before = len(list(db.scalars(select(TrainingSample))))
        assert camera.analysis_enabled
        assert analyze_buffer(db, camera) == 1
        assert len(list(db.scalars(select(TrainingSample)))) == before
        result = db.scalar(select(AnalysisResult))
        assert result.status == ("skipped" if groups == 1 else "accepted")
        assert result.review_sample_id is None and not result.review_visible
        assert (db.scalar(select(Measurement)) is not None) == (groups == 3)
    image = next(
        c for c in admin.get("/api/containers").json() if c["id"] == container["id"]
    )["current_image"]
    assert image["has_measurement"] == (groups == 3)
    assert image["analysis"]["status"] == ("skipped" if groups == 1 else "accepted")
    assert image["analysis"]["raw_fill_level"] is not None
    assert admin.get(f"/api/training/analyses?camera_id={camera_id}").json() == []
    assert (
        admin.put(
            f"/api/training/cameras/{camera_id}/learning", json={"enabled": True}
        ).status_code
        == 200
    )
    # Neue Bilder sammeln wieder; frühere pausierte werden nicht rückwirkend archiviert.
    product_image(container, 45, 780, 1)
    with SessionLocal() as db:
        assert analyze_buffer(db, db.get(Camera, camera_id)) == 1
    assert len(admin.get(f"/api/training/analyses?camera_id={camera_id}").json()) == 1


def test_polygon_trains_compatible_model(admin, container):
    configure(admin, container)
    for i, level in enumerate([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]):
        upload(admin, container, level, 900 + i, series=f"Serie {i % 2}")
    result = admin.put(
        f"/api/training/cameras/{container['camera']['id']}/config",
        json={"roi": POLYGON},
    )
    assert result.status_code == 200
    queued = admin.post(
        "/api/models/train", json={"camera_id": container["camera"]["id"]}
    )
    assert queued.status_code == 202
    import json

    from app.ml_service import model_path, run_training_job

    run_training_job()
    with SessionLocal() as db:
        model = db.get(ModelVersion, queued.json()["id"])
        assert model.status == "ready"
        LocalForestAnalyzer(json.loads(model_path(model).read_text()))
        assert (
            camera_config(db.get(Camera, container["camera"]["id"]))
            == model.config_json
        )


def test_legacy_reference_preserved_and_explicit_deletion_not_undone(admin, container):
    from app.models import ImageBuffer
    from app.ml_service import preserve_review_references
    from app.vision import transform_key

    model, _ = train_ready(admin, container, groups=1)
    image_id = product_image(container, 55, 1800)
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        image = db.get(ImageBuffer, image_id)
        old_transform = image.transform_key
        result = AnalysisResult(
            camera_id=camera.id,
            source_import_id=image.import_id,
            model_id=model["id"],
            measured_at=image.captured_at,
            raw_fill_level=55,
            confidence=0.4,
            status="pending",
            timestamp_source="ftp_mtime",
        )
        db.add(result)
        camera.image_rotation = 90
        db.commit()
        preserve_review_references(db, camera)
        assert result.review_sample_id
        sample = db.get(TrainingSample, result.review_sample_id)
        assert sample.transform_key == old_transform
        assert sample.transform_key != transform_key(90, False)
        sample_id = sample.id
    assert admin.delete(f"/api/training/samples/{sample_id}").status_code == 200
    with SessionLocal() as db:
        preserve_review_references(db, db.get(Camera, container["camera"]["id"]))
        assert db.get(TrainingSample, sample_id) is None
        assert not db.get(AnalysisResult, result.id).review_visible
