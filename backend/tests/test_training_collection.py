from datetime import timedelta

from app.db import SessionLocal, aware, utcnow
from app.main import app
from app.ml_service import capture_review_sample, eligible_samples, sample_path
from app.models import (
    AnalysisResult,
    Camera,
    ImageBuffer,
    ImportRecord,
    Measurement,
    ModelVersion,
    TrainingCollection,
    TrainingFetchRecord,
    TrainingSample,
)
from app.training_collection import collect_training
from app.training_store import automatic_open_count, cleanup_discarded
from app.vision import camera_config, transform_key
from conftest import authenticate
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_scan import camera_connection, image
from test_scan import ftp_tree as ftp_tree
from test_training import configure, product_image, upload


def test_user_review_permissions_and_pause(admin, container):
    configure(admin, container)
    row = upload(admin, container, 40, 901, label=False)
    cid = container["camera"]["id"]
    base = f"/api/training/cameras/{cid}"
    item = f"sample_{row['id']}"
    with TestClient(app) as client:
        user = authenticate(client, "user@example.test", "Leser-passwort-123")
        assert user.get(base).json()["models"] == []
        assert user.get(base + "/queue").status_code == 200
        assert user.get(f"/api/training/samples/{row['id']}/content").status_code == 200
        for extra in ({"image_mode": "night_ir"}, {"series_name": "Fake"}):
            assert (
                user.post(
                    base + f"/queue/{item}/confirm", json={"fill_level": 40, **extra}
                ).status_code
                == 403
            )
        assert (
            user.post(
                base + f"/queue/{item}/confirm", json={"fill_level": 40}
            ).status_code
            == 200
        )
        assert (
            user.post(
                base + f"/queue/{item}/confirm", json={"fill_level": 45}
            ).status_code
            == 200
        )
        assert user.post(base + f"/queue/{item}/discard").status_code == 409
        assert user.put(base + "/learning", json={"enabled": False}).status_code == 403
        assert (
            user.put(
                base + "/config",
                json={"roi": {"x": 0, "y": 0, "width": 1, "height": 1}},
            ).status_code
            == 403
        )
        assert user.post(base + "/retry").status_code == 403
        assert user.delete(f"/api/training/samples/{row['id']}").status_code == 403
        assert (
            user.post(
                "/api/training/samples",
                data={"camera_id": cid},
                files={"file": ("x.jpg", b"x", "image/jpeg")},
            ).status_code
            == 403
        )
        admin.put(base + "/learning", json={"enabled": False})
        for path in (
            base,
            base + "/queue",
            f"/api/training/samples/{row['id']}/content",
        ):
            assert user.get(path).status_code == 403
        assert (
            user.post(
                base + f"/queue/{item}/confirm", json={"fill_level": 70}
            ).status_code
            == 403
        )
        assert user.post(base + f"/queue/{item}/discard").status_code == 403
        assert admin.get(base).status_code == 200
    with SessionLocal() as db:
        assert db.get(TrainingSample, row["id"]).target_fill_level == 45
        assert db.scalar(select(Measurement)) is None


def test_discard_is_durable_idempotent_and_csrf_protected(admin, container):
    configure(admin, container)
    row = upload(admin, container, 10, 902, label=False)
    cid = container["camera"]["id"]
    url = f"/api/training/cameras/{cid}/queue/sample_{row['id']}/discard"
    with SessionLocal() as db:
        sample = db.get(TrainingSample, row["id"])
        sample.origin = "camera"
        db.commit()
        assert automatic_open_count(db, cid) == 1
        path = sample_path(sample)
    assert admin.post(url).status_code == 200
    assert admin.post(url).status_code == 200
    assert not path.exists()
    assert admin.get(f"/api/training/cameras/{cid}/queue").json()["pending_count"] == 0
    with SessionLocal() as db:
        assert db.get(TrainingSample, row["id"]).discarded_at
        assert eligible_samples(db, db.get(Camera, cid)) == []
    admin.headers.pop("x-csrf-token")
    assert admin.post(url).status_code == 403


