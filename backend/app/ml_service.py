"""Worker-Jobs, Modellversionen und geprüfte automatische Messwerte."""

import hashlib
import json
import logging
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupShuffleSplit
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, aware, utcnow
from .models import (
    AnalysisResult,
    Camera,
    Container,
    ImageBuffer,
    ImportRecord,
    Measurement,
    ModelVersion,
    TrainingSample,
    WorkerStatus,
)
from .services import image_path, update_rates
from .vision import (
    FEATURE_VERSION,
    GEOMETRY_FEATURE_VERSION,
    POLYGON_FEATURE_VERSION,
    LocalForestAnalyzer,
    camera_config,
    config_key,
    image_features,
    physical_config_key,
    transform_key,
)

logger = logging.getLogger("fillens.ml")


def sample_path(sample: TrainingSample) -> Path:
    return settings().ml_data_dir / "training" / sample.camera_id / sample.filename


def model_path(model: ModelVersion) -> Path:
    return settings().ml_data_dir / "models" / model.camera_id / (model.id + ".json")


def compatible(camera: Camera, model: ModelVersion) -> bool:
    return (
        camera.learning_generation == model.learning_generation
        and physical_config_key(camera_config(camera))
        == physical_config_key(model.config_json)
    )


def train_model_v1(
    db: Session, model: ModelVersion, progress: Callable | None = None
) -> None:
    model.status, model.error = "running", None
    db.commit()
    try:
        matrix, targets, groups = [], [], []
        for index, row in enumerate(model.dataset_json):
            sample = db.get(TrainingSample, row["id"])
            if not sample or sample.file_hash != row["hash"]:
                raise ValueError(
                    "Ein ausgewähltes Trainingsbild fehlt oder wurde verändert."
                )
            vector, _, _ = image_features(sample_path(sample), model.config_json["roi"])
            matrix.append(vector)
            targets.append(row["target"])
            groups.append(row["series"])
            if progress:
                progress("training", index + 1, 0)
        x, y, group = np.asarray(matrix), np.asarray(targets), np.asarray(groups)
        predictions, expected, held_out = [], [], set()
        modes = np.array([s["image_mode"] for s in model.dataset_json])
        mode_predictions = {m: [] for m in ("day", "night_ir")}
        mode_expected = {m: [] for m in mode_predictions}
        mode_held = {m: set() for m in mode_predictions}
        if len(set(groups)) >= 2:
            for train, validation in GroupShuffleSplit(
                n_splits=5, test_size=0.3, random_state=42
            ).split(x, y, group):
                if len(set(y[train])) < 2:
                    continue
                estimator = RandomForestRegressor(
                    n_estimators=64, max_depth=12, random_state=42, n_jobs=1
                )
                estimator.fit(x[train], y[train])
                estimated = estimator.predict(x[validation])
                predictions.extend(estimated.tolist())
                for mode in mode_predictions:
                    train_targets = y[train][modes[train] == mode]
                    if len(set(train_targets)) < 3 or np.ptp(train_targets) < 40:
                        continue
                    mask = modes[validation] == mode
                    mode_predictions[mode].extend(estimated[mask].tolist())
                    mode_expected[mode].extend(y[validation][mask].tolist())
                    mode_held[mode].update(validation[mask].tolist())
                expected.extend(y[validation].tolist())
                held_out.update(validation.tolist())
        metrics = {
            "mae_percent_points": round(
                float(mean_absolute_error(expected, predictions)), 3
            )
            if expected
            else None,
            "validation_samples": len(held_out),
            "series_count": len(set(groups)),
            "training_samples": len(y),
            "min_target": float(y.min()),
            "max_target": float(y.max()),
            "validation_method": "group_shuffle_split" if expected else "unavailable",
            "confidence_type": "heuristic_not_calibrated",
            "feature_version": model.config_json["feature_version"],
        }
        quality = {}
        for mode in mode_predictions:
            selected = [row for row in model.dataset_json if row["image_mode"] == mode]
            targets_mode = [s["target"] for s in selected]
            error = (
                round(
                    float(
                        mean_absolute_error(mode_expected[mode], mode_predictions[mode])
                    ),
                    3,
                )
                if mode_expected[mode]
                else None
            )
            quality[mode] = {
                "sample_count": len(selected),
                "series_count": len({s["series"] for s in selected}),
                "distinct_levels": len(set(targets_mode)),
                "target_span": max(targets_mode, default=0)
                - min(targets_mode, default=0),
                "validation_samples": len(mode_held[mode]),
                "mae_percent_points": error,
            }
            q = quality[mode]
            q["ready"] = bool(
                q["sample_count"] >= 12
                and q["series_count"] >= 2
                and q["distinct_levels"] >= 3
                and q["target_span"] >= 40
                and q["validation_samples"] >= 2
                and error is not None
                and error <= 10
            )
        metrics["mode_quality"] = quality
        estimator = RandomForestRegressor(
            n_estimators=96, max_depth=12, random_state=42, n_jobs=1
        )
        estimator.fit(x, y)
        trees = []
        for tree in estimator.estimators_:
            t = tree.tree_
            trees.append(
                {
                    "left": t.children_left.tolist(),
                    "right": t.children_right.tolist(),
                    "feature": t.feature.tolist(),
                    "threshold": t.threshold.tolist(),
                    "value": t.value.reshape(t.node_count, -1)[:, 0].tolist(),
                }
            )
        anchor_indices = np.linspace(0, len(x) - 1, min(len(x), 1024), dtype=int)
        artifact = {
            "schema": 1,
            "managed_learning": model.managed,
            "feature_version": model.config_json["feature_version"],
            "config": model.config_json,
            "metrics": metrics,
            "trees": trees,
            "anchors": x[anchor_indices].tolist(),
            "scale": x.std(axis=0).tolist(),
            "image_modes": sorted({row["image_mode"] for row in model.dataset_json}),
        }
        content = json.dumps(artifact, allow_nan=False, separators=(",", ":")).encode()
        path = model_path(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        model.metrics_json, model.artifact_hash = (
            metrics,
            hashlib.sha256(content).hexdigest(),
        )
        model.status, model.finished_at = "ready", utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        model.status, model.active, model.finished_at = "failed", False, utcnow()
        model.error = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("Ein ausgewähltes")
            else "Training fehlgeschlagen. Bilddateien, ROI und Worker-Protokoll prüfen."
        )
        db.commit()
        logger.error(
            "event=training_failed model=%s type=%s", model.id, type(exc).__name__
        )


