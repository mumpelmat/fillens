"""Ein sichtbarer Lernstand, konservative Tag-/Nachtfreigabe und atomare Übernahme."""

import hashlib
import json

from sqlalchemy import select

from .db import SessionLocal
from .models import Camera, ModelVersion
from .vision import camera_config, config_key

MODES = ("day", "night_ir")


def dataset_snapshot(samples):
    return [
        {
            "id": s.id,
            "hash": s.file_hash,
            "target": s.target_fill_level,
            **({"rejection_reason": s.rejection_reason} if s.rejection_reason else {}),
            "series": s.series_name,
            "image_mode": s.image_mode,
        }
        for s in samples
    ]


def fingerprint(config, snapshot):
    normalized = sorted(snapshot, key=lambda s: s["id"])
    return hashlib.sha256(
        (
            config_key(config)
            + json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        ).encode()
    ).hexdigest()


def qualified_modes(model):
    metrics = model.metrics_json or {}
    if model.managed:
        return {
            mode
            for mode, report in metrics.get("mode_quality", {}).items()
            if report.get("ready")
        }
    # Alte, ausdrücklich aktivierte Modelle bleiben nutzbar. Eine vorhandene
    # unabhängige Freigabe darf durch die Automatik nicht verloren gehen.
    if (
        metrics.get("mae_percent_points") is None
        or metrics["mae_percent_points"] > 10
        or metrics.get("validation_samples", 0) < 2
    ):
        return set()
    return {s["image_mode"] for s in model.dataset_json if s["image_mode"] in MODES}


def adopt_ready_models():
    from .ml_service import compatible, eligible_samples, model_path

    with SessionLocal() as db:
        ids = list(db.scalars(select(Camera.id)))
    for camera_id in ids:
        with SessionLocal() as db:
            camera = db.get(
                Camera, camera_id, with_for_update=True, populate_existing=True
            )
            if not camera.learning_enabled or not camera.roi_json:
                continue
            current = fingerprint(
                camera_config(camera), dataset_snapshot(eligible_samples(db, camera, include_unusable=True))
            )
            active = list(
                db.scalars(
                    select(ModelVersion).where(
                        ModelVersion.camera_id == camera_id,
                        ModelVersion.active.is_(True),
                    )
                )
            )
            required = set().union(
                *(qualified_modes(m) for m in active if compatible(camera, m))
            )
            candidates = list(
                db.scalars(
                    select(ModelVersion)
                    .where(
                        ModelVersion.camera_id == camera_id,
                        ModelVersion.managed.is_(True),
                        ModelVersion.status == "ready",
                        ModelVersion.active.is_(False),
                    )
                    .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
                )
            )
            active_current = [
                m
                for m in active
                if m.managed and fingerprint(m.config_json, m.dataset_json) == current
            ]
            newest_active = max(
                ((m.created_at, m.id) for m in active_current), default=None
            )
            for model in candidates:
                if (
                    newest_active is not None
                    and (model.created_at, model.id) <= newest_active
                ):
                    continue
                if (
                    not compatible(camera, model)
                    or fingerprint(model.config_json, model.dataset_json) != current
                ):
                    continue
                modes = qualified_modes(model)
                if not modes or not required.issubset(modes):
                    continue
                metrics = model.metrics_json or {}
                reports = metrics.get("mode_quality", {})
                rejection_ready = {mode for mode, report in metrics.get("unusable_quality", {}).items() if report.get("ready")}
                previous_rejection_ready = {mode for previous in active if compatible(camera, previous)
                    for mode, report in (previous.metrics_json or {}).get("unusable_quality", {}).items() if report.get("ready")}
                if not previous_rejection_ready.issubset(rejection_ready):
                    continue
                if model.algorithm == "local_geometry_v2" and required:
                    safe = all(
                        reports.get(mode, {}).get("challenger_safe", False)
                        for mode in required
                    )
                    improved = metrics.get("challenger_passed", False) or bool(
                        modes - required
                    )
                    rejection_improved = bool(rejection_ready) and all(
                        reports.get(mode, {}).get("mae_percent_points") is not None
                        and reports[mode]["mae_percent_points"] <= old["mae_percent_points"] + 1
                        for previous in active if compatible(camera, previous)
                        for mode, old in (previous.metrics_json or {}).get("mode_quality", {}).items()
                        if old.get("ready") and old.get("mae_percent_points") is not None
                    )
                    if not safe or not (improved or rejection_improved):
                        continue
                path = model_path(model)
                try:
                    valid = (
                        path.is_file()
                        and hashlib.sha256(path.read_bytes()).hexdigest()
                        == model.artifact_hash
                    )
                except OSError:
                    valid = False
                if not valid:
                    model.status = "failed"
                    model.error = "Gespeicherter Lernstand konnte nicht geprüft werden. Lernen erneut versuchen."
                    db.commit()
                    break
                for previous in active:
                    previous.active = False
                model.active = True
                camera.analysis_enabled = camera.recognition_requested
                db.commit()
                break


