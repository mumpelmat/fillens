"""Kontowiederherstellung mit kurzlebigen Einmalcodes und bestehendem SMTP."""

import logging
import multiprocessing
import secrets
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import Field
from sqlalchemy import delete, func, select, text, update

from .alerts import deliver_message, smtp_ready
from .db import SessionLocal, aware, utcnow
from .models import AuthSession, LoginAttempt, PasswordReset, SMTPConfig, User
from .schemas import Input
from .security import DB, digest, password_hasher

router = APIRouter(prefix="/api/auth/password-reset")
logger = logging.getLogger("fillens.password_reset")
ACCEPTED = "Falls ein aktives Konto zu dieser E-Mail existiert, wird ein Rücksetzcode gesendet. Bitte auch den Spamordner prüfen."


class ResetRequest(Input):
    email: str = Field(min_length=3, max_length=254)


class ResetConfirm(Input):
    code: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=256)


def _mail_job(sender, config_data, recipient, code):
    try:
        config = SimpleNamespace(**config_data)
        message = EmailMessage()
        message["From"], message["To"] = config.sender, recipient
        message["Subject"] = "Fillens · Passwort zurücksetzen"
        message.set_content(
            "Für Ihr Fillens-Konto wurde eine Passwortzurücksetzung angefordert.\n\n"
            f"Ihr einmaliger Rücksetzcode:\n{code}\n\n"
            "Öffnen Sie Ihre bekannte Fillens-Adresse und wählen Sie auf der Anmeldung "
            "„Passwort vergessen“ und „Ich habe bereits einen Code“. "
            "Fügen Sie den Code ein und vergeben Sie ein neues Passwort. "
            "Der Code ist 30 Minuten gültig.\n\n"
            "Falls Sie diese Anfrage nicht gestellt haben, ignorieren Sie diese E-Mail. "
            "Ihr Passwort wurde noch nicht geändert.\n"
        )
        deliver_message(config, message)
        sender.send(True)
    except Exception:
        # Weder Serverantworten noch Codes, Empfänger oder Zugangsdaten protokollieren.
        sender.send(False)
    finally:
        sender.close()


def send_reset_mail(config_data, recipient, code) -> bool:
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_mail_job, args=(sender, config_data, recipient, code))
    try:
        process.start()
        sender.close()
        return bool(receiver.poll(15) and receiver.recv())
    except (EOFError, OSError):
        return False
    finally:
        sender.close()
        receiver.close()
        if process.pid is not None:
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)
            process.close()


def deliver_reset(token_hash, config_data, recipient, code):
    if not send_reset_mail(config_data, recipient, code):
        with SessionLocal() as db:
            db.execute(delete(PasswordReset).where(PasswordReset.token_hash == token_hash))
            db.commit()
        logger.warning("event=password_reset_mail_failed")


def require_json(request: Request):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "JSON erforderlich.")


@router.get("/status")
def reset_status(db: DB):
    return {"available": smtp_ready(db.get(SMTPConfig, "main"))}


@router.post("/request", status_code=202)
def request_reset(data: ResetRequest, request: Request, tasks: BackgroundTasks, db: DB):
    require_json(request)
    config = db.get(SMTPConfig, "main")
    if not smtp_ready(config):
        raise HTTPException(503, "Passwortzurücksetzung per E-Mail ist noch nicht eingerichtet. Bitte den Administrator kontaktieren.")
    email = data.email.strip().lower()
    if "@" not in email or any(ord(char) < 32 for char in email):
        raise HTTPException(422, "Gültige E-Mail erforderlich.")
    subject = digest("reset-email:" + email)
    global_subject = digest("reset-global")
    if db.bind.dialect.name == "postgresql":
        lock_key = int.from_bytes(bytes.fromhex(global_subject[:16]), "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    cutoff = utcnow() - timedelta(minutes=15)
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    db.execute(delete(PasswordReset).where(PasswordReset.expires_at <= utcnow()))
    for key, maximum in ((subject, 3), (global_subject, 30)):
        count = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.subject_hash == key))
        if count >= maximum:
            db.commit()
            return {"message": ACCEPTED}
    db.add_all([LoginAttempt(subject_hash=subject), LoginAttempt(subject_hash=global_subject)])
    user = db.scalar(select(User).where(User.email == email, User.active.is_(True)))
    if user:
        code = secrets.token_urlsafe(24)
        token_hash = digest(code)
        db.add(PasswordReset(token_hash=token_hash, user_id=user.id,
            password_revision=digest(user.password_hash), expires_at=utcnow() + timedelta(minutes=30)))
        config_data = {key: getattr(config, key) for key in (
            "host", "port", "encryption", "username", "encrypted_password", "sender",
        )}
        tasks.add_task(deliver_reset, token_hash, config_data, user.email, code)
    db.commit()
    return {"message": ACCEPTED}


@router.post("/confirm")
def confirm_reset(data: ResetConfirm, request: Request, db: DB):
    require_json(request)
    token_hash = digest(data.code.strip())
    token = db.get(PasswordReset, token_hash)
    invalid = HTTPException(400, "Rücksetzcode ist ungültig oder abgelaufen. Bitte einen neuen Code anfordern.")
    if not token:
        raise invalid
    # Alle Codes eines Kontos verbrauchen; parallele Bestätigungen serialisieren.
    user = db.get(User, token.user_id, with_for_update=True, populate_existing=True)
    token = db.get(PasswordReset, token_hash, populate_existing=True)
    if (not user or not user.active or not token or aware(token.expires_at) <= utcnow()
            or not secrets.compare_digest(token.password_revision, digest(user.password_hash))):
        raise invalid
    changed = db.execute(update(User).where(
        User.id == user.id, User.active.is_(True), User.password_hash == user.password_hash,
    ).values(password_hash=password_hasher.hash(data.password)).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise invalid
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.execute(delete(LoginAttempt).where(LoginAttempt.subject_hash == digest(user.email)))
    db.commit()
    return {"message": "Passwort geändert. Bitte mit dem neuen Passwort anmelden."}
