import socketserver
from datetime import timedelta
from threading import Thread
from uuid import uuid4

import pytest
from app.alerts import evaluate, process_outbox
from app.db import SessionLocal, utcnow
from app.models import (
    Container,
    EmailOutbox,
    ImportRecord,
    Measurement,
    Notification,
    SMTPConfig,
)
from app.rates import rebuild, request_rebuild, run_rate_jobs
from app.services import container_data, update_rates
from conftest import authenticate
from sqlalchemy import func, select


def add(db, container, minute, level, base, capacity=None, capacity_unit=None):
    camera = container["camera"]["id"]
    imported = ImportRecord(
        camera_id=camera,
        fingerprint=uuid4().hex,
        remote_path="test.jpg",
        remote_size=1,
        remote_modified="",
        file_hash=uuid4().hex,
    )
    db.add(imported)
    db.flush()
    c = db.get(Container, container["id"])
    row = Measurement(
        container_id=c.id,
        camera_id=camera,
        source_import_id=imported.id,
        measured_at=base + timedelta(minutes=minute),
        effective_fill_level=level,
        absolute_level=level * (capacity or c.capacity) / 100,
        capacity_snapshot=capacity or c.capacity,
        capacity_unit=capacity_unit or c.capacity_unit,
        timestamp_source="test",
        created_by=None,
    )
    db.add(row)
    db.flush()
    update_rates(db, row)
    return row


def test_weighted_clipped_noise_and_modes(container):
    with SessionLocal() as db:
        base = utcnow() - timedelta(hours=3)
        rows = [
            add(db, container, m, l, base)
            for m, l in [(0, 0), (30, 10), (60, 30), (90, 20), (120, 30)]
        ]
        rebuild(db, container["id"])
        assert rows[1].operating_rate_percent == pytest.approx(20)
        assert rows[2].operating_rate_percent == pytest.approx(30)
        assert rows[3].operating_rate_percent == pytest.approx(10)
        assert rows[4].operating_rate_percent == pytest.approx(0)
        row = add(db, container, 135, 45, base)
        rebuild(db, container["id"])
        assert row.operating_rate_percent == pytest.approx(20)
        c = db.get(Container, container["id"])
        c.display_mode = "consumption"
        request_rebuild(db, c.id)
        rebuild(db, c.id)
        assert rows[3].operating_rate_percent == pytest.approx(20)
        assert rows[3].operating_rate_absolute == pytest.approx(6)
        assert row.operating_rate_percent == 0
        db.commit()


def test_auto_manual_gaps_capacity_and_equal_timestamps(container, admin):
    base = utcnow() - timedelta(hours=8)
    with SessionLocal() as db:
        rows = [
            add(db, container, m, l, base)
            for m, l in [
                (0, 50),
                (20, 60),
                (40, 5),
                (60, 15),
                (60, 16),
                (80, 26),
                (160, 36),
            ]
        ]
        rebuild(db, container["id"])
        assert rows[2].cycle_break and rows[2].operating_rate_percent is None
        assert rows[3].operating_rate_percent == 30
        assert rows[4].operating_rate_percent is None
        assert rows[5].operating_rate_percent == pytest.approx(30)
        assert rows[6].cycle_break
        changed = add(db, container, 180, 40, base, capacity=60)
        rebuild(db, container["id"])
        assert changed.cycle_break and changed.operating_rate_percent is None
        db.commit()
    event = admin.post(
        f"/api/containers/{container['id']}/events",
        json={
            "occurred_at": (base + timedelta(minutes=70)).isoformat(),
            "kind": "emptying",
        },
    ).json()
    run_rate_jobs()
    with SessionLocal() as db:
        after = db.get(Measurement, rows[5].id)
        assert after.cycle_break and after.operating_rate_percent is None
        assert db.scalar(select(func.count()).select_from(Measurement)) == 8
    assert (
        admin.put(
            f"/api/containers/{container['id']}/events/{event['id']}",
            json={
                "occurred_at": (base + timedelta(minutes=90)).isoformat(),
                "kind": "refill",
                "note": "Korrektur",
            },
        ).status_code
        == 200
    )
    assert (
        admin.delete(
            f"/api/containers/{container['id']}/events/{event['id']}"
        ).status_code
        == 200
    )
    run_rate_jobs()
    with SessionLocal() as db:
        assert db.get(Measurement, rows[5].id).operating_rate_percent is not None
    automatic = next(
        e
        for e in admin.get(f"/api/containers/{container['id']}/events").json()
        if e["source"] == "automatic"
    )
    assert (
        admin.delete(
            f"/api/containers/{container['id']}/events/{automatic['id']}"
        ).status_code
        == 200
    )
    run_rate_jobs()
    assert not admin.get(f"/api/containers/{container['id']}/events").json()


