from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from app import worker
from app.db import SessionLocal, engine, utcnow
from app.models import WorkerControl, WorkerStatus
from app.worker_control import (
    begin_run,
    finish_run,
    pending_run,
    recover_control,
    request_run,
)
from conftest import authenticate


def fresh_worker():
    with SessionLocal() as db:
        db.add(WorkerStatus(id="importer", phase="idle"))
        db.commit()


def test_manual_request_coalesced_and_completes(admin, monkeypatch):
    fresh_worker()
    assert admin.post("/api/system/worker/run", json={}).json()["status"] == "requested"
    assert admin.post("/api/system/worker/run", json={}).json()["status"] == "pending"
    assert admin.get("/api/system/status").json()["manual_run_pending"]
    assert pending_run()

    def cycle(_):
        status = admin.get("/api/system/status").json()
        assert status["worker_busy"] and not status["manual_run_pending"]
        assert status["worker_phase"] == "cycle"
        assert (
            admin.post("/api/system/worker/run", json={}).json()["status"] == "running"
        )

    monkeypatch.setattr(worker, "cycle", cycle)
    worker.run_once({})
    with SessionLocal() as db:
        control = db.get(WorkerControl, "importer")
        assert control.requested == control.claimed == control.completed == 1
        assert not control.running and control.started_at and control.finished_at
    status = admin.get("/api/system/status").json()
    assert not status["worker_busy"] and not status["manual_run_pending"]
    assert status["last_run_finished_at"]
    assert not pending_run()


def test_manual_wakes_poll_loop_without_waiting_for_interval(admin, monkeypatch):
    fresh_worker()
    admin.post("/api/system/worker/run", json={})

    def unexpected_sleep(_):
        raise AssertionError("Bei Anforderung darf nicht gewartet werden")

    monkeypatch.setattr(worker.time, "sleep", unexpected_sleep)
    worker.wait_for_next(worker.time.monotonic())


def test_request_roles_csrf_and_offline(admin):
    assert admin.post("/api/system/worker/run", json={}).status_code == 503
    fresh_worker()
    with SessionLocal() as db:
        row = db.get(WorkerStatus, "importer")
        row.heartbeat_at = utcnow() - timedelta(hours=1)
        db.commit()
    assert admin.post("/api/system/worker/run", json={}).status_code == 503
    admin.headers.pop("x-csrf-token")
    assert admin.post("/api/system/worker/run", json={}).status_code == 403
    authenticate(admin, "user@example.test", "Leser-passwort-123")
    assert admin.post("/api/system/worker/run", json={}).status_code == 403


def test_recovery_preserves_pending_and_failure_is_visible(admin, monkeypatch):
    fresh_worker()
    admin.post("/api/system/worker/run", json={})
    begin_run()
    with SessionLocal() as db:
        recover_control(db)
        db.commit()
    assert pending_run()

    def broken(_):
        raise RuntimeError("private-server-secret")

    monkeypatch.setattr(worker, "cycle", broken)
    with pytest.raises(RuntimeError):
        worker.run_once({})
    status = admin.get("/api/system/status").json()
    assert not status["worker_busy"] and not status["manual_run_pending"]
    assert status["worker_error"] and "private-server-secret" not in str(status)
    assert admin.post("/api/system/worker/run", json={}).json()["status"] == "requested"


def test_automatic_cycle_and_next_request_are_not_lost(admin):
    fresh_worker()
    begin_run()
    assert admin.post("/api/system/worker/run", json={}).json()["status"] == "running"
    finish_run()
    assert admin.post("/api/system/worker/run", json={}).json()["status"] == "requested"
    begin_run()
    finish_run()
    assert not pending_run()


def test_parallel_manual_clicks_create_only_one_request(admin):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL-Sperrprüfung")
    fresh_worker()

    def request():
        with SessionLocal() as db:
            state = request_run(db)
            db.commit()
            return state

    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(lambda _: request(), range(4)))
    assert states.count("requested") == 1 and states.count("pending") == 3
    with SessionLocal() as db:
        assert db.get(WorkerControl, "importer").requested == 1