def learning_summary(db, camera):
    from .ml_service import compatible, eligible_samples, training_ready

    selected = eligible_samples(db, camera, include_unusable=True)
    models = list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.camera_id == camera.id)
            .where(ModelVersion.status != "cancelled")
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
        )
    )
    active = [m for m in models if m.active and compatible(camera, m)]
    ready = set().union(*(qualified_modes(m) for m in active))
    current_fp = (
        fingerprint(camera_config(camera), dataset_snapshot(selected))
        if camera.roi_json
        else None
    )
    matching = next(
        (
            m
            for m in models
            if m.managed
            and m.learning_generation == camera.learning_generation
            and current_fp
            and fingerprint(m.config_json, m.dataset_json) == current_fp
        ),
        None,
    )
    reports = {}
    for mode in MODES:
        samples = [s for s in selected if s.image_mode == mode and not s.rejection_reason]
        metric = (
            (matching.metrics_json or {}).get("mode_quality", {}).get(mode, {})
            if matching
            else {}
        )
        label = "Bereit" if mode in ready else "Weitere Bilder bestätigen"
        if (
            mode not in ready
            and training_ready(samples)
            and len({s.series_name for s in samples}) < 2
        ):
            label = "Bilder aus weiteren Aufnahmen nötig"
        elif (
            mode not in ready
            and metric.get("mae_percent_points") is not None
            and not metric.get("ready")
        ):
            label = "Weitere Beispiele nötig"
        levels = sorted({float(s.target_fill_level) for s in samples})
        span = max(levels, default=0) - min(levels, default=0)
        series_count = len({s.series_name for s in samples})
        if len(samples) < 12:
            recommendation = f"Noch {12 - len(samples)} bestätigte Bilder aufnehmen."
        elif series_count < 2:
            recommendation = "Bilder aus einer zweiten unabhängigen Aufnahmeserie bestätigen."
        elif len(levels) < 3:
            recommendation = "Mindestens einen weiteren deutlich anderen Füllstand bestätigen."
        elif span < 40:
            recommendation = "Einen niedrigeren oder höheren Füllstand bestätigen, bis mindestens 40 Prozentpunkte abgedeckt sind."
        elif metric.get("validation_samples", 0) < 6:
            recommendation = "Weitere Bilder aus unabhängigen Serien für mindestens sechs Validierungsvorhersagen bestätigen."
        elif metric.get("mae_percent_points") is not None and metric["mae_percent_points"] > 10:
            recommendation = "Weitere Beispiele an bisher schwach vertretenen Füllständen bestätigen."
        else:
            recommendation = "Der Bildmodus erfüllt die Qualitätsanforderungen."
        reports[mode] = {
            "ready": mode in ready,
            "label": label,
            "approved_count": len(samples),
            "series_count": series_count,
            "distinct_levels": len(levels),
            "target_span": span,
            "validation_samples": metric.get("validation_samples", 0),
            "mae_percent_points": metric.get("mae_percent_points"),
            "estimated_reliability": metric.get("estimated_reliability"),
            "expected_error_percent_points": metric.get(
                "expected_error_percent_points"
            ),
            "recommendation": recommendation,
        }
    if not camera.roi_json:
        state, label, hint = (
            "needs_roi",
            "Bildbereich festlegen",
            "Den relevanten Behälterbereich einmal festlegen.",
        )
    elif not camera.learning_enabled:
        state, label, hint = (
            "paused",
            "Lernen pausiert",
            "Vorhandene Erkennung bleibt nutzbar.",
        )
    elif any(m.status in ("queued", "running") for m in models):
        state, label, hint = (
            "training",
            "Lernstand wird aktualisiert",
            "Du kannst weitere Bilder bestätigen.",
        )
    elif camera.training_mode == "manual" and camera.learning_dirty:
        state, label, hint = ("manual_ready", "Training wartet auf deinen Start",
                              "Bestätigte Bewertungen sind gespeichert. Ein Admin kann das Training starten.")
    elif camera.learning_dirty:
        state, label, hint = (
            (
                "waiting",
                "Lernen wird vorbereitet",
                "Der nächste Worker-Durchlauf aktualisiert den Lernstand.",
            )
            if training_ready(selected)
            else (
                "collecting",
                "Bilder bestätigen",
                "Bestätigte Füllstände fließen automatisch ins Lernen ein.",
            )
        )
    elif matching and matching.status == "failed":
        state, label, hint = (
            "failed",
            "Lernen konnte nicht abgeschlossen werden",
            "Erneut versuchen. Die bisherige Erkennung bleibt erhalten.",
        )
    elif active:
        state, label, hint = (
            "active",
            "Erkennung aktiv" if camera.recognition_requested else "Erkennung pausiert",
            "Verbesserungen werden nach Prüfung automatisch übernommen.",
        )
    else:
        state, label, hint = (
            "collecting",
            "Weitere Bilder bestätigen",
            "Verschiedene Füllstände aus getrennten Aufnahmen helfen beim Lernen.",
        )
    if state not in ("paused", "needs_roi", "failed", "waiting", "manual_ready"):
        if not ready:
            hint = "Für Tag und Nacht verschiedene Füllstände aus getrennten Aufnahmen bestätigen."
        elif "night_ir" not in ready:
            hint = "Für Nacht noch Bilder bestätigen."
        elif "day" not in ready:
            hint = "Für Tag noch Bilder bestätigen."
    return {
        "can_train": bool(camera.learning_enabled and training_ready(selected) and camera.learning_dirty and not any(m.status in ("queued", "running") for m in models)),
        "training_running": any(m.status == "running" for m in models),
        "state": state,
        "label": label,
        "hint": hint,
        "modes": reports,
        "recognition_requested": camera.recognition_requested,
        "can_retry": bool(
            camera.learning_enabled
            and camera.roi_json
            and matching
            and matching.status == "failed"
        ),
        "approved_count": len(selected),
        "has_active_recognition": bool(active and camera.analysis_enabled),
        "unusable": {
            "approved_count": sum(bool(s.rejection_reason) for s in selected),
            "ready_modes": sorted({mode for m in active
                for mode, report in (m.metrics_json or {}).get("unusable_quality", {}).items()
                if report.get("ready")}),
            "modes": (matching.metrics_json or {}).get("unusable_quality", {}) if matching else {},
        },
    }
