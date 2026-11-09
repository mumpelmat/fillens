import io
import json

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.db import SessionLocal, utcnow
from app.ftp_scan import RemoteFile
from app.importer import ingest
from app.learning import dataset_snapshot, fingerprint
from app.main import app
from app.ml_service import analyze_buffer, eligible_samples, model_path, queue_learning_jobs, run_training_job, sample_path
from app.models import AnalysisResult, Camera, Measurement, ModelVersion, TrainingSample
from app.vision import LocalForestAnalyzer, camera_config
from conftest import authenticate
from test_training import configure, scene, upload
from test_unified_learning import add_samples


def lid(seed):
    pixels = np.full((96, 96, 3), [45, 65, 170], dtype=np.int16)
    pixels[::8] = [180, 190, 230]
    pixels += np.random.default_rng(seed).integers(-4, 5, size=pixels.shape)
    stream = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8)).save(stream, format="JPEG", quality=95)
    return stream.getvalue()


def negative(admin, container, seed=0, series="Serie 1"):
    cid = container["camera"]["id"]
    response = admin.post("/api/training/samples", data={"camera_id": cid, "series_name": series, "image_mode": "auto"},
                          files={"file": (f"deckel-{seed}.jpg", lid(seed), "image/jpeg")})
    assert response.status_code == 201, response.text
    row = response.json()
    response = admin.post(f"/api/training/cameras/{cid}/queue/sample_{row['id']}/unusable", json={"reason": "lid_closed"})
    assert response.status_code == 200, response.text
    return row


def test_negative_retained_correctable_and_restorable(admin, container):
    configure(admin, container)
    cid = container["camera"]["id"]
    base = f"/api/training/cameras/{cid}"
    row = negative(admin, container)
    with SessionLocal() as db:
        camera = db.get(Camera, cid)
        sample = db.get(TrainingSample, row["id"])
        assert sample.approved and sample.rejection_reason == "lid_closed"
        assert sample.target_fill_level is None and sample.discarded_at is None
        assert sample_path(sample).is_file()
        assert not eligible_samples(db, camera)
        assert eligible_samples(db, camera, include_unusable=True) == [sample]
        before = fingerprint(camera_config(camera), dataset_snapshot([sample]))
        assert not db.scalar(select(func.count()).select_from(Measurement))
    assert admin.get(base + "/queue").json()["total_count"] == 0
    confirmed = admin.get(base + "/queue?tab=confirmed").json()
    assert confirmed["items"][0]["rejection_reason"] == "lid_closed"
    assert admin.post(base + f"/queue/sample_{row['id']}/discard").status_code == 409
    point = admin.post(base + "/restore-points").json()["id"]
    assert admin.post(base + "/reset").status_code == 200
    with SessionLocal() as db:
        assert db.get(TrainingSample, row["id"]).rejection_reason is None
    assert admin.post(base + f"/restore-points/{point}/restore").status_code == 200
    assert admin.get(base + "/queue?tab=confirmed").json()["items"][0]["rejection_reason"] == "lid_closed"
    assert admin.post(base + f"/queue/sample_{row['id']}/confirm", json={"fill_level": 30}).status_code == 200
    with SessionLocal() as db:
        sample = db.get(TrainingSample, row["id"])
        assert sample.rejection_reason is None and sample.target_fill_level == 30
        assert before != fingerprint(camera_config(db.get(Camera, cid)), dataset_snapshot([sample]))
        assert not db.scalar(select(func.count()).select_from(Measurement))


