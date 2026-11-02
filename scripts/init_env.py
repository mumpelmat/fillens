"""Lokale Konfiguration mit Zufallssecrets erzeugen, vorhandene Datei nie ersetzen."""
import argparse
import base64
import os
from pathlib import Path
import re
import secrets

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--http", action="store_true", help="Cookies für lokalen HTTP-Test erlauben")
parser.add_argument("--base-path", default="", help="z. B. /fuellstand")
args = parser.parse_args()
if args.base_path and not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+", args.base_path):
    parser.error("Ungültiger Basis-Pfad")
root = Path(__file__).resolve().parents[1]
target = root / ".env"
content = (root / ".env.example").read_text()
content = content.replace("REPLACE_WITH_RANDOM_HEX", secrets.token_hex(32))
content = content.replace("REPLACE_WITH_FERNET_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
content = content.replace("APP_BASE_PATH=", "APP_BASE_PATH=" + args.base_path)
if args.http:
    content = content.replace("COOKIE_SECURE=true", "COOKIE_SECURE=false")
try:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    parser.error(".env existiert bereits und wurde nicht verändert")
with os.fdopen(descriptor, "w") as handle:
    handle.write(content)
print(".env mit Dateirechten 0600 erzeugt. Secrets werden nicht ausgegeben.")
