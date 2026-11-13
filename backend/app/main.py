import logging
import math
from datetime import timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from . import schemas
from .config import settings
from .db import aware, utcnow
from .models import (
    AuthSession,
    AnalysisResult,
    Camera,
    Container,
    FTPConnection,
    ImageBuffer,
    LoginAttempt,
    Measurement,
    Site,
    User,
    WorkerStatus,
    WorkerControl,
    ModelVersion,
)
from .security import (
    Admin,
    CurrentUser,
    DB,
    DUMMY_HASH,
    csrf_cookie,
    digest,
    encrypt,
    new_session,
    password_hasher,
    session_cookie,
)
from .services import container_data, image_path, measurement_data, update_rates
from .training_api import router as training_router
from .operations_api import router as operations_router
from .rates import request_rebuild
from .alerts import evaluate
from .worker_control import request_run, worker_fresh
from .password_reset import router as password_reset_router
from .level_correction import correct_level, create_manual_measurement
from .user_admin import delete_user, set_password, set_role

app = FastAPI(
    title="Fillens API",
    version="0.7.1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
logger = logging.getLogger("fillens.api")
app.include_router(training_router)
app.include_router(operations_router)
app.include_router(password_reset_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        return JSONResponse(
            {"detail": "Anfrage von fremder Website abgelehnt."}, status_code=403
        )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    # Pydantic-Eingabewerte können Passwörter enthalten: niemals zurückgeben/loggen.
    return JSONResponse(
        {
            "detail": "Ungültige Eingabe.",
            "fields": [".".join(map(str, e["loc"][1:])) for e in exc.errors()],
        },
        status_code=422,
    )


@app.exception_handler(IntegrityError)
async def conflict(_request: Request, _exc: IntegrityError):
    return JSONResponse(
        {"detail": "Eintrag existiert bereits oder wurde zwischenzeitlich geändert."},
        status_code=409,
    )


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    logger.error("event=api_error type=%s", type(exc).__name__)
    return JSONResponse(
        {"detail": "Interner Fehler. Systemstatus prüfen."}, status_code=500
    )


def require(db, model, item_id: str):
    item = db.get(model, item_id)
    if not item:
        raise HTTPException(404, "Eintrag nicht gefunden.")
    return item


def user_data(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "active": user.active,
    }


@app.get("/api/health")
def health(db: DB):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(data: schemas.Login, response: Response, request: Request, db: DB):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "JSON erforderlich.")
    email = data.email.lower().strip()
    subject = digest(email)
    if db.bind.dialect.name == "postgresql":
        # Gleiche Konten auch bei parallelen Anfragen vor dem Zählen serialisieren.
        lock_key = int.from_bytes(bytes.fromhex(subject[:16]), "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    cutoff = utcnow() - timedelta(minutes=15)
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
    count = (
        db.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(LoginAttempt.subject_hash == subject)
        )
        or 0
    )
    if count >= 5:
        db.commit()
        raise HTTPException(
            429, "Zu viele Anmeldeversuche. In 15 Minuten erneut versuchen."
        )
    # Versuch vor Hashprüfung persistieren; mehrere API-Prozesse teilen das Limit.
    db.add(LoginAttempt(subject_hash=subject))
    db.commit()
    user = db.scalar(select(User).where(User.email == email))
    valid = password_hasher.verify(
        data.password, user.password_hash if user else DUMMY_HASH
    )
    if not valid or not user or not user.active:
        raise HTTPException(401, "E-Mail oder Passwort ist ungültig.")
    old = request.cookies.get(session_cookie())
    if old:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == digest(old)))
    token, csrf = new_session(db, user)
    user.last_login = utcnow()
    db.execute(delete(LoginAttempt).where(LoginAttempt.subject_hash == subject))
    db.commit()
    cookie_path = settings().app_base_path + "/"
    for name, value, httponly in [
        (session_cookie(), token, True),
        (csrf_cookie(), csrf, False),
    ]:
        response.set_cookie(
            name,
            value,
            httponly=httponly,
            secure=settings().cookie_secure,
            samesite="strict",
            path=cookie_path,
            max_age=settings().session_hours * 3600,
        )
    return user_data(user)


@app.get("/api/auth/me")
def me(user: CurrentUser):
    return user_data(user)


@app.post("/api/auth/logout")
def logout(response: Response, request: Request, db: DB, _user: CurrentUser):
    db.execute(
        delete(AuthSession).where(
            AuthSession.token_hash == digest(request.cookies.get(session_cookie(), ""))
        )
    )
    db.commit()
    for name in (session_cookie(), csrf_cookie()):
        response.delete_cookie(
            name,
            path=settings().app_base_path + "/",
            secure=settings().cookie_secure,
            samesite="strict",
        )
    return {"ok": True}


