"""Schwellenalarme und persistenter, zeitlich begrenzter SMTP-Versand."""

import multiprocessing
import smtplib
import ssl
import time
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, aware, utcnow
from .models import (
    AlertState,
    Container,
    EmailOutbox,
    Measurement,
    Notification,
    SMTPConfig,
)
from .security import decrypt


def smtp_ready(config: SMTPConfig | None) -> bool:
    return bool(
        config
        and config.host
        and config.sender
        and (not config.username or config.encrypted_password)
    )


def evaluate(db: Session, container: Container) -> None:
    # Aufruf unter Behältersperre. Nur die aktuellste übernommene Messung zählt.
    current = db.scalar(
        select(Measurement)
        .where(Measurement.container_id == container.id)
        .order_by(
            Measurement.measured_at.desc(),
            Measurement.created_at.desc(),
            Measurement.id.desc(),
        )
        .limit(1)
    )
    state = db.get(AlertState, container.id)
    if not state:
        state = AlertState(
            container_id=container.id,
            mode=container.display_mode,
            severity=0,
            episode=0,
            emitted_level=0,
        )
        db.add(state)
    if state.mode != container.display_mode:
        state.mode, state.severity, state.emitted_level = container.display_mode, 0, 0
        state.episode += 1
    if not current or not container.active:
        if state.severity:
            state.episode += 1
        state.severity, state.emitted_level = 0, 0
        return
    fill = container.display_mode == "fill"
    level = current.effective_fill_level
    notice = container.fill_notice if fill else container.consumption_notice
    warning = container.fill_warning if fill else container.consumption_warning
    score, n, w = (level, notice, warning) if fill else (-level, -notice, -warning)
    severity = 2 if score >= w else 1 if score >= n else 0
    if state.severity == 2 and score >= w - 2:
        severity = 2
    elif state.severity >= 1 and score >= n - 2:
        severity = max(severity, 1)
    if not severity:
        if state.severity:
            state.episode += 1
        state.severity, state.emitted_level = 0, 0
        return
    state.severity = severity
    if severity <= state.emitted_level:
        return
    # Ein direkter Sprung bis zur Warnung erreicht beide Stufen. Beide werden
    # im Portal erfasst; pro Empfänger wird dabei nur die höchste gemailt.
    for reached in range(state.emitted_level + 1, severity + 1):
        notification = Notification(
            container_id=container.id,
            episode=state.episode,
            severity=reached,
            mode=container.display_mode,
            container_name=container.name,
            measured_at=current.measured_at,
            fill_level=level,
            threshold=warning if reached == 2 else notice,
        )
        db.add(notification)
        db.flush()
        if (
            reached == severity
            and container.email_enabled
            and smtp_ready(db.get(SMTPConfig, "main"))
        ):
            for address in container.email_recipients:
                db.add(EmailOutbox(notification_id=notification.id, recipient=address))
    state.emitted_level = severity


def evaluate_current_levels() -> None:
    with SessionLocal() as db:
        ids = list(db.scalars(select(Container.id)))
    for container_id in ids:
        with SessionLocal() as db:
            container = db.scalar(
                select(Container).where(Container.id == container_id).with_for_update()
            )
            evaluate(db, container)
            db.commit()


def send_one(config: SMTPConfig, notification: Notification, recipient: str) -> None:
    message = EmailMessage()
    message["From"], message["To"] = config.sender, recipient
    label = "Warnung" if notification.severity == 2 else "Hinweis"
    # Behälternamen sind frei editierbar; keine Header-Injektion.
    name = " ".join(notification.container_name.splitlines())
    message["Subject"] = f"Fillens · {label} · {name}"
    mode = (
        "Füllen – Überlaufen droht"
        if notification.mode == "fill"
        else "Verbrauch – Leerstand droht"
    )
    message.set_content(
        f"{label}: {notification.container_name}\nModus: {mode}\nMesszeit: {aware(notification.measured_at).isoformat()}\nFüllstand: {notification.fill_level:g} %\nSchwelle: {notification.threshold:g} %\n"
    )
    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if config.encryption == "ssl" else smtplib.SMTP
    kwargs = {"timeout": 5}
    if config.encryption == "ssl":
        kwargs["context"] = context
    with smtp_class(config.host, config.port, **kwargs) as client:
        if config.encryption == "starttls":
            client.starttls(context=context)
        if config.username:
            client.login(config.username, decrypt(config.encrypted_password))
        client.send_message(message)


