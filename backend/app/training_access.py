"""Freigegebene Bildprüfung; technische Verwaltung bleibt administrativ."""

from fastapi import HTTPException

from .models import Camera


def training_camera(db, camera_id, user, *, lock=False):
    camera = db.get(Camera, camera_id, with_for_update=lock, populate_existing=True)
    if not camera:
        raise HTTPException(404, "Kamera nicht gefunden.")
    if user.role != "admin" and not camera.learning_enabled:
        raise HTTPException(403, "Lernen ist für diesen Behälter ausgeschaltet.")
    return camera