def test_backdated_versions_units_and_configuration(container, admin):
    base = utcnow() - timedelta(hours=2)
    with SessionLocal() as db:
        add(db, container, 0, 0, base)
        last = add(db, container, 60, 60, base)
        rebuild(db, container["id"])
        db.commit()
        assert (
            container_data(db, db.get(Container, container["id"]))["rate_status"]
            == "ready"
        )
        add(db, container, 30, 10, base)
        db.commit()
        assert (
            container_data(db, db.get(Container, container["id"]))[
                "operating_rate_percent"
            ]
            is None
        )
    run_rate_jobs()
    data = {
        k: container[k]
        for k in [
            "site_id",
            "name",
            "capacity",
            "capacity_unit",
            "display_mode",
            "description",
            "active",
        ]
    }
    data.update(smoothing_minutes=30, rate_time_unit="day", event_threshold=20)
    result = admin.put(f"/api/containers/{container['id']}", json=data)
    assert result.status_code == 200 and result.json()["rate_status"] == "pending"
    run_rate_jobs()
    with SessionLocal() as db:
        assert db.get(Measurement, last.id).operating_rate_percent == 100
        c = db.get(Container, container["id"])
        c.capacity_unit = "m3"
        c.capacity = 0.03
        db.commit()
        assert container_data(db, c)["operating_rate_absolute"] == pytest.approx(0.03)


