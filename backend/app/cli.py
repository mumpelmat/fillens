import argparse
import getpass

from sqlalchemy import select

from .db import SessionLocal
from .models import AuthSession, User
from .security import password_hasher


def main() -> None:
    parser = argparse.ArgumentParser(description="Fillens-Benutzerverwaltung")
    parser.add_argument("command", choices=["create-admin", "reset-password"])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    email = args.email.strip().lower()
    if "@" not in email:
        parser.error("Gültige E-Mail erforderlich")
    password = getpass.getpass("Passwort (mindestens 12 Zeichen): ")
    if (
        len(password) < 12
        or len(password) > 256
        or getpass.getpass("Passwort wiederholen: ") != password
    ):
        parser.error("Passwortlänge oder Wiederholung ungültig")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if args.command == "create-admin":
            if user:
                parser.error("Benutzer existiert bereits")
            db.add(
                User(
                    email=email,
                    password_hash=password_hasher.hash(password),
                    role="admin",
                )
            )
        else:
            if not user:
                parser.error("Benutzer nicht gefunden")
            user.password_hash = password_hasher.hash(password)
            for session in db.scalars(
                select(AuthSession).where(AuthSession.user_id == user.id)
            ):
                db.delete(session)
        db.commit()
    print("Benutzer aktualisiert.")


if __name__ == "__main__":
    main()