def test_negative_roles_csrf_missing_and_discarded(admin, container):
    configure(admin, container)
    row = upload(admin, container, 30, label=False)
    cid = container["camera"]["id"]
    base = f"/api/training/cameras/{cid}"
    path = base + f"/queue/sample_{row['id']}/unusable"
    with TestClient(app) as client:
        user = authenticate(client, "user@example.test", "Leser-passwort-123")
        assert user.post(path, json={"reason": "lid_closed", "series_name": "test"}).status_code == 403
        assert user.post(path, json={"reason": "invalid"}).status_code == 422
        token = user.headers.pop("x-csrf-token")
        assert user.post(path, json={"reason": "lid_closed"}).status_code == 403
        user.headers["x-csrf-token"] = token
        assert user.post(path, json={"reason": "obstructed"}).status_code == 200
        admin.put(base + "/learning", json={"enabled": False})
        assert user.post(path, json={"reason": "lid_closed"}).status_code == 403
    removed = upload(admin, container, 40, seed=2, label=False)
    assert admin.post(base + f"/queue/sample_{removed['id']}/discard").status_code == 200
    assert admin.post(base + f"/queue/sample_{removed['id']}/unusable", json={"reason": "lid_closed"}).status_code == 410
    assert admin.post(base + "/queue/sample_missing/unusable", json={"reason": "lid_closed"}).status_code == 404


def test_classifier_adoption_rejection_no_measurement_and_manual_override(admin, container, tmp_path):
    configure(admin, container)
    add_samples(admin, container)
    queue_learning_jobs()
    assert run_training_job()
    cid = container["camera"]["id"]
    with SessionLocal() as db:
        previous = db.scalar(select(ModelVersion).where(ModelVersion.active.is_(True))).id
    for group in range(3):
        for index in range(3):
            negative(admin, container, group * 10 + index, f"day série {group}")
    queue_learning_jobs()
    assert run_training_job()
    with SessionLocal() as db:
        model = db.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)))
        assert model.id != previous
        assert model.metrics_json["training_samples"] == 18
        report = model.metrics_json["unusable_quality"]["day"]
        assert report["ready"] and report["false_rejections"] == 0
        assert report["validation_unusable"] == 9 and report["validation_usable"] == 18
        analyzer = LocalForestAnalyzer(json.loads(model_path(model).read_text()))
        good = tmp_path / "good.jpg"
        good.write_bytes(scene(60, 800))
        assert not analyzer.analyze(good).unusable
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(lid(800))
        decision = analyzer.analyze(bad)
        assert decision.unusable and decision.fill_level_percent is None
        camera = db.get(Camera, cid)
        assert ingest(db, camera, RemoteFile("/closed.jpg", bad.stat().st_size, utcnow().strftime("%Y%m%d%H%M%S")), bad.read_bytes())
        assert analyze_buffer(db, camera) == 1
        result = db.scalar(select(AnalysisResult))
        assert result.status == "discarded" and result.raw_fill_level is None
        assert result.discarded_at is None and result.review_visible
        sample = db.get(TrainingSample, result.review_sample_id)
        assert not sample.approved and sample.rejection_reason is None
        assert sample_path(sample).is_file()
        assert not db.scalar(select(func.count()).select_from(Measurement))
        item = sample.id
    base = f"/api/training/cameras/{cid}"
    pending = admin.get(base + "/queue").json()
    assert any(row["sample_id"] == item for row in pending["items"])
    response = admin.post(base + f"/queue/sample_{item}/confirm", json={"fill_level": 60})
    assert response.status_code == 200 and response.json()["measurement_created"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Measurement)) == 1
    # Nachträgliche Negativbewertung verändert keine bereits gespeicherte Messung.
    response = admin.post(base + f"/queue/sample_{item}/unusable", json={"reason": "lid_closed"})
    assert response.json()["measurement_unchanged"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Measurement)) == 1


def test_unvalidated_negatives_never_enable_rejection(admin, container):
    configure(admin, container)
    add_samples(admin, container)
    # Nur eine negative Aufnahmeserie: keine unabhängige Prüfung möglich.
    for i in range(6):
        negative(admin, container, i)
    queue_learning_jobs()
    assert run_training_job()
    with SessionLocal() as db:
        model = db.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)))
        assert not model.metrics_json["unusable_quality"]["day"]["ready"]
        artifact = json.loads(model_path(model).read_text())
        assert not artifact["unusable_heads"]
