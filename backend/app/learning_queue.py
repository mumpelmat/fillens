"""Gemeinsame Bildwarteschlange ohne doppelte Modellvorschläge je Referenz."""

from sqlalchemy import exists, func, literal, or_, select, union_all

from .db import aware
from .models import AnalysisResult, ImageBuffer, Measurement, TrainingSample


def queue_query(camera_id, tab):
    pending = exists(
        select(AnalysisResult.id).where(
            AnalysisResult.review_sample_id == TrainingSample.id,
            AnalysisResult.review_visible.is_(True),
            AnalysisResult.status == "pending",
        )
    )
    needs = or_(
        TrainingSample.approved.is_(False),
        TrainingSample.target_fill_level.is_(None),
        pending,
    )
    sample = select(
        TrainingSample.id.label("id"),
        literal("sample").label("kind"),
        func.coalesce(
            TrainingSample.captured_at, TrainingSample.created_at
        ).label("created_at"),
    ).where(
        TrainingSample.camera_id == camera_id,
        TrainingSample.discarded_at.is_(None),
        TrainingSample.queue_state == ("pending" if tab == "pending" else "confirmed"),
        needs if tab == "pending" else ~needs,
    )
    legacy = select(
        AnalysisResult.id.label("id"),
        literal("analysis").label("kind"),
        AnalysisResult.created_at.label("created_at"),
    ).where(
        AnalysisResult.camera_id == camera_id,
        AnalysisResult.review_sample_id.is_(None),
        AnalysisResult.review_visible.is_(True),
        AnalysisResult.status.in_(
            ["pending", "accepted"] if tab == "pending" else ["reviewed"]
        ),
    )
    return union_all(sample, legacy).subquery()


def queue_data(db, camera_id, tab, offset, limit, excluded):
    from .ml_service import sample_path
    from .services import image_path

    query = queue_query(camera_id, tab)
    filters = []
    for kind in ("sample", "analysis"):
        ids = [key[len(kind) + 1 :] for key in excluded if key.startswith(kind + "_")]
        if ids:
            filters.append(or_(query.c.kind != kind, query.c.id.not_in(ids)))
    total = db.scalar(select(func.count()).select_from(query)) or 0
    pending_count = (
        db.scalar(select(func.count()).select_from(queue_query(camera_id, "pending")))
        or 0
    )
    confirmed_count = (
        db.scalar(select(func.count()).select_from(queue_query(camera_id, "confirmed")))
        or 0
    )
    remaining = db.scalar(select(func.count()).select_from(query).where(*filters)) or 0
    keys = list(
        db.execute(
            select(query)
            .where(*filters)
            .order_by(query.c.created_at.desc(), query.c.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    items = []
    for key in keys:
        sample = db.get(TrainingSample, key.id) if key.kind == "sample" else None
        results = (
            list(
                db.scalars(
                    select(AnalysisResult)
                    .where(
                        AnalysisResult.review_sample_id == sample.id,
                        AnalysisResult.review_visible.is_(True),
                    )
                    .order_by(
                        AnalysisResult.created_at.desc(), AnalysisResult.id.desc()
                    )
                )
            )
            if sample
            else [db.get(AnalysisResult, key.id)]
        )
        result = next(
            (r for r in results if r.status == "pending"),
            results[0] if results else None,
        )
        image = (
            db.scalar(
                select(ImageBuffer).where(
                    ImageBuffer.import_id == result.source_import_id
                )
            )
            if result
            else None
        )
        available = (
            sample_path(sample).is_file()
            if sample
            else bool(image and image_path(image).is_file())
        )
        measurement = (
            db.get(Measurement, result.measurement_id)
            if result and result.measurement_id
            else None
        )
        items.append(
            {
                "id": key.kind + "_" + key.id,
                "sample_id": sample.id if sample else None,
                "content_url": f"/training/samples/{sample.id}/content"
                if sample
                else f"/training/analyses/{key.id}/content",
                "has_reference": available,
                "source": sample.origin if sample else "camera",
                "name": sample.original_name if sample else "Früheres Prüfbild",
                "created_at": aware(key.created_at).isoformat(),
                "measured_at": aware(result.measured_at).isoformat()
                if result
                else aware(sample.captured_at).isoformat() if sample and sample.captured_at else None,
                "fill_level": sample.target_fill_level
                if sample
                else measurement.effective_fill_level
                if measurement
                else None,
                "suggested_fill_level": result.raw_fill_level if result else None,
                "estimated_error": result.estimated_error if result else None,
                "image_mode": sample.image_mode if sample else result.image_mode,
                "series_name": sample.series_name
                if sample
                else "Frühere Kameraaufnahmen",
                "approved": bool(sample and sample.approved),
                "transform_key": sample.transform_key
                if sample
                else image.transform_key
                if image
                else None,
            }
        )
    return {
        "items": items,
        "total_count": total,
        "remaining_count": remaining,
        "pending_count": pending_count,
        "confirmed_count": confirmed_count,
    }