@pytest.mark.parametrize(
    "mode,levels",
    [
        ("fill", [80, 79, 90, 89, 87, 90, 77, 80]),
        ("consumption", [20, 21, 10, 11, 13, 10, 23, 20]),
    ],
)
def test_alert_hysteresis_escalation_read_and_no_historical_mail(
    container, admin, mode, levels
):
    with SessionLocal() as db:
        c = db.get(Container, container["id"])
        c.display_mode = mode
        base = utcnow() - timedelta(hours=2)
        for i, level in enumerate(levels):
            add(db, container, i, level, base)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 3
        add(db, container, -10, 99 if mode == "fill" else 0, base)
        rebuild(db, c.id)
        evaluate(db, c)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 3
        assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 0
    notices = admin.get("/api/notifications").json()
    assert len(notices) == 3 and sum(n["active"] for n in notices) == 1
    assert (
        admin.post(f"/api/notifications/{notices[0]['id']}/read", json={}).status_code
        == 200
    )
    assert admin.get("/api/notifications").json()[0]["read"]
    authenticate(admin, "user@example.test", "Leser-passwort-123")
    assert not admin.get("/api/notifications").json()[0]["read"]
    assert (
        admin.post(f"/api/notifications/{notices[0]['id']}/read", json={}).status_code
        == 200
    )
    assert admin.get("/api/system/smtp").status_code == 403
    assert (
        admin.put(f"/api/containers/{container['id']}/alert-rules", json={}).status_code
        == 403
    )
    assert (
        admin.post(
            f"/api/containers/{container['id']}/events",
            json={"kind": "emptying", "occurred_at": utcnow().isoformat()},
        ).status_code
        == 403
    )
    admin.headers.pop("x-csrf-token")
    assert (
        admin.post(f"/api/notifications/{notices[0]['id']}/read", json={}).status_code
        == 403
    )


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 local test\r\n")
        data = False
        lines = []
        while line := self.rfile.readline():
            if data:
                if line == b".\r\n":
                    self.server.messages.append(b"".join(lines))
                    self.wfile.write(b"250 accepted\r\n")
                    data = False
                else:
                    lines.append(line)
            elif line.upper().startswith(b"DATA"):
                self.wfile.write(b"354 data\r\n")
                data = True
            elif line.upper().startswith(b"QUIT"):
                self.wfile.write(b"221 bye\r\n")
                return
            elif self.server.reject and line.upper().startswith(b"MAIL"):
                self.wfile.write(b"550 private-secret-must-not-escape\r\n")
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def smtp_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
    server.messages = []
    server.reject = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def test_smtp_local_success_failure_retry_and_secrets(container, admin, smtp_server):
    smtp = {
        "host": "127.0.0.1",
        "port": smtp_server.server_address[1],
        "encryption": "none",
        "username": "",
        "password": "top-secret",
        "sender": "portal@example.test",
    }
    response = admin.put("/api/system/smtp", json=smtp)
    assert response.status_code == 200 and response.json()["ready"]
    assert (
        "top-secret" not in response.text and "encrypted_password" not in response.text
    )
    assert admin.put("/api/system/smtp", json={**smtp, "password": None}).json()[
        "has_password"
    ]
    assert (
        admin.put(
            f"/api/containers/{container['id']}/alert-rules",
            json={"email_enabled": True, "email_recipients": ["user@example.test"]},
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        base = utcnow() - timedelta(hours=1)
        add(db, container, 0, 80, base)
        db.commit()
    assert process_outbox() == 1
    assert len(smtp_server.messages) == 1 and b"80 %" in smtp_server.messages[0]
    smtp_server.reject = True
    with SessionLocal() as db:
        add(db, container, 30, 90, base)
        db.commit()
    assert process_outbox() == 1
    notices = admin.get("/api/notifications").json()
    delivery = notices[0]["deliveries"][0]
    assert delivery["status"] == "retry" and "private-secret" not in str(notices)
    smtp_server.reject = False
    with SessionLocal() as db:
        pending = db.get(EmailOutbox, delivery["id"])
        pending.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert process_outbox() == 1
    with SessionLocal() as db:
        assert db.get(EmailOutbox, delivery["id"]).status == "sent"
        assert db.get(SMTPConfig, "main").encrypted_password != "top-secret"
    invalid = admin.put("/api/system/smtp", json={**smtp, "password": "leak\ninvalid"})
    assert invalid.status_code == 422 and "leak" not in invalid.text


def test_direct_warning_records_both_levels_but_one_email(container, admin):
    from app.models import AlertState

    with SessionLocal() as db:
        c = db.get(Container, container["id"])
        c.email_enabled = True
        c.email_recipients = ["user@example.test"]
        db.add(
            SMTPConfig(
                id="main",
                host="127.0.0.1",
                port=9,
                encryption="none",
                sender="portal@example.test",
            )
        )
        add(db, container, 0, 95, utcnow())
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 2
        assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 1
        c.active = False
        evaluate(db, c)
        db.commit()
        assert db.get(AlertState, c.id).severity == 0
        c.active = True
        evaluate(db, c)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 4
    bad = admin.put(
        f"/api/containers/{container['id']}/alert-rules",
        json={"fill_notice": 95, "fill_warning": 80},
    )
    assert bad.status_code == 422
    bad = admin.put(
        "/api/system/smtp", json={"sender": "bad\r\nBcc:other@example.test"}
    )
    assert bad.status_code == 422


def test_history_backfill_never_mails_old_threshold_crossings(container):
    with SessionLocal() as db:
        c = db.get(Container, container["id"])
        c.email_enabled = True
        c.email_recipients = ["user@example.test"]
        db.add(
            SMTPConfig(
                id="main",
                host="127.0.0.1",
                port=9,
                encryption="none",
                sender="portal@example.test",
            )
        )
        base = utcnow() - timedelta(hours=2)
        add(db, container, 60, 30, base)
        add(db, container, 0, 99, base)
        rebuild(db, c.id)
        evaluate(db, c)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
        assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 0


def test_parallel_backdated_measurement_invalidates_published_revision(container):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.db import engine

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL-Zeilensperren werden im PostgreSQL-Testlauf geprüft")
    base = utcnow() - timedelta(hours=2)
    with SessionLocal() as db:
        add(db, container, 0, 10, base)
        add(db, container, 40, 50, base)
        db.commit()
    started = Event()
    finished = Event()

    def late_input():
        with SessionLocal() as db:
            started.set()
            db.get(
                Container, container["id"], with_for_update=True, populate_existing=True
            )
            add(db, container, 20, 15, base)
            db.commit()
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool, SessionLocal() as calculating:
        rebuild(calculating, container["id"])
        job = pool.submit(late_input)
        assert started.wait(2) and not finished.wait(0.15)
        calculating.commit()
        job.result(timeout=5)
    with SessionLocal() as db:
        c = db.get(Container, container["id"])
        assert c.rate_revision > c.calculated_revision
        assert container_data(db, c)["rate_status"] == "pending"
        assert container_data(db, c)["operating_rate_percent"] is None
    run_rate_jobs()
    with SessionLocal() as db:
        c = db.get(Container, container["id"])
        assert c.rate_revision == c.calculated_revision
        assert container_data(db, c)["operating_rate_percent"] == pytest.approx(60)


def test_outbox_cancel_and_attempt_limit_without_network(container, monkeypatch):
    from app import alerts

    def unexpected_send(*_args):
        raise AssertionError("Für abgebrochene/ausgeschöpfte Aufträge kein SMTP")

    monkeypatch.setattr(alerts, "send_bounded", unexpected_send)
    with SessionLocal() as db:
        c = db.get(Container, container["id"])
        c.email_enabled = True
        c.email_recipients = ["user@example.test"]
        db.add(
            SMTPConfig(
                id="main",
                host="127.0.0.1",
                port=9,
                encryption="none",
                sender="portal@example.test",
            )
        )
        add(db, container, 0, 80, utcnow())
        db.commit()
        item = db.scalar(select(EmailOutbox))
        c.email_enabled = False
        db.commit()
    assert process_outbox() == 1
    with SessionLocal() as db:
        assert db.get(EmailOutbox, item.id).status == "cancelled"
        c = db.get(Container, container["id"])
        c.email_enabled = True
        item = db.get(EmailOutbox, item.id)
        item.status = "retry"
        item.attempts = 5
        item.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert process_outbox() == 0
    with SessionLocal() as db:
        assert db.get(EmailOutbox, item.id).status == "failed"
