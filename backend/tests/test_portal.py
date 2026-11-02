import io
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal, utcnow
from app.importer import RemoteFile, ingest, prepare_image, reconcile
from app.ftp_errors import ImportFailure
from app.models import Camera, FTPConnection, ImageBuffer, ImportRecord, Measurement
from app.security import csrf_cookie, decrypt
from conftest import authenticate


def jpeg(value: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 48), (value, value // 2, 20)).save(output, format="JPEG")
    return output.getvalue()


def add_image(container, index=0):
    raw = jpeg(30 + index * 20)
    timestamp = utcnow() - timedelta(hours=10 - index)
    remote = RemoteFile(
        f"/nested/{index}.jpg", len(raw), timestamp.strftime("%Y%m%d%H%M%S")
    )
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        assert ingest(db, camera, remote, raw)
        image = db.scalar(
            select(ImageBuffer)
            .where(ImageBuffer.camera_id == camera.id)
            .order_by(ImageBuffer.captured_at.desc())
        )
        return image.id


def test_auth_roles_csrf_and_logout(client, container):
    client.cookies.clear()
    client.headers.pop("x-csrf-token", None)
    assert client.get("/api/containers").status_code == 401
    authenticate(client, "user@example.test", "Leser-passwort-123")
    assert client.get("/api/containers").status_code == 200
    assert client.get("/api/system/status").status_code == 403
    assert client.post("/api/sites", json={"name": "Verboten"}).status_code == 403
    assert (
        client.post(
            f"/api/containers/{container['id']}/measurements",
            json={"image_id": "x", "fill_level": 30},
        ).status_code
        == 403
    )
    assert client.get("/api/ftp-connections").status_code == 403
    assert client.get("/api/users").status_code == 403
    authenticate(client)
    token = client.headers.pop("x-csrf-token")
    assert client.post("/api/sites", json={"name": "CSRF"}).status_code == 403
    client.headers["x-csrf-token"] = token
    assert (
        client.post(
            "/api/sites",
            json={"name": "CSRF"},
            headers={"Sec-Fetch-Site": "cross-site"},
        ).status_code
        == 403
    )
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_throttle_and_no_secret_echo(client):
    secret = "nicht-ausgeben-123"
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login", json={"email": "x@test.de", "password": secret}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"email": "x@test.de", "password": secret}
        ).status_code
        == 429
    )
    response = client.post(
        "/api/auth/login",
        json={"email": "x@test.de", "password": secret, "unexpected": secret},
    )
    assert response.status_code == 422
    assert secret not in response.text


def test_rolling_buffer_preserves_measurement_and_deduplicates(admin, container):
    first = add_image(container)
    result = admin.post(
        f"/api/containers/{container['id']}/measurements",
        json={"image_id": first, "fill_level": 50, "image_mode": "day"},
    )
    assert result.status_code == 201, result.text
    assert result.json()["absolute_level"] == 15
    assert result.json()["confidence"] is None
    assert result.json()["raw_fill_level"] is None
    assert (
        admin.post(
            f"/api/containers/{container['id']}/measurements",
            json={"image_id": first, "fill_level": 51},
        ).status_code
        == 409
    )
    for index in range(1, 5):
        add_image(container, index)
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(ImageBuffer).where(
                        ImageBuffer.camera_id == container["camera"]["id"]
                    )
                )
            )
            assert len(rows) <= 3
            from app.config import settings

            assert (
                len(
                    list(
                        (
                            settings().data_dir / "buffer" / container["camera"]["id"]
                        ).glob("*.jpg")
                    )
                )
                <= 3
            )
    assert admin.get(f"/api/images/{first}/content").status_code == 404
    history = admin.get(
        f"/api/containers/{container['id']}/measurements?period=all"
    ).json()
    assert history["total_count"] == 1
    assert history["items"][0]["effective_fill_level"] == 50
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        old_raw = jpeg(30)
        assert not ingest(
            db,
            camera,
            RemoteFile("/renamed-duplicate.jpg", len(old_raw), "20260101000000"),
            old_raw,
        )
        assert len(list(db.scalars(select(Measurement)))) == 1
        assert len(list(db.scalars(select(ImportRecord)))) == 6


def test_rates_and_capacity_snapshot(admin, container):
    for i, level in [(0, 40), (1, 50)]:
        image_id = add_image(container, i)
        assert (
            admin.post(
                f"/api/containers/{container['id']}/measurements",
                json={"image_id": image_id, "fill_level": level},
            ).status_code
            == 201
        )
    result = admin.get(f"/api/containers/{container['id']}/current").json()
    assert result["change_rate_percent"] == pytest.approx(10)
    assert result["change_rate_absolute"] == pytest.approx(3)
    assert result["remaining_capacity"] == 15
    result = admin.put(
        f"/api/containers/{container['id']}",
        json={
            "site_id": container["site_id"],
            "name": "Bunker",
            "capacity": 35,
            "capacity_unit": "m3",
        },
    )
    assert result.status_code == 200
    assert result.json()["absolute_level"] == 17.5
    history = admin.get(
        f"/api/containers/{container['id']}/measurements?period=all"
    ).json()
    assert history["items"][-1]["capacity_unit"] == "liter"
    assert history["items"][-1]["absolute_level"] == 15


def test_backdated_measurement_updates_successor(admin, container):
    images = [add_image(container, i) for i in range(3)]
    for index, level in [(0, 10), (2, 50), (1, 40)]:
        assert (
            admin.post(
                f"/api/containers/{container['id']}/measurements",
                json={"image_id": images[index], "fill_level": level},
            ).status_code
            == 201
        )
    current = admin.get(f"/api/containers/{container['id']}/current").json()
    assert current["change_rate_percent"] == pytest.approx(10)
    history = admin.get(
        f"/api/containers/{container['id']}/measurements?period=all"
    ).json()["items"]
    assert history[1]["change_rate_percent"] == pytest.approx(30)
    assert history[2]["change_rate_percent"] == pytest.approx(10)


