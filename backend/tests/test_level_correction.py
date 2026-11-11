from app.db import SessionLocal
from app.main import app
from app.models import ImageBuffer, Measurement, TrainingSample
from conftest import authenticate
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_portal import add_image


def model_measurement(container, image_id, level):
    with SessionLocal() as db:
        image = db.get(ImageBuffer, image_id)
        db.add(Measurement(
            container_id=container["id"], camera_id=container["camera"]["id"],
            source_import_id=image.import_id, measured_at=image.captured_at,
            raw_fill_level=level, effective_fill_level=level,
            absolute_level=container["capacity"] * level / 100,
            capacity_snapshot=container["capacity"], capacity_unit="liter",
            analysis_method="local_rf_v1", confidence=0.9,
            timestamp_source=image.timestamp_source,
        ))
        db.commit()


def test_user_corrects_model_value_and_labels_image(admin, container):
    older = add_image(container, 0)
    latest = add_image(container, 1)
    model_measurement(container, latest, 20)
    url = f"/api/containers/{container['id']}/level-correction"
    with TestClient(app) as client:
        user = authenticate(client, "user@example.test", "Leser-passwort-123")
        assert user.post(url, json={"image_id": older, "fill_level": 30}).status_code == 409
        assert user.post(url, json={"image_id": latest, "fill_level": 101}).status_code == 422
        response = user.post(url, json={"image_id": latest, "fill_level": 55})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["learned"] is True
        assert body["measurement"]["effective_fill_level"] == 55
        assert body["measurement"]["analysis_method"] == "manual_review_v1"
        # Zweite Korrektur desselben Bildes überschreibt Messwert und Label.
        assert user.post(url, json={"image_id": latest, "fill_level": 60}).status_code == 200
    with SessionLocal() as db:
        image = db.get(ImageBuffer, latest)
        rows = list(db.scalars(select(Measurement).where(
            Measurement.source_import_id == image.import_id)))
        assert len(rows) == 1
        assert rows[0].effective_fill_level == rows[0].corrected_fill_level == 60
        assert rows[0].raw_fill_level == 20
        assert rows[0].absolute_level == container["capacity"] * 0.6
        sample = db.get(TrainingSample, image.training_sample_id)
        assert sample.approved and sample.target_fill_level == 60
        assert sample.queue_state == "confirmed"
    queue = admin.get(f"/api/training/cameras/{container['camera']['id']}/queue?tab=confirmed").json()
    assert queue["confirmed_count"] >= 1


def test_correction_without_analysis_creates_manual_measurement(admin, container):
    latest = add_image(container, 0)
    response = admin.post(f"/api/containers/{container['id']}/level-correction",
                          json={"image_id": latest, "fill_level": 40})
    assert response.status_code == 200, response.text
    assert response.json()["measurement"]["analysis_method"] == "manual_v1"
    assert response.json()["learned"] is True
    current = admin.get(f"/api/containers/{container['id']}").json()
    assert current["current_measurement"]["effective_fill_level"] == 40
    assert current["current_image"]["has_measurement"] is True


def test_correction_follows_training_permission(admin, container):
    latest = add_image(container, 0)
    url = f"/api/containers/{container['id']}/level-correction"
    camera = container["camera"]["id"]
    assert admin.put(f"/api/training/cameras/{camera}/learning", json={"enabled": False}).status_code == 200
    with TestClient(app) as client:
        user = authenticate(client, "user@example.test", "Leser-passwort-123")
        assert user.post(url, json={"image_id": latest, "fill_level": 30}).status_code == 403
    assert admin.post(url, json={"image_id": latest, "fill_level": 30}).status_code == 200
    assert admin.post("/api/containers/unknown/level-correction",
                      json={"image_id": latest, "fill_level": 30}).status_code == 404
