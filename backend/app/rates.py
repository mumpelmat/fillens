"""Versionierte Betriebsraten; Veröffentlichung zusammen mit den Zyklusgrenzen."""

from collections import deque
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import SessionLocal, aware
from .models import Container, ContainerEvent, Measurement


def request_rebuild(db: Session, container_id: str) -> None:
    # SQL-Inkrement und dieselbe Behältersperre wie beim Publizieren: keine verlorene
    # Anforderung bei paralleler Konfiguration oder verspäteten Messwerten.
    db.execute(
        update(Container)
        .where(Container.id == container_id)
        .values(rate_revision=Container.rate_revision + 1)
    )


def rebuild(db: Session, container_id: str) -> None:
    container = db.scalar(
        select(Container)
        .where(Container.id == container_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not container or container.rate_revision == container.calculated_revision:
        return
    revision = container.rate_revision
    rows = list(
        db.scalars(
            select(Measurement)
            .where(Measurement.container_id == container_id)
            .order_by(Measurement.measured_at, Measurement.created_at, Measurement.id)
        )
    )
    manual = list(
        db.scalars(
            select(ContainerEvent)
            .where(
                ContainerEvent.container_id == container_id,
                ContainerEvent.source == "manual",
            )
            .order_by(ContainerEvent.occurred_at)
        )
    )
    old_automatic = {
        event.measurement_id: event
        for event in db.scalars(
            select(ContainerEvent).where(
                ContainerEvent.container_id == container_id,
                ContainerEvent.source == "automatic",
            )
        )
    }
    retained_automatic = set()
    suppressed = set(
        db.scalars(
            select(ContainerEvent.measurement_id).where(
                ContainerEvent.container_id == container_id,
                ContainerEvent.source.in_(["manual", "suppressed"]),
                ContainerEvent.measurement_id.is_not(None),
            )
        )
    )
    window = timedelta(minutes=container.smoothing_minutes)
    direction = 1 if container.display_mode == "fill" else -1
    intervals = deque()
    weighted_time = weighted_percent = weighted_absolute = 0.0
    previous = None
    cycle = 0
    event_index = 0
    for row in rows:
        now = aware(row.measured_at)
        manual_break = False
        while (
            event_index < len(manual) and aware(manual[event_index].occurred_at) <= now
        ):
            manual_break = True
            event_index += 1
        row.operating_rate_percent = row.operating_rate_absolute = None
        row.cycle_break = previous is None or manual_break
        automatic = False
        same_time = previous is not None and aware(previous.measured_at) == now
        if previous and not same_time:
            seconds = (now - aware(previous.measured_at)).total_seconds()
            delta = row.effective_fill_level - previous.effective_fill_level
            automatic = (
                delta * direction <= -container.event_threshold
                and not manual_break
                and row.id not in suppressed
            )
            changed_capacity = (
                row.capacity_snapshot != previous.capacity_snapshot
                or row.capacity_unit != previous.capacity_unit
            )
            row.cycle_break = (
                row.cycle_break
                or automatic
                or seconds > window.total_seconds()
                or changed_capacity
            )
            if automatic:
                event = old_automatic.get(row.id)
                if event is None:
                    event = ContainerEvent(
                        container_id=container_id,
                        occurred_at=row.measured_at,
                        kind="emptying" if direction == 1 else "refill",
                        source="automatic",
                        measurement_id=row.id,
                    )
                    db.add(event)
                else:
                    event.kind = "emptying" if direction == 1 else "refill"
                    event.occurred_at = row.measured_at
                retained_automatic.add(row.id)
            if not row.cycle_break:
                p_rate = delta / (seconds / 3600)
                a_rate = (row.absolute_level - previous.absolute_level) / (
                    seconds / 3600
                )
                intervals.append((aware(previous.measured_at), now, p_rate, a_rate))
                weighted_time += seconds
                weighted_percent += seconds * p_rate
                weighted_absolute += seconds * a_rate
        if row.cycle_break:
            cycle += 1
            intervals.clear()
            weighted_time = weighted_percent = weighted_absolute = 0.0
        row.rate_cycle, row.rate_revision = cycle, revision
        cutoff = now - window
        while intervals and intervals[0][1] <= cutoff:
            start, end, p_rate, a_rate = intervals.popleft()
            duration = (end - start).total_seconds()
            weighted_time -= duration
            weighted_percent -= duration * p_rate
            weighted_absolute -= duration * a_rate
        if intervals and not same_time:
            start, _, p_rate, a_rate = intervals[0]
            clipped = max(0.0, (cutoff - start).total_seconds())
            total = weighted_time - clipped
            if total > 0:
                row.operating_rate_percent = max(
                    0.0, direction * (weighted_percent - clipped * p_rate) / total
                )
                row.operating_rate_absolute = max(
                    0.0, direction * (weighted_absolute - clipped * a_rate) / total
                )
        # Mehrere Messungen am selben Zeitpunkt: die zuletzt erstellte ist die
        # deterministische Basis des nächsten Intervalls, ohne Zusatzrate.
        previous = row
    for measurement_id, event in old_automatic.items():
        if measurement_id not in retained_automatic:
            db.delete(event)
    container.calculated_revision = revision
    db.flush()


def run_rate_jobs() -> int:
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Container.id).where(
                    Container.rate_revision != Container.calculated_revision
                )
            )
        )
    for container_id in ids:
        with SessionLocal() as db:
            rebuild(db, container_id)
            db.commit()
    return len(ids)
