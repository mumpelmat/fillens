from datetime import timedelta

import pytest
from sqlalchemy import select

from app import password_reset
from app.db import SessionLocal, utcnow
from app.models import AuthSession, PasswordReset, SMTPConfig, User
from app.security import digest, password_hasher
from conftest import authenticate
from test_operations import smtp_server  # noqa: F401, gemeinsamer lokaler SMTP-Testserver


@pytest.fixture
def reset_mail(monkeypatch):
    with SessionLocal() as db:
        db.add(SMTPConfig(id="main", host="mail.example.test", sender="fillens@example.test"))
        db.commit()
    sent = []
    def send(config, recipient, code):
        sent.append((recipient, code))
        return True
    monkeypatch.setattr(password_reset, "send_reset_mail", send)
    return sent


def request_code(client, email="admin@example.test"):
    return client.post("/api/auth/password-reset/request", json={"email": email})


def confirm(client, code, password="Mein-neues-Passwort-123"):
    return client.post("/api/auth/password-reset/confirm", json={"code": code, "password": password})


def test_requires_configured_smtp(client):
    assert client.get("/api/auth/password-reset/status").json() == {"available": False}
    assert request_code(client).status_code == 503
    with SessionLocal() as db:
        assert db.scalar(select(PasswordReset)) is None


def test_reset_is_one_time_and_revokes_all_sessions(client, reset_mail):
    authenticate(client)
    assert client.get("/api/auth/password-reset/status").json() == {"available": True}
    response = request_code(client, " ADMIN@example.test ")
    assert response.status_code == 202
    recipient, code = reset_mail[0]
    assert recipient == "admin@example.test" and len(code) == 32
    assert code not in response.text
    with SessionLocal() as db:
        row = db.get(PasswordReset, digest(code))
        assert row and code not in str(row.__dict__)
    assert confirm(client, code).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert confirm(client, code).status_code == 400
    assert client.post("/api/auth/login", json={"email": recipient, "password": "Admin-passwort-123"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": recipient, "password": "Mein-neues-Passwort-123"}).status_code == 200


def test_accounts_are_not_disclosed_and_inactive_accounts_cannot_reset(client, reset_mail):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "user@example.test"))
        user.active = False
        db.commit()
    known = request_code(client).json()
    assert request_code(client, "unknown@example.test").json() == known
    assert request_code(client, "user@example.test").json() == known
    assert len(reset_mail) == 1


@pytest.mark.parametrize("reason", ["expired", "changed", "inactive"])
def test_invalidated_codes_do_not_change_password(client, reset_mail, reason):
    request_code(client)
    code = reset_mail[0][1]
    with SessionLocal() as db:
        token = db.get(PasswordReset, digest(code))
        user = db.get(User, token.user_id)
        if reason == "expired": token.expires_at = utcnow() - timedelta(seconds=1)
        if reason == "changed": user.password_hash = password_hasher.hash("Anderes-neues-Passwort")
        if reason == "inactive": user.active = False
        expected = user.password_hash
        user_id = user.id
        db.commit()
    assert confirm(client, code).status_code == 400
    with SessionLocal() as db:
        assert db.get(User, user_id).password_hash == expected


def test_success_invalidates_other_codes_for_same_account(client, reset_mail):
    request_code(client)
    request_code(client)
    assert confirm(client, reset_mail[0][1]).status_code == 200
    assert confirm(client, reset_mail[1][1]).status_code == 400


def test_request_rate_limit_and_cross_site_request(client, reset_mail):
    for _ in range(5): assert request_code(client).status_code == 202
    assert len(reset_mail) == 3
    assert client.post("/api/auth/password-reset/request", json={"email": "admin@example.test"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/auth/password-reset/confirm", json={"code": reset_mail[0][1], "password": "Neues-Passwort-123"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_failed_mail_does_not_leave_a_usable_code(client, reset_mail, monkeypatch):
    monkeypatch.setattr(password_reset, "send_reset_mail", lambda *args: False)
    assert request_code(client).status_code == 202
    with SessionLocal() as db:
        assert db.scalar(select(PasswordReset)) is None


def test_invalid_password_does_not_consume_code(client, reset_mail):
    request_code(client)
    code = reset_mail[0][1]
    response = confirm(client, code, "short")
    assert response.status_code == 422
    assert code not in response.text and "short" not in response.text
    assert confirm(client, code).status_code == 200


def test_email_contains_only_intended_recovery_instructions(monkeypatch):
    messages = []
    monkeypatch.setattr(password_reset, "deliver_message", lambda config, message: messages.append(message))
    class Sender:
        def send(self, value): assert value is True
        def close(self): pass
    password_reset._mail_job(Sender(), {"sender": "fillens@example.test"}, "admin@example.test", "one-time-code")
    message = messages[0]
    assert message["To"] == "admin@example.test"
    assert "one-time-code" in message.get_content()
    assert "30 Minuten" in message.get_content()


def test_recovery_over_real_local_smtp(client, smtp_server):
    import email
    with SessionLocal() as db:
        db.add(SMTPConfig(id="main", host="127.0.0.1", port=smtp_server.server_address[1],
            encryption="none", sender="fillens@example.test"))
        db.commit()
    response = request_code(client)
    assert response.status_code == 202 and len(smtp_server.messages) == 1
    message = email.message_from_bytes(smtp_server.messages[0])
    content = message.get_payload(decode=True).decode(message.get_content_charset())
    code = content.split("Ihr einmaliger Rücksetzcode:")[1].split()[0]
    assert code not in response.text
    assert confirm(client, code).status_code == 200
    smtp_server.reject = True
    assert request_code(client).status_code == 202
    with SessionLocal() as db:
        assert db.scalar(select(PasswordReset)) is None


def test_concurrent_confirmation_consumes_code_once(client, reset_mail):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from app.db import engine
    from app.main import app
    if engine.dialect.name != "postgresql":
        pytest.skip("Parallelbestätigung wird gegen PostgreSQL geprüft")
    request_code(client)
    code = reset_mail[0][1]
    barrier = Barrier(2)
    def consume(index):
        with TestClient(app) as other:
            barrier.wait(timeout=5)
            return confirm(other, code, f"Neues-Passwort-{index}-123").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, range(2)))
    assert sorted(results) == [200, 400]
