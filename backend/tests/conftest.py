import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete

_temp = TemporaryDirectory(prefix="fillens-tests-")
test_url = os.environ.get("FILLENS_TEST_DATABASE_URL")
if test_url:
    from sqlalchemy.engine import make_url

    if make_url(test_url).database != "fillens_test":
        raise RuntimeError(
            "Externe Tests dürfen ausschließlich die Datenbank fillens_test verwenden"
        )
os.environ["DATABASE_URL"] = test_url or "sqlite:///" + str(
    Path(_temp.name) / "test.db"
)
os.environ["DATA_DIR"] = _temp.name
os.environ["ML_DATA_DIR"] = str(Path(_temp.name) / "ml")
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["COOKIE_SECURE"] = "false"
os.environ["FTP_SETTLE_SECONDS"] = "0"

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import User
from app.security import csrf_cookie, password_hasher


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.add_all(
            [
                User(
                    email="admin@example.test",
                    password_hash=password_hasher.hash("Admin-passwort-123"),
                    role="admin",
                ),
                User(
                    email="user@example.test",
                    password_hash=password_hasher.hash("Leser-passwort-123"),
                    role="user",
                ),
            ]
        )
        db.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def authenticate(client, email="admin@example.test", password="Admin-passwort-123"):
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = client.cookies.get(csrf_cookie())
    return client


@pytest.fixture
def admin(client):
    return authenticate(client)


@pytest.fixture
def container(admin):
    site = admin.post("/api/sites", json={"name": "Werkstatt"}).json()
    response = admin.post(
        "/api/containers",
        json={
            "site_id": site["id"],
            "name": "Papierkorb",
            "capacity": 30,
            "capacity_unit": "liter",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
