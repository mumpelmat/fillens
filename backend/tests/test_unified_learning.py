import io
from copy import deepcopy

import pytest
from app.db import SessionLocal
from app.learning import adopt_ready_models, qualified_modes
from app.ml_service import (
    analyze_buffer,
    model_path,
    queue_learning_jobs,
    run_training_job,
    sample_path,
)
from app.models import AnalysisResult, Camera, Measurement, ModelVersion, TrainingSample
from app.vision import LocalForestAnalyzer
from conftest import authenticate
from PIL import Image
from sqlalchemy import func, select
from test_training import ROI, configure, product_image, scene, train_ready, upload


def add_samples(admin, container, mode="day", groups=3, start=0):
    rows = []
    for group in range(groups):
        for i, level in enumerate([0, 20, 40, 60, 80, 100]):
            seed = start + group * 20 + i
            raw = scene(level, seed)
            if mode == "night_ir":
                out = io.BytesIO()
                Image.open(io.BytesIO(raw)).convert("L").save(
                    out, format="JPEG", quality=95
                )
                raw = out.getvalue()
            response = admin.post(
                "/api/training/samples",
                data={
                    "camera_id": container["camera"]["id"],
                    "series_name": f"{mode} série {group}",
                    "image_mode": "auto",
                    "target_fill_level": str(level),
                },
                files={"file": (f"{mode}-{seed}.jpg", raw, "image/jpeg")},
            )
            assert response.status_code == 201, response.text
            assert response.json()["image_mode"] == mode
            rows.append(response.json())
    return rows


def active(db, camera):
    return list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera, ModelVersion.active.is_(True)
            )
        )
    )


def test_day_adoption_night_requires_own_validation_and_no_repeat(
    admin, container, tmp_path
):
    configure(admin, container)
    add_samples(admin, container)
    queue_learning_jobs()
    assert run_training_job()
    camera_id = container["camera"]["id"]
    with SessionLocal() as db:
        model = active(db, camera_id)[0]
        assert model.managed and qualified_modes(model) == {"day"}
        assert db.get(Camera, camera_id).analysis_enabled
        first = model.id
    # Keine Rückwechsel auf einen älteren Kandidaten; gleiche Daten erzeugen keinen Job.
    with SessionLocal() as db:
        db.get(Camera, camera_id).learning_dirty = True
        db.commit()
    queue_learning_jobs()
    assert not run_training_job()
    adopt_ready_models()
    with SessionLocal() as db:
        assert active(db, camera_id)[0].id == first
    night = add_samples(admin, container, "night_ir", groups=1, start=100)
    queue_learning_jobs()
    assert run_training_job()
    with SessionLocal() as db:
        model = active(db, camera_id)[0]
        assert qualified_modes(model) == {"day"}
        import json

        analyzer = LocalForestAnalyzer(json.loads(model_path(model).read_text()))
        assert (
            analyzer.analyze(
                sample_path(db.get(TrainingSample, night[2]["id"]))
            ).confidence
            <= 0.45
        )
    info = admin.get(f"/api/training/cameras/{camera_id}").json()
    assert (
        info["summary"]["modes"]["day"]["ready"]
        and not info["summary"]["modes"]["night_ir"]["ready"]
    )


def test_both_modes_and_existing_night_must_not_be_lost(admin, container):
    configure(admin, container)
    add_samples(admin, container)
    night = add_samples(admin, container, "night_ir", groups=3, start=300)
    queue_learning_jobs()
    assert run_training_job()
    camera_id = container["camera"]["id"]
    with SessionLocal() as db:
        previous = active(db, camera_id)[0]
        assert qualified_modes(previous) == {"day", "night_ir"}, previous.metrics_json
        for s in night:
            db.get(TrainingSample, s["id"]).series_name = "Eine einzige Nachtserie"
        db.get(Camera, camera_id).learning_dirty = True
        db.commit()
    queue_learning_jobs()
    assert run_training_job()
    with SessionLocal() as db:
        assert active(db, camera_id)[0].id == previous.id
        assert db.scalar(select(func.count()).select_from(ModelVersion)) == 2


