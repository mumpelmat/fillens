"""Fillens-Updater: wechselt eine Image-Installation auf eine neue Version.

Läuft als eigener Compose-Dienst mit Docker-Socket und Projektordner unter /project.
Nur über das interne Compose-Netz erreichbar und per Bearer-Token geschützt.
Ausschließlich Python-Standardbibliothek.
"""

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
PROJECT = Path(os.environ.get("UPDATER_PROJECT_MOUNT", "/project"))
STATUS_FILE = "backups/update-status.json"
RELEASE_URL = os.environ.get(
    "FILLENS_RELEASE_URL",
    "https://github.com/mumpelmat/fillens/releases/download/v{version}/fillens-{version}-update.json",
)
HEALTH_URL = os.environ.get("BACKEND_HEALTH_URL", "http://backend:8000/api/health")
HEALTH_TIMEOUT = int(os.environ.get("UPDATER_HEALTH_TIMEOUT", "600"))
APP_SERVICES = ["backend", "worker", "frontend"]
_lock = threading.Lock()


class UpdateError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_version(value):
    match = VERSION_RE.fullmatch(value or "")
    if not match:
        raise UpdateError("Ungültige Versionsnummer.")
    return tuple(int(part) for part in match.groups())


def set_env_version(text, version):
    """FILLENS_VERSION ersetzen oder anhängen; alle anderen Zeilen bleiben unverändert."""
    parse_version(version)
    lines = text.splitlines(keepends=True)
    pattern = re.compile(r"^\s*(export\s+)?FILLENS_VERSION\s*=")
    found = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[index] = f"FILLENS_VERSION={version}{ending}"
            found = True
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"FILLENS_VERSION={version}\n")
    return "".join(lines)


def env_version(text):
    for line in text.splitlines():
        match = re.match(r"^\s*(?:export\s+)?FILLENS_VERSION\s*=\s*['\"]?([^'\"\s#]*)", line)
        if match:
            return match.group(1)
    return None


