"""Lokale, versionierte ROI-Features und austauschbare Füllstandsanalyse."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from PIL import Image

FEATURE_VERSION = "roi_features_v1"
POLYGON_FEATURE_VERSION = "roi_polygon_v2"
GEOMETRY_FEATURE_VERSION = "roi_geometry_v3"


def transform_key(rotation: int, flip: bool) -> str:
    return hashlib.sha256(f"{rotation}:{flip}".encode()).hexdigest()


def camera_config(camera, feature_version: str = GEOMETRY_FEATURE_VERSION) -> dict:
    return {
        "roi": camera.roi_json,
        "rotation": camera.image_rotation,
        "flip": camera.image_flip,
        "feature_version": feature_version,
    }


def config_key(config: dict) -> str:
    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, (int, float)):
            return round(float(value), 8)
        return value

    normalized = {**config, "roi": normalize(config.get("roi"))}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


def physical_config_key(config: dict) -> str:
    return config_key(
        {
            "roi": config.get("roi"),
            "rotation": config.get("rotation", 0),
            "flip": config.get("flip", False),
        }
    )


def image_features(
    path: Path, roi: dict, feature_version: str | None = None
) -> tuple[np.ndarray, str, dict]:
    with Image.open(path) as source:
        rgb = np.asarray(source.convert("RGB"))
    if feature_version == GEOMETRY_FEATURE_VERSION:
        return geometry_features(rgb, roi)
    height, width = rgb.shape[:2]
    if "points" in roi:
        return polygon_features(rgb, roi)
    x1, y1 = int(roi["x"] * width), int(roi["y"] * height)
    x2, y2 = (
        min(width, max(x1 + 2, int((roi["x"] + roi["width"]) * width))),
        min(height, max(y1 + 2, int((roi["y"] + roi["height"]) * height))),
    )
    crop = rgb[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("Der Bildbereich enthält keine Pixel.")
    crop = cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mode = (
        "night_ir" if float(np.mean(np.ptp(crop.astype(float), axis=2))) < 8 else "day"
    )
    small = (
        cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(float).ravel()
        / 255
    )
    histograms = [
        cv2.calcHist(
            [hsv], [channel], None, [16], [0, 180 if channel == 0 else 256]
        ).ravel()
        / gray.size
        for channel in range(3)
    ]
    edges = cv2.Canny(gray, 50, 150).astype(float) / 255
    texture = cv2.resize(edges, (4, 4), interpolation=cv2.INTER_AREA).ravel()
    stats = np.array(
        [
            gray.mean() / 255,
            gray.std() / 255,
            np.mean(gray < 10),
            np.mean(gray > 245),
            min(cv2.Laplacian(gray, cv2.CV_64F).var() / 2000, 1),
        ]
    )
    features = np.concatenate([small, *histograms, texture, stats]).astype(float)
    return (
        features,
        mode,
        {
            "too_dark": bool(np.mean(gray < 10) > 0.95),
            "overexposed": bool(np.mean(gray > 245) > 0.95),
        },
    )


def _pixel_points(rgb, roi):
    height, width = rgb.shape[:2]
    if "points" in roi:
        return np.array(
            [
                [
                    min(width - 1, max(0, round(point["x"] * (width - 1)))),
                    min(height - 1, max(0, round(point["y"] * (height - 1)))),
                ]
                for point in roi["points"]
            ],
            dtype=np.float32,
        )
    return np.array(
        [
            [roi["x"] * width, roi["y"] * height],
            [(roi["x"] + roi["width"]) * width - 1, roi["y"] * height],
            [
                (roi["x"] + roi["width"]) * width - 1,
                (roi["y"] + roi["height"]) * height - 1,
            ],
            [roi["x"] * width, (roi["y"] + roi["height"]) * height - 1],
        ],
        dtype=np.float32,
    )


def _geometry_image(rgb, roi, size=128):
    """Normalisiert das Polygon in Behälterbreite und -höhe."""
    points = _pixel_points(rgb, roi)
    height, width = rgb.shape[:2]
    integer = np.rint(points).astype(np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [integer], 255)
    if np.count_nonzero(mask) < 16:
        raise ValueError("Vieleck enthält zu wenige Bildpixel")
    if len(points) == 4 and cv2.isContourConvex(integer):
        ordered_y = points[np.argsort(points[:, 1])]
        top = ordered_y[:2][np.argsort(ordered_y[:2, 0])]
        bottom = ordered_y[2:][np.argsort(ordered_y[2:, 0])]
        source = np.array([top[0], top[1], bottom[1], bottom[0]], np.float32)
        target = np.array(
            [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]],
            np.float32,
        )
        matrix = cv2.getPerspectiveTransform(source, target)
        normalized = cv2.warpPerspective(
            rgb, matrix, (size, size), flags=cv2.INTER_AREA
        )
        valid = cv2.warpPerspective(
            mask, matrix, (size, size), flags=cv2.INTER_NEAREST
        )
        return normalized, valid
    x, y, w, h = cv2.boundingRect(integer)
    cropped, cropped_mask = rgb[y : y + h, x : x + w], mask[y : y + h, x : x + w]
    output = np.zeros((size, size, 3), dtype=np.uint8)
    output_mask = np.zeros((size, size), dtype=np.uint8)
    available = [column for column in range(w) if np.any(cropped_mask[:, column])]
    if not available:
        raise ValueError("Vieleck enthält keine auswertbaren Spalten")
    for column in range(size):
        source_x = int(round(column * (w - 1) / max(size - 1, 1)))
        source_x = min(available, key=lambda value: abs(value - source_x))
        vertical = np.flatnonzero(cropped_mask[:, source_x])
        top, bottom = int(vertical[0]), int(vertical[-1])
        source_y = np.linspace(top, bottom, size).astype(np.float32)
        source_xs = np.full(size, source_x, dtype=np.float32)
        output[:, column] = cv2.remap(
            cropped,
            source_xs.reshape(-1, 1),
            source_y.reshape(-1, 1),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )[:, 0]
        output_mask[:, column] = 255
    return output, output_mask


def geometry_features(rgb, roi):
    normalized, mask = _geometry_image(rgb, roi)
    gray = cv2.cvtColor(normalized, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(normalized, cv2.COLOR_RGB2HSV)
    valid = mask > 0
    pixels = gray[valid]
    chroma = np.ptp(normalized[valid].astype(float), axis=1)
    mode = "night_ir" if float(np.mean(chroma)) < 8 else "day"
    edges = cv2.Canny(gray, 40, 120).astype(float) / 255
    laplace = np.abs(cv2.Laplacian(gray, cv2.CV_64F))
    bands = []
    for start, end in zip(np.linspace(0, 128, 25)[:-1], np.linspace(0, 128, 25)[1:]):
        first, last = int(round(start)), max(int(round(end)), int(round(start)) + 1)
        band_gray = gray[first:last]
        band_hsv = hsv[first:last]
        band_edges = edges[first:last]
        band_laplace = laplace[first:last]
        bands.extend(
            [
                band_gray.mean() / 255,
                band_gray.std() / 255,
                band_hsv[:, :, 1].mean() / 255,
                band_edges.mean(),
                np.mean(band_gray < 28),
                min(band_laplace.mean() / 80, 1),
            ]
        )
    coarse = (
        cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(float).ravel()
        / 255
    )
    texture = cv2.resize(edges, (6, 6), interpolation=cv2.INTER_AREA).ravel()
    vertical_change = np.abs(np.diff(gray.astype(float), axis=0)).mean(axis=1) / 255
    change_profile = cv2.resize(
        vertical_change.reshape(-1, 1), (1, 24), interpolation=cv2.INTER_AREA
    ).ravel()
    stats = np.array(
        [
            pixels.mean() / 255,
            pixels.std() / 255,
            np.mean(pixels < 10),
            np.mean(pixels > 245),
            min(cv2.Laplacian(gray, cv2.CV_64F).var() / 2000, 1),
        ]
    )
    return (
        np.concatenate([np.asarray(bands), coarse, texture, change_profile, stats]),
        mode,
        {
            "too_dark": bool(np.mean(pixels < 10) > 0.95),
            "overexposed": bool(np.mean(pixels > 245) > 0.95),
        },
    )


def polygon_features(rgb, roi):
    height, width = rgb.shape[:2]
    points = np.array(
        [
            [
                min(width - 1, round(p["x"] * (width - 1))),
                min(height - 1, round(p["y"] * (height - 1))),
            ]
            for p in roi["points"]
        ],
        dtype=np.int32,
    )
    x, y, w, h = cv2.boundingRect(points)
    crop = rgb[y : y + h, x : x + w].copy()
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [points - [x, y]], 255)
    if np.count_nonzero(mask) < 4:
        raise ValueError("Vieleck enthält zu wenige Bildpixel")
    # Vor dem Skalieren ausmaskieren: Außenbereiche dürfen weder die Merkmale
    # noch die Tag-/Nachterkennung oder Qualitätsbewertung beeinflussen.
    crop[mask == 0] = np.mean(crop[mask > 0], axis=0).round().astype(np.uint8)
    crop = cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA)
    mask = cv2.resize(mask, (128, 128), interpolation=cv2.INTER_NEAREST)
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    valid = mask > 0
    pixels = gray[valid]
    mode = (
        "night_ir"
        if float(np.mean(np.ptp(crop[valid].astype(float), axis=1))) < 8
        else "day"
    )
    coverage = cv2.resize(valid.astype(float), (8, 8), interpolation=cv2.INTER_AREA)
    small = cv2.resize(gray.astype(float) * valid, (8, 8), interpolation=cv2.INTER_AREA)
    small = (
        np.divide(
            small, coverage, out=np.full((8, 8), pixels.mean()), where=coverage > 0
        ).ravel()
        / 255
    )
    histograms = [
        cv2.calcHist(
            [hsv], [channel], mask, [16], [0, 180 if channel == 0 else 256]
        ).ravel()
        / len(pixels)
        for channel in range(3)
    ]
    edges = cv2.Canny(gray, 50, 150).astype(float) / 255
    edges[cv2.erode(mask, np.ones((3, 3), np.uint8)) == 0] = 0
    texture = cv2.resize(edges, (4, 4), interpolation=cv2.INTER_AREA).ravel()
    stats = np.array(
        [
            pixels.mean() / 255,
            pixels.std() / 255,
            np.mean(pixels < 10),
            np.mean(pixels > 245),
            min(cv2.Laplacian(gray, cv2.CV_64F)[valid].var() / 2000, 1),
        ]
    )
    return (
        np.concatenate([small, *histograms, texture, stats]).astype(float),
        mode,
        {
            "too_dark": bool(np.mean(pixels < 10) > 0.95),
            "overexposed": bool(np.mean(pixels > 245) > 0.95),
        },
    )


@dataclass
class Analysis:
    fill_level_percent: float | None
    confidence: float
    image_mode: str
    reason: str | None
    analysis_method: str = "local_rf_v1"
    estimated_error: float | None = None
    unusable: bool = False


class FillLevelAnalyzer(Protocol):
    def analyze(self, path: Path) -> Analysis: ...


class LocalForestAnalyzer:
    """Liest ausschließlich eigenen JSON-Baumexport; kein Pickle-/Modellupload."""

    def __init__(self, artifact: dict):
        if artifact.get("schema") not in (1, 2) or artifact.get(
            "feature_version"
        ) not in (FEATURE_VERSION, POLYGON_FEATURE_VERSION, GEOMETRY_FEATURE_VERSION):
            raise ValueError("Modellformat oder Feature-Version passt nicht.")
        self.artifact = artifact

    def analyze(self, path: Path) -> Analysis:
        a = self.artifact
        if a["schema"] == 2:
            return self._analyze_v2(path)
        features, mode, quality = image_features(
            path, a["config"]["roi"], a["feature_version"]
        )
        predictions = self._predict_trees(features, a["trees"])
        estimate = float(np.clip(np.mean(predictions), 0, 100))
        spread = float(np.std(predictions))
        anchors = np.asarray(a["anchors"])
        scale = np.maximum(np.asarray(a["scale"]), 0.03)
        distance = float(
            np.min(np.sqrt(np.mean(((anchors - features) / scale) ** 2, axis=1)))
        )
        mode_quality = a["metrics"].get("mode_quality", {}).get(mode, {})
        mae = (
            mode_quality.get("mae_percent_points")
            if a.get("managed_learning")
            else a["metrics"].get("mae_percent_points")
        )
        confidence = float(
            np.exp(-spread / 30)
            * np.exp(-max(distance - 0.5, 0) / 2)
            * (np.exp(-mae / 25) if mae is not None else 0.45)
        )
        reasons = []
        if a.get("managed_learning") and not mode_quality.get("ready"):
            confidence = min(confidence, 0.45)
            reasons.append("Für diesen Bildmodus noch weitere Bilder bestätigen.")
        if mode not in a.get("image_modes", []):
            confidence = min(confidence, 0.45)
            reasons.append("Für diesen Bildmodus fehlen passende Trainingsbeispiele.")
        if mae is None:
            reasons.append("Unabhängige Validierung nach Aufnahmeserien fehlt.")
        if distance > 2:
            reasons.append("Bildmerkmale weichen deutlich von den Trainingsbildern ab.")
        if quality["too_dark"] or quality["overexposed"]:
            confidence = min(confidence, 0.3)
            reasons.append("Bild nahezu vollständig dunkel oder überbelichtet.")
        return Analysis(estimate, round(confidence, 4), mode, " ".join(reasons) or None)

    @staticmethod
    def _predict_trees(features, trees):
        predictions = []
        for tree in trees:
            node = 0
            for _ in range(len(tree["feature"])):
                left, right = tree["left"][node], tree["right"][node]
                if left == right:
                    predictions.append(tree["value"][node])
                    break
                node = (
                    left
                    if features[tree["feature"][node]] <= tree["threshold"][node]
                    else right
                )
            else:
                raise ValueError("Ungültiger Modellbaum")
        return np.asarray(predictions)

    def _analyze_v2(self, path):
        a = self.artifact
        features, mode, quality = image_features(
            path, a["config"]["roi"], GEOMETRY_FEATURE_VERSION
        )
        rejection = a.get("unusable_heads", {}).get(mode)
        if rejection and rejection.get("ready"):
            from .unusable_learning import FULL_FRAME

            full, _, _ = image_features(path, FULL_FRAME, GEOMETRY_FEATURE_VERSION)
            score = float(np.mean(self._predict_trees(full, rejection["trees"])))
            distance = float(np.min(np.sqrt(np.mean(
                ((np.asarray(rejection["anchors"]) - full) /
                 np.maximum(np.asarray(rejection["scale"]), 0.03)) ** 2, axis=1))))
            if score >= 0.5:
                rejected = score >= rejection["threshold"] and distance <= 2
                return Analysis(
                    None, 0, mode,
                    "Als ungeeignet erkannt (z. B. Deckel zu oder Sicht verdeckt). Kein Messwert übernommen."
                    if rejected else "Bild möglicherweise ungeeignet. Bitte manuell prüfen; kein Messwert übernommen.",
                    "local_geometry_v2", None, rejected,
                )
        head = a.get("heads", {}).get(mode) or a.get("fallback")
        if not head:
            return Analysis(
                50,
                0,
                mode,
                "Für diesen Bildmodus fehlen passende Trainingsbeispiele.",
                "local_geometry_v2",
                None,
            )
        predictions = self._predict_trees(features, head["trees"])
        estimate = float(np.clip(np.mean(predictions), 0, 100))
        spread = float(np.std(predictions))
        anchors = np.asarray(head["anchors"])
        scale = np.maximum(np.asarray(head["scale"]), 0.03)
        distance = float(
            np.min(np.sqrt(np.mean(((anchors - features) / scale) ** 2, axis=1)))
        )
        calibration = head.get("calibration", {})
        spread_limit = max(float(calibration.get("spread_p90", 1)), 0.1)
        distance_limit = max(float(calibration.get("distance_p90", 1)), 0.1)
        spread_ratio = spread / spread_limit
        distance_ratio = distance / distance_limit
        confidence = float(calibration.get("reliability", 0.2))
        confidence *= float(np.exp(-max(spread_ratio - 1, 0)))
        confidence *= float(np.exp(-max(distance_ratio - 1, 0)))
        estimated_error = float(calibration.get("error_p80", 25)) * max(
            1, spread_ratio, distance_ratio
        )
        reasons = []
        if not head.get("ready") or mode not in a.get("heads", {}):
            confidence = min(confidence, 0.45)
            reasons.append("Bildmodus ist noch nicht ausreichend gelernt.")
        if distance_ratio > 1:
            reasons.append("Bild liegt außerhalb des bekannten Trainingsbereichs.")
        if spread_ratio > 1:
            reasons.append("Modellstreuung ist für dieses Bild erhöht.")
        if estimated_error > 10:
            reasons.append("Erwartete Abweichung ist größer als zehn Prozentpunkte.")
        if quality["too_dark"] or quality["overexposed"]:
            confidence = min(confidence, 0.3)
            reasons.append("Bild nahezu vollständig dunkel oder überbelichtet.")
        return Analysis(
            estimate,
            round(float(np.clip(confidence, 0, 1)), 4),
            mode,
            " ".join(reasons) or None,
            "local_geometry_v2",
            round(min(estimated_error, 100), 2),
        )
