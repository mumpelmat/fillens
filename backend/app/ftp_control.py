"""Kontrollscan nach Ordnersyntax: Metadaten statt täglicher Bildarchiv-Vollsuche."""

import posixpath
from datetime import date, datetime
from ftplib import FTP

from .ftp_scan import ScanBudget, listing, remote_time, root_context


def control_paths(
    ftp: FTP,
    root: str,
    pattern: str,
    budget: ScanBudget,
    since: datetime,
    now: datetime,
    interval_hours: int,
    already_checked: set[str],
) -> list[str]:
    parts = pattern.split("/")
    tokens = [p for p in parts if p in {"YYYY", "MM", "TT"}]
    index = parts.index(tokens[0])
    prefix, suffix = "/".join(parts[:index]), parts[index + len(tokens) :]
    base = posixpath.join(root, prefix)
    fixed_year, fixed_month = root_context(root)
    year_nodes = (
        listing(ftp, base, budget)
        if "YYYY" in tokens
        else [(str(fixed_year), {"type": "dir"})]
    )
    found: list[tuple[str, str]] = []
    for year, yf in year_nodes:
        if (
            yf["type"] != "dir"
            or len(year) != 4
            or not year.isdigit()
            or not 1970 <= int(year) <= 2200
        ):
            continue
        year_path = posixpath.join(base, year) if "YYYY" in tokens else base
        month_nodes = (
            listing(ftp, year_path, budget)
            if "MM" in tokens
            else [(f"{fixed_month:02}", {"type": "dir"})]
        )
        for month, mf in month_nodes:
            if (
                mf["type"] != "dir"
                or len(month) != 2
                or not month.isdigit()
                or not 1 <= int(month) <= 12
            ):
                continue
            month_path = (
                posixpath.join(year_path, month) if "MM" in tokens else year_path
            )
            for day, df in listing(ftp, month_path, budget):
                if df["type"] != "dir" or len(day) != 2 or not day.isdigit():
                    continue
                try:
                    date(int(year), int(month), int(day))
                except ValueError:
                    continue
                path = posixpath.join(month_path, day)
                modified = df.get("modify", "")
                valid = True
                # Bei festem Suffix zählt der Zeitstempel des eigentlichen Bildordners.
                for component in suffix:
                    child = next(
                        (
                            facts
                            for name, facts in listing(ftp, path, budget)
                            if name == component and facts["type"] == "dir"
                        ),
                        None,
                    )
                    if child is None:
                        valid = False
                        break
                    modified = child.get("modify", "")
                    path = posixpath.join(path, component)
                relative = posixpath.relpath(path, root)
                if valid and relative not in already_checked:
                    found.append((relative, modified))
    found.sort()
    changed = [
        path
        for path, stamp in found
        if remote_time(stamp) and remote_time(stamp) >= since
    ]
    # Manche Server ändern beim Ersetzen einer Datei keine Ordnerzeitstempel.
    # Deshalb rotierend zusätzlich drei alte Tagesordner prüfen, kein Archiv-Vollscan.
    if found:
        offset = (int(now.timestamp() // (interval_hours * 3600)) * 3) % len(found)
        changed.extend(
            found[(offset + i) % len(found)][0] for i in range(min(3, len(found)))
        )
    return list(dict.fromkeys(changed))
