import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .config import settings
from .db import aware, get_db, utcnow
from .models import AuthSession, User

password_hasher = PasswordHash.recommended()
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(24))
DB = Annotated[Session, Depends(get_db)]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def encrypt(value: str) -> str:
    return Fernet(settings().encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return Fernet(settings().encryption_key.encode()).decrypt(value.encode()).decode()


def session_cookie() -> str:
    # Eigene Namen erlauben mehrere Installationen unter einem Host.
    return "fillens_session_" + digest(settings().app_base_path)[:8]


def csrf_cookie() -> str:
    return "fillens_csrf_" + digest(settings().app_base_path)[:8]


def current_user(request: Request, db: DB) -> User:
    token = request.cookies.get(session_cookie(), "")
    session = db.get(AuthSession, digest(token)) if token else None
    if session is None or aware(session.expires_at) <= utcnow():
        raise HTTPException(401, "Bitte erneut anmelden.")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Benutzer ist nicht aktiv.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), session.csrf_hash):
            raise HTTPException(403, "Ungültiger CSRF-Schutz. Bitte neu anmelden.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administratorrechte erforderlich.")
    return user


Admin = Annotated[User, Depends(admin_user)]


def new_session(db: Session, user: User) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=digest(token),
            csrf_hash=digest(csrf),
            user_id=user.id,
            expires_at=utcnow() + timedelta(hours=settings().session_hours),
        )
    )
    return token, csrf