def _forest(kind: str, trees=128):
    cls = ExtraTreesRegressor if kind == "extra_trees" else RandomForestRegressor
    return cls(
        n_estimators=trees,
        max_depth=14,
        random_state=42,
        n_jobs=1,
    )


def _sample_weights(matrix, targets, groups):
    buckets = [(str(group), int(float(target) // 10)) for group, target in zip(groups, targets)]
    bucket_counts = Counter(buckets)
    signatures = [
        (str(group), int(float(target) // 10), np.round(row, 2).tobytes())
        for row, target, group in zip(matrix, targets, groups)
    ]
    signature_counts = Counter(signatures)
    weights = np.asarray(
        [
            1 / bucket_counts[bucket] / signature_counts[signature]
            for bucket, signature in zip(buckets, signatures)
        ],
        dtype=float,
    )
    return weights / max(weights.mean(), 1e-9)


def _evaluate(matrix, targets, groups, kind, splits, weights):
    predicted, expected, held, spreads = [], [], set(), []
    for train, validation in splits:
        if len(set(targets[train])) < 2:
            continue
        estimator = _forest(kind, 96)
        estimator.fit(matrix[train], targets[train], sample_weight=weights[train])
        values = estimator.predict(matrix[validation])
        tree_values = np.asarray(
            [tree.predict(matrix[validation]) for tree in estimator.estimators_]
        )
        spreads.extend(tree_values.std(axis=0).tolist())
        predicted.extend(values.tolist())
        expected.extend(targets[validation].tolist())
        held.update(validation.tolist())
    error = (
        round(float(mean_absolute_error(expected, predicted)), 3)
        if expected
        else None
    )
    residuals = np.abs(np.asarray(expected) - np.asarray(predicted)).tolist()
    return error, predicted, expected, held, residuals, spreads


def _export_trees(estimator):
    trees = []
    for tree in estimator.estimators_:
        structure = tree.tree_
        trees.append(
            {
                "left": structure.children_left.tolist(),
                "right": structure.children_right.tolist(),
                "feature": structure.feature.tolist(),
                "threshold": structure.threshold.tolist(),
                "value": structure.value.reshape(structure.node_count, -1)[:, 0].tolist(),
            }
        )
    return trees


def _calibration(estimator, matrix, residuals, validation_spreads):
    scale = np.maximum(matrix.std(axis=0), 0.03)
    if len(matrix) > 1:
        normalized = (matrix[:, None, :] - matrix[None, :, :]) / scale
        distances = np.sqrt(np.mean(normalized**2, axis=2))
        np.fill_diagonal(distances, np.inf)
        nearest = distances.min(axis=1)
        distance_p90 = float(np.quantile(nearest, 0.9))
    else:
        distance_p90 = 1.0
    spreads = np.asarray(validation_spreads, dtype=float)
    if not len(spreads):
        tree_values = np.asarray(
            [tree.predict(matrix) for tree in estimator.estimators_]
        )
        spreads = tree_values.std(axis=0)
    residuals = np.asarray(residuals, dtype=float)
    reliability = (
        float((np.count_nonzero(residuals <= 10) + 1) / (len(residuals) + 2))
        if len(residuals)
        else 0.2
    )
    return {
        "reliability": round(reliability, 4),
        "error_p80": round(float(np.quantile(residuals, 0.8)), 3)
        if len(residuals)
        else 25.0,
        "spread_p90": round(float(np.quantile(spreads, 0.9)), 3),
        "distance_p90": round(max(distance_p90, 0.1), 3),
        "validation_predictions": len(residuals),
    }


def train_model_v2(db: Session, model: ModelVersion, progress: Callable | None = None):
    model.status, model.error = "running", None
    db.commit()
    try:
        from .unusable_learning import train_unusable

        unusable_heads, unusable_reports = train_unusable(db, model, progress)
        geometry, legacy, targets, groups, modes = [], [], [], [], []
        legacy_version = (
            POLYGON_FEATURE_VERSION
            if model.config_json.get("roi") and "points" in model.config_json["roi"]
            else FEATURE_VERSION
        )
        for index, row in enumerate(model.dataset_json):
            if row.get("rejection_reason"):
                continue
            sample = db.get(TrainingSample, row["id"])
            if not sample or sample.file_hash != row["hash"]:
                raise ValueError("Ein ausgewähltes Trainingsbild fehlt oder wurde verändert.")
            path = sample_path(sample)
            geometry.append(
                image_features(path, model.config_json["roi"], GEOMETRY_FEATURE_VERSION)[0]
            )
            legacy.append(image_features(path, model.config_json["roi"], legacy_version)[0])
            targets.append(float(row["target"]))
            groups.append(row["series"])
            modes.append(row["image_mode"])
            if progress:
                progress("training", index + 1, 0)
        geometry = np.asarray(geometry)
        legacy = np.asarray(legacy)
        targets = np.asarray(targets)
        groups = np.asarray(groups)
        modes = np.asarray(modes)
        reports, heads = {}, {}
        all_expected, all_predictions = [], []
        improved, safe = False, True
        for mode in ("day", "night_ir"):
            indices = np.flatnonzero(modes == mode)
            selected_targets = targets[indices]
            selected_groups = groups[indices]
            matrix = geometry[indices]
            old_matrix = legacy[indices]
            distinct = len(set(selected_targets.tolist()))
            series = len(set(selected_groups.tolist()))
            span = float(np.ptp(selected_targets)) if len(selected_targets) else 0.0
            report = {
                "sample_count": len(indices),
                "series_count": series,
                "distinct_levels": distinct,
                "target_span": span,
                "validation_samples": 0,
                "mae_percent_points": None,
                "baseline_mae_percent_points": None,
                "estimated_reliability": None,
                "algorithm": None,
                "ready": False,
            }
            if len(indices) < 2 or distinct < 2:
                reports[mode] = report
                continue
            weights = _sample_weights(matrix, selected_targets, selected_groups)
            splits = (
                list(
                    GroupShuffleSplit(n_splits=8, test_size=0.3, random_state=42).split(
                        matrix, selected_targets, selected_groups
                    )
                )
                if series >= 2
                else []
            )
            candidates = {}
            for kind in ("random_forest", "extra_trees"):
                candidates[kind] = _evaluate(
                    matrix, selected_targets, selected_groups, kind, splits, weights
                )
            baseline = _evaluate(
                old_matrix,
                selected_targets,
                selected_groups,
                "random_forest",
                splits,
                weights,
            )
            kind = min(
                candidates,
                key=lambda name: candidates[name][0]
                if candidates[name][0] is not None
                else float("inf"),
            )
            error, predictions, expected, held, residuals, validation_spreads = candidates[kind]
            baseline_error = baseline[0]
            estimator = _forest(kind)
            estimator.fit(matrix, selected_targets, sample_weight=weights)
            calibration = _calibration(
                estimator, matrix, residuals, validation_spreads
            )
            ready = bool(
                len(indices) >= 12
                and series >= 2
                and distinct >= 3
                and span >= 40
                and len(held) >= 6
                and error is not None
                and error <= 10
            )
            mode_safe = bool(
                baseline_error is None or error is not None and error <= baseline_error + 1
            )
            mode_improved = bool(
                error is not None
                and (baseline_error is None or error < baseline_error - 0.05)
            )
            safe = safe and (not ready or mode_safe)
            improved = improved or (ready and mode_improved)
            report.update(
                {
                    "validation_samples": len(held),
                    "mae_percent_points": error,
                    "baseline_mae_percent_points": baseline_error,
                    "estimated_reliability": calibration["reliability"],
                    "expected_error_percent_points": calibration["error_p80"],
                    "algorithm": kind,
                    "challenger_safe": mode_safe,
                    "challenger_improved": mode_improved,
                    "ready": ready,
                }
            )
            head = {
                "algorithm": kind,
                "trees": _export_trees(estimator),
                "anchors": matrix.tolist(),
                "scale": matrix.std(axis=0).tolist(),
                "calibration": calibration,
                "ready": ready,
            }
            heads[mode] = head
            reports[mode] = report
            all_expected.extend(expected)
            all_predictions.extend(predictions)
        if len(set(targets.tolist())) < 2:
            raise ValueError("Mindestens zwei unterschiedliche Füllstände benötigt.")
        fallback = _forest("random_forest", 96)
        fallback_weights = _sample_weights(geometry, targets, groups)
        fallback.fit(geometry, targets, sample_weight=fallback_weights)
        fallback_head = {
            "algorithm": "random_forest",
            "trees": _export_trees(fallback),
            "anchors": geometry.tolist(),
            "scale": geometry.std(axis=0).tolist(),
            "calibration": {
                "reliability": 0.2,
                "error_p80": 25.0,
                "spread_p90": 1.0,
                "distance_p90": 1.0,
                "validation_predictions": 0,
            },
            "ready": False,
        }
        metrics = {
            "mae_percent_points": round(
                float(mean_absolute_error(all_expected, all_predictions)), 3
            )
            if all_expected
            else None,
            "validation_samples": sum(
                report["validation_samples"] for report in reports.values()
            ),
            "series_count": len(set(groups.tolist())),
            "training_samples": len(targets),
            "min_target": float(targets.min()),
            "max_target": float(targets.max()),
            "validation_method": "group_shuffle_split" if all_expected else "unavailable",
            "confidence_type": "empirical_residual_calibration",
            "feature_version": GEOMETRY_FEATURE_VERSION,
            "mode_quality": reports,
            "challenger_passed": bool(improved and safe),
            "unusable_quality": unusable_reports,
        }
        artifact = {
            "schema": 2,
            "managed_learning": model.managed,
            "feature_version": GEOMETRY_FEATURE_VERSION,
            "config": model.config_json,
            "metrics": metrics,
            "heads": heads,
            "unusable_heads": unusable_heads,
            "fallback": fallback_head,
            "image_modes": sorted(set(modes.tolist())),
        }
        content = json.dumps(artifact, allow_nan=False, separators=(",", ":")).encode()
        path = model_path(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        model.algorithm = "local_geometry_v2"
        model.metrics_json = metrics
        model.artifact_hash = hashlib.sha256(content).hexdigest()
        model.status, model.finished_at = "ready", utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        model.status, model.active, model.finished_at = "failed", False, utcnow()
        model.error = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc).startswith(("Ein ausgewähltes", "Mindestens zwei"))
            else "Training fehlgeschlagen. Bilddateien, ROI und Worker-Protokoll prüfen."
        )
        db.commit()
        logger.error("event=training_failed model=%s type=%s", model.id, type(exc).__name__)


def train_model(db: Session, model: ModelVersion, progress: Callable | None = None):
    if model.config_json.get("feature_version") == GEOMETRY_FEATURE_VERSION:
        return train_model_v2(db, model, progress)
    return train_model_v1(db, model, progress)


def run_training_job(progress_factory: Callable | None = None) -> bool:
    with SessionLocal() as db:
        model = db.scalar(
            select(ModelVersion)
            .join(Camera, Camera.id == ModelVersion.camera_id)
            .where(
                ModelVersion.status == "queued",
                or_(ModelVersion.managed.is_(False), Camera.learning_enabled.is_(True)),
            )
            .order_by(ModelVersion.created_at)
            .limit(1)
        )
        if not model:
            return False
        camera = db.get(
            Camera, model.camera_id, with_for_update=True, populate_existing=True
        )
        db.refresh(model)
        if model.status != "queued" or (model.managed and not camera.learning_enabled):
            return False
        if model.trigger == "automatic" and camera.training_mode != "automatic":
            model.status, model.finished_at = "cancelled", utcnow()
            camera.learning_dirty = True
            db.commit()
            return False
        db.execute(
            update(WorkerStatus)
            .where(WorkerStatus.id == "importer")
            .values(
                phase="training",
                active_camera_id=model.camera_id,
                operation_started_at=utcnow(),
                heartbeat_at=utcnow(),
                scanned_entries=0,
                imported_count=0,
            )
        )
        model.status = "running"
        db.commit()
        progress = progress_factory(model.camera_id) if progress_factory else None
        train_model(db, model, progress)
        from .training_store import cleanup_discarded

        cleanup_discarded(db, model.camera_id)
        if model.managed:
            from .learning import adopt_ready_models

            adopt_ready_models()
        return True


def create_analysis_measurement(
    db: Session,
    result: AnalysisResult,
    camera: Camera,
    corrected: float | None = None,
    user_id: str | None = None,
) -> Measurement:
    container = db.get(
        Container, camera.container_id, with_for_update=True, populate_existing=True
    )
    existing = db.scalar(
        select(Measurement).where(
            Measurement.source_import_id == result.source_import_id
        )
    )
    if existing:
        result.status, result.measurement_id = "superseded", existing.id
        return existing
    effective = corrected if corrected is not None else result.raw_fill_level
    if effective is None:
        raise ValueError("Kein Füllstand vorhanden")
    measurement = Measurement(
        container_id=container.id,
        camera_id=camera.id,
        source_import_id=result.source_import_id,
        measured_at=result.measured_at,
        raw_fill_level=result.raw_fill_level,
        corrected_fill_level=corrected,
        effective_fill_level=effective,
        confidence=result.confidence,
        absolute_level=container.capacity * effective / 100,
        capacity_snapshot=container.capacity,
        capacity_unit=container.capacity_unit,
        analysis_method=(
            "manual_review_v1"
            if corrected is not None
            else (db.get(ModelVersion, result.model_id).algorithm or "local_rf_v1")
        ),
        model_version=result.model_id,
        image_mode=result.image_mode,
        timestamp_source=result.timestamp_source,
        created_by=user_id,
    )
    db.add(measurement)
    db.flush()
    update_rates(db, measurement)
    result.status, result.measurement_id = "accepted", measurement.id
    return measurement


def analyze_buffer(
    db: Session, camera: Camera, progress: Callable | None = None
) -> int:
    if not camera.analysis_enabled or not camera.roi_json:
        return 0
    models = list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.camera_id == camera.id,
                ModelVersion.active.is_(True),
                ModelVersion.status == "ready",
            )
        )
    )
    models = [model for model in models if compatible(camera, model)]
    if not models:
        return 0
    images = list(
        db.scalars(
            select(ImageBuffer)
            .where(
                ImageBuffer.camera_id == camera.id,
                ~select(Measurement.id).where(Measurement.source_import_id == ImageBuffer.import_id).exists(),
                ~select(AnalysisResult.id).where(
                    AnalysisResult.source_import_id == ImageBuffer.import_id,
                    AnalysisResult.model_id.in_([model.id for model in models]),
                ).exists(),
            )
            .order_by(ImageBuffer.captured_at.desc())
            .limit(settings().max_imports_per_cycle)
        )
    )
    analyzed = 0
    for image in images:
        camera = db.get(Camera, camera.id, with_for_update=True, populate_existing=True)
        if not camera.analysis_enabled:
            break
        if db.scalar(
            select(Measurement.id).where(
                Measurement.source_import_id == image.import_id
            )
        ):
            continue
        if image.transform_key != transform_key(
            camera.image_rotation, camera.image_flip
        ) and not (
            image.transform_key is None
            and camera.image_rotation == 0
            and not camera.image_flip
        ):
            continue
        try:
            _, mode, _ = image_features(image_path(image), camera.roi_json)
        except Exception as exc:
            logger.warning(
                "event=analysis_image_unavailable camera=%s type=%s",
                camera.id,
                type(exc).__name__,
            )
            continue
        model = next(
            (m for m in models if m.image_mode == mode),
            next((m for m in models if m.image_mode == "all"), None),
        )
        if not model or db.scalar(
            select(AnalysisResult.id).where(
                AnalysisResult.source_import_id == image.import_id,
                AnalysisResult.model_id == model.id,
            )
        ):
            continue
        if progress:
            progress("analysis", analyzed, 0)
        result = AnalysisResult(
            camera_id=camera.id,
            source_import_id=image.import_id,
            model_id=model.id,
            measured_at=image.captured_at,
            image_mode=mode,
            timestamp_source=image.timestamp_source,
        )
        try:
            content = model_path(model).read_bytes()
            if hashlib.sha256(content).hexdigest() != model.artifact_hash:
                raise ValueError("Modellintegrität verletzt")
            analysis = LocalForestAnalyzer(json.loads(content)).analyze(
                image_path(image)
            )
            result.raw_fill_level, result.confidence, result.image_mode = (
                analysis.fill_level_percent,
                analysis.confidence,
                analysis.image_mode,
            )
            result.estimated_error = analysis.estimated_error
            result.reason = analysis.reason
            image.image_mode = result.image_mode
            sample = capture_review_sample(db, camera, image)
            result.review_sample_id = sample.id if sample else None
            result.review_visible = camera.learning_enabled and sample is not None and not sample.approved
            if sample is None and result.confidence < camera.min_confidence:
                result.status = "skipped"
            db.add(result)
            db.flush()
            if analysis.unusable:
                result.status = "discarded"
            elif analysis.fill_level_percent is not None and result.confidence >= camera.min_confidence:
                create_analysis_measurement(db, result, camera)
            else:
                result.reason = (
                    result.reason
                    or "Modell-Vertrauen liegt unter der automatischen Freigabeschwelle."
                )
            image.image_mode = result.image_mode
            db.commit()
            analyzed += 1
        except Exception as exc:
            db.rollback()
            result = AnalysisResult(
                camera_id=camera.id,
                source_import_id=image.import_id,
                model_id=model.id,
                measured_at=image.captured_at,
                image_mode=mode,
                timestamp_source=image.timestamp_source,
                status="failed",
                review_visible=False,
                reason="Analyse fehlgeschlagen. Modellintegrität, Bilddatei und Worker-Protokoll prüfen.",
            )
            db.add(result)
            db.commit()
            logger.error(
                "event=analysis_failed camera=%s model=%s type=%s",
                camera.id,
                model.id,
                type(exc).__name__,
            )
    return analyzed


def capture_review_sample(
    db: Session, camera: Camera, image: ImageBuffer, *, explicit: bool = False
) -> TrainingSample | None:
    """Importiertes Bild unabhängig vom Modell als Lernreferenz sichern."""
    if not camera.learning_enabled and not explicit:
        return None
    discarded = db.scalar(select(AnalysisResult).where(
        AnalysisResult.source_import_id == image.import_id,
        AnalysisResult.discarded_at.is_not(None),
    ))
    if discarded:
        image.training_sample_id = discarded.review_sample_id
        return None
    content = image_path(image).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(TrainingSample).where(
            TrainingSample.camera_id == camera.id, TrainingSample.file_hash == digest
        )
    )
    if existing:
        image.training_sample_id = existing.id
    if existing and (existing.discarded_at or existing.queue_state == "expired"):
        return None
    if existing:
        if existing.queue_state == "deferred" and not existing.approved:
            path = sample_path(existing)
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            existing.queue_state = "pending"
            from .training_store import rebalance_window

            rebalance_window(db, camera.id)
        return existing
    source = db.get(ImportRecord, image.import_id)
    sample = TrainingSample(
        camera_id=camera.id,
        filename=uuid4().hex + ".jpg",
        original_name=f"Prüfbild {aware(image.captured_at).isoformat()}",
        origin="camera",
        captured_at=image.captured_at,
        file_hash=digest,
        size_bytes=len(content),
        series_name=f"Kamera {aware(image.captured_at).strftime('%Y-%m-%d')}",
        image_mode=image.image_mode,
        approved=False,
        queue_state="pending",
        window_managed=True,
        source_fingerprint=source.fingerprint if source else None,
        target_fill_level=None,
        transform_key=image.transform_key or transform_key(0, False),
        created_by=None,
    )
    path = sample_path(sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    sample.image_mode = image_features(path, camera.roi_json or {"x": 0, "y": 0, "width": 1, "height": 1})[1]
    image.image_mode = sample.image_mode
    db.add(sample)
    db.flush()
    image.training_sample_id = sample.id
    from .training_store import rebalance_window

    rebalance_window(db, camera.id)
    if sample.queue_state != "pending":
        return None
    return sample


def eligible_samples(db, camera, image_mode="all", *, include_unusable=False):
    query = select(TrainingSample).where(
        TrainingSample.camera_id == camera.id,
        TrainingSample.approved.is_(True),
        TrainingSample.discarded_at.is_(None),
        or_(TrainingSample.target_fill_level.is_not(None),
            TrainingSample.rejection_reason.is_not(None) if include_unusable else False),
        TrainingSample.transform_key
        == transform_key(camera.image_rotation, camera.image_flip),
    )
    if image_mode != "all":
        query = query.where(TrainingSample.image_mode == image_mode)
    return list(
        db.scalars(query.order_by(TrainingSample.created_at, TrainingSample.id))
    )


def training_ready(selected):
    targets = [s.target_fill_level for s in selected if s.target_fill_level is not None and not s.rejection_reason]
    return (
        len(targets) >= 12
        and len(set(targets)) >= 3
        and max(targets, default=0) - min(targets, default=0) >= 40
    )


def enqueue_model(db, camera, name, image_mode, user_id, *, managed=False, force=False, trigger="manual"):
    if not camera.roi_json:
        raise ValueError("Zuerst den relevanten Bildbereich (ROI) speichern.")
    if db.scalar(
        select(ModelVersion.id).where(
            ModelVersion.camera_id == camera.id,
            ModelVersion.status.in_(["queued", "running"]),
        )
    ):
        raise ValueError("Für diese Kamera steht bereits ein Training aus.")
    selected = eligible_samples(db, camera, image_mode, include_unusable=True)
    if not training_ready(selected):
        raise ValueError(
            "Mindestens 12 freigegebene Bilder, drei Soll-Füllstände und 40 Prozentpunkte Spannweite benötigt. Empfohlen: 30–50 Bilder aus mehreren Serien."
        )
    from .learning import dataset_snapshot, fingerprint

    snapshot = dataset_snapshot(selected)
    if managed and not force:
        current = fingerprint(camera_config(camera), snapshot)
        previous = list(
            db.scalars(
                select(ModelVersion).where(
                    ModelVersion.camera_id == camera.id,
                    ModelVersion.managed.is_(True),
                    ModelVersion.learning_generation == camera.learning_generation,
                    ModelVersion.status != "cancelled",
                )
            )
        )
        if any(fingerprint(m.config_json, m.dataset_json) == current for m in previous):
            camera.learning_dirty = False
            return None
    model = ModelVersion(
        camera_id=camera.id,
        name=name,
        managed=managed,
        trigger=trigger,
        learning_generation=camera.learning_generation,
        version=utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6],
        image_mode=image_mode,
        config_json=camera_config(camera),
        dataset_json=snapshot,
        training_sample_count=len(snapshot),
        created_by=user_id,
    )
    db.add(model)
    if image_mode == "all":
        camera.learning_dirty = False
    return model


