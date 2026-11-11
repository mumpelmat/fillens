"""Administrative Kontoverwaltung: Passwort setzen und Konten löschen."""

import logging

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from .db import Base
from .models import AuthSession, PasswordReset, User
from .security import digest, password_hasher

logger = logging.getLogger("fillens.api")

# Kontobezogene Datensätze ohne fachlichen Wert entfallen mit dem Konto.
OWNED_TABLES = {"auth_sessions", "notification_reads", "password_resets"}


def set_password(db, user: User, password: str, current_token: str) -> None:
    user.password_hash = password_hasher.hash(password)
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id))
    # Alle Sitzungen des Kontos beenden; nur die eigene aktuelle Sitzung bleibt.
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.token_hash != digest(current_token),
        )
    )
    logger.info("event=user_password_set user=%s", user.id)


def _other_active_admins(db, user: User) -> int:
    return db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == "admin", User.active.is_(True), User.id != user.id)
    ) or 0


def set_role(db, user: User, role: str, admin: User) -> None:
    if user.role == role:
        return
    if user.id == admin.id:
        raise HTTPException(409, "Eigene Rolle kann nicht geändert werden.")
    if user.role == "admin" and user.active and not _other_active_admins(db, user):
        raise HTTPException(409, "Der letzte aktive Administrator muss Administrator bleiben.")
    # Rolle wird je Anfrage aus der Datenbank gelesen und gilt damit sofort.
    user.role = role
    logger.info("event=user_role_set user=%s role=%s by=%s", user.id, role, admin.id)


def delete_user(db, user: User, admin: User) -> None:
    if user.id == admin.id:
        raise HTTPException(409, "Eigenes Konto kann nicht gelöscht werden.")
    if user.role == "admin" and user.active and not _other_active_admins(db, user):
        raise HTTPException(409, "Der letzte aktive Administrator kann nicht gelöscht werden.")
    # Fachdaten (Messwerte, Lernbilder, Modelle) bleiben erhalten; nur der Bezug entfällt.
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name != "users" or table.name == "users":
                continue
            column = fk.parent
            if table.name in OWNED_TABLES:
                db.execute(delete(table).where(column == user.id))
            elif column.nullable:
                db.execute(update(table).where(column == user.id).values({column.name: None}))
            else:
                # Pflichtbezug (z. B. Modell-/Sicherungsersteller) geht auf den löschenden Admin über.
                db.execute(update(table).where(column == user.id).values({column.name: admin.id}))
    db.execute(delete(User).where(User.id == user.id))
    logger.info("event=user_deleted user=%s by=%s", user.id, admin.id)