def _delivery_job(connection, config_data, notification_data, recipient):
    try:
        send_one(
            SimpleNamespace(**config_data),
            SimpleNamespace(**notification_data),
            recipient,
        )
        connection.send(None)
    except Exception as exc:
        connection.send(
            "SMTP-Anmeldung abgelehnt."
            if isinstance(exc, smtplib.SMTPAuthenticationError)
            else "E-Mail konnte nicht gesendet werden. SMTP-Verbindung und Einstellungen prüfen."
        )
    finally:
        connection.close()


def send_bounded(config, notification, recipient, timeout):
    # Harte Frist für den gesamten Dialog, einschließlich TLS/Serverantworten.
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    config_data = {
        k: getattr(config, k)
        for k in (
            "host",
            "port",
            "encryption",
            "username",
            "encrypted_password",
            "sender",
        )
    }
    notification_data = {
        k: getattr(notification, k)
        for k in (
            "severity",
            "container_name",
            "mode",
            "measured_at",
            "fill_level",
            "threshold",
        )
    }
    process = ctx.Process(
        target=_delivery_job, args=(sender, config_data, notification_data, recipient)
    )
    try:
        process.start()
        sender.close()
        if receiver.poll(max(0, timeout)):
            try:
                error = receiver.recv()
            except EOFError:
                error = "SMTP-Versandprozess abgebrochen."
        else:
            error = "Zeitlimit für den E-Mail-Versand überschritten."
        return error
    finally:
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        receiver.close()
        sender.close()


def process_outbox(budget_seconds: float = 15) -> int:
    deadline, processed = time.monotonic() + budget_seconds, 0
    # Ein Einzel-Worker; jeder Versuch wird vor SMTP dauerhaft gezählt. Ein
    # Abbruch wird nach Ablauf der Reservierung erneut versucht (at least once).
    while processed < 3 and time.monotonic() < deadline:
        with SessionLocal() as db:
            config = db.get(SMTPConfig, "main")
            if not smtp_ready(config):
                break
            item = db.scalar(
                select(EmailOutbox)
                .where(
                    EmailOutbox.status.in_(["pending", "retry", "sending"]),
                    EmailOutbox.next_attempt_at <= utcnow(),
                )
                .order_by(EmailOutbox.next_attempt_at)
                .limit(1)
                .with_for_update()
            )
            if not item:
                break
            notification = db.get(Notification, item.notification_id)
            container = db.get(Container, notification.container_id)
            if (
                not container.email_enabled
                or item.recipient not in container.email_recipients
            ):
                item.status, item.last_error = "cancelled", None
                db.commit()
                processed += 1
                continue
            if item.attempts >= 5:
                item.status, item.last_error = (
                    "failed",
                    "Versand nach fünf Versuchen beendet.",
                )
                db.commit()
                continue
            item.status, item.attempts = "sending", item.attempts + 1
            item.next_attempt_at = utcnow() + timedelta(minutes=5)
            db.commit()
            error = send_bounded(
                config, notification, item.recipient, deadline - time.monotonic()
            )
            if error is None:
                item.status, item.sent_at, item.last_error = "sent", utcnow(), None
            else:
                item.status = "failed" if item.attempts >= 5 else "retry"
                item.last_error = error
                item.next_attempt_at = utcnow() + timedelta(
                    minutes=min(60, 2**item.attempts)
                )
            db.commit()
            processed += 1
    return processed