def test_discard_keeps_files_needed_by_jobs(admin, container):
    configure(admin, container)
    row = upload(admin, container, 20, 903, label=False)
    cid = container["camera"]["id"]
    with SessionLocal() as db:
        sample = db.get(TrainingSample, row["id"])
        path = sample_path(sample)
        model = ModelVersion(
            camera_id=cid,
            name="Job",
            version="job",
            config_json=camera_config(db.get(Camera, cid)),
            dataset_json=[{"id": sample.id}],
            training_sample_count=1,
            created_by=sample.created_by,
            status="queued",
        )
        db.add(model)
        db.commit()
        mid = model.id
    assert (
        admin.post(
            f"/api/training/cameras/{cid}/queue/sample_{row['id']}/discard"
        ).status_code
        == 200
    )
    assert path.exists()
    with SessionLocal() as db:
        db.get(ModelVersion, mid).status = "failed"
        db.commit()
        cleanup_discarded(db, cid)
    assert not path.exists()


def test_backfill_uses_newest_rolling_window_and_is_training_only(
    admin, container, ftp_tree
):
    root, port, calls = ftp_tree
    now = utcnow()
    for day in range(7):
        for i in range(12):
            stamp = now - timedelta(days=day, hours=1, minutes=i)
            image(
                root,
                stamp.strftime("%Y/%m/%d/") + f"{day}-{i}.jpg",
                20 + day * 25 + i,
                stamp,
            )
    stamp = now - timedelta(days=9)
    image(root, stamp.strftime("%Y/%m/%d/old.jpg"), 99, stamp)
    cid = container["camera"]["id"]
    with SessionLocal() as db:
        camera_connection(
            db, container, port, scan_mode="date", date_pattern="YYYY/MM/TT"
        )
        for _ in range(10):
            assert 0 <= collect_training(db, cid) <= 10
            if automatic_open_count(db, cid) == 50:
                break
        assert automatic_open_count(db, cid) == 50
        assert collect_training(db, cid) == 0
        rows = list(db.scalars(select(TrainingSample)))
        open_rows = [s for s in rows if s.queue_state == "pending"]
        assert len(open_rows) == 50
        oldest = min(open_rows, key=lambda row: aware(row.captured_at))
        newest_stamp = now - timedelta(seconds=30)
        image(
            root,
            newest_stamp.strftime("%Y/%m/%d/newest.jpg"),
            222,
            newest_stamp,
        )
        db.get(TrainingCollection, cid).scanned_at = now - timedelta(hours=2)
        db.commit()
        assert collect_training(db, cid) == 1
        assert automatic_open_count(db, cid) == 50
        assert db.get(TrainingSample, oldest.id).queue_state == "deferred"
        assert db.scalar(
            select(TrainingSample.id).where(
                TrainingSample.camera_id == cid,
                TrainingSample.captured_at >= newest_stamp - timedelta(seconds=1),
                TrainingSample.queue_state == "pending",
            )
        )
        rows = list(db.scalars(select(TrainingSample)))
        assert all(s.origin == "ftp" and not s.approved and s.captured_at for s in rows)
        for cls in (ImageBuffer, ImportRecord, Measurement, AnalysisResult):
            assert db.scalar(select(cls)) is None
        victim_id = rows[0].id
        digest = rows[0].file_hash
        previous = db.scalar(select(func.count()).select_from(TrainingFetchRecord))
    assert (
        admin.post(
            f"/api/training/cameras/{cid}/queue/sample_{victim_id}/discard"
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        assert collect_training(db, cid) == 0
        assert automatic_open_count(db, cid) == 50
        assert (
            db.scalar(select(func.count()).select_from(TrainingFetchRecord))
            >= previous
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TrainingSample)
                .where(TrainingSample.file_hash == digest)
            )
            == 1
        )
        camera = db.get(Camera, cid)
        camera.learning_enabled = False
        db.commit()
        assert collect_training(db, cid) == 0
    assert stamp.strftime("%Y/%m/%d") not in calls


def test_live_capture_honors_common_limit_and_tombstone(admin, container):
    configure(admin, container)
    cid = container["camera"]["id"]
    product_image(container)
    with SessionLocal() as db:
        camera = db.get(Camera, cid)
        image_row = db.scalar(select(ImageBuffer))
        first = capture_review_sample(db, camera, image_row)
        db.commit()
        first.discarded_at = utcnow()
        db.commit()
        assert capture_review_sample(db, camera, image_row) is None
        for i in range(50):
            row = TrainingSample(
                    camera_id=cid,
                    filename=f"{i}.jpg",
                    original_name="pending",
                    file_hash=str(i),
                    size_bytes=1,
                    origin="ftp",
                    captured_at=utcnow() - timedelta(minutes=10 + i),
                    window_managed=True,
                    series_name="Kamera",
                    transform_key=transform_key(0, False),
                )
            db.add(row)
            db.flush()
            sample_path(row).write_bytes(b"x")
        db.commit()
        assert automatic_open_count(db, cid) == 50
        first.discarded_at = None
        first.file_hash = "changed"
        db.commit()
        replacement = capture_review_sample(db, camera, image_row)
        assert replacement is not None
        assert automatic_open_count(db, cid) == 50
        assert db.scalar(
            select(func.count())
            .select_from(TrainingSample)
            .where(TrainingSample.queue_state == "deferred")
        ) >= 1


