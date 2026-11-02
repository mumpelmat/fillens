"""Quellrelease mit expliziter Allowlist, Inhaltsmanifest und SHA256 erzeugen."""
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
version = json.loads((root / "frontend/package.json").read_text())["version"]
backend_version = re.search(r'^version = "([^"]+)"', (root / "backend/pyproject.toml").read_text(), re.MULTILINE)
if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not backend_version or backend_version.group(1) != version:
    raise SystemExit("Frontend- und Backend-Version stimmen nicht überein")

root_files = [".gitignore", ".env.example", "compose.yaml", "README.md", "ARCHITECTURE.md", "ML.md"]
excluded = {"node_modules", ".venv", ".next", "__pycache__", ".pytest_cache", ".ruff_cache", "data", ".git"}
internal_docs = {"docs/AI_SESSION_HANDOFF.md", "docs/PLAN_REVIEW.md", "docs/SOFTWARE_ASSET_MEMO.md"}
extensions = {".py", ".toml", ".lock", ".ini", ".mako", ".md", ".tsx", ".ts", ".json", ".css", ".mjs", ".svg"}
files = [root / path for path in root_files]
for directory in ("backend", "frontend", "scripts", "docs"):
    for file in (root / directory).rglob("*"):
        relative = file.relative_to(root)
        if set(relative.parts) & excluded or relative.as_posix() in internal_docs or not file.is_file():
            continue
        if file.suffix in extensions or file.name in {"Dockerfile", ".dockerignore"}:
            files.append(file)
files = sorted(set(files))
manifest = []
for file in files:
    if file.is_symlink() or not file.resolve().is_relative_to(root):
        raise SystemExit("Symlinks sind im Release nicht erlaubt")
    content = file.read_bytes()
    manifest.append({"path": file.relative_to(root).as_posix(), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})

destination = root.parent / "release"
destination.mkdir(exist_ok=True)
archive = destination / f"fillens-{version}-source.zip"
with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=9) as output:
    for file in files:
        output.write(file, arcname=f"fillens-{version}/" + file.relative_to(root).as_posix())
    output.writestr(f"fillens-{version}/RELEASE_CONTENTS.json", json.dumps({"version": version, "files": manifest}, ensure_ascii=False, indent=2))
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n")
print(f"{archive}\n{len(files)} Quelldateien, {archive.stat().st_size} Bytes\nSHA256: {checksum}")
