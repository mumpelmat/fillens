from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


def uid() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    subject_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Container(Base):
    __tablename__ = "containers"
    __table_args__ = (
        CheckConstraint("capacity > 0"),
        CheckConstraint("capacity_unit IN ('liter', 'm3')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    capacity: Mapped[float] = mapped_column(Float)
    capacity_unit: Mapped[str] = mapped_column(String(16), default="liter")
    display_mode: Mapped[str] = mapped_column(String(16), default="fill")
    rate_time_unit: Mapped[str] = mapped_column(
        String(16), default="hour", server_default="hour"
    )
    smoothing_minutes: Mapped[int] = mapped_column(
        Integer, default=60, server_default="60"
    )
    event_threshold: Mapped[float] = mapped_column(
        Float, default=20, server_default="20"
    )
    rate_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    calculated_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    fill_notice: Mapped[float] = mapped_column(Float, default=80, server_default="80")
    fill_warning: Mapped[float] = mapped_column(Float, default=90, server_default="90")
    consumption_notice: Mapped[float] = mapped_column(
        Float, default=20, server_default="20"
    )
    consumption_warning: Mapped[float] = mapped_column(
        Float, default=10, server_default="10"
    )
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    email_recipients: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class FTPConnection(Base):
    __tablename__ = "ftp_connections"
    __table_args__ = (CheckConstraint("protocol IN ('ftp', 'ftps')"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=21)
    username: Mapped[str] = mapped_column(String(200))
    encrypted_password: Mapped[str] = mapped_column(Text)
    protocol: Mapped[str] = mapped_column(String(12), default="ftp")
    root_path: Mapped[str] = mapped_column(Text, default="/")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    container_id: Mapped[str] = mapped_column(ForeignKey("containers.id"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    ftp_connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("ftp_connections.id")
    )
    ftp_root: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    image_rotation: Mapped[int] = mapped_column(Integer, default=0)
    image_flip: Mapped[bool] = mapped_column(Boolean, default=False)
    roi_json: Mapped[dict | None] = mapped_column(JSON)
    recognition_requested: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    analysis_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    learning_dirty: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    learning_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    learning_generation: Mapped[str] = mapped_column(
        String(36), default=uid, server_default="legacy"
    )
    min_confidence: Mapped[float] = mapped_column(
        Float, default=0.75, server_default="0.75"
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    scan_mode: Mapped[str] = mapped_column(
        String(16), default="auto", server_default="auto"
    )
    date_pattern: Mapped[str] = mapped_column(
        String(500), default="YYYY/MM/TT", server_default="YYYY/MM/TT"
    )
    recent_days: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    full_scan_hours: Mapped[int] = mapped_column(
        Integer, default=24, server_default="24"
    )
    detected_date_pattern: Mapped[str | None] = mapped_column(String(500))
    last_date_path: Mapped[str | None] = mapped_column(Text)
    last_full_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_scan_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_scan_kind: Mapped[str | None] = mapped_column(String(24))
    last_scan_entries: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )


class ImportRecord(Base):
    """Dauerhaftes Metadatenjournal; enthält niemals Bildbytes."""

    __tablename__ = "import_records"
    __table_args__ = (
        UniqueConstraint("camera_id", "fingerprint"),
        Index("ix_import_camera_hash", "camera_id", "file_hash"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    remote_path: Mapped[str] = mapped_column(Text)
    remote_size: Mapped[int] = mapped_column(Integer)
    remote_modified: Mapped[str] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="imported")
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ImageBuffer(Base):
    __tablename__ = "image_buffer"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("import_records.id"), unique=True)
    filename: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timestamp_source: Mapped[str] = mapped_column(String(24))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    image_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    size_bytes: Mapped[int] = mapped_column(Integer)
    transform_key: Mapped[str | None] = mapped_column(String(64))


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        CheckConstraint("effective_fill_level >= 0 AND effective_fill_level <= 100"),
        UniqueConstraint("source_import_id"),
        Index("ix_measurement_container_time", "container_id", "measured_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    container_id: Mapped[str] = mapped_column(ForeignKey("containers.id"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    source_import_id: Mapped[str] = mapped_column(ForeignKey("import_records.id"))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_fill_level: Mapped[float | None] = mapped_column(Float)
    corrected_fill_level: Mapped[float | None] = mapped_column(Float)
    effective_fill_level: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    absolute_level: Mapped[float] = mapped_column(Float)
    capacity_snapshot: Mapped[float] = mapped_column(Float)
    capacity_unit: Mapped[str] = mapped_column(String(16))
    change_rate_percent: Mapped[float | None] = mapped_column(Float)
    change_rate_absolute: Mapped[float | None] = mapped_column(Float)
    operating_rate_percent: Mapped[float | None] = mapped_column(Float)
    operating_rate_absolute: Mapped[float | None] = mapped_column(Float)
    rate_revision: Mapped[int | None] = mapped_column(Integer)
    rate_cycle: Mapped[int | None] = mapped_column(Integer)
    cycle_break: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    analysis_method: Mapped[str] = mapped_column(String(40), default="manual_v1")
    model_version: Mapped[str | None] = mapped_column(String(64))
    image_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    timestamp_source: Mapped[str] = mapped_column(String(24))
    is_outlier: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class WorkerStatus(Base):
    __tablename__ = "worker_status"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="importer")
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(
        String(24), default="idle", server_default="idle"
    )
    active_camera_id: Mapped[str | None] = mapped_column(String(36))
    operation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    scanned_entries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    imported_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class TrainingSample(Base):
    __tablename__ = "training_samples"
    __table_args__ = (UniqueConstraint("camera_id", "file_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    filename: Mapped[str] = mapped_column(String(80))
    original_name: Mapped[str] = mapped_column(String(200))
    file_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    target_fill_level: Mapped[float | None] = mapped_column(Float)
    image_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    series_name: Mapped[str] = mapped_column(String(100))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(
        String(16), default="upload", server_default="upload"
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queue_state: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    window_managed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    transform_key: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(64))
    managed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    learning_generation: Mapped[str] = mapped_column(
        String(36), default="legacy", server_default="legacy"
    )
    algorithm: Mapped[str] = mapped_column(String(40), default="local_rf_v1")
    image_mode: Mapped[str] = mapped_column(String(16), default="all")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict] = mapped_column(JSON)
    dataset_json: Mapped[list] = mapped_column(JSON)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    artifact_hash: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    training_sample_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = (UniqueConstraint("source_import_id", "model_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    source_import_id: Mapped[str] = mapped_column(
        ForeignKey("import_records.id"), index=True
    )
    model_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timestamp_source: Mapped[str] = mapped_column(String(24), default="unknown")
    raw_fill_level: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    estimated_error: Mapped[float | None] = mapped_column(Float)
    image_mode: Mapped[str] = mapped_column(String(16), default="unknown")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_visible: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    review_sample_id: Mapped[str | None] = mapped_column(
        ForeignKey("training_samples.id", ondelete="SET NULL"), index=True
    )
    measurement_id: Mapped[str | None] = mapped_column(ForeignKey("measurements.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ContainerEvent(Base):
    __tablename__ = "container_events"
    __table_args__ = (Index("ix_event_container_time", "container_id", "occurred_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    container_id: Mapped[str] = mapped_column(ForeignKey("containers.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(500), default="")
    measurement_id: Mapped[str | None] = mapped_column(ForeignKey("measurements.id"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AlertState(Base):
    __tablename__ = "alert_states"
    container_id: Mapped[str] = mapped_column(
        ForeignKey("containers.id"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(16))
    severity: Mapped[int] = mapped_column(Integer, default=0)
    episode: Mapped[int] = mapped_column(Integer, default=0)
    emitted_level: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("container_id", "episode", "severity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    container_id: Mapped[str] = mapped_column(ForeignKey("containers.id"), index=True)
    episode: Mapped[int] = mapped_column(Integer)
    severity: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    container_name: Mapped[str] = mapped_column(String(120))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fill_level: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SMTPConfig(Base):
    __tablename__ = "smtp_config"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="main")
    host: Mapped[str] = mapped_column(String(253), default="")
    port: Mapped[int] = mapped_column(Integer, default=587)
    encryption: Mapped[str] = mapped_column(String(16), default="starttls")
    username: Mapped[str] = mapped_column(String(254), default="")
    encrypted_password: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str] = mapped_column(String(254), default="")


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (UniqueConstraint("notification_id", "recipient"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("notifications.id"), index=True
    )
    recipient: Mapped[str] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_error: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerControl(Base):
    __tablename__ = "worker_control"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    requested: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    claimed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    running: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainingCollection(Base):
    __tablename__ = "training_collection"
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="waiting")
    candidates_json: Mapped[list] = mapped_column(JSON, default=list)
    scan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_key: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)


class TrainingFetchRecord(Base):
    __tablename__ = "training_fetch_records"
    __table_args__ = (UniqueConstraint("camera_id", "fingerprint", "transform_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    transform_key: Mapped[str] = mapped_column(String(64))
    remote_path: Mapped[str | None] = mapped_column(Text)
    remote_size: Mapped[int | None] = mapped_column(Integer)
    remote_modified: Mapped[str | None] = mapped_column(String(64))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sample_id: Mapped[str | None] = mapped_column(
        ForeignKey("training_samples.id", ondelete="SET NULL"), index=True
    )
    state: Mapped[str] = mapped_column(
        String(16), default="candidate", server_default="candidate"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class FTPRejection(Base):
    __tablename__ = "ftp_rejections"
    __table_args__ = (UniqueConstraint("camera_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    remote_path: Mapped[str] = mapped_column(Text)
    remote_size: Mapped[int] = mapped_column(Integer)
    remote_modified: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LearningRestorePoint(Base):
    __tablename__ = "learning_restore_points"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), index=True)
    reason: Mapped[str] = mapped_column(String(24), default="manual")
    config_json: Mapped[dict] = mapped_column(JSON)
    active_models_json: Mapped[list] = mapped_column(JSON, default=list)
    artifact_hashes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LearningRestoreSample(Base):
    __tablename__ = "learning_restore_samples"
    restore_point_id: Mapped[str] = mapped_column(
        ForeignKey("learning_restore_points.id", ondelete="CASCADE"), primary_key=True
    )
    sample_id: Mapped[str] = mapped_column(
        ForeignKey("training_samples.id"), primary_key=True, index=True
    )
    file_hash: Mapped[str] = mapped_column(String(64))
    target_fill_level: Mapped[float] = mapped_column(Float)
    image_mode: Mapped[str] = mapped_column(String(16))
    series_name: Mapped[str] = mapped_column(String(100))
    transform_key: Mapped[str] = mapped_column(String(64))
    window_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