def write_atomic(path, content):
    path = Path(path)
    stat = path.stat() if path.exists() else None
    temp = path.with_name(f".{path.name}.updater-tmp")
    with open(temp, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if stat:
        os.chmod(temp, stat.st_mode & 0o7777)
        try:
            os.chown(temp, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
    os.replace(temp, path)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Status:
    def __init__(self, root=PROJECT):
        self.path = Path(root) / STATUS_FILE

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"state": "idle", "steps": []}

    def write(self, data):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def start(self, version, current):
        data = {
            "state": "running",
            "version": version,
            "from_version": current,
            "started_at": now(),
            "finished_at": None,
            "backup_dir": None,
            "error": None,
            "steps": [],
        }
        self.write(data)
        return data

    def step(self, data, text):
        data["steps"].append({"at": now(), "text": text})
        self.write(data)

    def finish(self, data, state, error=None):
        data["state"] = state
        data["error"] = error
        data["finished_at"] = now()
        self.write(data)


class Docker:
    """Kapselt Docker-/Compose-Aufrufe mit Host-Projektordner aus den Compose-Labels."""

    def __init__(self):
        self._context = None

    def run(self, args, *, stdin=None, stdout=None, env=None, timeout=900):
        # Saubere Umgebung: Variablen des Updaters dürfen die .env nicht überschreiben.
        clean = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/tmp"}
        if os.environ.get("DOCKER_HOST"):
            clean["DOCKER_HOST"] = os.environ["DOCKER_HOST"]
        clean.update(env or {})
        result = subprocess.run(
            args,
            stdin=stdin,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip().splitlines()
            raise UpdateError(
                f"{' '.join(args[:4])} … fehlgeschlagen: {message[-1] if message else result.returncode}"
            )
        return result.stdout.decode(errors="replace") if stdout is None else ""

    def context(self):
        if self._context:
            return self._context
        container = os.environ.get("HOSTNAME", "")
        labels = json.loads(
            self.run(["docker", "inspect", container, "--format", "{{json .Config.Labels}}"])
        ) or {}
        host_dir = os.environ.get("FILLENS_PROJECT_DIR") or labels.get(
            "com.docker.compose.project.working_dir"
        )
        project = labels.get("com.docker.compose.project")
        if not host_dir or not project:
            raise UpdateError(
                "Compose-Projekt nicht erkennbar. FILLENS_PROJECT_DIR setzen oder den Updater per docker compose starten."
            )
        files = []
        for item in (labels.get("com.docker.compose.project.config_files") or "").split(","):
            item = item.strip()
            if not item:
                continue
            relative = os.path.relpath(item, host_dir)
            if relative.startswith(".."):
                raise UpdateError("Compose-Dateien außerhalb des Projektordners werden nicht unterstützt.")
            files.append(PROJECT / relative)
        if not files:
            files = [PROJECT / "compose.yaml"]
        self._context = {"host_dir": host_dir, "project": project, "files": files}
        return self._context

    def compose_args(self):
        ctx = self.context()
        args = ["docker", "compose", "-p", ctx["project"], "--project-directory", ctx["host_dir"]]
        for file in ctx["files"]:
            args += ["-f", str(file)]
        return args + ["--env-file", str(PROJECT / ".env")]

    def compose(self, *args, **kwargs):
        return self.run(self.compose_args() + list(args), **kwargs)

    def services(self):
        output = self.compose("ps", "--all", "--format", "json")
        rows = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            rows.extend(value if isinstance(value, list) else [value])
        return {row.get("Service"): row for row in rows}

    def image(self, service):
        return self.compose("config", "--images", service).strip()

    def recreate_self_detached(self):
        """Den Updater-Dienst aus einem losgelösten Hilfscontainer neu erstellen.

        Der laufende Prozess würde beim Ersetzen seines eigenen Containers beendet.
        """
        ctx = self.context()
        image = self.image("updater")
        command = ["docker", "compose", "-p", ctx["project"], "--project-directory", ctx["host_dir"]]
        for file in ctx["files"]:
            command += ["-f", ctx["host_dir"] + "/" + os.path.relpath(file, PROJECT)]
        command += ["--env-file", ctx["host_dir"] + "/.env", "up", "-d", "--no-deps", "updater"]
        self.run(
            [
                "docker", "run", "-d", "--rm",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{ctx['host_dir']}:{ctx['host_dir']}",
                "--entrypoint", "sh", image, "-c", "sleep 3 && exec \"$@\"", "sh", *command,
            ]
        )


def fetch_json(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "fillens-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(65536))


def backend_version():
    try:
        return fetch_json(HEALTH_URL, timeout=5).get("version")
    except (OSError, ValueError):
        return None


class Updater:
    def __init__(self, docker=None, status=None, fetch=fetch_json, version=backend_version, sleep=time.sleep):
        self.docker = docker or Docker()
        self.status = status or Status()
        self.fetch = fetch
        self.version = version
        self.sleep = sleep

    def preflight(self, target):
        target_tuple = parse_version(target)
        current = self.version()
        if not current:
            raise UpdateError("Laufende Version nicht ermittelbar. Backend muss erreichbar sein.")
        if target_tuple <= parse_version(current):
            raise UpdateError(f"Version {target} ist nicht neuer als die installierte Version {current}.")
        try:
            manifest = self.fetch(RELEASE_URL.format(version=target))
        except (OSError, ValueError):
            raise UpdateError(
                "Für dieses Release liegt keine Update-Beschreibung (update.json) vor. Bitte manuell nach Anleitung aktualisieren."
            )
        if manifest.get("version") != target or not isinstance(manifest.get("compose_sha256"), str):
            raise UpdateError("Update-Beschreibung des Releases ist ungültig.")
        compose = self.docker.context()["files"][0]
        if sha256(compose) != manifest["compose_sha256"]:
            raise UpdateError(
                "Dieses Update ändert die compose.yaml oder sie wurde lokal angepasst. Bitte manuell nach Anleitung aktualisieren."
            )
        return current

    def begin(self, target):
        """Prüft synchron und startet das Update im Hintergrund. Liefert den Startstatus."""
        if not _lock.acquire(blocking=False):
            raise UpdateError("Es läuft bereits ein Update.")
        try:
            if self.status.read().get("state") == "running":
                raise UpdateError("Es läuft bereits ein Update.")
            current = self.preflight(target)
            data = self.status.start(target, current)
        except BaseException:
            _lock.release()
            raise
        threading.Thread(target=self._run_locked, args=(data,), daemon=True).start()
        return data

    def _run_locked(self, data):
        try:
            self.run(data)
        finally:
            _lock.release()

    def run(self, data):
        target = data["version"]
        changed = False
        try:
            self.status.step(data, f"Lade Images für {target}")
            try:
                self.docker.compose(
                    "pull", *APP_SERVICES, "updater", env={"FILLENS_VERSION": target}, timeout=1800
                )
            except UpdateError as error:
                raise UpdateError(f"Images für {target} konnten nicht geladen werden ({error}).")

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = PROJECT / "backups" / f"vor-{target}-{stamp}"
            backup.mkdir(mode=0o700, parents=True)
            data["backup_dir"] = f"backups/{backup.name}"
            self.status.step(data, "Sichere Datenbank und Konfiguration")
            for name in [".env"] + [os.path.relpath(f, PROJECT) for f in self.docker.context()["files"]]:
                (backup / Path(name).name).write_bytes((PROJECT / name).read_bytes())
                os.chmod(backup / Path(name).name, 0o600)
            dump = backup / "fillens.dump"
            try:
                with open(dump, "wb") as handle:
                    os.chmod(dump, 0o600)
                    self.docker.compose(
                        "exec", "-T", "postgres", "pg_dump", "-U", "fillens", "-d", "fillens", "-Fc",
                        stdout=handle,
                    )
            except UpdateError as error:
                raise UpdateError(f"Datenbanksicherung fehlgeschlagen ({error}).")
            if dump.stat().st_size == 0:
                raise UpdateError("Datenbanksicherung ist leer.")
            with open(dump, "rb") as handle:
                listing = self.docker.compose("exec", "-T", "postgres", "pg_restore", "--list", stdin=handle)
            (backup / "database-contents.txt").write_text(listing, encoding="utf-8")
            os.chmod(backup / "database-contents.txt", 0o600)

            self.status.step(data, "Stoppe Worker und Backend")
            self.docker.compose("stop", "worker", "backend", timeout=300)
            env_file = PROJECT / ".env"
            write_atomic(env_file, set_env_version(env_file.read_bytes().decode("utf-8"), target))
            changed = True

            self.status.step(data, f"Starte Version {target}; Datenbankmigrationen laufen")
            self.docker.compose("up", "-d", "--no-deps", *APP_SERVICES, timeout=900)
            self.wait_healthy(data, target)

            self.status.finish(data, "succeeded")
            try:
                self.docker.recreate_self_detached()
            except UpdateError:
                pass  # Der alte Updater bleibt funktionsfähig; nächstes compose up erneuert ihn.
        except Exception as error:  # noqa: BLE001 – jeder Fehler landet im Status
            message = str(error) if isinstance(error, UpdateError) else f"Unerwarteter Fehler: {error}"
            if not changed:
                # Vor dem Versionswechsel: alte Dienste wieder starten, nichts wurde verändert.
                try:
                    self.docker.compose("up", "-d", "--no-deps", "backend", "worker", timeout=600)
                except Exception:  # noqa: BLE001
                    pass
                message += " Die bisherige Version läuft unverändert weiter."
            else:
                message += " Sicherung und Rückweg: siehe docs/UPDATE.md."
            self.status.finish(data, "failed", message)

    def wait_healthy(self, data, target):
        deadline = time.monotonic() + HEALTH_TIMEOUT
        reported = False
        while time.monotonic() < deadline:
            if self.version() == target:
                services = self.docker.services()
                states = [services.get(name) or {} for name in APP_SERVICES]
                if all(
                    s.get("State") == "running" and s.get("Health") in ("healthy", "", None)
                    for s in states
                ):
                    self.status.step(data, f"Version {target} läuft, alle Dienste gesund")
                    return
                if not reported:
                    self.status.step(data, "Backend bereit, warte auf Worker und Portal")
                    reported = True
            self.sleep(5)
        raise UpdateError(
            f"Version {target} wurde nicht innerhalb von {HEALTH_TIMEOUT // 60} Minuten gesund. "
            "Protokolle mit docker compose logs backend worker frontend prüfen."
        )


def make_handler(updater, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fillens-updater"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002
            sys.stderr.write("%s %s\n" % (now(), format % args))

        def reply(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            header = self.headers.get("Authorization", "")
            given = header[7:] if header.startswith("Bearer ") else ""
            if not secrets.compare_digest(given.encode(), token.encode()):
                self.reply(401, {"detail": "Nicht autorisiert."})
                return False
            return True

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                return self.reply(200, {"status": "ok"})
            if not self.authorized():
                return
            if self.path == "/status":
                return self.reply(200, updater.status.read())
            self.reply(404, {"detail": "Nicht gefunden."})

        def do_POST(self):  # noqa: N802
            if not self.authorized():
                return
            if self.path != "/update":
                return self.reply(404, {"detail": "Nicht gefunden."})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1024:
                return self.reply(400, {"detail": "Ungültige Anfrage."})
            try:
                version = json.loads(self.rfile.read(length)).get("version")
                self.reply(202, updater.begin(version))
            except UpdateError as error:
                self.reply(409, {"detail": str(error)})
            except (ValueError, AttributeError):
                self.reply(400, {"detail": "Ungültige Anfrage."})

    return Handler


def main():
    token = os.environ.get("UPDATER_TOKEN", "")
    if len(token) < 32:
        sys.exit("UPDATER_TOKEN fehlt oder ist kürzer als 32 Zeichen; Updater startet nicht.")
    updater = Updater()
    state = updater.status.read()
    if state.get("state") == "running":
        # Ein beim Neustart unterbrochenes Update nicht als laufend stehen lassen.
        updater.status.finish(state, "failed", "Update wurde unterbrochen (Updater neu gestartet). Systemzustand prüfen.")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), make_handler(updater, token))
    print(f"{now()} Fillens-Updater bereit", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