def test_date_window_includes_year_boundary_and_dst():
    from datetime import UTC, datetime
    from app.training_collection import training_paths

    now = datetime(2027, 1, 2, 12, tzinfo=UTC)
    paths = training_paths("YYYY/MM/TT", "Europe/Berlin", now, "/")
    assert "2026/12/26" in paths and "2027/01/02" in paths
    assert training_paths("MM/TT", "Europe/Berlin", now, "/2026") == [
        f"12/{n}" for n in range(31, 25, -1)
    ]
    now = datetime(2026, 3, 29, 22, 30, tzinfo=UTC)
    paths = training_paths("YYYY/MM/TT", "Europe/Berlin", now, "/")
    assert paths[0] == "2026/03/30" and paths[-1] == "2026/03/22"


def test_scan_limit_is_backed_off_and_configuration_change_retries(
    container, ftp_tree, monkeypatch
):
    from app.config import settings
    from app.models import TrainingCollection

    root, port, calls = ftp_tree
    now = utcnow()
    for i in range(5):
        image(
            root,
            now.strftime("%Y/%m/%d/") + f"{i}.jpg",
            30 + i * 20,
            now - timedelta(minutes=3),
        )
    cid = container["camera"]["id"]
    with SessionLocal() as db:
        camera_connection(
            db, container, port, scan_mode="date", date_pattern="YYYY/MM/TT"
        )
        monkeypatch.setattr(settings(), "max_scan_files", 2)
        assert collect_training(db, cid) == 0
        state = db.get(TrainingCollection, cid)
        assert state.status == "blocked" and state.retry_at
        before = len(calls)
        assert collect_training(db, cid) == 0 and len(calls) == before
        monkeypatch.setattr(settings(), "max_scan_files", 100)
        camera = db.get(Camera, cid)
        camera.image_flip = True
        db.commit()
        assert collect_training(db, cid) > 0


def test_pause_during_download_does_not_publish(container, ftp_tree, monkeypatch):
    from app import training_collection

    root, port, _ = ftp_tree
    now = utcnow()
    cid = container["camera"]["id"]
    image(root, now.strftime("%Y/%m/%d/one.jpg"), 93, now - timedelta(minutes=3))
    prepare = training_collection.prepare_image

    def pause(*args):
        result = prepare(*args)
        with SessionLocal() as other:
            other.get(Camera, cid).learning_enabled = False
            other.commit()
        return result

    monkeypatch.setattr(training_collection, "prepare_image", pause)
    with SessionLocal() as db:
        camera_connection(
            db, container, port, scan_mode="date", date_pattern="YYYY/MM/TT"
        )
        assert collect_training(db, cid) == 0
        assert db.scalar(select(TrainingSample)) is None


def test_duplicate_and_damaged_ftp_files_are_not_retried(container, ftp_tree):
    root, port, _ = ftp_tree
    now = utcnow()
    cid = container["camera"]["id"]
    for name in ["a.jpg", "b.jpg"]:
        image(root, now.strftime("%Y/%m/%d/") + name, 93, now - timedelta(minutes=3))
    bad = root / now.strftime("%Y/%m/%d/bad.jpg")
    bad.write_bytes(b"invalid image")
    with SessionLocal() as db:
        camera_connection(
            db, container, port, scan_mode="date", date_pattern="YYYY/MM/TT"
        )
        assert collect_training(db, cid) == 1
        assert db.scalar(select(func.count()).select_from(TrainingFetchRecord)) == 3
        assert collect_training(db, cid) == 0


def test_stable_bad_ftp_fingerprint_retries_twice_then_blocks(container):
    from datetime import timedelta

    from app.ftp_rejections import reject, retry_allowed
    from app.ftp_scan import RemoteFile

    cid = container["camera"]["id"]
    remote = RemoteFile("/bad.jpg", 12, "20261101090000")
    with SessionLocal() as db:
        first = reject(db, cid, remote, "image_invalid")
        db.commit()
        assert first.attempts == 1 and first.retry_at and not first.blocked
        assert not retry_allowed(db, cid, remote)
        first.retry_at = utcnow() - timedelta(seconds=1)
        db.commit()
        second = reject(db, cid, remote, "image_invalid")
        db.commit()
        assert second.attempts == 2 and second.retry_at and not second.blocked
        second.retry_at = utcnow() - timedelta(seconds=1)
        db.commit()
        third = reject(db, cid, remote, "image_invalid")
        db.commit()
        assert third.attempts == 3 and third.blocked and not retry_allowed(db, cid, remote)
        changed = RemoteFile("/bad.jpg", 13, "20261101090100")
        assert changed.fingerprint != remote.fingerprint
        assert retry_allowed(db, cid, changed)