@app.get("/api/users")
def users(db: DB, _admin: Admin):
    return [user_data(user) for user in db.scalars(select(User).order_by(User.email))]


@app.post("/api/users", status_code=201)
def create_user(data: schemas.UserCreate, db: DB, _admin: Admin):
    if "@" not in data.email:
        raise HTTPException(422, "Gültige E-Mail erforderlich.")
    user = User(
        email=data.email.lower().strip(),
        password_hash=password_hasher.hash(data.password),
        role=data.role,
    )
    db.add(user)
    db.commit()
    return user_data(user)


@app.put("/api/users/{user_id}/active")
def activate_user(user_id: str, active: bool, db: DB, admin: Admin):
    user = require(db, User, user_id)
    if user.id == admin.id and not active:
        raise HTTPException(
            409, "Eigenes Administratorkonto kann nicht deaktiviert werden."
        )
    user.active = active
    if not active:
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return user_data(user)


@app.put("/api/users/{user_id}/password")
def user_password(
    user_id: str, data: schemas.PasswordSet, request: Request, db: DB, _admin: Admin
):
    user = require(db, User, user_id)
    set_password(db, user, data.password, request.cookies.get(session_cookie(), ""))
    db.commit()
    return user_data(user)


@app.put("/api/users/{user_id}/role")
def user_role(user_id: str, data: schemas.RoleSet, db: DB, admin: Admin):
    user = require(db, User, user_id)
    set_role(db, user, data.role, admin)
    db.commit()
    return user_data(user)


@app.delete("/api/users/{user_id}", status_code=204)
def remove_user(user_id: str, db: DB, admin: Admin):
    user = require(db, User, user_id)
    delete_user(db, user, admin)
    db.commit()


@app.get("/api/sites")
def sites(db: DB, _user: CurrentUser):
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "timezone": s.timezone,
        }
        for s in db.scalars(select(Site).order_by(Site.name))
    ]


@app.post("/api/sites", status_code=201)
def create_site(data: schemas.SiteInput, db: DB, _admin: Admin):
    site = Site(**data.model_dump())
    db.add(site)
    db.commit()
    return {"id": site.id, "name": site.name, "timezone": site.timezone}


@app.get("/api/containers")
def containers(db: DB, _user: CurrentUser):
    return [
        container_data(db, c)
        for c in db.scalars(select(Container).order_by(Container.created_at))
    ]


@app.get("/api/containers/{container_id}")
@app.get("/api/containers/{container_id}/current")
def container_current(container_id: str, db: DB, _user: CurrentUser):
    return container_data(db, require(db, Container, container_id))


@app.post("/api/containers", status_code=201)
def create_container(data: schemas.ContainerInput, db: DB, _admin: Admin):
    site = require(db, Site, data.site_id)
    container = Container(**data.model_dump())
    db.add(container)
    db.flush()
    db.add(
        Camera(
            container_id=container.id,
            name=f"Kamera · {container.name}",
            timezone=site.timezone,
        )
    )
    db.commit()
    return container_data(db, container)


@app.put("/api/containers/{container_id}")
def update_container(
    container_id: str, data: schemas.ContainerInput, db: DB, _admin: Admin
):
    require(db, Site, data.site_id)
    container = db.get(
        Container, container_id, with_for_update=True, populate_existing=True
    )
    if container is None:
        raise HTTPException(404, "Behälter nicht gefunden.")
    changed = any(
        getattr(container, key) != getattr(data, key)
        for key in ("display_mode", "smoothing_minutes", "event_threshold")
    )
    for key, value in data.model_dump().items():
        setattr(container, key, value)
    if changed:
        request_rebuild(db, container.id)
    evaluate(db, container)
    db.commit()
    return container_data(db, container)


@app.put("/api/containers/{container_id}/camera")
def update_camera(container_id: str, data: schemas.CameraInput, db: DB, _admin: Admin):
    require(db, Container, container_id)
    camera = db.scalar(
        select(Camera)
        .where(Camera.container_id == container_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if data.ftp_connection_id:
        require(db, FTPConnection, data.ftp_connection_id)
    search_fields = (
        "ftp_connection_id",
        "ftp_root",
        "scan_mode",
        "date_pattern",
        "timezone",
    )
    if any(getattr(camera, key) != getattr(data, key) for key in search_fields):
        camera.detected_date_pattern = camera.last_date_path = None
        camera.last_full_scan_at = camera.last_full_scan_attempt_at = None
    if (
        camera.image_rotation != data.image_rotation
        or camera.image_flip != data.image_flip
    ):
        camera.analysis_enabled = False
        camera.learning_dirty = True
        for model in db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera.id, ModelVersion.active.is_(True)
            )
        ):
            model.active = False
    for key, value in data.model_dump(exclude={"description"}).items():
        setattr(camera, key, value)
    db.commit()
    return container_data(db, require(db, Container, container_id))


