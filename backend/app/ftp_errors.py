"""Sichere, unterscheidbare Importfehler ohne rohe Serverantworten oder Secrets."""

import errno
import socket
import ssl
from ftplib import error_perm, error_proto, error_reply, error_temp

from cryptography.fernet import InvalidToken


class ImportFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def import_failure(exc: Exception, operation: str = "import") -> ImportFailure:
    if isinstance(exc, ImportFailure):
        return exc
    if isinstance(exc, InvalidToken):
        return ImportFailure(
            "password_decryption",
            "Das gespeicherte FTP-Passwort kann nicht entschlüsselt werden. Passwort neu speichern oder ursprünglichen ENCRYPTION_KEY wiederherstellen.",
        )
    if isinstance(exc, socket.gaierror):
        return ImportFailure(
            "dns",
            "Der FTP-Hostname kann nicht aufgelöst werden. Servername und DNS prüfen.",
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ImportFailure(
            "timeout",
            "Zeitüberschreitung beim FTP-Zugriff. Netzwerk, Server und passive Datenverbindung prüfen.",
        )
    if isinstance(exc, ConnectionRefusedError):
        return ImportFailure(
            "connection_refused",
            "Der FTP-Server lehnt die Verbindung ab. Server, Port und gestarteten FTP-Dienst prüfen.",
        )
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ImportFailure(
            "tls_certificate",
            "Das FTPS-Zertifikat ist ungültig oder passt nicht zum Servernamen. Zertifikat und Hostname prüfen.",
        )
    if isinstance(exc, ssl.SSLError):
        return ImportFailure(
            "tls",
            "Die FTPS-Verschlüsselung konnte nicht aufgebaut werden. Explizites FTPS und Serverkonfiguration prüfen.",
        )
    if isinstance(exc, (error_perm, error_temp, error_proto, error_reply)):
        code = str(exc)[:3]
        if code == "530":
            return ImportFailure(
                "authentication",
                "FTP-Anmeldung abgelehnt (530). Benutzername, Passwort und Kontofreigabe prüfen.",
            )
        if code in {"425", "426", "522"}:
            return ImportFailure(
                "data_connection",
                f"FTP-Datenverbindung fehlgeschlagen ({code}). Passive Ports, Firewall und FTPS-Einstellungen prüfen.",
            )
        if code == "550":
            if operation == "root":
                return ImportFailure(
                    "root_unavailable",
                    "FTP-Root oder Kamera-Unterverzeichnis ist nicht vorhanden oder nicht zugänglich (550). Pfad und Verzeichnisrechte prüfen.",
                )
            if operation == "download":
                return ImportFailure(
                    "file_unavailable",
                    "Eine FTP-Datei ist nicht mehr vorhanden oder nicht lesbar (550). Datei- und Leserechte prüfen.",
                )
            return ImportFailure(
                "directory_unavailable",
                "Ein FTP-Unterverzeichnis ist nicht vorhanden oder nicht zugänglich (550). Pfad und Leserechte prüfen.",
            )
        if code == "421":
            return ImportFailure(
                "server_unavailable",
                "FTP-Dienst vorübergehend nicht verfügbar (421). Serverlast und Verbindungslimit prüfen.",
            )
        if code in {"500", "501", "502", "504"}:
            return ImportFailure(
                "command_unsupported",
                "Der FTP-Server unterstützt einen benötigten Befehl nicht. SIZE, MDTM und Verzeichnisauflistung prüfen.",
            )
        return ImportFailure(
            "ftp_protocol",
            f"FTP-Protokollfehler{f' ({code})' if code.isdigit() else ''}. Serverprotokoll und Berechtigungen prüfen.",
        )
    if isinstance(exc, (ConnectionError, EOFError)):
        return ImportFailure(
            "connection_lost",
            "Die Verbindung zum FTP-Server wurde unterbrochen. Netzwerk und Server prüfen.",
        )
    if isinstance(exc, OSError):
        if operation == "storage":
            return ImportFailure(
                "storage_full" if exc.errno == errno.ENOSPC else "storage",
                "Lokaler Bildspeicher ist voll."
                if exc.errno == errno.ENOSPC
                else "Lokaler Bildspeicher ist nicht beschreibbar. Volume und Dateirechte prüfen.",
            )
        return ImportFailure(
            "network",
            "Der FTP-Server ist über das Netzwerk nicht erreichbar. Routing, Firewall und Server prüfen.",
        )
    return ImportFailure(
        "internal", "Interner Importfehler. Fehlercode und Worker-Protokoll prüfen."
    )
