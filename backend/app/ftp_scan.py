"""Begrenzte FTP-Auflistung und wiederverwendbare Datumsordner-Erkennung."""

import hashlib
import posixpath
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from ftplib import FTP, error_perm
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .config import settings
from .ftp_errors import ImportFailure, import_failure

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    modified: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            f"{self.path}\0{self.size}\0{self.modified}".encode()
        ).hexdigest()


@dataclass
class ScanBudget:
    entries: int = 0
    on_progress: Callable[[int], None] | None = None

    def consume(self, count: int) -> None:
        self.entries += count
        if self.on_progress:
            self.on_progress(self.entries)
        if self.entries > settings().max_scan_files:
            limit = f"{settings().max_scan_files:,}".replace(",", ".")
            raise ImportFailure(
                "scan_limit",
                f"FTP-Scanlimit von {limit} Einträgen überschritten. Datumsmodus verwenden, Root eingrenzen oder MAX_SCAN_FILES erhöhen.",
            )


def remote_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.split(".")[0], "%Y%m%d%H%M%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def validate_pattern(value: str) -> str:
    parts = value.strip().split("/")
    parts = ["TT" if p == "DD" else p for p in parts]
    if any(not p or p in {".", ".."} or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError("Datumsmuster muss ein sicherer relativer Pfad sein")
    tokens = [part for part in parts if part in {"YYYY", "MM", "TT"}]
    if tokens not in (["YYYY", "MM", "TT"], ["MM", "TT"], ["TT"]):
        raise ValueError(
            "Datumsmuster benötigt YYYY/MM/TT, MM/TT oder TT (DD ebenfalls erlaubt)"
        )
    index = parts.index(tokens[0])
    if parts[index : index + len(tokens)] != tokens:
        raise ValueError("Datumsbestandteile müssen direkt aufeinanderfolgen")
    return "/".join(parts)


def stat(ftp: FTP, path: str) -> RemoteFile:
    try:
        ftp.voidcmd("TYPE I")
        size = ftp.size(path)
        if size is None:
            raise ImportFailure(
                "size_unavailable",
                "Der FTP-Server liefert keine Dateigröße. SIZE-Unterstützung und Leserechte prüfen.",
            )
        try:
            modified = ftp.sendcmd("MDTM " + path)[4:].strip()
        except error_perm as exc:
            if str(exc)[:3] not in {"500", "501", "502", "504"}:
                raise
            modified = ""
        return RemoteFile(path, size, modified)
    except Exception as exc:
        raise import_failure(exc, "download") from exc


def listing(
    ftp: FTP, directory: str, budget: ScanBudget, operation: str = "list"
) -> list[tuple[str, dict[str, str]]]:
    try:
        ftp.cwd(directory)
        try:
            result = []

            def entry(line):
                budget.consume(1)
                facts, _, name = line.partition(" ")
                parsed = dict(
                    item.split("=", 1) for item in facts.split(";") if "=" in item
                )
                result.append(
                    (name, {key.lower(): value for key, value in parsed.items()})
                )

            ftp.retrlines("MLSD", entry)
        except error_perm as exc:
            if str(exc)[:3] not in {"500", "501", "502", "504"}:
                raise
            result = []

            def legacy_entry(name):
                budget.consume(1)
                result.append((posixpath.basename(name.rstrip("/")), {}))

            ftp.retrlines("NLST", legacy_entry)
        safe = []
        for name, facts in result:
            if name in {".", ".."} or "/" in name or any(ord(c) < 32 for c in name):
                continue
            kind = facts.get("type", "")
            if kind in {"cdir", "pdir"} or (kind and kind not in {"file", "dir"}):
                continue
            if not kind:
                try:
                    ftp.cwd(posixpath.join(directory, name))
                except error_perm:
                    kind = "file"
                else:
                    kind = "dir"
                finally:
                    ftp.cwd(directory)
                facts = {**facts, "type": kind}
            safe.append((name, facts))
        return safe
    except Exception as exc:
        raise import_failure(exc, operation) from exc


def scan(ftp: FTP, root: str, budget: ScanBudget | None = None) -> Iterator[RemoteFile]:
    budget = budget or ScanBudget()
    stack = [(posixpath.normpath("/" + root.lstrip("/")), 0)]
    visited: set[str] = set()
    while stack:
        directory, depth = stack.pop()
        if directory in visited:
            continue
        if depth > 32:
            raise ImportFailure(
                "depth_limit",
                "FTP-Verzeichnistiefe von 32 Ebenen überschritten. Root eingrenzen und Verzeichnisschleifen prüfen.",
            )
        visited.add(directory)
        for name, facts in listing(
            ftp, directory, budget, "root" if depth == 0 else "list"
        ):
            path = posixpath.join(directory, name)
            if facts["type"] == "dir":
                stack.append((path, depth + 1))
            elif PurePosixPath(name).suffix.lower() in EXTENSIONS:
                try:
                    size = int(facts.get("size", "-1"))
                except ValueError as exc:
                    raise ImportFailure(
                        "invalid_metadata",
                        "Der FTP-Server meldet eine ungültige Dateigröße.",
                    ) from exc
                yield (
                    RemoteFile(path, size, facts.get("modify", ""))
                    if size >= 0
                    else stat(ftp, path)
                )


def _date_directory(ftp: FTP, base: str, budget: ScanBudget) -> str | None:
    years = listing(ftp, base, budget, "root")
    for year, facts in sorted(years, reverse=True):
        if (
            facts["type"] != "dir"
            or len(year) != 4
            or not year.isdigit()
            or not 1970 <= int(year) <= 2200
        ):
            continue
        year_path = posixpath.join(base, year)
        for month, mf in sorted(listing(ftp, year_path, budget), reverse=True):
            if (
                mf["type"] != "dir"
                or len(month) != 2
                or not month.isdigit()
                or not 1 <= int(month) <= 12
            ):
                continue
            for day, df in sorted(
                listing(ftp, posixpath.join(year_path, month), budget), reverse=True
            ):
                if df["type"] != "dir" or len(day) != 2 or not day.isdigit():
                    continue
                try:
                    date(int(year), int(month), int(day))
                except ValueError:
                    continue
                return posixpath.join(year, month, day)
    return None


def detect_layout(ftp: FTP, root: str, budget: ScanBudget) -> tuple[str, str] | None:
    """Bis drei feste Präfixebenen; Bildordner selbst müssen dafür nicht gelesen werden."""
    year, month = root_context(root)
    if year:
        found = partial_date_directory(ftp, root, budget, year, month)
        if found:
            return ("TT" if month else "MM/TT", found)
    queue = deque([("", 0)])
    candidates: list[tuple[str, str]] = []
    visited = 0
    while queue:
        prefix, depth = queue.popleft()
        visited += 1
        if visited > 128:
            raise ImportFailure(
                "discovery_limit",
                "Zu viele Verzeichnisse für die automatische Strukturerkennung. Kamera-Unterverzeichnis eingrenzen oder Datumsmuster ausdrücklich einstellen.",
            )
        base = posixpath.join(root, prefix)
        found = _date_directory(ftp, base, budget)
        if found:
            candidates.append(
                (posixpath.join(prefix, "YYYY/MM/TT"), posixpath.join(prefix, found))
            )
            if len(candidates) > 1:
                return None  # Keine stillschweigende Wahl zwischen verschiedenen Kamerabäumen.
            continue
        if depth < 3:
            for name, facts in listing(ftp, base, budget, "root"):
                if facts["type"] == "dir" and not (len(name) == 4 and name.isdigit()):
                    queue.append((posixpath.join(prefix, name), depth + 1))
    return candidates[0] if candidates else None


def root_context(root: str) -> tuple[int | None, int | None]:
    parts = root.rstrip("/").split("/")

    def is_year(value: str) -> bool:
        return len(value) == 4 and value.isdigit() and 1970 <= int(value) <= 2200

    if is_year(parts[-1]):
        return int(parts[-1]), None
    if (
        len(parts) >= 2
        and is_year(parts[-2])
        and len(parts[-1]) == 2
        and parts[-1].isdigit()
        and 1 <= int(parts[-1]) <= 12
    ):
        return int(parts[-2]), int(parts[-1])
    return None, None


def partial_date_directory(
    ftp: FTP, base: str, budget: ScanBudget, year: int, month: int | None
) -> str | None:
    months = (
        [("", {"type": "dir"})]
        if month
        else sorted(listing(ftp, base, budget, "root"), reverse=True)
    )
    for name, facts in months:
        if not month and (
            facts["type"] != "dir"
            or len(name) != 2
            or not name.isdigit()
            or not 1 <= int(name) <= 12
        ):
            continue
        for day, df in sorted(
            listing(ftp, posixpath.join(base, name), budget), reverse=True
        ):
            if df["type"] != "dir" or len(day) != 2 or not day.isdigit():
                continue
            try:
                date(year, month or int(name), int(day))
            except ValueError:
                continue
            return posixpath.join(name, day)
    return None


def recent_paths(
    pattern: str, timezone: str, days: int, now: datetime, root: str | None = None
) -> list[str]:
    today = now.astimezone(ZoneInfo(timezone)).date()
    fixed_year = fixed_month = None
    if "YYYY" not in pattern.split("/"):
        fixed_year, fixed_month = root_context(root or "")
        if not fixed_year or ("MM" not in pattern.split("/") and not fixed_month):
            raise ImportFailure(
                "date_root_context",
                "MM/TT benötigt einen FTP-Root auf einem Jahresordner; TT benötigt einen Root auf einem Jahr-/Monatsordner.",
            )
        if today.year != fixed_year or (
            "MM" not in pattern.split("/") and today.month != fixed_month
        ):
            raise ImportFailure(
                "date_root_outdated",
                "Der FTP-Root ist auf ein vergangenes Jahr oder einen vergangenen Monat festgelegt. Root auf den aktuellen oder einen übergeordneten Ordner ändern.",
            )
    return [
        pattern.replace("YYYY", str(day.year))
        .replace("MM", f"{day.month:02}")
        .replace("TT", f"{day.day:02}")
        for delta in range(days)
        for day in [today - timedelta(days=delta)]
        if (fixed_year is None or day.year == fixed_year)
        and (
            "MM" in pattern.split("/")
            or fixed_month is None
            or day.month == fixed_month
        )
    ]


def latest_path(ftp: FTP, root: str, pattern: str, budget: ScanBudget) -> str | None:
    parts = pattern.split("/")
    tokens = [part for part in parts if part in {"YYYY", "MM", "TT"}]
    index = parts.index(tokens[0])
    prefix, suffix = "/".join(parts[:index]), "/".join(parts[index + len(tokens) :])
    if "YYYY" in tokens:
        found = _date_directory(ftp, posixpath.join(root, prefix), budget)
    else:
        year, month = root_context(root)
        if year is None:
            raise ImportFailure(
                "date_root_context", "Datumsmuster und FTP-Root passen nicht zusammen."
            )
        found = partial_date_directory(
            ftp,
            posixpath.join(root, prefix),
            budget,
            year,
            month if "MM" not in tokens else None,
        )
    return posixpath.join(prefix, found, suffix).rstrip("/") if found else None
