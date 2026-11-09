"""Lesender FTP-Test mit einer harten Frist, ohne Import- oder DB-Schreibzugriff."""

import multiprocessing
import posixpath
import time
from pathlib import PurePosixPath
from types import SimpleNamespace

from .ftp_errors import import_failure

TEST_TIMEOUT = 20.0


def test_path(root: str, relative: str) -> str:
    return posixpath.normpath("/" + posixpath.join(root, relative).lstrip("/"))


def _probe(sender, config: dict, path: str, max_entries: int):
    # Import erst im Kindprozess; keine ORM-Objekte oder DB-Sitzungen übertragen.
    from .config import settings
    from .ftp_scan import EXTENSIONS, ScanBudget, listing
    from .importer import connect

    ftp = None
    connected = False
    try:
        settings().max_scan_files = max_entries
        ftp = connect(SimpleNamespace(**config))
        connected = True
        sender.send({"stage": "connected"})
        entries = listing(ftp, path, ScanBudget(), operation="root", strict_types=True)
        sender.send({
            "path": path,
            "connected": True,
            "listed": True,
            "folders": sum(facts["type"] == "dir" for _, facts in entries),
            "images": sum(
                facts["type"] == "file"
                and PurePosixPath(name).suffix.lower() in EXTENSIONS
                for name, facts in entries
            ),
            "error_code": None,
            "message": "Verbindung erfolgreich. Erste Ebene vollständig geprüft.",
        })
    except Exception as exc:
        failure = import_failure(exc, "root" if connected else "connect")
        message = failure.message
        if failure.code == "scan_limit":
            message = "Zu viele Einträge auf erster Ebene. Zählung nicht vollständig; Verzeichnis eingrenzen."
        sender.send(_failure(path, connected, failure.code, message))
    finally:
        if ftp is not None:
            ftp.close()
        sender.close()


def _failure(path, connected, code, message):
    return {
        "path": path, "connected": connected, "listed": False,
        "folders": None, "images": None, "error_code": code, "message": message,
    }


def probe_connection(config: dict, path: str, max_entries: int) -> dict:
    """Begrenzt auch DNS, TLS und endlose/tröpfelnde Verzeichnisantworten."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_probe, args=(sender, config, path, max_entries))
    deadline = time.monotonic() + TEST_TIMEOUT
    connected = False
    try:
        process.start()
        sender.close()
        while receiver.poll(max(0, deadline - time.monotonic())):
            try:
                result = receiver.recv()
            except EOFError:
                return _failure(path, connected, "internal", "FTP-Test wurde abgebrochen. Bitte erneut versuchen.")
            if result.get("stage") == "connected":
                connected = True
            else:
                return result
        return _failure(path, connected, "timeout", "FTP-Test nach 20 Sekunden abgebrochen. Keine vollständige Zählung verfügbar.")
    finally:
        sender.close()
        receiver.close()
        if process.pid is not None:
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)
            process.close()
