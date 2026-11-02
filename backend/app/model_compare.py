"""Reproduzierbarer, schreibgeschützter Offlinevergleich von Merkmalen v1/v2."""

import argparse
import json

import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sqlalchemy import select

from .db import SessionLocal
from .ml_service import _evaluate, _sample_weights, eligible_samples, sample_path
from .models import Camera
from .vision import (
    FEATURE_VERSION,
    GEOMETRY_FEATURE_VERSION,
    POLYGON_FEATURE_VERSION,
    image_features,
)


def compare_camera(db, camera: Camera) -> dict:
    rows = eligible_samples(db, camera)
    result = {"camera_id": camera.id, "modes": {}}
    legacy_version = (
        POLYGON_FEATURE_VERSION
        if camera.roi_json and "points" in camera.roi_json
        else FEATURE_VERSION
    )
    for mode in ("day", "night_ir"):
        samples = [row for row in rows if row.image_mode == mode]
        targets = np.asarray([row.target_fill_level for row in samples], dtype=float)
        groups = np.asarray([row.series_name for row in samples])
        report = {
            "samples": len(samples),
            "series": len(set(groups.tolist())),
            "v1_mae_percent_points": None,
            "v2_mae_percent_points": None,
            "v2_algorithm": None,
            "delta_percent_points": None,
        }
        if len(samples) < 2 or len(set(groups.tolist())) < 2 or len(set(targets.tolist())) < 2:
            result["modes"][mode] = report
            continue
        v1 = np.asarray(
            [
                image_features(sample_path(row), camera.roi_json, legacy_version)[0]
                for row in samples
            ]
        )
        v2 = np.asarray(
            [
                image_features(
                    sample_path(row), camera.roi_json, GEOMETRY_FEATURE_VERSION
                )[0]
                for row in samples
            ]
        )
        splits = list(
            GroupShuffleSplit(n_splits=8, test_size=0.3, random_state=42).split(
                v2, targets, groups
            )
        )
        weights = _sample_weights(v2, targets, groups)
        v1_error = _evaluate(
            v1, targets, groups, "random_forest", splits, weights
        )[0]
        candidates = {
            kind: _evaluate(v2, targets, groups, kind, splits, weights)[0]
            for kind in ("random_forest", "extra_trees")
        }
        valid = {key: value for key, value in candidates.items() if value is not None}
        best = min(valid, key=valid.get) if valid else None
        v2_error = valid.get(best) if best else None
        report.update(
            {
                "v1_mae_percent_points": v1_error,
                "v2_mae_percent_points": v2_error,
                "v2_algorithm": best,
                "delta_percent_points": round(v2_error - v1_error, 3)
                if v1_error is not None and v2_error is not None
                else None,
            }
        )
        result["modes"][mode] = report
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Schreibgeschützter V1/V2-Vergleich auf bestätigten Bildern"
    )
    parser.add_argument("--camera-id")
    args = parser.parse_args()
    with SessionLocal() as db:
        cameras = (
            [db.get(Camera, args.camera_id)]
            if args.camera_id
            else list(db.scalars(select(Camera).order_by(Camera.name)))
        )
        if not cameras or any(camera is None for camera in cameras):
            parser.error("Kamera nicht gefunden")
        print(
            json.dumps(
                [compare_camera(db, camera) for camera in cameras],
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