def test_learning_pause_and_recognition_wish_separate(admin, container):
    configure(admin, container)
    add_samples(admin, container)
    cid = container["camera"]["id"]
    queue_learning_jobs()
    assert (
        admin.put(
            f"/api/training/cameras/{cid}/learning", json={"enabled": False}
        ).status_code
        == 200
    )
    assert not run_training_job()
    with SessionLocal() as db:
        assert not active(db, cid)
    assert (
        admin.put(
            f"/api/training/cameras/{cid}/config",
            json={"roi": ROI, "recognition_requested": False},
        ).status_code
        == 200
    )
    admin.put(f"/api/training/cameras/{cid}/learning", json={"enabled": True})
    assert run_training_job()
    adopt_ready_models()
    with SessionLocal() as db:
        assert len(active(db, cid)) == 1
        assert not db.get(Camera, cid).analysis_enabled
    response = admin.put(
        f"/api/training/cameras/{cid}/config",
        json={"roi": ROI, "recognition_requested": True},
    )
    assert response.status_code == 200 and response.json()["analysis_enabled"]


def test_stale_dataset_and_roi_not_published(admin, container):
    configure(admin, container)
    rows = add_samples(admin, container)
    cid = container["camera"]["id"]
    queue_learning_jobs()
    response = admin.post(
        f"/api/training/cameras/{cid}/queue/sample_{rows[0]['id']}/confirm",
        json={"fill_level": 5},
    )
    assert response.status_code == 200, response.text
    assert run_training_job()
    with SessionLocal() as db:
        assert not active(db, cid)
        assert db.get(Camera, cid).learning_dirty
    queue_learning_jobs()
    assert run_training_job()
    with SessionLocal() as db:
        assert active(db, cid)
    admin.put(
        f"/api/training/cameras/{cid}/config",
        json={
            "roi": {"x": 0, "y": 0, "width": 0.8, "height": 1},
            "recognition_requested": True,
        },
    )
    with SessionLocal() as db:
        assert not active(db, cid) and db.get(Camera, cid).recognition_requested
    adopt_ready_models()
    with SessionLocal() as db:
        assert not active(db, cid)


def test_queue_unifies_proposals_and_confirm_preserves_measurement(admin, container):
    model, _ = train_ready(admin, container, groups=1)
    admin.post(f"/api/models/{model['id']}/activate")
    product_image(container)
    cid = container["camera"]["id"]
    with SessionLocal() as db:
        analyze_buffer(db, db.get(Camera, cid))
        result = db.scalar(select(AnalysisResult))
        sample_id = result.review_sample_id
        original = db.get(ModelVersion, model["id"])
        other = ModelVersion(
            camera_id=cid,
            name="Früherer Kandidat",
            version="older",
            config_json=deepcopy(original.config_json),
            dataset_json=deepcopy(original.dataset_json),
            training_sample_count=12,
            created_by=original.created_by,
            status="ready",
        )
        db.add(other)
        db.flush()
        db.add(
            AnalysisResult(
                camera_id=cid,
                source_import_id=result.source_import_id,
                model_id=other.id,
                review_sample_id=sample_id,
                measured_at=result.measured_at,
                raw_fill_level=42,
                confidence=0.4,
                status="pending",
            )
        )
        db.commit()
    response = admin.get(f"/api/training/cameras/{cid}/queue").json()
    assert response["pending_count"] == 1 and len(response["items"]) == 1
    item = response["items"][0]
    assert item["id"] == f"sample_{sample_id}" and item["has_reference"]
    skipped = admin.get(
        f"/api/training/cameras/{cid}/queue?exclude={item['id']}"
    ).json()
    assert skipped["pending_count"] == 1 and not skipped["items"]
    reply = admin.post(
        f"/api/training/cameras/{cid}/queue/{item['id']}/confirm",
        json={"fill_level": 55},
    )
    assert reply.status_code == 200 and reply.json()["measurement_created"]
    reply = admin.post(
        f"/api/training/cameras/{cid}/queue/{item['id']}/confirm",
        json={"fill_level": 65},
    )
    assert reply.status_code == 200 and reply.json()["measurement_unchanged"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Measurement)) == 1
        assert db.scalar(select(Measurement)).effective_fill_level == 55
        assert db.get(TrainingSample, sample_id).target_fill_level == 65
    assert admin.get(f"/api/training/cameras/{cid}/queue").json()["pending_count"] == 0