def ftp_data(connection: FTPConnection) -> dict:
    return {
        **{
            key: getattr(connection, key)
            for key in (
                "id",
                "name",
                "host",
                "port",
                "username",
                "protocol",
                "root_path",
            )
        },
        "has_password": bool(connection.encrypted_password),
    }


@app.get("/api/ftp-connections")
def ftp_connections(db: DB, _admin: Admin):
    return [
        ftp_data(c)
        for c in db.scalars(select(FTPConnection).order_by(FTPConnection.name))
    ]


@app.post("/api/ftp-connections/test")
def test_ftp_connection(data: schemas.FTPTestInput, db: DB, _admin: Admin):
    from .config import settings
    from .ftp_probe import probe_connection, test_path

    stored = require(db, FTPConnection, data.connection_id) if data.connection_id else None
    if data.password is None and stored is None:
        raise HTTPException(422, "FTP-Passwort erforderlich.")
    config = data.model_dump(include={"host", "port", "username", "protocol"})
    config["encrypted_password"] = (
        encrypt(data.password) if data.password is not None else stored.encrypted_password
    )
    return probe_connection(
        config, test_path(data.root_path, data.ftp_root), settings().max_scan_files
    )


@app.post("/api/ftp-connections", status_code=201)
def create_ftp(data: schemas.FTPInput, db: DB, _admin: Admin):
    if data.password is None:
        raise HTTPException(422, "FTP-Passwort erforderlich.")
    connection = FTPConnection(
        **data.model_dump(exclude={"password"}),
        encrypted_password=encrypt(data.password),
    )
    db.add(connection)
    db.commit()
    return ftp_data(connection)


@app.put("/api/ftp-connections/{connection_id}")
def update_ftp(connection_id: str, data: schemas.FTPInput, db: DB, _admin: Admin):
    connection = require(db, FTPConnection, connection_id)
    for key, value in data.model_dump(exclude={"password"}).items():
        setattr(connection, key, value)
    if data.password is not None:
        connection.encrypted_password = encrypt(data.password)
    for camera in db.scalars(
        select(Camera).where(Camera.ftp_connection_id == connection_id)
    ):
        camera.detected_date_pattern = camera.last_date_path = None
        camera.last_full_scan_at = camera.last_full_scan_attempt_at = None
    db.commit()
    return ftp_data(connection)


@app.get("/api/images/{image_id}/content")
def image_content(image_id: str, db: DB, _user: CurrentUser):
    image = require(db, ImageBuffer, image_id)
    path = image_path(image)
    if not path.is_file():
        raise HTTPException(
            410, "Bild ist nicht mehr im Puffer. Ansicht aktualisieren."
        )
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@app.post("/api/containers/{container_id}/measurements", status_code=201)
def manual_measurement(
    container_id: str, data: schemas.ManualInput, db: DB, admin: Admin
):
    container = db.get(
        Container, container_id, with_for_update=True, populate_existing=True
    )
    if container is None:
        raise HTTPException(404, "Behälter nicht gefunden.")
    image = require(db, ImageBuffer, data.image_id)
    camera = require(db, Camera, image.camera_id)
    if camera.container_id != container.id:
        raise HTTPException(422, "Bild gehört nicht zu diesem Behälter.")
    if db.scalar(
        select(Measurement.id).where(Measurement.source_import_id == image.import_id)
    ):
        raise HTTPException(409, "Für dieses Bild ist bereits ein Messwert vorhanden.")
    image.image_mode = data.image_mode
    measurement = create_manual_measurement(
        db, container, camera, image, data.fill_level, data.image_mode, admin.id
    )
    db.execute(
        update(AnalysisResult)
        .where(
            AnalysisResult.source_import_id == image.import_id,
            AnalysisResult.status == "pending",
        )
        .values(status="superseded", measurement_id=measurement.id)
    )
    db.commit()
    return measurement_data(measurement)


@app.post("/api/containers/{container_id}/level-correction")
def level_correction(
    container_id: str, data: schemas.LevelCorrection, db: DB, user: CurrentUser
):
    result = correct_level(db, container_id, data.image_id, data.fill_level, user)
    db.commit()
    return {
        "measurement": measurement_data(result["measurement"]),
        "learned": result["learned"],
    }


