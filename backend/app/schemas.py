from typing import Literal
from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(Login):
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "user"] = "user"


class RoleSet(Input):
    role: Literal["admin", "user"]


class PasswordSet(Input):
    password: str = Field(min_length=12, max_length=256)


class SiteInput(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    timezone: str = "Europe/Berlin"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unbekannte Zeitzone") from exc
        return value


class ContainerInput(Input):
    site_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    capacity: float = Field(gt=0, le=1e9)
    capacity_unit: Literal["liter", "m3"] = "liter"
    display_mode: Literal["fill", "consumption"] = "fill"
    active: bool = True
    rate_time_unit: Literal["minute", "hour", "day"] = "hour"
    smoothing_minutes: int = Field(default=60, ge=1, le=10080)
    event_threshold: float = Field(default=20, gt=0, le=100)


class AlertRulesInput(Input):
    fill_notice: float = Field(default=80, ge=0, le=100)
    fill_warning: float = Field(default=90, ge=0, le=100)
    consumption_notice: float = Field(default=20, ge=0, le=100)
    consumption_warning: float = Field(default=10, ge=0, le=100)
    email_enabled: bool = False
    email_recipients: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def thresholds(self):
        if (
            self.fill_notice >= self.fill_warning
            or self.consumption_warning >= self.consumption_notice
        ):
            raise ValueError(
                "Hinweis und Warnung müssen in der Reihenfolge des Modus liegen"
            )
        self.email_recipients = list(
            dict.fromkeys(valid_email(v) for v in self.email_recipients)
        )
        if self.email_enabled and not self.email_recipients:
            raise ValueError("Mindestens einen Empfänger angeben")
        return self


def valid_email(value: str) -> str:
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(
        r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}",
        value,
    ):
        raise ValueError("Ungültige E-Mail-Adresse")
    return value


class EventInput(Input):
    occurred_at: datetime
    kind: Literal["emptying", "refill"]
    note: str = Field(default="", max_length=500)

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Zeitpunkt benötigt eine Zeitzone")
        return value


class SMTPInput(Input):
    host: str = Field(default="", max_length=253, pattern=r"^[a-zA-Z0-9.:_-]*$")
    port: int = Field(default=587, ge=1, le=65535)
    encryption: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = Field(default="", max_length=254)
    password: str | None = Field(default=None, max_length=1000)
    sender: str = Field(default="", max_length=254)
    clear_password: bool = False

    @field_validator("sender")
    @classmethod
    def sender_address(cls, value):
        return valid_email(value) if value else ""

    @field_validator("username", "password")
    @classmethod
    def no_controls(cls, value):
        if value and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Steuerzeichen sind nicht erlaubt")
        return value


class FTPInput(Input):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.:_-]+$")
    port: int = Field(default=21, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=200)
    password: str | None = Field(default=None, max_length=1000)
    protocol: Literal["ftp", "ftps"] = "ftp"
    root_path: str = Field(default="/", max_length=2000)

    @field_validator("username", "password", "root_path")
    @classmethod
    def no_controls(cls, value: str | None) -> str | None:
        if value and any(ord(char) < 32 for char in value):
            raise ValueError("Steuerzeichen sind nicht erlaubt")
        return value

    @field_validator("root_path")
    @classmethod
    def safe_root(cls, value: str) -> str:
        if ".." in value.split("/"):
            raise ValueError("Elternverzeichnisse sind nicht erlaubt")
        return value


class CameraInput(SiteInput):
    ftp_connection_id: str | None = None
    ftp_root: str = Field(default="", max_length=2000)
    image_rotation: Literal[0, 90, 180, 270] = 0
    image_flip: bool = False
    active: bool = True
    scan_mode: Literal["auto", "date", "recursive"] = "auto"
    date_pattern: str = Field(default="YYYY/MM/TT", max_length=500)
    recent_days: int = Field(default=3, ge=1, le=31)
    full_scan_hours: int = Field(default=24, ge=1, le=720)

    @field_validator("date_pattern")
    @classmethod
    def pattern(cls, value: str) -> str:
        from .ftp_scan import validate_pattern

        return validate_pattern(value)

    @field_validator("ftp_root")
    @classmethod
    def root(cls, value: str) -> str:
        if (
            value.startswith("/")
            or ".." in value.split("/")
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Kamera-Pfad muss relativ zum FTP-Root sein")
        return value


class FTPTestInput(FTPInput):
    connection_id: str | None = Field(default=None, max_length=36)
    ftp_root: str = Field(default="", max_length=2000)

    @field_validator("ftp_root")
    @classmethod
    def root(cls, value: str) -> str:
        return CameraInput.root(value)


class ManualInput(Input):
    image_id: str
    fill_level: float = Field(ge=0, le=100)
    image_mode: Literal["day", "night_ir", "unknown"] = "unknown"


class LevelCorrection(Input):
    image_id: str = Field(min_length=1, max_length=50)
    fill_level: float = Field(ge=0, le=100)