def test_queue_upload_roles_csrf_and_missing_reference(admin, container):
    configure(admin, container)
    row = upload(admin, container, 30, 2000, label=False)
    cid = container["camera"]["id"]
    item = f"sample_{row['id']}"
    response = admin.post(
        f"/api/training/cameras/{cid}/queue/{item}/confirm", json={"fill_level": 30}
    )
    assert response.status_code == 200 and not response.json()["measurement_created"]
    with SessionLocal() as db:
        assert db.scalar(select(Measurement)) is None
    assert (
        admin.get(f"/api/training/cameras/{cid}/queue?tab=confirmed").json()[
            "confirmed_count"
        ]
        == 1
    )
    with SessionLocal() as db:
        sample_path(db.get(TrainingSample, row["id"])).unlink()
    assert (
        admin.post(
            f"/api/training/cameras/{cid}/queue/{item}/confirm", json={"fill_level": 30}
        ).status_code
        == 410
    )
    admin.headers.pop("x-csrf-token")
    assert (
        admin.post(
            f"/api/training/cameras/{cid}/queue/{item}/confirm", json={"fill_level": 30}
        ).status_code
        == 403
    )
    authenticate(admin, "user@example.test", "Leser-passwort-123")
    assert admin.get(f"/api/training/cameras/{cid}/queue").status_code == 200
    assert (
        admin.post(
            f"/api/training/cameras/{cid}/queue/{item}/confirm", json={"fill_level": 30}
        ).status_code
        == 410
    )


def test_running_training_can_finish_during_pause_then_resume(admin, container):
    configure(admin, container)
    add_samples(admin, container)
    queue_learning_jobs()
    cid = container["camera"]["id"]

    def progress_factory(_camera_id):
        changed = False

        def progress(*_args):
            nonlocal changed
            if not changed:
                with SessionLocal() as db:
                    db.get(Camera, cid).learning_enabled = False
                    db.commit()
                changed = True

        return progress

    assert run_training_job(progress_factory)
    with SessionLocal() as db:
        assert not active(db, cid)
        assert db.scalar(select(ModelVersion)).status == "ready"
    admin.put(f"/api/training/cameras/{cid}/learning", json={"enabled": True})
    adopt_ready_models()
    with SessionLocal() as db:
        assert len(active(db, cid)) == 1


def test_bad_artifact_retry_and_no_downgrade_to_older_candidate(admin, container):
    configure(admin, container)
    add_samples(admin, container)
    queue_learning_jobs()
    cid = container["camera"]["id"]

    # Lernen während der Berechnung pausieren, damit der neue Kandidat noch nicht aktiv ist.
    def progress_factory(_):
        def progress(*_args):
            with SessionLocal() as db:
                db.get(Camera, cid).learning_enabled = False
                db.commit()

        return progress

    assert run_training_job(progress_factory)
    with SessionLocal() as db:
        model = db.scalar(select(ModelVersion))
        model_path(model).write_text("bad artifact")
        db.get(Camera, cid).learning_enabled = True
        db.commit()
    adopt_ready_models()
    info = admin.get(f"/api/training/cameras/{cid}").json()
    assert info["summary"]["can_retry"] and info["summary"]["state"] == "failed"
    assert admin.post(f"/api/training/cameras/{cid}/retry").status_code == 202
    assert run_training_job()
    with SessionLocal() as db:
        current = active(db, cid)[0]
        old = db.get(ModelVersion, model.id)
        # Ein älterer gültiger Kandidat derselben Datenbasis darf nicht wieder aktiviert werden.
        model_path(old).write_bytes(model_path(current).read_bytes())
        old.artifact_hash = current.artifact_hash
        old.status = "ready"
        db.commit()
    adopt_ready_models()
    with SessionLocal() as db:
        assert active(db, cid)[0].id == current.id


def test_downgrade_cannot_remove_mode_safety_metadata(admin, container):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import engine

    configure(admin, container)
    add_samples(admin, container)
    queue_learning_jobs()
    path = (
        Path(__file__).parents[1] / "migrations/versions/0006_gemeinsamer_lernstand.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Downgrade gesperrt"):
                module.downgrade()
    with SessionLocal() as db:
        assert db.scalar(select(ModelVersion)).managed