@app.get("/api/containers/{container_id}/measurements")
def measurements(
    container_id: str,
    db: DB,
    _user: CurrentUser,
    period: Literal["24h", "7d", "30d", "3m", "1y", "all"] = "7d",
    limit: int = Query(default=800, ge=10, le=2000),
):
    container = require(db, Container, container_id)
    filters = [Measurement.container_id == container_id]
    days = {"24h": 1, "7d": 7, "30d": 30, "3m": 90, "1y": 365}
    if period in days:
        filters.append(
            Measurement.measured_at >= utcnow() - timedelta(days=days[period])
        )
    count = (
        db.scalar(select(func.count()).select_from(Measurement).where(*filters)) or 0
    )
    # Gleichmäßig über den gesamten Zeitraum; keine still abgeschnittene Historie.
    stride = max(1, math.ceil((count - 1) / (limit - 1)))
    ranked = (
        select(
            Measurement.id,
            func.row_number()
            .over(order_by=(Measurement.measured_at, Measurement.created_at))
            .label("n"),
        )
        .where(*filters)
        .subquery()
    )
    query = (
        select(Measurement)
        .join(ranked, ranked.c.id == Measurement.id)
        .where(((ranked.c.n - 1) % stride == 0) | (ranked.c.n == count))
        .order_by(Measurement.measured_at, Measurement.created_at)
    )
    return {
        "items": [measurement_data(m) for m in db.scalars(query)],
        "rate_status": "ready"
        if container.rate_revision == container.calculated_revision
        else "pending",
        "rate_revision": container.rate_revision,
        "total_count": count,
        "sampled": stride > 1,
    }


@app.post("/api/system/worker/run", status_code=202)
def run_worker(db: DB, _admin: Admin):
    state = request_run(db)
    if state == "offline":
        db.rollback()
        raise HTTPException(
            503,
            "Worker nicht erreichbar. Den Worker-Dienst starten; anschließend erneut versuchen.",
        )
    db.commit()
    return {"status": state}


@app.get("/api/system/status")
def system_status(db: DB, _admin: Admin):
    worker = db.get(WorkerStatus, "importer")
    fresh = worker_fresh(worker)
    control = db.get(WorkerControl, "importer")
    cycle_running = bool(control and control.running)
    pending = bool(
        control and control.requested > control.completed and not cycle_running
    )
    cameras = []
    for c in db.scalars(select(Camera).order_by(Camera.name)):
        count, size = db.execute(
            select(
                func.count(), func.coalesce(func.sum(ImageBuffer.size_bytes), 0)
            ).where(ImageBuffer.camera_id == c.id)
        ).one()
        cameras.append(
            {
                "id": c.id,
                "name": c.name,
                "last_scan_at": aware(c.last_scan_at).isoformat()
                if c.last_scan_at
                else None,
                "last_import_at": aware(c.last_import_at).isoformat()
                if c.last_import_at
                else None,
                "error": c.last_error,
                "error_code": c.last_error_code,
                "scan_mode": c.scan_mode,
                "detected_date_pattern": c.detected_date_pattern,
                "date_pattern": c.date_pattern,
                "scan_kind": c.last_scan_kind,
                "scanned_entries": c.last_scan_entries,
                "last_full_scan_at": aware(c.last_full_scan_at).isoformat()
                if c.last_full_scan_at
                else None,
                "buffer_count": count,
                "buffer_bytes": size,
                "configured": c.ftp_connection_id is not None,
                "active": c.active,
            }
        )
    return {
        "database": "ok",
        "worker": "ok" if fresh else "missing",
        "worker_phase": (
            "cycle" if cycle_running and worker.phase == "idle" else worker.phase
        )
        if fresh
        else "offline",
        "worker_busy": bool(fresh and (cycle_running or worker.phase != "idle")),
        "manual_run_pending": pending,
        "last_run_started_at": aware(control.started_at).isoformat()
        if control and control.started_at
        else None,
        "last_run_finished_at": aware(control.finished_at).isoformat()
        if control and control.finished_at
        else None,
        "active_camera_id": worker.active_camera_id if fresh else None,
        "scanned_entries": worker.scanned_entries if worker else 0,
        "imported_count": worker.imported_count if worker else 0,
        "operation_started_at": aware(worker.operation_started_at).isoformat()
        if worker and worker.operation_started_at
        else None,
        "worker_error": worker.last_error if worker else None,
        "heartbeat_at": aware(worker.heartbeat_at).isoformat() if worker else None,
        "poll_seconds": settings().ftp_poll_seconds,
        "cameras": cameras,
        "analysis": "local_rf_v1",
    }
