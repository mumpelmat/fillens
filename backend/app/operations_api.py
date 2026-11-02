from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from . import schemas
from .alerts import evaluate, smtp_ready
from .db import aware
from .models import (
    AlertState,
    Container,
    ContainerEvent,
    EmailOutbox,
    Notification,
    NotificationRead,
    SMTPConfig,
)
from .rates import request_rebuild
from .security import DB, Admin, CurrentUser, encrypt

router = APIRouter(prefix="/api")


def require(db, model, key, lock=False):
    row = db.get(model, key, with_for_update=lock)
    if not row:
        raise HTTPException(404, "Eintrag nicht gefunden.")
    return row


def event_data(row):
    return {
        "id": row.id,
        "occurred_at": aware(row.occurred_at).isoformat(),
        "kind": row.kind,
        "source": row.source,
        "note": row.note,
    }


@router.get("/containers/{container_id}/events")
def events(container_id: str, db: DB, _user: CurrentUser):
    require(db, Container, container_id)
    return [
        event_data(e)
        for e in db.scalars(
            select(ContainerEvent)
            .where(
                ContainerEvent.container_id == container_id,
                ContainerEvent.source != "suppressed",
            )
            .order_by(ContainerEvent.occurred_at)
        )
    ]


@router.post("/containers/{container_id}/events", status_code=201)
def create_event(container_id: str, data: schemas.EventInput, db: DB, admin: Admin):
    require(db, Container, container_id, True)
    row = ContainerEvent(
        container_id=container_id,
        source="manual",
        created_by=admin.id,
        **data.model_dump(),
    )
    db.add(row)
    request_rebuild(db, container_id)
    db.commit()
    return event_data(row)


@router.put("/containers/{container_id}/events/{event_id}")
def update_event(
    container_id: str, event_id: str, data: schemas.EventInput, db: DB, admin: Admin
):
    require(db, Container, container_id, True)
    row = require(db, ContainerEvent, event_id)
    if row.container_id != container_id:
        raise HTTPException(404, "Ereignis nicht gefunden.")
    # Eine bewusst korrigierte Automatik-Markierung wird manuell und bleibt bei
    # späteren Neuberechnungen bestehen.
    row.source = "manual"
    row.created_by = admin.id
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    request_rebuild(db, container_id)
    db.commit()
    return event_data(row)


@router.delete("/containers/{container_id}/events/{event_id}")
def delete_event(container_id: str, event_id: str, db: DB, _admin: Admin):
    require(db, Container, container_id, True)
    row = require(db, ContainerEvent, event_id)
    if row.container_id != container_id:
        raise HTTPException(404, "Ereignis nicht gefunden.")
    if row.measurement_id:
        row.source = "suppressed"
    else:
        db.delete(row)
    request_rebuild(db, container_id)
    db.commit()
    return {"ok": True}


@router.get("/containers/{container_id}/alert-rules")
def rules(container_id: str, db: DB, _admin: Admin):
    row = require(db, Container, container_id)
    return {key: getattr(row, key) for key in schemas.AlertRulesInput.model_fields}


@router.put("/containers/{container_id}/alert-rules")
def update_rules(
    container_id: str, data: schemas.AlertRulesInput, db: DB, _admin: Admin
):
    row = require(db, Container, container_id, True)
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    db.flush()
    evaluate(db, row)
    db.commit()
    return {"ok": True}


def smtp_data(row):
    return {
        "host": row.host if row else "",
        "port": row.port if row else 587,
        "encryption": row.encryption if row else "starttls",
        "username": row.username if row else "",
        "sender": row.sender if row else "",
        "has_password": bool(row and row.encrypted_password),
        "ready": smtp_ready(row),
    }


@router.get("/system/smtp")
def smtp_settings(db: DB, _admin: Admin):
    return smtp_data(db.get(SMTPConfig, "main"))


@router.put("/system/smtp")
def update_smtp(data: schemas.SMTPInput, db: DB, _admin: Admin):
    row = db.get(SMTPConfig, "main")
    if not row:
        row = SMTPConfig(id="main")
        db.add(row)
    for key in ("host", "port", "encryption", "username", "sender"):
        setattr(row, key, getattr(data, key))
    if data.clear_password:
        row.encrypted_password = None
    elif data.password:
        row.encrypted_password = encrypt(data.password)
    db.commit()
    return smtp_data(row)


@router.get("/notifications")
def notifications(
    db: DB,
    user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    rows = list(
        db.scalars(
            select(Notification)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    result = []
    for n in rows:
        state = db.get(AlertState, n.container_id)
        active = bool(
            state
            and state.episode == n.episode
            and state.mode == n.mode
            and state.severity == n.severity
        )
        outbox = list(
            db.scalars(select(EmailOutbox).where(EmailOutbox.notification_id == n.id))
        )
        result.append(
            {
                "id": n.id,
                "container_id": n.container_id,
                "container_name": n.container_name,
                "severity": n.severity,
                "mode": n.mode,
                "measured_at": aware(n.measured_at).isoformat(),
                "created_at": aware(n.created_at).isoformat(),
                "fill_level": n.fill_level,
                "threshold": n.threshold,
                "active": active,
                "read": db.get(NotificationRead, (n.id, user.id)) is not None,
                "deliveries": [
                    {
                        "id": o.id,
                        "recipient": o.recipient,
                        "status": o.status,
                        "attempts": o.attempts,
                        "last_error": o.last_error,
                    }
                    for o in outbox
                ]
                if user.role == "admin"
                else [],
            }
        )
    return result


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, db: DB, user: CurrentUser):
    require(db, Notification, notification_id)
    # Idempotent upsert unter Benutzersperre, auch bei parallelen Browser-Tabs.
    from .models import User

    require(db, User, user.id, True)
    if db.get(NotificationRead, (notification_id, user.id)) is None:
        db.add(NotificationRead(notification_id=notification_id, user_id=user.id))
    db.commit()
    return {"ok": True}