def queue_learning_jobs():
    from .models import User

    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Camera.id).where(
                    Camera.learning_enabled.is_(True), Camera.learning_dirty.is_(True), Camera.training_mode == "automatic"
                )
            )
        )
    for camera_id in ids:
        with SessionLocal() as db:
            camera = db.get(Camera, camera_id, with_for_update=True)
            admin = db.scalar(
                select(User)
                .where(User.active.is_(True), User.role == "admin")
                .order_by(User.created_at)
                .limit(1)
            )
            if not camera.learning_enabled or camera.training_mode != "automatic" or not admin:
                continue
            try:
                enqueue_model(
                    db, camera, "Gemeinsamer Lernstand", "all", admin.id, managed=True, trigger="automatic"
                )
                db.commit()
            except ValueError:
                db.rollback()


def learning_status(db, camera):
    selected = eligible_samples(db, camera)
    models = list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.camera_id == camera.id)
            .order_by(ModelVersion.created_at.desc())
        )
    )
    matching = [m for m in models if m.status == "ready" and compatible(camera, m)]
    validated = next(
        (
            m
            for m in matching
            if m.metrics_json
            and m.metrics_json.get("mae_percent_points") is not None
            and m.metrics_json["mae_percent_points"] <= 10
            and m.metrics_json.get("validation_samples", 0) >= 2
        ),
        None,
    )
    if any(m.status in ("queued", "running") for m in models):
        state, label = "training", "Training läuft / geplant"
    elif validated:
        state, label = "validated", "Validiert: mittlerer Fehler ≤ 10 Prozentpunkte"
    elif not training_ready(selected):
        state, label = "needs_data", "Weitere bewertete Füllstände benötigt"
    elif not matching:
        state, label = "ready_to_train", "Genug Daten für erstes Training"
    elif not any(
        m.metrics_json and m.metrics_json.get("mae_percent_points") is not None
        for m in matching
    ):
        state, label = "needs_validation", "Unabhängige Aufnahmeserien fehlen"
    else:
        state, label = (
            "needs_improvement",
            "Weiter lernen: Validierungsfehler > 10 Prozentpunkte",
        )
    return {
        "state": state,
        "label": label,
        "training_ready": training_ready(selected),
        "validated": bool(validated),
        "validated_model_id": validated.id if validated else None,
        "approved_count": len(selected),
        "distinct_levels": len({s.target_fill_level for s in selected}),
        "series_count": len({s.series_name for s in selected}),
        "target_span": max((s.target_fill_level for s in selected), default=0)
        - min((s.target_fill_level for s in selected), default=0),
    }


def preserve_review_references(db, camera):
    """Noch vorhandene Referenzen älterer Versionen vor dem nächsten Import retten."""
    camera = db.get(Camera, camera.id, with_for_update=True, populate_existing=True)
    if not camera.learning_enabled:
        return
    from .training_store import collect_buffer
    collect_buffer(db, camera)
    results = list(
        db.scalars(
            select(AnalysisResult)
            .join(ImageBuffer, ImageBuffer.import_id == AnalysisResult.source_import_id)
            .where(
                AnalysisResult.camera_id == camera.id,
                AnalysisResult.review_visible.is_(True),
                AnalysisResult.review_sample_id.is_(None),
                AnalysisResult.status.in_(["pending", "accepted"]),
            )
        )
    )
    for result in results:
        image = db.scalar(
            select(ImageBuffer).where(ImageBuffer.import_id == result.source_import_id)
        )
        if image and image_path(image).is_file():
            sample = capture_review_sample(db, camera, image)
            result.review_sample_id = sample.id if sample else None
    db.commit()
