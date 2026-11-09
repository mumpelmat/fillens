"""Kameraspezifische Verwendbarkeitsprüfung, getrennt von Soll-Füllständen."""

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GroupKFold

from .vision import GEOMETRY_FEATURE_VERSION, image_features

FULL_FRAME = {"x": 0, "y": 0, "width": 1, "height": 1}
REJECTION_THRESHOLD = 0.9


def train_unusable(db, model, progress=None):
    from .ml_service import _export_trees, sample_path
    from .models import TrainingSample

    if not any(row.get("rejection_reason") for row in model.dataset_json):
        return {}, {}
    rows, features = [], []
    for index, row in enumerate(model.dataset_json):
        sample = db.get(TrainingSample, row["id"])
        if not sample or sample.file_hash != row["hash"]:
            raise ValueError("Ein ausgewähltes Trainingsbild fehlt oder wurde verändert.")
        features.append(image_features(sample_path(sample), FULL_FRAME, GEOMETRY_FEATURE_VERSION)[0])
        rows.append(row)
        if progress:
            progress("training", index + 1, 0)
    matrix = np.asarray(features)
    heads, reports = {}, {}
    for mode in ("day", "night_ir"):
        indices = [i for i, row in enumerate(rows) if row["image_mode"] == mode]
        x = matrix[indices]
        y = np.asarray([int(bool(rows[i].get("rejection_reason"))) for i in indices])
        groups = np.asarray([rows[i]["series"] for i in indices])
        counts = [int(np.sum(y == label)) for label in (0, 1)]
        report = {"usable_count": counts[0], "unusable_count": counts[1],
                  "validation_usable": 0, "validation_unusable": 0,
                  "false_rejections": 0, "recall": None, "ready": False}
        reports[mode] = report
        if min(counts) < 6 or any(len(set(groups[y == label])) < 2 for label in (0, 1)):
            continue

        def estimator():
            return RandomForestRegressor(n_estimators=96, max_depth=12, random_state=42, n_jobs=1)

        predictions = np.full(len(y), np.nan)
        for train, test in GroupKFold(n_splits=min(5, len(set(groups)))).split(x, y, groups):
            if len(set(y[train])) != 2:
                continue
            forest = estimator()
            weights = np.asarray([1 / np.sum(y[train] == label) for label in y[train]])
            forest.fit(x[train], y[train], sample_weight=weights)
            predictions[test] = forest.predict(x[test])
        valid = np.isfinite(predictions)
        held_good, held_bad = int(np.sum(valid & (y == 0))), int(np.sum(valid & (y == 1)))
        rejected = predictions >= REJECTION_THRESHOLD
        false_rejections = int(np.sum(rejected & (y == 0)))
        recall = float(np.sum(rejected & (y == 1)) / held_bad) if held_bad else None
        ready = held_good >= 6 and held_bad >= 6 and false_rejections == 0 and recall >= 0.8
        report.update(validation_usable=held_good, validation_unusable=held_bad,
                      false_rejections=false_rejections, recall=recall, ready=bool(ready))
        forest = estimator()
        forest.fit(x, y, sample_weight=np.asarray([1 / counts[label] for label in y]))
        heads[mode] = {"ready": bool(ready), "threshold": REJECTION_THRESHOLD,
                       "trees": _export_trees(forest), "anchors": x.tolist(),
                       "scale": x.std(axis=0).tolist()}
    return heads, reports