def test_invalid_level_and_wrong_container_image(admin, container):
    image_id = add_image(container)
    other = admin.post(
        "/api/containers",
        json={"site_id": container["site_id"], "name": "Anderer", "capacity": 100},
    ).json()
    assert (
        admin.post(
            f"/api/containers/{other['id']}/measurements",
            json={"image_id": image_id, "fill_level": 50},
        ).status_code
        == 422
    )
    for value in [-1, 100.1]:
        assert (
            admin.post(
                f"/api/containers/{container['id']}/measurements",
                json={"image_id": image_id, "fill_level": value},
            ).status_code
            == 422
        )
    assert (
        admin.get(f"/api/containers/{container['id']}/current").json()[
            "current_measurement"
        ]
        is None
    )


def test_secret_encryption_and_validation(admin, container):
    secret = "  Passwort-mit-Leerzeichen  "
    data = {
        "name": "Test",
        "host": "127.0.0.1",
        "username": "camera",
        "password": secret,
    }
    response = admin.post("/api/ftp-connections", json=data)
    assert response.status_code == 201, response.text
    assert secret not in response.text and "encrypted_password" not in response.text
    with SessionLocal() as db:
        connection = db.get(FTPConnection, response.json()["id"])
        assert connection.encrypted_password != secret
        assert decrypt(connection.encrypted_password) == secret
    for changes in [
        {"root_path": "/a/../b"},
        {"root_path": "/ok\r\nDELE x"},
        {"host": "ftp://host"},
    ]:
        assert (
            admin.post("/api/ftp-connections", json={**data, **changes}).status_code
            == 422
        )
    assert (
        admin.put(
            f"/api/containers/{container['id']}/camera",
            json={"name": "x", "ftp_root": "../escape"},
        ).status_code
        == 422
    )
    assert (
        admin.post(
            "/api/containers",
            json={"site_id": container["site_id"], "name": "x", "capacity": 0},
        ).status_code
        == 422
    )


def test_exif_timezone_and_invalid_image(container):
    output = io.BytesIO()
    image = Image.new("RGB", (40, 30), "white")
    exif = Image.Exif()
    exif[36867] = "2026:01:01 12:00:00"
    image.save(output, format="JPEG", exif=exif)
    raw = output.getvalue()
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        content, captured, source = prepare_image(
            raw, camera, RemoteFile("/image.jpg", len(raw), "20260101140000")
        )
        assert source == "exif" and captured.isoformat() == "2026-01-01T11:00:00+00:00"
        with Image.open(io.BytesIO(content)) as normalized:
            assert not normalized.getexif()
        with pytest.raises(Exception):
            ingest(db, camera, RemoteFile("/bad.jpg", 7, "20260101000000"), b"not-jpg")
        assert not list(db.scalars(select(ImageBuffer)))


def test_orphan_cleanup(container):
    add_image(container)
    from app.config import settings

    path = settings().data_dir / "buffer" / container["camera"]["id"]
    (path / "aborted.jpg").write_bytes(b"orphan")
    with SessionLocal() as db:
        reconcile(db, db.get(Camera, container["camera"]["id"]))
        assert not (path / "aborted.jpg").exists()
        row = db.scalar(
            select(ImageBuffer).where(
                ImageBuffer.camera_id == container["camera"]["id"]
            )
        )
        (path / row.filename).unlink()
        reconcile(db, db.get(Camera, container["camera"]["id"]))
        assert not list(
            db.scalars(
                select(ImageBuffer).where(
                    ImageBuffer.camera_id == container["camera"]["id"]
                )
            )
        )


def test_incomplete_download_and_changed_remote_file(container):
    raw = jpeg(43)
    with SessionLocal() as db:
        camera = db.get(Camera, container["camera"]["id"])
        remote = RemoteFile("/same.jpg", len(raw), "20260101000000")
        with pytest.raises(ImportFailure, match="unvollständig"):
            ingest(db, camera, remote, raw[:-10])
        assert not list(db.scalars(select(ImportRecord)))
        assert ingest(db, camera, remote, raw)
        updated = jpeg(86)
        assert ingest(
            db, camera, RemoteFile("/same.jpg", len(updated), "20260101010000"), updated
        )
        assert len(list(db.scalars(select(ImageBuffer)))) == 2
        assert len(list(db.scalars(select(ImportRecord)))) == 2


def test_user_deactivation_revokes_session(admin):
    user = admin.post(
        "/api/users",
        json={"email": "new@test.de", "password": "Ein-neues-Passwort", "role": "user"},
    ).json()
    assert admin.put(f"/api/users/{user['id']}/active?active=false").status_code == 200
    own = admin.get("/api/auth/me").json()
    assert admin.put(f"/api/users/{own['id']}/active?active=false").status_code == 409


def test_history_sampling_keeps_endpoints(admin, container):
    for index in range(12):
        image_id = add_image(container, index)
        assert (
            admin.post(
                f"/api/containers/{container['id']}/measurements",
                json={"image_id": image_id, "fill_level": index * 5},
            ).status_code
            == 201
        )
    result = admin.get(
        f"/api/containers/{container['id']}/measurements?period=all&limit=10"
    ).json()
    assert result["total_count"] == 12 and result["sampled"] is True
    assert len(result["items"]) <= 10
    assert result["items"][0]["effective_fill_level"] == 0
    assert result["items"][-1]["effective_fill_level"] == 55
    full = admin.get(
        f"/api/containers/{container['id']}/measurements?period=all&limit=12"
    ).json()
    assert len(full["items"]) == 12 and full["sampled"] is False