def test_live_slot_occupied_during_download_is_respected(
    container, ftp_tree, monkeypatch
):
    from app import training_collection

    root, port, _ = ftp_tree
    now = utcnow()
    cid = container["camera"]["id"]
    image(root, now.strftime("%Y/%m/%d/one.jpg"), 93, now - timedelta(minutes=3))
    prepare = training_collection.prepare_image

    def occupy(*args):
        result = prepare(*args)
        with SessionLocal() as other:
            other.get(Camera, cid, with_for_update=True)
            other.add(
                TrainingSample(
                    camera_id=cid,
                    filename="last.jpg",
                    original_name="Live",
                    file_hash="last",
                    size_bytes=1,
                    origin="camera",
                    series_name="Kamera",
                    transform_key=transform_key(0, False),
                )
            )
            other.commit()
        return result

    monkeypatch.setattr(training_collection, "prepare_image", occupy)
    with SessionLocal() as db:
        camera_connection(
            db, container, port, scan_mode="date", date_pattern="YYYY/MM/TT"
        )
        for i in range(49):
            db.add(
                TrainingSample(
                    camera_id=cid,
                    filename=f"{i}.jpg",
                    original_name="Live",
                    file_hash=str(i),
                    size_bytes=1,
                    origin="camera",
                    series_name="Kamera",
                    transform_key=transform_key(0, False),
                )
            )
        db.commit()
        assert collect_training(db, cid) == 1
        assert automatic_open_count(db, cid) <= 50


def test_parallel_confirm_and_discard_cannot_both_win(admin, container):
    from concurrent.futures import ThreadPoolExecutor
    from app.db import engine
    import pytest

    if engine.dialect.name != "postgresql":
        pytest.skip("Zeilensperre wird mit PostgreSQL geprüft")
    row = upload(admin, container, 33, 999, label=False)
    cid = container["camera"]["id"]
    item = f"sample_{row['id']}"
    with TestClient(app) as first, TestClient(app) as second:
        authenticate(first, "user@example.test", "Leser-passwort-123")
        authenticate(second, "user@example.test", "Leser-passwort-123")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(
                first.post,
                f"/api/training/cameras/{cid}/queue/{item}/confirm",
                json={"fill_level": 33},
            )
            b = pool.submit(
                second.post, f"/api/training/cameras/{cid}/queue/{item}/discard"
            )
            codes = [a.result().status_code, b.result().status_code]
        assert codes in ([200, 409], [410, 200])
    with SessionLocal() as db:
        sample = db.get(TrainingSample, row["id"])
        assert not (sample.approved and sample.discarded_at)


def test_downgrade_does_not_reenable_discarded_references(admin, container):
    import importlib.util
    from pathlib import Path
    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.db import engine

    row = upload(admin, container, 33, 998, label=False)
    cid = container["camera"]["id"]
    assert (
        admin.post(
            f"/api/training/cameras/{cid}/queue/sample_{row['id']}/discard"
        ).status_code
        == 200
    )
    spec = importlib.util.spec_from_file_location(
        "migration0007",
        Path(__file__).parents[1] / "migrations/versions/0007_trainingssammlung.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Downgrade gesperrt"):
                module.downgrade()


def test_mlsd_timestamp_works_without_mdtm(container, ftp_tree, monkeypatch):
    from app import training_collection
    from app.ftp_scan import RemoteFile

    root, port, _ = ftp_tree
    now = utcnow()
    cid = container["camera"]["id"]
    image(root, now.strftime("%Y/%m/%d/one.jpg"), 93, now - timedelta(minutes=3))
    original = training_collection.stat

    def no_timestamp(ftp, path):
        row = original(ftp, path)
        return RemoteFile(row.path, row.size, "")

    monkeypatch.setattr(training_collection, "stat", no_timestamp)
    with SessionLocal() as db:
        camera_connection(
            db, container, port, scan_mode="date", date_pattern="YYYY/MM/TT"
        )
        assert collect_training(db, cid) == 1
